#!/usr/bin/env python3
"""Reconcile the utility's billed TOU kWh against the raw 15-minute meter export.

This household holds both sides of the ledger: what SDG&E says it billed in each
season x TOU bucket (data/bill_tou_detail.csv, parsed from the statements) and the
raw interval data those buckets were derived from. This script re-buckets the
intervals and compares, which tests the TOU window assignment and the netting
convention at once.

What is being compared. Statements report kWh NET per (billing period, season, TOU
period): the per-bucket rows sum to the statement's net_kwh, and buckets that ran
net-export carry negative kWh.

Two assignment rules are scored.

  as_billed  -- the structure the bills themselves demonstrate (below).
  canonical  -- the current tariff exactly as the pipeline applies it:
                rates.period() with rates.off_peak_day() day types (today's
                windows and the eight tariff holidays) on every date.

Both are reported because they are both correct for different purposes: canonical
is the right basis for a forward projection at constant current rates, while
as_billed is what the utility actually charged and is the only basis on which a
historical statement can be reproduced. The two differ only where the tariff
STRUCTURE changed inside the corpus: canonical applies the post-2026-03-01
weekday midday super-off-peak window to every date, as_billed only after it
took effect.

Two structural facts were determined from the bills rather than assumed, and both
are re-derived by this script every run rather than trusted:

  Midday super-off-peak. The weekday 10:00-14:00 super-off-peak window took effect
  2026-03-01. Before that date those hours were off-peak. The fit is flat across
  2026-02-28 to 2026-03-02 because that span is a weekend, so the effective date
  cannot be narrowed further from meter data alone. Applying the post-March window
  to earlier periods misallocates roughly 250-360 kWh per period between off-peak
  and super-off-peak, with no effect on the period total.

  Holidays. The tariff assigns weekend windows to the holidays listed in
  research/rates-reference.md, and all eight are confirmed individually against the
  bills. Removing any one of them from the set makes its own billing period
  reconcile measurably worse, by between 5.0 and 59.3 kWh against a 3.0 kWh
  rounding bound, so none of them rests on the tariff sheet alone.

  Weekend-falling holidays additionally get an observed-day adjudication
  (weekend_shift_evidence): the no-shift, observed-Friday and observed-Monday
  variants are scored against the printed buckets with an explicit
  identifiability bound, reporting indeterminate when the discriminating energy
  sits inside statement rounding. A decisive contradiction of the documented
  Sunday-to-Monday rule encoded in rates.holidays fails the verdict.

Tolerance. Statements print whole kWh, so a bucket carries up to +/-0.5 kWh of
rounding before any real disagreement exists. A bucket passes when the residual is
within max(1.0 kWh, 0.5% of the billed magnitude); the floor keeps small buckets
from failing on rounding alone.

Period totals are checked separately, because per-bucket residuals that each sit
inside the floor can still accumulate into a period that does not reconcile. A
period total passes within max(0.5 kWh x buckets, 0.2% of the billed total): the
first term is the exact worst-case rounding bound for that many printed buckets,
the second is the reconciliation requirement. Both bucket and total failures count
towards the verdict.

Coverage. Only billing periods lying wholly inside the interval export are audited.
A period is audited only when every day inside it carries exactly the 15-minute
slots the calendar requires: 96 unique slots on an ordinary day, 92 on the
spring-forward Sunday (02:00-02:45 do not exist) and 100 on the fall-back Sunday
(01:00-01:45 occur twice). A truncated, padded or duplicated day disqualifies its
whole period, because a day short of slots reconciles against energy that was
never measured. Partially covered periods are skipped, never partially credited.

Skips are not all equal, and telling them apart needs the export's own header. A
statement outside the exported range is an expected coverage fact: thirteen 2024
and early-2025 periods are permanently in that category because the portal keeps
roughly thirteen months of interval data. Retention cuts on a date unrelated to
billing cycles, so a period straddling either end of the file is normal and says
nothing about data quality.

What does prove data loss is the export failing to deliver the range it declares.
Green Button files print `Reading Start` and `Reading End`, which is an independent
statement of what the file was supposed to contain, and every day inside that range
must be present. Days promised and not supplied, along with any gap, malformed day
or zero-energy day inside an audited period, force an INCOMPLETE verdict: a period
excluded for data loss is not a period that reconciled, and quietly dropping it
would turn a damaged export into a clean result. Without the header the check falls
back to treating boundary periods as coverage limits, which risks missing a
truncation but never invents one.

The export's final day is a placeholder: it carries a full set of zero-valued
slots, so it satisfies the slot check while measuring nothing. It is dropped from
coverage. Any other zero-energy day disqualifies its period, since a day recording
neither an import nor an export is a data fault rather than a quiet day.

Completeness. Statements print one row per TOU period for every season the billing
period touches, so the expected bucket set is derived from the period's own dates
and every expected bucket must be present. A missing or unexpected bucket is a
parser or season-boundary regression rather than an audit result, and stops the run:
without that check, an omitted bucket would silently drop its energy out of the
comparison and still report a clean reconciliation.

The set of statements is taken from bill_periods_electric.csv rather than from the
detail file, because everything here is driven by the periods discovered in that
file: a statement whose rows are missing is never audited, never reported as
skipped, and leaves no trace at all. The two artifacts must list the same periods,
and each period's buckets must sum to the statement's own net_kwh.

Run from the private/verify sandbox with the Green Button export as usage.csv.
Writes data/tou_audit.csv and data/tou_audit_summary.json as one set: both files
are built in full before either is published, so a run that fails part-way
publishes neither. See write_artifacts for what that does and does not promise.
"""
import collections
import csv
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish as _publish
import rates as R

