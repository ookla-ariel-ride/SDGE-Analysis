#!/usr/bin/env python3
"""Behavioural tests for battery_dispatch_policies.py's discharge-eligibility rule.

ISSUE #147. run_batt() refuses to serve an import of 2.5 kW or more outside
on-peak, on the ground that such an import is EV charging spilling out of its
window and the free schedule fix is about to move it. That is a claim about a
household WITH an EV. On a household whose intake says household.has_ev is
false there is no spillover, and the same rule withheld ordinary house load --
a heat pump, an oven, a well pump -- from a battery that should have served it.

These cases pin BOTH halves of the fixed rule, because a build that simply
deleted the exclusion passes the no-EV half on its own:

  no EV   an off-peak import above 2.5 kW IS served, by the named kWh
  has EV  the SAME interval is still excluded

and they pin that the artifact's notes.ev_exclusion says something true on each
household rather than asserting an exclusion that did not run.

battery_dispatch_policies.py imports behavior_rebuild.py, which reads
private/household.yaml at its own module top level and fails closed (SystemExit)
when it is absent. So, as test_battery_sizing_curve.py already does, point
household.PATH at a synthetic invented household BEFORE importing: this file
then imports cleanly on any checkout, private/ or not. Every dispatch case runs
on a hand-built 96-interval frame and needs no archive at all; the one case that
reads the committed artifact SKIPS when it is absent.

Run from the repo root:  ./.venv/bin/python analysis/test_battery_dispatch_policies.py
"""
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\n  has_ev: true\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import behavior_rebuild as br                  # noqa: E402
import battery_dispatch_policies as B          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "data" / "battery_dispatch_policies.json"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet.
    Counted as neither pass nor fail."""


EPS = 1e-9

# One 15-minute interval drawing 4 kW. Above the 2.5 kW spillover threshold, so
# it is exactly the interval the rule decides about, and 1.0 kWh so the assertion
# can name the energy rather than a ratio.
SPILL_KW = 4.0
SPILL_KWH = SPILL_KW * 0.25


def _frame():
    """A 96-interval day with an explicit TOU column, built here rather than
    read off rates.period_at().

    run_batt() reads only d.p, d.hour and len(d), and the point of these cases
    is the eligibility rule, not this household's tariff -- so the periods are
    stated outright: super-off-peak before 06:00, on-peak 16:00-21:00, off-peak
    everywhere else.
    """
    dtr = pd.date_range("2026-01-07", periods=96, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = ["sop" if h < 6 else ("on" if 16 <= h < 21 else "off") for h in d.hour]
    return d


def _one_import(hour):
    """A baseline that imports SPILL_KW for the single interval starting at
    `hour`, and nothing anywhere else. No generation, so the battery never
    charges from surplus and the only thing it can do is discharge."""
    d = _frame()
    idx = int(round(hour * 4))
    imp0 = np.zeros(96)
    imp0[idx] = SPILL_KWH
    return d, imp0, np.zeros(96), idx


def _served(policy, hour, has_ev, cap=13.5):
    """Run one dispatch on a full pack with the EV gate forced either way.

    br.EV_ANALYSIS is the intake flag household.has_ev; run_batt() reads it at
    call time, so a case sets it, runs, and puts it back.
    """
    d, imp0, gen0, idx = _one_import(hour)
    was = br.EV_ANALYSIS
    br.EV_ANALYSIS = has_ev
    try:
        imp, exp, served, thru = B.run_batt(d, imp0, gen0, cap, policy,
                                            charge_kw=B.CHARGE_KW, soc0=cap)
    finally:
        br.EV_ANALYSIS = was
    return served, imp[idx]


# ---------------------------------------------------------------------------
# (a) the rule itself, both halves, both policies that carry it
# ---------------------------------------------------------------------------
@case
def case_no_ev_household_serves_a_high_power_offpeak_import():
    """The case this issue exists for: with household.has_ev false there is no
    EV spillover, so a 4 kW off-peak import is ordinary house load and the
    battery must serve it."""
    served, left = _served("greedy", 12.0, has_ev=False)
    assert abs(served - SPILL_KWH) < EPS, (
        f"a no-EV household's {SPILL_KW} kW off-peak import was not served: "
        f"the battery delivered {served} kWh, expected {SPILL_KWH} kWh")
    assert abs(left) < EPS, (
        f"the served interval still imports {left} kWh from the grid")
    return (f"no EV: the {SPILL_KW} kW off-peak interval is served in full "
            f"({SPILL_KWH} kWh) and its grid import falls to 0")


@case
def case_ev_household_still_excludes_the_same_offpeak_import():
    """The positive control. Without it, a build that simply deleted the
    exclusion would pass the case above. With an EV declared, the SAME interval
    is spillover the free schedule fix moves, and the battery must not serve
    it."""
    served, left = _served("greedy", 12.0, has_ev=True)
    assert abs(served) < EPS, (
        f"an EV household's {SPILL_KW} kW off-peak import was served anyway: "
        f"the battery delivered {served} kWh, expected 0 -- the EV-spillover "
        "exclusion is not being applied")
    assert abs(left - SPILL_KWH) < EPS, (
        f"the excluded interval's grid import moved to {left} kWh, expected "
        f"{SPILL_KWH} kWh untouched")
    return (f"has EV: the same {SPILL_KW} kW off-peak interval is excluded "
            f"(0 kWh served, all {SPILL_KWH} kWh still imported)")


@case
def case_twowin_carries_the_same_gate_as_greedy():
    """Both policies carry the 2.5 kW term, so both had the defect. twowin's
    second window is 06:00-09:00, so the interval is placed at 07:00."""
    no_ev, no_ev_left = _served("twowin", 7.0, has_ev=False)
    has_ev, has_ev_left = _served("twowin", 7.0, has_ev=True)
    assert abs(no_ev - SPILL_KWH) < EPS, (
        f"twowin on a no-EV household did not serve its 07:00 {SPILL_KW} kW "
        f"import: {no_ev} kWh served, expected {SPILL_KWH} kWh")
    assert abs(has_ev) < EPS, (
        f"twowin on an EV household served a {SPILL_KW} kW 07:00 import it "
        f"should have excluded: {has_ev} kWh")
    return (f"twowin: {SPILL_KWH} kWh served at 07:00 with no EV, 0 kWh with "
            "one -- the same gate greedy applies")


@case
def case_an_ordinary_sub_threshold_import_is_served_on_both_households():
    """A control on the control: the gate must move ONLY intervals at or above
    2.5 kW. A 1 kW off-peak import is servable whatever the intake flag says,
    so a case that reported a difference here would be reading some other
    change."""
    d = _frame()
    idx = 48
    imp0 = np.zeros(96)
    imp0[idx] = 0.25          # 1.0 kW, below the 2.5 kW threshold
    out = {}
    for has_ev in (True, False):
        was = br.EV_ANALYSIS
        br.EV_ANALYSIS = has_ev
        try:
            _, _, served, _ = B.run_batt(d, imp0, np.zeros(96), 13.5, "greedy",
                                         charge_kw=B.CHARGE_KW, soc0=13.5)
        finally:
            br.EV_ANALYSIS = was
        out[has_ev] = served
    assert abs(out[True] - 0.25) < EPS and abs(out[False] - 0.25) < EPS, out
    return ("a 1.0 kW off-peak import is served on both households "
            f"({out[True]} kWh each) -- the gate touches only >= 2.5 kW")


@case
def case_super_off_peak_charges_from_surplus_and_never_discharges():
    """The gate must not widen the discharge window into super-off-peak, on
    either household: a 12.5c import is never worth a stored kWh.

    Asserted on the CHARGE side as well as the discharge side, because the
    discharge side alone cannot see the defect. run_batt()'s super-off-peak
    branch `continue`s before the discharge branch is reached, so dropping the
    `p[i] != "sop"` term leaves `served` at zero anyway -- what it actually
    breaks is the charge test one line above it, which stops the interval's own
    solar surplus being stored. The interval therefore carries BOTH flows and
    the case checks both."""
    d = _frame()
    idx = 8                                  # 02:00, super-off-peak
    imp0 = np.zeros(96)
    exp0 = np.zeros(96)
    imp0[idx] = SPILL_KWH                    # 4 kW import
    exp0[idx] = 0.5                          # and 2 kW of export to store
    out = {}
    for has_ev in (True, False):
        was = br.EV_ANALYSIS
        br.EV_ANALYSIS = has_ev
        try:
            imp, exp, served, _ = B.run_batt(d, imp0, exp0, 13.5, "greedy",
                                             charge_kw=B.CHARGE_KW, soc0=0.0)
        finally:
            br.EV_ANALYSIS = was
        out[has_ev] = (served, float(exp[idx]), float(imp[idx]))
    for has_ev, (served, left_exp, imp_at) in out.items():
        assert abs(served) < EPS, (
            f"has_ev={has_ev}: a super-off-peak import was discharged against "
            f"({served} kWh served)", out)
        assert left_exp < exp0[idx] - EPS, (
            f"has_ev={has_ev}: the super-off-peak interval's surplus was not "
            f"stored ({left_exp} kWh still exported of {exp0[idx]}) -- the "
            "discharge window has widened into super-off-peak", out)
        assert imp_at >= SPILL_KWH - EPS, (
            f"has_ev={has_ev}: the super-off-peak import shrank to {imp_at} kWh",
            out)
    return ("super-off-peak on both households: 0 kWh served, its own surplus "
            f"stored ({exp0[idx] - out[True][1]:.2f} kWh) and its import intact")


# ---------------------------------------------------------------------------
# (b) the artifact must describe the rule that actually ran
# ---------------------------------------------------------------------------
@case
def case_ev_exclusion_note_states_what_each_household_did():
    """notes.ev_exclusion is a claim about this run. On a no-EV household it
    must not claim an EV exclusion, and on an EV household it must keep the
    string the committed artifact has always carried."""
    was = br.EV_ANALYSIS
    try:
        br.EV_ANALYSIS = True
        with_ev = B.ev_exclusion_note()
        br.EV_ANALYSIS = False
        without = B.ev_exclusion_note()
    finally:
        br.EV_ANALYSIS = was
    assert with_ev == ">=2.5 kW outside on-peak = EV spillover, never battery-served", \
        f"the EV household's note changed: {with_ev!r}"
    assert without != with_ev, (
        "a no-EV household publishes the EV household's exclusion note, which "
        f"describes a filter that did not run: {without!r}")
    low = without.lower()
    assert "no ev-spillover exclusion applied" in low, (
        f"the no-EV note does not say the exclusion was not applied: {without!r}")
    assert "has_ev is false" in low, (
        f"the no-EV note does not name the intake flag it keyed off: {without!r}")
    return ("the note is the EV string with an EV and states that no exclusion "
            "was applied without one")


@case
def case_committed_artifact_note_agrees_with_its_own_free_fix_scenario():
    """The committed artifact is checked against ITSELF, not against this
    household: post_behavior.free_fix_scenario already records which branch of
    household.has_ev the run took, so the note has to be that branch's note."""
    if not ARTIFACT.is_file():
        raise SkipCase(f"{ARTIFACT} is not present in this checkout")
    art = json.loads(ARTIFACT.read_text())
    scen = art["post_behavior"]["free_fix_scenario"]
    note = art["notes"]["ev_exclusion"]
    expect = (B.EV_EXCLUSION_NOTE_EV if scen == B.FREE_FIX_SCENARIO_EV
              else B.EV_EXCLUSION_NOTE_NO_EV)
    assert note == expect, (
        f"the committed artifact ran free fix scenario {scen!r} but publishes "
        f"ev_exclusion {note!r}; that scenario's note is {expect!r}")
    return (f"the committed artifact's scenario {scen!r} and its ev_exclusion "
            "note describe the same rule")


@case
def case_the_gate_reads_the_intake_flag_not_the_detector():
    """A detector that found no sessions is not the same fact as a household
    with no EV -- an EV that charged away from home for the whole window
    detects as nothing. The frames these cases build contain no EV session at
    all, and with the flag set the exclusion must still fire, so the gate
    cannot be reading the detector."""
    import inspect
    src = inspect.getsource(B.run_batt)
    # CODE only. run_batt's own comment explains why the detector is the wrong
    # predicate, and naming it there must not read as calling it.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "br.EV_ANALYSIS" in code, (
        "run_batt no longer gates the exclusion on br.EV_ANALYSIS")
    assert "detect_sessions" not in code, (
        "run_batt reads the EV DETECTOR; the gate must be the intake flag")
    served, _ = _served("greedy", 12.0, has_ev=True)
    assert abs(served) < EPS, (
        "with the intake flag set and no detectable session anywhere in the "
        f"frame, the exclusion did not fire: {served} kWh served")
    return ("the gate is br.EV_ANALYSIS: the exclusion fires on a frame with no "
            "detectable EV session at all")


# ---------------------------------------------------------------------------
def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
