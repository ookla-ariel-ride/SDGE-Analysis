#!/usr/bin/env python3
"""Carbon-vs-cost battery dispatch tradeoff (issue #8, second half).

Issue #8's first half replaced 28 sampled CAISO days with a measured year
(carbon_fullyear.py -> data/caiso_hourly_intensity.csv, 364/365 real days plus
one month-hour-mean interpolated day, data/carbon_fullyear_results.json). This
script asks the second half's question: does dispatching the household battery
to minimize the BILL fight dispatching it to minimize grid CO2, and by how much?

Three dispatch runs, all at 13.5 kWh usable (Powerwall 3), 11.5 kW, ETA =
sqrt(0.90) round-trip split per direction -- the exact constants
battery_dispatch_policies.py already uses, imported from there rather than
re-declared:

  Run A -- cost-minimizing. battery_dispatch_policies.run_batt(d, imp0, gen0,
    13.5, "greedy") is called DIRECTLY, unmodified: this is the existing,
    already-tested, price-aware policy (discharge whenever the TOU period is
    not super-off-peak and house load is under the 2.5 kW EV-spillover gate;
    grid-charge only during super-off-peak, up to full capacity; always charge
    from solar surplus first).

  Run B -- carbon-minimizing (new). Same control structure as run_batt's
    "greedy" branch, but the TOU-period-based decision is replaced by an
    intensity-based one: discharge when the measured per-interval grid
    intensity is ABOVE a threshold ("dirty"), grid-charge only when it is
    BELOW that threshold ("clean"). The EV-spillover exclusion (>=2.5 kW never
    battery-served outside the discharge window) is kept, gating discharge the
    same way run_batt gates its non-sop discharge clause.

    One deliberate simplification vs. run_batt, stated here rather than left
    implicit: run_batt's "greedy" disch_win is actually the OR of two clauses
    -- an unconditional 16-21h on-peak window (no kW gate at all) and a
    non-sop-with-low-kW clause. That on-peak carve-out has no analogue in a
    pure two-way "dirty vs. clean" intensity split (there is no third,
    always-serve intensity tier), so Run B's discharge condition is the plain
    binary one the issue asks for: dirty AND kW < 2.5. This is the one place
    Run B is not a mechanical find-and-replace of Run A; it is called out
    explicitly rather than silently narrowed.

  Run C -- the union/efficient policy (new). Discharges whenever EITHER Run
    A's actual condition (its full disch_win: on-peak-unconditional OR
    non-sop-with-low-kW) OR Run B's condition (dirty AND low-kW) holds; grid-
    charges only when BOTH cheap and clean hold (sop AND intensity <=
    threshold). This isolates the genuinely conflicting hours (cheap-but-dirty,
    clean-but-expensive) from the win-win hours served either way.

THRESHOLD DERIVATION (not an invented kg/MWh cutoff): Run B's discharge window
must be sized to the SAME fraction of the year as Run A's non-sop discharge
window, so the comparison isolates WHICH hours get served rather than how MANY
hours get served. The non-sop fraction is measured directly from this
household's own TOU assignment (rates.period_at via behavior_rebuild.load()),
not hardcoded: threshold = the intensity value at the sop-fraction quantile of
the year's per-interval intensity array. Ties at 0.1 kg/MWh resolution mean the
achieved split is close to, not exactly, the target; both are reported.

INTENSITY SOURCE: the committed, already-verified data/caiso_hourly_intensity.csv
(364/365 real CAISO days) is read directly via carbon_fullyear.build_covered_from_
committed_csv() (imported, not re-implemented) -- the raw CAISO day-cache under
private/1-raw-data/caiso_raw/ is NOT touched by this script. The one uncovered
day (2026-03-08, the spring-forward DST day whose 02:00 hour is blank in
CAISO's own files -- see carbon_fullyear.py's caveats) is filled by the month-
hour mean of the covered days in the same calendar month, mirroring the
identical construction in carbon_fullyear.py's main(). That construction is not
exposed there as a standalone function, so it is mirrored here (build_intensity_
map() below) rather than imported; the underlying reader
(build_covered_from_committed_csv) IS imported and reused so the CSV-parsing
and schema-validation logic exists in exactly one place.

AVERAGE VS. MARGINAL (issue AC7): every CO2 figure here, like carbon_fullyear.py's,
values energy at grid-AVERAGE intensity (kg CO2 / MWh delivered, all in-state
sources plus imports, per CAISO's public "Today's Outlook" history endpoints),
never a marginal emissions rate. CAISO's public history endpoints do not publish
a marginal rate or a per-hour marginal-fuel identification -- that is a different,
more complex data product this repo does not fetch. Direction, stated rather
than quantified: marginal generation is disproportionately gas overnight (when
average intensity is already gas-heavy, so the marginal-vs-average gap there may
be small) and increasingly displaces cleaner marginal resources at solar-heavy
midday (so a marginal accounting would likely show an even LARGER clean-midday-
vs-dirty-overnight gap than the average-based figures below already show) --
the same directional caveat carbon_fullyear.py's own artifact states; this
script does not contradict it. Exports (from all three policies, including the
export reduction a policy's own solar-charging causes) are valued at the SAME
average intensity at the export hour, the identical "avoided emissions"
convention carbon_fullyear.py already uses for its own export-avoided figure --
so a marginal-based export credit would likely differ in the same direction.

Run from private/verify with usage.csv, behavior_rebuild.py, battery_dispatch_
policies.py, carbon_fullyear.py and rates.py beside it (repo paths resolve
automatically, same convention as the sibling generators):
  data/carbon_dispatch_tradeoff.json
"""
import json
import os
import pathlib
import tempfile