# Determined by fitting the changeover day inside the 2/27/26-3/27/26 statement:
# residual 35.4 kWh with no changeover, 0.5 kWh with it. See the module docstring
# for why 2026-02-28 .. 2026-03-02 are indistinguishable.
MIDDAY_SOP_START = dt.date(2026, 3, 1)
MIDDAY_SOP_AMBIGUITY = (dt.date(2026, 2, 28), dt.date(2026, 3, 2))

TOU_NAME = {"on": "on_peak", "off": "off_peak", "sop": "super_off_peak"}
SEASON_NAME = {"S": "summer", "W": "winter"}

# The two rebuild rules every bucket is scored under, named once so the row
# schema below and the code that fills it cannot name different sets.
RULES = ("as_billed", "canonical")

# THE CSV'S COLUMNS ARE A SCHEMA, NOT A SAMPLE OF ROW 0. audit() gives every row
# exactly these keys in this order, so the header can be named whenever the file
# can be written -- including when there is no row 0 to read one off. Taking the
# names off rows[0] instead raises IndexError with the handle already open, which
# leaves data/tou_audit.csv truncated rather than saying nothing was computed
# (issue #215). It is the shape issue #160 fixed in bill_decomposition.py, and the
# answer is the one parse_bills.py's reader settled on: take the columns from a
# source that exists independently of the rows.
ROW_FIELDS = tuple(["period", "season", "tou_period", "billed_kwh"]
                   + [f"{rule}_{col}" for rule in RULES
                      for col in ("kwh", "diff_kwh", "diff_pct", "pass")])

ABS_TOL_KWH = 1.0        # whole-kWh statement rounding floor
REL_TOL_CELL = 0.005     # 0.5% per bucket
REL_TOL_TOTAL = 0.002    # 0.2% on period totals
ROUNDING_PER_BUCKET = 0.5   # worst-case rounding a printed whole-kWh bucket hides

# 15-minute wall-clock slots, and the two the US DST rules move.
CANONICAL_SLOTS = tuple(i * 0.25 for i in range(96))

# Skip reasons. Only the first is expected: it means the export simply does not
# reach that statement, which is a coverage fact rather than a data fault. Every
# other reason means the export IS supposed to cover the period but is degraded
# there, and that must never be quietly excluded from a passing verdict.
SKIP_OUT_OF_COVERAGE = "outside interval coverage"
SKIP_GAP = "interval gap inside period"
SKIP_MALFORMED = "malformed interval day"
SPRING_FORWARD_GAP = (2.0, 2.25, 2.5, 2.75)     # 02:00-02:45 do not occur
FALL_BACK_REPEAT = (1.0, 1.25, 1.5, 1.75)       # 01:00-01:45 occur twice


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"


def holidays(years):
    """Tariff holidays assigned weekend TOU windows -- ACTUAL dates only.

    Source: research/rates-reference.md -- "Holidays treated as weekends: New
    Year's, Presidents, Memorial, July 4, Labor, Veterans, Thanksgiving,
    Christmas".

    Deliberately NOT rates.holidays(): that set also carries the documented
    Sunday-to-Monday observed dates, which no bill has confirmed yet. as_billed
    reproduces what the statements demonstrate, so its baseline is the actual-date
    set, and weekend_shift_evidence() scores the observed-day variants against the
    bills explicitly whenever a weekend holiday enters an audited period.
    """
    def nth_weekday(y, m, wd, n):
        c = dt.date(y, m, 1)
        return c + dt.timedelta(days=(wd - c.weekday()) % 7 + 7 * (n - 1))

    out = set()
    for y in years:
        out |= {dt.date(y, 1, 1), dt.date(y, 7, 4), dt.date(y, 11, 11), dt.date(y, 12, 25)}
        out.add(nth_weekday(y, 2, 0, 3))    # Presidents: 3rd Monday, February
        out.add(nth_weekday(y, 9, 0, 1))    # Labor: 1st Monday, September
        out.add(nth_weekday(y, 11, 3, 4))   # Thanksgiving: 4th Thursday, November
        c = dt.date(y, 5, 31)               # Memorial: last Monday, May
        while c.weekday() != 0:
            c -= dt.timedelta(days=1)
        out.add(c)
    return out


def dst_dates(year):
    """(spring-forward Sunday, fall-back Sunday) -- delegates to rates.py, the
    single home of the tariff clock, so the DST rule cannot fork."""
    return R.dst_transition_sundays(year)


def expected_slots(date):
    """The exact multiset of 15-minute slots this calendar day should carry."""
    return collections.Counter(R.expected_day_hours(date))


def day_defect(date, slots):
    """None when the day is well formed, else a short reason naming the problem.

    A day short of slots would reconcile against energy that was never measured,
    and a day with duplicated slots double-counts it. Either way the day's period
    must not be audited.
    """
    want = expected_slots(date)
    got = collections.Counter(slots)
    if got == want:
        return None
    missing = sorted(h for h in want if got[h] < want[h])
    extra = sorted(h for h in got if got[h] > want.get(h, 0))
    fmt = lambda hs: ", ".join(f"{int(h):02d}:{int(round((h % 1) * 60)):02d}"
                               for h in hs[:6]) + ("..." if len(hs) > 6 else "")
    parts = [f"{sum(got.values())} slots, expected {sum(want.values())}"]
    if missing:
        parts.append(f"missing {fmt(missing)}")
    if extra:
        parts.append(f"duplicated {fmt(extra)}")
    return "; ".join(parts)


