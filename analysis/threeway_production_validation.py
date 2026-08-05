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
    """{(date, hour): (import_kwh, export_kwh)}, hourly sums of the SDG&E
    revenue meter's 15-minute Consumption/Generation columns.

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
            a = acc.setdefault(key, [0.0, 0.0])
            a[0] += float(rec[4])
            a[1] += float(rec[5])
    if not acc:
        raise SystemExit(
            "threeway_production_validation.py: no interval rows parsed "
            f"from {USAGE_CSV} -- is this a Green Button 15-minute export?")
    return {k: (v[0], v[1]) for k, v in acc.items()}


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


def derive_daily(dates, dst_days, sam_hourly, gb_hourly):
    """{date: meter_derived_kwh or None} -- None for the two DST dates
    (skipped outright, never computed), a hard stop for any OTHER day this
    window claims to cover but the raw archive does not actually have all
    24 hours of (a real data gap, not a DST artifact, and not something to
    paper over)."""
    daily = {}
    gaps = []
    for d in dates:
        if d in dst_days:
            daily[d] = None
            continue
        total = 0.0
        hours_seen = 0
        for h in range(24):
            key = (d, h)
            if key not in sam_hourly or key not in gb_hourly:
                continue
            sam_h = sam_hourly[key]
            imp_h, exp_h = gb_hourly[key]
            total += max(sam_h - imp_h + exp_h, 0.0)
            hours_seen += 1
        if hours_seen != 24:
            gaps.append((d, hours_seen))
            daily[d] = None
        else:
            daily[d] = total
    if gaps:
        raise SystemExit(
            "threeway_production_validation.py: incomplete hourly coverage "
            "on non-DST day(s) -- " +
            "; ".join(f"{d} has {n}/24 hours in both the SAM and Green "
                     f"Button archives" for d, n in gaps) +
            ". This is a real data gap, not the DST exclusion, and is not "
            "papered over.")
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
# 5. Write the artifact.
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

    stats_en = validation_stats(dates, dst_days, derived, en)
    stats_pv = validation_stats(dates, dst_days, derived, pv)
    ref_diffs = [pv[d] - en[d] for d in dates if d not in dst_days]
    ref_mae = sum(abs(v) for v in ref_diffs) / len(ref_diffs)
    ref_ratio = (sum(pv[d] for d in dates if d not in dst_days) /
                sum(en[d] for d in dates if d not in dst_days))
    print(f"validation, over the {stats_en['n_days']} non-DST days:")
    print(f"  meter_derived vs enphase_meter: corr={stats_en['correlation']:.5f} "
         f"MAE={stats_en['mae_kwh']:.3f} kWh/day "
         f"ratio={stats_en['ratio_derived_over_reference']:.4f}")
    print(f"  meter_derived vs pvoutput:      corr={stats_pv['correlation']:.5f} "
         f"MAE={stats_pv['mae_kwh']:.3f} kWh/day "
         f"ratio={stats_pv['ratio_derived_over_reference']:.4f}")
    print(f"  pvoutput vs enphase_meter (the two REFERENCE instruments, for "
         f"scale): MAE={ref_mae:.3f} kWh/day ratio={ref_ratio:.4f}")


if __name__ == "__main__":
    main()
