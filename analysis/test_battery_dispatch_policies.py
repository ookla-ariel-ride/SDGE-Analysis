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
import glob
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
import rates as R                              # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "data" / "battery_dispatch_policies.json"
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"

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
    # The "value" policy (issue #240) prices each interval's bucket, so the
    # frame also names its season and month; the original policies read
    # neither. Winter, like the date.
    d["seas"] = "W"
    d["ym"] = "2026-01"
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
# (c) issue #240: the "value" policy prices both sides of the charge decision
# ---------------------------------------------------------------------------
RTE = float(B.ETA * B.ETA)


def _summer_frame():
    """A summer weekday (2026-07-08) with the same stated TOU layout as
    _frame(), plus a midday super-off-peak window 10:00-14:00, so a day can
    carry both a morning shoulder surplus and a cheaper midday one.

    The 96 intervals run from 06:00 to 06:00, not midnight to midnight: both
    policies top the pack up toward full in every super-off-peak gap, and a
    day that opened with six such hours would hand every case a full pack
    before the first decision it wants to watch."""
    dtr = pd.date_range("2026-07-08 06:00", periods=96, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = ["sop" if (h < 6 or 10 <= h < 14) else ("on" if 16 <= h < 21 else "off")
              for h in d.hour]
    d["seas"] = "S"
    d["ym"] = "2026-07"
    return d


def _fill(arr, h0, h1, kwh_per_interval):
    """Set the intervals [h0, h1) of a 06:00-based day."""
    arr[int((h0 - 6) * 4):int((h1 - 6) * 4)] = kwh_per_interval


# The shoulder day. 8 kWh of off-peak surplus at 08:00-10:00, then 12 kWh of
# super-off-peak surplus at 10:00-14:00, a 1 kWh off-peak import at 15:00, a
# 12 kWh on-peak import at 17:00-20:00 and a 10 kWh off-peak import at
# 06:00-08:00 (so the day's off-peak bucket is net-import by 3 kWh, the sign
# every off-peak bucket of the measured year but one has). The pack starts
# empty and nothing can serve the 06:00 import, so what the pack holds at
# 14:00 is what the two policies chose.
SHOULDER_KWH = 8.0
MIDDAY_KWH = 12.0
OFFPEAK_IMPORT_KWH = 1.0
MORNING_IMPORT_KWH = 10.0
# What a midday kWh costs delivered on this day: its bucket is net-export, so
# the surplus end of the bracket applies (see the case that stores it).
MIDDAY_COST = R.credit("S", "sop") / RTE


def _shoulder_day(morning_import_kwh=MORNING_IMPORT_KWH):
    d = _summer_frame()
    imp0 = np.zeros(96); gen0 = np.zeros(96)
    _fill(gen0, 8, 10, SHOULDER_KWH / 8)
    _fill(gen0, 10, 14, MIDDAY_KWH / 16)
    _fill(imp0, 6, 8, morning_import_kwh / 8)
    _fill(imp0, 15, 16, OFFPEAK_IMPORT_KWH / 4)
    _fill(imp0, 17, 20, 12.0 / 12)
    return d, imp0, gen0


def _run(d, imp0, gen0, policy, **kw):
    was = br.EV_ANALYSIS
    br.EV_ANALYSIS = False
    try:
        return B.run_batt(d, imp0, gen0, 13.5, policy, charge_kw=B.CHARGE_KW,
                          soc0=0.0, **kw)
    finally:
        br.EV_ANALYSIS = was


def _stored_by_period(d, gen0, exp):
    p = d.p.values
    return {q: float((gen0 - exp)[p == q].sum()) for q in ("off", "sop", "on")}


@case
def case_greedy_stores_shoulder_surplus_that_crowds_out_the_cheaper_midday_surplus():
    """The defect issue #240 names, on one day. Greedy stores every kWh it has
    room for, in time order, so the 8 kWh morning shoulder surplus (a forgone
    off-peak export worth energy(S, off), 54.5c per kWh delivered) goes in
    first and the 12 kWh of midday surplus that follows (8.4c delivered here,
    its bucket being net-export on this one day) finds only the room that is
    left. The pack holds 13.5 / ETA = 14.23 kWh of
    AC input, so 8 kWh of shoulder and 6.23 kWh of midday are stored and 5.77
    kWh of midday surplus is exported at 10.4c while 8 kWh of 49c exports were
    given up in its place."""
    d, imp0, gen0 = _shoulder_day()
    imp, exp, served, _ = _run(d, imp0, gen0, "greedy")
    got = _stored_by_period(d, gen0, exp)
    room = 13.5 / B.ETA
    assert abs(got["off"] - SHOULDER_KWH) < 1e-6, (
        f"greedy stored {got['off']:.3f} kWh of shoulder surplus, expected all "
        f"{SHOULDER_KWH} kWh; if it now declines shoulder surplus this case is "
        "no longer the positive control for the value policy")
    assert abs(got["sop"] - (room - SHOULDER_KWH)) < 1e-6, (
        f"greedy stored {got['sop']:.3f} kWh of midday surplus, expected the "
        f"{room - SHOULDER_KWH:.3f} kWh of room the shoulder left")
    shoulder_cost = R.energy("S", "off") / RTE
    offpeak_value = R.allin("S", "off")
    assert shoulder_cost > offpeak_value, (shoulder_cost, offpeak_value)
    p = d.p.values
    off_served = float((imp0 - imp)[p == "off"].sum())
    assert abs(off_served - OFFPEAK_IMPORT_KWH) < 1e-6, off_served
    return (f"greedy stores all {SHOULDER_KWH:.0f} kWh of shoulder surplus "
            f"({shoulder_cost*100:.1f}c/kWh delivered) and only "
            f"{got['sop']:.2f} of the {MIDDAY_KWH:.0f} kWh of midday surplus; "
            f"it then serves the {OFFPEAK_IMPORT_KWH:.0f} kWh off-peak import "
            f"({offpeak_value*100:.1f}c) from that one untagged pool")


@case
def case_value_policy_stores_no_shoulder_surplus_and_all_the_midday_surplus():
    """Same day, the "value" policy. Its charge test prices the shoulder kWh at
    energy(S, off) / RTE = 54.5c delivered against the cheapest import the
    policy serves this season, allin(S, off) = 51.1c, and declines it; the
    whole 12 kWh of midday surplus then fits. The trace names the kWh it
    declined and the two figures it compared, so the comparison is checked
    off the run rather than off this docstring."""
    d, imp0, gen0 = _shoulder_day()
    tr = []
    imp, exp, served, _ = _run(d, imp0, gen0, "value", trace=tr)
    got = _stored_by_period(d, gen0, exp)
    assert abs(got["off"]) < 1e-9, (
        f"the value policy stored {got['off']:.3f} kWh of shoulder surplus that "
        "costs more delivered than the off-peak import it would serve")
    assert abs(got["sop"] - MIDDAY_KWH) < 1e-6, (
        f"the value policy stored {got['sop']:.3f} kWh of midday surplus, "
        f"expected all {MIDDAY_KWH} kWh once the shoulder no longer takes the room")
    skips = [r for r in tr if r["kind"] == "skip_surplus"]
    skipped = sum(r["kwh"] for r in skips)
    assert abs(skipped - SHOULDER_KWH) < 1e-6, skipped
    for r in skips:
        assert abs(r["cost"] - R.energy("S", "off") / RTE) < 1e-9, r
        assert abs(r["value"] - R.allin("S", "off")) < 1e-9, r
        assert r["cost"] >= r["value"], r
    # This day's super-off-peak bucket is net-export (12 kWh out, nothing in),
    # so its stored kWh is priced at the surplus end, credit(S, sop) / RTE =
    # 8.4c; the measured year's super-off-peak buckets are all net-import and
    # price at energy(), 11.7c. Same test, other side of zero.
    stores = [r for r in tr if r["kind"] == "charge_surplus"]
    for r in stores:
        assert abs(r["cost"] - MIDDAY_COST) < 1e-9, r
        assert r["cost"] < r["value"], r
    # and the day bills better for it, through the same engine that publishes
    # every battery figure
    d2 = d.copy()
    g_imp, g_exp, _, _ = _run(d, imp0, gen0, "greedy")
    greedy_bill = B.billed(d2, g_imp, g_exp)
    value_bill = B.billed(d2, imp, exp)
    assert value_bill < greedy_bill, (value_bill, greedy_bill)
    return (f"value: 0 of {SHOULDER_KWH:.0f} kWh shoulder surplus stored (each "
            f"kWh priced {skips[0]['cost']*100:.1f}c delivered against a "
            f"{skips[0]['value']*100:.1f}c import), all {MIDDAY_KWH:.0f} kWh "
            f"midday surplus stored; the day bills ${greedy_bill - value_bill:.2f} "
            "less than greedy")


@case
def case_value_policy_prices_a_bucket_on_the_side_of_zero_its_net_lands_on():
    """Acceptance criterion: the comparison uses bill_nem_monthly()'s own
    `net >= 0` bucket test, not a rate card. With the 10 kWh morning import
    the day's off-peak bucket is net-import (10 + 1 in, 8 out; the 12 kWh
    on-peak import is another bucket), so the shoulder kWh is priced at
    energy(); with no morning import the bucket is net-export and the same
    kWh is priced at credit(). Both figures come off the trace."""
    costs = {}
    for morning in (MORNING_IMPORT_KWH, 0.0):
        d, imp0, gen0 = _shoulder_day(morning_import_kwh=morning)
        net = float(imp0[d.p.values == "off"].sum() - gen0[d.p.values == "off"].sum())
        tr = []
        _run(d, imp0, gen0, "value", trace=tr)
        skips = {round(r["cost"], 9) for r in tr if r["kind"] == "skip_surplus"}
        assert len(skips) == 1, skips
        costs[net >= 0] = skips.pop()
    assert abs(costs[True] - R.energy("S", "off") / RTE) < 1e-9, costs
    assert abs(costs[False] - R.credit("S", "off") / RTE) < 1e-9, costs
    assert costs[True] > costs[False], costs
    return (f"shoulder kWh priced {costs[True]*100:.2f}c delivered in a net-import "
            f"off-peak bucket (energy) and {costs[False]*100:.2f}c in a net-export "
            "one (credit); the PCIA gap between them is the bracket, not a rate card")


def _assert_no_import_served_below_its_energy_cost(trace):
    dis = [r for r in trace if r["kind"] == "discharge"]
    assert dis, "the run discharged nothing, so the invariant was never exercised"
    bad = [r for r in dis if r["cost"] >= r["value"]]
    assert not bad, (
        f"{len(bad)} of {len(dis)} discharge decisions served an import worth "
        f"less than the delivered cost of the energy serving it; first: {bad[0]}")
    return dis


@case
def case_value_policy_never_serves_an_import_below_the_cost_of_the_energy_serving_it():
    """Acceptance criterion, on the synthetic day, plus its positive control:
    the same day under greedy DOES serve the 51.1c off-peak import from a
    pool that holds 54.5c shoulder energy. Greedy carries no lot ledger, so
    the exception is documented here rather than traced: its pool is untagged,
    which is the reason the published section 6 prose says which import a
    shoulder kWh serves is not determined."""
    d, imp0, gen0 = _shoulder_day()
    tr = []
    imp, exp, served, _ = _run(d, imp0, gen0, "value", trace=tr)
    dis = _assert_no_import_served_below_its_energy_cost(tr)
    p = d.p.values
    off_served = float((imp0 - imp)[p == "off"].sum())
    assert abs(off_served - OFFPEAK_IMPORT_KWH) < 1e-6, off_served
    # every kWh that served the off-peak import came from a midday lot
    off_rows = [r for r in dis if p[r["i"]] == "off"]
    assert off_rows and all(abs(r["cost"] - MIDDAY_COST) < 1e-9
                            for r in off_rows), off_rows
    g_imp, g_exp, _, _ = _run(d, imp0, gen0, "greedy")
    g_stored_off = _stored_by_period(d, gen0, g_exp)["off"]
    g_off_served = float((imp0 - g_imp)[p == "off"].sum())
    assert g_stored_off > 0 and g_off_served > 0, (g_stored_off, g_off_served)
    return (f"value: {len(dis)} discharge decisions, none below cost; the off-peak "
            f"import is served from {MIDDAY_COST*100:.1f}c midday lots. Greedy on "
            f"the same day serves it from a pool holding {g_stored_off:.0f} kWh of "
            f"{R.energy('S', 'off') / RTE * 100:.1f}c energy")


@case
def case_the_three_original_policies_ignore_the_value_arguments():
    """charge_ref and trace belong to the value policy alone. A trace handed to
    greedy must come back empty and the result must not depend on charge_ref,
    or the committed artifact (which cmp proves byte-identical) could move on
    a caller that passes them."""
    d, imp0, gen0 = _shoulder_day()
    for pol in ("evening", "twowin", "greedy"):
        outs = []
        for ref in ("floor", "best"):
            tr = []
            imp, exp, served, thru = _run(d, imp0, gen0, pol, charge_ref=ref, trace=tr)
            assert tr == [], (pol, ref, tr[:2])
            outs.append((imp.tobytes(), exp.tobytes(), served, thru))
        assert outs[0] == outs[1], pol
    return "evening, twowin and greedy write no trace and do not read charge_ref"


@case
def case_value_policy_on_the_measured_year():
    """Acceptance criterion: the saving re-billed end to end through
    rates.bill_nem() against the published greedy figure, and the no-import-
    below-cost invariant on every discharge of the real year. Reports both
    charge references (VALUE_CHARGE_REF's comment says why "floor" is the
    default), both configurations, and the post-free-fix marginal the report's
    package figures are built on. These are the figures TECHNICAL.md section
    3.13 quotes for the value policy. SKIPS without the private archive."""
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and {HOUSEHOLD_YAML}")
    br.CSV = files[0]
    d = br.load()
    imp0 = d.Consumption.values.astype(float); gen0 = d.Generation.values.astype(float)
    base = B.billed(d, imp0, gen0)
    imp_sh, _, _ = B.free_fix_shift(d, imp0)
    b_sh = B.billed(d, imp_sh, gen0)
    out = {}
    for name, cap, chg in (("pw3", 13.5, B.CHARGE_KW),
                           ("pw3x", 27.0, B.CHARGE_KW_WITH_EXPANSION)):
        i2, e2, _, _ = B.run_batt(d, imp0, gen0, cap, "greedy", charge_kw=chg)
        out[name, "greedy"] = base - B.billed(d, i2, e2)
        i2, e2, _, _ = B.run_batt(d, imp_sh, gen0, cap, "greedy", charge_kw=chg)
        out[name, "greedy", "post"] = b_sh - B.billed(d, i2, e2)
        for ref in ("floor", "best"):
            tr = []
            i3, e3, _, _ = B.run_batt(d, imp0, gen0, cap, "value", charge_kw=chg,
                                      charge_ref=ref, trace=tr)
            _assert_no_import_served_below_its_energy_cost(tr)
            out[name, ref] = base - B.billed(d, i3, e3)
            i3, e3, _, _ = B.run_batt(d, imp_sh, gen0, cap, "value", charge_kw=chg,
                                      charge_ref=ref)
            out[name, ref, "post"] = b_sh - B.billed(d, i3, e3)
    if ARTIFACT.is_file():
        art = json.loads(ARTIFACT.read_text())
        for name, key in (("pw3", "mid"), ("pw3x", "high")):
            assert abs(out[name, "greedy"] - art[name]["greedy"]["save"]) < 1.0, name
            assert abs(out[name, "greedy", "post"]
                       - art["post_behavior"][key]["battery_marginal"]) < 1.0, name
    for name in ("pw3", "pw3x"):
        assert out[name, "floor"] >= out[name, "greedy"], (name, out)
        assert out[name, "floor"] >= out[name, "best"], (name, out)
    lines = []
    for name in ("pw3", "pw3x"):
        lines.append("{}: greedy ${:,.2f} / value-floor ${:,.2f} ({:+.2f}) / value-best "
                     "${:,.2f} ({:+.2f}); after the free fix, greedy ${:,.2f} / "
                     "value-floor ${:,.2f} ({:+.2f})".format(
                         name, out[name, "greedy"], out[name, "floor"],
                         out[name, "floor"] - out[name, "greedy"], out[name, "best"],
                         out[name, "best"] - out[name, "greedy"],
                         out[name, "greedy", "post"], out[name, "floor", "post"],
                         out[name, "floor", "post"] - out[name, "greedy", "post"]))
    return ("measured year, rates.bill_nem(), $/yr; no discharge below cost in any "
            "value run. " + "; ".join(lines))


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