def expected_buckets(start, end):
    """The (season, tou) buckets a statement must print for this period.

    Derived from the period's own dates, so a season-boundary disagreement between
    the tariff and rates.SUMMER_MONTHS shows up as an unexpected or absent bucket
    rather than passing unnoticed.
    """
    seasons = {"S" if (start + dt.timedelta(days=i)).month in R.SUMMER_MONTHS else "W"
               for i in range((end - start).days + 1)}
    return {(SEASON_NAME[s], t) for s in seasons for t in TOU_NAME.values()}


def assign(date, hour, rule, hol):
    """TOU period for one interval under `rule` ('as_billed' or 'canonical')."""
    if rule == "canonical":
        # the current tariff exactly as the pipeline applies it: today's windows
        # on every date, weekend-or-holiday day types via rates.off_peak_day
        return R.period(hour, R.off_peak_day(date))
    weekend = date.weekday() >= 5 or date in hol
    if 16 <= hour < 21:
        return "on"
    if weekend:
        return "sop" if hour < 14 else "off"
    if hour < 6:
        return "sop"
    if date >= MIDDAY_SOP_START and 10 <= hour < 14:
        return "sop"
    return "off"


def load_intervals(path):
    """[(date, hour_frac, consumption, generation)] from a Green Button export.

    The export is local wall-clock time with real DST days (a 25-hour day carries
    100 intervals, a 23-hour day 92), so the printed hour is used as-is. No
    timezone conversion is applied or wanted.
    """
    rows, started = [], False
    with open(path, newline="") as fh:
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
            rows.append((d, t.hour + t.minute / 60.0, float(rec[4]), float(rec[5])))
    if not rows:
        raise SystemExit(f"no interval rows parsed from {path} -- is this a Green "
                         "Button 15-minute export?")
    return rows


def read_declared_range(path):
    """(start, end) the export says it covers, from its own header.

    Green Button exports print `Reading Start` and `Reading End`. That is the only
    independent statement of what the file was supposed to contain, and without it
    the covered range can only be inferred from the rows themselves, which cannot
    distinguish "the export stops here because the portal's retention stops here"
    from "the export dropped days". Retention cuts on an arbitrary date unrelated
    to billing cycles, so guessing from row extents would flag a healthy export as
    degraded every month.

    Returns None when the header is absent, and the caller then falls back to the
    conservative reading: boundary periods are treated as expected coverage limits
    rather than as data loss.
    """
    start = end = None
    with open(path, newline="") as fh:
        for rec in csv.reader(fh):
            if not rec or len(rec) < 2:
                continue
            key = rec[0].strip()
            if key == "Reading Start":
                start = rec[1].strip()
            elif key == "Reading End":
                end = rec[1].strip()
            elif key == "Meter Number" and rec[1].strip() == "Date":
                break     # the DATA header row; a bare "Meter Number,<id>" precedes it
    if not start or not end:
        return None
    f = lambda s: dt.datetime.strptime(s.split()[0], "%m/%d/%Y").date()
    return f(start), f(end)


def parse_period(text):
    """'7/29/25 - 8/26/25' -> (date, date), inclusive of both endpoints."""
    a, b = [s.strip() for s in text.split("-")]
    f = lambda s: dt.datetime.strptime(s, "%m/%d/%y").date()
    return f(a), f(b)


def load_billed(path, periods_path):
    """({(period, season, tou): kwh}, [period, ...]) from the delivery section.

    Delivery and generation sections carry identical kWh and differ only in rate,
    so delivery is used and generation becomes a parser cross-check.

    The period list cannot come from this file alone. Everything downstream is
    driven by the periods discovered here, so a statement whose rows are missing
    is not audited, is not reported as skipped, and leaves no trace: the remaining
    periods reconcile and the run declares success over a corpus with a statement
    missing from it. parse_bills.py checks that invariant when it runs, but this
    script consumes the committed artifact and may be looking at a stale or
    truncated copy.

    So the authoritative period set comes from bill_periods_electric.csv, an
    independently produced artifact, and the two must agree exactly. Each period's
    delivery buckets must also sum to that file's net_kwh, which catches a partial
    loss the set comparison cannot see.
    """
    authoritative, net_by_period = [], {}
    with open(periods_path, newline="") as fh:
        for r in csv.DictReader(fh):
            authoritative.append(r["period"])
            net_by_period[r["period"]] = float(r["net_kwh"])
    if not authoritative:
        raise SystemExit(f"no billing periods in {periods_path}")

    billed, gen, periods = {}, {}, []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            key = (r["period"], r["season"], r["tou_period"])
            tgt = billed if r["section"] == "delivery" else gen
            tgt[key] = tgt.get(key, 0.0) + float(r["kwh"])
            if r["period"] not in periods:
                periods.append(r["period"])
    if not billed:
        raise SystemExit(f"no delivery rows in {path}")
    # Compare the key SETS, not just the overlap: a bucket dropped from both
    # sections by the same parser regression would otherwise look consistent.
    only_del, only_gen = set(billed) - set(gen), set(gen) - set(billed)
    if only_del or only_gen:
        raise SystemExit(
            f"delivery and generation cover different buckets in {path}: "
            f"{len(only_del)} delivery-only (e.g. {sorted(only_del)[:1]}), "
            f"{len(only_gen)} generation-only (e.g. {sorted(only_gen)[:1]}) -- the "
            "bill parse is not internally consistent; fix parse_bills.py")
    bad = [k for k in billed if abs(billed[k] - gen[k]) > 1e-9]
    if bad:
        raise SystemExit(f"delivery and generation kWh disagree for {len(bad)} "
                         f"bucket(s), e.g. {bad[0]} -- the bill parse is not "
                         "internally consistent; fix parse_bills.py")

    missing = [p for p in authoritative if p not in periods]
    extra = [p for p in periods if p not in net_by_period]
    if missing or extra:
        raise SystemExit(
            f"{path.name} and {periods_path.name} describe different billing "
            f"periods: {len(missing)} with no TOU rows ({missing[:3]}), "
            f"{len(extra)} with no statement row ({extra[:3]}). A statement "
            "missing from the detail file would never be audited and would leave "
            "no trace in the result, so nothing is published. Re-run "
            "parse_bills.py.")

    for p in authoritative:
        got = sum(v for (per, _, _), v in billed.items() if per == p)
        n = sum(1 for (per, _, _) in billed if per == p)
        tol = max(ABS_TOL_KWH, ROUNDING_PER_BUCKET * n)
        if abs(got - net_by_period[p]) > tol:
            raise SystemExit(
                f"period {p}: TOU buckets sum to {got:.1f} kWh but the statement "
                f"row reports net_kwh {net_by_period[p]:.1f} (tolerance "
                f"{tol:.1f}). The two bill artifacts disagree, so the buckets "
                "cannot be trusted as the billed quantities. Re-run parse_bills.py.")
    return billed, periods


