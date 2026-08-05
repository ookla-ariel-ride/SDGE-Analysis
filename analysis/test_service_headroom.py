#!/usr/bin/env python3
"""Unit guards for service_headroom.py.

What runs where, stated exactly, because the previous version of this paragraph
claimed more than it delivered. It said "everything that could be wrong in a way
that matters runs in CI"; a reviewer injected a 10% error into build() and CI
still reported 78 of 81 passed, because the only cases that ran the generator
skipped without the private archive and the round-trip case re-serializes the
committed bytes rather than regenerating them.

  * WITHOUT the private archive (CI): every unit case -- the NEC arithmetic and
    its citations, the DST day-length handling, the zero-padding truncation, the
    coverage-lag gate, the fail-closed intake reads, the panel-schedule
    geometry, the three-valued verdicts -- plus
    case_build_runs_end_to_end_on_a_synthetic_house, which writes a complete
    synthetic year to a temporary directory and runs the whole of build()
    against it. That case is what closes the gap above: an arithmetic error in
    the assembly moves a figure the fixture determines by hand, and it fails.
    The artifact-reading cases also run, against the COMMITTED artifact, which
    checks its internal consistency but cannot check that it regenerates.
  * ONLY with the private archive: the two cases that run build() on the real
    inputs (the no-EV variant, and the byte-identical reproduction with the EV
    flag deleted) and the private-only leak scan. They raise SkipCase, so a case
    that cannot run says so instead of reading as green. Regeneration itself is
    a repository gate (CLAUDE.md section 9), not something this file can assert
    in CI.

The two failure modes worth naming: a naive hourly aggregation of this dataset
reports a 21.4 kW peak that never happened, because the fall-back Sunday's
repeated hour carries eight 15-minute intervals covering two real hours; and the
current-year Enphase export is zero-padded into the future, so its tail is not
measurement. Both are tested directly rather than trusted.

Run from the repo root:  ./.venv/bin/python analysis/test_service_headroom.py
"""
import ast
import collections
import datetime as dt
import json
import pathlib
import random
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import household as HH
import privacy_tiers as PT
import rates as R
import service_headroom as S

EPS = 1e-9


class SkipCase(Exception):
    """A case that cannot run here, raised rather than returned.

    The runner used to sniff a returned string for a "SKIP" prefix, so a case
    that happened to open its sentence that way would have been counted as
    skipped, and a case that meant to skip and worded it differently would have
    been counted as passed. test_parse_bills.py already carries the typed
    version; this is the same exception, for the same reason.
    """


def _close(a, b, eps=EPS):
    return abs(float(a) - float(b)) <= eps


def _rq(x, n):
    """The artifact's own rounding at a stated number of places, so a published
    figure is compared exactly instead of through a loose epsilon that would
    hide real drift."""
    return round(float(x), n)


def _with_household(text):
    """Point the intake loader at a synthetic household.yaml (or its absence).

    Returns the TemporaryDirectory so the caller keeps it alive; main() restores
    the module's real path and cache after every case.
    """
    td = tempfile.TemporaryDirectory()
    p = pathlib.Path(td.name) / "household.yaml"
    if text is not None:
        p.write_text(text)
    HH.PATH = p
    HH._cache = None
    return td


PANEL_YAML = """
charger:
  kw: 11.5
solar:
  kw_ac: 9.45
  inverter_model: Example IQ Micro
  inverter_count: 30
panel:
  service_rating_a: 175
  busbar_rating_a: 200
  main_breaker_catalog: TESTCO-MAIN01
  enclosure_catalog: TESTCO-ENC02
  enclosure_type: NEMA 1 indoor, meter-main combination
  meter_class: CL100
  assembly_sccr_ka: 22
  breaker_family: Test-Brand BR
  pv_backfeed_a: 50
  meter_socket_continuous_a: 170
  pv_breaker_position: bottom
  spaces: 20
  max_circuits: 40
  schedule:
    - {device: full-size 2-pole, poles: 2, amps: 60, label: EV charger}
    - {device: tandem, poles: 2, amps: [20, 20], label: Kitchen}
    - {device: quad, poles: 4, amps: [15, 20, 20, 15], label: Test kitchen circuit}
    - {device: full-size 2-pole, poles: 2, amps: 50, label: PV backfeed}
"""


def _day(date, import_kwh=1.0, export_kwh=0.0):
    """One synthetic calendar day at the slot multiset the tariff clock says it
    has -- 96 slots ordinarily, 92 on spring-forward, 100 on fall-back. The
    day-length rule is imported from rates.expected_day_hours, the same single
    source tou_audit.py uses, so this fixture cannot drift from the production
    definition."""
    return [(date, h, import_kwh, export_kwh) for h in R.expected_day_hours(date)]


# ---------------------------------------------------------------------------
# NEC arithmetic
# ---------------------------------------------------------------------------

PANEL_YAML_NO_SOCKET = PANEL_YAML.replace(
    "meter_socket_continuous_a: 170", "meter_socket_continuous_a: null")
PANEL_YAML_NO_BACKFEED = PANEL_YAML.replace(
    "pv_backfeed_a: 50", "pv_backfeed_a: null")
# The keys gone entirely -- an unanswered question, not a surveyed absence.
PANEL_YAML_BACKFEED_UNASKED = PANEL_YAML.replace("  pv_backfeed_a: 50\n", "")
PANEL_YAML_SOCKET_UNASKED = PANEL_YAML.replace(
    "  meter_socket_continuous_a: 170\n", "")


def case_220_87_chain_on_hand_computed_inputs():
    # 12.000 kW at 240 V is 50.000 A; x1.25 is 62.500 A; against a 200 A
    # service that leaves 137.500 A, and against a 150 A socket 87.500 A.
    steps = S.nec_220_87_steps(12.0, 200.0, 150.0, 400, S.SOCKET_READ)
    assert [s["step"] for s in steps] == [1, 2, 3, 4], steps
    assert _close(steps[0]["result_a"], 50.0), steps[0]
    assert _close(steps[1]["result_a"], 62.5), steps[1]
    assert _close(steps[2]["result_a"], 137.5), steps[2]
    assert _close(steps[3]["result_a"], 87.5), steps[3]
    # every step names its inputs, so the artifact shows the arithmetic
    for s in steps:
        assert s["formula"] and s["inputs"] and "label" in s, s
    assert steps[1]["inputs"]["factor"] == S.NEC_220_87_FACTOR
    # condition (1) is a 1-YEAR period. The 30-day figure that used to sit here
    # is the Exception's, and the Exception is closed to a PV service.
    assert steps[1]["inputs"]["condition_1_days_required"] == 365
    assert steps[1]["inputs"]["condition_1_days_required"] == \
        S.NEC_220_87_CONDITION_1_DAYS
    assert not hasattr(S, "NEC_220_87_MIN_DAYS"), \
        "the 30-day 'code minimum' constant is back"
    return "the 220.87 chain reproduces a hand calculation at every step"


def case_amps_uses_the_240_v_service_basis():
    assert _close(S.amps(24.0), 100.0)
    assert _close(S.SERVICE_VOLTAGE_V, 240.0)
    return "kW to amps uses the 240 V single-phase service basis"


def case_evse_is_a_continuous_load():
    # NEC 625.42: 48 A output x 1.25 = 60 A; the circuit is the smallest
    # standard OCPD carrying 48 A at 80%, which is 60 A.
    assert _close(S.evse_code_load_a(48.0), 60.0)
    assert _close(S.standard_circuit_for(48.0), 60.0)
    assert _close(S.evse_code_load_a(24.0), 30.0)
    assert _close(S.standard_circuit_for(24.0), 30.0)
    assert _close(S.standard_circuit_for(32.0), 40.0)
    try:
        S.standard_circuit_for(500.0)
        raise AssertionError("an impossible output found a standard circuit")
    except SystemExit as e:
        assert "no standard OCPD" in str(e), e
    return "EVSE code load is 125% of output and sizes to a standard circuit"


def case_220_87_socket_step_follows_the_three_socket_states():
    """A surveyed socket with no printed rating drops step 4: the constraint
    genuinely does not apply. A socket nobody read must NOT drop it -- the
    chain would then end at step 3 and read exactly like the surveyed case,
    with the tighter of the two constraints silently gone."""
    none_printed = S.nec_220_87_steps(12.0, 200.0, None, 400,
                                      S.SOCKET_SURVEYED_NONE)
    assert [s["step"] for s in none_printed] == [1, 2, 3], none_printed
    for s in none_printed:
        assert s["result_a"] is not None, s
        assert all(v is not None for v in s["inputs"].values()), s
    assert _close(none_printed[2]["result_a"], 137.5), none_printed[2]

    unasked = S.nec_220_87_steps(12.0, 200.0, None, 400, S.SOCKET_NOT_RECORDED)
    assert [s["step"] for s in unasked] == [1, 2, 3, 4], unasked
    four = unasked[3]
    assert four["result_a"] is None, four
    assert four["verdict"] == "not_determined", four
    assert "ABSENT" in four["reading"], four
    assert "TIGHTER" in four["reading"], four
    assert "meter_socket_continuous_a" in four["what_would_settle_it"], four
    # the first three steps are identical either way: only step 4 differs
    assert unasked[:3] == none_printed[:3], (unasked, none_printed)
    # and a token this function cannot describe stops the run
    for stale in (True, None, "maybe"):
        try:
            S.nec_220_87_steps(12.0, 200.0, None, 400, stale)
            raise AssertionError(f"accepted {stale!r} as a socket basis")
        except SystemExit as e:
            assert "meter-socket basis" in str(e), e
    return "step 4 is dropped only for a surveyed socket, never for an unasked one"


def case_three_valued_verdict_needs_both_bases():
    """The flip, on the household's own headroom figures.

    The figures are READ from the committed artifact rather than typed here:
    typed copies went stale twice while still passing, and a case labelled
    "the household's own" has to be the household's own. The shape the case
    depends on is asserted first, so an artifact that stops exhibiting the flip
    fails this case instead of quietly testing something else.
    """
    d = json.loads(S.OUT.read_text())
    nec, sens = d["nec_220_87"], d["nec_220_87"]["sensitivity_on_the_upper_bound"]
    measured = {"service": nec["headroom_a"]["vs_service_rating"],
                "meter_socket": nec["headroom_a"]["vs_meter_socket"]}
    conservative = {"service": sens["headroom_vs_service_a"],
                    "meter_socket": sens["headroom_vs_meter_socket_a"]}
    # the case below is built on a second 48 A EVSE (60 A code load) fitting on
    # one basis and not the other; if that stops being true, say so
    assert measured["meter_socket"] > 60.0 > conservative["meter_socket"], \
        (measured, conservative)

    def verdict(fixed_a):
        m = S.remaining_headroom(measured, fixed_a, S.SOCKET_READ)
        c = S.remaining_headroom(conservative, fixed_a, S.SOCKET_READ)
        return S.ampacity_verdict(m["binding"], c["binding"]), m, c

    # nothing added: fits on both bases
    v, m, c = verdict(0.0)
    assert v == "pass", (v, m, c)
    # a second 48 A EVSE needs 60 A: it fits against 76.46 and does NOT fit
    # against 43.97, which is the flip a bare boolean used to publish as true
    v, m, c = verdict(60.0)
    assert v == "not_determined", (v, m, c)
    assert m["binding"] > 0 > c["binding"], (m, c)
    # the battery case does not fit even on the measured maximum
    v, m, c = verdict(measured["meter_socket"] + 1.0)
    assert v == "fail", (v, m, c)
    assert m["binding"] < 0, m
    # exactly zero headroom is not a pass
    assert S.ampacity_verdict(0.0, 0.0) == "fail"
    assert S.ampacity_verdict(1.0, 0.0) == "not_determined"
    return "the ampacity verdict is three-valued and computed on both bases"


def case_remaining_headroom_omits_an_absent_socket():
    """With no socket rating the binding constraint is the service rating
    alone -- never a None dragged into min(). But the number alone cannot say
    whether it IS the binding constraint or just the tightest one anybody asked
    about, so `binding_is` travels with it at every exit."""
    r = S.remaining_headroom({"service": 100.0}, 60.0, S.SOCKET_SURVEYED_NONE)
    assert r["vs_meter_socket"] is None, r
    assert _close(r["vs_service_rating"], 40.0) and _close(r["binding"], 40.0), r
    assert "only ampacity constraint" in r["binding_is"], r
    r2 = S.remaining_headroom({"service": 100.0, "meter_socket": 95.0}, 60.0,
                              S.SOCKET_READ)
    assert _close(r2["vs_meter_socket"], 35.0) and _close(r2["binding"], 35.0), r2
    assert "both evaluated" in r2["binding_is"], r2
    # the case that used to be indistinguishable: same map, same number, and a
    # label that refuses to call it binding
    r3 = S.remaining_headroom({"service": 100.0}, 60.0, S.SOCKET_NOT_RECORDED)
    assert _close(r3["binding"], r["binding"]), (r3, r)
    assert "UPPER LIMIT" in r3["binding_is"], r3
    assert r3["binding_is"] != r["binding_is"], r3
    for stale in (True, None, "socket"):
        try:
            S.remaining_headroom({"service": 100.0}, 60.0, stale)
            raise AssertionError(f"accepted {stale!r} as a socket basis")
        except SystemExit as e:
            assert "meter-socket basis" in str(e), e
    return "an absent meter socket drops out of the arithmetic but never silently"


def case_busbar_120_percent_rule_fails_the_battery():
    b = S.busbar_120_percent(200.0, 175.0, 50.0)
    assert _close(b["busbar_x_120pct_a"], 240.0), b
    assert _close(b["total_backfeed_allowed_a"], 65.0), b
    assert _close(b["remaining_backfeed_a"], 15.0), b
    assert _close(b["remaining_backfeed_kva"], 3.6), b
    # a Powerwall 3 at 11.5 kW discharge needs a 60 A backfeed breaker, so it
    # does not fit
    breaker = S.standard_circuit_for(S.amps(S.BATTERY_DISCHARGE_KW))
    assert _close(breaker, 60.0), breaker
    assert breaker > b["remaining_backfeed_a"], (breaker, b)
    assert _close(breaker - b["remaining_backfeed_a"], 45.0)
    return "the 120% busbar rule leaves 15 A and the 60 A battery breaker fails"


def case_busbar_120_percent_rule_passes_a_smaller_main():
    # Same 200 A bus with a 100 A main: 240 - 100 = 140 A of backfeed allowed,
    # 90 A left after the existing PV, which does accept a 60 A breaker.
    b = S.busbar_120_percent(200.0, 100.0, 50.0)
    assert _close(b["total_backfeed_allowed_a"], 140.0), b
    assert _close(b["remaining_backfeed_a"], 90.0), b
    assert 60.0 <= b["remaining_backfeed_a"]
    # ... and even then the arithmetic alone is not a compliant verdict: with
    # no recorded breaker positions the second condition is undecided.
    assert b["position_condition"]["verdict"] == "not_determined", b
    return "the same rule passes the arithmetic when the main breaker is smaller"


def case_the_busbar_position_condition_is_evaluated_not_ignored():
    """NEC 705.12(B)(3)(2) is conjunctive. The 120% arithmetic is half of it;
    the backfeed breaker at the opposite end of the bus from the main is the
    other half, and it fails closed on absent evidence."""
    opposite = S.position_condition("bottom", "top")
    assert opposite["verdict"] == "pass", opposite
    assert "opposite" in opposite["requirement"]
    same = S.position_condition("bottom", "bottom")
    assert same["verdict"] == "fail", same
    assert "same end" in same["reading"] or "bottom of the busbar" in same["reading"]
    # the main's end is what this repo's own intake does not carry
    assert S.position_condition("bottom", None)["verdict"] == "not_determined"
    assert S.position_condition(None, "top")["verdict"] == "not_determined"
    assert S.position_condition(None, None)["verdict"] == "not_determined"
    # a value that is neither end is not evidence about the condition
    unreadable = S.position_condition("middle", "top")
    assert unreadable["verdict"] == "not_determined", unreadable
    assert unreadable["source_breaker_position"] is None, unreadable
    # case and whitespace in the intake string do not decide a code question
    assert S.position_condition(" Bottom ", "TOP")["verdict"] == "pass"
    return "the breaker-position condition is three-valued and fails closed"


def case_busbar_carries_both_legs_of_the_rule():
    ok = S.busbar_120_percent(200.0, 100.0, 50.0, S.BACKFEED_READ,
                              "bottom", "top")
    assert ok["position_condition"]["verdict"] == "pass", ok
    assert "conjunctive" in ok["position_condition"]["requirement"], ok
    assert "both must hold" in ok["remaining_backfeed_is_the_ampacity_leg_only"]
    bad = S.busbar_120_percent(200.0, 100.0, 50.0, S.BACKFEED_READ,
                               "top", "top")
    assert bad["position_condition"]["verdict"] == "fail", bad
    # the arithmetic is identical in both: the position leg is what differs
    assert _close(ok["remaining_backfeed_a"], bad["remaining_backfeed_a"]), (ok, bad)
    return "busbar_120_percent reports the position condition beside the arithmetic"


def case_the_busbar_ampacity_leg_is_three_valued():
    """Where the intake never answered, the allowance is computed with the
    existing backfeed at 0 A, which makes it the LARGEST it could be. A
    shortfall against the largest possible allowance is real; a fit against it
    is an assumption, and must not read as a pass. A surveyed zero is not that
    case -- it is an answer, and it decides the leg."""
    # a rating read off the breaker: the arithmetic decides it either way
    assert S.busbar_ampacity_leg(60.0, 15.0, S.BACKFEED_READ) == "fail"
    assert S.busbar_ampacity_leg(60.0, 90.0, S.BACKFEED_READ) == "pass"
    # surveyed and nothing backfeeds it: also an answer, also decisive
    assert S.busbar_ampacity_leg(60.0, 15.0, S.BACKFEED_SURVEYED_NONE) == "fail"
    assert S.busbar_ampacity_leg(60.0, 90.0, S.BACKFEED_SURVEYED_NONE) == "pass"
    # never asked: a shortfall survives the assumption, a fit does not
    assert S.busbar_ampacity_leg(60.0, 15.0, S.BACKFEED_NOT_RECORDED) == "fail"
    assert S.busbar_ampacity_leg(60.0, 90.0, S.BACKFEED_NOT_RECORDED) == \
        "not_determined"
    # exactly at the allowance is not a shortfall
    assert S.busbar_ampacity_leg(60.0, 60.0, S.BACKFEED_READ) == "pass"
    assert "0 A" in S.AMPACITY_LEG_BASIS and "not_determined" in S.AMPACITY_LEG_BASIS
    assert "surveyed" in S.AMPACITY_LEG_BASIS, S.AMPACITY_LEG_BASIS
    assert "pv_backfeed_a" in S.AMPACITY_LEG_SETTLE
    # a stale boolean where a basis token belongs must not read as truthy
    for stale in (True, False, None, "yes"):
        try:
            S.busbar_ampacity_leg(60.0, 90.0, stale)
            raise AssertionError(f"accepted {stale!r} as a backfeed basis")
        except SystemExit as e:
            assert "existing-backfeed basis" in str(e), e
    return "the busbar ampacity leg is three-valued only when nobody was asked"


def case_the_sum_of_breakers_rule_is_three_valued():
    """705.12(B)(3)(1) is asked as a way to ADD the battery, so the proposed
    breaker is counted. The recorded schedule can only understate the true sum,
    so a sum over the busbar rating fails and a sum under it is not settled."""
    over = S.sum_of_breakers_rule(460.0, 200.0, 60.0)
    assert _close(over["counted_sum_a"], 520.0), over
    assert over["verdict"] == "fail", over
    assert over["what_would_settle_it"] is None, over
    under = S.sum_of_breakers_rule(100.0, 200.0, 60.0)
    assert _close(under["counted_sum_a"], 160.0), under
    assert under["verdict"] == "not_determined", under
    assert "complete device-by-device" in under["what_would_settle_it"], under
    # the proposed breaker is what turns a passing sum into a failing one
    assert S.sum_of_breakers_rule(160.0, 200.0, 60.0)["verdict"] == "fail"
    assert S.sum_of_breakers_rule(160.0, 200.0, 0.0)["verdict"] == "not_determined"
    return "the sum-of-breakers rule counts the proposed breaker and is three-valued"


def case_the_battery_verdict_needs_both_legs():
    """The arithmetic alone never reads as compliant. A panel with room on the
    120% allowance but no recorded breaker positions is not determined, and
    either leg failing fails the panel."""
    assert S.battery_verdict("pass", "pass") == "fits within the 120% allowance"
    assert S.battery_verdict("pass", "not_determined").startswith("NOT DETERMINED")
    assert "705.12(B)(3)(2)" in S.battery_verdict("pass", "not_determined")
    assert S.battery_verdict("pass", "fail") == "FAILS as the panel stands"
    assert S.battery_verdict("fail", "pass") == "FAILS as the panel stands"
    assert S.battery_verdict("fail", "not_determined") == "FAILS as the panel stands"
    # the ampacity leg is three-valued too, and its undecided case says so
    # instead of borrowing the position leg's reason
    und = S.battery_verdict("not_determined", "pass")
    assert und.startswith("NOT DETERMINED") and "backfeeds the busbar" in und, und
    both = S.battery_verdict("not_determined", "not_determined")
    assert "backfeeds the busbar" in both and "breaker-position" in both, both
    assert S.battery_verdict("not_determined", "fail") == "FAILS as the panel stands"
    return "the battery verdict is conjunctive over the ampacity and position legs"


def case_the_two_zero_backfeeds_are_told_apart():
    """Both zeros spend 0 A of the allowance; only one of them is a finding.
    The note has to tell a reader which, in words that cannot be confused."""
    unasked = S.busbar_120_percent(200.0, 175.0, 0.0, S.BACKFEED_NOT_RECORDED)
    assert _close(unasked["remaining_backfeed_a"], 65.0), unasked
    assert unasked["existing_pv_backfeed_basis"] == S.BACKFEED_NOT_RECORDED
    note = unasked["existing_pv_backfeed_note"]
    assert "ABSENT" in note and "nobody has answered" in note, note
    assert "rather than a measurement" in note, note

    surveyed = S.busbar_120_percent(200.0, 175.0, 0.0, S.BACKFEED_SURVEYED_NONE)
    # identical arithmetic, opposite evidential standing
    assert _close(surveyed["remaining_backfeed_a"],
                  unasked["remaining_backfeed_a"]), (surveyed, unasked)
    assert surveyed["existing_pv_backfeed_basis"] == S.BACKFEED_SURVEYED_NONE
    snote = surveyed["existing_pv_backfeed_note"]
    assert "explicit null" in snote and "NOTHING backfeeds it" in snote, snote
    assert "known, complete answer" in snote, snote
    assert snote != note, "the two zeros publish the same sentence"

    declared = S.busbar_120_percent(200.0, 175.0, 0.0, S.BACKFEED_READ)
    assert declared["existing_pv_backfeed_basis"] == S.BACKFEED_READ
    assert "read off" in declared["existing_pv_backfeed_note"], declared
    # and no bare boolean is left standing in place of the three states
    for b in (unasked, surveyed, declared):
        assert "existing_pv_backfeed_declared" not in b, b
    return "a surveyed zero and an unanswered one spend 0 A and are told apart"


# ---------------------------------------------------------------------------
# DST
# ---------------------------------------------------------------------------

def case_fall_back_day_produces_no_phantom_peak():
    fall = R.dst_transition_sundays(2025)[1]
    rows = _day(fall, import_kwh=1.0)
    assert len(rows) == 100, len(rows)
    hs = S.hourly_sums(rows)
    assert hs[(fall, 1)][2] == 8, hs[(fall, 1)]
    kw = S.hourly_mean_kw(hs)
    # 1.0 kWh per 15-minute slot is 4.0 kW in every hour of the day, including
    # the repeated one: 8.0 kWh over 2.0 elapsed hours.
    assert all(_close(v, 4.0) for v in kw.values()), sorted(set(kw.values()))
    g = S.dst_guard(hs, {fall})
    assert _close(g["naive_max_kw"], 8.0), g
    assert g["naive_max_at"] == f"{fall} 01:00", g
    assert g["naive_max_is_a_dst_artifact"] is True, g
    assert _close(g["corrected_max_kw"], 4.0), g
    assert len(g["irregular_hours"]) == 1, g["irregular_hours"]
    ir = g["irregular_hours"][0]
    assert ir["intervals"] == 8 and _close(ir["elapsed_hours"], 2.0), ir
    assert _close(ir["naive_kw"], 8.0) and _close(ir["corrected_kw"], 4.0), ir
    return "the fall-back Sunday's repeated hour reports 4 kW, not a phantom 8 kW"


def case_spring_forward_day_is_short_and_still_clean():
    spring = R.dst_transition_sundays(2026)[0]
    rows = _day(spring, import_kwh=1.0)
    assert len(rows) == 92, len(rows)
    hs = S.hourly_sums(rows)
    assert (spring, 2) not in hs, "the missing 02:00 hour was invented"
    assert len([k for k in hs if k[0] == spring]) == 23
    kw = S.hourly_mean_kw(hs)
    assert all(_close(v, 4.0) for v in kw.values()), sorted(set(kw.values()))
    g = S.dst_guard(hs, {spring})
    assert g["irregular_hours"] == [], g["irregular_hours"]
    assert _close(g["naive_max_kw"], 4.0) and _close(g["corrected_max_kw"], 4.0), g
    return "the spring-forward Sunday carries 92 slots and no invented hour"


def case_day_lengths_match_the_tariff_clock():
    """The fixture days above are accepted by the same validator tou_audit.py
    uses, so this suite's notion of a DST day cannot fork from production's."""
    for d in (dt.date(2025, 8, 22),
              R.dst_transition_sundays(2025)[1],
              R.dst_transition_sundays(2026)[0]):
        rows = _day(d)
        R.validate_interval_coverage([(x[0], x[1]) for x in rows], d, d)
    return "the synthetic DST days satisfy rates.validate_interval_coverage"


def case_dst_dates_are_derived_not_listed():
    days = {dt.date(2025, 6, 27) + dt.timedelta(days=i) for i in range(395)}
    got = S.dst_dates_in(days)
    assert got == {dt.date(2025, 11, 2), dt.date(2026, 3, 8)}, sorted(got)
    # a window with no transition in it yields nothing rather than a stale pair
    assert S.dst_dates_in({dt.date(2025, 6, 1) + dt.timedelta(days=i)
                           for i in range(30)}) == set()
    return "DST dates come from rates.dst_transition_sundays, per window"


# ---------------------------------------------------------------------------
# Enphase zero padding
# ---------------------------------------------------------------------------

def case_zero_padding_is_truncated_not_treated_as_data():
    real, padded = S.truncate_sam_padding([1.0, 2.0, 3.0] + [0.0] * 97)
    assert real == [1.0, 2.0, 3.0], real
    assert padded == 97, padded
    # an interior zero is not a tail and must not truncate the series
    real, padded = S.truncate_sam_padding([1.0, 0.0, 3.0, 0.0])
    assert real == [1.0, 0.0, 3.0] and padded == 1, (real, padded)
    return "the zero tail is truncated and an interior zero is preserved"


def case_an_all_zero_enphase_export_fails_closed():
    try:
        S.truncate_sam_padding([0.0] * 8760)
        raise AssertionError("an all-zero export was accepted as measurement")
    except SystemExit as e:
        assert "all zeros" in str(e), e
    return "an all-zero Enphase export raises SystemExit instead of measuring nothing"


def case_enphase_loader_checks_shape_and_reads_the_trailing_year():
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        p = d / "enphase_sam8760_2025.csv"
        p.write_text("kWh\n" + "".join(f"{1.0}\n" for _ in range(8760)))
        sam, prov = S.load_sam([p])
        assert prov[0]["year"] == 2025, prov      # not 8760 from "sam8760"
        assert prov[0]["rows_used"] == 8760 and prov[0]["zero_padded_rows"] == 0
        assert sam[(dt.date(2025, 1, 1), 0)] == 1.0
        assert sam[(dt.date(2025, 12, 31), 23)] == 1.0
        short = d / "enphase_sam8760_2024.csv"
        short.write_text("kWh\n" + "1.0\n" * 8760)      # 2024 is a leap year
        try:
            S.load_sam([short])
            raise AssertionError("a short leap-year file was accepted")
        except SystemExit as e:
            assert "8784" in str(e), e
        bad = d / "enphase_sam8760_2023.csv"
        bad.write_text("Wh\n1.0\n")
        try:
            S.load_sam([bad])
            raise AssertionError("wrong columns were accepted")
        except SystemExit as e:
            assert "expected ['kWh']" in str(e), e
    return "the Enphase loader validates row count and columns and reads _YYYY"


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

FLAG_FALSE = "household:\n  has_new_load_interest: false\n"
FLAG_TRUE = "household:\n  has_new_load_interest: true\n"


class _Reached(Exception):
    """Raised by a tripwire standing in for a real input read."""


def case_only_an_explicit_false_new_load_flag_disables_the_analysis():
    """The has_ev contract, applied to this flag. The flag is the authority on
    whether the question is being asked, and its ABSENCE is not an answer --
    the flag postdates some intake files, so a household without it must still
    get the analysis (and still stop on a missing panel)."""
    real = S.load_panel

    def tripwire():
        raise _Reached()

    try:
        S.load_panel = tripwire
        with _with_household(FLAG_FALSE):
            out = S.build()
            assert out["not_applicable"] is True, out
        for text, why in ((FLAG_TRUE, "an explicit true"),
                          (PANEL_YAML, "an absent flag"),
                          ("household:\n  plan: EV-TOU-5\n", "an absent flag")):
            with _with_household(text):
                try:
                    S.build()
                    raise AssertionError(f"{why} did not reach the panel intake")
                except _Reached:
                    pass
    finally:
        S.load_panel = real
    # a flag nobody can read is an intake defect, not a default
    for bad in ("household:\n  has_new_load_interest: 'false'\n",
                "household:\n  has_new_load_interest: 0\n"):
        with _with_household(bad):
            try:
                S.build()
                raise AssertionError(f"accepted {bad!r} as a flag")
            except SystemExit as e:
                assert "must be a YAML boolean" in str(e), e
                assert "has_new_load_interest" in str(e), e
    return "only an explicit false disables the analysis; absence never does"


