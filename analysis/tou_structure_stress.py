#!/usr/bin/env python3
"""Tariff STRUCTURE stress test, not price level (issue #14).

TECHNICAL.md's escalation sensitivity (analysis/battery_dispatch_policies.py's
`escalation()`, reported in index.html §13) varies how fast $/kWh RATES rise
while holding the TOU window SHAPES fixed. This script holds today's prices
fixed and instead varies the window SHAPES: on-peak start/end, the weekday
midday super-off-peak window, and the summer-season month set. For a battery
with a 10-15 year horizon, a redrawn window boundary is a real, precedented
risk that the escalation-only sensitivity cannot see.

Corpus check (the issue's first acceptance criterion): this household's own
bill corpus ALREADY contains a real, bill-measured TOU-window structural
change, not merely a price change. `analysis/tou_audit.py` fits the exact
changeover day for the weekday 10am-2pm window from 2026 statement residuals
(35.4 kWh unexplained with no changeover, 0.5 kWh with one) and pins it to
`tou_audit.MIDDAY_SOP_START` (2026-03-01, ambiguous only within the enclosing
weekend 2026-02-28..2026-03-02): before that date those hours were off-peak;
after, they are super-off-peak. That measured change is exactly the shape of
the "midday super-off-peak narrowed or removed" scenario below, so that
scenario reverts to the pre-2026-03-01 structure directly rather than
inventing a hypothetical window. `analysis/lifetime_payback.py` separately
hardcodes a THIRD, older window shape for 2020-2025 (`_per_old`: midday SOP
only in March/April) for its multi-year valuation, but that shape is not
independently bill-confirmed the way the 2026-03-01 change is (no
`tou_audit.py` function scores it, and the bill corpus used here only starts
2024-05-25) -- noted, not relied on, as a lead for a future audit rather than
treated as measured precedent in this script.

The other three scenarios have no in-corpus precedent, so each cites external,
verifiable grounding instead, per the issue's own instruction to label an
ungrounded scenario hypothetical:

  on-peak widened / on-peak shifted later -- BOTH draw on the same real,
  well-documented history of THIS utility's own default TOU window: SDG&E's
  on-peak period was 11am-6pm (7 hours) for roughly 30 years before the CPUC's
  March 2019 mandated default-TOU transition moved it to today's 4-9pm
  (5 hours), explicitly to track the evening "duck curve" net-demand peak as
  rooftop solar grew (KPBS "SDG&E's New Time-Of-Use Plan Explained", July
  2019; Utility Dive "California utilities prep nation's biggest time-of-use
  rate roll-out"). "On-peak widened" tests a partial reversion toward that
  historical WIDTH (7h, at 14-21 rather than the historical clock hours);
  "on-peak shifted later" tests a further, smaller move in the SAME direction
  the 2019 transition already moved (16-21 -> 17-22) -- a real, precedented
  lever, not an invented one, even though the specific magnitude modeled here
  is a bounding choice, not a re-enactment of the 2019 change itself. Labeled
  "historically motivated" rather than "measured" for exactly that reason
  (Codex adversarial review, second pass): the DIRECTION and mechanism trace
  to real history, but neither exact scenario was itself ever observed, so
  claiming "measured" would overstate the evidentiary basis of the dollar
  figures that follow from it.

  summer season extended -- NO precedent was found: SDG&E's summer (Jun-Oct,
  5 months) is already longer than PG&E's or SCE's (Jun-Sep, 4 months each),
  and no CPUC proceeding defining a longer season turned up. Modeled as a
  bounding hypothetical (extend one month, through November) motivated by
  real (if not yet regulatorily acted-on) evidence that California's fire/
  heat season is measurably lengthening into the traditionally cooler months
  (NOAA/Yale Climate Connections coverage of the Jan 2025 LA fires; Scripps
  Institution of Oceanography Santa Ana wind-timing research). Labeled
  hypothetical in both this artifact and the report prose, per the issue's
  own explicit instruction.

Mechanics: `rates.py`'s canonical `period()`/`period_at()` and `SUMMER_MONTHS`
are NOT modified (they are the single source of truth for the CURRENT
tariff). Instead `period_variant()` here is a parametrized generalization
(verified to reproduce `rates.period` exactly at today's parameters) used
only to build a scenario's OWN `p`/`seas` columns on a COPY of the real
measured year -- the physical Consumption/Generation never change, only
which TOU bucket each interval falls into. `behavior_rebuild.build_sop_index/
shift_ev/shift_house` and `battery_dispatch_policies.run_batt` all read
`p`/`hour`/`seas` off the frame they are given, not off `rates.py` directly,
so re-running them against a scenario frame naturally re-derives the EV shift
target and the battery's discharge windows under that scenario's own
structure, with zero changes to either module.

Each scenario reports three deltas against the CURRENT-structure figures
(recomputed fresh here, not read from a sibling artifact, so the comparison
is apples-to-apples on identical code and identical physical data):
  baseline_delta_usd          -- change to the no-behavior, no-battery bill
  behavior_save_delta_usd     -- change to the EV-shift-only saving
  battery_marginal_delta_usd  -- change to the battery's own marginal saving
    (price-aware/"greedy" policy, on top of the shifted load -- the same
    integrated, decision-relevant convention battery_dispatch_policies.py and
    the report's MID package use)
and a combined `total_package_impact_usd` = baseline_delta_usd -
behavior_save_delta_usd - battery_marginal_delta_usd: how much MORE (or
less) a fully-optimized household (EV shift + price-aware battery) would pay
per year under that scenario, holding physical usage fixed -- the single
number that answers "which structural change hurts most, and by how much."

Output: tou_structure_stress.json. This script fully regenerates the
committed artifact.
"""
import json
import os