def rebuild(intervals, start, end, rule, hol):
    """Net kWh per (season, tou) over [start, end] inclusive, under one rule."""
    out = {}
    for d, hour, imp, exp in intervals:
        if start <= d <= end:
            k = ("S" if d.month in R.SUMMER_MONTHS else "W", assign(d, hour, rule, hol))
            out[k] = out.get(k, 0.0) + imp - exp
    return out


def cell_pass(billed, rebuilt):
    return abs(rebuilt - billed) <= max(ABS_TOL_KWH, REL_TOL_CELL * abs(billed))


def audit(intervals, billed, periods, hol, declared=None):
    slots_by_day = collections.defaultdict(list)
    energy_by_day = collections.defaultdict(float)
    for d, hour, imp, exp in intervals:
        slots_by_day[d].append(hour)
        energy_by_day[d] += abs(imp) + abs(exp)
    covered = set(slots_by_day)

    # A Green Button export's final day is a placeholder carrying a full set of
    # zero-valued slots, so it passes the slot check while measuring nothing. It is
    # dropped from coverage rather than trusted. Any OTHER zero-energy day is
    # treated as malformed below: with a 10 kW array, a day recording neither an
    # import nor an export is a data fault, not a quiet day.
    placeholder = max(covered)
    dropped_placeholder = energy_by_day[placeholder] == 0.0
    if dropped_placeholder:
        covered.discard(placeholder)
    cov_start, cov_end = min(covered), max(covered)

    # Did the export deliver the range it declared? This is the only check that can
    # tell a retention limit from a dropped day, and it is made once for the file
    # rather than inferred per period. A day the export promised and did not supply
    # is data loss wherever it sits; a period running past the promised range is
    # simply beyond what this file was ever going to contain.
    undelivered = []
    if declared:
        d_start, d_end = declared
        if dropped_placeholder and placeholder == d_end:
            d_end = d_end - dt.timedelta(days=1)   # the trailing placeholder is a known quirk
        n = (d_end - d_start).days + 1
        undelivered = [str(d_start + dt.timedelta(days=i)) for i in range(n)
                       if d_start + dt.timedelta(days=i) not in covered]
        cov_start, cov_end = max(cov_start, d_start), min(cov_end, d_end)

    rows, skipped, audited, totals = [], [], [], []
    for ptext in periods:
        start, end = parse_period(ptext)
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        # Wholly outside the export is a coverage fact and expected. Overlapping it
        # but running past either end is data loss: the export was supposed to reach
        # this statement and stops short, so it must not be filed as benign.
        if start < cov_start or end > cov_end:
            outside = [d for d in days if d < cov_start or d > cov_end]
            skipped.append({"period": ptext, "reason": SKIP_OUT_OF_COVERAGE,
                            "days_in_period": len(days),
                            "days_outside_export": len(outside)})
            continue
        gaps = [d for d in days if d not in covered]
        if gaps:
            skipped.append({"period": ptext, "reason": SKIP_GAP,
                            "missing_days": [str(x) for x in gaps][:10]})
            continue
        malformed = [(d, day_defect(d, slots_by_day[d])
                      or ("no energy recorded all day" if energy_by_day[d] == 0.0
                          else None)) for d in days]
        malformed = [(d, why) for d, why in malformed if why]
        if malformed:
            skipped.append({"period": ptext, "reason": SKIP_MALFORMED,
                            "malformed_days": [f"{d}: {why}"
                                               for d, why in malformed][:10]})
            continue

        # Every bucket the statement must print has to be there. A silently absent
        # bucket would drop its energy out of the comparison and still reconcile.
        want = expected_buckets(start, end)
        have = {(sn, tn) for (p, sn, tn) in billed if p == ptext}
        if have != want:
            raise SystemExit(
                f"bill_tou_detail.csv does not carry the expected buckets for "
                f"{ptext}: missing {sorted(want - have)}, unexpected "
                f"{sorted(have - want)}. Either parse_bills.py dropped rows or the "
                "tariff's season boundary differs from rates.SUMMER_MONTHS; both "
                "invalidate the reconciliation, so nothing is published.")

        audited.append(ptext)
        built = {r: rebuild(intervals, start, end, r, hol)
                 for r in RULES}
        raw = {r: {"billed": 0.0, "rebuilt": 0.0} for r in built}
        for s_short, s_name in SEASON_NAME.items():
            for t_short, t_name in TOU_NAME.items():
                key = (ptext, s_name, t_name)
                if (s_name, t_name) not in want:
                    continue
                b = billed[key]
                row = {"period": ptext, "season": s_name, "tou_period": t_name,
                       "billed_kwh": round(b, 1)}
                for rule in RULES:
                    v = built[rule].get((s_short, t_short), 0.0)
                    row[f"{rule}_kwh"] = round(v, 2)
                    row[f"{rule}_diff_kwh"] = round(v - b, 2)
                    row[f"{rule}_diff_pct"] = (round(100 * (v - b) / abs(b), 3)
                                               if abs(b) > 1e-9 else "")
                    row[f"{rule}_pass"] = cell_pass(b, v)
                    raw[rule]["billed"] += b          # unrounded, for the total check
                    raw[rule]["rebuilt"] += v
                rows.append(row)

        entry = {"period": ptext, "buckets": len(want)}
        for rule in RULES:
            b, v = raw[rule]["billed"], raw[rule]["rebuilt"]
            tol = max(ROUNDING_PER_BUCKET * len(want), REL_TOL_TOTAL * abs(b))
            entry[f"{rule}_billed_total_kwh"] = round(b, 2)
            entry[f"{rule}_rebuilt_total_kwh"] = round(v, 2)
            entry[f"{rule}_diff_kwh"] = round(v - b, 2)
            entry[f"{rule}_diff_pct"] = (round(100 * (v - b) / abs(b), 3)
                                         if abs(b) > 1e-9 else None)
            entry[f"{rule}_tolerance_kwh"] = round(tol, 2)
            entry[f"{rule}_pass"] = abs(v - b) <= tol
        totals.append(entry)

    if not audited:
        raise SystemExit("no billing period lies wholly inside the interval "
                         "export; nothing to audit")
    return rows, audited, skipped, totals, cov_start, cov_end, undelivered