import numpy as np
import pandas as pd

import behavior_rebuild as br
import battery_dispatch_policies as bp
import carbon_fullyear as CF
import rates as R  # noqa: F401  -- imported for parity with sibling scripts; bp.billed() is the actual call site


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
OUT_JSON = DATA / "carbon_dispatch_tradeoff.json"

ETA = bp.ETA
PWRQ = bp.PWRQ
KG = 1e-3          # kWh * kg/MWh -> kg, identical to carbon_fullyear.py
CAP = 13.5         # Powerwall 3 usable capacity, matching battery_dispatch_policies.py's "pw3"
MEANINGFUL_PCT = 0.02   # Run C is judged "distinct" if it clears this vs BOTH A and B


# --------------------------------------------------------------- intensity map
def build_intensity_map():
    """{pd.Timestamp date: np.array(24) kg/MWh} for the full 365-day analysis
    year, covered days as measured (from the committed CSV), the one uncovered
    day filled by the month-hour mean of covered days in the same calendar
    month -- identical method to carbon_fullyear.py's main(), mirrored here
    because that construction is inline in main() rather than a standalone
    function. The reader that does the CSV parsing/schema validation
    (build_covered_from_committed_csv) is imported and reused, not
    reimplemented, so that logic exists in exactly one place in the repo.

    Fails closed if the committed CSV is missing/malformed (the imported
    reader raises SystemExit itself) or has fewer than carbon_fullyear.py's
    own COVERAGE_MIN covered days.
    """
    covered = CF.build_covered_from_committed_csv()
    if len(covered) < CF.COVERAGE_MIN:
        raise SystemExit(
            f"only {len(covered)}/365 covered days in the committed "
            f"{CF.HOURLY_CSV} (need >= {CF.COVERAGE_MIN}, carbon_fullyear.py's "
            "own bar); rerun carbon_fullyear.py before this script")

    days = pd.date_range(CF.YEAR_START, CF.YEAR_END, freq="D")
    cov = pd.DataFrame({
        "date": [dt_ for dt_ in covered for _ in range(24)],
        "hour": list(range(24)) * len(covered),
        "kg": np.concatenate([covered[dt_] for dt_ in covered]),
    })
    cov["month"] = cov.date.dt.month
    mh_mean = cov.groupby(["month", "hour"]).kg.mean()

    inten_map = {}
    missing = []
    for dt_ in days:
        if dt_ in covered:
            inten_map[dt_] = covered[dt_]
        else:
            inten_map[dt_] = mh_mean.loc[dt_.month].sort_index().values
            missing.append(dt_.strftime("%Y-%m-%d"))
    return inten_map, len(covered), missing


def household_intensity(d, inten_map):
    """Per-15-min-interval kg/MWh, aligned to the household's own calendar
    date and integer hour -- identical alignment to carbon_fullyear.py's
    `inten = np.array([inten_map[day_][hr] for day_, hr in zip(d.day, d.hr)])`."""
    day = d.dt.dt.normalize()
    hr = d.dt.dt.hour
    try:
        inten = np.array([inten_map[dy][h] for dy, h in zip(day, hr)], dtype=float)
    except KeyError as e:
        raise SystemExit(f"household interval date {e} has no intensity map entry "
                         "-- the intensity map does not cover the household's "
                         "analysis window") from e
    if len(inten) != len(d):
        raise SystemExit(f"intensity array length {len(inten)} does not match "
                         f"household data length {len(d)}")
    if not np.isfinite(inten).all():
        raise SystemExit("non-finite intensity value(s) aligned to household data "
                         "-- refusing to dispatch or bill against garbage input")
    return inten