import numpy as np

import behavior_rebuild as br
import battery_dispatch_policies as bdp
import rates as R
import tou_audit as TA

CAP_KWH = 13.5
POWER_KW = 11.5

CURRENT = dict(on_start=16, on_end=21, weekday_sop_windows=((0, 6), (10, 14)),
              weekend_sop_end=14, summer_months=frozenset(R.SUMMER_MONTHS))


def repo_root():
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = base
        for _ in range(8):
            if os.path.exists(os.path.join(p, "data", "plan_results.csv")):
                return p
            p = os.path.dirname(p)
    raise SystemExit("repo root not found (data/plan_results.csv)")


def period_variant(hour_frac, is_weekend, on_start, on_end, weekday_sop_windows,
                    weekend_sop_end):
    """Parametrized generalization of rates.period(). At CURRENT's parameters
    this reproduces rates.period exactly (verified directly in
    test_tou_structure_stress.py, not merely assumed from the algebra)."""
    if on_start <= hour_frac < on_end:
        return "on"
    if is_weekend:
        return "sop" if hour_frac < weekend_sop_end else "off"
    for a, b in weekday_sop_windows:
        if a <= hour_frac < b:
            return "sop"
    return "off"


def assign_structure(d, on_start, on_end, weekday_sop_windows, weekend_sop_end,
                     summer_months):
    """A COPY of d with p/seas rebuilt under the given window structure.
    Consumption/Generation (physical reality) are untouched -- only which
    TOU bucket each interval is billed under changes. `wkend` (holiday-aware
    weekend flag) and `ym` (calendar month) are structure-invariant and
    carried over unchanged."""
    d2 = d.copy()
    hour = d.hour.values
    wkend = d.wkend.values
    d2["p"] = [period_variant(h, w, on_start, on_end, weekday_sop_windows, weekend_sop_end)
               for h, w in zip(hour, wkend)]
    d2["seas"] = np.where(d.dt.dt.month.isin(sorted(summer_months)), "S", "W")
    return d2


