#!/usr/bin/env python3
"""Unit guards for service_headroom.py.

The full run needs the private Green Button export and the private Enphase
consumption-CT files, which exist on one machine. Everything that could be wrong
in a way that matters -- the NEC arithmetic, the DST day-length handling, the
zero-padding truncation, the fail-closed intake read, the panel-schedule
geometry -- is arithmetic or parsing, and all of it is exercised here against
synthetic fixtures. No private data; runs in CI.

The two cases worth naming: a naive hourly aggregation of this dataset reports a
21.4 kW peak that never happened, because the fall-back Sunday's repeated hour
carries eight 15-minute intervals covering two real hours; and the current-year
Enphase export is zero-padded into the future, so its tail is not measurement.
Both are tested directly rather than trusted.

Run from the repo root:  ./.venv/bin/python analysis/test_service_headroom.py
"""
import ast
import datetime as dt
import json
import pathlib
import random
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import household as HH
import rates as R
import service_headroom as S

EPS = 1e-9


def _close(a, b, eps=EPS):
    return abs(float(a) - float(b)) <= eps


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
  pv_backfeed_a: 50
  meter_socket_continuous_a: 170
  pv_breaker_position: bottom
  spaces: 20
  max_circuits: 40
  schedule:
    - {device: full-size 2-pole, poles: 2, amps: 60, label: Car charger}
    - {device: tandem, poles: 2, amps: [20, 20], label: Kitchen}
    - {device: quad, poles: 4, amps: [15, 20, 20, 15], label: Kitchen / dining}
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
    assert steps[1]["inputs"]["code_minimum_days"] == 30
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
    # The household's own figures: 76.4583 A of binding headroom on the
    # measured basis, 43.9688 A on the conservative upper-bound basis.
    measured = {"service": 81.4583, "meter_socket": 76.4583}
    conservative = {"service": 48.9688, "meter_socket": 43.9688}

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
    v, m, c = verdict(119.8958)
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
    # a Powerwall 3 at 11.5 kW needs a 60 A backfeed breaker, so it does not fit
    breaker = S.standard_circuit_for(S.amps(S.BATTERY_INVERTER_KW))
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
    assert len(p["schedule"]) == 3
    return "load_panel pulls every panel field through the fail-closed accessor"


def case_every_required_panel_field_still_fails_closed():
    """Only the two documented-nullable fields became optional. Everything the
    panel answer actually rests on still stops the run when it is absent."""
    for key in ("service_rating_a: 175", "busbar_rating_a: 200", "spaces: 20",
                "max_circuits: 40"):
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
            S.breaker_geometry(entry)
            raise AssertionError(f"accepted a malformed entry: {entry}")
        except SystemExit as e:
            assert needle in str(e), (needle, str(e))
    return "malformed tandem/quad entries stop the run rather than miscounting"


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
    ceiling = {h: 0.0 for h in range(24)}
    ceiling.update({12: 2.0, 13: 2.0})
    env = S.gross_envelope(rows, pv, ceiling, 8.0)
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
    env = S.gross_envelope([(d, 12.0, 0.0, 0.0)], {(d, 12): 99.0},
                           {h: 99.0 for h in range(24)}, 9.45)
    assert _close(env[0][3], 9.45 * 0.25 * 4.0), env
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


def case_missing_enphase_hours_fall_back_to_the_empirical_ceiling():
    d = dt.date(2026, 1, 15)
    rows = [(d, 1.0, 1.0, 0.0), (d, 12.0, 1.0, 0.0)]
    ceiling = {h: 0.0 for h in range(24)}
    ceiling[12] = 6.0
    env = S.gross_envelope(rows, {}, ceiling, 8.0)
    # 01:00 has never produced, so the bound still collapses there ...
    assert env[0][5] is True, env[0]
    # ... and midday falls back to the largest production ever seen in that hour
    assert _close(env[1][3], (1.0 + min(6.0, 2.0)) * 4.0), env[1]
    return "hours the Enphase file does not cover fall back to the per-hour ceiling"


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
    assert _close(a["correlation"], 1.0, 1e-4), a
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
    ok = S.enphase_peak_invariant(17.819, "2025-07-03 16:00", 17.96, 24.77)
    assert [c["passed"] for c in ok["checks"]] == [True, True], ok
    assert _close(ok["margin_below_the_headline_peak_kw"], 0.141, 1e-6), ok
    assert _close(ok["margin_below_the_envelope_top_kw"], 6.951, 1e-6), ok
    # above the headline peak but inside the envelope: the physics holds and
    # the publication precondition does not
    try:
        S.enphase_peak_invariant(20.0, "2025-07-03 16:00", 17.96, 24.77)
        raise AssertionError("an hourly mean above the headline peak was published")
    except SystemExit as e:
        assert "enphase_hourly_max_within_the_headline_peak" in str(e), e
        assert "enphase_hourly_max_within_the_envelope" not in str(e), e
        assert "optimistic" in str(e), e
    # above the envelope too: the instruments are not describing the same house
    try:
        S.enphase_peak_invariant(30.0, "2025-07-03 16:00", 17.96, 24.77)
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
    assert nec["measurement_days"] >= S.NEC_220_87_MIN_DAYS, nec
    assert [c["case"] for c in d["cases"]] == [
        "heat_pump_only", "second_evse_only", "heat_pump_and_second_evse",
        "heat_pump_second_evse_and_battery"], d["cases"]
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
        assert v == S.ampacity_verdict(m, cons), c
        if v == "not_determined":
            assert c["what_would_settle_it"], c
            assert "15-minute" in c["what_would_settle_it"], c
            assert "never metered" in c["what_would_settle_it"], c
        else:
            assert c["what_would_settle_it"] is None, c
    # this household: a second 48 A EVSE is exactly the case that flips
    by_name = {c["case"]: c for c in d["cases"]}
    assert by_name["second_evse_only"]["ampacity_verdict"] == "not_determined"
    assert by_name["heat_pump_only"]["ampacity_verdict"] == "pass"
    assert by_name["heat_pump_second_evse_and_battery"]["ampacity_verdict"] == "fail"
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
    # the two that DO distinguish are the two with three published states
    assert presence == {"panel.pv_backfeed_a", "panel.meter_socket_continuous_a"}, \
        presence
    return f"all {len(optional)} optional intake reads handle absent vs null explicitly"


