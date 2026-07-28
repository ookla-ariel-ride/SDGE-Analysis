#!/usr/bin/env python3
"""Battery x plan matrix (report §4) — the integrated method, regenerable.

Question: does a battery change which rate plan is best? For the top-3 plans in
data/plan_results.csv (EV-TOU-5 and its two nearest competitors, EV-TOU-2 and
TOU-ELEC, CEA generation, no relief credit), bill the same 365-day year WITHOUT
a battery and WITH the price-aware 13.5 kWh / 11.5 kW Powerwall 3 dispatch
(run_batt "greedy" imported from battery_dispatch_policies.py), under each
plan's own rate structure.

Rates basis: PUBLISHED RATE-TABLE values (research/rates-reference.md; the same
tables as analyze*.py) — the CLAUDE.md §9 canonical-rates exception for
cross-plan RANKING. Billing replicates analyze_norelief.py exactly (interval
netting, export credit = max(rate - NBC, 0), BSC x 365, holiday-as-weekend TOU
assignment) so the no-battery column ties out to the committed
data/plan_results.csv to the dollar (asserted below).

Window note: all three plans share the same 2026 three-period TOU windows, so a
single dispatch trace serves all three. Day types come from rates.off_peak_day,
the same canonical rule the published EV-TOU-5 economics use, so the two bases
now agree on which days take weekend windows; what still separates this column
from the canonical figures is the rate basis (published tables here, bill-derived
there). Both are recorded in the artifact (canonical_crosscheck, asserted against
the committed dispatch artifact).

Output: data/battery_plan_matrix.json (repo-root resolved, so the
private/verify sandbox pattern needs no path edits).
"""
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

import rates as R                 # canonical TOU assignment
import behavior_rebuild as br
from battery_dispatch_policies import run_batt

# ---- published rate-table values (ranking-only; identical to analyze*.py) ----
WFNBC_DWR = 0.00591
PCIA = 0.02828
NBC = 0.01515 + 0.00000 - 0.00007 + WFNBC_DWR
BSC = 0.79343

UDC = {
 "EV-TOU-5": {"S": {"on": 0.31711, "off": 0.31711, "sop": 0.04114},
              "W": {"on": 0.31711, "off": 0.31711, "sop": 0.04114}},
 "EV-TOU-2": {"S": {"on": 0.30372, "off": 0.30372, "sop": 0.16275},
              "W": {"on": 0.30372, "off": 0.30372, "sop": 0.16275}},
 "TOU-ELEC": {"S": {"on": 0.25317, "off": 0.25317, "sop": 0.25317},
              "W": {"on": 0.25317, "off": 0.25317, "sop": 0.25317}},
}
CEA_GEN = {"S": {"on": 0.51684, "off": 0.15975, "sop": 0.04961},
           "W": {"on": 0.24430, "off": 0.15782, "sop": 0.05187}}  # same row, all 3 plans
PLANS = ["EV-TOU-5", "EV-TOU-2", "TOU-ELEC"]


def repo_root():
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = base
        for _ in range(8):
            if os.path.exists(os.path.join(p, "data", "plan_results.csv")):
                return p
            p = os.path.dirname(p)
    raise SystemExit("repo root not found (data/plan_results.csv)")


# Holiday calendar comes from rates.off_peak_day (the canonical, bill-confirmed
# rule); this module used to keep its own copy of the same eight dates.


def bill_plan(plan, seas, per, imp, exp):
    """analyze_norelief.py's interval method: charges - credits + BSC, table rates."""
    rate = np.array([UDC[plan][s][p] + WFNBC_DWR + PCIA + CEA_GEN[s][p]
                     for s, p in zip(seas, per)])
    return float((imp * rate).sum() - (exp * np.maximum(rate - NBC, 0)).sum()) + BSC * 365


if __name__ == "__main__":
    root = repo_root()
    d = br.load()
    # holiday-aware TOU assignment (analyze.py convention -> ties to plan_results.csv)
    h = d.hour.values
    wk = d.dt.dt.date.map(R.off_peak_day)
    d = d.copy()
    d["p"] = np.where((h >= 16) & (h < 21), "on",
                      np.where(wk, np.where(h < 14, "sop", "off"),
                               np.where((h < 6) | ((h >= 10) & (h < 14)), "sop", "off")))
    seas, per = d.seas.values, d.p.values
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)

    # one dispatch trace (shared TOU windows across the three plans)
    imp_b, exp_b, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy")

    ref = pd.read_csv(os.path.join(root, "data", "plan_results.csv"))
    ref = ref[ref.provider == "CEA"].set_index("plan").total.to_dict()

    plans = {}
    for plan in PLANS:
        no_b = bill_plan(plan, seas, per, imp0, gen0)
        with_b = bill_plan(plan, seas, per, imp_b, exp_b)
        assert abs(no_b - ref[plan]) < 1.0, \
            f"{plan}: no-battery ${no_b:,.2f} fails tie-out to plan_results.csv ${ref[plan]:,.2f}"
        plans[plan] = {"no_battery": round(no_b), "with_battery": round(with_b),
                       "battery_value": round(no_b - with_b)}
        print(f"{plan:9s} no-batt ${no_b:8,.0f}  with PW3 ${with_b:8,.0f}  "
              f"battery value ${no_b - with_b:6,.0f}/yr")

    # cross-check the EV-TOU-5 column against the canonical-engine artifact: the
    # table-rate battery value must agree with the published canonical figure to ~$100
    canon = json.load(open(os.path.join(root, "data", "battery_dispatch_policies.json")))
    assert abs(plans["EV-TOU-5"]["battery_value"] - canon["pw3"]["greedy"]["save"]) < 100, \
        "EV-TOU-5 battery value diverged from the canonical dispatch artifact"
    out = {
        "method": ("integrated: bill the year with and without the price-aware PW3 "
                   "dispatch (run_batt 'greedy', 13.5 kWh / 11.5 kW, 90% RTE, EV-spillover "
                   "exclusion) under each plan's own rate structure"),
        "rates_basis": ("published rate tables, CEA generation without relief credit "
                        "(ranking-only; ties out to data/plan_results.csv, asserted). "
                        "Holiday-as-weekend TOU assignment per analyze.py (TECHNICAL §6.5)."),
        "plans_selection": "EV-TOU-5 + the two nearest competitors in data/plan_results.csv",
        "dispatch_note": ("all three plans share the same 2026 three-period TOU windows, so "
                          "one dispatch trace is billed under each plan; kWh served "
                          f"{round(served)}, cycles/day {round(thru / 13.5 / 365, 2)}"),
        "plans": plans,
        "canonical_crosscheck_ev_tou_5": {
            "no_battery": canon["baseline_bill_current_rates"],
            "battery_value": canon["pw3"]["greedy"]["save"],
            "basis": ("data/battery_dispatch_policies.json — bill-derived rates, "
                      "rates.bill_nem monthly NEM netting, canonical holiday rule; "
                      "the published EV-TOU-5 battery economics")},
    }
    tmp = os.path.join(root, "data", "battery_plan_matrix.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(root, "data", "battery_plan_matrix.json"))
    print("wrote data/battery_plan_matrix.json")