# --------------------------------------------------------------- threshold
def carbon_threshold(d, inten):
    """The Run-B discharge/charge threshold, sized to match Run A's non-sop
    discharge-window FRACTION of the year exactly (not an invented kg/MWh
    cutoff) -- see the module docstring for why. Returns (info dict, threshold
    float, dirty bool array)."""
    counts = d.p.value_counts()
    total = len(d)
    sop_frac = float(counts.get("sop", 0)) / total
    nonsop_frac = 1.0 - sop_frac
    threshold = float(np.quantile(inten, sop_frac))
    dirty = inten > threshold
    achieved_dirty_frac = float(dirty.mean())
    info = {
        "kg_per_mwh": round(threshold, 2),
        "target_clean_frac": round(sop_frac, 4),
        "target_dirty_frac": round(nonsop_frac, 4),
        "achieved_clean_frac": round(1.0 - achieved_dirty_frac, 4),
        "achieved_dirty_frac": round(achieved_dirty_frac, 4),
        "tou_interval_counts": {
            "sop": int(counts.get("sop", 0)),
            "off": int(counts.get("off", 0)),
            "on": int(counts.get("on", 0)),
            "total": int(total),
        },
        "method": ("threshold = intensity value at the np.quantile(inten, sop_frac) "
                   "of the year's per-interval intensity array, where sop_frac is "
                   "measured from this household's own rates.period_at TOU "
                   "assignment (behavior_rebuild.load()), not hardcoded"),
    }
    return info, threshold, dirty


# --------------------------------------------------------------- dispatch runs
def run_batt_carbon(d, imp0, gen0, cap, inten, threshold):
    """Carbon-minimizing dispatch -- see module docstring "Run B" for the full
    reasoning. Mirrors battery_dispatch_policies.run_batt's "greedy" control
    structure (charge from solar surplus first unless this interval both wants
    to discharge AND has import; otherwise grid-charge in clean hours, up to
    full capacity; otherwise discharge in dirty hours under the EV-spillover
    gate), with the TOU-period test replaced by an intensity-threshold test.
    """
    imp = imp0.copy()
    exp = gen0.copy()
    soc = cap / 2
    served = 0.0
    thru = 0.0
    kw = imp0 * 4
    for i in range(len(d)):
        dirty = inten[i] > threshold
        disch_win = dirty and kw[i] < 2.5
        if exp[i] > 0 and not (disch_win and imp[i] > 0):
            c = min(exp[i], (cap - soc) / ETA, PWRQ)
            if c > 0:
                soc += c * ETA
                exp[i] -= c
                thru += c * ETA
            continue
        if not dirty:
            take = min(max((cap - soc) / ETA, 0), PWRQ)
            if take > 0:
                soc += take * ETA
                imp[i] += take
                thru += take * ETA
            continue
        if disch_win:
            dd = min(imp[i], soc * ETA, PWRQ)
            if dd > 0:
                soc -= dd / ETA
                imp[i] -= dd
                served += dd
    return imp, exp, served, thru


def run_batt_union(d, imp0, gen0, cap, inten, threshold):
    """The union/efficient policy -- see module docstring "Run C". Discharges
    whenever EITHER Run A's actual disch_win (on-peak-unconditional OR
    non-sop-with-low-kW, exactly as battery_dispatch_policies.run_batt computes
    it for "greedy") OR Run B's condition (dirty AND low-kW) holds; grid-
    charges only when BOTH cheap (sop) and clean (intensity <= threshold) hold.
    """
    imp = imp0.copy()
    exp = gen0.copy()
    soc = cap / 2
    served = 0.0
    thru = 0.0
    p = d.p.values
    h = d.hour.values
    kw = imp0 * 4
    for i in range(len(d)):
        cond_a = (16 <= h[i] < 21) or (p[i] != "sop" and kw[i] < 2.5)
        cond_b = (inten[i] > threshold) and (kw[i] < 2.5)
        disch_win = cond_a or cond_b
        cheap_clean = (p[i] == "sop") and (inten[i] <= threshold)
        if exp[i] > 0 and not (disch_win and imp[i] > 0):
            c = min(exp[i], (cap - soc) / ETA, PWRQ)
            if c > 0:
                soc += c * ETA
                exp[i] -= c
                thru += c * ETA
            continue
        if cheap_clean:
            take = min(max((cap - soc) / ETA, 0), PWRQ)
            if take > 0:
                soc += take * ETA
                imp[i] += take
                thru += take * ETA
            continue
        if disch_win:
            dd = min(imp[i], soc * ETA, PWRQ)
            if dd > 0:
                soc -= dd / ETA
                imp[i] -= dd
                served += dd
    return imp, exp, served, thru


