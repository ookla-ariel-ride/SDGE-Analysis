#!/usr/bin/env python3
"""Regenerate data/threeway_production_validation.csv (issue #37).

The committed file had three columns (an ISO-date index, `pvoutput`,
`enphase_meter`) but no committed generator wrote it, and TECHNICAL.md's own
description of the file said a third, "three-way" series -- meter-derived
production -- was "computed in-script and summarized in the report," which
is exactly the CLAUDE.md section 9 gate ("every committed artifact
regenerable by its committed script") this file was failing. This script is
that generator: it reproduces the two REFERENCE series from their own
committed daily records and independently DERIVES the third from raw meter
data, so the whole file is regenerable end to end.

THE THREE COLUMNS, exactly:
  * `pvoutput`       -- PVOutput.org's own daily production total
                        (data/pvoutput_daily.csv, `generated_kwh`), passed
                        through unchanged.
  * `enphase_meter`  -- the Enphase production CT's own daily total
                        (data/enphase_daily_production.csv, "Energy
                        Delivered (kWh)"), passed through unchanged.
  * `meter_derived`  -- a THIRD, independent production estimate built
                        entirely from meter data, with NO production
                        monitoring feed involved at all:

                            pv_hour = max(sam_hour - import_hour + export_hour, 0)
                            meter_derived_day = sum(pv_hour for hour in day)

                        where `sam_hour` is the Enphase SAM 8760 whole-home
                        CONSUMPTION CT's hourly gross load (private/1-raw-
                        data/enphase_sam8760_*.csv, staged here as
                        samA.csv/samB.csv per the sandbox convention) and
                        `import_hour`/`export_hour` are the SDG&E revenue
                        meter's own 15-minute Consumption/Generation columns
                        (usage.csv), summed to the hour. This is the
                        identity `gross_load = import - export + production`
                        rearranged for production -- the SAME identity and
                        the SAME clip-at-zero (absorbing dark-hour
                        instrument noise) that analysis/service_headroom.py's
                        `derive_pv()` uses for its own gross-load envelope,
                        applied here as a straight daily sum instead of an
                        hourly bound. `pv_hour` values below zero are meter
                        timing/rounding noise between the two independent
                        instruments (the CT and the revenue meter) at a dark
                        hour where the true value is zero; clipping at zero
                        is the same convention service_headroom.py documents
                        for the identical noise source.

DST DAYS -- HANDLED EXPLICITLY, NOT SILENTLY:
  The Enphase SAM 8760 export is a flat 24-hours-a-day grid indexed from
  January 1st 00:00 (service_headroom.py's own load_sam() docstring: "the
  grid is flat 24 h/day and knows nothing about DST"). The Green Button
  meter is real wall-clock time, so the fall-back Sunday carries a real
  25-hour day (its 01:00 hour holds two real hours of energy in one bucket)
  and the spring-forward Sunday carries a real 23-hour day (no 02:00 exists
  at all). Matching a flat-clock SAM hour against a wall-clock meter hour on
  either of those two calendar days does not compare the same 60 minutes of
  the year, so `meter_derived` is NOT computed for them -- this script skips
  the hourly reconstruction outright for both dates (same treatment as
  service_headroom.py's `excluded_days` parameter to `derive_pv()`) rather
  than silently averaging a wrong number in. The two dates in this window,
  from analysis/rates.py's own dst_transition_sundays() (the single home of
  the tariff clock -- not re-derived here) are printed as a NOTICE at run
  time and are recorded as blank/NaN in the `meter_derived` column. All 365
  calendar rows stay in the file; `pvoutput` and `enphase_meter` are
  independent instruments unaffected by the SAM/wall-clock mismatch and are
  populated normally for both dates. The validation summary this script
  prints (correlation, MAE, ratio against each reference series) is computed
  over the OTHER 363 days only -- see `validation_stats()`.

WHAT THIS DOES NOT VALIDATE: `meter_derived`'s agreement with `pvoutput`/
`enphase_meter` speaks to whether the meter-based reconstruction is
physically sound, not to which of the three is "more correct" -- pvoutput
and enphase_meter are themselves independent instruments that already
disagree with each other by a few percent (printed below), so
`meter_derived` sitting inside that same spread is the bar this script
checks it against, not exact agreement with either one.

Run from private/verify per the standard sandbox (needs usage.csv, samA.csv,
samB.csv beside it, plus the committed data/pvoutput_daily.csv and
data/enphase_daily_production.csv). Writes data/threeway_production_
validation.csv directly (repo-root discovery + atomic tmp-then-replace, the
same convention analysis/quiet_night_floor.py and analysis/rates_history.py
use), so the CLAUDE.md section 9 gate applies unchanged.
"""
import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import rates as R  # noqa: E402  -- dst_transition_sundays() only, the tariff clock's single home