def summarise(rows, totals, rule):
    fails = [r for r in rows if not r[f"{rule}_pass"]]
    worst = max(rows, key=lambda r: abs(r[f"{rule}_diff_kwh"]))
    tot_b = sum(r["billed_kwh"] for r in rows)
    tot_r = sum(r[f"{rule}_kwh"] for r in rows)
    total_fails = [t for t in totals if not t[f"{rule}_pass"]]
    return {
        "buckets": len(rows),
        "buckets_failing": len(fails),
        "periods": len(totals),
        "period_totals_failing": len(total_fails),
        "failing_period_totals": [
            {"period": t["period"], "billed_kwh": t[f"{rule}_billed_total_kwh"],
             "rebuilt_kwh": t[f"{rule}_rebuilt_total_kwh"],
             "diff_kwh": t[f"{rule}_diff_kwh"], "diff_pct": t[f"{rule}_diff_pct"],
             "tolerance_kwh": t[f"{rule}_tolerance_kwh"]} for t in total_fails],
        "worst_period_total_pct": max((abs(t[f"{rule}_diff_pct"] or 0)
                                       for t in totals), default=0.0),
        "max_abs_residual_kwh": abs(worst[f"{rule}_diff_kwh"]),
        "sum_abs_residual_kwh": round(sum(abs(r[f"{rule}_diff_kwh"]) for r in rows), 1),
        "net_total_billed_kwh": round(tot_b, 1),
        "net_total_rebuilt_kwh": round(tot_r, 1),
        "net_total_diff_pct": round(100 * (tot_r - tot_b) / abs(tot_b), 3),
        "worst_bucket": {"period": worst["period"], "season": worst["season"],
                         "tou_period": worst["tou_period"],
                         "diff_kwh": worst[f"{rule}_diff_kwh"]},
        "failing_buckets": [{"period": r["period"], "season": r["season"],
                             "tou_period": r["tou_period"],
                             "billed_kwh": r["billed_kwh"],
                             "rebuilt_kwh": r[f"{rule}_kwh"],
                             "diff_kwh": r[f"{rule}_diff_kwh"]} for r in fails],
    }


def refit_changeover(intervals, billed, periods, hol):
    """Re-derive the midday super-off-peak effective date from the bills.

    Scans candidate changeover days across the statement that contains the
    configured date and returns every day that ties for the lowest residual.
    """
    target = [p for p in periods
              if parse_period(p)[0] <= MIDDAY_SOP_START <= parse_period(p)[1]]
    if not target:
        return None
    ptext = target[0]
    start, end = parse_period(ptext)
    covered = {d for d, _, _, _ in intervals}
    if start < min(covered) or end > max(covered):
        return None
    scores = []
    for k in range((end - start).days + 2):
        cut = start + dt.timedelta(days=k)
        acc = {}
        for d, hour, imp, exp in intervals:
            if not (start <= d <= end):
                continue
            weekend = d.weekday() >= 5 or d in hol
            if 16 <= hour < 21:
                p = "on"
            elif weekend:
                p = "sop" if hour < 14 else "off"
            elif hour < 6 or (d >= cut and 10 <= hour < 14):
                p = "sop"
            else:
                p = "off"
            k2 = ("S" if d.month in R.SUMMER_MONTHS else "W", p)
            acc[k2] = acc.get(k2, 0.0) + imp - exp
        resid = sum(abs(acc.get((sh, th), 0.0) - billed[(ptext, sn, tn)])
                    for sh, sn in SEASON_NAME.items()
                    for th, tn in TOU_NAME.items() if (ptext, sn, tn) in billed)
        scores.append((round(resid, 2), cut))
    best = min(s for s, _ in scores)
    return {"statement_period": ptext, "best_residual_kwh": best,
            "indistinguishable_days": [str(c) for s, c in scores if s == best],
            "configured": str(MIDDAY_SOP_START)}