SCENARIOS = {
    "onpeak_widened": dict(
        label="On-peak widened (16-21 -> 14-21, 5h -> 7h)",
        params=dict(on_start=14, on_end=21,
                    weekday_sop_windows=CURRENT["weekday_sop_windows"],
                    weekend_sop_end=CURRENT["weekend_sop_end"],
                    summer_months=CURRENT["summer_months"]),
        precedent="historically motivated",
        precedent_note=(
            "SDG&E's own on-peak window was 11am-6pm (7 hours) for roughly 30 "
            "years before the CPUC's March 2019 mandated default-TOU transition "
            "moved it to today's 4-9pm (5 hours). Widening today's window to "
            "7 hours (14-21) matches that historical WIDTH, not its exact "
            "clock hours (11-18) -- the DIRECTION and rough magnitude are real "
            "precedent, but this specific scenario was never itself observed, "
            "so it is labeled 'historically motivated', not 'measured' (Codex "
            "adversarial review: an earlier version labeled this 'measured', "
            "overstating how directly this exact scenario traces to the cited "
            "history). Sources: KPBS \"SDG&E's New Time-Of-Use Plan "
            "Explained\" (Jul 2019), Utility Dive \"California utilities prep "
            "nation's biggest time-of-use rate roll-out\"."),
    ),
    "onpeak_shifted_later": dict(
        label="On-peak shifted later (16-21 -> 17-22)",
        params=dict(on_start=17, on_end=22,
                    weekday_sop_windows=CURRENT["weekday_sop_windows"],
                    weekend_sop_end=CURRENT["weekend_sop_end"],
                    summer_months=CURRENT["summer_months"]),
        precedent="historically motivated",
        precedent_note=(
            "The same March 2019 CPUC-mandated transition moved the on-peak "
            "window LATER (11am-6pm -> 4pm-9pm) by regulatory design, "
            "explicitly to track the evening 'duck curve' net-demand peak as "
            "rooftop solar grew. A further 1-hour later shift (17-22) tests "
            "continuation of that same, real regulatory trend -- a real "
            "lever, not an invented one -- but this exact magnitude (17-22) "
            "was never itself observed (the 2019 transition landed at 16-21 "
            "and stopped there), so it is labeled 'historically motivated', "
            "not 'measured' (Codex adversarial review: an earlier version "
            "labeled this 'measured', overstating how directly this exact "
            "scenario traces to the cited history)."),
    ),
    "midday_sop_narrowed": dict(
        label="Midday super-off-peak narrowed (weekday 10-14 reverts to off-peak)",
        params=dict(on_start=CURRENT["on_start"], on_end=CURRENT["on_end"],
                    weekday_sop_windows=((0, 6),),
                    weekend_sop_end=CURRENT["weekend_sop_end"],
                    summer_months=CURRENT["summer_months"]),
        precedent="measured, in-corpus",
        precedent_note=(
            f"Reverts the weekday 10am-2pm super-off-peak window (added "
            f"{TA.MIDDAY_SOP_START.isoformat()}, per tou_audit.py's own "
            "changeover-day fit against statement residuals: 35.4 kWh "
            "unexplained with no changeover, 0.5 kWh with one) back to its "
            "pre-change state (off-peak) -- an actual structural change "
            "already observed in this household's own bill corpus, not a "
            "hypothetical one."),
    ),
    "summer_extended": dict(
        label="Summer season extended one month (Jun-Oct -> Jun-Nov)",
        params=dict(on_start=CURRENT["on_start"], on_end=CURRENT["on_end"],
                    weekday_sop_windows=CURRENT["weekday_sop_windows"],
                    weekend_sop_end=CURRENT["weekend_sop_end"],
                    summer_months=frozenset({6, 7, 8, 9, 10, 11})),
        precedent="hypothetical",
        precedent_note=(
            "No precedent found: SDG&E's summer (Jun-Oct, 5 months) is "
            "already longer than PG&E's or SCE's (Jun-Sep, 4 months each), "
            "and no CPUC proceeding defining a longer season was found. "
            "Modeled as a bounding hypothetical, motivated by real (if not "
            "yet regulatorily acted-on) evidence that California's fire/heat "
            "season is measurably lengthening into the traditionally cooler "
            "months (NOAA/Yale Climate Connections coverage of the Jan 2025 "
            "LA fires; Scripps Institution of Oceanography Santa Ana "
            "wind-timing research). Labeled hypothetical, not measured."),
    ),
}


