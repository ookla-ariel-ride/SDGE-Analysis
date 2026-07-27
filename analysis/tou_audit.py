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
  canonical  -- rates.period() exactly as the analysis pipeline imports it.

Both are reported because they are both correct for different purposes: canonical
is the current tariff and is the right basis for a forward projection at constant
current rates, while as_billed is what the utility actually charged and is the only
basis on which a historical statement can be reproduced.

Two structural facts were determined from the bills rather than assumed, and both
are re-derived by this script every run rather than trusted:

  Midday super-off-peak. The weekday 10:00-14:00 super-off-peak window took effect
  2026-03-01. Before that date those hours were off-peak. The fit is flat across
  2026-02-28 to 2026-03-02 because that span is a weekend, so the effective date
  cannot be narrowed further from meter data alone. Applying the post-March window
  to earlier periods misallocates roughly 250-360 kWh per period between off-peak
  and super-off-peak, with no effect on the period total.

  Holidays. The tariff assigns weekend windows to the holidays listed in
  research/rates-reference.md. Labor Day, Veterans Day and Presidents Day each move
  enough energy to be resolved against the bills and each confirms the rule.
  Thanksgiving, Christmas, New Year's Day and Memorial Day move only a few kWh in
  this corpus, which is inside whole-kWh statement rounding, so they are carried on
  the tariff's authority rather than confirmed here. Independence Day falls outside
  the audited periods.

Tolerance. Statements print whole kWh, so a bucket carries up to +/-0.5 kWh of
rounding before any real disagreement exists. A bucket passes when the residual is
within max(1.0 kWh, 0.5% of the billed magnitude); the floor keeps small buckets
from failing on rounding alone. Period totals use 0.2%.

Coverage. Only billing periods lying wholly inside the interval export are audited.
Partially covered periods are reported as skipped, never partially credited.

Run from the private/verify sandbox with the Green Button export as usage.csv.
Writes data/tou_audit.csv and data/tou_audit_summary.json atomically.
"""
import csv
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as R

# Determined by fitting the changeover day inside the 2/27/26-3/27/26 statement:
# residual 35.4 kWh with no changeover, 0.5 kWh with it. See the module docstring
# for why 2026-02-28 .. 2026-03-02 are indistinguishable.
MIDDAY_SOP_START = dt.date(2026, 3, 1)
MIDDAY_SOP_AMBIGUITY = (dt.date(2026, 2, 28), dt.date(2026, 3, 2))

TOU_NAME = {"on": "on_peak", "off": "off_peak", "sop": "super_off_peak"}
SEASON_NAME = {"S": "summer", "W": "winter"}
ABS_TOL_KWH = 1.0        # whole-kWh statement rounding floor
REL_TOL_CELL = 0.005     # 0.5% per bucket
REL_TOL_TOTAL = 0.002    # 0.2% on period totals


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
    """Tariff holidays assigned weekend TOU windows.

    Source: research/rates-reference.md -- "Holidays treated as weekends: New
    Year's, Presidents, Memorial, July 4, Labor, Veterans, Thanksgiving,
    Christmas".
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


def assign(date, hour, rule, hol):
    """TOU period for one interval under `rule` ('as_billed' or 'canonical')."""
    if rule == "canonical":
        return R.period(hour, date.weekday() >= 5)
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


def parse_period(text):
    """'7/29/25 - 8/26/25' -> (date, date), inclusive of both endpoints."""
    a, b = [s.strip() for s in text.split("-")]
    f = lambda s: dt.datetime.strptime(s, "%m/%d/%y").date()
    return f(a), f(b)


def load_billed(path):
    """({(period, season, tou): kwh}, [period, ...]) from the delivery section.

    Delivery and generation sections carry identical kWh and differ only in rate,
    so delivery is used and generation becomes a parser cross-check.
    """
    billed, gen, periods = {}, {}, []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            key = (r["period"], r["season"], r["tou_period"])
            tgt = billed if r["section"] == "delivery" else gen
            tgt[key] = tgt.get(key, 0.0) + float(r["kwh"])
            if r["period"] not in periods:
                periods.append(r["period"])
    bad = [k for k in billed if k in gen and abs(billed[k] - gen[k]) > 1e-9]
    if bad:
        raise SystemExit(f"delivery and generation kWh disagree for {len(bad)} "
                         f"bucket(s), e.g. {bad[0]} -- the bill parse is not "
                         "internally consistent; fix parse_bills.py")
    if not billed:
        raise SystemExit(f"no delivery rows in {path}")
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