def _period_residual(intervals, billed, ptext, hol):
    """Total absolute bucket residual for one period under a given holiday set."""
    start, end = parse_period(ptext)
    got = rebuild(intervals, start, end, "as_billed", hol)
    return sum(abs(got.get((sh, th), 0.0) - billed[(ptext, sn, tn)])
               for sh, sn in SEASON_NAME.items() for th, tn in TOU_NAME.items()
               if (ptext, sn, tn) in billed)


def holiday_evidence(intervals, billed, hol, audited):
    """Confirm each tariff holiday against the bills, one at a time.

    For every weekday holiday inside an audited period, the period is rebuilt with
    that holiday removed from the set. If the residual gets measurably worse, the
    bills are carrying weekend windows on that date and the tariff rule is
    confirmed for it. This is a leave-one-out test rather than a size heuristic:
    counting the NET kWh a holiday reassigns understates the evidence, because the
    import and export legs move in opposite directions between buckets and cancel.

    The threshold is the six-bucket rounding bound, so a holiday only counts as
    confirmed when dropping it degrades the fit by more than statement rounding
    could explain.
    """
    threshold = ROUNDING_PER_BUCKET * 6
    out = []
    for d in sorted(hol):
        if d.weekday() >= 5:
            continue
        inside = [p for p in audited if parse_period(p)[0] <= d <= parse_period(p)[1]]
        if not inside:
            out.append({"date": str(d), "weekday": d.strftime("%A"),
                        "inside_an_audited_period": False, "confirmed": None})
            continue
        ptext = inside[0]
        with_it = _period_residual(intervals, billed, ptext, hol)
        without = _period_residual(intervals, billed, ptext, hol - {d})
        out.append({
            "date": str(d), "weekday": d.strftime("%A"),
            "inside_an_audited_period": True, "period": ptext,
            "residual_with_holiday_rule_kwh": round(with_it, 2),
            "residual_without_kwh": round(without, 2),
            "degradation_kwh": round(without - with_it, 2),
            "confirmed": (without - with_it) > threshold})
    return out


# The documented weekend-shift rule (sdge.ca/sdge-holiday-rates, read 2026-07-29,
# encoded in rates.holidays): a Sunday holiday is also observed the following
# Monday; a Saturday holiday does not shift. Keyed by weekday().
DOCUMENTED_SHIFT = {5: "none", 6: "monday"}


def weekend_shift_evidence(intervals, billed, hol, audited):
    """Adjudicate the observed-day rule for every weekend-falling holiday.

    For each weekend holiday, three candidate rules are scored against the printed
    buckets of every audited period touching the holiday or its candidate observed
    days: `none` (no shift -- the as_billed baseline), `friday` (the federal
    convention) and `monday` (the rule SDG&E documents for Sundays). Each
    candidate adds its observed day to the holiday set and the period is rebuilt;
    the candidate whose rebuild reconciles best wins ONLY when it beats every
    other candidate by more than the identifiability bound: 0.5 kWh of statement
    rounding for each bucket the two candidates actually move energy between.
    Anything inside that bound is reported `indeterminate` -- ranking residuals
    that differ by less than print rounding is noise, not evidence (the July 2026
    Saturday moves ~2 kWh under a Friday shift and ~0.7 kWh under a Monday shift,
    so a wrong winner could otherwise be crowned by rounding alone).

    Weekend holidays outside every audited period are reported `untested` rather
    than dropped, so the check cannot be skipped silently. A determined winner is
    compared against DOCUMENTED_SHIFT; a contradiction fails the run's verdict in
    main() -- the canonical calendar in rates.py rides on the documented rule and
    must be corrected if a bill decisively disagrees.
    """
    out = []
    for d in sorted(hol):
        if d.weekday() < 5:
            continue
        observed = {"none": None,
                    "friday": d - dt.timedelta(days=d.weekday() - 4),
                    "monday": d + dt.timedelta(days=7 - d.weekday())}
        cand_days = {d} | {o for o in observed.values() if o}
        periods = [p for p in audited
                   if any(parse_period(p)[0] <= x <= parse_period(p)[1]
                          for x in cand_days)]
        entry = {"date": str(d), "weekday": d.strftime("%A"),
                 "documented_rule": DOCUMENTED_SHIFT[d.weekday()]}
        if not periods:
            entry.update({"inside_an_audited_period": False, "outcome": "untested",
                          "consistent_with_documented": None})
            out.append(entry)
            continue

        resid, built = {}, {}
        for name, obs in observed.items():
            hset = hol | ({obs} if obs else set())
            resid[name] = round(sum(_period_residual(intervals, billed, p, hset)
                                    for p in periods), 2)
            built[name] = {(p, k): v for p in periods
                           for k, v in rebuild(intervals, *parse_period(p),
                                               "as_billed", hset).items()}
        winner = min(resid, key=resid.get)
        margins, decisive = {}, True
        for other in resid:
            if other == winner:
                continue
            keys = set(built[winner]) | set(built[other])
            moved = sum(1 for k in keys
                        if abs(built[winner].get(k, 0.0)
                               - built[other].get(k, 0.0)) > 1e-9)
            bound = ROUNDING_PER_BUCKET * moved
            margin = resid[other] - resid[winner]
            margins[other] = {"margin_kwh": round(margin, 2),
                              "identifiability_bound_kwh": round(bound, 2)}
            if margin <= bound:
                decisive = False
        entry.update({
            "inside_an_audited_period": True, "periods": periods,
            "residual_kwh": resid, "margins": margins,
            "outcome": f"determined:{winner}" if decisive else "indeterminate",
            "consistent_with_documented": (
                (winner == DOCUMENTED_SHIFT[d.weekday()]) if decisive else None)})
        out.append(entry)
    return out