def case_a_false_new_load_flag_reads_no_input_at_all():
    """The documented promise for a false flag is 'not applicable', and a
    bill-only household has no panel survey, no meter export and no Enphase
    file to fail closed on. Every reader is a tripwire here, and the intake
    accessor records every path asked for."""
    readers = ("load_panel", "only_match", "load_intervals", "load_sam",
               "load_pv_ac_nameplate")
    real = {name: getattr(S, name) for name in readers}
    real_get = HH.get
    reads = []

    def _tripwire(name):
        def boom(*a, **kw):
            raise AssertionError(f"{name}() was called under a false flag")
        return boom

    def recording_get(path, required=True):
        reads.append(path)
        return real_get(path, required=required)

    try:
        for name in readers:
            setattr(S, name, _tripwire(name))
        HH.get = recording_get
        with _with_household(FLAG_FALSE):
            out = S.build()
    finally:
        for name, fn in real.items():
            setattr(S, name, fn)
        HH.get = real_get

    assert reads == [S.NEW_LOAD_FLAG], reads
    assert not [p for p in reads if p.startswith(("panel.", "solar.", "charger."))]
    # the stub names the flag, and what to set to get an answer
    assert out["flag"] == S.NEW_LOAD_FLAG, out
    assert S.NEW_LOAD_FLAG in out["reason"] and "is false" in out["reason"], out
    assert "true" in out["to_enable_it"], out
    assert "panel.service_rating_a" in out["to_enable_it"], out
    assert "not_applicable" in out and "not_determined" not in json.dumps(out), out
    # nothing was computed, so nothing that reads as a computed answer is there
    for key in ("caveat", "maximum_demand", "nec_220_87", "cases",
                "battery_inverter", "panel"):
        assert key not in out, (key, sorted(out))
    return "a false flag reads only the flag and publishes a not_applicable stub"


def case_a_false_new_load_flag_writes_the_stub_artifact():
    """The stub is an artifact, not a print statement: main() writes it through
    the same atomic path the real result takes, and exits 0."""
    real_out, real_root = S.OUT, S.ROOT
    td = tempfile.TemporaryDirectory()
    try:
        S.ROOT = pathlib.Path(td.name)
        S.OUT = S.ROOT / "data" / "service_headroom.json"
        with _with_household(FLAG_FALSE):
            assert S.main() == 0
        d = json.loads(S.OUT.read_text())
    finally:
        S.OUT, S.ROOT = real_out, real_root
        td.cleanup()
    assert d["not_applicable"] is True, d
    assert d["flag"] == S.NEW_LOAD_FLAG, d
    assert "postdates" in d["flag_contract"], d
    assert "None beyond the flag itself" in d["inputs_read"], d
    return "a false flag writes a not_applicable artifact and exits 0"


def case_a_true_new_load_flag_still_fails_closed_on_the_panel():
    """The flag switches the analysis off; it never softens it. A household
    that asks for the answer and has not supplied the service rating still
    stops, which is the whole point of the panel fields having no defaults."""
    for text in (FLAG_TRUE + PANEL_YAML.replace("  service_rating_a: 175\n", ""),
                 PANEL_YAML.replace("  service_rating_a: 175\n", "")):
        with _with_household(text):
            try:
                S.build()
                raise AssertionError("a missing service rating was accepted")
            except SystemExit as e:
                assert "panel.service_rating_a" in str(e), e
    return "a true or absent flag keeps the fail-closed stop on the service rating"


def case_absent_service_rating_fails_closed():
    with _with_household("charger:\n  kw: 11.5\n"):
        try:
            S.load_panel()
            raise AssertionError("a missing service rating did not fail closed")
        except SystemExit as e:
            assert "panel.service_rating_a" in str(e), e
    with _with_household(None):
        try:
            S.load_panel()
            raise AssertionError("a missing household.yaml did not fail closed")
        except SystemExit as e:
            assert "intake interview" in str(e), e
    return "an absent service rating (or intake file) raises SystemExit, no default"


def case_panel_intake_reads_every_required_field():
    with _with_household(PANEL_YAML):
        p = S.load_panel()
    assert _close(p["service_rating_a"], 175.0)
    assert _close(p["busbar_rating_a"], 200.0)
    assert _close(p["pv_backfeed_a"], 50.0)
    assert _close(p["meter_socket_continuous_a"], 170.0)
    assert p["spaces"] == 20 and p["max_circuits"] == 40
    assert len(p["schedule"]) == 4
    # issue #41: the six panel fields that were never read at all before
    assert p["main_breaker_catalog"] == "TESTCO-MAIN01", p
    assert p["enclosure_catalog"] == "TESTCO-ENC02", p
    assert p["enclosure_type"] == \
        "NEMA 1 indoor, meter-main combination", p
    assert p["meter_class"] == "CL100", p
    assert _close(p["assembly_sccr_ka"], 22.0), p
    assert p["breaker_family"] == "Test-Brand BR", p
    return "load_panel pulls every panel field through the fail-closed accessor"


def case_every_required_panel_field_still_fails_closed():
    """Only the four documented-nullable fields (plus the three optional
    breaker positions) are not required. Everything the panel answer actually
    rests on still stops the run when it is absent -- including the six
    fields issue #41 started reading for the first time."""
    for key in ("service_rating_a: 175", "busbar_rating_a: 200", "spaces: 20",
                "max_circuits: 40", "main_breaker_catalog: TESTCO-MAIN01",
                "enclosure_catalog: TESTCO-ENC02",
                "enclosure_type: NEMA 1 indoor, meter-main "
                "combination",
                "meter_class: CL100", "assembly_sccr_ka: 22",
                "breaker_family: Test-Brand BR"):
        with _with_household(PANEL_YAML.replace(key, "")):
            try:
                S.load_panel()
                raise AssertionError(f"a missing {key.split(':')[0]} was accepted")
            except SystemExit as e:
                assert key.split(":")[0] in str(e), (key, str(e))
    with _with_household(PANEL_YAML[:PANEL_YAML.index("  schedule:")]):
        try:
            S.load_panel()
            raise AssertionError("a missing schedule was accepted")
        except SystemExit as e:
            assert "panel.schedule" in str(e), e
    return "service rating, busbar, spaces, max circuits and schedule stay required"


def case_a_null_meter_socket_rating_runs_end_to_end():
    """The committed template ships meter_socket_continuous_a: null. float(None)
    raises, so this path is the difference between a runnable template and one
    that cannot produce an answer at all."""
    with _with_household(PANEL_YAML_NO_SOCKET):
        p = S.load_panel()
    assert p["meter_socket_continuous_a"] is None, p
    assert p["meter_socket_recorded"] is True, p
    basis = S.socket_basis_of(p)
    assert basis == S.SOCKET_SURVEYED_NONE, basis
    # the whole chain build() runs on that field, in order
    steps = S.nec_220_87_steps(12.0, p["service_rating_a"],
                               p["meter_socket_continuous_a"], 400, basis)
    assert [s["step"] for s in steps] == [1, 2, 3], steps
    avail = S.availability(p["service_rating_a"],
                           p["meter_socket_continuous_a"], steps[1]["result_a"])
    assert set(avail) == {"service"}, avail
    rem = S.remaining_headroom(avail, 60.0, basis)
    assert rem["vs_meter_socket"] is None, rem
    assert _close(rem["binding"], 175.0 - 62.5 - 60.0), rem
    assert S.ampacity_verdict(rem["binding"], rem["binding"]) == "pass"
    # the sentence the artifact publishes here is TRUE and stands
    assert "does not apply" in S.SOCKET_CONSTRAINT[basis], basis
    return "a surveyed socket with no printed rating drops out, and says why"


def case_an_absent_meter_socket_key_is_not_a_socket_without_a_rating():
    """The optimistic half of this defect class. The meter socket is the
    TIGHTER of the two ampacity constraints wherever it exists, so an unasked
    question that reads as 'does not apply' deletes the binding constraint and
    inflates every headroom in the artifact."""
    with _with_household(PANEL_YAML_SOCKET_UNASKED):
        p = S.load_panel()
        assert S._key_present("panel.meter_socket_continuous_a") is False
    assert p["meter_socket_continuous_a"] is None, p
    assert p["meter_socket_recorded"] is False, p
    basis = S.socket_basis_of(p)
    assert basis == S.SOCKET_NOT_RECORDED, basis
    # step 4 survives, as an undetermined row naming what would settle it
    steps = S.nec_220_87_steps(12.0, p["service_rating_a"],
                               p["meter_socket_continuous_a"], 400, basis)
    assert [s["step"] for s in steps] == [1, 2, 3, 4], steps
    assert steps[3]["verdict"] == "not_determined", steps[3]
    # the arithmetic is identical to the surveyed case; the labels are not
    with _with_household(PANEL_YAML_NO_SOCKET):
        surveyed = S.socket_basis_of(S.load_panel())
    avail = S.availability(p["service_rating_a"], None, steps[1]["result_a"])
    unasked_rem = S.remaining_headroom(avail, 60.0, basis)
    surveyed_rem = S.remaining_headroom(avail, 60.0, surveyed)
    assert _close(unasked_rem["binding"], surveyed_rem["binding"]), unasked_rem
    assert unasked_rem["binding_is"] != surveyed_rem["binding_is"]
    assert "UPPER LIMIT" in unasked_rem["binding_is"], unasked_rem
    assert "does not apply" not in S.SOCKET_CONSTRAINT[basis], basis
    assert "NOT DETERMINED" in S.SOCKET_CONSTRAINT[basis], basis
    assert "meter_socket_continuous_a" in S.SOCKET_SETTLE
    # and a recorded rating is a third thing again
    with _with_household(PANEL_YAML):
        assert S.socket_basis_of(S.load_panel()) == S.SOCKET_READ
    return "an absent meter-socket key is not_determined, never 'does not apply'"


def case_a_null_pv_backfeed_runs_end_to_end():
    """pv_backfeed_a: null is documented as 'the panel was surveyed and nothing
    backfeeds it'. That is an ANSWER: 0 A of spent allowance, known, and the
    ampacity leg resolves on it instead of being withheld."""
    with _with_household(PANEL_YAML_NO_BACKFEED):
        p = S.load_panel()
    assert p["pv_backfeed_a"] is None, p
    assert p["pv_backfeed_recorded"] is True, p
    existing, basis = S.existing_backfeed(p)
    assert _close(existing, 0.0) and basis == S.BACKFEED_SURVEYED_NONE, basis
    b = S.busbar_120_percent(p["busbar_rating_a"], p["service_rating_a"],
                             existing, basis, p["pv_breaker_position"],
                             p["main_breaker_position"])
    assert _close(b["existing_pv_backfeed_a"], 0.0), b
    assert b["existing_pv_backfeed_basis"] == S.BACKFEED_SURVEYED_NONE, b
    assert _close(b["remaining_backfeed_a"], 65.0), b
    assert b["position_condition"]["verdict"] == "not_determined", b
    # 65 A of allowance takes the 60 A source breaker, and with the existing
    # backfeed KNOWN that is a pass rather than a withheld verdict
    assert S.busbar_ampacity_leg(60.0, b["remaining_backfeed_a"], basis) == "pass"
    # and the same panel with a 70 A breaker still fails on the arithmetic
    assert S.busbar_ampacity_leg(70.0, b["remaining_backfeed_a"], basis) == "fail"
    return "a surveyed null pv_backfeed_a is a known 0 A and decides the leg"


def case_an_absent_pv_backfeed_key_is_not_a_surveyed_zero():
    """The distinction household.get() cannot make on its own. A key that is
    not there is an unanswered question, and it must not inherit the standing
    of an explicit null."""
    with _with_household(PANEL_YAML_BACKFEED_UNASKED):
        p = S.load_panel()
        assert S._key_present("panel.pv_backfeed_a") is False
    assert p["pv_backfeed_a"] is None, p
    assert p["pv_backfeed_recorded"] is False, p
    existing, basis = S.existing_backfeed(p)
    assert _close(existing, 0.0) and basis == S.BACKFEED_NOT_RECORDED, basis
    # same amps, same allowance, different standing
    with _with_household(PANEL_YAML_NO_BACKFEED):
        surveyed = S.existing_backfeed(S.load_panel())
    assert _close(surveyed[0], existing), (surveyed, existing)
    assert surveyed[1] != basis, (surveyed, basis)
    b = S.busbar_120_percent(p["busbar_rating_a"], p["service_rating_a"],
                             existing, basis)
    assert _close(b["remaining_backfeed_a"], 65.0), b
    assert S.busbar_ampacity_leg(60.0, b["remaining_backfeed_a"], basis) == \
        "not_determined"
    assert "not recorded" in S.AMPACITY_LEG_SETTLE or \
        "does not carry" in S.AMPACITY_LEG_SETTLE, S.AMPACITY_LEG_SETTLE
    # a recorded 50 A is a third thing again
    with _with_household(PANEL_YAML):
        assert S.existing_backfeed(S.load_panel()) == (50.0, S.BACKFEED_READ)
    # the presence test needs a parent mapping to look the leaf up in, and says
    # so rather than silently reporting "absent" for a top-level key
    try:
        S._key_present("pv_backfeed_a")
        raise AssertionError("accepted a path with no parent")
    except SystemExit as e:
        assert "dotted path" in str(e), e
    return "an absent pv_backfeed_a key is not_determined, not a surveyed zero"


def case_impossible_panel_values_fail_closed_by_field():
    """Present is not the same as possible. Each value that can flip a verdict
    or make the busbar arithmetic meaningless is checked, and the message names
    the field and the value rather than dying somewhere downstream."""
    for edit, replacement, needle in (
            ("service_rating_a: 175", "service_rating_a: 0", "service_rating_a"),
            ("busbar_rating_a: 200", "busbar_rating_a: -5", "busbar_rating_a"),
            ("meter_socket_continuous_a: 170", "meter_socket_continuous_a: 0",
             "meter_socket_continuous_a"),
            ("pv_backfeed_a: 50", "pv_backfeed_a: -50", "pv_backfeed_a"),
            ("spaces: 20", "spaces: 0", "spaces"),
            ("max_circuits: 40", "max_circuits: 10", "max_circuits"),
            ("service_rating_a: 175", "service_rating_a: 250",
             "service_rating_a")):
        with _with_household(PANEL_YAML.replace(edit, replacement)):
            try:
                S.load_panel()
                raise AssertionError(f"accepted {replacement!r}")
            except SystemExit as e:
                assert f"panel.{needle} is" in str(e), (replacement, str(e))
                assert replacement.split(": ")[1] in str(e), (replacement, str(e))
    # the one that matters most: a negative existing backfeed does not produce a
    # wrong-looking number, it produces a bigger allowance. 200x1.2-175 = 65 A
    # of total backfeed; a -50 A "existing" source turns 15 A remaining into
    # 115 A, which accepts the 60 A battery breaker the real panel refuses.
    honest = S.busbar_120_percent(200.0, 175.0, 50.0)
    flipped = S.busbar_120_percent(200.0, 175.0, -50.0)
    assert _close(honest["remaining_backfeed_a"], 15.0), honest
    assert _close(flipped["remaining_backfeed_a"], 115.0), flipped
    assert S.busbar_ampacity_leg(
        60.0, honest["remaining_backfeed_a"], S.BACKFEED_READ) == "fail"
    assert S.busbar_ampacity_leg(
        60.0, flipped["remaining_backfeed_a"], S.BACKFEED_READ) == "pass"
    return "impossible panel values stop the run naming the field and the value"


def case_non_finite_panel_values_fail_closed():
    """Codex adversarial review, issue #41: NaN and +/-inf both fail EVERY
    `<` and `>` comparison as False, so a bare `not value > 0` check (which
    is what every one of the checks above looked like before this review)
    silently ADMITS them -- neither "positive" nor "negative", the value
    reaches the 120% arithmetic and produces a false safety PASS instead of
    stopping the run. YAML represents these as `.nan`/`.inf`/`-.inf`."""
    for edit, replacement, needle in (
            ("service_rating_a: 175", "service_rating_a: .nan", "service_rating_a"),
            ("busbar_rating_a: 200", "busbar_rating_a: .inf", "busbar_rating_a"),
            ("meter_socket_continuous_a: 170", "meter_socket_continuous_a: .nan",
             "meter_socket_continuous_a"),
            ("pv_backfeed_a: 50", "pv_backfeed_a: .nan", "pv_backfeed_a"),
            ("pv_backfeed_a: 50", "pv_backfeed_a: -.inf", "pv_backfeed_a"),
            ("assembly_sccr_ka: 22", "assembly_sccr_ka: .nan", "assembly_sccr_ka")):
        with _with_household(PANEL_YAML.replace(edit, replacement)):
            try:
                S.load_panel()
                raise AssertionError(f"accepted {replacement!r}")
            except SystemExit as e:
                assert f"panel.{needle} is" in str(e), (replacement, str(e))
    return "NaN and +/-inf panel values fail closed rather than silently passing every sign check"


def case_a_nan_backfeed_does_not_produce_a_false_safety_pass():
    """The exact scenario the finding above closes: BEFORE this fix, `not
    float('nan') > 0.0` and `not float('nan') < 0.0` were BOTH False -- so
    NaN skipped the negative check, skipped the schedule cross-check ('>
    0.0' is also False for NaN), and would have reached
    busbar_ampacity_leg() as a value neither positive nor negative, where
    `60.0 > remaining_a` is False for a NaN remaining_a too, reporting
    'pass' on a panel that was never actually checked (confirmed directly:
    a NaN remaining_a produces exactly that False/'pass' result today).
    validate_panel() now has to be the point that refuses it, since nothing
    downstream will."""
    assert S.busbar_ampacity_leg(60.0, float("nan"), S.BACKFEED_READ) == "pass", (
        "if busbar_ampacity_leg() itself now rejects NaN, this assertion "
        "should be updated to say so explicitly -- but as of this test, "
        "the arithmetic layer does NOT catch it, which is exactly why the "
        "refusal has to happen earlier, in validate_panel()")
    with _with_household(PANEL_YAML.replace("pv_backfeed_a: 50", "pv_backfeed_a: .nan")):
        try:
            S.load_panel()
            raise AssertionError(
                "a NaN pv_backfeed_a reached the panel dict -- it must be "
                "refused before ever reaching busbar_ampacity_leg()")
        except SystemExit as e:
            assert "not a finite number" in str(e), str(e)
    return ("a NaN panel.pv_backfeed_a is refused by validate_panel() before "
           "it can reach busbar_ampacity_leg() and read as neither a pass "
           "nor a fail on the arithmetic")


def case_spaces_and_max_circuits_reject_lossy_coercion():
    """Codex review, issue #41: `int(HH.get("panel.spaces"))` alone silently
    truncates a fractional YAML value (20.9 -> 20) or coerces a boolean
    (True -> 1) BEFORE validate_panel()'s own positivity check ever runs --
    by then the malformed original is gone and the coerced value looks like
    a plausible count. _positive_whole_intake() checks the RAW value at
    load_panel() instead, the same discipline breaker_geometry()'s
    _positive_whole() already applies to schedule pole counts."""
    for edit, replacement, needle in (
            ("spaces: 20", "spaces: 20.9", "spaces"),
            ("spaces: 20", "spaces: true", "spaces"),
            ("spaces: 20", "spaces: 0", "spaces"),
            ("spaces: 20", "spaces: .nan", "spaces"),
            ("max_circuits: 40", "max_circuits: 39.5", "max_circuits"),
            ("max_circuits: 40", "max_circuits: false", "max_circuits")):
        with _with_household(PANEL_YAML.replace(edit, replacement)):
            try:
                S.load_panel()
                raise AssertionError(f"accepted {replacement!r}")
            except SystemExit as e:
                assert f"panel.{needle} is" in str(e), (replacement, str(e))
                assert "positive whole number" in str(e), str(e)
    return ("spaces and max_circuits refuse fractional, boolean, zero and "
           "non-finite raw values instead of silently truncating them "
           "with int()")


def case_panel_float_fields_reject_boolean_coercion():
    """Codex review, issue #41: the mirror of the spaces/max_circuits fix
    above, for float()-coerced fields. `float(True) == 1.0`, so
    panel.assembly_sccr_ka: true would otherwise pass every downstream
    positive/finite check as a falsely-plausible 1.0 kA rating -- checked
    on the raw value now, in _required_number()/_optional_number(), before
    float() ever runs."""
    for edit, replacement, needle in (
            ("service_rating_a: 175", "service_rating_a: true", "service_rating_a"),
            ("busbar_rating_a: 200", "busbar_rating_a: false", "busbar_rating_a"),
            ("assembly_sccr_ka: 22", "assembly_sccr_ka: true", "assembly_sccr_ka"),
            ("pv_backfeed_a: 50", "pv_backfeed_a: true", "pv_backfeed_a"),
            ("meter_socket_continuous_a: 170",
             "meter_socket_continuous_a: false", "meter_socket_continuous_a")):
        with _with_household(PANEL_YAML.replace(edit, replacement)):
            try:
                S.load_panel()
                raise AssertionError(f"accepted {replacement!r}")
            except SystemExit as e:
                assert f"panel.{needle} is" in str(e), (replacement, str(e))
                assert "is not a number" in str(e), str(e)
    return ("panel.service_rating_a/busbar_rating_a/assembly_sccr_ka/"
           "pv_backfeed_a/meter_socket_continuous_a all refuse a boolean "
           "raw value rather than silently coercing it to 1.0/0.0")


def case_panel_domain_checks_accept_the_edges_that_are_real():
    """The guard is on safety arithmetic, not a schema validator: values that
    are unusual but physically possible must still run."""
    ok = (PANEL_YAML
          .replace("pv_backfeed_a: 50", "pv_backfeed_a: 0")
          .replace("service_rating_a: 175", "service_rating_a: 200")
          .replace("max_circuits: 40", "max_circuits: 20"))
    with _with_household(ok):
        p = S.load_panel()
    # a zero existing backfeed is a real reading; a main equal to the busbar and
    # a panel with no twin-density capacity are real panels
    assert _close(p["pv_backfeed_a"], 0.0) and p["max_circuits"] == 20, p
    assert _close(p["service_rating_a"], p["busbar_rating_a"]), p
    with _with_household(PANEL_YAML_NO_SOCKET):
        assert S.load_panel()["meter_socket_continuous_a"] is None
    with _with_household(PANEL_YAML_NO_BACKFEED):
        assert S.load_panel()["pv_backfeed_a"] is None
    return "a zero backfeed, a main equal to the busbar and null optionals still run"


# ---------------------------------------------------------------------------
# issue #41: the rest of the panel schema, one negative test per new rule
# ---------------------------------------------------------------------------

def case_free_text_panel_fields_fail_closed_on_blank_or_absurd_length():
    """main_breaker_catalog, enclosure_catalog, enclosure_type and
    breaker_family have no invented vocabulary -- they are manufacturer part
    numbers, family names and hand-transcribed enclosure descriptions -- but
    _private_text_ok() still catches a blank answer or one far longer than
    any real catalog number, family name or description in this project's
    own intake (the longest today is under 60 characters). The bad value
    itself never reaches stderr: these are all private-only fields."""
    edits = {
        "enclosure_type": "enclosure_type: NEMA 1 indoor, meter-main combination",
        "breaker_family": "breaker_family: Test-Brand BR",
        "main_breaker_catalog": "main_breaker_catalog: TESTCO-MAIN01",
        "enclosure_catalog": "enclosure_catalog: TESTCO-ENC02",
    }
    for field, edit in edits.items():
        with _with_household(PANEL_YAML.replace(edit, f'{field}: ""')):
            try:
                S.load_panel()
                raise AssertionError(f"a blank {field} was accepted")
            except SystemExit as e:
                assert f"panel.{field}" in str(e), (field, str(e))
                assert "empty" in str(e), (field, str(e))
        overlong = "x" * 250
        with _with_household(
                PANEL_YAML.replace(edit, f'{field}: "{overlong}"')):
            try:
                S.load_panel()
                raise AssertionError(f"an absurdly long {field} was accepted")
            except SystemExit as e:
                assert f"panel.{field}" in str(e), (field, str(e))
                assert "characters" in str(e), (field, str(e))
                assert overlong not in str(e), \
                    (field, "the private value reached stderr", str(e))
    return ("the free-text catalog/family/enclosure fields fail closed on a "
            "blank or absurdly long value, and the value never reaches stderr")


def case_meter_class_format_fails_closed():
    """panel.meter_class has a real, checkable format -- the cheatsheet's own
    question spells out the convention ('CL10, CL100, CL320') -- so a
    value outside 'CL' + digits is a bad reading, not a variant spelling.
    This is a format check, not a closed list of legitimate class numbers."""
    for bad in ("Class 200", "cl100x", "200", "CL 100"):
        text = PANEL_YAML.replace("meter_class: CL100",
                                  f"meter_class: {bad!r}")
        with _with_household(text):
            try:
                S.load_panel()
                raise AssertionError(f"accepted meter_class {bad!r}")
            except SystemExit as e:
                assert "panel.meter_class" in str(e), (bad, str(e))
                assert "ANSI meter-class format" in str(e), (bad, str(e))
                assert bad not in str(e), \
                    (bad, "the private value reached stderr", str(e))
    return "meter_class outside the 'CL' + digits format fails closed"


def case_assembly_sccr_ka_must_be_positive():
    """assembly_sccr_ka was never read at all before issue #41, so it was
    never checked either. A short-circuit current rating of zero or negative
    is not a figure any rating label prints."""
    for bad in ("0", "-5"):
        text = PANEL_YAML.replace("assembly_sccr_ka: 22",
                                  f"assembly_sccr_ka: {bad}")
        with _with_household(text):
            try:
                S.load_panel()
                raise AssertionError(f"accepted assembly_sccr_ka: {bad}")
            except SystemExit as e:
                assert "panel.assembly_sccr_ka is" in str(e), (bad, str(e))
                assert bad in str(e), (bad, str(e))
    return "a non-positive assembly_sccr_ka fails closed, naming the value"


def case_meter_socket_requires_a_meter_main_enclosure():
    """meter_socket_continuous_a is the meter socket's own printed rating,
    and that rating only exists where the meter shares the main's enclosure.
    A number recorded against a panel whose enclosure_type says otherwise is
    two intake answers disagreeing, not a fact about this panel."""
    not_combo = PANEL_YAML.replace(
        "enclosure_type: NEMA 1 indoor, meter-main combination",
        "enclosure_type: NEMA 1 indoor, main-breaker load center")
    with _with_household(not_combo):
        try:
            S.load_panel()
            raise AssertionError("a meter-socket rating on a non-meter-main "
                                 "enclosure was accepted")
        except SystemExit as e:
            assert "panel.meter_socket_continuous_a is 170" in str(e), e
            assert "meter-main" in str(e), e
            # enclosure_type is private-only: its own text never reaches stderr
            assert "load center" not in str(e), e
    return ("a recorded meter-socket rating on a panel not described as a "
            "meter-main combination fails closed")


def case_meter_socket_accepts_the_unhyphenated_spelling_too():
    """Codex adversarial review, issue #41, pass 3: 'meter-main' and
    'meter main' describe the same arrangement -- the hyphen is a spelling
    choice, not a different fact, and a genuine survey answer spelled
    without it must not fail closed over punctuation alone."""
    unhyphenated = PANEL_YAML.replace(
        "enclosure_type: NEMA 1 indoor, meter-main combination",
        "enclosure_type: NEMA 1 indoor, meter main combination")
    with _with_household(unhyphenated):
        p = S.load_panel()  # must NOT raise
    assert p["meter_socket_continuous_a"] == 170.0, p
    return "panel.enclosure_type spelled 'meter main' (no hyphen) is accepted, not just 'meter-main'"


def case_pv_backfeed_must_match_a_schedule_breaker():
    """A POSITIVE pv_backfeed_a claims one specific installed breaker; if no
    breaker in panel.schedule carries that rating, the two intake answers
    disagree and the 120% arithmetic would spend an allowance against a
    breaker nobody catalogued. (0.0 is exempt from this rule --
    case_panel_domain_checks_accept_the_edges_that_are_real covers that
    edge, since no real breaker is rated 0 A for it to match.)"""
    no_match = PANEL_YAML.replace("pv_backfeed_a: 50", "pv_backfeed_a: 65")
    with _with_household(no_match):
        try:
            S.load_panel()
            raise AssertionError("a pv_backfeed_a with no matching schedule "
                                 "breaker was accepted")
        except SystemExit as e:
            assert "panel.pv_backfeed_a is 65" in str(e), e
            assert "does not match" in str(e), e
    return "pv_backfeed_a with no matching schedule breaker fails closed"


def case_KNOWN_LIMITATION_an_unrelated_same_rated_breaker_still_passes():
    """Codex adversarial review, issue #41, pass 2, NOT fixed here (filed as
    a follow-up, see validate_panel()'s own comment on the check above): the
    schedule-match cross-check is amp-VALUE membership only, with no row
    IDENTITY -- panel.schedule has no role/ID field distinguishing "this
    row is the PV breaker" from "this row happens to share its rating."

    This test documents the gap on purpose, asserting the CURRENT (still
    limited) behavior rather than silently letting it drift: with the real
    50 A "PV backfeed" row removed from the schedule and an unrelated 50 A
    breaker left in its place (an EV charger, say), pv_backfeed_a: 50 still
    passes -- an omitted PV breaker silently vanishes from panel_occupancy's
    space/pole count and OCPD sum with no error. If this is ever fixed (the
    real fix needs a structured schedule role/ID, an intake-contract change
    outside this issue's scope), THIS TEST should start failing and should
    be updated to assert the new, stricter behavior instead of loosened to
    keep passing."""
    pv_breaker_removed = PANEL_YAML.replace(
        "    - {device: full-size 2-pole, poles: 2, amps: 60, label: EV charger}\n"
        "    - {device: tandem, poles: 2, amps: [20, 20], label: Kitchen}\n"
        "    - {device: quad, poles: 4, amps: [15, 20, 20, 15], label: Test kitchen circuit}\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 50, label: PV backfeed}\n",
        # the real PV breaker row is gone; an UNRELATED 50 A breaker (a second
        # EV charger, nothing to do with solar) coincidentally shares its rating
        "    - {device: full-size 2-pole, poles: 2, amps: 60, label: EV charger}\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 50, label: Second EV charger}\n"
        "    - {device: tandem, poles: 2, amps: [20, 20], label: Kitchen}\n"
        "    - {device: quad, poles: 4, amps: [15, 20, 20, 15], label: Test kitchen circuit}\n")
    assert pv_breaker_removed != PANEL_YAML, "test needs updating: fixture text not found"
    with _with_household(pv_breaker_removed):
        p = S.load_panel()  # must NOT raise -- this is the documented gap, not a crash
    assert p["pv_backfeed_a"] == 50.0, p
    occ = S.panel_occupancy(p["schedule"], p["spaces"], p["max_circuits"])
    # the real PV breaker's 2 spaces / 50 A are simply absent from these totals
    # -- nothing here flags that the amp-match came from an unrelated device
    assert not any("PV backfeed" in str(e.get("label", "")) for e in p["schedule"]), (
        "test setup error: the real PV breaker row is still present")
    return ("KNOWN LIMITATION, tracked not silently regressed: an unrelated "
           "same-rated breaker still satisfies the pv_backfeed_a schedule "
           "cross-check when the real PV breaker is omitted entirely")


