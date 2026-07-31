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
panel:
  service_rating_a: 175
  busbar_rating_a: 200
  pv_backfeed_a: 50
  meter_socket_continuous_a: 170
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
    return "the same rule passes when the main breaker is smaller"


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


def case_conservation_residual_is_computed_and_bounded():
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(5)]
    ref_a = [10.0, 12.0, 14.0, 16.0, 18.0]
    pv = {}
    for d, v in zip(days, ref_a):
        pv[(d, 11)] = v * 0.505 * 2      # derived series is 1% high
        pv[(d, 12)] = 0.0
    dst = dt.date(2026, 3, 8)
    pv[(dst, 11)] = 9999.0               # a DST day must never enter the sum
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "threeway.csv"
        lines = [",refA,refB"]
        for d, v in zip(days, ref_a):
            lines.append(f"{d},{v},{v * 1.02}")
        lines.append(f"{dst},1.0,1.0")
        p.write_text("\n".join(lines) + "\n")
        old = S.THREEWAY
        S.THREEWAY = p
        try:
            out = S.conservation_check(pv, {dst})
        finally:
            S.THREEWAY = old
    assert out["days_compared"] == 5, out
    assert str(dst) in out["dst_days_excluded"], out
    assert _close(out["derived_total_kwh"], 70.7, 1e-6), out
    a = out["against"]["refA"]
    assert _close(a["ratio_derived_over_reference"], 1.01, 1e-4), a
    assert _close(a["residual_pct"], 1.0, 1e-3), a
    assert _close(a["correlation"], 1.0, 1e-4), a
    assert a["mae_kwh_per_day"] > 0.0, a
    assert _close(out["references_disagree_pct"], 2.0, 1e-2), out
    # the 9999 kWh DST day would blow every one of those figures if included
    assert out["derived_total_kwh"] < 100.0, out
    return "the conservation residual is computed, bounded, and excludes DST days"


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
    for c in d["cases"]:
        exp = nec["headroom_a"]["vs_service_rating"] - c["fixed_added_load_a"]
        assert _close(c["remaining_headroom_a"]["vs_service_rating"], exp, 1e-3), c
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
    case_amps_uses_the_240_v_service_basis,
    case_evse_is_a_continuous_load,
    case_busbar_120_percent_rule_fails_the_battery,
    case_busbar_120_percent_rule_passes_a_smaller_main,
    case_fall_back_day_produces_no_phantom_peak,
    case_spring_forward_day_is_short_and_still_clean,
    case_day_lengths_match_the_tariff_clock,
    case_dst_dates_are_derived_not_listed,
    case_zero_padding_is_truncated_not_treated_as_data,
    case_an_all_zero_enphase_export_fails_closed,
    case_enphase_loader_checks_shape_and_reads_the_trailing_year,
    case_absent_service_rating_fails_closed,
    case_panel_intake_reads_every_required_field,
    case_pole_counting_handles_int_and_list_amps,
    case_malformed_schedule_entries_fail_closed,
    case_panel_occupancy_counts_spaces_poles_and_ocpd,
    case_gross_is_exact_only_where_nothing_was_produced,
    case_missing_enphase_hours_fall_back_to_the_empirical_ceiling,
    case_conservation_residual_is_computed_and_bounded,
    case_only_match_refuses_zero_or_two_candidates,
    case_artifact_round_trips_byte_identically,
    case_artifact_is_internally_consistent,
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