def case_artifact_reports_physical_fit_not_a_boolean():
    """Finding C's exit: `fits_without_panel_work` claimed adjacency the panel
    schedule cannot establish. This household is short on the COUNT, which is
    determinable, so the answer is a fail either way -- but it has to be a fail
    for the reason the data supports."""
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
        # this household: 1 free space, every case wants at least two
        assert sp["full_size_spaces_required"] > sp["spaces_free"], c
        assert sp["physical_fit"] == "fail", c
        assert sp["what_would_settle_it"] is None, sp
        assert "adjacency is not in the data" in sp["physical_fit_basis"], sp
    return "physical fit is three-valued in the artifact and fails on the count here"


def case_artifact_states_the_battery_charging_assumption():
    """Finding A. 11.5 kW is the only power rating this project records for the
    unit, and research/battery-research-notes.md does not split charge from
    discharge. The figure stays; asserting it as a charging specification does
    not."""
    d = json.loads(S.OUT.read_text())
    v = d["added_load_code_values"]
    basis = v["battery_charging_basis"]
    assert "assumption" in basis, basis
    assert "battery-research-notes" in basis, basis
    assert "conservative" in basis, basis
    assert "would draw less" in basis, basis
    # the old sentence asserted it as fact; that phrasing must not come back
    assert "kW grid charging =" not in basis, basis
    nd = v["battery_charging_not_determined"]
    assert nd.startswith("NOT DETERMINED"), nd
    assert "AC CHARGE INPUT" in nd, nd
    assert "nameplate or datasheet" in nd, nd
    # the number itself is unchanged and still reproduces from the constant
    assert _close(v["battery_charging_a"],
                  S.amps(S.BATTERY_INVERTER_KW) * S.NEC_625_42_FACTOR, 1e-2), v
    assert _close(S.BATTERY_INVERTER_KW, 11.5)
    # and the case that carries it says the same thing rather than its own
    batt_case = [c for c in d["cases"] if "battery" in c["case"]][0]
    assert "assumption" in batt_case["note"], batt_case["note"]
    # the busbar leg is what fails, and it still does
    assert d["battery_inverter"]["verdict"] == "FAILS as the panel stands"
    assert d["battery_inverter"]["ampacity_leg"] == "fail"
    return "the battery charging basis is published as an assumption, not a fact"


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
    case_panel_domain_checks_accept_the_edges_that_are_real,
    case_a_schedule_larger_than_its_enclosure_fails_closed,
    case_breaker_positions_are_read_from_the_intake,
    case_pole_counting_handles_int_and_list_amps,
    case_malformed_schedule_entries_fail_closed,
    case_panel_occupancy_counts_spaces_poles_and_ocpd,
    case_gross_is_exact_only_where_nothing_was_produced,
    case_the_pv_ceiling_is_the_inverter_nameplate,
    case_an_observed_pv_maximum_above_the_nameplate_fails_closed,
    case_an_absent_pv_nameplate_stops_the_run,
    case_the_battery_position_leg_is_its_own_input,
    case_missing_enphase_hours_fall_back_to_the_empirical_ceiling,
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
    case_artifact_states_the_battery_charging_assumption,
    case_artifact_sum_rule_counts_the_proposed_breaker,
    case_artifact_carries_the_scoping_caveat,
    case_artifact_carries_no_identifiers,
]


def main():
    real_path, real_cache = HH.PATH, HH._cache
    ran = failures = 0
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
        finally:
            HH.PATH, HH._cache = real_path, real_cache
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