def case_unrecognized_breaker_position_fails_closed():
    """_end() maps anything outside {'top', 'bottom'} to None, and downstream
    that reads as 'not surveyed' -- exactly what an unanswered question looks
    like. validate_panel() has to tell a REAL typo apart from that before
    _end() ever sees it, or a bad answer (a mis-typed 'buttom') silently
    reads the same as nobody having looked."""
    cases = (
        ("pv_breaker_position",
         PANEL_YAML.replace("pv_breaker_position: bottom",
                            "pv_breaker_position: buttom"), "buttom"),
        ("main_breaker_position",
         PANEL_YAML + "  main_breaker_position: middle\n", "middle"),
        ("battery_breaker_position",
         PANEL_YAML + "  battery_breaker_position: sideways\n", "sideways"),
    )
    for field, text, bad in cases:
        with _with_household(text):
            try:
                S.load_panel()
                raise AssertionError(f"accepted {field}: {bad!r}")
            except SystemExit as e:
                assert f"panel.{field} is {bad!r}" in str(e), (field, str(e))
                assert "recognized busbar end" in str(e), (field, str(e))
    # a real value survives case and surrounding whitespace -- this is the
    # normalization the bad values above must not be confused with
    with _with_household(PANEL_YAML + "  main_breaker_position: ' Top '\n"):
        p = S.load_panel()
        assert p["main_breaker_position"] == " Top ", p
        assert S._end(p["main_breaker_position"]) == "top", p
    return "an unrecognized breaker position fails closed, not 'not surveyed'"


def case_schedule_device_and_label_must_be_non_empty_text():
    """Every schedule row's device marking and door-legend label must be
    non-empty text, the same free-text sanity the panel-level catalog fields
    get. The row is named POSITIONALLY in the message -- breaker_geometry()'s
    own discipline -- because the label is private-tier intake."""
    full = "device: full-size 2-pole, poles: 2, amps: 60, label: EV charger"
    for bad_field, replacement in (
            ("device", "device: '', poles: 2, amps: 60, label: EV charger"),
            ("label", "device: full-size 2-pole, poles: 2, amps: 60, "
                      "label: ''")):
        with _with_household(PANEL_YAML.replace(full, replacement)):
            try:
                S.load_panel()
                raise AssertionError(
                    f"a blank schedule {bad_field} was accepted")
            except SystemExit as e:
                assert f"panel.schedule[1].{bad_field}" in str(e), \
                    (bad_field, str(e))
                assert "empty" in str(e), (bad_field, str(e))
    return "a blank schedule device or label fails closed, naming the row"


def case_a_schedule_larger_than_its_enclosure_fails_closed():
    """Two intake answers about one panel can disagree. If they do, every
    free-space figure below is negative or invented, so the run stops."""
    sched = [{"poles": 2, "amps": 60, "label": "a"},        # 2 spaces, 2 poles
             {"poles": 2, "amps": [20, 20], "label": "b"},  # 1 space,  2 poles
             {"poles": 4, "amps": [15, 20, 20, 15], "label": "c"}]  # 2 sp, 4 p
    assert S.panel_occupancy(sched, 5, 8)["spaces_free"] == 0
    try:
        S.panel_occupancy(sched, 4, 40)
        raise AssertionError("a schedule overfilling the enclosure was accepted")
    except SystemExit as e:
        assert "occupies 5 full-size spaces" in str(e), e
        assert "panel.spaces records 4" in str(e), e
    try:
        S.panel_occupancy(sched, 20, 6)
        raise AssertionError("a schedule overfilling the pole positions was accepted")
    except SystemExit as e:
        assert "occupies 8 pole positions" in str(e), e
        assert "panel.max_circuits records 6" in str(e), e
    return "a schedule that overfills its own enclosure stops the run"


def case_physical_fit_is_three_valued():
    """A 240 V circuit needs two ADJACENT full-size spaces and the schedule
    records devices, not slot positions. A shortage is determinable from the
    count; a fit is not, and used to be published as a boolean true."""
    # one free space, one 2-pole breaker wanted: short on the count alone
    assert S.physical_fit(1, 1, None) == "fail"
    assert S.physical_fit(2, 3, None) == "fail"
    # enough free spaces, no positions recorded: not a fit, not a failure
    assert S.physical_fit(1, 2, None) == "not_determined"
    assert S.physical_fit(3, 6, None) == "not_determined"
    # only recorded positions can produce a pass, and they can still fail
    assert S.physical_fit(1, 2, 1) == "pass"
    assert S.physical_fit(2, 6, 2) == "pass"
    assert S.physical_fit(2, 6, 1) == "fail"
    assert S.physical_fit(1, 4, 0) == "fail"
    # a case adding NO breaker needs no adjacent pair, whatever the schedule
    # records: the short-circuit on adjacent_free_pairs used to report a
    # not_determined for a configuration with nothing to fit
    assert S.physical_fit(0, 1, None) == "pass"
    assert S.physical_fit(0, 0, None) == "pass"
    assert S.physical_fit(0, 4, 0) == "pass"
    assert "adjacency is not in the data" in S.PHYSICAL_FIT_BASIS
    assert "Slot positions" in S.PHYSICAL_FIT_SETTLE
    return "physical fit is three-valued: a count can fail a case but never pass one"


def case_breaker_positions_are_read_from_the_intake():
    with _with_household(PANEL_YAML):
        p = S.load_panel()
    # this household records the backfeed end and not the main's
    assert p["pv_breaker_position"] == "bottom", p
    assert p["main_breaker_position"] is None, p
    with _with_household(PANEL_YAML + "  main_breaker_position: top\n"):
        p2 = S.load_panel()
    assert p2["main_breaker_position"] == "top", p2
    assert S.position_condition(p2["pv_breaker_position"],
                                p2["main_breaker_position"])["verdict"] == "pass"
    return "load_panel reads both breaker positions, absent ones as None"


# ---------------------------------------------------------------------------
# Panel schedule geometry
# ---------------------------------------------------------------------------

def case_pole_counting_handles_int_and_list_amps():
    # a full-size 2-pole breaker: one OCPD, two spaces
    assert S.breaker_geometry({"poles": 2, "amps": 60}) == (2, 2, [60.0])
    # a tandem: two 1-pole OCPDs sharing one space
    assert S.breaker_geometry({"poles": 2, "amps": [20, 20]}) == (1, 2, [20.0, 20.0])
    # a quad: two 2-pole OCPDs across two spaces, outer and inner pairs
    assert S.breaker_geometry({"poles": 4, "amps": [20, 30, 30, 20]}) == (
        2, 4, [20.0, 30.0])
    return "breaker geometry reads an int as full-size and a list as twin-density"


def case_malformed_schedule_entries_fail_closed():
    for entry, needle in (
            ({"poles": 2, "amps": [20, 20, 20], "label": "x"}, "3 amp values"),
            ({"poles": 4, "amps": [20, 30, 20, 30], "label": "x"}, "common-trip"),
            ({"poles": 6, "amps": [15] * 6, "label": "x"}, "twin-density")):
        try:
            S.breaker_geometry(entry, "schedule entry 3 of 9")
            raise AssertionError(f"accepted a malformed entry: {entry}")
        except SystemExit as e:
            assert needle in str(e), (needle, str(e))
            # the message locates the row positionally; the door-legend label
            # is private-tier intake and has no business on stderr
            assert "schedule entry 3 of 9" in str(e), str(e)
            assert entry["label"] not in str(e), str(e)
    return "malformed tandem/quad entries stop the run rather than miscounting"


def case_breaker_geometry_rejects_non_finite_or_non_positive_poles():
    """Codex adversarial review, issue #41: poles/amps went through int()/
    float() with no domain check -- a negative pole count or a non-finite
    amp rating passed straight through into panel_occupancy()'s real
    arithmetic, silently corrupting the reported free capacity rather than
    stopping the run."""
    for entry, needle in (
            ({"poles": -2, "amps": -100, "label": "x"}, "poles"),
            ({"poles": 0, "amps": 20, "label": "x"}, "poles"),
            ({"poles": 2.5, "amps": 20, "label": "x"}, "poles"),
            ({"poles": True, "amps": 20, "label": "x"}, "poles"),
            ({"poles": float("nan"), "amps": 20, "label": "x"}, "poles"),
            ({"poles": float("inf"), "amps": 20, "label": "x"}, "poles")):
        try:
            S.breaker_geometry(entry, "schedule entry 1 of 1")
            raise AssertionError(f"accepted a malformed pole count: {entry}")
        except SystemExit as e:
            assert needle in str(e), (needle, str(e))
    return ("breaker_geometry refuses negative, zero, fractional, boolean, "
           "NaN and infinite pole counts")


def case_breaker_geometry_rejects_non_finite_or_non_positive_amps():
    for entry, needle in (
            ({"poles": 2, "amps": -60, "label": "x"}, "amps"),
            ({"poles": 2, "amps": 0, "label": "x"}, "amps"),
            ({"poles": 2, "amps": float("nan"), "label": "x"}, "amps"),
            ({"poles": 2, "amps": float("inf"), "label": "x"}, "amps"),
            ({"poles": 2, "amps": [20, -20], "label": "x"}, "amps[1]"),
            ({"poles": 2, "amps": [float("nan"), 20], "label": "x"}, "amps[0]")):
        try:
            S.breaker_geometry(entry, "schedule entry 1 of 1")
            raise AssertionError(f"accepted a malformed amp rating: {entry}")
        except SystemExit as e:
            assert needle in str(e), (needle, str(e))
    return ("breaker_geometry refuses negative, zero, NaN and infinite amp "
           "ratings, in both the scalar and twin-density-list forms")


def case_panel_occupancy_counts_spaces_poles_and_ocpd():
    sched = [{"poles": 2, "amps": 60, "label": "a"},        # 2 spaces, 2 poles, 60
             {"poles": 2, "amps": [20, 20], "label": "b"},  # 1 space,  2 poles, 40
             {"poles": 4, "amps": [15, 20, 20, 15], "label": "c"}]  # 2 sp, 4 p, 35
    occ = S.panel_occupancy(sched, 20, 40)
    assert occ["spaces_used"] == 5, occ
    assert occ["spaces_free"] == 15, occ
    assert occ["pole_positions_used"] == 8, occ
    assert _close(occ["branch_ocpd_sum_a"], 135.0), occ
    assert occ["twin_density_devices"] == 2, occ
    return "occupancy separates spaces from pole positions and sums branch OCPDs"


# ---------------------------------------------------------------------------
# Gross reconstruction
# ---------------------------------------------------------------------------

def case_gross_is_exact_only_where_nothing_was_produced():
    d = dt.date(2026, 1, 15)
    rows = [(d, 0.0, 1.0, 0.0),     # dark hour, no export
            (d, 12.0, 0.5, 0.4),    # daylight, exporting
            (d, 13.0, 0.5, 0.0)]    # daylight, importing under PV
    pv = {(d, 0): 0.0, (d, 12): 2.0, (d, 13): 2.0}
    env = S.gross_envelope(rows, pv, 8.0)
    # dark hour: bound collapses, gross is the metered import
    assert env[0][5] is True and _close(env[0][2], 4.0) and _close(env[0][3], 4.0)
    # exporting daylight interval: lower is the import, upper adds the capped PV
    assert env[1][5] is False
    assert _close(env[1][2], 2.0), env[1]
    assert _close(env[1][3], (0.5 - 0.4 + min(2.0, 8.0 * 0.25)) * 4.0), env[1]
    # a zero-export daylight interval is still NOT exact: PV can be fully
    # self-consumed, which is the mistake that makes a headroom answer optimistic
    assert env[2][5] is False, env[2]
    assert env[2][3] > env[2][2], env[2]
    return "gross is a point only when the hour produced nothing, never merely on zero export"


def case_the_pv_ceiling_is_the_inverter_nameplate():
    """The per-interval PV cap is a PHYSICAL bound -- the inverters' AC
    nameplate -- not the largest hourly production observed. A quarter-hour can
    legitimately carry more than a quarter of the best full hour, so capping on
    the observed maximum produces an 'upper bound' able to sit below true gross
    demand, which is the direction a capacity answer must never fail in."""
    corr = [("derived hourly PV", 8.878, "hourly mean"),
            ("15-minute meter export", 8.3, "export"),
            ("5-minute inverter output", 7.932, "AC output")]
    c = S.pv_ac_ceiling(9.45, "Example IQ Micro", 30, corr)
    assert _close(c["ceiling_kw"], 9.45), c
    assert _close(c["per_interval_ceiling_kwh"], 9.45 * 0.25), c
    assert "nameplate" in c["basis"] and "Example IQ Micro" in c["basis"], c
    assert "30 x" in c["basis"], c
    # the nameplate is LOOSER than every observation, which is the safe
    # direction: the empirical 8.878 would have capped a quarter-hour at
    # 2.2195 kWh, below what the array can physically deliver
    assert c["ceiling_kw"] > max(k for _n, k, _m in corr), c
    for row in c["corroboration"]:
        assert row["exceeds_nameplate"] is False, row
        assert row["below_nameplate_by_kw"] > 0.0, row
    # and the envelope uses it: the cap is nameplate x 0.25, not observed x 0.25
    d = dt.date(2026, 1, 15)
    env = S.gross_envelope([(d, 12.0, 0.0, 0.0)], {(d, 12): 99.0}, 9.45)
    assert _close(env[0][3], 9.45 * 0.25 * 4.0), env
    assert env[0][6] == S.PV_BASIS_MEASURED, env
    return "the per-interval PV ceiling is the inverter AC nameplate, not an observation"


def case_an_observed_pv_maximum_above_the_nameplate_fails_closed():
    """If an instrument reads above the nameplate, neither figure is a ceiling:
    the array is mislabelled, the export is rescaled, or it is the wrong
    system. Publishing either one would be a guess."""
    try:
        S.pv_ac_ceiling(9.45, "Example IQ Micro", 30,
                        [("derived hourly PV", 8.878, "hourly mean"),
                         ("5-minute inverter output", 10.2, "AC output")])
        raise AssertionError("an observation above the nameplate was accepted")
    except SystemExit as e:
        assert "EXCEEDS the inverter AC nameplate" in str(e), e
        assert "5-minute inverter output" in str(e), e
        assert "10.200 kW" in str(e) and "0.750 kW above" in str(e), e
        assert "derived hourly PV" not in str(e), e   # names which one, not all
    return "an empirical maximum above the inverter nameplate stops the run"


def case_an_absent_pv_nameplate_stops_the_run():
    """No fallback to the empirical maximum, because that figure is not a bound
    and every ampacity verdict here rests on the envelope being one."""
    with _with_household(PANEL_YAML):
        assert _close(S.load_pv_ac_nameplate(), 9.45)
    for yaml_text in (PANEL_YAML.replace("  kw_ac: 9.45\n", ""),
                      PANEL_YAML.replace("kw_ac: 9.45", "kw_ac: 0")):
        with _with_household(yaml_text):
            try:
                S.load_pv_ac_nameplate()
                raise AssertionError("a missing inverter nameplate was accepted")
            except SystemExit as e:
                assert "solar.kw_ac" in str(e), e
                assert "not a substitute" in str(e), e
                assert "stops rather than publish" in str(e), e
    return "an absent or non-positive solar.kw_ac stops the run, with no empirical fallback"


def case_the_battery_position_leg_is_its_own_input():
    """The proposed battery breaker's position is a separate question from
    where the existing PV breaker sits. Inheriting the PV breaker's end would
    let the battery read as compliant because somebody else's breaker happens
    to sit opposite the main."""
    with _with_household(PANEL_YAML):
        p = S.load_panel()
    assert p["pv_breaker_position"] == "bottom", p
    assert p["battery_breaker_position"] is None, p
    # the existing PV breaker WOULD pass this leg against a top-fed main ...
    assert S.position_condition("bottom", "top",
                                S.SOURCE_EXISTING_PV)["verdict"] == "pass"
    # ... and the battery, on the same panel, is not determined
    b = S.busbar_120_percent(200.0, 100.0, 50.0, S.BACKFEED_READ,
                             p["battery_breaker_position"], "top",
                             S.SOURCE_PROPOSED_BATTERY)
    pos = b["position_condition"]
    assert pos["verdict"] == "not_determined", pos
    assert pos["source"] == S.SOURCE_PROPOSED_BATTERY, pos
    assert pos["source_breaker_position"] is None, pos
    assert "SURVEYED" in pos["what_would_settle_it"], pos
    assert "two adjacent" in pos["what_would_settle_it"], pos
    assert "not carried over" in pos["what_would_settle_it"], pos
    # a surveyed position does decide it -- at the same end as the main, it fails
    with _with_household(PANEL_YAML + "  battery_breaker_position: top\n"):
        p2 = S.load_panel()
    assert p2["battery_breaker_position"] == "top", p2
    assert p2["pv_breaker_position"] == "bottom", p2
    assert S.position_condition(p2["battery_breaker_position"], "top",
                                S.SOURCE_PROPOSED_BATTERY)["verdict"] == "fail"
    assert S.position_condition(p2["battery_breaker_position"], "bottom",
                                S.SOURCE_PROPOSED_BATTERY)["verdict"] == "pass"
    return "the battery's position leg reads its own intake field, never the PV breaker's"


def case_uncovered_hours_take_the_nameplate_ceiling_not_an_empirical_one():
    """An hour the Enphase file does not cover has no measurement of itself, so
    the only ceiling that holds on it is the inverters' AC nameplate.

    The largest production previously OBSERVED at the same hour of day is not a
    bound on it: an uncovered hour can legitimately beat every hour yet seen at
    that clock position. Narrowing to the smaller of the two would pick the
    empirical figure whenever it was lower -- which, on this array, is the case
    at fourteen of the twenty-four hours of day -- and the result would sit
    BELOW true gross demand, the one direction a capacity verdict may not err
    in. The fixture is built so the two answers differ and only one is a bound.
    """
    d = dt.date(2026, 1, 15)
    kw_ac, cap = 8.0, 8.0 * 0.25          # 2.0 kWh in a quarter-hour
    rows = [(d, 1.0, 1.0, 0.0),           # dark hour, uncovered
            (d, 12.0, 1.0, 0.0),          # midday, uncovered
            (d, 13.0, 1.0, 0.0)]          # midday, covered by a small reading
    # A covered hour with a SMALL reading, and an empirical hour-of-day maximum
    # that is smaller than the nameplate cap at both uncovered hours. Under the
    # retired min(empirical, nameplate) rule 01:00 would have been capped at
    # 0.05 kWh and 12:00 at 1.5 kWh -- both below the cap, both not bounds.
    pv = {(d, 13): 0.5}
    env = S.gross_envelope(rows, pv, kw_ac)
    # the uncovered dark hour: the full nameplate cap, NOT the 0.05 kWh that is
    # all this array has ever made at 01:00. The bound is loose and honest.
    assert env[0][6] == S.PV_BASIS_NAMEPLATE, env[0]
    assert _close(env[0][3], (1.0 + cap) * 4.0), env[0]
    assert env[0][5] is False, env[0]     # never point-determined when uncovered
    # the uncovered midday hour: the same cap, above the 1.5 kWh empirical figure
    assert env[1][6] == S.PV_BASIS_NAMEPLATE, env[1]
    assert _close(env[1][3], (1.0 + cap) * 4.0), env[1]
    # the covered hour keeps its own measurement, which is narrower than the cap
    assert env[2][6] == S.PV_BASIS_MEASURED, env[2]
    assert _close(env[2][3], (1.0 + 0.5) * 4.0), env[2]
    assert env[2][3] < env[1][3], (env[1], env[2])
    # and the whole point: the nameplate answer is the LARGER one everywhere the
    # two differ, so the envelope stays an upper bound
    for e in (env[0], env[1]):
        assert e[3] >= env[2][3], e
    return "an uncovered hour takes the nameplate ceiling, never an empirical one"


def _sam_csv(path, year, hours, tail_zeros):
    """An 8760-row Enphase export with a zero-filled future tail."""
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    vals = [hours(i) for i in range(n - tail_zeros)] + [0.0] * tail_zeros
    path.write_text("kWh\n" + "".join(f"{v}\n" for v in vals))


def case_the_zero_padded_tail_and_the_dst_days_take_the_nameplate_path():
    """The two named sources of uncovered hours, checked end to end.

    Both were previously bounded by the per-hour-of-day empirical maximum, and
    both are the loosest part of the record -- the tail because the file simply
    stops, the DST days because the flat 8760 grid cannot be aligned to a day
    that is 23 or 25 hours long. Neither may borrow another day's production as
    a ceiling.
    """
    year = 2026
    spring, _fall = R.dst_transition_sundays(year)
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / f"enphase_sam8760_{year}.csv"
        # 48 zero rows at the end = the last two days of the year uncovered
        _sam_csv(p, year, lambda i: 1.0 + (i % 24) * 0.01, 48)
        sam, prov = S.load_sam([p])
    assert prov[0]["zero_padded_rows"] == 48, prov
    last_covered = max(sam)
    tail_day = dt.date(year, 12, 31)
    assert (tail_day, 12) not in sam, "the zero tail must not read as measurement"

    dst = {spring}
    days = [spring, tail_day, dt.date(year, 6, 15)]
    hsums = {(d, h): (1.0, 0.0, 4) for d in days for h in range(24)}
    pv = S.derive_pv(hsums, sam, dst)
    # the DST day is covered by the file but excluded from the join ...
    assert (spring, 12) in sam and (spring, 12) not in pv, spring
    # ... and the tail is not in the file at all
    assert (tail_day, 12) not in pv, tail_day
    # a normal day inside coverage is derived as usual
    assert (dt.date(year, 6, 15), 12) in pv

    rows = [(d, 12.0, 1.0, 0.0) for d in days]
    env = S.gross_envelope(rows, pv, 8.0)
    assert env[0][6] == S.PV_BASIS_NAMEPLATE, ("dst day", env[0])
    assert env[1][6] == S.PV_BASIS_NAMEPLATE, ("zero tail", env[1])
    assert env[2][6] == S.PV_BASIS_MEASURED, ("covered day", env[2])
    for e in (env[0], env[1]):
        assert _close(e[3], (1.0 + 8.0 * 0.25) * 4.0), e

    split = S.ceiling_basis_split(env, dst, sam)
    assert split["nameplate_intervals"] == 2, split
    assert split["measured_hour_intervals"] == 1, split
    by = split["nameplate_intervals_by_reason"]
    assert by["excluded_dst_day"] == 1, by
    assert by["after_the_last_hour_the_enphase_files_measured"] == 1, by
    assert sum(by.values()) == split["nameplate_intervals"], split
    assert split["enphase_coverage_last_hour"].startswith(str(last_covered[0]))
    assert "not a ceiling on it" in split["why_not_the_empirical_hour_of_day_maximum"]
    return "the zero-padded tail and the DST days are bounded by the nameplate alone"


def case_the_ceiling_split_must_account_for_every_nameplate_interval():
    """The split is published beside the envelope, so an interval it cannot
    explain is a defect in the published figure, not a rounding detail."""
    d = dt.date(2026, 6, 15)
    env = [(d, 12.0, 4.0, 6.0, 0.0, False, S.PV_BASIS_NAMEPLATE)]
    # the interval sits INSIDE coverage and is not a DST day, so it lands in the
    # gap bucket rather than being silently dropped
    sam = {(d, 11): 1.0, (d, 13): 1.0}
    split = S.ceiling_basis_split(env, set(), sam)
    assert split["nameplate_intervals_by_reason"] == {
        "missing_hour_inside_the_enphase_coverage": 1}, split
    return "every nameplate-ceiling interval is attributed to a stated reason"


def _reference_days(n=120, seed=7):
    """[(date, refA_kwh)] -- a weather-like daily production series.

    Deterministic but not monotonic: a straight ramp correlates at 1.0 with its
    own one-day shift, which would hide exactly the failure the correlation and
    MAE gates exist to catch.
    """
    rnd = random.Random(seed)
    d0 = dt.date(2026, 1, 1)
    return [(d0 + dt.timedelta(days=i), 20.0 + 30.0 * rnd.random())
            for i in range(n)]


def _run_conservation(pairs, derived, ref_b_scale=1.02, excluded=(), extra=()):
    """conservation_check() against a synthetic threeway CSV.

    `pairs` is [(date, refA_kwh)] and `derived` the derived daily series over
    the same dates. `extra` adds rows to the reference file that the derived
    series does not cover, so a short overlap can be built without shortening
    the reference.
    """
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "threeway.csv"
        lines = [",refA,refB"]
        for d, v in list(pairs) + list(extra):
            lines.append(f"{d},{v},{v * ref_b_scale}")
        p.write_text("\n".join(lines) + "\n")
        pv = {(d, 11): got for (d, _v), got in zip(pairs, derived)}
        old = S.THREEWAY
        S.THREEWAY = p
        try:
            return S.conservation_check(pv, set(excluded))
        finally:
            S.THREEWAY = old


def _conservation_fails(pairs, derived, **kw):
    """The SystemExit message from a conservation run that must not publish."""
    try:
        _run_conservation(pairs, derived, **kw)
    except SystemExit as e:
        return str(e)
    raise AssertionError("a broken conservation comparison was published")


def case_conservation_residual_is_computed_and_bounded():
    pairs = _reference_days(120)
    dst = dt.date(2026, 3, 8)
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "threeway.csv"
        lines = [",refA,refB"]
        for d, v in pairs:
            lines.append(f"{d},{v},{v * 1.02}")
        lines.append(f"{dst},1.0,1.0")
        p.write_text("\n".join(lines) + "\n")
        pv = {}
        for d, v in pairs:
            pv[(d, 11)] = v * 1.01           # derived series is 1% high
            pv[(d, 12)] = 0.0
        pv[(dst, 11)] = 9999.0               # a DST day must never enter the sum
        old = S.THREEWAY
        S.THREEWAY = p
        try:
            out = S.conservation_check(pv, {dst})
        finally:
            S.THREEWAY = old
    # 120 fixture days minus the DST Sunday, which falls inside the window
    assert out["days_compared"] == 119, out
    assert str(dst) in out["dst_days_excluded"], out
    a = out["against"]["refA"]
    assert _close(a["ratio_derived_over_reference"], 1.01, 1e-4), a
    assert _close(a["residual_pct"], 1.0, 1e-3), a
    # a series exactly 1.01x another correlates at 1.0 to floating point, not
    # to four decimal places
    assert _close(a["correlation"], 1.0, 1e-12), a
    assert a["mae_kwh_per_day"] > 0.0, a
    assert _close(out["references_disagree_pct"], 2.0, 1e-2), out
    # the 9999 kWh day would be the largest single term in the sum if included
    assert out["derived_total_kwh"] < 9999.0, out
    return "the conservation residual is computed, bounded, and excludes DST days"


def case_conservation_gates_are_declared_with_thresholds():
    """Every gate names what it observed, what it had to satisfy and what a
    breach would mean. A check whose threshold is not written down is not one a
    reader can disagree with."""
    pairs = _reference_days(120)
    out = _run_conservation(pairs, [v * 1.01 for _d, v in pairs])
    assert out["gates_total"] == 7, out["gates_total"]
    assert out["gates_passed"] == out["gates_total"], out
    names = [g["gate"] for g in out["gates"]]
    assert "minimum_overlapping_days" in names, names
    for ref in ("refA", "refB"):
        for stem in ("abs_residual_pct_vs_", "mae_kwh_per_day_vs_",
                     "correlation_vs_"):
            assert stem + ref in names, (stem, ref, names)
    for g in out["gates"]:
        assert g["threshold"] is not None and g["comparison"] in ("<=", ">="), g
        assert g["requirement"] and g["catches"], g
        assert g["passed"] is True, g
    assert "halting" in out["gates_are"], out["gates_are"]
    return "each conservation gate declares its threshold, its reading and what it catches"


def case_a_rescaled_series_fails_the_residual_gate():
    """A DC-for-AC substitution or a unit error moves the total. The residual
    gate is what refuses to publish it."""
    pairs = _reference_days(120)
    msg = _conservation_fails(pairs, [v * 1.20 for _d, v in pairs])
    assert "FAILED" in msg and "abs_residual_pct_vs_refA" in msg, msg
    assert "must be <= 5.0" in msg, msg
    assert "nothing was written" in msg, msg
    # 20% is the breach; the same series at 1% publishes
    ok = _run_conservation(pairs, [v * 1.01 for _d, v in pairs])
    assert ok["gates_passed"] == ok["gates_total"], ok
    return "a rescaled derived series trips the residual gate and stops the run"


def case_a_shifted_series_fails_the_mae_and_correlation_gates():
    """A whole-series time shift leaves the annual total, and therefore the
    ratio, almost untouched. MAE and correlation are what catch it -- which is
    why the ratio alone was never a verification."""
    pairs = _reference_days(120)
    vals = [v for _d, v in pairs]
    shifted = vals[1:] + vals[:1]
    msg = _conservation_fails(pairs, shifted)
    assert "mae_kwh_per_day_vs_refA" in msg, msg
    assert "correlation_vs_refA" in msg, msg
    # the ratio gate did NOT catch it: the shift preserves the total
    assert "abs_residual_pct_vs_refA" not in msg, msg
    return "a day-shifted series trips the MAE and correlation gates, not the ratio"


def case_a_short_overlap_fails_the_day_count_gate():
    """A truncated or wrong-year reference pull shrinks the comparison while
    every ratio still looks respectable."""
    pairs = _reference_days(120)
    short = pairs[:30]
    msg = _conservation_fails(short, [v * 1.01 for _d, v in short],
                              extra=pairs[30:])
    assert "minimum_overlapping_days" in msg, msg
    assert "observed 30, must be >= 90" in msg, msg
    return "fewer overlapping days than the gate requires stops the run"


def case_the_conservation_reading_follows_the_gates():
    """The narrative is assembled from the outcomes. The inside-the-spread
    sentence appears only when the claim was tested AND held."""
    pairs = _reference_days(120)
    inside = _run_conservation(pairs, [v * 1.01 for _d, v in pairs])
    assert inside["claims_tested"][
        "derived_total_sits_inside_the_reference_spread"] is True, inside
    assert "sits inside the spread" in inside["reading"], inside["reading"]
    assert "All 7 conservation gates passed on 120 overlapping days" \
        in inside["reading"], inside["reading"]
    # the same gates pass with the derived total BELOW both references, and the
    # sentence changes rather than being repeated
    outside = _run_conservation(pairs, [v * 0.98 for _d, v in pairs])
    assert outside["gates_passed"] == outside["gates_total"], outside
    assert outside["claims_tested"][
        "derived_total_sits_inside_the_reference_spread"] is False, outside
    assert "OUTSIDE the spread" in outside["reading"], outside["reading"]
    assert "not the weakest link" not in outside["reading"], outside["reading"]
    return "the conservation reading is derived from the gate outcomes, not fixed text"