def check_publishable(rows, audited, skipped, cov_start, cov_end):
    """Refuse, before anything is opened for writing, if there is nothing to publish.

    Two separate failures, both of which used to surface as an exception thrown
    with data/tou_audit.csv already truncated to zero bytes (issue #215):

      no rows at all -- summarise() would raise ValueError off max() on an empty
      sequence, and the CSV writer IndexError off rows[0]. Neither says what the
      run actually did, and a reader left holding an empty artifact cannot tell
      "audited nothing" from "audit found no disagreement". The refusal names the
      periods audited, the periods skipped and the coverage window instead.

      a row that does not carry ROW_FIELDS -- the header is written from the
      schema, so a row that drifted from it would be silently written with its
      new column dropped and a missing one blank. DictWriter's own extrasaction
      default would raise mid-write for the same reason the old rows[0] did:
      after the handle is open. Checking here keeps the artifact untouched.
    """
    if not rows:
        raise SystemExit(
            f"no TOU buckets were computed, so nothing is published: "
            f"{len(audited)} period(s) audited, {len(skipped)} skipped, over "
            f"{cov_start} .. {cov_end}. An audited period always prints at least "
            f"one season x TOU bucket, so an empty result means the periods were "
            f"read but no bucket was built from them -- data/tou_audit.csv and "
            f"data/tou_audit_summary.json are left as they were.")
    drifted = [r for r in rows if tuple(r) != ROW_FIELDS]
    if drifted:
        raise SystemExit(
            f"{len(drifted)} of {len(rows)} bucket row(s) do not match the "
            f"published column schema, so nothing is written: expected "
            f"[{', '.join(ROW_FIELDS)}], first mismatch carries "
            f"[{', '.join(str(k) for k in drifted[0])}]. Update ROW_FIELDS and the "
            f"row builder together -- they are the same schema stated twice.")


def write_artifacts(rows, out, dest=None):
    """Stage BOTH artifacts in full, then publish them as one set (issue #226).

    The CSV and the JSON are two views of a single audit run and the report cites
    both, so a reader who finds them disagreeing has no way to tell which one is
    speaking for the run. Building each file and promoting it in turn published
    exactly that: the CSV was replaced before the JSON was even serialized, so
    any failure in between left a fresh CSV beside the previous summary -- and in
    that order every time, which makes the JSON always the stale half rather than
    one of the two at random.

    WHAT IS GUARANTEED HERE. Both temporary files are written and closed before
    anything is promoted, so every failure that belongs to BUILDING a file -- a
    serialization error, a full filesystem, a quota, a permission change, a row
    the writer cannot encode -- lands while both destinations still hold their
    previous contents. A failed run publishes nothing and leaves nothing behind:
    the partial temporaries are removed on the way out. Promotion itself is
    delegated to publish.promote_set rather than re-implemented here, so this
    pair gets the same protocol parse_bills.py and analyze.py publish under -- an
    exclusive lock, whole-file targets, and restoration of the pre-call set on a
    Python-level failure, with .bak recovery copies that make the NEXT run refuse
    to start if an abrupt kill interrupted a promotion.

    WHAT IS NOT GUARANTEED, and publish.py says so at more length: reader
    isolation. Promoting two files is two renames, so a reader overlapping that
    window can still observe a mixed pair; what it cannot observe is a truncated
    file, and what it cannot leave behind is a mismatch nobody notices. Nor is
    durability claimed -- nothing here is fsynced, so a machine crash just after
    a successful run can lose a rename the filesystem reported as done. Every one
    of those residues has the same repair: re-run this script, and let the
    regeneration gate diff both artifacts against the committed copies.

    Columns come from ROW_FIELDS, never rows[0] (issue #215).
    """
    dest = DATA if dest is None else dest
    dest.mkdir(exist_ok=True)
    staged = {}
    try:
        for name, kind in (("tou_audit.csv", "csv"),
                           ("tou_audit_summary.json", "json")):
            tmp = dest / f"{name}.tmp{os.getpid()}"
            staged[name] = str(tmp)      # recorded BEFORE the write, so a file
            with open(tmp, "w", newline="") as fh:   # that fails mid-write is
                if kind == "csv":                    # still cleaned up below
                    w = csv.DictWriter(fh, fieldnames=list(ROW_FIELDS),
                                       lineterminator="\n")
                    w.writeheader()
                    w.writerows(rows)
                else:
                    json.dump(out, fh, indent=2)
                    fh.write("\n")
            # The close above is the flush: a filesystem that ran out of room
            # reports it here, while both destinations are still untouched.
    except BaseException:
        for tmp in staged.values():
            try:
                os.remove(tmp)
            except OSError:
                pass                     # nothing to clean up is not a failure
        raise
    _publish.promote_set(staged, str(dest))


