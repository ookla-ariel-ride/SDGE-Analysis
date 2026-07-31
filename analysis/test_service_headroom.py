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


def case_220_87_chain_on_hand_computed_inputs():
    # 12.000 kW at 240 V is 50.000 A; x1.25 is 62.500 A; against a 200 A
    # service that leaves 137.500 A, and against a 150 A socket 87.500 A.
    steps = S.nec_220_87_steps(12.0, 200.0, 150.0, 400)
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


def case_220_87_omits_the_socket_step_when_none_is_recorded():
    """A null meter-socket rating means the constraint does not apply. The row
    is omitted, not emitted with a null on both sides of a subtraction."""
    steps = S.nec_220_87_steps(12.0, 200.0, None, 400)
    assert [s["step"] for s in steps] == [1, 2, 3], steps
    for s in steps:
        assert s["result_a"] is not None, s
        assert all(v is not None for v in s["inputs"].values()), s
    assert _close(steps[2]["result_a"], 137.5), steps[2]
    return "a null meter-socket rating drops step 4 instead of computing on None"


def case_three_valued_verdict_needs_both_bases():
    # The household's own figures: 76.4583 A of binding headroom on the
    # measured basis, 43.9688 A on the conservative upper-bound basis.
    measured = {"service": 81.4583, "meter_socket": 76.4583}
    conservative = {"service": 48.9688, "meter_socket": 43.9688}

    def verdict(fixed_a):
        m = S.remaining_headroom(measured, fixed_a)
        c = S.remaining_headroom(conservative, fixed_a)
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
    alone -- never a None dragged into min()."""
    r = S.remaining_headroom({"service": 100.0}, 60.0)
    assert r["vs_meter_socket"] is None, r
    assert _close(r["vs_service_rating"], 40.0) and _close(r["binding"], 40.0), r
    r2 = S.remaining_headroom({"service": 100.0, "meter_socket": 95.0}, 60.0)
    assert _close(r2["vs_meter_socket"], 35.0) and _close(r2["binding"], 35.0), r2
    return "an absent meter socket drops out of the binding headroom entirely"


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
    ok = S.busbar_120_percent(200.0, 100.0, 50.0, True, "bottom", "top")
    assert ok["position_condition"]["verdict"] == "pass", ok
    assert "conjunctive" in ok["position_condition"]["requirement"], ok
    assert "both must hold" in ok["remaining_backfeed_is_the_ampacity_leg_only"]
    bad = S.busbar_120_percent(200.0, 100.0, 50.0, True, "top", "top")
    assert bad["position_condition"]["verdict"] == "fail", bad
    # the arithmetic is identical in both: the position leg is what differs
    assert _close(ok["remaining_backfeed_a"], bad["remaining_backfeed_a"]), (ok, bad)
    return "busbar_120_percent reports the position condition beside the arithmetic"


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
    return "the battery verdict is conjunctive over the ampacity and position legs"


def case_an_undeclared_backfeed_source_spends_no_allowance():
    """pv_backfeed_a: null means nothing backfeeds the panel -- 0 A spent, and
    the artifact says so rather than printing a bare measured-looking zero."""
    b = S.busbar_120_percent(200.0, 175.0, 0.0, False)
    assert _close(b["remaining_backfeed_a"], 65.0), b
    assert b["existing_pv_backfeed_declared"] is False, b
    assert "no existing backfeed source was declared" in b["existing_pv_backfeed_note"]
    declared = S.busbar_120_percent(200.0, 175.0, 0.0, True)
    assert declared["existing_pv_backfeed_declared"] is True, declared
    assert "read off" in declared["existing_pv_backfeed_note"], declared
    return "an undeclared backfeed source spends 0 A and is labelled as undeclared"


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
    # the whole chain build() runs on that field, in order
    steps = S.nec_220_87_steps(12.0, p["service_rating_a"],
                               p["meter_socket_continuous_a"], 400)
    assert [s["step"] for s in steps] == [1, 2, 3], steps
    avail = S.availability(p["service_rating_a"],
                           p["meter_socket_continuous_a"], steps[1]["result_a"])
    assert set(avail) == {"service"}, avail
    rem = S.remaining_headroom(avail, 60.0)
    assert rem["vs_meter_socket"] is None, rem
    assert _close(rem["binding"], 175.0 - 62.5 - 60.0), rem
    assert S.ampacity_verdict(rem["binding"], rem["binding"]) == "pass"
    return "a null meter-socket rating runs through the whole chain, socket omitted"


def case_a_null_pv_backfeed_runs_end_to_end():
    """pv_backfeed_a: null is documented as 'nothing backfeeds the panel'. It
    means 0 A of spent allowance, stated as undeclared rather than measured."""
    with _with_household(PANEL_YAML_NO_BACKFEED):
        p = S.load_panel()
    assert p["pv_backfeed_a"] is None, p
    declared = p["pv_backfeed_a"] is not None
    existing = p["pv_backfeed_a"] if declared else 0.0
    b = S.busbar_120_percent(p["busbar_rating_a"], p["service_rating_a"],
                             existing, declared, p["pv_breaker_position"],
                             p["main_breaker_position"])
    assert _close(b["existing_pv_backfeed_a"], 0.0), b
    assert b["existing_pv_backfeed_declared"] is False, b
    assert _close(b["remaining_backfeed_a"], 65.0), b
    assert b["position_condition"]["verdict"] == "not_determined", b
    return "a null pv_backfeed_a spends 0 A and is reported as undeclared"


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
    b = S.busbar_120_percent(200.0, 100.0, 50.0, True,
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
    assert b["ampacity_leg"] in ("pass", "fail"), b
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
    assert "existing_pv_backfeed_declared" in pan, pan
    assert isinstance(pan["existing_pv_backfeed_declared"], bool), pan
    if not pan["existing_pv_backfeed_declared"]:
        assert _close(pan["existing_pv_backfeed_a"], 0.0), pan
        assert "no existing backfeed source" in pan["existing_pv_backfeed_note"], pan
    socket = pan["meter_socket_continuous_a"]
    nec = d["nec_220_87"]
    if socket is None:
        assert "does not apply" in pan["meter_socket_constraint"], pan
        assert [s["step"] for s in nec["steps"]] == [1, 2, 3], nec["steps"]
        assert nec["headroom_a"]["vs_meter_socket"] is None, nec
    else:
        assert [s["step"] for s in nec["steps"]] == [1, 2, 3, 4], nec["steps"]
        assert nec["headroom_a"]["vs_meter_socket"] is not None, nec
    return "the artifact says which nullable panel fields were declared"


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
    case_220_87_omits_the_socket_step_when_none_is_recorded,
    case_three_valued_verdict_needs_both_bases,
    case_remaining_headroom_omits_an_absent_socket,
    case_amps_uses_the_240_v_service_basis,
    case_evse_is_a_continuous_load,
    case_busbar_120_percent_rule_fails_the_battery,
    case_busbar_120_percent_rule_passes_a_smaller_main,
    case_the_busbar_position_condition_is_evaluated_not_ignored,
    case_busbar_carries_both_legs_of_the_rule,
    case_the_battery_verdict_needs_both_legs,
    case_an_undeclared_backfeed_source_spends_no_allowance,
    case_fall_back_day_produces_no_phantom_peak,
    case_spring_forward_day_is_short_and_still_clean,
    case_day_lengths_match_the_tariff_clock,
    case_dst_dates_are_derived_not_listed,
    case_zero_padding_is_truncated_not_treated_as_data,
    case_an_all_zero_enphase_export_fails_closed,
    case_enphase_loader_checks_shape_and_reads_the_trailing_year,
    case_absent_service_rating_fails_closed,
    case_panel_intake_reads_every_required_field,
    case_every_required_panel_field_still_fails_closed,
    case_a_null_meter_socket_rating_runs_end_to_end,
    case_a_null_pv_backfeed_runs_end_to_end,
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