def case_the_enphase_peak_invariant_fails_closed():
    """Hourly averaging cannot manufacture a peak. Both comparisons are
    enforced, and the second one -- against the headline maximum every headroom
    figure rests on -- fails in the direction that would make the answer
    optimistic."""
    # the household's own figures, read from the artifact so they cannot go
    # stale: the consumption CT's hourly maximum, the headline 15-minute
    # maximum, and the top of the envelope
    d = json.loads(S.OUT.read_text())
    corr = d["maximum_demand"]["independent_corroboration"]
    sam_max = corr["max_hourly_mean_kw"]
    peak = d["maximum_demand"]["peak_kw"]
    top = d["gross_reconstruction"]["max_upper_bound_kw"]
    assert sam_max < peak < top, (sam_max, peak, top)
    ok = S.enphase_peak_invariant(sam_max, corr["at"], peak, top)
    assert [c["passed"] for c in ok["checks"]] == [True, True], ok
    assert _close(ok["margin_below_the_headline_peak_kw"], peak - sam_max, 1e-9), ok
    assert _close(ok["margin_below_the_envelope_top_kw"], top - sam_max, 1e-9), ok
    # above the headline peak but inside the envelope: the physics holds and
    # the publication precondition does not
    try:
        S.enphase_peak_invariant(peak + 2.0, corr["at"], peak, top)
        raise AssertionError("an hourly mean above the headline peak was published")
    except SystemExit as e:
        assert "enphase_hourly_max_within_the_headline_peak" in str(e), e
        assert "enphase_hourly_max_within_the_envelope" not in str(e), e
        assert "optimistic" in str(e), e
    # above the envelope too: the instruments are not describing the same house
    try:
        S.enphase_peak_invariant(top + 5.0, corr["at"], peak, top)
        raise AssertionError("an impossible hourly mean was published")
    except SystemExit as e:
        assert "enphase_hourly_max_within_the_envelope" in str(e), e
        assert "enphase_hourly_max_within_the_headline_peak" in str(e), e
    return "the Enphase peak corroboration is enforced on both legs, not asserted"


def case_only_match_refuses_zero_or_two_candidates():
    with tempfile.TemporaryDirectory() as td:
        old = S.RAW_DIR
        S.RAW_DIR = pathlib.Path(td)
        try:
            try:
                S.only_match("Electric_15_Minute_*.csv", "export")
                raise AssertionError("no candidate was accepted")
            except SystemExit as e:
                assert "found 0" in str(e), e
            (S.RAW_DIR / "Electric_15_Minute_a.csv").write_text("x")
            assert S.only_match("Electric_15_Minute_*.csv", "export").name \
                == "Electric_15_Minute_a.csv"
            (S.RAW_DIR / "Electric_15_Minute_b.csv").write_text("x")
            try:
                S.only_match("Electric_15_Minute_*.csv", "export")
                raise AssertionError("two candidates were accepted")
            except SystemExit as e:
                assert "found 2" in str(e), e
        finally:
            S.RAW_DIR = old
    return "the raw-export glob demands exactly one match instead of taking [0]"


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

def case_artifact_round_trips_byte_identically():
    raw = S.OUT.read_bytes()
    obj = json.loads(raw)
    again = (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode()
    assert again == raw, (
        "the committed artifact is not what its own serializer produces from "
        "its own contents -- regeneration cannot be byte-identical")
    return "data/service_headroom.json round-trips through its serializer byte for byte"


def case_artifact_is_internally_consistent():
    d = json.loads(S.OUT.read_text())
    for k in ("caveat", "provenance", "panel", "gross_reconstruction",
              "maximum_demand", "nec_220_87", "added_load_code_values",
              "cases", "noncoincident_loads", "battery_inverter", "mitigations"):
        assert k in d, f"artifact is missing {k}"
    nec, md, pan = d["nec_220_87"], d["maximum_demand"], d["panel"]
    assert _close(md["peak_a"], md["peak_kw"] * 1000.0 / 240.0, 1e-3), md
    assert _close(nec["calculated_load_a"], md["peak_a"] * 1.25, 1e-3), nec
    assert _close(nec["headroom_a"]["vs_service_rating"],
                  pan["service_rating_a"] - nec["calculated_load_a"], 1e-3), nec
    assert _close(nec["headroom_a"]["vs_meter_socket"],
                  pan["meter_socket_continuous_a"] - nec["calculated_load_a"], 1e-3)
    assert nec["measurement_days"] >= S.NEC_220_87_CONDITION_1_DAYS, nec
    assert [c["case"] for c in d["cases"]] == [
        "heat_pump_only", "second_evse_only", "heat_pump_and_second_evse",
        "heat_pump_second_evse_and_battery", "heat_pump_replaces_ac"], d["cases"]
    sens = nec["sensitivity_on_the_upper_bound"]
    for c in d["cases"]:
        rem = c["remaining_headroom_a"]
        exp = nec["headroom_a"]["vs_service_rating"] - c["fixed_added_load_a"]
        assert _close(rem["measured_basis"]["vs_service_rating"], exp, 1e-3), c
        exp_c = sens["headroom_vs_service_a"] - c["fixed_added_load_a"]
        assert _close(rem["conservative_basis"]["vs_service_rating"], exp_c, 1e-3), c
    assert len(d["mitigations"]) >= 2, d["mitigations"]
    for m in d["mitigations"]:
        assert "reduction_a" in m or "table" in m, m
    b = d["battery_inverter"]
    assert _close(b["shortfall_a"],
                  b["backfeed_breaker_a"]
                  - b["busbar_120_percent"]["remaining_backfeed_a"], 1e-3), b
    assert d["added_load_code_values"]["heat_pump_a"] is None, \
        "a heat-pump nameplate was invented"
    return "the committed artifact's own arithmetic checks out end to end"


def case_artifact_publishes_no_verdict_the_data_does_not_support():
    """No case may publish a bare pass that flips inside the artifact's own
    disclosed uncertainty, and none may publish a boolean a reader would quote
    in place of the three-valued answer."""
    txt = S.OUT.read_text()
    assert "passes_on_ampacity" not in txt, \
        "the boolean verdict is still in the artifact beside the three-valued one"
    d = json.loads(txt)
    for c in d["cases"]:
        v = c["ampacity_verdict"]
        assert v in ("pass", "fail", "not_determined"), c
        rem = c["remaining_headroom_a"]
        m, cons = rem["measured_basis"]["binding"], rem["conservative_basis"]["binding"]
        assert cons <= m, ("the conservative basis is not the tighter one", c)
        # heat_pump_replaces_ac's verdict is taken across the credit axis too
        # (see heat_pump_replacement_case()), so it need not equal the plain
        # two-basis formula every other case is held to; it is checked on its
        # own terms in case_heat_pump_replacement_case_* below.
        if c["case"] != "heat_pump_replaces_ac":
            assert v == S.ampacity_verdict(m, cons), c
        if v == "not_determined" and c["case"] != "heat_pump_replaces_ac":
            # the settler is DERIVED from the basis the binding interval took:
            # where that hour was never covered, 15-minute production data
            # settles nothing, and pointing a reader at it is pointing them at
            # the wrong instrument
            settle = c["what_would_settle_it"]
            assert settle, c
            binding = d["gross_reconstruction"]["max_upper_bound_binding_interval"]
            assert settle == S.what_would_settle_it(
                binding,
                d["gross_reconstruction"]["pv_ceiling_basis_split"][
                    "enphase_coverage_lag"]), settle
            assert binding["timestamp_local"] in settle, settle
            if binding["hour_has_a_production_measurement"]:
                assert "never metered here" in settle, settle
            else:
                assert "AT ANY RESOLUTION" in settle, settle
                assert "would not settle this case" in settle, settle
        elif v != "not_determined":
            assert c["what_would_settle_it"] is None, c
    # this household: a second 48 A EVSE is exactly the case that flips
    by_name = {c["case"]: c for c in d["cases"]}
    assert by_name["second_evse_only"]["ampacity_verdict"] == "not_determined"
    assert by_name["heat_pump_only"]["ampacity_verdict"] == "pass"
    assert by_name["heat_pump_second_evse_and_battery"]["ampacity_verdict"] == "fail"
    # this household's condenser nameplate is not in intake (issue #45): the
    # replacement case fails closed on the credit but still passes outright,
    # because it needs no credit to clear zero -- see AC_REPLACEMENT_SETTLE.
    hpr = by_name["heat_pump_replaces_ac"]
    assert hpr["ampacity_verdict"] == "pass", hpr
    assert hpr["what_would_settle_it"] is None, hpr
    assert hpr["existing_ac_nameplate_basis"] == S.AC_NAMEPLATE_NOT_RECORDED, hpr
    assert hpr["remaining_headroom_a"]["assuming_full_credit"]["measured_basis"] \
        is None, hpr
    return "every case verdict is three-valued and survives the disclosed uncertainty"


def case_artifact_states_both_legs_of_the_busbar_rule():
    d = json.loads(S.OUT.read_text())
    b = d["battery_inverter"]
    pos = b["busbar_120_percent"]["position_condition"]
    assert b["ampacity_leg"] in ("pass", "fail", "not_determined"), b
    assert b["ampacity_leg"] == S.busbar_ampacity_leg(
        b["backfeed_breaker_a"], b["busbar_120_percent"]["remaining_backfeed_a"],
        b["busbar_120_percent"]["existing_pv_backfeed_basis"]), b
    assert (b["ampacity_leg_what_would_settle_it"] is not None) == \
        (b["ampacity_leg"] == "not_determined"), b
    assert b["position_leg"] == pos["verdict"], b
    assert pos["verdict"] in ("pass", "fail", "not_determined"), pos
    assert "opposite end" in pos["requirement"], pos
    # a compliant verdict requires BOTH legs; nothing less may read as one
    if b["verdict"].startswith("fits"):
        assert b["ampacity_leg"] == "pass" and b["position_leg"] == "pass", b
    if b["ampacity_leg"] == "fail" or b["position_leg"] == "fail":
        assert b["verdict"] == "FAILS as the panel stands", b
    return "the artifact states both conjunctive legs of NEC 705.12(B)(3)(2)"


def case_artifact_bounds_pv_on_the_inverter_nameplate():
    d = json.loads(S.OUT.read_text())
    c = d["gross_reconstruction"]["pv_ac_ceiling"]
    assert "nameplate" in c["basis"], c
    assert "physical bound" in c["basis"], c
    assert _close(c["per_interval_ceiling_kwh"], c["ceiling_kw"] * 0.25, 1e-9), c
    obs = c["corroboration"]
    assert len(obs) == 3, obs
    for row in obs:
        assert row["exceeds_nameplate"] is False, row
        assert row["observed_kw"] < c["ceiling_kw"], row
    # the published ceiling is not the largest observation -- that substitution
    # is exactly what made the "upper" bound capable of sitting too low
    assert c["ceiling_kw"] > max(r["observed_kw"] for r in obs), c
    assert "not a per-interval ceiling" in c["why_not_the_observed_maximum"], c
    g = d["gross_reconstruction"]
    assert g["max_upper_bound_kw"] > g["max_lower_bound_kw"], g
    # the conservative basis the verdicts are computed on IS this envelope
    sens = d["nec_220_87"]["sensitivity_on_the_upper_bound"]
    assert _close(sens["max_upper_bound_kw"], g["max_upper_bound_kw"]), sens
    assert _close(sens["calculated_load_a"],
                  g["max_upper_bound_kw"] * 1000.0 / 240.0 * 1.25, 1e-3), sens
    return "the artifact's PV ceiling is the inverter nameplate and the envelope uses it"


def case_artifact_gates_the_conservation_check():
    """AC-1 asks for conservation VERIFIED. The artifact must carry the checks
    that could have failed, not a sentence that is printed either way."""
    d = json.loads(S.OUT.read_text())
    c = d["gross_reconstruction"]["conservation"]
    assert c["gates_total"] >= 7, c
    assert c["gates_passed"] == c["gates_total"], c
    names = [g["gate"] for g in c["gates"]]
    assert "minimum_overlapping_days" in names, names
    for stem in ("abs_residual_pct_vs_", "mae_kwh_per_day_vs_",
                 "correlation_vs_"):
        assert any(nm.startswith(stem) for nm in names), (stem, names)
    for g in c["gates"]:
        assert g["passed"] is True, g
        assert g["threshold"] is not None and g["requirement"] and g["catches"], g
        if g["comparison"] == "<=":
            assert g["observed"] <= g["threshold"], g
        else:
            assert g["observed"] >= g["threshold"], g
    # the reading is assembled from those outcomes
    assert f"All {c['gates_total']} conservation gates passed" in c["reading"], c
    claims = c["claims_tested"]
    inside = claims["derived_total_sits_inside_the_reference_spread"]
    lo, hi = claims["reference_spread_kwh"]
    assert inside == (lo <= c["derived_total_kwh"] <= hi), (claims, c)
    assert ("sits inside the spread" in c["reading"]) == inside, c["reading"]
    # and the peak corroboration is enforced the same way
    corr = d["maximum_demand"]["independent_corroboration"]
    assert len(corr["checks"]) == 2, corr
    for chk in corr["checks"]:
        assert chk["passed"] is True, chk
        assert corr["max_hourly_mean_kw"] <= chk["threshold"] + 1e-9, chk
    assert _close(corr["margin_below_the_headline_peak_kw"],
                  d["maximum_demand"]["peak_kw"] - corr["max_hourly_mean_kw"],
                  1e-3), corr
    return "the artifact publishes conservation gates that could have failed, and did not"


def case_artifact_battery_position_is_not_the_pv_breakers():
    d = json.loads(S.OUT.read_text())
    pan, b = d["panel"], d["battery_inverter"]
    pos = b["busbar_120_percent"]["position_condition"]
    assert pos["source"] == S.SOURCE_PROPOSED_BATTERY, pos
    assert pos["source_breaker_position"] == pan["battery_breaker_position"], pos
    assert b["position_leg"] == pos["verdict"], b
    # this household records the PV breaker's end and has surveyed no position
    # for a new one, so the battery leg is not determined ON THAT GROUND
    assert pan["pv_breaker_position"] is not None, pan
    assert pan["battery_breaker_position"] is None, pan
    assert pos["verdict"] == "not_determined", pos
    assert pos["source_breaker_position"] != pan["pv_breaker_position"], pos
    assert "SURVEYED" in pos["what_would_settle_it"], pos
    # the existing breaker's own condition is still reported, labelled as such
    ex = pan["existing_pv_position_condition"]
    assert ex["source"] == S.SOURCE_EXISTING_PV, ex
    assert ex["source_breaker_position"] == pan["pv_breaker_position"], ex
    assert "not the battery's position leg" in \
        pan["existing_pv_position_condition_note"], pan
    # the bottom line does not move: the ampacity leg fails on its own
    assert b["ampacity_leg"] == "fail", b
    assert b["verdict"] == "FAILS as the panel stands", b
    return "the battery's position leg comes from its own field, and the verdict still fails"


def case_artifact_labels_the_nullable_panel_fields():
    d = json.loads(S.OUT.read_text())
    pan = d["panel"]
    basis = pan["existing_pv_backfeed_basis"]
    assert basis in S.BACKFEED_NOTE, pan
    assert pan["existing_pv_backfeed_note"] == S.BACKFEED_NOTE[basis], pan
    assert basis == S.BACKFEED_READ or _close(pan["existing_pv_backfeed_a"], 0.0), pan
    # the two zeros are told apart in the artifact's own words, not inferred
    if basis == S.BACKFEED_SURVEYED_NONE:
        assert "surveyed" in pan["existing_pv_backfeed_note"], pan
        assert d["battery_inverter"]["ampacity_leg"] != "not_determined", d
    if basis == S.BACKFEED_NOT_RECORDED:
        assert "ABSENT" in pan["existing_pv_backfeed_note"], pan
    socket = pan["meter_socket_basis"]
    nec = d["nec_220_87"]
    assert socket in S.SOCKET_CONSTRAINT, pan
    assert pan["meter_socket_constraint"] == S.SOCKET_CONSTRAINT[socket], pan
    assert nec["headroom_a"]["binding_is"] == S.BINDING_IS[socket], nec
    assert (pan["meter_socket_what_would_settle_it"] is not None) == \
        (socket == S.SOCKET_NOT_RECORDED), pan
    if socket == S.SOCKET_READ:
        assert pan["meter_socket_continuous_a"] is not None, pan
        assert [s["step"] for s in nec["steps"]] == [1, 2, 3, 4], nec["steps"]
        assert nec["steps"][3]["result_a"] is not None, nec["steps"]
        assert nec["headroom_a"]["vs_meter_socket"] is not None, nec
    else:
        assert pan["meter_socket_continuous_a"] is None, pan
        assert nec["headroom_a"]["vs_meter_socket"] is None, nec
    if socket == S.SOCKET_SURVEYED_NONE:
        # the only state in which the constraint may be dropped outright
        assert "does not apply" in pan["meter_socket_constraint"], pan
        assert [s["step"] for s in nec["steps"]] == [1, 2, 3], nec["steps"]
    if socket == S.SOCKET_NOT_RECORDED:
        # dropped step 4 is what made the omission invisible; it stays, as a
        # not_determined row, and every binding figure is an upper limit
        assert [s["step"] for s in nec["steps"]] == [1, 2, 3, 4], nec["steps"]
        assert nec["steps"][3]["verdict"] == "not_determined", nec["steps"]
        assert "UPPER LIMIT" in nec["headroom_a"]["binding_is"], nec
        for c in d["cases"]:
            for b in ("measured_basis", "conservative_basis"):
                assert "UPPER LIMIT" in \
                    c["remaining_headroom_a"][b]["binding_is"], c
    return "the artifact says what each nullable panel field actually records"


JUDGEMENT_WORDS = ("fits", "passes", "compliant", "verified", "sufficient",
                   "supported", "ok", "valid", "allowed", "safe")

# Sentences that assert a constraint does not apply, a source is absent, or a
# figure is zero because the answer was "none". Each is a positive claim about
# the world and may only be published on the strength of an answer somebody
# actually gave.
NON_APPLICABILITY_PHRASES = ("does not apply", "no continuous rating is recorded",
                             "nothing backfeeds it", "is a known, complete answer")

# The intake fields with THREE states, and the (claim field, basis field,
# vocabulary) each publishes. Two review passes found the same defect at two of
# these; the rule is stated once here so a third cannot be introduced quietly:
#
#   * exactly three states, with distinct sentences;
#   * the SURVEYED-absence state is the only one entitled to say the thing is
#     absent or does not apply;
#   * the NOT-RECORDED state may say none of that, and must say it is open;
#   * wherever the claim appears in the artifact, its basis appears beside it
#     and the sentence is the vocabulary's, not a paraphrase that could drift.
THREE_STATE_FIELDS = [
    ("existing_pv_backfeed_note", "existing_pv_backfeed_basis", S.BACKFEED_NOTE,
     S.BACKFEED_READ, S.BACKFEED_SURVEYED_NONE, S.BACKFEED_NOT_RECORDED),
    ("meter_socket_constraint", "meter_socket_basis", S.SOCKET_CONSTRAINT,
     S.SOCKET_READ, S.SOCKET_SURVEYED_NONE, S.SOCKET_NOT_RECORDED),
]

# Every boolean the artifact publishes, and why a bare true/false is honest
# there. Each one is a DIRECT, COMPLETE restatement of something measured or of
# a comparison whose two sides are printed beside it -- never a judgement whose
# evidence is somewhere else or absent. A new boolean fails this test until it
# is either justified here or made three-valued.
ALLOWED_BOOLEANS = {
    "passed":
        "restates a gate's own comparison, whose observed value, comparison and "
        "threshold are printed in the same object -- and a false one halts the "
        "run, so it can never be published",
    "exceeds_nameplate":
        "restates observed_kw > ceiling_kw, both printed beside it; a true one "
        "halts the run",
    "point_determined":
        "restates that an interval's upper and lower bounds coincide, which is "
        "a fact about that interval",
    "naive_max_is_a_dst_artifact":
        "restates that the naive maximum's own date is one of the DST days "
        "listed in the same artifact",
    "derived_total_sits_inside_the_reference_spread":
        "restates a comparison of three totals printed beside it",
    "each_reference_is_closer_to_the_reconstruction_than_to_the_other":
        "restates a comparison of residuals printed beside it",
    "annual_peak_falls_in_the_cooling_hours":
        "restates that the published peak hour lies in the published window",
    "hour_has_a_production_measurement":
        "restates which PV ceiling the binding interval took, a fact about "
        "that interval, with the basis token and the uncovered reason printed "
        "in the same object",
    "max_upper_bound_is_set_by_an_hour_with_no_production_measurement":
        "the same restatement, mirrored where a reader meets the conservative "
        "basis: it is the negation of the binding interval's own "
        "hour_has_a_production_measurement, published beside it",
    "available_to_this_service":
        "restates that this service has a renewable energy system -- the "
        "photovoltaic nameplate is quoted in the sentence beside it -- against "
        "the Exception's own text, which is printed in the same object. Both "
        "sides of the comparison are on the page",
    "rating_is_at_or_above_the_code_figure":
        "restates ocpd_rating_a >= code_figure_125pct_of_output_a, both "
        "printed beside it along with their difference",
    "every_rating_is_at_or_above_the_code_figure":
        "restates the conjunction of the per-source booleans above, each "
        "printed with its two sides; it is null, not true, where there is no "
        "source to compare",
    "scored":
        "heat_pump_replaces_ac only (issue #45): false restates that "
        "existing_ac_ocpd_basis (printed in the case's own 'reason', with "
        "the schedule reading beside it) did not resolve to AC_READ, so "
        "there is no established circuit to reuse -- the case then carries "
        "none of the ampacity or spaces claims a guess would need, rather "
        "than a three-valued verdict with nothing behind it",
    "capped_by_existing_branch_circuit":
        "heat_pump_replaces_ac only (issue #45 review): restates binding > "
        "existing_ac_ocpd_a before the cap was applied, both of which are "
        "printed beside it (the pre-cap panel-level figure is recoverable "
        "from vs_service_rating/vs_meter_socket, and existing_ac_ocpd_a "
        "sits at the case's own top level) -- true means the published "
        "binding is the breaker rating, not the panel's own headroom",
}


def case_artifact_publishes_no_bare_boolean_judgement():
    """The class, not the instance. Four review rounds found the same defect at
    four exits -- a boolean or a two-valued verdict published where the data
    settles only one direction. This walks every leaf in the artifact: booleans
    must be on the justified allowlist above, and anything named like a
    judgement must be three-valued."""
    d = json.loads(S.OUT.read_text())
    bools, verdicts, dicts = {}, {}, []

    def walk(o, path):
        if isinstance(o, dict):
            dicts.append(o)
            for k, v in o.items():
                walk(v, path + [k])
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + [f"[{i}]"])
        elif isinstance(o, bool):
            bools.setdefault(path[-1], []).append(".".join(path))
        elif isinstance(o, str) and (
                path[-1].endswith("verdict") or path[-1].endswith("_leg")
                or path[-1] == "physical_fit"):
            verdicts.setdefault(".".join(path), o)

    walk(d, [])
    unjustified = sorted(set(bools) - set(ALLOWED_BOOLEANS))
    assert not unjustified, (
        f"boolean judgement(s) with no justification: {unjustified}. Either "
        f"make them three-valued with a what_would_settle_it, or add them to "
        f"ALLOWED_BOOLEANS with the reason the data settles them completely")
    # no boolean may be NAMED like a conclusion a reader would quote
    for leaf in bools:
        low = leaf.lower()
        assert not any(w in low.split("_") for w in JUDGEMENT_WORDS), \
            f"{leaf} is a boolean named like a verdict"
    # the three-valued vocabulary is one vocabulary
    assert verdicts, "no verdict fields found -- the walk is not finding them"
    for path, v in verdicts.items():
        assert v in ("pass", "fail", "not_determined") or v.isupper() or \
            v.startswith(("fits", "FAILS", "NOT DETERMINED")), (path, v)
    # and every field that reports a not_determined names what would settle it
    for path, v in verdicts.items():
        if v != "not_determined":
            continue
        owner = d
        for key in path.split(".")[:-1]:
            owner = owner[int(key.strip("[]"))] if key.startswith("[") else owner[key]
        settle = [val for k, val in owner.items()
                  if "what_would_settle_it" in k and val]
        assert settle, f"{path} is not_determined and names nothing that would settle it"

    # The companion rule. A claim that something is absent, zero or
    # inapplicable must trace to an answer that was GIVEN, never to a key
    # nobody filled in -- the defect that published a battery leg as
    # undecidable on a fully surveyed panel, and deleted the tighter of two
    # ampacity constraints on a panel nobody had looked at.
    for claim, basis_key, vocab, read, none, unasked in THREE_STATE_FIELDS:
        assert len(vocab) == 3, (claim, vocab)
        assert len({vocab[read], vocab[none], vocab[unasked]}) == 3, \
            f"{claim}: two of the three states publish the same sentence"
        assert any(ph in vocab[none].lower() for ph in NON_APPLICABILITY_PHRASES), \
            (f"{claim}: the surveyed-absence state is the one entitled to say "
             f"the thing is absent, and it does not say it")
        for state in (read, unasked):
            said = [ph for ph in NON_APPLICABILITY_PHRASES
                    if ph in vocab[state].lower()]
            assert not said, (
                f"{claim}[{state}] claims {said} -- only the surveyed-absence "
                f"state may, and {unasked!r} means nobody looked")
        low = vocab[unasked].lower()
        assert ("not determined" in low or "never been looked at" in low
                or "nobody has answered" in low), (
            f"{claim}[{unasked}] does not say the question is still open")
        # and wherever it is published, the basis is published beside it
        found = 0
        for owner in dicts:
            if claim not in owner:
                continue
            found += 1
            assert basis_key in owner, \
                f"{claim} published with no {basis_key} beside it"
            assert owner[basis_key] in vocab, (claim, owner[basis_key])
            assert owner[claim] == vocab[owner[basis_key]], (
                f"{claim} has drifted from the sentence its basis declares")
        assert found, f"{claim} is not in the artifact -- the walk is stale"
    return "no bare boolean or two-valued judgement survives in the artifact"


# Every optional intake read in service_headroom.py that does NOT ask whether
# its key was recorded, and what makes an absent key and an explicit null the
# same answer there. Anything not on this list must pair its read with
# _key_present(), because household.get() returns None for both and the two
# mean opposite things. A new optional field fails the case below until its
# author has either distinguished them or written down why they cannot differ.
OPTIONAL_READS_WITHOUT_A_PRESENCE_TEST = {
    "household.has_new_load_interest": (
        "the flag's contract is that only an explicit false disables the "
        "analysis; absent and null are both 'not answered' and both leave the "
        "analysis running with its fail-closed panel requirements intact, "
        "which is the conservative direction"),
    "solar.kw_ac": (
        "absent and null both stop the run with PV_CEILING_MISSING -- there is "
        "no path on which either becomes a published figure"),
    "solar.inverter_model": (
        "prose only: it names the hardware inside pv_ac_ceiling's basis "
        "sentence and no verdict, bound or arithmetic reads it"),
    "solar.inverter_count": (
        "prose only, as above -- it appears beside the model in the same "
        "sentence and nothing is computed from it"),
    "panel.pv_breaker_position": (
        "position_condition() maps absent and unreadable alike to "
        "not_determined with what would settle it; there is no state in which "
        "a missing answer becomes a pass, so the two collapse safely"),
    "panel.battery_breaker_position": (
        "same as pv_breaker_position, and this one has no surveyed value at "
        "all in any intake yet: absent is the only state it has"),
    "panel.main_breaker_position": (
        "same as pv_breaker_position -- with no main end recorded the "
        "'opposite end' test cannot be evaluated and reports not_determined"),
    "household.has_ev": (
        "the same contract the new-load flag carries: only an explicit false "
        "switches the EVSE scenarios off, and absent and null both mean 'not "
        "answered', which leaves charger.kw required exactly as it was -- the "
        "conservative direction, since a household that has an EV and never "
        "set the flag keeps the fail-closed read"),
    "solar.kw_dc": (
        "one of solar_present()'s five presence probes, deliberately NOT read "
        "through _key_present(): has_solar (DATA-SOURCES-CHEATSHEET.md) is "
        "answered by the shape of the file, not by a boolean, so there is no "
        "surveyed-null state for 'this household was asked and has no solar' "
        "to occupy -- absent and null both mean the same thing here, unlike "
        "the panel fields where a null is itself an answer"),
    "solar.module_count": (
        "same as solar.kw_dc, and the same reasoning covers the other three "
        "presence probes (solar.kw_ac, solar.inverter_model, "
        "solar.inverter_count) exempted separately above for their own, "
        "narrower reasons predating issue #42"),
}