def _pipeline(d):
    """(baseline_bill, behavior_save, battery_marginal_save) for one
    structure's frame -- EV-shift-only behavior saving (scenario a: 100%
    compliance), then the price-aware ("greedy") battery marginal on top of
    the shifted load, matching the report's own MID-package convention."""
    baseline_bill = br.bill(d, "imp", "exp")

    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    if br.EV_ANALYSIS and sessions:
        all_mask = [True] * len(sessions)
        imp_shifted, _moved = br.shift_ev(d, ev, sessions, all_mask, sop_idx, sop_ts)
    else:
        imp_shifted = d.imp.values.astype(float).copy()

    f = d.copy(); f["imp"] = imp_shifted
    behavior_bill = br.bill(f, "imp", "exp")
    behavior_save = baseline_bill - behavior_bill

    gen0 = d.exp.values.astype(float)
    imp_batt, exp_batt, _served, _thru = bdp.run_batt(
        d, imp_shifted, gen0, CAP_KWH, "greedy", power_kw=POWER_KW)
    battery_bill = bdp.billed(d, imp_batt, exp_batt)
    battery_marginal = behavior_bill - battery_bill

    return baseline_bill, behavior_save, battery_marginal


def _jsonify_structure(params):
    """Plain-JSON rendering of a scenario's window-structure params."""
    return {
        "on_start": params["on_start"],
        "on_end": params["on_end"],
        "weekday_sop_windows": [list(w) for w in params["weekday_sop_windows"]],
        "weekend_sop_end": params["weekend_sop_end"],
        "summer_months": sorted(params["summer_months"]),
    }


def main():
    d = br.load()
    d["imp"] = d.Consumption.astype(float)
    d["exp"] = d.Generation.astype(float)

    cur_baseline, cur_behavior_save, cur_battery_marginal = _pipeline(d)

    out = {
        "method": (
            "holds today's $/kWh rates fixed and varies the TOU WINDOW "
            "SHAPES instead (on-peak start/end, weekday midday "
            "super-off-peak window, summer-season months), re-running the "
            "same EV-shift (behavior_rebuild.shift_ev, 100% compliance) and "
            "price-aware battery dispatch (battery_dispatch_policies."
            "run_batt, greedy policy) pipelines against each scenario's own "
            "p/seas assignment -- both already read p/hour/seas off the "
            "frame they are given, so no changes to either module were "
            "needed."),
        "current_structure": _jsonify_structure(CURRENT),
        "current_figures_usd": {
            "baseline_bill": round(cur_baseline, 2),
            "behavior_save": round(cur_behavior_save, 2),
            "battery_marginal_save": round(cur_battery_marginal, 2),
        },
        "scenarios": {},
    }

    for key, spec in SCENARIOS.items():
        d_scen = assign_structure(d, **spec["params"])
        base, beh, batt = _pipeline(d_scen)
        baseline_delta = base - cur_baseline
        behavior_delta = beh - cur_behavior_save
        battery_delta = batt - cur_battery_marginal
        total_impact = baseline_delta - behavior_delta - battery_delta
        out["scenarios"][key] = {
            "label": spec["label"],
            "precedent": spec["precedent"],
            "precedent_note": spec["precedent_note"],
            "structure": _jsonify_structure(spec["params"]),
            "baseline_bill_usd": round(base, 2),
            "behavior_save_usd": round(beh, 2),
            "battery_marginal_save_usd": round(batt, 2),
            "baseline_delta_usd": round(baseline_delta, 2),
            "behavior_save_delta_usd": round(behavior_delta, 2),
            "battery_marginal_delta_usd": round(battery_delta, 2),
            "total_package_impact_usd": round(total_impact, 2),
        }

    worst_key = max(out["scenarios"], key=lambda k: out["scenarios"][k]["total_package_impact_usd"])
    worst = out["scenarios"][worst_key]
    out["worst_scenario"] = {
        "key": worst_key,
        "label": worst["label"],
        "total_package_impact_usd": worst["total_package_impact_usd"],
        "sentence": (
            f"{worst['label']} hurts most: it would cost this household an "
            f"extra ${worst['total_package_impact_usd']:.2f}/yr even after "
            "the EV shift and the price-aware battery, holding physical "
            "usage fixed at the measured year."),
    }

    root = repo_root()
    tmp = os.path.join(root, "data", "tou_structure_stress.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(root, "data", "tou_structure_stress.json"))
    print("wrote data/tou_structure_stress.json")
    print("worst scenario:", out["worst_scenario"]["sentence"])


if __name__ == "__main__":
    main()