def main():
    usage = pathlib.Path("usage.csv")
    if not usage.exists():
        raise SystemExit("usage.csv not found in the working directory. Copy the "
                         "Green Button 15-minute export there (see CLAUDE.md, the "
                         "private/verify sandbox pattern).")
    intervals = load_intervals(usage)
    billed, periods = load_billed(DATA / "bill_tou_detail.csv",
                                 DATA / "bill_periods_electric.csv")
    covered = {d for d, _, _, _ in intervals}
    hol = holidays(range(min(covered).year, max(covered).year + 1))

    declared = read_declared_range(usage)
    rows, audited, skipped, totals, cov_start, cov_end, undelivered = audit(
        intervals, billed, periods, hol, declared)
    check_publishable(rows, audited, skipped, cov_start, cov_end)
    dst = sorted(d for d in covered
                 if sum(1 for x, _, _, _ in intervals if x == d) != 96)

    out = {
        "coverage": {"interval_start": str(cov_start), "interval_end": str(cov_end),
                     "interval_days": len(covered), "periods_audited": len(audited),
                     "periods_skipped": len(skipped), "audited": audited,
                     "skipped": skipped},
        "tolerance": {
            "bucket": f"max({ABS_TOL_KWH} kWh, {REL_TOL_CELL * 100}%)",
            "period_total": (f"max({ROUNDING_PER_BUCKET} kWh x buckets, "
                             f"{REL_TOL_TOTAL * 100}%)"),
            "basis": ("statements print whole kWh, so the per-bucket rounding bound "
                      "is 0.5 kWh and a period's is 0.5 kWh times its bucket count")},
        "dst_days": [{"date": str(d),
                      "intervals": sum(1 for x, _, _, _ in intervals if x == d)}
                     for d in dst],
        "midday_sop_changeover": refit_changeover(intervals, billed, periods, hol),
        "holiday_evidence": holiday_evidence(intervals, billed, hol, audited),
        "weekend_holiday_shift_evidence": weekend_shift_evidence(
            intervals, billed, hol, audited),
        "period_totals": totals,
        "rules": {r: summarise(rows, totals, r) for r in RULES},
    }

    # A period skipped for degraded data is NOT a period that reconciled. Only
    # out-of-coverage skips are expected; a gap, a malformed day or a zero-energy
    # day means the export was supposed to cover that statement and did not, so no
    # reconciliation claim can be made over the corpus as a whole.
    integrity = [s for s in skipped if s["reason"] != SKIP_OUT_OF_COVERAGE]
    out["integrity_skips"] = integrity
    out["declared_range"] = ([str(declared[0]), str(declared[1])] if declared else None)
    out["days_declared_but_not_delivered"] = undelivered
    out["complete"] = not integrity and not undelivered

    # A decisive weekend-shift determination that contradicts the documented rule
    # invalidates the canonical calendar in rates.py, so it must fail the verdict
    # even if the aggregate residuals happen to sit inside tolerance.
    shift_conflicts = [e for e in out["weekend_holiday_shift_evidence"]
                       if e["consistent_with_documented"] is False]
    out["weekend_shift_conflicts"] = shift_conflicts

    v = out["rules"]["as_billed"]
    reconciles = (v["buckets_failing"] == 0 and v["period_totals_failing"] == 0
                  and not shift_conflicts)
    if undelivered:
        out["verdict"] = (
            f"INCOMPLETE -- the export declares {declared[0]}..{declared[1]} but did "
            f"not deliver {len(undelivered)} day(s) inside it "
            f"(e.g. {undelivered[0]}); no reconciliation claim is made")
    elif integrity:
        out["verdict"] = (
            f"INCOMPLETE -- {len(integrity)} in-coverage period(s) excluded for "
            f"degraded interval data ({', '.join(s['period'] for s in integrity)}); "
            "no reconciliation claim is made over the corpus")
    elif shift_conflicts:
        out["verdict"] = (
            f"{len(shift_conflicts)} weekend holiday(s) decisively contradict the "
            "documented observed-day rule encoded in rates.holidays "
            f"(e.g. {shift_conflicts[0]['date']}: {shift_conflicts[0]['outcome']}); "
            "correct the canonical calendar to follow the bills")
    elif reconciles:
        out["verdict"] = "the utility's TOU accounting reproduces from raw interval data"
    else:
        out["verdict"] = (f"{v['buckets_failing']} bucket(s) and "
                          f"{v['period_totals_failing']} period total(s) "
                          "do not reconcile")

    write_artifacts(rows, out)

    print(f"audited {len(audited)} periods / {len(rows)} buckets "
          f"({cov_start} .. {cov_end}); skipped {len(skipped)}")
    for rule in RULES:
        s = out["rules"][rule]
        print(f"  {rule:<10} buckets failing {s['buckets_failing']:>2}/{s['buckets']}  "
              f"period totals failing {s['period_totals_failing']:>2}/{s['periods']}  "
              f"max|residual| {s['max_abs_residual_kwh']:>7.2f} kWh  "
              f"worst period total {s['worst_period_total_pct']:.3f}%")
    print(f"  DST days: {[(d['date'], d['intervals']) for d in out['dst_days']]}")
    shifts = out["weekend_holiday_shift_evidence"]
    print("  weekend-holiday shift: " + (", ".join(
        f"{e['date']} {e['outcome']}" for e in shifts) if shifts else "none in calendar"))
    if integrity:
        print(f"  !! {len(integrity)} in-coverage period(s) excluded for degraded data:")
        for s in integrity:
            detail = s.get("malformed_days") or s.get("missing_days") or []
            print(f"       {s['period']}: {s['reason']}" +
                  (f" ({detail[0]})" if detail else ""))
    print(f"  verdict: {out['verdict']}")


if __name__ == "__main__":
    main()