def _module_string_constants(tree):
    """Module-level NAME = "literal" bindings, so a read passed a constant is
    still seen as the path it resolves to."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def case_every_optional_intake_read_distinguishes_absent_from_null():
    """The class, at the source rather than in one artifact. This household
    records both nullable panel fields, so no artifact of its own could catch a
    third field conflating an unanswered question with an answer of 'none'.
    The reads themselves are audited instead: every optional read either asks
    _key_present() or is declared above with the reason the two cannot differ."""
    src = pathlib.Path(S.__file__).with_suffix(".py").read_text()
    tree = ast.parse(src)
    consts = _module_string_constants(tree)

    def paths(call):
        out = []
        for a in call.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append(a.value)
            elif isinstance(a, ast.Name) and a.id in consts:
                out.append(consts[a.id])
        return out

    optional, presence = {}, set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name == "get" and isinstance(f, ast.Attribute) and \
                getattr(f.value, "id", "") == "HH":
            req = [k for k in n.keywords if k.arg == "required"]
            if req and isinstance(req[0].value, ast.Constant) and \
                    req[0].value.value is False:
                for p in paths(n):
                    optional[p] = "HH.get(required=False)"
        elif name in ("_optional_number", "_flag"):
            for p in paths(n):
                optional[p] = name
        elif name == "_key_present":
            presence.update(paths(n))

    assert optional, "the read audit found nothing -- it has gone stale"
    unhandled = sorted(p for p in optional
                       if p not in presence
                       and p not in OPTIONAL_READS_WITHOUT_A_PRESENCE_TEST)
    assert not unhandled, (
        f"optional intake read(s) that cannot tell an absent key from an "
        f"explicit null: {unhandled}. household.get() returns None for both. "
        f"Either pair the read with _key_present() and publish which state it "
        f"is in, or declare it in OPTIONAL_READS_WITHOUT_A_PRESENCE_TEST with "
        f"the reason the two cannot mean different things there")
    # the declared exemptions stay honest: still read, still not distinguished
    for p, reason in OPTIONAL_READS_WITHOUT_A_PRESENCE_TEST.items():
        assert p in optional, f"{p} is declared exempt but is no longer read"
        assert p not in presence, \
            f"{p} now has a presence test -- drop its exemption"
        assert len(reason) > 40, f"{p}'s exemption has no real reason behind it"
    # the three that DO distinguish are the three with three published states
    # (issue #45 added the condenser nameplate to the other two)
    assert presence == {"panel.pv_backfeed_a", "panel.meter_socket_continuous_a",
                        "panel.existing_ac_nameplate_rla_a"}, presence
    return f"all {len(optional)} optional intake reads handle absent vs null explicitly"


def case_artifact_reports_physical_fit_not_a_boolean():
    """Finding C's exit: `fits_without_panel_work` claimed adjacency the panel
    schedule cannot establish. This household is short on the COUNT, which is
    determinable, so the answer is a fail either way -- but it has to be a fail
    for the reason the data supports. heat_pump_replaces_ac (issue #45) is the
    one exception BY DESIGN: it needs zero new spaces, so the same 1-free-space
    panel that fails every ADD case does not block it."""
    txt = S.OUT.read_text()
    assert "fits_without_panel_work" not in txt, \
        "the adjacency boolean is still in the artifact"
    d = json.loads(txt)
    occ = d["panel"]["occupancy"]
    for c in d["cases"]:
        sp = c["spaces"]
        assert sp["physical_fit"] in ("pass", "fail", "not_determined"), c
        assert sp["adjacent_free_pairs"] is None, sp
        assert sp["physical_fit"] == S.physical_fit(
            sp["new_2pole_breakers_required"], sp["spaces_free"],
            sp["adjacent_free_pairs"]), c
        assert sp["spaces_free"] == occ["spaces_free"], c
        if c["case"] == "heat_pump_replaces_ac":
            # reuses the existing circuit: zero new spaces, never blocked by
            # the count that fails every ADD case on this panel
            assert sp["new_2pole_breakers_required"] == 0, sp
            assert sp["full_size_spaces_required"] == 0, sp
            assert sp["physical_fit"] == "pass", sp
            assert "REUSES the existing" in sp["note"], sp
        else:
            # this household: 1 free space, every ADD case wants at least two
            assert sp["full_size_spaces_required"] > sp["spaces_free"], c
            assert sp["physical_fit"] == "fail", c
            assert sp["what_would_settle_it"] is None, sp
            assert "adjacency is not in the data" in sp["physical_fit_basis"], sp
    return "physical fit is three-valued in the artifact and fails on the count here"


def case_artifact_states_the_battery_charging_basis_from_the_datasheet():
    """Issue #40. Tesla's own official 2025 Powerwall 3 Datasheet gives
    DIFFERENT continuous power ratings for charge (5 kW, single unit, no
    expansions) and discharge (11.5 kW). The demand-side grid-charging load is
    computed from the CHARGE figure, cited, not borrowed from the discharge
    rating and not carried as an open assumption -- the prior 'not determined'
    framing is retired because the datum now exists."""
    d = json.loads(S.OUT.read_text())
    v = d["added_load_code_values"]
    basis = v["battery_charging_basis"]
    assert "battery-research-notes" in basis, basis
    assert "5.0 kW" in basis or "5 kW" in basis, basis
    assert "Maximum Continuous Charge Power" in basis, basis
    assert "11.5 kW" in basis, basis  # cites the discharge figure for contrast
    # the old uncited-assumption framing must not come back
    assert "assumption" not in basis, basis
    assert "conservative" not in basis, basis
    # the field that used to carry the open question is gone: it is settled
    assert "battery_charging_not_determined" not in v, v
    # the two directions are distinct constants, not one number serving twice
    assert not _close(S.BATTERY_CHARGE_KW, S.BATTERY_DISCHARGE_KW)
    assert _close(S.BATTERY_CHARGE_KW, 5.0)
    assert _close(S.BATTERY_DISCHARGE_KW, 11.5)
    # the demand-side amps now come from the CHARGE constant, not discharge
    assert _close(v["battery_charging_a"],
                  _rq(S.amps(S.BATTERY_CHARGE_KW) * S.NEC_625_42_FACTOR, 2)), v
    assert not _close(v["battery_charging_a"],
                       _rq(S.amps(S.BATTERY_DISCHARGE_KW) * S.NEC_625_42_FACTOR, 2))
    # and the case that carries it repeats the same cited basis
    batt_case = [c for c in d["cases"] if "battery" in c["case"]][0]
    assert "Maximum Continuous Charge Power" in batt_case["note"], batt_case["note"]
    # the busbar leg is what fails, and it still does -- this fix is a demand-
    # side correction and does not touch the independent busbar-leg failure
    assert d["battery_inverter"]["verdict"] == "FAILS as the panel stands"
    assert d["battery_inverter"]["ampacity_leg"] == "fail"
    # discharge-direction figures (breaker sizing, busbar source current)
    # still use the discharge rating, unchanged by this fix
    assert _close(d["battery_inverter"]["discharge_kw"], 11.5)
    assert _close(d["battery_inverter"]["charge_kw"], 5.0)
    assert _close(d["battery_inverter"]["continuous_output_a"],
                  _rq(S.amps(S.BATTERY_DISCHARGE_KW), 2))
    return "the battery charging basis is a cited datasheet figure, not an assumption"


def case_the_load_sharing_mitigation_separates_the_amps_from_the_breaker():
    """The mitigation makes two claims and only one of them is citable.

    The AMPS are code: NEC 625.42 sizes a load-management system on the
    system's maximum output, so the added demand is 0 A rather than 60 A. That
    stands on a citation and stays.

    The BREAKER is hardware: whether two connectors in a power-sharing group may
    share ONE branch circuit, or whether each still needs its own, is a
    manufacturer installation fact. Nothing in research/, TECHNICAL.md or the
    intake records it, so it is published as not determined, with what would
    settle it and the physical-fit consequence stated BOTH ways -- the same
    treatment battery_charging_not_determined gets.
    """
    d = json.loads(S.OUT.read_text())
    m = [x for x in d["mitigations"] if x["mitigation"] == "EVSE load sharing"]
    assert len(m) == 1, [x["mitigation"] for x in d["mitigations"]]
    m = m[0]
    # the amps claim: determined, cited, and still the mitigation's content
    assert "625.42" in m["basis"], m
    assert "MAXIMUM OUTPUT" in m["basis"], m
    assert _close(m["added_load_with_a"], 0.0), m
    assert _close(m["added_load_without_a"],
                  _rq(S.evse_code_load_a(S.EXISTING_EVSE_OUTPUT_A), 4)), m
    assert _close(m["reduction_a"], m["added_load_without_a"]), m
    assert "625.42" in m["what_is_determined_here"], m
    assert "does not depend on how the connectors are wired" in \
        m["what_is_determined_here"], m
    # the breaker claim: not determined, in the shape the battery basis uses
    assert m["shares_the_existing_branch_circuit"] is None, m
    nd = m["shares_the_existing_branch_circuit_not_determined"]
    assert nd.startswith("NOT DETERMINED"), nd
    assert "manufacturer installation fact" in nd, nd
    assert "installation instructions" in nd and "AHJ" in nd, nd
    assert "either way" in nd, nd
    # ... and the consequence is given for BOTH answers, not just the convenient one
    shares = m["physical_fit_if_it_shares_the_existing_circuit"]
    own = m["physical_fit_if_it_needs_its_own_circuit"]
    assert "No new breaker and no new space" in shares, shares
    assert "two ADJACENT full-size spaces" in own, own
    free = d["panel"]["occupancy"]["spaces_free"]
    assert str(free) in shares and str(free) in own, (free, shares, own)
    assert ("short of two" in own) is (free < 2), (free, own)
    return "the load-sharing mitigation determines the amps and not the breaker"


# The retired assertion, in every phrasing it was published in. It claimed a
# manufacturer installation fact this project cannot cite, and it appeared at
# more than one site before it was removed -- so the scan is over the WHOLE
# artifact and the whole module, not over the one field it was found in.
UNCITABLE_SHARING_PHRASES = (
    "needs no new breaker",
    "It needs no new breaker",
    "no new breaker, which matters more than the amps",
    "share power across one circuit",
    "on the existing circuit",
)


def case_no_uncitable_breaker_claim_survives_anywhere():
    d = S.OUT.read_text()
    src = (pathlib.Path(S.__file__)).read_text()
    for phrase in UNCITABLE_SHARING_PHRASES:
        assert phrase not in d, f"artifact still asserts: {phrase!r}"
        assert phrase not in src, f"service_headroom.py still asserts: {phrase!r}"
    # and the mitigation's name no longer smuggles the claim in either
    for x in json.loads(d)["mitigations"]:
        assert "existing circuit" not in x["mitigation"], x["mitigation"]
    # the ONE place the phrase may appear is inside an explicit both-ways
    # consequence, which is conditional by construction
    obj = json.loads(d)
    shares = [x for x in obj["mitigations"]
              if x["mitigation"] == "EVSE load sharing"][0][
                  "physical_fit_if_it_shares_the_existing_circuit"]
    assert "No new breaker" in shares, shares
    return "the uncitable 'no new breaker' claim appears nowhere in artifact or module"


def case_artifact_publishes_the_pv_ceiling_basis_split():
    """How much of the envelope is measurement-narrowed and how much is the bare
    physical cap, stated rather than implied -- the uncovered intervals are the
    loosest part of the record and a reader is entitled to know how many."""
    d = json.loads(S.OUT.read_text())
    g = d["gross_reconstruction"]
    sp = g["pv_ceiling_basis_split"]
    assert sp["measured_hour_intervals"] + sp["nameplate_intervals"] == \
        g["intervals"], sp
    assert sum(sp["nameplate_intervals_by_reason"].values()) == \
        sp["nameplate_intervals"], sp
    assert _close(sp["measured_hour_pct"],
                  100.0 * sp["measured_hour_intervals"] / g["intervals"], 1e-3)
    assert _close(sp["nameplate_pct"],
                  100.0 * sp["nameplate_intervals"] / g["intervals"], 1e-3)
    # this window: both named sources of uncovered hours are present
    by = sp["nameplate_intervals_by_reason"]
    assert by["excluded_dst_day"] > 0, by
    assert by["after_the_last_hour_the_enphase_files_measured"] > 0, by
    # the DST days carry every interval of both transition Sundays
    assert by["excluded_dst_day"] == sum(
        len(R.expected_day_hours(dt.date.fromisoformat(s)))
        for s in d["provenance"]["dst_days"]), by
    # nothing empirical narrows them, and the artifact says why
    why = sp["why_not_the_empirical_hour_of_day_maximum"]
    assert "not a ceiling on it" in why, why
    assert "not an upper bound" in why, why
    assert "optimistically" in why, why
    assert "pv_ceiling_basis_split" in g["honesty"], g["honesty"]
    return "the artifact counts each PV ceiling basis and names why the loose one is loose"


def case_artifact_sum_rule_counts_the_proposed_breaker():
    d = json.loads(S.OUT.read_text())
    sr = d["battery_inverter"]["sum_rule"]
    occ = d["panel"]["occupancy"]
    assert "passes" not in sr, "the sum rule still publishes a bare boolean"
    assert sr["verdict"] in ("fail", "not_determined"), sr
    assert _close(sr["branch_ocpd_sum_a"], occ["branch_ocpd_sum_a"]), sr
    assert _close(sr["proposed_battery_breaker_a"],
                  d["battery_inverter"]["backfeed_breaker_a"]), sr
    assert _close(sr["counted_sum_a"],
                  sr["branch_ocpd_sum_a"] + sr["proposed_battery_breaker_a"]), sr
    assert sr["verdict"] == ("fail" if sr["counted_sum_a"] > sr["busbar_rating_a"]
                             else "not_determined"), sr
    # this panel: 460 A of branch devices plus a 60 A source breaker on a 200 A
    # bus, so the sum rule is not an escape route from the 120% failure
    assert sr["verdict"] == "fail", sr
    return "the sum-of-breakers rule counts the proposed breaker and is not a boolean"


def case_artifact_carries_the_scoping_caveat():
    d = json.loads(S.OUT.read_text())
    low = d["caveat"].lower()
    assert "licensed electrician" in low and "220.87" in d["caveat"], d["caveat"]
    assert "authority having jurisdiction" in low, d["caveat"]
    assert "licensed electrician" in S.__doc__.lower(), "docstring caveat missing"
    assert "SCOPING ESTIMATE" in S.__doc__, "docstring caveat missing"
    return "the scoping caveat is in both the artifact and the module docstring"


def case_artifact_carries_no_identifiers():
    """The Green Button header holds the customer name, service address, account
    and meter numbers. None of it may reach a committed artifact."""
    txt = S.OUT.read_text()
    for needle in ("Meter Number", "Account", "/Users/", "@"):
        assert needle not in txt, f"artifact contains {needle!r}"
    import re
    bad = []
    for run in re.findall(r"\d{8,}", txt):
        # The export's own filename carries a YYYYMMDD pull date, which is
        # provenance worth keeping. Anything else eight digits long is not.
        try:
            dt.datetime.strptime(run, "%Y%m%d")
        except ValueError:
            bad.append(run)
    assert not bad, f"artifact contains long digit runs: {bad[:5]}"
    return "the artifact carries no account, meter, path or long-digit identifier"


# ---------------------------------------------------------------------------
# Intake privacy tiers vs the committed artifact
#
# CLAUDE.md §4: no private-only or secret intake answer may appear in any
# committed artifact. The check below is mechanical -- it reads the tiers out of
# DATA-SOURCES-CHEATSHEET.md and the values out of private/household.yaml, so a
# field re-tiered tomorrow is checked tomorrow with no test edit. Nothing here
# hardcodes a value from this household.
#
# The scan itself now lives in privacy_tiers.py, because one artifact was never
# the right scope: the value that prompted the rule reached index.html and
# TECHNICAL.md too, and neither has a generator. test_privacy_tiers.py runs the
# same needles across every tracked file. What stays here is this artifact's
# own case -- the file this suite owns, checked where the rest of it is checked.
# ---------------------------------------------------------------------------

CHEATSHEET = PT.CHEATSHEET
REAL_HOUSEHOLD = PT.REAL_HOUSEHOLD
TIERS = PT.TIERS


def case_the_cheatsheet_tiers_every_field_it_declares():
    """Requirement, not decoration: the leak scan below reads its universe of
    private values out of these blocks, so a block that fails to parse or
    forgets its privacy tag would shrink that universe silently."""
    fields = PT.cheatsheet_fields()
    tiers = {t: sum(1 for f in fields if f["privacy"] == t) for t in TIERS}
    assert sum(tiers.values()) == len(fields), tiers
    panel = [f for f in fields if f["id"].startswith("panel_")]
    assert panel, "the panel intake block has gone from the cheatsheet"
    for f in panel:
        assert "privacy_note" in f and len(f["privacy_note"]) > 40, (
            f"{f['id']} carries no privacy_note -- this section is tiered field "
            f"by field, and each field has to say why it is where it is")
    # every field either resolves to a household.yaml path or is declared to
    # store no value; an id whose subject cannot be located is a broken rule
    report = PT.resolution_report(fields=fields)
    assert not report["unresolvable"], (
        f"intake id(s) that resolve to no household.yaml path: "
        f"{report['unresolvable']} -- give them a row in YAML_PATH_OVERRIDES "
        f"or declare them in PATHLESS_FIELDS")
    return (f"all {len(fields)} intake fields parse and carry a tier "
            f"({tiers['public-ok']} public-ok, {tiers['private-only']} "
            f"private-only, {tiers['secret']} secret)")


def case_no_private_only_intake_value_reaches_the_artifact():
    """CLAUDE.md §4, checked instead of asserted. The tiers come from the
    cheatsheet and the values from the real intake, so this follows a re-tier
    without being edited.

    It is the case that would have caught the meter class: a private-only
    answer, quoted inside a provenance sentence, invisible to a key-by-key
    review of the artifact.
    """
    import yaml
    if not REAL_HOUSEHOLD.is_file():
        raise SkipCase("the private-only leak scan needs "
                       "private/household.yaml (gitignored) -- it has no "
                       "values to look for without it")
    household = yaml.safe_load(REAL_HOUSEHOLD.read_text()) or {}
    fields = PT.cheatsheet_fields()
    needles = PT.needles(household, fields)
    assert needles, (
        "no private-only value resolved out of private/household.yaml -- the "
        "scan has nothing to look for, which is a broken scan, not a clean bill")

    # The universe is reported, not asserted. Its completeness -- that no id
    # resolves to nothing -- is test_privacy_tiers.py's business; what matters
    # here is that this artifact was scanned against a universe of a stated
    # size, so "clean" can be read against how much was looked for.
    report = PT.resolution_report(household, fields)
    assert not report["unresolvable"], report["unresolvable"]

    text = S.OUT.read_text()
    hits = PT.leaks(needles, text, json.loads(text))
    assert not hits, (
        "private-only intake value(s) in data/service_headroom.json: "
        + "; ".join(str(h) for h in hits))
    return (f"none of the {len(needles)} private-only intake values reaches "
            f"the committed artifact ({len(report['resolved'])} intake fields "
            f"resolved, {len(report['absent'])} legitimately absent, "
            f"{len(report['pathless'])} declared path-less)")


def case_the_private_leak_scan_catches_a_planted_value():
    """The positive control. A scan that never fires is indistinguishable from
    a scan that cannot fire, and this one decides whether a privacy claim gets
    published -- so it is run against an artifact known to be dirty.

    Synthetic throughout: it plants values of its own rather than reading the
    real intake, so the control runs in CI where the private file does not
    exist.
    """
    fields = PT.cheatsheet_fields()
    private_panel = [f["id"] for f in fields
                     if f["privacy"] == "private-only"
                     and f["id"].startswith("panel_")]
    assert private_panel, "no private-only panel field left to plant"
    household = {"panel": {"meter_class": "CL999-TEST",
                           "schedule": [{"device": "TESTCO XY-1234",
                                         "poles": 2, "amps": 60,
                                         "label": "Aviary"}]}}
    needles = PT.needles(household, fields)
    planted = {n.value for n in needles}
    assert {"CL999-TEST", "TESTCO XY-1234", "Aviary"} <= planted, planted
    assert "60" not in planted and "2" not in planted, (
        "a bare ampere rating or pole count is being treated as identifying")

    clean = {"panel": {"service_rating_a": 175.0, "occupancy": {"devices": 1}},
             "provenance": {"voltage_basis": "120/240 V residential service"}}
    clean_text = json.dumps(clean, indent=1, sort_keys=True)
    assert not PT.leaks(needles, clean_text, clean), \
        "the scan fires on an artifact that carries none of the planted values"

    # each mode gets its own dirty artifact: one buried in prose, one published
    # as a value, because the two are looked for by different means
    prose = dict(clean)
    prose["provenance"] = {"voltage_basis": "120/240 V service, meter class "
                                            "CL999-TEST; legs at 240 V"}
    prose_text = json.dumps(prose, indent=1, sort_keys=True)
    caught = {h.field_id for h in PT.leaks(needles, prose_text, prose)}
    assert "panel_meter_class" in caught, \
        "a private value quoted inside a prose sentence was not caught"

    as_value = {"panel": {"occupancy": {"largest_branch_ocpd_a": 60.0},
                          "circuits": ["Aviary"]}}
    value_text = json.dumps(as_value, indent=1, sort_keys=True)
    caught = {h.leaf_path for h in PT.leaks(needles, value_text, as_value)}
    assert "panel.schedule[].label" in caught, \
        "a private label published as a value was not caught"
    return ("the leak scan fires on planted values in prose and as values, and "
            "stays quiet on a clean artifact")


# Figures DERIVED from the panel schedule that the artifact publishes outside
# panel.occupancy, each with the reason it is publishable. The cheatsheet's
# panel_schedule privacy_note is a closed allow-list of aggregates, and a
# derived figure that is not on it is a disagreement between the artifact and
# the stated rule -- which is how existing_ac_ocpd_a came to be published
# without anyone deciding it could be. Both guards miss it structurally: the
# leak scan skips whole integers below 1000 (a bare ampere rating is not
# identifying), and the row-shape scan only rejects device/poles/label keys.
# So it is declared here, and the artifact is checked to publish nothing else.
SCHEDULE_DERIVED_FIGURES = {
    "noncoincident_loads.existing_ac_ocpd_a": (
        "one bare ampere rating, the same class of value the cheatsheet already "
        "tiers public-ok for the service, busbar and backfeed ratings: a "
        "standard NEC 240.6(A) size that millions of dwellings share. It is "
        "load-bearing -- the 220.60 credit bound is 125% of it -- and it is "
        "published without the device marking or the door-legend label that "
        "selected it. The cheatsheet's panel_schedule privacy_note has to name "
        "it as an admitted aggregate; until it does, this artifact and that "
        "note disagree"),
    "noncoincident_loads.schedule_entries_matching_an_air_conditioning_token": (
        "a count over the schedule, the same shape as devices and "
        "twin_density_devices, and the thing that makes the three-valued "
        "existing_ac_ocpd_a readable"),
}

# What the artifact is allowed to say about the panel schedule: counts and sums
# over it, never a row of it. panel_schedule stays private-only because its
# `device` markings and door-legend `label`s describe one particular house.
OCCUPANCY_KEYS = {
    "branch_ocpd_sum_a", "devices", "largest_branch_ocpd_a", "note",
    "pole_positions_free", "pole_positions_total", "pole_positions_used",
    "spaces_free", "spaces_total", "spaces_used", "twin_density_devices",
}


def case_the_artifact_aggregates_the_panel_schedule_away():
    """The structural half of the privacy boundary. The leak scan catches this
    household's own strings; this catches the SHAPE that would leak anyone's --
    a per-device row reaching the artifact at all."""
    d = json.loads(S.OUT.read_text())
    occ = d["panel"]["occupancy"]
    extra = set(occ) - OCCUPANCY_KEYS
    assert not extra, (
        f"panel.occupancy has grown {sorted(extra)} -- the schedule is "
        f"published as aggregates only; add the key here if it is one")
    for k, v in occ.items():
        assert k == "note" or isinstance(v, (int, float)), (
            f"panel.occupancy.{k} is not a count or a sum: {v!r}")

    # A schedule row is recognisable by its own keys. `device` and `poles`
    # belong to nothing else in this artifact, and `label` beside an amp or
    # pole figure is a row even where `label` alone is an ordinary caption --
    # nec_220_87.steps[].label is a step's name, not a door legend.
    row_only = {"device", "poles"}
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            here = ".".join(path) or "<root>"
            for k in sorted(row_only & set(o)):
                found.append(f"{here}.{k}")
            if "label" in o and {"amps", "poles", "device"} & set(o):
                found.append(f"{here}.label (beside a device figure)")
            for k, v in o.items():
                walk(v, path + [k])
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + [f"[{i}]"])

    walk(d, [])
    assert not found, (
        f"panel-schedule row key(s) in the artifact: {found} -- devices and "
        f"door-legend labels are private-only and only aggregates over them "
        f"may be published")

    # Figures derived from the schedule outside panel.occupancy are declared,
    # with the reason each may be published. An undeclared one is the defect
    # this catches: neither the leak scan nor the row-shape walk above can see
    # a bare ampere rating lifted out of one row.
    nc = d["noncoincident_loads"]
    derived = {f"noncoincident_loads.{k}" for k in nc
               if k.startswith(("existing_ac_ocpd_a",
                                "schedule_entries_matching"))}
    undeclared = sorted(derived - set(SCHEDULE_DERIVED_FIGURES))
    assert not undeclared, (
        f"schedule-derived figure(s) published without a declared reason: "
        f"{undeclared}. Declare them in SCHEDULE_DERIVED_FIGURES and have the "
        f"cheatsheet's panel_schedule privacy_note admit them, or stop "
        f"publishing them")
    for key, why in SCHEDULE_DERIVED_FIGURES.items():
        assert len(why) > 60, key
        leaf = key.split(".")[-1]
        assert leaf in nc, f"{key} is declared but no longer published"
        assert nc[leaf] is None or isinstance(nc[leaf], (int, float)), \
            f"{key} is not a bare number: {nc[leaf]!r}"
    return "the artifact publishes aggregates of the panel schedule, never a row of it"


# ---------------------------------------------------------------------------
# household.has_ev -- the second applicability flag
# ---------------------------------------------------------------------------

PANEL_YAML_NO_CHARGER = PANEL_YAML.replace("charger:\n  kw: 11.5\n", "")
PANEL_YAML_NO_EV = "household:\n  has_ev: false\n" + PANEL_YAML_NO_CHARGER
PANEL_YAML_EV_TRUE = "household:\n  has_ev: true\n" + PANEL_YAML_NO_CHARGER


def case_charger_kw_is_read_only_where_there_is_an_ev():
    """The intake contract says a household with has_ev: false may leave the
    charger: block out. load_panel() read charger.kw unconditionally, so that
    household stopped on a key the contract permits to be missing -- and
    has_ev: false with has_new_load_interest: true is an ordinary combination,
    somebody scoping a heat pump."""
    # absent flag -> unchanged: the read is still required and still fails closed
    td = _with_household(PANEL_YAML_NO_CHARGER)
    try:
        S.load_panel()
        raise AssertionError("an absent has_ev excused the charger.kw read")
    except SystemExit as e:
        assert "charger.kw" in str(e), e
    finally:
        del td
    # explicit true -> same
    td = _with_household(PANEL_YAML_EV_TRUE)
    try:
        S.load_panel()
        raise AssertionError("has_ev: true excused the charger.kw read")
    except SystemExit as e:
        assert "charger.kw" in str(e), e
    finally:
        del td
    # explicit false -> the key is not read at all, and its absence is fine
    td = _with_household(PANEL_YAML_NO_EV)
    panel = S.load_panel()
    assert panel["has_ev"] is False, panel["has_ev"]
    assert panel["charger_kw"] is None, panel["charger_kw"]
    assert panel["service_rating_a"] == 175.0, panel
    del td
    # ... and not read even when it IS there: the flag is the authority
    td = _with_household("household:\n  has_ev: false\n" + PANEL_YAML)
    panel = S.load_panel()
    assert panel["charger_kw"] is None, (
        "charger.kw was read under has_ev: false -- the flag decides whether "
        "the EVSE scenarios exist, not the presence of the key")
    del td
    # a value that is not a YAML boolean is an intake defect, not a default
    td = _with_household('household:\n  has_ev: "false"\n' + PANEL_YAML)
    try:
        S.load_panel()
        raise AssertionError("a quoted 'false' was accepted as a flag")
    except SystemExit as e:
        assert "boolean" in str(e), e
    del td
    return "charger.kw is read only where household.has_ev is not explicitly false"


def _private_run_ready():
    """The real intake plus the raw archive the full build() needs."""
    return (REAL_HOUSEHOLD.is_file()
            and len(list(S.RAW_DIR.glob("Electric_15_Minute_*.csv"))) == 1
            and bool(list(S.RAW_DIR.glob("enphase_sam8760_*.csv"))))


def _build_with(mutate):
    """build() against a copy of the real intake, mutated in memory."""
    import copy
    import yaml
    household = yaml.safe_load(REAL_HOUSEHOLD.read_text())
    household = copy.deepcopy(household)
    mutate(household)
    td = _with_household(yaml.safe_dump(household, sort_keys=False))
    try:
        return S.build()
    finally:
        del td


def case_a_household_with_no_ev_still_gets_its_panel_answer():
    """End to end on the real inputs with the EV taken away: the heat-pump and
    battery questions still get answered, the second-charger scenarios report
    themselves not applicable with the flag that did it, and charger.kw is
    never touched."""
    if not _private_run_ready():
        raise SkipCase("the end-to-end no-EV run needs the private archive "
                       "(the Green Button export and the Enphase "
                       "consumption-CT files)")

    def no_ev(h):
        h["household"]["has_ev"] = False
        h.pop("charger", None)

    reads = []
    real_get = HH.get

    def spy(path, required=True):
        reads.append(path)
        return real_get(path, required=required)

    HH.get = spy
    try:
        d = _build_with(no_ev)
    finally:
        HH.get = real_get

    assert "charger.kw" not in reads, \
        f"charger.kw was read on a household with no EV: {reads}"
    assert [c["case"] for c in d["cases"]] == \
        ["heat_pump_only", "heat_pump_and_battery", "heat_pump_replaces_ac"], \
        [c["case"] for c in d["cases"]]
    skipped = d["scenarios_not_applicable"]
    assert skipped, "nothing was reported as skipped"
    items = {s["item"] for s in skipped}
    assert {"second_evse_only", "heat_pump_and_second_evse",
            "heat_pump_second_evse_and_battery",
            "panel.existing_evse_kw"} <= items, items
    for s in skipped:
        assert s["flag"] == "household.has_ev", s
        assert "has_ev is false" in s["reason"], s
        assert "charger.kw is not read" in s["reason"], s
        assert s["to_enable_it"], s
    assert d["panel"]["existing_evse_kw"] is None, d["panel"]
    assert "NOT APPLICABLE" in d["panel"]["existing_evse_kw_basis"], d["panel"]
    assert d["added_load_code_values"]["second_evse_a"] is None, \
        d["added_load_code_values"]
    assert "NOT APPLICABLE" in \
        d["added_load_code_values"]["second_evse_basis"], d["added_load_code_values"]
    assert [m["mitigation"] for m in d["mitigations"]] == \
        ["Power control system on the sources (NEC 705.13)"], d["mitigations"]

    # the questions that do not depend on an EV are answered, and answered the
    # same way: nothing about the demand side or the busbar moved
    committed = json.loads(S.OUT.read_text())
    assert d["nec_220_87"] == committed["nec_220_87"], \
        "the 220.87 chain moved when the EV was taken away"
    assert d["battery_inverter"] == committed["battery_inverter"], \
        "the busbar analysis moved when the EV was taken away"
    hp = next(c for c in d["cases"] if c["case"] == "heat_pump_only")
    hp_committed = next(c for c in committed["cases"]
                        if c["case"] == "heat_pump_only")
    assert hp["remaining_headroom_a"] == hp_committed["remaining_headroom_a"], hp
    return ("a household with no EV gets the heat-pump and battery answers and "
            "is told which EVSE scenarios were skipped and why")


def case_an_absent_ev_flag_reproduces_the_committed_artifact():
    """Absence is not false, proved at the only scale that settles it: with the
    flag deleted the run has to produce the committed artifact byte for byte."""
    if not _private_run_ready():
        raise SkipCase("the absent-flag reproduction needs the private archive "
                       "(the Green Button export and the Enphase "
                       "consumption-CT files)")
    d = _build_with(lambda h: h["household"].pop("has_ev", None))
    got = (json.dumps(d, indent=1, sort_keys=True) + "\n").encode()
    assert got == S.OUT.read_bytes(), (
        "deleting household.has_ev changed the artifact -- an absent flag is "
        "being read as false somewhere")
    return "an absent household.has_ev reproduces the committed artifact byte for byte"


# ---------------------------------------------------------------------------
# NEC citations
# ---------------------------------------------------------------------------

# A section number, as this module publishes them: 705.12(B)(3)(3), 220.87(1),
# 440.4(B). Bare three-digit-dot-number and any parenthesised subdivisions.
NEC_SECTION_RE = r"\d{3}\.\d+(?:\([A-Za-z0-9]+\))*"


def _nec_sections(text):
    import re
    return set(re.findall(r"\b" + NEC_SECTION_RE, text))


def case_every_nec_citation_comes_from_one_declared_table():
    """Three citations were wrong at once, each at more than one site: the
    sum-of-all-OCPDs rule cited as 705.12(B)(3)(1), the MCA marking cited as
    440.6, and the 30-day recording route cited as the body of 220.87 rather
    than as the Exception this service may not use.

    The fix is structural rather than three edits. Every section number lives in
    NEC_RULES with the rule it stands for, and every published citation is built
    by nec()/nec_rule() from that table -- so a number typed into prose is a
    test failure here, and a misnumbering is a one-line fix in one place.
    """
    import re
    src = pathlib.Path(S.__file__).read_text()
    tree = ast.parse(src)

    table = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and \
                any(getattr(t, "id", "") == "NEC_RULES" for t in node.targets):
            table.update(id(n) for n in ast.walk(node))
    assert table, "NEC_RULES is not a module-level table any more"

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))

    inline, in_docs = [], set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in table:
            continue
        found = _nec_sections(node.value)
        if not found:
            continue
        if id(node) in docstrings:
            in_docs |= found
        elif not (node.value.strip() in S.NEC_RULES):
            # a literal that IS a table key is a citation argument to
            # nec()/nec_rule(), which validates it at run time
            inline.append((node.lineno, sorted(found), node.value[:60]))
    assert not inline, (
        f"NEC section number(s) typed into prose rather than cited through "
        f"nec()/nec_rule(): {inline}. Add the section to NEC_RULES and build "
        f"the string from it, so the number and the rule it stands for cannot "
        f"drift apart")
    undeclared = sorted(in_docs - set(S.NEC_RULES))
    assert not undeclared, (
        f"docstring(s) cite section(s) the table does not declare: "
        f"{undeclared}")

    # the artifact's own citations resolve to the table too
    text = S.OUT.read_text()
    cited = set()
    for hit in re.findall(r"NEC (" + NEC_SECTION_RE + ")", text):
        cited.add(hit)
    assert cited, "the artifact cites no NEC section -- the scan is stale"
    unknown = sorted(cited - set(S.NEC_RULES))
    assert not unknown, f"artifact cites undeclared section(s): {unknown}"

    # and the three that were wrong are right, at every site
    d = json.loads(text)
    assert "440.6" not in text, "the retired 440.6 MCA citation is back"
    # the module may say why 440.6 was wrong; it may not cite it
    assert "NEC 440.6" not in src and "440.6 MCA" not in src, \
        "service_headroom.py still cites 440.6 for MCA"
    assert "440.4(B)" in S.NEC_RULES and "440.35" in S.NEC_RULES
    sr = d["battery_inverter"]["sum_rule"]["rule"]
    assert "705.12(B)(3)(3)" in sr, sr
    assert "705.12(B)(3)(1)" not in text, \
        "the sum-of-breakers rule is still cited as (B)(3)(1) somewhere"
    assert "705.12(B)(3)(1)" not in src, sr
    hp = d["added_load_code_values"]["heat_pump_basis"]
    assert "440.4(B)" in hp and "440.35" in hp, hp
    # the AHJ sentence is Article 100's, not a quotation from 220.87
    cav = d["caveat"]
    assert "authority having jurisdiction" in cav, cav
    assert "NEC 100" in cav, cav
    assert "220.87 requires that the maximum-demand data be acceptable" \
        not in src, "the AHJ sentence is still attributed to 220.87"
    return (f"all {len(cited)} NEC citations in the artifact resolve to the "
            f"one declared table, and none is typed inline")


def case_the_220_87_conditions_are_published_with_the_right_minimum():
    """220.87(1) is a 1-YEAR period. The 30-day recording is the EXCEPTION to
    it, and the Exception is closed to a service with a photovoltaic system --
    so "13x the code minimum of 30" was wrong twice: wrong minimum, and a route
    this service could never take."""
    d = json.loads(S.OUT.read_text())
    nec = d["nec_220_87"]
    con = nec["conditions"]
    assert nec["condition_1_days_required"] == 365, nec
    assert "code_minimum_days" not in nec, nec
    assert "code minimum" not in json.dumps(nec), nec["window_note"]

    c1 = con["condition_1"]
    assert c1["days_required"] == 365, c1
    assert c1["days_available"] == nec["measurement_days"], c1
    assert _close(c1["margin_x"], _rq(c1["days_available"] / 365.0, 2)), c1
    assert c1["margin_x"] < 2.0, ("395 days is not 13x anything", c1)
    assert c1["verdict"] == "pass", c1

    exc = con["condition_1_exception_30_day_recording"]
    assert exc["available_to_this_service"] is False, exc
    assert "renewable energy system" in exc["rule"], exc
    assert "photovoltaic" in exc["why"], exc
    # the PV that closes the Exception is the intake's own figure
    kw = d["gross_reconstruction"]["pv_ac_ceiling"]["ceiling_kw"]
    assert f"{kw:.2f} kW" in exc["why"], (kw, exc["why"])
    assert "WEAKER" in exc["why_it_strengthens_rather_than_weakens"], exc
    assert "condition (1)" in exc["why_it_strengthens_rather_than_weakens"], exc

    # condition (2) is what the artifact computes; (3) is what it cannot
    assert "compute" in con["condition_2"]["where_it_is_evaluated"], con
    c3 = con["condition_3"]
    assert c3["verdict"] == "not_determined", c3
    assert c3["what_would_settle_it"], c3
    assert "240.4" in c3["rule"] and "230.90" in c3["rule"], c3
    # and the window note states the true margin
    note = nec["window_note"]
    assert "1-year period" in note, note
    assert f"{_rq(c1['days_available'] / 365.0, 2)}x" in note, note
    assert "Exception" in note and "closed" in note, note
    return "the 220.87 conditions are published, with a 1-year minimum and the Exception ruled out"


def case_the_busbar_rule_shows_both_source_figures():
    """705.12(B)(3)(2) counts 125% of each source's OUTPUT CIRCUIT CURRENT; this
    analysis counts breaker ratings. The ratings are the larger figures here, so
    the substitution is conservative -- which is worth showing rather than
    leaving to chance."""
    d = json.loads(S.OUT.read_text())
    b = d["battery_inverter"]["busbar_120_percent"]
    sc = b["source_current_basis"]
    assert "125 percent of the power-source(s) output circuit current" in \
        sc["rule_as_written"], sc
    assert "120 percent of the ampacity of the busbar" in sc["rule_as_written"]
    rows = sc["sources"]
    assert len(rows) == 2, rows
    for r in rows:
        # both figures are published rounded, so they agree to the last
        # published place rather than to the bit
        assert _close(r["code_figure_125pct_of_output_a"],
                      r["output_circuit_current_a"] * 1.25, 1e-3), r
        assert _close(r["rating_minus_code_figure_a"],
                      _rq(r["ocpd_rating_a"] - r["code_figure_125pct_of_output_a"],
                          4), 1e-4), r
        assert r["rating_is_at_or_above_the_code_figure"] is (
            r["ocpd_rating_a"] >= r["code_figure_125pct_of_output_a"]), r
        assert r["output_basis"], r
    assert sc["every_rating_is_at_or_above_the_code_figure"] is True, sc
    # the figures the review checked by hand: a 50 A PV breaker against
    # 1.25 x 39.375 A, and a 60 A battery breaker against 1.25 x 47.9167 A
    by = {r["source"]: r for r in rows}
    pv = by[S.SOURCE_EXISTING_PV]
    assert _close(pv["ocpd_rating_a"], b["existing_pv_backfeed_a"]), pv
    assert _close(pv["code_figure_125pct_of_output_a"], 49.2188, 1e-4), pv
    bat = by[S.SOURCE_PROPOSED_BATTERY]
    assert _close(bat["ocpd_rating_a"],
                  d["battery_inverter"]["backfeed_breaker_a"]), bat
    assert _close(bat["code_figure_125pct_of_output_a"], 59.8958, 1e-4), bat
    # nothing published moves: the arithmetic still runs on the ratings
    assert _close(b["remaining_backfeed_a"],
                  _rq(b["busbar_x_120pct_a"] - b["main_ocpd_a"]
                      - b["existing_pv_backfeed_a"], 1)), b
    # an empty source list claims nothing rather than claiming everything
    empty = S.source_current_basis(())
    assert empty["every_rating_is_at_or_above_the_code_figure"] is None, empty
    assert "cannot be compared" in empty["reading"], empty
    # and a source whose breaker is SMALLER than its code figure is caught
    low = S.source_current_basis([("x", 40.0, 39.375, "test")])
    assert low["every_rating_is_at_or_above_the_code_figure"] is False, low
    assert "BELOW" in low["reading"], low
    return "the 120% rule publishes both the breaker ratings and the code's 125%-of-output figures"


# ---------------------------------------------------------------------------
# The conservative basis, and the coverage gap that sets it
# ---------------------------------------------------------------------------

def _covered_env(basis):
    """A one-interval envelope on the given PV ceiling basis, plus a `sam` that
    makes the interval covered or not."""
    d = dt.date(2026, 6, 15)
    env = [(d, 1.25, 4.0, 25.0, 0.0, False, basis)]
    sam = {(d, 1): 1.0} if basis == S.PV_BASIS_MEASURED else {(d, 0): 1.0}
    return d, env, sam


def case_the_conservative_basis_names_the_interval_that_sets_it():
    """The published description of the conservative basis used to be a fixed
    sentence about daylight while the interval that actually set it was a 01:15
    in the uncovered tail. It is derived from that interval now, and so is what
    would settle a case computed against it -- because where the hour was never
    covered, 15-minute production data settles nothing."""
    # uncovered: the bound is the bare nameplate cap, and the remedy is a
    # consumption-CT export that reaches the end of the meter window
    d, env, sam = _covered_env(S.PV_BASIS_NAMEPLATE)
    lag = {"enphase_coverage_last_hour": "2026-06-14 07:00",
           "meter_window_last_interval": "2026-06-15 23:45", "lag_hours": 40.75}
    b = S.binding_upper_interval(env, set(), sam, 9.45, lag)
    assert b["timestamp_local"] == "2026-06-15 01:15", b
    assert b["pv_ceiling_basis"] == S.PV_BASIS_NAMEPLATE, b
    assert b["hour_has_a_production_measurement"] is False, b
    assert b["why_the_hour_is_uncovered"] == S.UNCOVERED_AFTER, b
    assert "COVERAGE GAP, not daylight" in b["reading"], b
    assert "2.3625 kWh" in b["reading"], b
    assert lag["enphase_coverage_last_hour"] in b["reading"], b
    settle = S.what_would_settle_it(b, lag)
    assert "AT ANY RESOLUTION" in settle, settle
    assert "would not settle this case" in settle, settle
    assert b["timestamp_local"] in settle, settle
    assert S.conservative_basis_is(b).endswith(b["reading"]), S.conservative_basis_is(b)
    assert b["reading"] in S.verdict_basis(b), S.verdict_basis(b)

    # covered: the bound is hourly resolution, and 15-minute production IS the
    # instrument that would collapse it
    d2, env2, sam2 = _covered_env(S.PV_BASIS_MEASURED)
    b2 = S.binding_upper_interval(env2, set(), sam2, 9.45, lag)
    assert b2["hour_has_a_production_measurement"] is True, b2
    assert b2["why_the_hour_is_uncovered"] is None, b2
    assert "HAS an Enphase reading" in b2["reading"], b2
    assert "COVERAGE GAP" not in b2["reading"], b2
    settle2 = S.what_would_settle_it(b2, lag)
    assert settle2.startswith("15-minute PV production"), settle2
    assert "never metered here" in settle2, settle2
    assert settle2 != settle, "both bases name the same remedy"

    # the four uncovered reasons are exhaustive and each has its own sentence
    assert set(S.UNCOVERED_WHY) == {S.UNCOVERED_DST, S.UNCOVERED_AFTER,
                                    S.UNCOVERED_BEFORE, S.UNCOVERED_GAP}
    assert len(set(S.UNCOVERED_WHY.values())) == 4, S.UNCOVERED_WHY
    return "the conservative basis and its settler are derived from the interval that binds"


def case_the_coverage_lag_is_published_and_gated():
    """The lag between the end of the production record and the end of the
    meter window was computed and never acted on. Every interval inside it
    carries the bare nameplate cap, and on this household one of them sets the
    conservative basis -- so it is published in hours and intervals, and a lag
    past the declared threshold stops the run."""
    day = dt.date(2026, 6, 15)
    sam = {(day, h): 1.0 for h in range(8)}

    def env_to(hours):
        return [(day, h + f, 1.0, 2.0, 0.0, False, S.PV_BASIS_NAMEPLATE)
                for h in range(hours) for f in (0.0, .25, .5, .75)]

    ok = S.coverage_lag(env_to(9), sam, 4)
    assert ok["enphase_coverage_last_hour"] == "2026-06-15 07:00", ok
    assert ok["meter_window_last_interval"] == "2026-06-15 08:45", ok
    assert _close(ok["lag_hours"], 1.75), ok
    assert ok["lag_intervals"] == 4, ok
    assert ok["gate"]["passed"] is True, ok
    assert ok["gate"]["threshold"] == S.ENPHASE_COVERAGE_MAX_LAG_HOURS, ok
    assert "STALE" in ok["gate"]["catches"], ok
    assert "4 interval(s)" in ok["what_the_lag_costs"], ok

    # past the threshold the run stops rather than publishing a conservative
    # basis set by a lengthening stretch of unmeasured hours
    long_env = [(day + dt.timedelta(days=30), 12.0, 1.0, 2.0, 0.0, False,
                 S.PV_BASIS_NAMEPLATE)] + env_to(8)
    try:
        S.coverage_lag(long_env, sam, 3000)
        raise AssertionError("a stale Enphase export was accepted")
    except SystemExit as e:
        assert "stops" in str(e) and "before the end of the meter window" in str(e), e
        assert "Re-pull the Enphase export" in str(e), e
        assert "nothing was written" in str(e), e
        # and it says what NOT to do about it
        assert "Shortening the meter window instead is not the fix" in str(e), e
    assert S.ENPHASE_COVERAGE_MAX_LAG_HOURS == 168.0, \
        "the lag threshold moved -- the comment justifying it has to move too"
    return "the Enphase coverage lag is published in hours and intervals, and gated"


def case_the_artifact_flags_an_unmeasured_binding_hour():
    """A reader meeting max_upper_bound_kw must be able to see, without going
    looking, whether the interval that produced it had any production
    measurement behind it."""
    d = json.loads(S.OUT.read_text())
    g = d["gross_reconstruction"]
    b = g["max_upper_bound_binding_interval"]
    sens = d["nec_220_87"]["sensitivity_on_the_upper_bound"]
    flag = "max_upper_bound_is_set_by_an_hour_with_no_production_measurement"
    assert _close(b["upper_bound_kw"], g["max_upper_bound_kw"]), (b, g)
    assert _close(sens["max_upper_bound_kw"], g["max_upper_bound_kw"]), sens
    assert sens["binding_interval"] == b, sens
    assert g[flag] is not b["hour_has_a_production_measurement"], g[flag]
    assert sens[flag] == g[flag], (sens[flag], g[flag])
    assert b["pv_ceiling_basis"] in (S.PV_BASIS_MEASURED, S.PV_BASIS_NAMEPLATE)
    assert (b["pv_ceiling_basis"] == S.PV_BASIS_MEASURED) == \
        b["hour_has_a_production_measurement"], b
    assert b["reading"] in g["honesty"], g["honesty"]
    assert b["reading"] in sens["why"], sens["why"]
    # the lag that produced it, published beside the split
    lag = g["pv_ceiling_basis_split"]["enphase_coverage_lag"]
    assert lag["gate"]["passed"] is True, lag
    assert lag["lag_intervals"] == g["pv_ceiling_basis_split"][
        "nameplate_intervals_by_reason"].get(S.UNCOVERED_AFTER, 0), lag
    if not b["hour_has_a_production_measurement"]:
        # this household: the binding interval is in the uncovered tail, and
        # every not_determined case names the export re-pull, not a 15-minute
        # production series
        assert b["why_the_hour_is_uncovered"] == S.UNCOVERED_AFTER, b
        assert lag["lag_hours"] > 0, lag
        for c in d["cases"]:
            if c["ampacity_verdict"] == "not_determined":
                assert "Enphase consumption-CT export pulled through" in \
                    c["what_would_settle_it"], c
    return "the artifact says in one field whether an unmeasured hour sets the conservative basis"


# ---------------------------------------------------------------------------
# Mitigations, the A/C credit and the peak
# ---------------------------------------------------------------------------

def case_mitigations_carry_both_bases_and_a_verdict():
    """A mitigation figure published on the measured basis alone reads as
    available at settings the conservative basis does not fit -- including the
    48 A setting the second_evse_only case itself calls not_determined."""
    d = json.loads(S.OUT.read_text())
    by = {m["mitigation"]: m for m in d["mitigations"]}
    share = by["EVSE load sharing"]
    for key in ("case_second_evse_only_headroom_a", "case_both_heat_pump_mca_a"):
        fig = share[key]
        assert set(fig) >= {"measured_basis", "conservative_basis",
                            "ampacity_verdict"}, fig
        for basis in ("measured_basis", "conservative_basis"):
            assert "binding" in fig[basis] and fig[basis]["binding_is"], fig
        assert fig["conservative_basis"]["binding"] <= \
            fig["measured_basis"]["binding"], fig
        assert fig["ampacity_verdict"] == S.ampacity_verdict(
            fig["measured_basis"]["binding"],
            fig["conservative_basis"]["binding"]), fig
    # they are the whole calculated headroom: a sharing group adds no code load
    nec = d["nec_220_87"]
    assert _close(share["case_second_evse_only_headroom_a"]["measured_basis"][
        "binding"], nec["headroom_a"]["binding"]), share
    assert _close(share["case_second_evse_only_headroom_a"][
        "conservative_basis"]["binding"],
        nec["sensitivity_on_the_upper_bound"]["binding"]), share

    rate = by["Charge-rate limit on the second EVSE"]
    table = rate["table"]
    assert len(table) == len(S.WALL_CONNECTOR_OUTPUTS_A), table
    for row in table:
        assert row["ampacity_verdict"] in ("pass", "fail", "not_determined"), row
        assert row["ampacity_verdict"] == S.ampacity_verdict(
            row["headroom_left_measured_basis"]["binding"],
            row["headroom_left_conservative_basis"]["binding"]), row
        assert (row["what_would_settle_it"] is not None) == \
            (row["ampacity_verdict"] == "not_determined"), row
        assert _close(row["headroom_left_measured_basis"]["binding"],
                      _rq(nec["headroom_a"]["binding"] - row["code_load_a"], 4)), row
    # the verdicts are monotone in the setting, and this household's own answer
    verdicts = [r["ampacity_verdict"] for r in table]
    assert verdicts.index("not_determined") > 0, verdicts
    assert all(v == "pass" for v in verdicts[:verdicts.index("not_determined")])
    passing = rate["settings_that_pass_on_both_bases_a"]
    assert passing == [r["evse_output_a"] for r in table
                       if r["ampacity_verdict"] == "pass"], passing
    # the 48 A row is the one the case calls not_determined, and it says so
    by_out = {r["evse_output_a"]: r for r in table}
    top = by_out[S.EXISTING_EVSE_OUTPUT_A]
    case = [c for c in d["cases"] if c["case"] == "second_evse_only"][0]
    assert top["ampacity_verdict"] == case["ampacity_verdict"], (top, case)
    assert top["headroom_left_vs_service_a"] > 0 > \
        top["headroom_left_conservative_basis"]["vs_service_rating"], top
    assert f"{len(passing)} of {len(table)}" in rate["reading"], rate["reading"]
    return "every mitigation figure carries both bases and a three-valued verdict"


def case_the_existing_ac_ocpd_is_three_valued():
    """The A/C rating was taken from the first label that matched, and a miss
    was indistinguishable from a panel with no A/C circuit. Both are answers the
    schedule cannot give, so both report not_determined."""
    read = S.existing_ac_ocpd([{"poles": 2, "amps": 40, "label": "A/C unit"},
                               {"poles": 1, "amps": 20, "label": "Kitchen"}])
    assert _close(read["ocpd_a"], 40.0), read
    assert read["basis"] == S.AC_READ, read
    assert read["matches"] == 1, read
    assert read["what_would_settle_it"] is None, read
    assert "A/C unit" not in read["reading"], "the door legend leaked"

    none = S.existing_ac_ocpd([{"poles": 1, "amps": 20, "label": "Kitchen"}])
    assert none["ocpd_a"] is None and none["basis"] == S.AC_NO_MATCH, none
    assert none["reading"].startswith("NOT DETERMINED"), none
    assert "not the same as a panel with no air-conditioning circuit" in \
        none["reading"], none
    assert none["what_would_settle_it"], none

    two = S.existing_ac_ocpd([{"poles": 2, "amps": 40, "label": "A/C"},
                              {"poles": 2, "amps": 30, "label": "Condenser 2"}])
    assert two["ocpd_a"] is None and two["basis"] == S.AC_AMBIGUOUS, two
    assert two["matches"] == 2, two
    assert "choosing silently" in two["reading"], two

    twin = S.existing_ac_ocpd([{"poles": 2, "amps": [20, 20], "label": "a/c"}])
    assert twin["ocpd_a"] is None and twin["basis"] == S.AC_NOT_ONE_DEVICE, twin

    # in the artifact: the figure travels with its basis, and the credit bound
    # is computed from it or withheld with it
    d = json.loads(S.OUT.read_text())
    nc = d["noncoincident_loads"]
    assert nc["existing_ac_ocpd_basis"] in (
        S.AC_READ, S.AC_NO_MATCH, S.AC_AMBIGUOUS, S.AC_NOT_ONE_DEVICE), nc
    high = nc["credit_bounds_a"]["high"]
    if nc["existing_ac_ocpd_basis"] == S.AC_READ:
        assert _close(high, _rq(nc["existing_ac_ocpd_a"] * 1.25, 4)), nc
        assert nc["existing_ac_ocpd_what_would_settle_it"] is None, nc
    else:
        assert nc["existing_ac_ocpd_a"] is None and high is None, nc
        assert nc["existing_ac_ocpd_what_would_settle_it"], nc
    # only the bare rating is published, never a word of the schedule row
    assert isinstance(nc["schedule_entries_matching_an_air_conditioning_token"],
                      int), nc
    # the token list itself stays out: a token short enough to be useful is
    # exactly what a door legend says, and publishing the list republishes one
    # household's label verbatim -- the private-only leak scan caught it
    assert "air_conditioning_tokens_searched" not in nc, nc
    values = PT.structured_strings(nc)
    for tok in S.AC_LABEL_TOKENS:
        assert tok not in values, \
            f"{tok!r} is published as a value, and that is what a door legend says"
    return "the existing A/C rating is three-valued and publishes no schedule row"


# ---------------------------------------------------------------------------
# heat_pump_replaces_ac (issue #45): a heat pump REPLACING the existing A/C on
# its own circuit, rather than adding one beside it. Unit-level, calling
# S.heat_pump_replacement_case() directly with hand-picked inputs -- the same
# style case_busbar_120_percent_rule_fails_the_battery and its siblings use --
# rather than driving a full build() for every credit/nameplate combination.
# ---------------------------------------------------------------------------

AC_SCHEDULE = [{"device": "x", "poles": 2, "amps": 40, "label": "Condenser"}]


def _replacement_occ(spaces_free):
    """A panel_occupancy()-shaped dict with just the field the case reads.

    AC_SCHEDULE's one entry is a full-size 2-pole device (2 spaces), so the
    single-pole fillers make up the rest of the 20-space panel.
    """
    return S.panel_occupancy(
        [{"poles": 1, "amps": 15, "label": f"circuit {i}"}
         for i in range(20 - spaces_free - 2)] + AC_SCHEDULE,
        20, 40)


def case_heat_pump_replacement_case_shows_the_full_220_60_arithmetic():
    """With the nameplate on hand, the credit bound is 125% of it (the same
    220.87(2) factor the rest of the module applies to a measured maximum),
    and both the zero-credit and full-credit headroom are published so the
    arithmetic is checkable end to end -- AC1/AC2 of issue #45."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)
    assert ac["basis"] == S.AC_READ and _close(ac["ocpd_a"], 40.0), ac
    occ = _replacement_occ(spaces_free=1)

    avail = {"service": 10.0}
    avail_upper = {"service": -15.0}
    c = S.heat_pump_replacement_case(
        ac, 8.0, S.AC_NAMEPLATE_READ, avail, avail_upper, S.SOCKET_SURVEYED_NONE,
        occ, None, "measured basis", "conservative basis", "verdict basis")

    assert c["case"] == "heat_pump_replaces_ac" and c["scored"] is True, c
    assert c["rule"] == S.nec_rule("220.60"), c
    assert "125 percent of the motor load" in c["rule"], c
    assert "if it is the largest motor" in c["rule"], c
    assert _close(c["existing_ac_ocpd_a"], 40.0), c
    assert _close(c["existing_ac_nameplate_rla_a"], 8.0), c
    assert c["existing_ac_nameplate_basis"] == S.AC_NAMEPLATE_READ, c
    # 125% of the nameplate, the same 220.87(2) factor, not the breaker rating
    assert _close(c["noncoincident_credit_bounds_a"]["low"], 0.0), c
    assert _close(c["noncoincident_credit_bounds_a"]["high"],
                  8.0 * S.NEC_220_87_FACTOR), c
    rem = c["remaining_headroom_a"]
    # measured_basis/conservative_basis are the ZERO-credit reading, exactly
    # heat_pump_only's own arithmetic (fixed_added_load_a is 0.0 either way)
    assert c["fixed_added_load_a"] == 0.0, c
    assert _close(rem["measured_basis"]["binding"], 10.0), rem
    assert _close(rem["conservative_basis"]["binding"], -15.0), rem
    fc = rem["assuming_full_credit"]
    assert _close(fc["measured_basis"]["binding"], 10.0 + 10.0), fc
    assert _close(fc["conservative_basis"]["binding"], -15.0 + 10.0), fc
    return "heat_pump_replaces_ac publishes the 220.60 credit bound and both readings of it"


def case_heat_pump_replacement_case_verdict_spans_the_credit_and_envelope_axes():
    """The verdict is taken across BOTH uncertain axes at once: the envelope
    reconstruction (measured vs conservative, as every other case) and the
    noncoincident credit (0 A vs the nameplate bound). A pass needs the
    zero-credit conservative reading alone to clear zero -- credit can never
    manufacture a pass an unrecorded AC contribution might not have earned --
    but a bounded credit CAN turn what would otherwise be an open question
    into a real fail, which an absent nameplate must never do on its own."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)
    occ = _replacement_occ(spaces_free=1)
    avail, avail_upper = {"service": -5.0}, {"service": -15.0}

    # A small nameplate: even full credit (125% of 2 A = 2.5 A) cannot rescue
    # the worst-case combination, so this is a real, bounded FAIL.
    small = S.heat_pump_replacement_case(
        ac, 2.0, S.AC_NAMEPLATE_READ, avail, avail_upper, S.SOCKET_SURVEYED_NONE,
        occ, None, "m", "c", "v")
    assert small["ampacity_verdict"] == "fail", small
    assert small["what_would_settle_it"] is None, small

    # The identical panel figures, but the nameplate was never read: the same
    # zero-credit numbers fail, yet an unbounded credit cannot be ruled out of
    # rescuing it, so this reads not_determined rather than a guessed fail.
    absent = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert absent["ampacity_verdict"] == "not_determined", absent
    assert absent["what_would_settle_it"] == S.AC_REPLACEMENT_SETTLE, absent
    assert absent["remaining_headroom_a"]["assuming_full_credit"][
        "measured_basis"] is None, absent

    # A generous nameplate (the largest plausible RLA -- equal to the 40 A
    # breaker's own rating) still cannot manufacture a pass on its own: the
    # worst case is always assessed at ZERO credit, so a conservative
    # reading that is already negative stays undetermined-or-worse
    # regardless of RLA.
    generous = S.heat_pump_replacement_case(
        ac, 40.0, S.AC_NAMEPLATE_READ, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert generous["ampacity_verdict"] != "pass", generous
    return ("the verdict spans the credit and envelope axes together, and a "
            "missing nameplate never guesses a fail")


def case_heat_pump_replacement_case_pass_does_not_need_the_nameplate():
    """Where the zero-credit conservative reading already clears zero -- the
    same panel a plain ADD case would call a clean pass on -- the case passes
    outright and reports nothing left to settle, because any real credit can
    only add headroom. This is this household's own actual state: the
    condenser's nameplate is not in intake (verified against
    private/household.yaml and private/1-raw-data/panel/), and the committed
    artifact's own heat_pump_replaces_ac case is exactly this branch."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)
    occ = _replacement_occ(spaces_free=1)
    avail, avail_upper = {"service": 40.0}, {"service": 5.0}
    c = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert c["ampacity_verdict"] == "pass", c
    assert c["what_would_settle_it"] is None, c
    return "a zero-credit pass needs no nameplate reading to stand"


