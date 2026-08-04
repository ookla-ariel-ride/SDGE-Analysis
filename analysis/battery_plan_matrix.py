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
the resolved dispatch artifact — see ORDERING CONTRACT below).

ORDERING CONTRACT (this script runs AFTER the dispatch generator):
  battery_dispatch_policies.py  ->  battery_plan_matrix.py, in the SAME working
  directory. The canonical crosscheck below cites battery_dispatch_policies.json,
  which battery_dispatch_policies.py writes into the WORKING DIRECTORY;
  data/battery_dispatch_policies.json is only the last PROMOTED run. So that
  artifact is read whole from this run's copy when one is there, from the
  committed copy otherwise, and a disagreement between the two is announced
  loudly rather than resolved in silence (see _resolve_dispatch_artifact).
  CLAUDE.md's section 9 regeneration gate already runs the pair in this order.

Output: data/battery_plan_matrix.json (repo-root resolved, so the
private/verify sandbox pattern needs no path edits).
"""
import datetime as dt
import json
import os
import pathlib

import numpy as np
import pandas as pd

import rates as R                 # canonical TOU assignment
import behavior_rebuild as br
from battery_dispatch_policies import run_batt, CHARGE_KW

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


DISPATCH_JSON = "battery_dispatch_policies.json"  # written to the CWD by battery_dispatch_policies.py


def _read_dispatch_json(path):
    """Parse one battery_dispatch_policies.json whole.

    Fail-closed: a malformed or unreadable copy is an ERROR, never a licence
    to fall back to the other one — falling back past a broken artifact is
    how a stale figure gets published under a citation that looks current.
    """
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"{path}: cannot parse the dispatch artifact ({type(e).__name__}: "
            f"{e}). Regenerate it with battery_dispatch_policies.py; this "
            "script will not fall back past a broken artifact.")


def _resolve_dispatch_artifact(root):
    """Read battery_dispatch_policies.json whole for the canonical crosscheck
    below, preferring THIS run's copy in the CWD over the committed data/
    copy (same convention as carbon_fullyear.py's _scenario_a_saved /
    deep_analyses.py's _base_save, generalized to a whole-document read since
    the crosscheck needs two fields, no_battery and battery_value, not one
    scalar).

    NOTE: this is unrelated to analysis/extended_findings.py:216, which reads
    the SAME filename but DELIBERATELY always from data/ — that script is a
    consistency gate asserting the committed artifact agrees with what it
    just computed, so it must never prefer a current-run copy. This script
    has no such gate role for battery_dispatch_policies.json; it only wants
    the freshest figure for its own crosscheck, so it follows the normal
    current-run-preferred convention instead.

    Resolution order:
      1. current-run copy in the CWD (battery_dispatch_policies.py's
         product) — used when present;
      2. committed data/ copy — used only when there is no current-run copy,
         with a NOTICE saying so;
      3. both present and DISAGREEING — this run's copy wins, and the
         mismatch is announced loudly: the committed copy is stale relative
         to this run, and the section 9 regeneration gate will fail until it
         is promoted.
    """
    run = pathlib.Path.cwd() / DISPATCH_JSON
    committed = pathlib.Path(root) / "data" / DISPATCH_JSON
    if not run.exists():
        if not committed.exists():
            raise SystemExit(
                f"no {DISPATCH_JSON} artifact: neither a current-run {run} "
                f"nor the committed {committed} exists. Run "
                "battery_dispatch_policies.py in this working directory "
                "first (see the ordering contract above).")
        v = _read_dispatch_json(committed)
        print(f"NOTICE: no current-run {DISPATCH_JSON} in {pathlib.Path.cwd()}; "
              f"canonical crosscheck read from the committed {committed}. If "
              "this run's dispatch inputs changed, run "
              "battery_dispatch_policies.py here FIRST.")
        return v
    v = _read_dispatch_json(run)
    if committed.exists() and run.samefile(committed):
        print(f"NOTICE: canonical crosscheck read from {run} (the working "
              "directory IS the committed data/ directory).")
        return v
    if not committed.exists():
        print(f"NOTICE: canonical crosscheck read from this run's {run} (no "
              f"committed {committed} to compare against).")
        return v
    c = _read_dispatch_json(committed)
    if c == v:
        print(f"NOTICE: canonical crosscheck read from this run's {run} "
              f"(agrees with the committed {committed}).")
        return v
    bar = "!" * 72
    print(bar)
    print(f"NOTICE -- STALE COMMITTED ARTIFACT: this run's {DISPATCH_JSON} "
          f"differs from the committed {committed}.")
    print(f"  Using THIS RUN's {run}. The committed copy has not been "
          "promoted; CLAUDE.md's section 9 gate will fail until it is.")
    print(bar)
    return v


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
    # TOU assignment from the canonical module. This used to be a vectorised
    # re-implementation of the same windows; it agreed with rates.period_at only
    # for as long as nobody moved a boundary, and a window change would have made
    # the plan-ranking artifact drift away from the published economics silently.
    d = d.copy()
    d["p"] = [R.period_at(t) for t in d.dt]
    seas, per = d.seas.values, d.p.values
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)

    # one dispatch trace (shared TOU windows across the three plans). charge_kw
    # (issue #40) is this household's real, cited Powerwall 3 charge rating
    # (5 kW, vs. 11.5 kW discharge) -- imported from battery_dispatch_
    # policies.py so the two scripts cannot drift onto different figures.
    imp_b, exp_b, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy", charge_kw=CHARGE_KW)

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
    canon = _resolve_dispatch_artifact(root)
    assert abs(plans["EV-TOU-5"]["battery_value"] - canon["pw3"]["greedy"]["save"]) < 100, \
        "EV-TOU-5 battery value diverged from the canonical dispatch artifact"
    out = {
        "method": ("integrated: bill the year with and without the price-aware PW3 "
                   "dispatch (run_batt 'greedy', 13.5 kWh, 11.5 kW discharge / 5 kW charge "
                   "(Tesla's own datasheet, see research/battery-research-notes.md), 90% RTE, "
                   "EV-spillover exclusion) under each plan's own rate structure"),
        "rates_basis": ("published rate tables, CEA generation without relief credit "
                        "(ranking-only; ties out to data/plan_results.csv, asserted). "
                        "Canonical TOU assignment via rates.period_at, holidays included."),
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