# --------------------------------------------------------------- computation
def compute():
    """Run all three dispatch policies and assemble the results dict. Raises
    (never swallows) on any failure -- an unreadable intensity source, a
    length/schema mismatch, or a rates.bill_nem failure all abort before any
    output is assembled, so main() never has a partial result to write."""
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    if len(d) == 0:
        raise SystemExit("empty household dataset from behavior_rebuild.load()")

    base = bp.billed(d, imp0, gen0)
    if not (base == base) or base <= 0:  # NaN-safe: base != base iff NaN
        raise SystemExit(f"baseline bill ${base} is not a finite positive number")

    inten_map, n_covered, interpolated_dates = build_intensity_map()
    inten = household_intensity(d, inten_map)

    thresh_info, threshold, _dirty = carbon_threshold(d, inten)

    base_co2 = float((imp0 * inten).sum() * KG)
    base_export_avoided = float((gen0 * inten).sum() * KG)
    if base_co2 <= 0:
        raise SystemExit(f"baseline CO2 footprint {base_co2} kg is not positive")

    # ---------------- Run A: cost-minimizing (reuse, do not reimplement) ----
    iA, eA, servedA, thruA = bp.run_batt(d, imp0, gen0, CAP, "greedy")
    billA = bp.billed(d, iA, eA)
    co2A = float((iA * inten).sum() * KG)
    expA = float((eA * inten).sum() * KG)

    committed_path = DATA / "battery_dispatch_policies.json"
    committed = json.loads(committed_path.read_text())
    committed_save_a = committed["pw3"]["greedy"]["save"]
    computed_save_a = round(base - billA)
    if abs(computed_save_a - committed_save_a) > 5:
        raise SystemExit(
            f"Run A's computed saving ${computed_save_a} does not match the "
            f"committed {committed_path.name}'s pw3.greedy.save "
            f"${committed_save_a} (tolerance $5) -- battery_dispatch_policies.py "
            "and this script have diverged; regenerate/investigate before "
            "trusting either")

    # ---------------- Run B: carbon-minimizing (new) ------------------------
    iB, eB, servedB, thruB = run_batt_carbon(d, imp0, gen0, CAP, inten, threshold)
    billB = bp.billed(d, iB, eB)
    co2B = float((iB * inten).sum() * KG)
    expB = float((eB * inten).sum() * KG)

    # ---------------- Run C: union/efficient policy (new) --------------------
    iC, eC, servedC, thruC = run_batt_union(d, imp0, gen0, CAP, inten, threshold)
    billC = bp.billed(d, iC, eC)
    co2C = float((iC * inten).sum() * KG)
    expC = float((eC * inten).sum() * KG)

    def policy_block(bill, co2, export_avoided, served, thru):
        return {
            "bill_usd": round(bill, 2),
            "savings_vs_baseline_usd": round(base - bill, 2),
            "co2_kg": round(co2, 1),
            "co2_avoided_vs_baseline_kg": round(base_co2 - co2, 1),
            "export_co2_avoided_kg": round(export_avoided, 1),
            "kwh_served": round(served, 1),
            "kwh_cycled_thru": round(thru, 1),
            "usd_saved_per_kwh_cycled": round((base - bill) / thru, 4) if thru else None,
            "co2_avoided_per_kwh_cycled_kg": round((base_co2 - co2) / thru, 4) if thru else None,
        }

    policies = {
        "A_cost_min": policy_block(billA, co2A, expA, servedA, thruA),
        "B_carbon_min": policy_block(billB, co2B, expB, servedB, thruB),
        "C_union": policy_block(billC, co2C, expC, servedC, thruC),
    }

    cost_penalty = billB - billA
    co2_penalty = co2A - co2B
    mean_thru_ab = (thruA + thruB) / 2.0

    tradeoff = {
        "cost_penalty_of_clean_policy_usd": round(cost_penalty, 2),
        "cost_penalty_of_clean_policy_usd_per_kwh_cycled": (
            round(cost_penalty / mean_thru_ab, 4) if mean_thru_ab else None),
        "co2_penalty_of_cheap_policy_kg": round(co2_penalty, 1),
        "co2_penalty_of_cheap_policy_kg_per_kwh_cycled": (
            round(co2_penalty / mean_thru_ab, 4) if mean_thru_ab else None),
        "normalization_note": (
            "both penalty figures are normalized by the MEAN kWh cycled of "
            "runs A and B: they are two different dispatch schedules with "
            "different total throughput, so there is no single denominator "
            "that is 'the' right one; the mean is used and stated explicitly "
            "here rather than silently picking one run's throughput"),
        "cost_penalty_meaning": ("Run B's $ cost minus Run A's $ cost -- how "
                                 "much MORE it costs to dispatch for carbon "
                                 "than to dispatch for money"),
        "co2_penalty_meaning": ("Run A's CO2 minus Run B's CO2 -- how much "
                                "MORE carbon the money-optimal dispatch emits "
                                "than the carbon-optimal one"),
    }

    def pct_diff(x, y):
        return abs(x - y) / abs(y) if y else float("inf")

    c_vs_a = {"bill": pct_diff(billC, billA), "co2": pct_diff(co2C, co2A)}
    c_vs_b = {"bill": pct_diff(billC, billB), "co2": pct_diff(co2C, co2B)}
    close_to_a = c_vs_a["bill"] < MEANINGFUL_PCT and c_vs_a["co2"] < MEANINGFUL_PCT
    close_to_b = c_vs_b["bill"] < MEANINGFUL_PCT and c_vs_b["co2"] < MEANINGFUL_PCT
    run_c_analysis = {
        "meaningfully_differs_from_a_and_b": not (close_to_a or close_to_b),
        "meaningful_threshold_pct": MEANINGFUL_PCT,
        "pct_diff_bill_vs_a": round(c_vs_a["bill"], 4),
        "pct_diff_co2_vs_a": round(c_vs_a["co2"], 4),
        "pct_diff_bill_vs_b": round(c_vs_b["bill"], 4),
        "pct_diff_co2_vs_b": round(c_vs_b["co2"], 4),
        "note": ("Run C is judged to NOT meaningfully differ from A (or B) "
                 f"when BOTH its $ and its CO2 are within {MEANINGFUL_PCT:.0%} "
                 "of that policy's own figures; otherwise it is reported as a "
                 "genuinely distinct outcome."),
    }

    result = {
        "baseline": {
            "bill_usd": round(base, 2),
            "co2_kg": round(base_co2, 1),
            "export_co2_avoided_kg": round(base_export_avoided, 1),
        },
        "cross_check": {
            "battery_dispatch_policies_json_pw3_greedy_save_usd": committed_save_a,
            "run_a_computed_save_usd": computed_save_a,
            "tolerance_usd": 5,
            "note": ("Run A calls battery_dispatch_policies.run_batt() directly "
                     "(the same function/policy/capacity the committed "
                     "battery_dispatch_policies.json's pw3.greedy figure comes "
                     "from), so the two savings figures must agree within "
                     "rounding -- a real cross-check, not a coincidence."),
        },
        "coverage": {
            "days_covered": n_covered,
            "days_interpolated_month_hour_mean": interpolated_dates,
            "source": "data/caiso_hourly_intensity.csv (committed, verified aggregate)",
        },
        "threshold": thresh_info,
        "policies": policies,
        "tradeoff": tradeoff,
        "run_c_analysis": run_c_analysis,
        "average_vs_marginal_basis": (
            "All CO2 figures value energy at grid-AVERAGE intensity (kg CO2/MWh "
            "delivered, all sources incl. imports, per CAISO's public 'Today's "
            "Outlook' history endpoints), never a marginal emissions rate. "
            "CAISO's public history endpoints do not publish a marginal rate or "
            "per-hour marginal-fuel identification -- a different, more complex "
            "data product this repo does not fetch. Direction (not quantified): "
            "marginal generation is disproportionately gas overnight (average "
            "intensity there is already gas-heavy, so the gap may be small) and "
            "increasingly displaces cleaner marginal resources at solar-heavy "
            "midday (so a marginal accounting would likely show an even LARGER "
            "clean-midday-vs-dirty-overnight gap than these average-based "
            "figures already show) -- consistent with carbon_fullyear.py's own "
            "stated caveat, not a contradiction of it. Exports (including the "
            "export reduction each policy's own solar-charging causes) are "
            "valued at the SAME average intensity at the export hour -- the "
            "identical 'avoided emissions' convention carbon_fullyear.py uses "
            "for its own export-avoided figure -- so a marginal-based export "
            "credit would likely differ in the same direction."),
        "caveat": [
            "Grid-AVERAGE intensity, not marginal (see average_vs_marginal_basis).",
            "Run B's threshold is sized to match Run A's measured non-sop TOU "
                "fraction of the year, not an invented kg/MWh cutoff; ties at "
                "0.1 kg/MWh resolution mean the achieved split is close to, not "
                "exactly, that target (see threshold.achieved_* vs target_*).",
            "Run B deliberately drops run_batt's on-peak-unconditional discharge "
                "carve-out (there is no third, always-serve tier in a binary "
                "intensity split); see the module docstring for why.",
            f"Intensity measured on {n_covered}/365 real CAISO days; "
                f"{len(interpolated_dates)} day(s) ({', '.join(interpolated_dates)}) "
                "use the month-hour mean of covered days in the same calendar "
                "month, identical to carbon_fullyear.py's own treatment.",
            "All three runs share one battery model (13.5 kWh usable, 11.5 kW, "
                "90% round-trip efficiency) and one billing engine "
                "(rates.bill_nem); only the discharge/charge DECISION differs "
                "between them, isolating the dispatch-objective effect.",
        ],
        "provenance": {
            "inputs": [
                "private Green Button 15-min interval data, via "
                    "behavior_rebuild.load() (gitignored, not committed)",
                "data/caiso_hourly_intensity.csv (committed, 364/365 real CAISO days)",
                "data/battery_dispatch_policies.json (committed, cross-check only)",
                "analysis/rates.py (canonical bill-derived billing engine)",
            ],
            "generator": "analysis/carbon_dispatch_tradeoff.py",
            "engine": "rates.bill_nem via battery_dispatch_policies.billed()",
            "battery": {"cap_kwh": CAP, "power_kw": 11.5, "rte": 0.9},
        },
    }
    return result