def case_a_recorded_nameplate_never_changes_a_zero_credit_pass():
    """Review finding #7: the headline fail-closed property -- a nameplate
    reading, once the zero-credit conservative basis already passes, can
    only ever confirm the SAME verdict, never move it -- is the one this
    case's whole design rests on (a real credit can only raise headroom) but
    it was never asserted directly. Same panel figures through the case
    twice, with and without the reading, at several plausible RLA values."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)          # 40 A breaker
    occ = _replacement_occ(spaces_free=1)
    avail, avail_upper = {"service": 40.0}, {"service": 5.0}
    without = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert without["ampacity_verdict"] == "pass", without
    for rla in (0.1, 1.0, 8.0, 20.0, 40.0):
        with_reading = S.heat_pump_replacement_case(
            ac, rla, S.AC_NAMEPLATE_READ, avail, avail_upper,
            S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
        assert with_reading["ampacity_verdict"] == "pass", (rla, with_reading)
        assert with_reading["what_would_settle_it"] is None, (rla, with_reading)
    return ("a recorded nameplate never flips a zero-credit pass, at any "
            "plausible RLA")


def case_heat_pump_replacement_case_distinguishes_null_from_absent():
    """panel.existing_ac_nameplate_rla_a follows the same three-state contract
    as pv_backfeed_a and meter_socket_continuous_a: a surveyed null (the plate
    was read and carries no legible figure) is a different sentence from an
    absent key (nobody has looked), even though neither yields a number."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)
    occ = _replacement_occ(spaces_free=1)
    avail, avail_upper = {"service": -5.0}, {"service": -15.0}

    null = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_SURVEYED_NONE, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    absent = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert null["ampacity_verdict"] == absent["ampacity_verdict"] == "not_determined"
    assert null["existing_ac_nameplate_note"] != absent["existing_ac_nameplate_note"]
    assert "no legible" in null["existing_ac_nameplate_note"], null
    assert "ABSENT" in absent["existing_ac_nameplate_note"], absent
    return "a surveyed-illegible nameplate and an unasked one publish different sentences"


