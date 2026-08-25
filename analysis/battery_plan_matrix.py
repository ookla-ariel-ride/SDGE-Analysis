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
the resolved dispatch artifact — see ORDERING CONTRACT below). The same
shared-window reasoning covers the mid-package free-fix shift below: both
behavior_rebuild.shift_ev and behavior_rebuild.shift_house select source and
destination intervals by TOU period LABEL only (on/off -> sop), so one shifted
import series serves all three plans exactly as the one dispatch trace does.

MID PACKAGE (issue #200): the artifact also prices the report's mid package —
this household's free behavior fix FIRST, then the same 13.5 kWh greedy
dispatch on the shifted year — under EACH plan, so a household whose ranking
favors a different plan can read what the package is worth on that plan. This
is ONE integrated pipeline re-billed end-to-end per plan (shift, then dispatch,
then bill the whole modified year under the plan's own table rates) — never a
sum of separately modeled deltas (CLAUDE.md section 9's one-pipeline rule).
Baseline: the SAME plan's modeled no-package year (the no_battery column), same
published-table rate basis, one rate vintage.

WHICH free fix that is comes from battery_dispatch_policies.free_fix_shift()
(issue #147) — scenario a, the EV charge reschedule, when household.has_ev;
scenario c, the flexible house-load shift, when it is false — so this script
cannot drift onto a different branch from the two artifacts that already use
that function. It used to call behavior_rebuild.shift_ev() unconditionally,
which on a household with no EV moves nothing: kwh_moved was 0 and the "mid
package" row was the battery-only row wearing a package label, while
package_results.py's MID for the same household was scenario c THEN the
battery. Two pipelines under one name is precisely what CLAUDE.md section 9
forbids. The applied scenario key is published as
mid_package_on_plans.free_fix_scenario.

ORDERING CONTRACT (this script runs AFTER the dispatch generator):
  battery_dispatch_policies.py  ->  battery_plan_matrix.py, in the SAME working
  directory. The canonical crosscheck below cites battery_dispatch_policies.json,
  which battery_dispatch_policies.py writes into the WORKING DIRECTORY;
  data/battery_dispatch_policies.json is only the last PROMOTED run. So that
  artifact is read whole from this run's copy when one is there, from the
  committed copy otherwise, and a disagreement between the two is announced
  loudly rather than resolved in silence (see _resolve_dispatch_artifact).
  CLAUDE.md's section 9 regeneration gate already runs the pair in this order.
  Whichever copy is used, its EV APPLICABILITY must match this run's intake
  flag (household.has_ev) or the run refuses -- an artifact from a household
  with a different answer to that question is a different household's artifact,
  and the crosscheck block below would publish its baseline and battery value
  as this household's (_check_ev_applicability). The $100 crosscheck tolerance
  is NOT that guard: it is a tolerance on a magnitude, so two households whose
  battery economics happen to land within $100 of each other pass it while
  composing one artifact out of both.

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
from battery_dispatch_policies import (run_batt, free_fix_shift, CHARGE_KW,
                                       FREE_FIX_SCENARIO_EV, FREE_FIX_SCENARIO_NO_EV)

# What the mid-package row actually did, per free-fix scenario. The row is one
# integrated shift-then-dispatch run re-billed per plan either way; only the
# NAME of the shift that led it differs, so the method sentence has to name the
# fix that really ran rather than assert the EV one on every household.
_MID_PACKAGE_METHOD = {
    FREE_FIX_SCENARIO_EV:
        ("integrated mid package: EV shift scenario a (all sessions, "
         "behavior_rebuild.shift_ev) FIRST, then the price-aware PW3 "
         "greedy dispatch (13.5 kWh, 11.5 kW discharge / 5 kW charge) "
         "on the shifted year, and the WHOLE modified year re-billed "
         "end-to-end under each plan's own published-table rates — one "
         "pipeline, never a sum of separately modeled deltas. Baseline "
         "= the same plan's modeled no-package year (the no_battery "
         "column), same published-table rate basis, one rate vintage."),
    FREE_FIX_SCENARIO_NO_EV:
        ("integrated mid package: house-load shift scenario c "
         "(behavior_rebuild.shift_house — household.has_ev is false, so the "
         "free fix that precedes the battery is the flexible on-peak house "
         "load, not the EV charge reschedule) FIRST, then the price-aware PW3 "
         "greedy dispatch (13.5 kWh, 11.5 kW discharge / 5 kW charge) "
         "on the shifted year, and the WHOLE modified year re-billed "
         "end-to-end under each plan's own published-table rates — one "
         "pipeline, never a sum of separately modeled deltas. Baseline "
         "= the same plan's modeled no-package year (the no_battery "
         "column), same published-table rate basis, one rate vintage."),
}

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


def _check_ev_applicability(doc, path):
    """Fail closed when the resolved dispatch artifact belongs to a household
    with a DIFFERENT EV applicability than this run's intake declares.

    The two sides of the comparison are two independent facts, and this script
    reads both:
      * br.EV_ANALYSIS -- behavior_rebuild's household.has_ev predicate, read
        from THIS run's private/household.yaml. The intake flag is the
        authority on whether this household has an EV (CLAUDE.md section 0),
        and free_fix_shift() below keys the mid-package row off the same
        predicate;
      * the artifact's post_behavior.free_fix_scenario -- FREE_FIX_SCENARIO_EV
        ("a", the EV charge reschedule) on an EV household's artifact,
        FREE_FIX_SCENARIO_NO_EV ("c", the flexible house-load shift) on a
        no-EV household's. battery_dispatch_policies.py publishes it for
        exactly this purpose.

    A disagreement means the artifact was written for a DIFFERENT household (or
    before the intake flag changed), and neither direction may be resolved
    silently:
      * intake says no EV, artifact ran the EV fix -> canonical_crosscheck_
        ev_tou_5 would publish ANOTHER household's no-battery baseline and
        battery value beside this household's own scenario-c mid-package row,
        in one self-contradicting artifact;
      * intake says EV, artifact ran the house-load fix -> this household's
        real EV-shift package would be crosschecked against, and cited as, a
        run that never moved a charging session.

    The two existing $100 assertions do NOT cover this. They compare
    MAGNITUDES, so any two households whose battery economics land within $100
    of each other pass them; the applicability question is about identity, not
    tolerance.

    A missing or unrecognised free_fix_scenario is not an applicability answer.
    An artifact with no post_behavior block at all is reported by the
    mid-package crosscheck's own message further down; one that carries the
    block but cannot say which free fix it ran is refused here, since there is
    then nothing to check this household against.

    The remedy is the same in both directions and is this script's own ordering
    contract: run battery_dispatch_policies.py in this working directory first,
    so the dispatch artifact is THIS household's."""
    pb = doc.get("post_behavior")
    if not isinstance(pb, dict):
        return          # reported by the post_behavior.mid check in __main__
    scen = pb.get("free_fix_scenario")
    if scen not in (FREE_FIX_SCENARIO_EV, FREE_FIX_SCENARIO_NO_EV):
        raise SystemExit(
            f"the dispatch artifact {path} has a post_behavior block but no "
            f"usable post_behavior.free_fix_scenario (got {scen!r}; expected "
            f"{FREE_FIX_SCENARIO_EV!r} on a household with an EV or "
            f"{FREE_FIX_SCENARIO_NO_EV!r} on one without). Without it the "
            "artifact cannot say which household it belongs to, so this run "
            "cannot check it against its own intake flag household.has_ev "
            "(CLAUDE.md section 0: every figure must be this household's). "
            "Regenerate it with battery_dispatch_policies.py in this working "
            "directory (see the ORDERING CONTRACT in the module docstring).")
    artifact_has_ev = scen == FREE_FIX_SCENARIO_EV
    ev_applies = br.EV_ANALYSIS       # read at call time; tests rebind it
    if artifact_has_ev == ev_applies:
        return
    if ev_applies:
        flag_says = ("household.has_ev is NOT false (the intake applicability "
                     "flag in private/household.yaml says this household HAS "
                     "an EV)")
        harm = ("Crosschecking this run against that artifact would cite a run "
                "that never moved a charging session as the canonical figure "
                "for this household's EV-shift package")
    else:
        flag_says = ("household.has_ev is false (the intake applicability flag "
                     "in private/household.yaml says this household has NO EV)")
        harm = ("Crosschecking this run against that artifact would publish "
                "ANOTHER household's no-battery baseline and battery value in "
                "canonical_crosscheck_ev_tou_5, beside this household's own "
                "scenario-c mid-package row, in one self-contradicting "
                "artifact")
    raise SystemExit(
        f"EV APPLICABILITY MISMATCH between this run and its dispatch "
        f"artifact: this run's intake says {flag_says}, but the dispatch "
        f"artifact {path} records post_behavior.free_fix_scenario {scen!r}, "
        f"the free fix of a household with "
        f"{'an EV' if artifact_has_ev else 'NO EV'}. {harm} (CLAUDE.md "
        "section 0: every figure must be this household's). Run "
        "battery_dispatch_policies.py in this working directory "
        f"({pathlib.Path.cwd()}) first so the dispatch artifact belongs to "
        "THIS household; this script will not crosscheck one household's plan "
        "matrix against another household's dispatch artifact.")


def _resolve_dispatch_artifact(root):
    """Read battery_dispatch_policies.json whole for the canonical crosscheck
    below, preferring THIS run's copy in the CWD over the committed data/
    copy (same convention as carbon_fullyear.py's _scenario_a_saved /
    deep_analyses.py's _base_save, generalized to a whole-document read since
    the crosscheck needs two fields, no_battery and battery_value, not one
    scalar).

    Returns (value, source) -- source is a STABLE, machine-independent label
    for which copy this run's figures came from ("battery_dispatch_policies.json
    (this run's copy)" vs. "data/battery_dispatch_policies.json (committed)"),
    NOT always the latter: once a current-run copy can win (the whole point
    of this resolver), the published canonical_crosscheck.basis field must
    say so, or the artifact misstates its own provenance in exactly the
    scenario this resolver was built to handle (a current-run copy diverging
    from a stale committed one) -- caught in Codex review. The label is
    deliberately NOT the resolved absolute path: that would bake this
    machine's own working-directory location into a committed artifact,
    breaking the section 9 byte-identical-regeneration gate the moment two
    different checkouts ran it from different paths.

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
        _check_ev_applicability(v, committed)
        print(f"NOTICE: no current-run {DISPATCH_JSON} in {pathlib.Path.cwd()}; "
              f"canonical crosscheck read from the committed {committed}. If "
              "this run's dispatch inputs changed, run "
              "battery_dispatch_policies.py here FIRST.")
        return v, "data/battery_dispatch_policies.json (committed)"
    v = _read_dispatch_json(run)
    # The guard covers the CURRENT-RUN copy too, not just the committed
    # fallback: a stale battery_dispatch_policies.json left in this working
    # directory by another household's run is the same defect, one directory
    # across, and it WINS the resolution below.
    _check_ev_applicability(v, run)
    if committed.exists() and run.samefile(committed):
        print(f"NOTICE: canonical crosscheck read from {run} (the working "
              "directory IS the committed data/ directory).")
        return v, "data/battery_dispatch_policies.json (this run's copy; CWD is data/)"
    if not committed.exists():
        print(f"NOTICE: canonical crosscheck read from this run's {run} (no "
              f"committed {committed} to compare against).")
        return v, "battery_dispatch_policies.json (this run's copy)"
    c = _read_dispatch_json(committed)
    if c == v:
        print(f"NOTICE: canonical crosscheck read from this run's {run} "
              f"(agrees with the committed {committed}).")
        return v, "battery_dispatch_policies.json (this run's copy, agrees with data/battery_dispatch_policies.json)"
    bar = "!" * 72
    print(bar)
    print(f"NOTICE -- STALE COMMITTED ARTIFACT: this run's {DISPATCH_JSON} "
          f"differs from the committed {committed}.")
    print(f"  Using THIS RUN's {run}. The committed copy has not been "
          "promoted; CLAUDE.md's section 9 gate will fail until it is.")
    print(bar)
    return v, "battery_dispatch_policies.json (this run's copy; committed data/battery_dispatch_policies.json is STALE)"


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
    no_b_by_plan = {}
    for plan in PLANS:
        no_b = bill_plan(plan, seas, per, imp0, gen0)
        with_b = bill_plan(plan, seas, per, imp_b, exp_b)
        assert abs(no_b - ref[plan]) < 1.0, \
            f"{plan}: no-battery ${no_b:,.2f} fails tie-out to plan_results.csv ${ref[plan]:,.2f}"
        no_b_by_plan[plan] = no_b
        plans[plan] = {"no_battery": round(no_b), "with_battery": round(with_b),
                       "battery_value": round(no_b - with_b)}
        print(f"{plan:9s} no-batt ${no_b:8,.0f}  with PW3 ${with_b:8,.0f}  "
              f"battery value ${no_b - with_b:6,.0f}/yr")

    # ---- mid package (this household's free fix, then the 13.5 kWh battery), per plan
    # One integrated pipeline (CLAUDE.md section 9): shift first (exactly as
    # battery_dispatch_policies.py's post_behavior block does), dispatch on the
    # shifted year, then re-bill the WHOLE modified year under each plan's own
    # table rates. Never a sum of separately modeled deltas. The shift is
    # plan-independent for the same reason the single dispatch trace is: both
    # br.shift_ev and br.shift_house select source and destination intervals by
    # TOU period label only (on/off -> sop) and all three plans share the 2026
    # three-period windows.
    #
    # WHICH fix runs comes from battery_dispatch_policies.free_fix_shift(), the
    # single implementation of that branch: scenario a (the EV charge
    # reschedule) when household.has_ev, scenario c (the flexible house-load
    # shift) when it is false. This used to call br.shift_ev() unconditionally,
    # and on a household with no EV that is a NO-OP -- moved was 0.0 and the
    # "mid package" row silently equalled the battery-only row while still
    # carrying a package label. package_results.py's MID for the same household
    # is scenario c THEN the battery, so the two artifacts described different
    # pipelines under the same name. Now the row is the real free fix plus the
    # battery on every household, and mid_package_on_plans.free_fix_scenario
    # records which fix it was.
    imp_sh, moved, fix_scenario = free_fix_shift(d, imp0)
    imp_p, exp_p, _, _ = run_batt(d, imp_sh, gen0, 13.5, "greedy", charge_kw=CHARGE_KW)
    pkg = {}
    for plan in PLANS:
        pkg_bill = bill_plan(plan, seas, per, imp_p, exp_p)
        pkg[plan] = {"package_bill": round(pkg_bill),
                     "package_save": round(no_b_by_plan[plan] - pkg_bill)}
        print(f"{plan:9s} mid package ${pkg_bill:8,.0f}  "
              f"saves ${no_b_by_plan[plan] - pkg_bill:6,.0f}/yr")

    # cross-check the EV-TOU-5 column against the canonical-engine artifact: the
    # table-rate battery value must agree with the published canonical figure to ~$100
    canon, canon_source = _resolve_dispatch_artifact(root)
    assert abs(plans["EV-TOU-5"]["battery_value"] - canon["pw3"]["greedy"]["save"]) < 100, \
        "EV-TOU-5 battery value diverged from the canonical dispatch artifact"
    # same crosscheck for the mid package: the table-rate EV-TOU-5 package save
    # must agree with the canonical engine's post_behavior.mid figure to ~$100
    # (the same rate-basis gap as the battery crosscheck above). Fail-closed:
    # a dispatch artifact without the block aborts the run.
    try:
        canon_mid = canon["post_behavior"]["mid"]
    except KeyError:
        raise SystemExit(
            f"the resolved dispatch artifact ({canon_source}) has no "
            "post_behavior.mid block for the mid-package crosscheck. Regenerate "
            "it with battery_dispatch_policies.py first (see the ORDERING "
            "CONTRACT in the module docstring).")
    assert abs(pkg["EV-TOU-5"]["package_save"] - canon_mid["combined_save"]) < 100, \
        (f"EV-TOU-5 mid package save diverged from the canonical dispatch "
         f"artifact: table-rate ${pkg['EV-TOU-5']['package_save']} vs canonical "
         f"post_behavior.mid.combined_save ${canon_mid['combined_save']} "
         "(tolerance $100)")
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
            "basis": (f"{canon_source} — bill-derived rates, rates.bill_nem monthly "
                      "NEM netting, canonical holiday rule; the published EV-TOU-5 "
                      "battery economics")},
        "mid_package_on_plans": {
            "method": _MID_PACKAGE_METHOD[fix_scenario],
            "kwh_moved": round(moved),
            # WHICH free fix the kwh_moved and every package_save below sit on
            # top of, straight from free_fix_shift() rather than re-derived
            # here from an intake flag this artifact's readers cannot see.
            "free_fix_scenario": fix_scenario,
            "plans": pkg,
            "canonical_crosscheck_ev_tou_5": {
                "combined_save": canon_mid["combined_save"],
                "bill": canon_mid["bill"],
                "basis": (f"{canon_source} post_behavior.mid — bill-derived rates, "
                          "rates.bill_nem monthly NEM netting, the same integrated "
                          "shift-then-battery pipeline; the table-rate EV-TOU-5 "
                          "package save is asserted against combined_save within "
                          "$100 (rate-basis gap)")},
        },
    }
    tmp = os.path.join(root, "data", "battery_plan_matrix.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(root, "data", "battery_plan_matrix.json"))
    print("wrote data/battery_plan_matrix.json")