def repo_root():
    """Same contract as quiet_night_floor.py/gross_import_decomposition.py:
    nearest ancestor of the CWD (sandbox convention) or of this file
    containing both analysis/ and data/."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains analysis/ and data/")


ROOT = repo_root()
DATA = ROOT / "data"
OUT = DATA / "threeway_production_validation.csv"

# CWD-relative sandbox inputs (matches gross_import_decomposition.py's own
# USAGE_CSV/SAM_A_CSV/SAM_B_CSV convention -- overridable by tests).
USAGE_CSV = "usage.csv"
SAM_A_CSV = "samA.csv"   # 2026 (partial calendar year), per stage-private-data.sh
SAM_B_CSV = "samB.csv"   # 2025 (full calendar year)
SAM_FILES = ((SAM_B_CSV, 2025), (SAM_A_CSV, 2026))

PVOUTPUT_CSV = DATA / "pvoutput_daily.csv"
ENPHASE_CSV = DATA / "enphase_daily_production.csv"


# ---------------------------------------------------------------------------
# 1. The two REFERENCE series: read straight off the committed daily records
#    (soiling_analysis.py's load_pvoutput()/load_enphase() read the exact
#    same two files the exact same way -- reused pattern, independently
#    written here so this generator has no import-time dependency on that
#    module's household.yaml requirement).
# ---------------------------------------------------------------------------
def load_pvoutput(path):
    out = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            out[dt.date.fromisoformat(row["date"])] = float(row["generated_kwh"])
    return out


def load_enphase(path):
    out = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            ds = row["Date/Time"].strip()
            if ds.count("/") != 2:  # skip the "Total" footer row
                continue
            m, d, y = ds.split("/")
            try:
                val = float(row["Energy Delivered (kWh)"])
            except ValueError:
                continue
            out[dt.date(int(y), int(m), int(d))] = val
    return out


# ---------------------------------------------------------------------------
# 2. The two RAW meter sources the third series is derived from.
# ---------------------------------------------------------------------------
def load_sam_hourly():
    """{(date, hour): whole-home gross-load kWh} from the two Enphase SAM
    8760 exports staged for this sandbox.

    The current (partial) year's export is zero-padded through the rest of
    the calendar year -- those trailing zeros are un-run future hours, not
    measurements (a house never draws exactly 0.000 kWh for an hour), so
    each file is truncated at its own last nonzero row before use, same
    contract as service_headroom.py's truncate_sam_padding()/load_sam().
    """
    out = {}
    for fname, year in SAM_FILES:
        with open(fname, newline="") as fh:
            rd = csv.DictReader(fh)
            if rd.fieldnames != ["kWh"]:
                raise SystemExit(
                    f"threeway_production_validation.py: {fname} has columns "
                    f"{rd.fieldnames}, expected ['kWh']")
            vals = [float(r["kWh"]) for r in rd]
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        expect = 8784 if is_leap else 8760
        if len(vals) != expect:
            raise SystemExit(
                f"threeway_production_validation.py: {fname} has {len(vals)} "
                f"rows, expected {expect} for {year}")
        last = -1
        for i, v in enumerate(vals):
            if v != 0.0:
                last = i
        if last < 0:
            raise SystemExit(
                f"threeway_production_validation.py: {fname} is all zeros "
                "-- it carries no measurement")
        base = dt.datetime(year, 1, 1)
        for i in range(last + 1):
            ts = base + dt.timedelta(hours=i)
            key = (ts.date(), ts.hour)
            if key in out:
                raise SystemExit(
                    f"threeway_production_validation.py: {fname} duplicates "
                    f"an hour ({key}) already supplied by another SAM file "
                    "-- the two files' calendar years overlap")
            out[key] = vals[i]
    return out


def load_green_button_hourly():
    """{(date, hour): (import_kwh, export_kwh, n_intervals)}, hourly sums of
    the SDG&E revenue meter's 15-minute Consumption/Generation columns, WITH
    a count of how many 15-minute rows actually contributed to each hour.

    The count matters on its own: summing whatever rows happen to carry a
    given (date, hour) key silently accepts an hour built from 3 intervals
    (one quarter missing from the export) or 5 (a duplicated or
    misdated row) exactly as readily as a genuine 4 -- the SUM still looks
    like a plausible number either way, just a wrong one, with nothing in
    the shape of the data to say so. derive_daily() below refuses any
    non-DST hour whose count isn't exactly 4 rather than trusting presence
    alone (issue #37 review; DST hours are excluded before this check ever
    runs, so their genuinely non-standard interval counts -- 5 quarters in
    the fall-back hour, a missing 02:00 hour entirely on spring-forward --
    never reach it).

    Scans for the data header row rather than trusting a fixed skiprows
    count, and reads nothing above it into memory: the header block above
    carries the customer name, service address, account and meter numbers,
    none of which may reach a committed artifact -- same contract as
    service_headroom.py's load_intervals(). No timezone conversion is
    applied; the printed hour is used as-is (real DST days carry a real
    25-hour or 23-hour day here, which is exactly why they are excluded
    downstream rather than corrected).
    """
    acc = {}
    started = False
    with open(USAGE_CSV, newline="") as fh:
        for rec in csv.reader(fh):
            if not rec:
                continue
            if not started:
                started = (rec[0].strip() == "Meter Number" and len(rec) > 4
                           and rec[1].strip() == "Date")
                continue
            if len(rec) < 7:
                continue
            d = dt.datetime.strptime(rec[1].strip(), "%m/%d/%Y").date()
            t = dt.datetime.strptime(rec[2].strip(), "%I:%M %p")
            key = (d, t.hour)
            a = acc.setdefault(key, [0.0, 0.0, 0])
            a[0] += float(rec[4])
            a[1] += float(rec[5])
            a[2] += 1
    if not acc:
        raise SystemExit(
            "threeway_production_validation.py: no interval rows parsed "
            f"from {USAGE_CSV} -- is this a Green Button 15-minute export?")
    return {k: (v[0], v[1], v[2]) for k, v in acc.items()}


# ---------------------------------------------------------------------------
# 3. The window, the DST exclusion, and the derivation itself.
# ---------------------------------------------------------------------------
def window_dates(pv, en):
    """The analysis window: the exact date range BOTH reference series cover
    (currently identical, 365 contiguous days each) -- derived from the data
    rather than hardcoded, so a future refresh of either daily record
    extends this file automatically instead of silently going stale."""
    common = sorted(set(pv) & set(en))
    if not common:
        raise SystemExit(
            "threeway_production_validation.py: pvoutput_daily.csv and "
            "enphase_daily_production.csv share no dates at all")
    start, end = common[0], common[-1]
    expected = (end - start).days + 1
    if len(common) != expected or set(common) != {
            start + dt.timedelta(days=i) for i in range(expected)}:
        raise SystemExit(
            "threeway_production_validation.py: the shared pvoutput/enphase "
            f"date range ({start}..{end}) is not a contiguous run of days "
            f"-- got {len(common)} of {expected} expected")
    return [start + dt.timedelta(days=i) for i in range(expected)]


def dst_dates_in(dates):
    """The DST transition Sundays that fall inside this window, from
    analysis/rates.py's dst_transition_sundays() -- the single home of the
    tariff clock, not re-derived here (a second copy is a second thing to
    get wrong)."""
    date_set = set(dates)
    out = set()
    for y in sorted({d.year for d in dates}):
        for d in R.dst_transition_sundays(y):
            if d in date_set:
                out.add(d)
    return out


EXPECTED_INTERVALS_PER_HOUR = 4  # 15-minute Green Button rows, on any non-DST hour


def derive_daily(dates, dst_days, sam_hourly, gb_hourly):
    """{date: meter_derived_kwh or None} -- None for the two DST dates
    (skipped outright, never computed), a hard stop for any OTHER day this
    window claims to cover but the raw archive does not actually have all
    24 COMPLETE hours of (a real data gap, not a DST artifact, and not
    something to paper over).

    "Complete" means both: the hour key is present in both archives, AND
    the Green Button side was built from exactly EXPECTED_INTERVALS_PER_HOUR
    15-minute rows -- an hour missing one quarter, or carrying a duplicated
    one, still has a KEY (so a presence-only check would call it complete)
    but sums to a wrong total; gb_hourly's own n_intervals count (issue #37
    review) is what actually proves the hour is whole, not just present."""
    daily = {}
    gaps = []
    for d in dates:
        if d in dst_days:
            daily[d] = None
            continue
        total = 0.0
        hours_seen = 0
        bad_counts = []
        for h in range(24):
            key = (d, h)
            if key not in sam_hourly or key not in gb_hourly:
                continue
            sam_h = sam_hourly[key]
            imp_h, exp_h, n_h = gb_hourly[key]
            if n_h != EXPECTED_INTERVALS_PER_HOUR:
                bad_counts.append((h, n_h))
                continue
            total += max(sam_h - imp_h + exp_h, 0.0)
            hours_seen += 1
        if hours_seen != 24:
            gaps.append((d, hours_seen, bad_counts))
            daily[d] = None
        else:
            daily[d] = total
    if gaps:
        parts = []
        for d, n, bad in gaps:
            detail = f"{d} has {n}/24 complete hours"
            if bad:
                detail += (" (" + ", ".join(
                    f"hour {h} built from {c} intervals, not "
                    f"{EXPECTED_INTERVALS_PER_HOUR}" for h, c in bad) + ")")
            parts.append(detail)
        raise SystemExit(
            "threeway_production_validation.py: incomplete hourly coverage "
            "on non-DST day(s) -- " + "; ".join(parts) +
            ". This is a real data gap or a missing/duplicated 15-minute "
            "interval, not the DST exclusion, and is not papered over.")
    return daily


# ---------------------------------------------------------------------------
# 4. Validation: correlation/MAE/ratio against each reference series, over
#    the 363 non-DST days only.
# ---------------------------------------------------------------------------
def _pearson(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def validation_stats(dates, dst_days, derived, reference):
    valid = [d for d in dates if d not in dst_days]
    xs = [derived[d] for d in valid]
    ys = [reference[d] for d in valid]
    diffs = [x - y for x, y in zip(xs, ys)]
    return {
        "n_days": len(valid),
        "correlation": _pearson(xs, ys),
        "mae_kwh": sum(abs(v) for v in diffs) / len(diffs),
        "ratio_derived_over_reference": sum(xs) / sum(ys),
    }


# ---------------------------------------------------------------------------
# 5. Gate: `meter_derived` must actually agree with the two references before
#    it is trusted enough to publish -- printing corr/MAE/ratio is not a
#    check, it is a caption. A misaligned year, a units error, or an SAM/GB
#    hour-offset bug would still produce a complete-looking file with no
#    signal anything was wrong, and exit 0.
# ---------------------------------------------------------------------------
# The floor comes from evidence, not a guess (CLAUDE.md section 0): pvoutput
# and enphase_meter are two independent, already-trusted instruments, and
# their OWN mutual correlation on this exact window is the natural benchmark
# for "two honest measurements of the same daily production." meter_derived
# is a THIRD independent measurement of the same quantity, so its
# correlation with either reference should land in the same neighborhood,
# not measurably worse. Measured at the time this gate was written: the two
# references agree at r=0.99989 on this household's data; meter_derived
# agrees with enphase_meter at r=0.99996 and with pvoutput at r=0.99986 --
# both AT or ABOVE the two references' own agreement. CORRELATION_FLOOR_BELOW_REF
# gives a full two orders of magnitude of headroom below that (0.01, i.e.
# ~100x the ~0.0001 gap actually observed) before refusing to publish --
# generous enough that ordinary day-to-day noise never trips it, tight
# enough that a wrong-year SAM match or a broken hour alignment (which would
# scramble the day-to-day SHAPE of production, not just its level) does.
CORRELATION_FLOOR_BELOW_REF = 0.01
# Correlation is scale-invariant (issue #37 review, Codex pass 2): a
# derived series that is a CONSTANT multiple of the truth -- 60% of it, or
# 190% of it, a units mixup or a doubled/halved sum -- still tracks the
# reference's day-to-day SHAPE perfectly and would sail through the
# correlation floor above alone. The ratio check is what has to catch that,
# so it needs its own evidence-derived band, not a flat guess. Same
# principle as the correlation floor: the two REFERENCE instruments'
# own ratio (pvoutput/enphase_meter, measured 1.0205 on this household's
# data, i.e. a 0.0205 deviation from perfect agreement) is the natural
# "how far can two honest measurements of the same thing drift" benchmark.
# RATIO_DEVIATION_MARGIN_MULTIPLE gives 10x that measured deviation as
# headroom (0.205, i.e. the band [0.795, 1.205] on this data) -- generous
# enough for ordinary instrument disagreement, tight enough that a 60% or
# 190% scale error (Codex's own example) is nowhere close. RATIO_DEVIATION_FLOOR
# is a minimum band width so this never gets pathologically tight if the two
# references happen to agree almost exactly on some future refresh.
RATIO_DEVIATION_MARGIN_MULTIPLE = 10
RATIO_DEVIATION_FLOOR = 0.05


def check_validation(stats_en, stats_pv, ref_correlation, ref_ratio):
    ratio_deviation = max(RATIO_DEVIATION_MARGIN_MULTIPLE * abs(ref_ratio - 1.0),
                          RATIO_DEVIATION_FLOOR)
    ratio_lo, ratio_hi = 1.0 - ratio_deviation, 1.0 + ratio_deviation
    problems = []
    for label, stats in (("enphase_meter", stats_en), ("pvoutput", stats_pv)):
        corr = stats["correlation"]
        if corr is None:
            problems.append(
                f"meter_derived vs {label}: correlation is undefined (zero "
                "variance in one of the two series) -- the derivation "
                "produced a degenerate (e.g. constant or all-zero) series")
            continue
        floor = ref_correlation - CORRELATION_FLOOR_BELOW_REF
        if corr < floor:
            problems.append(
                f"meter_derived vs {label}: correlation {corr:.5f} is below "
                f"the {floor:.5f} floor (the two REFERENCE instruments' own "
                f"mutual correlation, {ref_correlation:.5f}, minus "
                f"{CORRELATION_FLOOR_BELOW_REF}) -- the derived series no "
                "longer tracks day-to-day production the way an honest "
                "third measurement should")
        ratio = stats["ratio_derived_over_reference"]
        if not (ratio_lo <= ratio <= ratio_hi):
            problems.append(
                f"meter_derived vs {label}: annual ratio {ratio:.4f} is "
                f"outside the [{ratio_lo:.4f}, {ratio_hi:.4f}] band (derived "
                f"from the two REFERENCE instruments' own {ref_ratio:.4f} "
                "ratio) -- looks like a units or scale error, since "
                "correlation alone cannot catch a constant multiplicative "
                "error")
    if problems:
        raise SystemExit(
            "threeway_production_validation.py: meter_derived FAILED "
            "validation against the two reference instruments; refusing to "
            "publish (the existing committed artifact is untouched):\n  " +
            "\n  ".join(problems))


# ---------------------------------------------------------------------------
# 6. Write the artifact -- only ever called AFTER check_validation() passes.
# ---------------------------------------------------------------------------
def write_csv(dates, pv, en, derived):
    lines = [",pvoutput,enphase_meter,meter_derived"]
    for d in dates:
        dv = derived[d]
        dv_s = "" if dv is None else str(round(dv, 3))
        lines.append(f"{d.isoformat()},{pv[d]},{en[d]},{dv_s}")
    tmp = OUT.with_suffix(".csv.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(OUT)


def main():
    pv = load_pvoutput(PVOUTPUT_CSV)
    en = load_enphase(ENPHASE_CSV)
    dates = window_dates(pv, en)
    dst_days = dst_dates_in(dates)

    sam_hourly = load_sam_hourly()
    gb_hourly = load_green_button_hourly()
    derived = derive_daily(dates, dst_days, sam_hourly, gb_hourly)

    # Validate BEFORE writing anything: a botched derivation must leave the
    # existing committed artifact byte-untouched, not overwrite it with a
    # complete-looking but wrong file.
    stats_en = validation_stats(dates, dst_days, derived, en)
    stats_pv = validation_stats(dates, dst_days, derived, pv)
    valid_dates = [d for d in dates if d not in dst_days]
    ref_diffs = [pv[d] - en[d] for d in valid_dates]
    ref_mae = sum(abs(v) for v in ref_diffs) / len(ref_diffs)
    ref_ratio = sum(pv[d] for d in valid_dates) / sum(en[d] for d in valid_dates)
    ref_correlation = _pearson([pv[d] for d in valid_dates],
                               [en[d] for d in valid_dates])
    if ref_correlation is None:
        raise SystemExit(
            "threeway_production_validation.py: the two REFERENCE "
            "instruments (pvoutput, enphase_meter) have zero variance "
            "between them on this window -- cannot derive a correlation "
            "floor from degenerate reference data")
    check_validation(stats_en, stats_pv, ref_correlation, ref_ratio)

    write_csv(dates, pv, en, derived)

    print(f"wrote data/threeway_production_validation.csv "
         f"({len(dates)} days, {dates[0]}..{dates[-1]})")
    print(f"NOTICE: meter_derived is null for {len(dst_days)} DST transition "
         f"date(s) inside the window -- {sorted(str(d) for d in dst_days)} -- "
         "the Enphase SAM 8760 export's flat 24-hours-a-day grid and the "
         "Green Button meter's real wall-clock day (25 hours fall-back, 23 "
         "hours spring-forward) do not align on these dates, so no "
         "meter_derived value is computed for them; pvoutput and "
         "enphase_meter are independent instruments and are unaffected.")

    print(f"validation, over the {stats_en['n_days']} non-DST days "
         "(PASSED -- see check_validation):")
    print(f"  meter_derived vs enphase_meter: corr={stats_en['correlation']:.5f} "
         f"MAE={stats_en['mae_kwh']:.3f} kWh/day "
         f"ratio={stats_en['ratio_derived_over_reference']:.4f}")
    print(f"  meter_derived vs pvoutput:      corr={stats_pv['correlation']:.5f} "
         f"MAE={stats_pv['mae_kwh']:.3f} kWh/day "
         f"ratio={stats_pv['ratio_derived_over_reference']:.4f}")
    print(f"  pvoutput vs enphase_meter (the two REFERENCE instruments, for "
         f"scale): corr={ref_correlation:.5f} MAE={ref_mae:.3f} kWh/day "
         f"ratio={ref_ratio:.4f}")


if __name__ == "__main__":
    main()