def main(out_path=None):
    out_path = pathlib.Path(out_path) if out_path is not None else OUT_JSON
    result = compute()
    # validate before writing -- a partial/failed run must change nothing on disk
    for k in ("baseline", "cross_check", "coverage", "threshold", "policies",
              "tradeoff", "run_c_analysis", "average_vs_marginal_basis",
              "caveat", "provenance"):
        assert k in result, f"result missing required section: {k}"
    for name in ("A_cost_min", "B_carbon_min", "C_union"):
        assert name in result["policies"], f"policies missing {name}"

    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(result, fh, indent=1)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    print(f"wrote {out_path}")
    print(f"baseline bill ${result['baseline']['bill_usd']:,.2f} | "
          f"CO2 {result['baseline']['co2_kg']:,.0f} kg")
    for name in ("A_cost_min", "B_carbon_min", "C_union"):
        p = result["policies"][name]
        print(f"  {name}: bill ${p['bill_usd']:,.2f} (save ${p['savings_vs_baseline_usd']:,.2f}) | "
              f"CO2 {p['co2_kg']:,.0f} kg (avoided {p['co2_avoided_vs_baseline_kg']:,.0f}) | "
              f"cycled {p['kwh_cycled_thru']:,.0f} kWh")
    t = result["tradeoff"]
    print(f"cost penalty of the clean policy: ${t['cost_penalty_of_clean_policy_usd']:,.2f}/yr "
          f"({t['cost_penalty_of_clean_policy_usd_per_kwh_cycled']:.4f} $/kWh cycled)")
    print(f"CO2 penalty of the cheap policy: {t['co2_penalty_of_cheap_policy_kg']:,.1f} kg/yr "
          f"({t['co2_penalty_of_cheap_policy_kg_per_kwh_cycled']:.4f} kg/kWh cycled)")
    print(f"Run C meaningfully differs from A and B: "
          f"{result['run_c_analysis']['meaningfully_differs_from_a_and_b']}")
    return result


if __name__ == "__main__":
    main()