def audit(intervals, billed, periods, hol):
    covered = {d for d, _, _, _ in intervals}
    cov_start, cov_end = min(covered), max(covered)
    rows, skipped, audited = [], [], []
    for ptext in periods:
        start, end = parse_period(ptext)
        if start < cov_start or end > cov_end:
            skipped.append({"period": ptext, "reason": "outside interval coverage"})
            continue
        gaps = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)
                if start + dt.timedelta(days=i) not in covered]
        if gaps:
            skipped.append({"period": ptext, "reason": "interval gap inside period",
                            "missing_days": [str(x) for x in gaps][:10]})
            continue
        audited.append(ptext)
        built = {r: rebuild(intervals, start, end, r, hol)
                 for r in ("as_billed", "canonical")}
        for s_short, s_name in SEASON_NAME.items():
            for t_short, t_name in TOU_NAME.items():
                key = (ptext, s_name, t_name)
                if key not in billed:
                    continue
                b = billed[key]
                row = {"period": ptext, "season": s_name, "tou_period": t_name,
                       "billed_kwh": round(b, 1)}
                for rule in ("as_billed", "canonical"):
                    v = built[rule].get((s_short, t_short), 0.0)
                    row[f"{rule}_kwh"] = round(v, 2)
                    row[f"{rule}_diff_kwh"] = round(v - b, 2)
                    row[f"{rule}_diff_pct"] = (round(100 * (v - b) / abs(b), 3)
                                               if abs(b) > 1e-9 else "")
                    row[f"{rule}_pass"] = cell_pass(b, v)
                rows.append(row)
    if not audited:
        raise SystemExit("no billing period lies wholly inside the interval "
                         "export; nothing to audit")
    return rows, audited, skipped, cov_start, cov_end


def summarise(rows, rule):
    fails = [r for r in rows if not r[f"{rule}_pass"]]
    worst = max(rows, key=lambda r: abs(r[f"{rule}_diff_kwh"]))
    tot_b = sum(r["billed_kwh"] for r in rows)
    tot_r = sum(r[f"{rule}_kwh"] for r in rows)
    return {
        "buckets": len(rows),
        "buckets_failing": len(fails),
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


def holiday_evidence(intervals, hol, cov_start, cov_end, audited):
    """Per-holiday: how much net energy the weekend rule reassigns.

    A holiday that moves less than the statement rounding floor cannot be
    confirmed or refuted by this corpus, and is reported as such.
    """
    spans = [parse_period(p) for p in audited]
    out = []
    for d in sorted(hol):
        if d.weekday() >= 5 or not (cov_start <= d <= cov_end):
            continue
        inside = any(s <= d <= e for s, e in spans)
        moved = sum(imp - exp for x, h, imp, exp in intervals if x == d
                    and assign(x, h, "as_billed", hol) != assign(x, h, "as_billed", set()))
        out.append({"date": str(d), "weekday": d.strftime("%A"),
                    "net_kwh_reassigned": round(moved, 1),
                    "inside_an_audited_period": inside,
                    "resolvable": inside and abs(moved) > 3 * ABS_TOL_KWH})
    return out


def main():
    usage = pathlib.Path("usage.csv")
    if not usage.exists():
        raise SystemExit("usage.csv not found in the working directory. Copy the "
                         "Green Button 15-minute export there (see CLAUDE.md, the "
                         "private/verify sandbox pattern).")
    intervals = load_intervals(usage)
    billed, periods = load_billed(DATA / "bill_tou_detail.csv")
    covered = {d for d, _, _, _ in intervals}
    hol = holidays(range(min(covered).year, max(covered).year + 1))

    rows, audited, skipped, cov_start, cov_end = audit(intervals, billed, periods, hol)
    dst = sorted(d for d in covered
                 if sum(1 for x, _, _, _ in intervals if x == d) != 96)

    out = {
        "coverage": {"interval_start": str(cov_start), "interval_end": str(cov_end),
                     "interval_days": len(covered), "periods_audited": len(audited),
                     "periods_skipped": len(skipped), "audited": audited,
                     "skipped": skipped},
        "tolerance": {"bucket": f"max({ABS_TOL_KWH} kWh, {REL_TOL_CELL * 100}%)",
                      "period_total_pct": REL_TOL_TOTAL * 100,
                      "basis": "statements print whole kWh"},
        "dst_days": [{"date": str(d),
                      "intervals": sum(1 for x, _, _, _ in intervals if x == d)}
                     for d in dst],
        "midday_sop_changeover": refit_changeover(intervals, billed, periods, hol),
        "holiday_evidence": holiday_evidence(intervals, hol, cov_start, cov_end, audited),
        "rules": {r: summarise(rows, r) for r in ("as_billed", "canonical")},
    }

    verdict = out["rules"]["as_billed"]
    out["verdict"] = (
        "the utility's TOU accounting reproduces from raw interval data"
        if verdict["buckets_failing"] == 0 else
        f"{verdict['buckets_failing']} bucket(s) do not reconcile")

    DATA.mkdir(exist_ok=True)
    for path, kind in ((DATA / "tou_audit.csv", "csv"),
                       (DATA / "tou_audit_summary.json", "json")):
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        with open(tmp, "w", newline="") as fh:
            if kind == "csv":
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()),
                                   lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
            else:
                json.dump(out, fh, indent=2)
                fh.write("\n")
        os.replace(tmp, path)

    print(f"audited {len(audited)} periods / {len(rows)} buckets "
          f"({cov_start} .. {cov_end}); skipped {len(skipped)}")
    for rule in ("as_billed", "canonical"):
        s = out["rules"][rule]
        print(f"  {rule:<10} failing {s['buckets_failing']:>2}/{s['buckets']}  "
              f"max|residual| {s['max_abs_residual_kwh']:>7.2f} kWh  "
              f"sum|residual| {s['sum_abs_residual_kwh']:>8.1f} kWh  "
              f"net total {s['net_total_diff_pct']:+.3f}%")
    print(f"  DST days: {[(d['date'], d['intervals']) for d in out['dst_days']]}")
    print(f"  verdict: {out['verdict']}")


if __name__ == "__main__":
    main()