def case_heat_pump_replacement_case_is_never_space_blocked():
    """AC3 of issue #45: the case reuses the existing 2-pole A/C circuit, so
    physical_fit() is called with new_2pole_breakers=0 and passes even where
    the panel has NO free space at all -- the constraint that fails every
    ADD case on this real household's panel (1 of 20 free)."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)
    occ = _replacement_occ(spaces_free=0)
    assert occ["spaces_free"] == 0, occ
    avail = avail_upper = {"service": 50.0}
    c = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    sp = c["spaces"]
    assert sp["new_2pole_breakers_required"] == 0, sp
    assert sp["full_size_spaces_required"] == 0, sp
    assert sp["physical_fit"] == "pass", sp
    assert sp["spaces_free"] == 0, sp
    assert "REUSES the existing" in sp["note"], sp
    # a plain ADD case on the same fully-occupied panel would fail outright
    assert S.physical_fit(1, occ["spaces_free"], None) == "fail"
    return "the replacement case is never space-blocked, even at zero free spaces"


def case_heat_pump_replacement_case_is_unscored_without_an_identified_circuit():
    """AC1/AC6: neither leg can be answered without knowing which schedule
    entry is the A/C's -- no space claim (there is no established circuit to
    reuse) and no demand claim (nothing to credit). The case says so rather
    than guessing, and carries none of the scored shape.

    Includes the pole-count gate (review finding #3): a label match alone
    does not prove a 240 V circuit. {"poles": 1, "amps": 20, "label": "A/C
    attic fan"} matches an air-conditioning token on a 120 V, 1-pole branch,
    and without the gate existing_ac_ocpd() would have read it as AC_READ,
    letting the case claim `physical_fit: pass` and "reuses the existing
    2-pole circuit" for a circuit that is not 2-pole at all -- a heat pump
    there needs an actual new 2-pole breaker and a net +1 space, not zero.
    """
    occ = _replacement_occ(spaces_free=1)
    for schedule, expect_basis in (
        ([{"poles": 1, "amps": 15, "label": "Garage"}], S.AC_NO_MATCH),
        ([{"poles": 2, "amps": 40, "label": "Condenser"},
          {"poles": 2, "amps": 30, "label": "Condenser B"}], S.AC_AMBIGUOUS),
        ([{"poles": 1, "amps": 20, "label": "A/C attic fan"}],
         S.AC_NOT_TWO_POLE),
    ):
        ac = S.existing_ac_ocpd(schedule)
        assert ac["basis"] == expect_basis, ac
        c = S.heat_pump_replacement_case(
            ac, None, S.AC_NAMEPLATE_NOT_RECORDED, {"service": 50.0},
            {"service": 50.0}, S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
        assert c["case"] == "heat_pump_replaces_ac", c
        assert c["scored"] is False, c
        assert "spaces" not in c and "ampacity_verdict" not in c, c
        assert expect_basis in c["reason"], c
        assert c["what_would_settle_it"] == ac["what_would_settle_it"], c
    return ("the case reports itself unscored without an identified, "
            "2-pole A/C circuit")


def case_existing_ac_ocpd_rejects_a_non_two_pole_match():
    """The pole-count gate in isolation (review finding #3), including the
    positive case: a genuine 2-pole match still reads normally."""
    one_pole = S.existing_ac_ocpd(
        [{"poles": 1, "amps": 20, "label": "A/C attic fan"}])
    assert one_pole["ocpd_a"] is None, one_pole
    assert one_pole["basis"] == S.AC_NOT_TWO_POLE, one_pole
    assert one_pole["matches"] == 1, one_pole
    assert "1-pole" in one_pole["reading"], one_pole
    assert "240 V" in one_pole["reading"] or "120 V" in one_pole["reading"], \
        one_pole
    assert one_pole["what_would_settle_it"], one_pole
    # a quad (4-pole) with a matching label is caught the same way
    four_pole = S.existing_ac_ocpd(
        [{"poles": 4, "amps": [15, 20, 20, 15], "label": "Condenser panel"}])
    # twin-density (amps is a list) is checked first and wins the label --
    # both are legitimate reasons this entry cannot be read as one 2-pole
    # circuit, and the twin-density check catches it before the pole count
    # does
    assert four_pole["basis"] == S.AC_NOT_ONE_DEVICE, four_pole
    # the real household's own entry is 2-pole and still reads normally
    two_pole = S.existing_ac_ocpd(AC_SCHEDULE)
    assert two_pole["basis"] == S.AC_READ, two_pole
    return "a label match on a non-2-pole entry is not read as the A/C's circuit"


def case_heat_pump_replacement_case_caps_the_solved_mca_at_the_existing_breaker():
    """Review finding #4: physical_fit() only ever checked SPACE, and a
    solved-for heat pump MCA past the existing breaker's own rating is not
    really "reusing the circuit" -- larger conductors would be needed too.
    The case caps every headroom figure it publishes at existing_ac_ocpd_a
    and says so, rather than publishing an unqualified pass built on an
    unverified assumption about the branch conductors."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)          # 40 A breaker
    occ = _replacement_occ(spaces_free=1)
    # Deliberately large panel-level headroom -- 100 A -- so the raw,
    # uncapped answer would be far past what a 40 A breaker's own circuit
    # could be said to support without new conductors.
    avail, avail_upper = {"service": 100.0}, {"service": 60.0}
    c = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    rem = c["remaining_headroom_a"]
    assert _close(rem["measured_basis"]["binding"], 40.0), rem
    assert rem["measured_basis"]["capped_by_existing_branch_circuit"] is True, rem
    # the conservative basis (60 A raw) is ALSO past the 40 A cap here
    assert _close(rem["conservative_basis"]["binding"], 40.0), rem
    assert rem["conservative_basis"]["capped_by_existing_branch_circuit"] is True, rem
    assert c["conductor_ampacity_caveat"] == S.CONDUCTOR_CAP_BASIS, c
    assert c["conductor_ampacity_what_would_settle_it"] == \
        S.CONDUCTOR_CAP_SETTLE, c
    assert "Capped at the existing branch circuit" in c["remaining_is"], c
    # still passes -- the cap narrows the CLAIM, not the verdict, when the
    # capped figure is itself still positive
    assert c["ampacity_verdict"] == "pass", c

    # and where the raw panel-level headroom sits BELOW the breaker rating,
    # the cap changes nothing and is not claimed to have applied
    small_avail, small_avail_upper = {"service": 10.0}, {"service": 5.0}
    uncapped = S.heat_pump_replacement_case(
        ac, None, S.AC_NAMEPLATE_NOT_RECORDED, small_avail, small_avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    urem = uncapped["remaining_headroom_a"]
    assert urem["measured_basis"]["capped_by_existing_branch_circuit"] is False, urem
    assert uncapped["conductor_ampacity_caveat"] is None, uncapped
    return ("the solved-for MCA is capped at the existing branch breaker's "
            "rating, and the cap names itself only when it actually binds")


def case_heat_pump_replacement_case_conductor_cap_covers_the_credit_scenarios_too():
    """The same cap applies to the full-credit figures, not only the
    zero-credit ones -- a nameplate reading does not exempt the branch
    conductors from the same physical limit."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)          # 40 A breaker
    occ = _replacement_occ(spaces_free=1)
    avail, avail_upper = {"service": 100.0}, {"service": 100.0}
    c = S.heat_pump_replacement_case(
        ac, 8.0, S.AC_NAMEPLATE_READ, avail, avail_upper,
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    fc = c["remaining_headroom_a"]["assuming_full_credit"]
    assert _close(fc["measured_basis"]["binding"], 40.0), fc
    assert fc["measured_basis"]["capped_by_existing_branch_circuit"] is True, fc
    assert _close(fc["conservative_basis"]["binding"], 40.0), fc
    assert fc["conservative_basis"]["capped_by_existing_branch_circuit"] is True, fc
    return "the conductor cap applies to the full-credit reading as well as zero-credit"


def case_an_implausible_nameplate_reading_fails_closed():
    """Review nitpick #10: an RLA reading above the ampere rating of the very
    breaker protecting that circuit cannot be a NEC-compliant installation
    (440.22(A) only ever sizes the breaker AT OR ABOVE the equipment's own
    RLA). A number that large is a data error -- wrong units, a transposed
    digit, or MCA recorded where RLA was asked for -- not a real fact to
    build a credit on, and the run stops naming both figures rather than
    publishing an implausible credit."""
    ac = S.existing_ac_ocpd(AC_SCHEDULE)          # 40 A breaker
    occ = _replacement_occ(spaces_free=1)
    try:
        S.heat_pump_replacement_case(
            ac, 200.0, S.AC_NAMEPLATE_READ, {"service": 50.0},
            {"service": 50.0}, S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
        raise AssertionError("a 200 A RLA on a 40 A breaker was accepted")
    except SystemExit as e:
        assert "200" in str(e) and "40" in str(e), e
        assert "existing_ac_nameplate_rla_a" in str(e), e
        assert "440.22(A)" in str(e), e
    # exactly at the breaker's own rating is plausible and runs
    at_the_limit = S.heat_pump_replacement_case(
        ac, 40.0, S.AC_NAMEPLATE_READ, {"service": 50.0}, {"service": 50.0},
        S.SOCKET_SURVEYED_NONE, occ, None, "m", "c", "v")
    assert at_the_limit["scored"] is True, at_the_limit
    return "an RLA reading above its own protecting breaker's rating fails closed"


def case_a_replacement_notes_do_not_overclaim_when_unscored():
    """Review nitpick #12: the shared ADD-case spaces.note and
    noncoincident_loads.why_it_matters used to say unconditionally that the
    replacement is "scored separately as heat_pump_replaces_ac" -- true only
    when the A/C circuit is actually identified. On a panel with no matching
    schedule entry, both must say the fifth case is UNSCORED, not silently
    claim a credit or a space answer that case never produces."""
    d0, d1 = dt.date(2025, 5, 31), dt.date(2025, 6, 1)
    rows = ([(d0, hf, 1.0, 0.0) for hf in R.expected_day_hours(d0)]
            + [(d1, hf, 1.0, 0.0) for hf in R.expected_day_hours(d1)])
    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n"
        "  schedule:\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 30, "
        "label: Test circuit}\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        d = S.build_no_solar()
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()

    hpr = next(c for c in d["cases"] if c["case"] == "heat_pump_replaces_ac")
    assert hpr["scored"] is False, hpr
    nc = d["noncoincident_loads"]
    assert nc["existing_ac_ocpd_basis"] == S.AC_NO_MATCH, nc
    assert "reports itself unscored" in nc["why_it_matters"], nc
    assert "applies this credit" not in nc["why_it_matters"], nc
    for c in d["cases"]:
        if c["case"] == "heat_pump_replaces_ac":
            continue
        note = c["spaces"]["note"]
        assert "not scored for this household" in note, note
        assert "is scored separately as heat_pump_replaces_ac, which is " \
            "not blocked" not in note, note
    return "the shared notes report the fifth case unscored rather than overclaiming"


def case_the_other_four_cases_are_unaffected_by_the_fifth():
    """Issue #45 added a fifth case and fixed the shared spaces.note wording;
    nothing else about the four ADD cases may move. Checked against the
    committed artifact -- the CLAUDE.md #9 regeneration gate is what proves
    byte-identity across a real regeneration, but this ties the first four
    cases' own arithmetic to the same nec_220_87 figures those cases have
    always been checked against, independent of the fifth case existing."""
    d = json.loads(S.OUT.read_text())
    nec = d["nec_220_87"]
    add_cases = d["cases"][:4]
    assert [c["case"] for c in add_cases] == [
        "heat_pump_only", "second_evse_only", "heat_pump_and_second_evse",
        "heat_pump_second_evse_and_battery"], add_cases
    assert d["cases"][4]["case"] == "heat_pump_replaces_ac", d["cases"][4]
    for c in add_cases:
        rem = c["remaining_headroom_a"]
        exp = nec["headroom_a"]["vs_service_rating"] - c["fixed_added_load_a"]
        assert _close(rem["measured_basis"]["vs_service_rating"], exp, 1e-3), c
        # none of the four ADD cases' own notes mention a credit -- that
        # arithmetic belongs only to the fifth case
        assert "noncoincident" not in c["note"].lower(), c["note"]
        assert "credit" not in c["spaces"]["note"].lower(), c["spaces"]["note"]
    return "the four ADD cases keep their own arithmetic with a fifth case beside them"


def case_the_noncoincident_rule_is_stated_whole():
    """220.60's second sentence is real 2020-NEC text, verified against Mike
    Holt's Code Forum and EC&M's code-basics coverage of the 2020 revision,
    independent of up.codes' own AI-generated (and disclaimed) summary of the
    section, which is what the previous string here actually was -- it
    widened the subject from 'a motor' to 'a motor or air-conditioning load'
    and replaced 'if it is the largest motor' with a max-of-two selection,
    neither of which is in the real code. The artifact's own rule string
    must be the corrected text, not the AI summary."""
    d = json.loads(S.OUT.read_text())
    nc = d["noncoincident_loads"]
    rule = nc["rule"]
    assert "largest load(s) that will be used at one time" in rule, rule
    assert "Where a motor is part of the noncoincident load" in rule, rule
    assert "125 percent of the motor load shall be used" in rule, rule
    assert "if it is the largest motor" in rule, rule
    # the wrong, AI-summary-derived wording must not survive anywhere
    assert "air-conditioning load, whichever is larger" not in rule, rule
    assert "125 percent of either the motor load" not in rule, rule
    assert rule == S.nec_rule("220.60"), rule
    # the anchor explaining which sentence actually applies is renamed and
    # reworked (issue #45 review): the second sentence sets a floor under a
    # RETAINED noncoincident load and authorizes nothing for one being
    # removed, which is what a replacement case actually does
    which = nc["which_sentence_of_220_60_applies"]
    assert "the_second_sentence_matters_here" not in nc, nc
    assert "FIRST sentence" in which and "SECOND sentence" in which, which
    assert "retain" in which.lower(), which
    assert "remov" in which.lower(), which
    # and the space note no longer asserts a universal the artifact contradicts
    # (issue #45: it stopped being true once heat_pump_replaces_ac needs none)
    for c in d["cases"]:
        note = c["spaces"]["note"]
        assert "every case here needs at least two adjacent free spaces" \
            not in note, note
        if c["case"] == "heat_pump_replaces_ac":
            assert "REUSES the existing" in note, note
            assert "ADDS equipment" not in note, note
        else:
            assert "ADDS equipment" in note, note
            assert "REPLACES the existing A/C" in note, note
            # this household's A/C circuit IS identified, so the ADD cases'
            # note correctly claims the fifth case is scored, not just
            # attempted -- see case_a_replacement_notes_do_not_overclaim_
            # when_unscored for the other branch
            assert "heat_pump_replaces_ac" in note, note
    # the replacement scenario noncoincident_loads names is scored now, not
    # merely flagged -- the artifact says so instead of still saying "not
    # modelled here"
    assert "not modelled here" not in nc["why_it_matters"], nc
    assert "heat_pump_replaces_ac" in nc["why_it_matters"], nc
    return "220.60 is stated whole, and the space note is scoped to the add cases"


def case_the_artifact_says_what_it_cannot_say_about_the_peak():
    """The peak's timestamp and conditions were published and what was running
    was not -- which reads as though nobody asked. Whole-house 15-minute data
    cannot attribute a peak to a load; that is an answer, and it is published
    with what would settle it and with what the series does show."""
    d = json.loads(S.OUT.read_text())
    md = d["maximum_demand"]
    w = md["what_was_running"]
    assert w["verdict"] == "not_determined", w
    assert "WHOLE HOUSE" in w["reading"], w
    assert "submetering" in w["what_would_settle_it"], w
    assert "disaggregate" in w["what_would_settle_it"], w
    shows = w["what_the_series_does_show"]
    rows = shows["intervals_around_the_peak"]
    assert 3 <= len(rows) <= 5, rows
    ts = [r["timestamp_local"] for r in rows]
    assert md["peak_timestamp_local"] in ts, (ts, md["peak_timestamp_local"])
    assert ts == sorted(ts), ts
    peak_row = [r for r in rows
                if r["timestamp_local"] == md["peak_timestamp_local"]][0]
    assert _close(peak_row["gross_kw_lower_bound"], md["peak_kw"]), peak_row
    others = [r["gross_kw_lower_bound"] for r in rows if r is not peak_row]
    assert _close(shows["largest_neighbouring_interval_kw"], max(others)), shows
    assert _close(shows["peak_minus_largest_neighbour_kw"],
                  _rq(md["peak_kw"] - max(others), 4)), shows
    assert shows["peak_minus_largest_neighbour_kw"] > 0, shows
    evse = shows["existing_evse_rated_kw"]
    if evse is None:
        assert shows["peak_minus_existing_evse_rated_kw"] is None, shows
    else:
        assert _close(evse, d["panel"]["existing_evse_kw"]), shows
        assert _close(shows["peak_minus_existing_evse_rated_kw"],
                      _rq(md["peak_kw"] - evse, 4)), shows
        assert "does not account for the interval on its own" in \
            shows["reading"], shows
    # no appliance is named anywhere in the answer: that is the attribution the
    # data cannot make, and naming one is the failure this case guards
    text = json.dumps(w).lower()
    for word in ("oven", "dryer", "air conditioner", "a/c", "pool", "pump",
                 "heater", "range"):
        assert word not in text, f"the peak answer names {word!r}"
    return "the peak's load attribution is published as not determined, with what the series shows"

# ---------------------------------------------------------------------------
# build(), end to end, on a synthetic house
#
# The two cases that run the real build() need the private archive and SKIP in
# CI, which left the whole body of build() -- the assembly, the wiring of every
# figure into the artifact -- unexercised there. This fixture builds a complete
# synthetic year on disk and runs build() against it, so an error injected into
# the assembly fails in CI rather than only on the one machine that holds the
# private data.
#
# It is a synthetic HOUSE, not a synthetic version of this household: the loads
# are flat, the production is a rectangle, and every figure asserted below is
# hand-computable from the two spikes the fixture plants. Nothing here is
# evidence about the real dataset, and nothing about the real dataset is
# reproduced from it.
# ---------------------------------------------------------------------------

SYNTH_START = dt.date(2025, 6, 1)
SYNTH_END = dt.date(2026, 6, 30)          # 395 days, DST both ways inside
SYNTH_BASE_KWH = 0.25                     # 1 kW of house load, every interval
SYNTH_PV_HOURS = range(9, 16)             # 7 producing hours a day
SYNTH_PV_KWH = 2.0                        # per producing hour, before weather
SYNTH_KW_AC = 9.45                        # matches PANEL_YAML's solar.kw_ac

# The night spike sets the measured maximum: 4.0 kWh in a quarter-hour is 16 kW,
# in an hour with no production, so the bound collapses and the peak is a point.
SYNTH_PEAK_DAY = dt.date(2025, 9, 10)
SYNTH_PEAK_HF = 5.75
SYNTH_PEAK_KWH = 4.0
# The daylight spike sets the conservative basis: 4.0 kWh of load against
# 0.5 kWh of PV in that interval is 3.5 kWh imported, and the upper bound adds
# the containing hour's whole 2.0 kWh of production -> (3.5 + 2.0) * 4 = 22 kW.
SYNTH_UPPER_DAY = dt.date(2025, 10, 15)
SYNTH_UPPER_HF = 12.25


def _synth_weather(day):
    """A deterministic, non-monotonic daily scale on production.

    Flat production would leave the daily series with no variance and the
    conservation correlation undefined; a ramp would correlate at 1.0 with its
    own shift. The spike day is pinned at 1.0 so the upper bound below is exact.
    """
    if day == SYNTH_UPPER_DAY:
        return 1.0
    rnd = random.Random((day - SYNTH_START).days * 7919)
    return 0.6 + 0.4 * rnd.random()


def _synth_series():
    """(meter rows, hourly gross by (date, hour), daily derived PV).

    Per interval the meter sees load - pv, split into import and export, so the
    identity the module inverts -- pv = sam - import + export -- holds exactly
    on every hour and the derived series is the fixture's own PV by
    construction.
    """
    rows, sam, daily_pv = [], {}, {}
    day = SYNTH_START
    while day <= SYNTH_END:
        slots = R.expected_day_hours(day)
        per_hour = collections.Counter(int(h) for h in slots)
        pv_day = 0.0
        for hf in slots:
            h = int(hf)
            pv_hour = (SYNTH_PV_KWH * _synth_weather(day)
                       if h in SYNTH_PV_HOURS else 0.0)
            pv_i = pv_hour / per_hour[h]
            load_i = SYNTH_BASE_KWH
            if (day, hf) == (SYNTH_PEAK_DAY, SYNTH_PEAK_HF) or \
                    (day, hf) == (SYNTH_UPPER_DAY, SYNTH_UPPER_HF):
                load_i = SYNTH_PEAK_KWH
            net = load_i - pv_i
            rows.append((day, hf, max(net, 0.0), max(-net, 0.0)))
            sam[(day, h)] = sam.get((day, h), 0.0) + load_i
            pv_day += pv_i
        daily_pv[day] = pv_day
        day += dt.timedelta(days=1)
    return rows, sam, daily_pv


def _write_synth_meter(path, rows):
    """A Green Button 15-minute export in the shape load_intervals() parses."""
    out = ["Name,SYNTHETIC FIXTURE", "",
           "Meter Number,Date,Start Time,End Time,Consumption,Generation,Units"]
    for day, hf, imp, exp in rows:
        t = dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=hf)
        end = t + dt.timedelta(minutes=15)
        out.append(f"SYNTH,{day:%m/%d/%Y},{t:%I:%M %p},{end:%I:%M %p},"
                   f"{imp:.6f},{exp:.6f},kWh")
    path.write_text("\n".join(out) + "\n")


def _write_synth_sam(path, year, sam):
    """One flat 8760-row Enphase export, zero-padded past the last hour."""
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    base = dt.datetime(year, 1, 1)
    vals = []
    for i in range(n):
        ts = base + dt.timedelta(hours=i)
        # inside the window: the hour's real gross load. Before it: the same
        # flat base, which is measurement of a house nobody is asking about.
        # After it: zero, the padding the loader truncates.
        if (ts.date(), ts.hour) in sam:
            vals.append(sam[(ts.date(), ts.hour)])
        elif ts.date() < SYNTH_START:
            vals.append(4 * SYNTH_BASE_KWH)
        else:
            vals.append(0.0)
    path.write_text("kWh\n" + "".join(f"{v:.6f}\n" for v in vals))


def _write_synth_threeway(path, daily_pv, dst_days):
    """Two reference production series over the fixture's own derived PV."""
    lines = [",synthetic_ct,synthetic_feed"]
    for day in sorted(daily_pv):
        if day in dst_days:
            continue
        lines.append(f"{day},{daily_pv[day]:.6f},{daily_pv[day] * 1.02:.6f}")
    path.write_text("\n".join(lines) + "\n")


def case_build_runs_end_to_end_on_a_synthetic_house():
    """The whole of build(), in CI, against inputs whose answers are known.

    Every figure asserted here follows from the fixture by hand: a 16 kW night
    spike in a non-producing hour is the measured maximum and is
    point-determined; a 14 kW daylight spike whose hour made 2.0 kWh is the
    conservative maximum at 22 kW; 395 days satisfies 220.87(1); the two DST
    Sundays are the only uncovered hours. An arithmetic error anywhere in the
    assembly moves one of them.
    """
    rows, sam, daily_pv = _synth_series()
    dst = {d for y in (2025, 2026) for d in R.dst_transition_sundays(y)
           if SYNTH_START <= d <= SYNTH_END}
    assert len(dst) == 2, dst
    real = (S.RAW_DIR, S.THREEWAY)
    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    for year in (2025, 2026):
        _write_synth_sam(raw / f"enphase_sam8760_{year}.csv", year, sam)
    _write_synth_threeway(raw / "threeway.csv", daily_pv, dst)
    hh = _with_household("household:\n  has_new_load_interest: true\n"
                         + PANEL_YAML)
    try:
        S.RAW_DIR, S.THREEWAY = raw, raw / "threeway.csv"
        d = S.build()
    finally:
        S.RAW_DIR, S.THREEWAY = real
        del hh
        td.cleanup()

    # the window and the measured maximum
    prov = d["provenance"]
    assert prov["window_days"] == 395, prov
    assert prov["interval_rows"] == len(rows), prov
    md = d["maximum_demand"]
    assert _close(md["peak_kw"], 16.0), md
    assert md["peak_timestamp_local"] == "2025-09-10 05:45", md
    assert md["peak_coincident"]["point_determined"] is True, md
    assert _close(md["peak_a"], 16.0 * 1000 / 240.0, 1e-4), md

    # 220.87 on that maximum, and its conditions
    nec = d["nec_220_87"]
    assert _close(nec["calculated_load_a"], _rq(16.0 * 1000 / 240.0 * 1.25, 4)), nec
    assert _close(nec["headroom_a"]["vs_service_rating"],
                  _rq(175.0 - nec["calculated_load_a"], 4)), nec
    assert _close(nec["headroom_a"]["binding"],
                  _rq(170.0 - nec["calculated_load_a"], 4)), nec
    assert nec["conditions"]["condition_1"]["verdict"] == "pass", nec
    assert nec["conditions"]["condition_1"]["days_available"] == 395, nec
    assert nec["conditions"]["condition_1_exception_30_day_recording"][
        "available_to_this_service"] is False, nec

    # the conservative basis, and the OTHER branch of the binding-interval
    # description: this fixture's binding interval sits in a COVERED hour, so
    # 15-minute production is the instrument that would settle it
    g = d["gross_reconstruction"]
    assert _close(g["max_upper_bound_kw"], 22.0), g
    b = g["max_upper_bound_binding_interval"]
    assert b["timestamp_local"] == "2025-10-15 12:15", b
    assert b["hour_has_a_production_measurement"] is True, b
    assert g["max_upper_bound_is_set_by_an_hour_with_no_production_measurement"] \
        is False, g
    assert g["intervals_whose_upper_bound_exceeds_the_peak"] == 1, g
    for c in d["cases"]:
        if c.get("scored") is False:
            continue  # heat_pump_replaces_ac, unscored on this no-A/C fixture
        if c["ampacity_verdict"] == "not_determined":
            assert c["what_would_settle_it"].startswith("15-minute PV production")

    # the PV ceiling split: the two DST Sundays are the only uncovered hours,
    # and the export reaches the end of the meter window bar the last hour
    sp = g["pv_ceiling_basis_split"]
    assert sp["nameplate_intervals_by_reason"] == {
        "excluded_dst_day": sum(len(R.expected_day_hours(x)) for x in dst)}, sp
    lag = sp["enphase_coverage_lag"]
    assert lag["enphase_coverage_last_hour"] == "2026-06-30 23:00", lag
    assert lag["meter_window_last_interval"] == "2026-06-30 23:45", lag
    assert _close(lag["lag_hours"], 0.75), lag
    assert lag["lag_intervals"] == 0, lag
    assert lag["gate"]["passed"] is True, lag

    # the conservation check ran on the fixture's own references and passed
    con = g["conservation"]
    assert con["gates_passed"] == con["gates_total"] >= 7, con
    assert con["days_compared"] == 395 - len(dst), con
    assert _close(con["against"]["synthetic_ct"]["residual_pct"], 0.0, 1e-3), con

    # the cases, the busbar and the mitigations all assembled
    assert [c["case"] for c in d["cases"]] == [
        "heat_pump_only", "second_evse_only", "heat_pump_and_second_evse",
        "heat_pump_second_evse_and_battery", "heat_pump_replaces_ac"], d["cases"]
    hp = d["cases"][0]
    assert _close(hp["remaining_headroom_a"]["measured_basis"]["binding"],
                  nec["headroom_a"]["binding"]), hp
    assert hp["spaces"]["physical_fit"] == "not_determined", hp
    assert d["battery_inverter"]["ampacity_leg"] == "fail", d["battery_inverter"]
    assert len(d["mitigations"]) == 3, d["mitigations"]
    # no A/C label in this fixture's schedule: a miss is not a panel without one
    nc = d["noncoincident_loads"]
    assert nc["existing_ac_ocpd_basis"] == S.AC_NO_MATCH, nc
    assert nc["credit_bounds_a"]["high"] is None, nc
    # heat_pump_replaces_ac (issue #45) needs an identified A/C circuit to
    # reuse; with none found in the schedule it reports itself unscored
    # rather than guessing which circuit a replacement would land on
    hpr = d["cases"][4]
    assert hpr["case"] == "heat_pump_replaces_ac", hpr
    assert hpr["scored"] is False, hpr
    assert "spaces" not in hpr and "ampacity_verdict" not in hpr, hpr
    assert hpr["reason"] and S.AC_NO_MATCH in hpr["reason"], hpr
    assert hpr["what_would_settle_it"] == S.AC_SETTLE, hpr
    # and the artifact serializes, which is what main() would write
    assert json.dumps(d, indent=1, sort_keys=True), "the result is not JSON"
    # issue #42's "solar" marker section is new API surface build_no_solar()
    # returns; build()'s own solar path is untouched by that issue (a single
    # early dispatch before panel = load_panel(), see build()'s own source)
    # and must carry no such key -- adding one here would break the
    # byte-identical regeneration CLAUDE.md requires.
    assert "solar" not in d, d
    return ("build() runs end to end on a synthetic year and reproduces every "
            "figure the fixture determines")


# ---------------------------------------------------------------------------
# The no-solar path (issue #42): a household with has_new_load_interest: true
# and no solar: block in its intake. build() dispatches to build_no_solar()
# before panel = load_panel(), so the solar-path cases above never run this
# code and this code never runs theirs.
# ---------------------------------------------------------------------------

NO_SOLAR_PANEL_YAML = """
household:
  has_new_load_interest: true
  has_ev: true
charger:
  kw: 11.5
panel:
  service_rating_a: 175
  busbar_rating_a: 200
  main_breaker_catalog: TESTCO-MAIN01
  enclosure_catalog: TESTCO-ENC02
  enclosure_type: NEMA 1 indoor, meter-main combination
  meter_class: CL100
  assembly_sccr_ka: 22
  breaker_family: Test-Brand BR
  pv_backfeed_a: null
  meter_socket_continuous_a: 170
  spaces: 20
  max_circuits: 40
  schedule:
    - {device: full-size 2-pole, poles: 2, amps: 60, label: Main circuit}
"""


def case_solar_present_reads_the_shape_of_the_file():
    """has_solar is documented (DATA-SOURCES-CHEATSHEET.md) as an
    applicability flag answered by the SHAPE of the intake file, not a
    boolean key -- there is no household.has_solar to read. solar_present()
    is required=False on the solar: block itself, and an explicit null is no
    more informative than the key being absent."""
    for text, expected in [
        ("solar:\n  kw_ac: 5.0\n  inverter_model: x\n  inverter_count: 1\n", True),
        ("household:\n  has_ev: false\n", False),
        ("solar: null\n", False),
        ("", False),
    ]:
        hh = _with_household(text)
        try:
            assert S.solar_present() is expected, (text, expected)
        finally:
            del hh
    return ("solar_present() is true only where the solar: block is present "
            "and non-null, matching the cheatsheet's shape-of-the-file "
            "contract")


def case_build_no_solar_runs_end_to_end_and_reads_no_solar_input():
    """build_no_solar(), in CI, against a synthetic no-solar house.

    S.PVOUTPUT_5MIN/S.THREEWAY are repointed at paths that do not exist at
    all -- FileNotFoundError, not a caught SystemExit, would surface if
    build_no_solar() ever opened either. RAW_DIR carries only the Green
    Button export: no enphase_sam8760_*.csv at all, because that file's mere
    PRESENCE (not its content) now stops the run on its own -- see
    case_the_no_solar_path_fails_closed_on_a_stray_enphase_export, which
    proves that guard directly. Between the two, "asserts no Enphase or
    PVOutput file is read" (issue #42's acceptance criteria) is proven by
    construction rather than by a call-count mock: there is nothing left in
    this fixture's directory that a read of either could possibly touch.

    The fixture also carries a single 16 kW night spike, exactly like
    case_build_runs_end_to_end_on_a_synthetic_house's solar fixture, so the
    same measured maximum is independently verifiable by hand -- and here it
    is also the CONSERVATIVE maximum, because there is nothing to bound.
    """
    start = dt.date(2025, 6, 1)
    end = dt.date(2026, 6, 30)          # 395 days, both seasons, DST both ways
    peak_day, peak_hf, peak_kwh = dt.date(2025, 9, 10), 5.75, 4.0
    rows = []
    day = start
    while day <= end:
        for hf in R.expected_day_hours(day):
            imp = peak_kwh if (day, hf) == (peak_day, peak_hf) else 1.0
            rows.append((day, hf, imp, 0.0))
        day += dt.timedelta(days=1)

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(NO_SOLAR_PANEL_YAML)
    real_raw = S.RAW_DIR
    real_pvo = S.PVOUTPUT_5MIN
    real_3way = S.THREEWAY
    try:
        S.RAW_DIR = raw
        S.PVOUTPUT_5MIN = raw / "MUST_NOT_BE_OPENED_pvoutput.csv"
        S.THREEWAY = raw / "MUST_NOT_BE_OPENED_threeway.csv"
        assert S.solar_present() is False
        d = S.build()
    finally:
        S.RAW_DIR, S.PVOUTPUT_5MIN, S.THREEWAY = real_raw, real_pvo, real_3way
        del hh
        td.cleanup()

    # the applicability marker, and every interval point-determined
    assert d["solar"]["present"] is False, d["solar"]
    md = d["maximum_demand"]
    assert _close(md["peak_kw"], 16.0), md
    assert md["peak_timestamp_local"] == "2025-09-10 05:45", md
    assert md["peak_coincident"]["point_determined"] is True, md
    assert "independent_corroboration" not in md, md
    assert "dst_guard" not in md, md
    g = d["gross_reconstruction"]
    assert g["intervals"] == len(rows), g
    assert g["point_determined_intervals"] == g["intervals"], g
    assert g["point_determined_fraction_pct"] == 100.0, g
    assert g["bounded_intervals"] == 0, g
    assert _close(g["max_lower_bound_kw"], g["max_upper_bound_kw"]), g
    assert g["pv_reconstruction"]["applicable"] is False, g
    assert g["conservation"]["applicable"] is False, g
    # the fields a solar household's artifact carries and this one must not
    for absent in ("pv_ac_ceiling", "pv_ceiling_basis_split",
                   "max_upper_bound_binding_interval"):
        assert absent not in g, (absent, g)

    # 220.87: the Exception WAS open to this service (no renewable system),
    # unlike the solar household, which qualifies under condition (1) with
    # the Exception closed to it
    cond = d["nec_220_87"]["conditions"]["condition_1_exception_30_day_recording"]
    assert cond["available_to_this_service"] is True, cond

    # every case verdict is two-valued: measured and conservative are the
    # same object, and nothing here is ever not_determined on ampacity
    assert [c["case"] for c in d["cases"]] == [
        "heat_pump_only", "second_evse_only", "heat_pump_and_second_evse",
        "heat_pump_second_evse_and_battery", "heat_pump_replaces_ac"], d["cases"]
    # this fixture's schedule carries no A/C label, so heat_pump_replaces_ac
    # (issue #45) reports itself unscored rather than guessing a circuit
    hpr = d["cases"][4]
    assert hpr["scored"] is False, hpr
    for c in d["cases"][:4]:
        rem = c["remaining_headroom_a"]
        assert rem["measured_basis"] == rem["conservative_basis"], c
        assert c["ampacity_verdict"] in ("pass", "fail"), c
        assert c["what_would_settle_it"] is None, c

    # the busbar, panel occupancy and mitigations all assembled
    assert d["battery_inverter"]["verdict"], d["battery_inverter"]
    assert d["panel"]["occupancy"]["spaces_total"] == 20, d["panel"]
    assert len(d["mitigations"]) == 3, d["mitigations"]
    assert json.dumps(d, indent=1, sort_keys=True), "the result is not JSON"
    return ("build_no_solar() runs end to end, reads no Enphase or PVOutput "
            "file, and reports every interval point-determined")


def case_the_no_solar_path_fails_closed_on_unexpected_export():
    """A no-solar household whose meter shows nonzero export contradicts the
    branch it is on -- something is backfeeding a service with no solar:
    block recorded -- so build_no_solar() stops rather than silently netting
    an unexplained credit into gross load."""
    day = dt.date(2025, 6, 1)
    hours = R.expected_day_hours(day)
    rows = [(day, hf, 1.0, 0.0) for hf in hours]
    mid = len(rows) // 2
    rows[mid] = (day, hours[mid], 0.5, 0.5)     # one interval exports 0.5 kWh

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n  schedule: []\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        try:
            S.build_no_solar()
            raise _Reached
        except _Reached:
            raise AssertionError(
                "nonzero export on a no-solar house did not fail closed")
        except SystemExit as e:
            assert "export" in str(e).lower(), e
            assert "solar" in str(e).lower(), e
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()
    return ("a no-solar household whose meter exports anything fails closed "
            "instead of netting an unexplained credit into gross load")


def case_the_no_solar_path_catches_a_negative_export_too():
    """A NEGATIVE export (meter/CT rounding or reverse-flow noise -- not
    physically impossible to record) is just as much a contradiction of the
    no-solar branch as a positive one, and a guard checking `exp > 0.0` would
    let it slip straight through to a "100% zero-export, all point-determined"
    claim the data does not support. The guard checks `!= 0.0` for exactly
    this reason."""
    day = dt.date(2025, 6, 1)
    hours = R.expected_day_hours(day)
    rows = [(day, hf, 1.0, 0.0) for hf in hours]
    mid = len(rows) // 2
    rows[mid] = (day, hours[mid], 1.0, -0.05)   # one interval "exports" -0.05 kWh

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n  schedule: []\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        try:
            S.build_no_solar()
            raise _Reached
        except _Reached:
            raise AssertionError(
                "a negative export on a no-solar house did not fail closed")
        except SystemExit as e:
            assert "export" in str(e).lower(), e
            assert "solar" in str(e).lower(), e
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()
    return ("a negative export on a no-solar household fails closed exactly "
            "like a positive one -- the guard checks != 0.0, not > 0.0")


def case_the_no_solar_path_handles_a_single_season_window():
    """A window that falls wholly inside or wholly outside R.SUMMER_MONTHS
    used to crash noncoincident_loads with ValueError: max() arg is an empty
    sequence -- the exact failure a reviewer reproduced with a summer-only
    and a winter-only 31-day window. This path is specifically the one
    nec_220_87_conditions_no_solar() documents as designed to work on a
    sub-year window (the 30-day Exception is open to it), so a short window
    crashing here is the worst possible place for that bug to live. Both
    all-summer and all-winter windows are exercised; each reports the
    season comparison as not applicable, with a reason, rather than
    raising or silently publishing a fabricated gap."""
    for start, label in ((dt.date(2025, 7, 1), "summer"),
                        (dt.date(2025, 1, 1), "winter")):
        end = start + dt.timedelta(days=30)          # 31 days, one month only
        assert start.month == end.month, (start, end)
        rows = []
        day = start
        while day <= end:
            rows += [(day, hf, 1.0, 0.0) for hf in R.expected_day_hours(day)]
            day += dt.timedelta(days=1)

        td = tempfile.TemporaryDirectory()
        raw = pathlib.Path(td.name)
        _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
        hh = _with_household(
            "household:\n  has_new_load_interest: true\n  has_ev: false\n"
            "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
            "  main_breaker_catalog: TESTCO-MAIN01\n"
            "  enclosure_catalog: TESTCO-ENC02\n"
            "  enclosure_type: NEMA 1 indoor, meter-main "
            "combination\n"
            "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
            "  breaker_family: Test-Brand BR\n"
            "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
            "  spaces: 20\n  max_circuits: 40\n"
            "  schedule:\n"
            "    - {device: full-size 2-pole, poles: 2, amps: 30, "
            "label: Test circuit}\n")
        real_raw = S.RAW_DIR
        try:
            S.RAW_DIR = raw
            d = S.build_no_solar()          # must not raise
        finally:
            S.RAW_DIR = real_raw
            del hh
            td.cleanup()

        ev = d["noncoincident_loads"]["evidence_on_where_the_credit_sits"]
        assert ev["summer_minus_winter_peak_kw"] is None, (label, ev)
        assert ev["season_comparison_not_applicable_reason"], (label, ev)
        if label == "summer":
            assert ev["max_summer_month_peak_kw"] is not None, ev
            assert ev["max_winter_month_peak_kw"] is None, ev
        else:
            assert ev["max_winter_month_peak_kw"] is not None, ev
            assert ev["max_summer_month_peak_kw"] is None, ev
    return ("a window falling wholly inside or outside the summer months "
            "no longer crashes noncoincident_loads, and reports the season "
            "comparison as not applicable with a reason")


def case_the_no_solar_path_narrative_matches_a_failed_condition_1():
    """A window short of the 1-year requirement must not claim, in the same
    breath, that condition (1) is met or that "a full year ... is what this
    household has" -- both false once condition_1.verdict is fail. 181 days
    (the exact width a reviewer used to catch this) spans both seasons, so
    only the days-vs-365 shortfall is under test here, not the season split
    covered above."""
    start = dt.date(2025, 5, 1)
    end = start + dt.timedelta(days=180)             # 181 days total
    rows = []
    day = start
    while day <= end:
        rows += [(day, hf, 1.0, 0.0) for hf in R.expected_day_hours(day)]
        day += dt.timedelta(days=1)

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n"
        "  schedule:\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 30, "
        "label: Test circuit}\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        d = S.build_no_solar()
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()

    nec = d["nec_220_87"]
    assert nec["conditions"]["condition_1"]["days_available"] == 181, nec
    assert nec["conditions"]["condition_1"]["verdict"] == "fail", nec
    strengthens = nec["conditions"]["condition_1_exception_30_day_recording"][
        "why_it_strengthens_rather_than_weakens"]
    window_note = nec["window_note"]
    for banned, text in (("met outright", strengthens),
                        ("stronger basis", strengthens),
                        ("stronger basis", window_note),
                        ("is what this household has", window_note)):
        assert banned not in text, (banned, text)
    return ("the no-solar path's narrative text no longer claims condition "
            "(1) is met or a full year is what this household has when the "
            "verdict itself says otherwise")


def case_the_no_solar_path_fails_closed_on_a_stray_enphase_export():
    """A stray enphase_sam8760_*.csv sitting beside the meter export
    contradicts a no-solar household -- a genuine Enphase consumption-CT
    export is not something a house with no array holds -- so build_no_solar()
    stops on its mere PRESENCE. The file's content is garbage here on purpose:
    the guard is a glob(), not a read, and this proves it needs no valid
    content to fire."""
    day = dt.date(2025, 6, 1)
    rows = [(day, hf, 1.0, 0.0) for hf in R.expected_day_hours(day)]

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    (raw / "enphase_sam8760_2025.csv").write_text("this is not a valid export")
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n  schedule: []\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        try:
            S.build_no_solar()
            raise _Reached
        except _Reached:
            raise AssertionError(
                "a stray enphase_sam8760 export on a no-solar house did not "
                "fail closed")
        except SystemExit as e:
            assert "enphase_sam8760" in str(e), e
            assert "solar" in str(e).lower(), e
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()
    return ("a stray Enphase consumption-CT export on a no-solar household "
            "fails closed on its filename alone, with no read of its content")


def case_the_no_solar_path_respects_has_ev_too():
    """has_ev is a second, independent applicability flag on the no-solar
    path exactly as it is on the solar path (module docstring, "Whether the
    question is asked at all"): false switches off the second-EVSE cases and
    mitigations, not the heat-pump or battery ones. Spans a non-summer and a
    summer day so the noncoincident-loads season split has both buckets."""
    d0, d1 = dt.date(2025, 5, 31), dt.date(2025, 6, 1)
    rows = ([(d0, hf, 1.0, 0.0) for hf in R.expected_day_hours(d0)]
            + [(d1, hf, 1.0, 0.0) for hf in R.expected_day_hours(d1)])

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 175\n  busbar_rating_a: 200\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n"
        "  schedule:\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 30, "
        "label: Test circuit}\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        d = S.build_no_solar()
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()

    assert [c["case"] for c in d["cases"]] == [
        "heat_pump_only", "heat_pump_and_battery", "heat_pump_replaces_ac"], \
        d["cases"]
    assert d["panel"]["existing_evse_kw"] is None, d["panel"]
    items = {s["item"] for s in d["scenarios_not_applicable"]}
    assert "second_evse_only" in items, d["scenarios_not_applicable"]
    assert d["mitigations"] and all(
        m["mitigation"] != "EVSE load sharing" for m in d["mitigations"]), \
        d["mitigations"]
    return "the no-solar path's has_ev contract matches the solar path's"


def case_the_no_solar_path_reports_a_shortfall_not_headroom():
    """A measured maximum that already exceeds the service rating is a
    SHORTFALL, not spare headroom -- the same wording the solar path's
    case() uses on its measured basis, exercised here on the no-solar path's
    single basis (fail on ampacity, the case's own remaining_is text names it
    a shortfall rather than a negative number with no explanation)."""
    d0, d1 = dt.date(2025, 5, 31), dt.date(2025, 6, 1)
    peak_day, peak_hf = d1, R.expected_day_hours(d1)[10]
    rows = ([(d0, hf, 1.0, 0.0) for hf in R.expected_day_hours(d0)]
            + [(d1, hf, 40.0 if hf == peak_hf else 1.0, 0.0)
               for hf in R.expected_day_hours(d1)])

    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    hh = _with_household(
        "household:\n  has_new_load_interest: true\n  has_ev: false\n"
        "panel:\n  service_rating_a: 50\n  busbar_rating_a: 60\n"
        "  main_breaker_catalog: TESTCO-MAIN01\n"
        "  enclosure_catalog: TESTCO-ENC02\n"
        "  enclosure_type: NEMA 1 indoor, meter-main "
        "combination\n"
        "  meter_class: CL100\n  assembly_sccr_ka: 22\n"
        "  breaker_family: Test-Brand BR\n"
        "  pv_backfeed_a: null\n  meter_socket_continuous_a: null\n"
        "  spaces: 20\n  max_circuits: 40\n"
        "  schedule:\n"
        "    - {device: full-size 2-pole, poles: 2, amps: 30, "
        "label: Test circuit}\n")
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw
        d = S.build_no_solar()
    finally:
        S.RAW_DIR = real_raw
        del hh
        td.cleanup()

    # 40 kWh in one quarter-hour is 160 kW; at 125% that is far past a 50 A
    # service, so even the bare heat_pump_only case (no fixed load added) is
    # already a shortfall
    hp = d["cases"][0]
    assert hp["case"] == "heat_pump_only", d["cases"]
    assert hp["remaining_headroom_a"]["measured_basis"]["binding"] < 0, hp
    assert hp["ampacity_verdict"] == "fail", hp
    assert "SHORTFALL" in hp["remaining_is"], hp
    assert "no heat pump fits" in hp["remaining_is"], hp
    return ("a measured maximum past the service rating reports a SHORTFALL "
            "on the no-solar path's single basis, not a bare negative number")


def case_the_two_paths_keep_their_shared_blocks_in_lockstep():
    """build_no_solar() is ~70% hand-copied from build(): the battery dict,
    the case()-closure's spaces sub-dict, and the EVSE-sharing mitigation are
    each written out twice rather than shared, and a future NEC fix applied
    to one twin could silently miss the other. Nothing here can stop that
    from happening -- only a refactor could -- but this case makes a
    schema-level drift SHOW UP: it runs a matched pair of has_ev: true
    fixtures, one with solar and one without, through build() and
    build_no_solar() respectively, and asserts the KEY SETS of the blocks
    that are meant to be copy-identical still match. Leaf VALUES are not
    compared -- the two paths derive their numbers from different
    reconstructions on purpose -- only the shape.
    """
    rows, sam, daily_pv = _synth_series()
    dst = {d for y in (2025, 2026) for d in R.dst_transition_sundays(y)
           if SYNTH_START <= d <= SYNTH_END}
    real = (S.RAW_DIR, S.THREEWAY)
    td = tempfile.TemporaryDirectory()
    raw = pathlib.Path(td.name)
    _write_synth_meter(raw / "Electric_15_Minute_synthetic.csv", rows)
    for year in (2025, 2026):
        _write_synth_sam(raw / f"enphase_sam8760_{year}.csv", year, sam)
    _write_synth_threeway(raw / "threeway.csv", daily_pv, dst)
    hh = _with_household("household:\n  has_new_load_interest: true\n"
                         + PANEL_YAML)
    try:
        S.RAW_DIR, S.THREEWAY = raw, raw / "threeway.csv"
        solar = S.build()
    finally:
        S.RAW_DIR, S.THREEWAY = real
        del hh
        td.cleanup()

    day = dt.date(2025, 6, 1)
    no_solar_rows = [(day, hf, 1.0, 0.0) for hf in R.expected_day_hours(day)]
    td2 = tempfile.TemporaryDirectory()
    raw2 = pathlib.Path(td2.name)
    _write_synth_meter(raw2 / "Electric_15_Minute_synthetic.csv", no_solar_rows)
    hh2 = _with_household(NO_SOLAR_PANEL_YAML)
    real_raw = S.RAW_DIR
    try:
        S.RAW_DIR = raw2
        no_solar = S.build_no_solar()
    finally:
        S.RAW_DIR = real_raw
        del hh2
        td2.cleanup()

    b_solar, b_no_solar = solar["battery_inverter"], no_solar["battery_inverter"]
    assert set(b_solar) == set(b_no_solar), (set(b_solar), set(b_no_solar))
    assert (set(b_solar["busbar_120_percent"]) ==
            set(b_no_solar["busbar_120_percent"])), b_no_solar
    assert (set(b_solar["busbar_120_percent"]["position_condition"]) ==
            set(b_no_solar["busbar_120_percent"]["position_condition"])), \
        b_no_solar
    assert set(b_solar["sum_rule"]) == set(b_no_solar["sum_rule"]), b_no_solar

    solar_cases = {c["case"]: c for c in solar["cases"]}
    no_solar_cases = {c["case"]: c for c in no_solar["cases"]}
    assert set(solar_cases) == set(no_solar_cases), (
        set(solar_cases), set(no_solar_cases))
    for name in solar_cases:
        # heat_pump_replaces_ac (issue #45) has a different shape when
        # unscored (neither fixture here carries an A/C label) -- the shared
        # shape claim this case exists to check still holds, one level up
        assert set(solar_cases[name]) == set(no_solar_cases[name]), name
        if "spaces" in solar_cases[name]:
            assert (set(solar_cases[name]["spaces"]) ==
                    set(no_solar_cases[name]["spaces"])), name

    def _by_name(mitigations, name):
        hits = [m for m in mitigations if m["mitigation"] == name]
        assert len(hits) == 1, (name, mitigations)
        return hits[0]

    sharing_solar = _by_name(solar["mitigations"], "EVSE load sharing")
    sharing_no_solar = _by_name(no_solar["mitigations"], "EVSE load sharing")
    assert set(sharing_solar) == set(sharing_no_solar), (
        set(sharing_solar), set(sharing_no_solar))

    for k in ("service_voltage_v", "voltage_basis", "timezone_handling"):
        assert k in solar["provenance"], k
        assert k in no_solar["provenance"], k
    return ("the battery dict, the per-case spaces sub-dict, the EVSE-sharing "
            "mitigation, and the provenance tail keep the same key shape "
            "between the solar and no-solar paths")


CASES = [
    case_220_87_chain_on_hand_computed_inputs,
    case_220_87_socket_step_follows_the_three_socket_states,
    case_three_valued_verdict_needs_both_bases,
    case_remaining_headroom_omits_an_absent_socket,
    case_amps_uses_the_240_v_service_basis,
    case_evse_is_a_continuous_load,
    case_busbar_120_percent_rule_fails_the_battery,
    case_busbar_120_percent_rule_passes_a_smaller_main,
    case_the_busbar_position_condition_is_evaluated_not_ignored,
    case_busbar_carries_both_legs_of_the_rule,
    case_the_busbar_ampacity_leg_is_three_valued,
    case_the_sum_of_breakers_rule_is_three_valued,
    case_the_battery_verdict_needs_both_legs,
    case_the_two_zero_backfeeds_are_told_apart,
    case_physical_fit_is_three_valued,
    case_fall_back_day_produces_no_phantom_peak,
    case_spring_forward_day_is_short_and_still_clean,
    case_day_lengths_match_the_tariff_clock,
    case_dst_dates_are_derived_not_listed,
    case_zero_padding_is_truncated_not_treated_as_data,
    case_an_all_zero_enphase_export_fails_closed,
    case_enphase_loader_checks_shape_and_reads_the_trailing_year,
    case_only_an_explicit_false_new_load_flag_disables_the_analysis,
    case_a_false_new_load_flag_reads_no_input_at_all,
    case_a_false_new_load_flag_writes_the_stub_artifact,
    case_a_true_new_load_flag_still_fails_closed_on_the_panel,
    case_absent_service_rating_fails_closed,
    case_panel_intake_reads_every_required_field,
    case_every_required_panel_field_still_fails_closed,
    case_a_null_meter_socket_rating_runs_end_to_end,
    case_an_absent_meter_socket_key_is_not_a_socket_without_a_rating,
    case_a_null_pv_backfeed_runs_end_to_end,
    case_an_absent_pv_backfeed_key_is_not_a_surveyed_zero,
    case_impossible_panel_values_fail_closed_by_field,
    case_non_finite_panel_values_fail_closed,
    case_a_nan_backfeed_does_not_produce_a_false_safety_pass,
    case_spaces_and_max_circuits_reject_lossy_coercion,
    case_panel_float_fields_reject_boolean_coercion,
    case_panel_domain_checks_accept_the_edges_that_are_real,
    case_free_text_panel_fields_fail_closed_on_blank_or_absurd_length,
    case_meter_class_format_fails_closed,
    case_assembly_sccr_ka_must_be_positive,
    case_meter_socket_requires_a_meter_main_enclosure,
    case_meter_socket_accepts_the_unhyphenated_spelling_too,
    case_pv_backfeed_must_match_a_schedule_breaker,
    case_KNOWN_LIMITATION_an_unrelated_same_rated_breaker_still_passes,
    case_unrecognized_breaker_position_fails_closed,
    case_schedule_device_and_label_must_be_non_empty_text,
    case_a_schedule_larger_than_its_enclosure_fails_closed,
    case_breaker_positions_are_read_from_the_intake,
    case_pole_counting_handles_int_and_list_amps,
    case_malformed_schedule_entries_fail_closed,
    case_breaker_geometry_rejects_non_finite_or_non_positive_poles,
    case_breaker_geometry_rejects_non_finite_or_non_positive_amps,
    case_panel_occupancy_counts_spaces_poles_and_ocpd,
    case_gross_is_exact_only_where_nothing_was_produced,
    case_the_pv_ceiling_is_the_inverter_nameplate,
    case_an_observed_pv_maximum_above_the_nameplate_fails_closed,
    case_an_absent_pv_nameplate_stops_the_run,
    case_the_battery_position_leg_is_its_own_input,
    case_uncovered_hours_take_the_nameplate_ceiling_not_an_empirical_one,
    case_the_zero_padded_tail_and_the_dst_days_take_the_nameplate_path,
    case_the_ceiling_split_must_account_for_every_nameplate_interval,
    case_conservation_residual_is_computed_and_bounded,
    case_conservation_gates_are_declared_with_thresholds,
    case_a_rescaled_series_fails_the_residual_gate,
    case_a_shifted_series_fails_the_mae_and_correlation_gates,
    case_a_short_overlap_fails_the_day_count_gate,
    case_the_conservation_reading_follows_the_gates,
    case_the_enphase_peak_invariant_fails_closed,
    case_only_match_refuses_zero_or_two_candidates,
    case_artifact_round_trips_byte_identically,
    case_artifact_is_internally_consistent,
    case_artifact_publishes_no_verdict_the_data_does_not_support,
    case_artifact_states_both_legs_of_the_busbar_rule,
    case_artifact_bounds_pv_on_the_inverter_nameplate,
    case_artifact_gates_the_conservation_check,
    case_artifact_battery_position_is_not_the_pv_breakers,
    case_artifact_labels_the_nullable_panel_fields,
    case_artifact_publishes_no_bare_boolean_judgement,
    case_every_optional_intake_read_distinguishes_absent_from_null,
    case_artifact_reports_physical_fit_not_a_boolean,
    case_artifact_states_the_battery_charging_basis_from_the_datasheet,
    case_the_load_sharing_mitigation_separates_the_amps_from_the_breaker,
    case_no_uncitable_breaker_claim_survives_anywhere,
    case_artifact_publishes_the_pv_ceiling_basis_split,
    case_artifact_sum_rule_counts_the_proposed_breaker,
    case_artifact_carries_the_scoping_caveat,
    case_artifact_carries_no_identifiers,
    case_the_cheatsheet_tiers_every_field_it_declares,
    case_no_private_only_intake_value_reaches_the_artifact,
    case_the_private_leak_scan_catches_a_planted_value,
    case_the_artifact_aggregates_the_panel_schedule_away,
    case_every_nec_citation_comes_from_one_declared_table,
    case_the_220_87_conditions_are_published_with_the_right_minimum,
    case_the_busbar_rule_shows_both_source_figures,
    case_the_conservative_basis_names_the_interval_that_sets_it,
    case_the_coverage_lag_is_published_and_gated,
    case_the_artifact_flags_an_unmeasured_binding_hour,
    case_mitigations_carry_both_bases_and_a_verdict,
    case_the_existing_ac_ocpd_is_three_valued,
    case_existing_ac_ocpd_rejects_a_non_two_pole_match,
    case_heat_pump_replacement_case_shows_the_full_220_60_arithmetic,
    case_heat_pump_replacement_case_verdict_spans_the_credit_and_envelope_axes,
    case_heat_pump_replacement_case_pass_does_not_need_the_nameplate,
    case_a_recorded_nameplate_never_changes_a_zero_credit_pass,
    case_heat_pump_replacement_case_distinguishes_null_from_absent,
    case_heat_pump_replacement_case_is_never_space_blocked,
    case_heat_pump_replacement_case_is_unscored_without_an_identified_circuit,
    case_heat_pump_replacement_case_caps_the_solved_mca_at_the_existing_breaker,
    case_heat_pump_replacement_case_conductor_cap_covers_the_credit_scenarios_too,
    case_an_implausible_nameplate_reading_fails_closed,
    case_a_replacement_notes_do_not_overclaim_when_unscored,
    case_the_other_four_cases_are_unaffected_by_the_fifth,
    case_the_noncoincident_rule_is_stated_whole,
    case_the_artifact_says_what_it_cannot_say_about_the_peak,
    case_build_runs_end_to_end_on_a_synthetic_house,
    case_charger_kw_is_read_only_where_there_is_an_ev,
    case_a_household_with_no_ev_still_gets_its_panel_answer,
    case_an_absent_ev_flag_reproduces_the_committed_artifact,
    case_solar_present_reads_the_shape_of_the_file,
    case_build_no_solar_runs_end_to_end_and_reads_no_solar_input,
    case_the_no_solar_path_fails_closed_on_unexpected_export,
    case_the_no_solar_path_catches_a_negative_export_too,
    case_the_no_solar_path_handles_a_single_season_window,
    case_the_no_solar_path_narrative_matches_a_failed_condition_1,
    case_the_no_solar_path_fails_closed_on_a_stray_enphase_export,
    case_the_no_solar_path_respects_has_ev_too,
    case_the_no_solar_path_reports_a_shortfall_not_headroom,
    case_the_two_paths_keep_their_shared_blocks_in_lockstep,
]


def _cases_defined_here():
    """Every case_* function this module defines, by name."""
    return {name for name, obj in globals().items()
            if name.startswith("case_") and callable(obj)}


def main():
    # The list is hand-maintained, so a case added and not listed would sit in
    # the file looking like coverage and never run. Checked both ways.
    listed = [c.__name__ for c in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    unlisted = sorted(_cases_defined_here() - set(listed))
    assert not unlisted, (
        f"case function(s) defined but not in CASES, so they never run: "
        f"{unlisted}")

    real_path, real_cache = HH.PATH, HH._cache
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {case.__name__}: {msg}")
            ran += 1
        except SkipCase as e:
            # A case that cannot run here says so; it never reads as green.
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
        except SystemExit as e:
            # A fail-closed raise the case did not expect. It is a failure of
            # that case, not a reason to abandon the ones after it -- an
            # uncaught SystemExit used to abort the run with every remaining
            # case unreported and the exit status of a clean pass.
            print(f"FAIL  {case.__name__}: unexpected SystemExit: {e}")
            failures += 1
        except Exception as e:                     # noqa: BLE001 -- see above
            print(f"FAIL  {case.__name__}: {type(e).__name__}: {e}")
            failures += 1
        finally:
            HH.PATH, HH._cache = real_path, real_cache
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
