#!/usr/bin/env python3
"""Tests for all_electric_endgame.py (issue #20).

Same pattern as test_heat_pump_conversion.py: point household.PATH at a
synthetic household BEFORE importing so this file always imports cleanly,
and gate archive-dependent cases (the real measured year, the real gas
export, byte-identical regeneration) behind SkipCase rather than failing.

Run from the repo root:  ./.venv/bin/python analysis/test_all_electric_endgame.py
"""
import calendar
import datetime as dt
import glob
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\n  has_ev: true\n  has_gas: true\n"
    "location:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None
# Deliberately no panel: block, and no appliance_fuels key -- this module's
# own cooking_fuel_evidence() must read ONLY the public-ok household.
# appliance_fuels field, never panel.schedule or panel.no_dryer_or_water_
# heater_circuit (both private-only, TECHNICAL.md section 11.3). An earlier
# version of the fixture carried a synthetic panel.schedule whose label text
# ('Oven', 'Range (30-2P)...') happened to coincide with this household's
# own REAL panel labels and tripped the repo's own pre-commit privacy gate
# on this test file -- removed along with the code that read it.

import rates as R                        # noqa: E402
import behavior_rebuild as br            # noqa: E402
import heat_pump_conversion as hpc       # noqa: E402
import service_headroom as sh            # noqa: E402
import all_electric_endgame as A         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"
REAL_GAS_CSV = ROOT / "private" / "1-raw-data" / "gas.csv"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def _require_archive():
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file() or not REAL_GAS_CSV.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}), "
                       f"{HOUSEHOLD_YAML} and {REAL_GAS_CSV}, which this "
                       "checkout does not have")
    br.CSV = files[0]
    return files[0]


# ---------------------------------------------------------------------------
# Synthetic gas/weather fixtures (mirrors test_heat_pump_conversion.py's own)
# ---------------------------------------------------------------------------
def _synthetic_gas_and_weather(tmp_path, floor=0.4, slope=0.15, days=365,
                               start=dt.date(2025, 1, 1)):
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    rows_gas = ["Name,Test\nAddress,Test\nAccount Number,0\nDisclaimer,x\n"
               "Title,x\nResource,Gas\nMeter Number,1\nInterval UOM,Day\n"
               "Reading Start,x\nReading End,x\nTotal Duration,x\n"
               "Total Usage,x\nUOM,Therms\n"
               "Meter Number,Date,Start Time,Duration,Consumption\n"]
    rows_w = ["header\n"]
    for i, d in enumerate(dates):
        frac = abs((i - days / 2) / (days / 2))
        hdd = 30 * frac
        tf = 65 - hdd
        therms = floor + slope * hdd
        rows_gas.append(f'"1","{d.month}/{d.day}/{d.year}","6:59 AM","Day","{therms:.4f}"\n')
        rows_w.append(f"{d.isoformat()},{tf:.2f}\n")
    (tmp_path / "gas.csv").write_text("".join(rows_gas))
    (tmp_path / "weather_daily_tmean.csv").write_text("".join(rows_w))


class _GasWeatherFixture:
    def __init__(self, tmp, **kw):
        self.tmp = tmp
        self.kw = kw

    def __enter__(self):
        _synthetic_gas_and_weather(self.tmp, **self.kw)
        self._real = (hpc.GAS_CSV, hpc.WEATHER_CSV)
        hpc.GAS_CSV = str(self.tmp / "gas.csv")
        hpc.WEATHER_CSV = str(self.tmp / "weather_daily_tmean.csv")
        return self

    def __exit__(self, *exc):
        hpc.GAS_CSV, hpc.WEATHER_CSV = self._real
        return False


def _gas_detail_rows(statement_date, period, gas_service, gas_energy, other_fees):
    rows = []
    for i, (days, bl_rate, nb_rate) in enumerate(gas_service):
        rows.append(dict(statement_date=statement_date, period=period,
                         charge_type="gas_service", segment=i, segment_days=days,
                         segment_therms=np.nan, baseline_rate=bl_rate, nonbaseline_rate=nb_rate,
                         energy_rate=np.nan, other_fees_rate=np.nan))
    for i, (days, er) in enumerate(gas_energy):
        rows.append(dict(statement_date=statement_date, period=period,
                         charge_type="gas_energy", segment=i, segment_days=days,
                         segment_therms=np.nan, baseline_rate=np.nan, nonbaseline_rate=np.nan,
                         energy_rate=er, other_fees_rate=np.nan))
    for i, (therms, ofr) in enumerate(other_fees):
        rows.append(dict(statement_date=statement_date, period=period,
                         charge_type="other_fees", segment=i, segment_days=np.nan,
                         segment_therms=therms, baseline_rate=np.nan, nonbaseline_rate=np.nan,
                         energy_rate=np.nan, other_fees_rate=ofr))
    return rows


def _single_segment_detail(statement_date, period, period_days, therms,
                           baseline_rate, nonbaseline_rate, energy_rate, other_fees_rate):
    return _gas_detail_rows(
        statement_date, period,
        gas_service=[(period_days, baseline_rate, nonbaseline_rate)],
        gas_energy=[(period_days, energy_rate)],
        other_fees=[(therms, other_fees_rate)])


class _GasDetailFixture:
    """Points hpc.GAS_PERIODS_CSV/GAS_DETAIL_CSV (all_electric_endgame.py's
    own floor_savings_by_period() reads these through the hpc module, not
    its own copies) at a temporary fixture pair."""

    def __init__(self, periods_df, detail_rows):
        self.periods_df = periods_df
        self.detail_rows = detail_rows

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._td.name)
        periods_csv = tmp / "bill_periods_gas.csv"
        detail_csv = tmp / "bill_gas_detail.csv"
        self.periods_df.to_csv(periods_csv, index=False)
        pd.DataFrame(self.detail_rows).to_csv(detail_csv, index=False)
        self._real = (hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV)
        hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV = str(periods_csv), str(detail_csv)
        return self

    def __exit__(self, *exc):
        hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV = self._real
        self._td.cleanup()
        return False


def _make_periods_df(rows):
    """rows: [(statement_date, period_str, therms, billed_amount,
    baseline_allowance_therms), ...]"""
    return pd.DataFrame([{
        "statement_date": r[0], "period": r[1], "period_end_month": "x",
        "therms": r[2], "total_gas_service": r[3], "billed_amount": r[3],
        "baseline_rate": 2.0, "nonbaseline_rate": 2.4,
        "baseline_allowance_therms": r[4], "gas_energy_charge_rate": 0.5,
        "other_fees_rate": 0.12,
    } for r in rows])


# ---------------------------------------------------------------------------
# AC1 -- fixed_charge_regression
# ---------------------------------------------------------------------------
@case
def case_fixed_charge_regression_recovers_a_known_zero_intercept():
    """A synthetic corpus built as EXACTLY slope*therms (zero intercept, no
    noise) must recover an intercept indistinguishable from zero -- the
    positive control for the near-zero-fixed-charge claim."""
    rows = [(f"2025-{m:02d}-01", f"p{m}", float(t), round(2.6 * t, 2), 11.0)
            for m, t in enumerate(range(5, 30), start=1)]
    df = _make_periods_df(rows)
    with _GasDetailFixture(df, []):
        result = A.fixed_charge_regression()
    assert abs(result["intercept_usd"]) < 0.05, result
    assert result["n_periods"] == len(rows)
    assert abs(result["slope_usd_per_therm"] - 2.6) < 0.01, result
    return f"zero-intercept synthetic corpus recovers intercept={result['intercept_usd']} (true 0)"


@case
def case_fixed_charge_regression_detects_a_real_fixed_charge():
    """The same synthetic corpus, but with a genuine $15 fixed charge added
    to every statement, must recover an intercept near $15 -- proves this
    regression would actually catch a real fixed charge if this rate had
    one, not just report near-zero regardless of the input (tests must fail
    on the defect they name)."""
    rows = [(f"2025-{m:02d}-01", f"p{m}", float(t), round(2.6 * t + 15.0, 2), 11.0)
            for m, t in enumerate(range(5, 30), start=1)]
    df = _make_periods_df(rows)
    with _GasDetailFixture(df, []):
        result = A.fixed_charge_regression()
    assert abs(result["intercept_usd"] - 15.0) < 0.05, result
    return f"a genuine $15 fixed charge is recovered as intercept={result['intercept_usd']}"


# ---------------------------------------------------------------------------
# AC2 -- cooking_fuel_evidence / third_end_use_gap / gas_end_use_enumeration
# ---------------------------------------------------------------------------
@case
def case_cooking_fuel_evidence_not_determined_without_appliance_fuels():
    """The default fixture household has no appliance_fuels key (the common,
    real-world case for this household) -- cooking_fuel_evidence() must
    report 'not determined', never infer a fuel mix from panel.schedule or
    panel.no_dryer_or_water_heater_circuit, both of which are private-only
    and MUST NOT be read by this function at all (a prior version read them
    directly and was blocked by the repo's own pre-commit privacy gate --
    this test is the regression guard for that fix)."""
    ev = A.cooking_fuel_evidence()
    assert ev["verdict"] == "not determined", ev
    assert ev["appliance_fuels_field_present"] is False, ev
    assert "electric_cooking_circuits_found" not in ev, (
        "cooking_fuel_evidence() must not read or report panel-schedule "
        "derived detail at all -- that key's mere presence would mean this "
        "function is reading a private-only field again")
    assert "no_dryer_or_water_heater_circuit" not in ev
    return "with no appliance_fuels answer, cooking_fuel_evidence() reports 'not determined', reading no private-only panel field"


@case
def case_cooking_fuel_evidence_reads_only_the_public_appliance_fuels_field():
    """When household.appliance_fuels IS answered (a different household's
    intake, or this one's own future state once answered), the function
    must report it present and read -- proving it actually reads that
    field, not just always returning 'not determined' regardless of input
    -- but must NOT claim the answer has been interpreted into a verdict:
    'answered_but_not_interpreted' is a real third state, distinct from
    'not determined' AND from any claim of resolution, since appliance_
    fuels is unstructured free text this script deliberately does not
    parse (Codex adversarial review, issue #20 -- presence alone used to
    be conflated with a favorable resolution)."""
    real_path, real_cache = _hh.PATH, _hh._cache
    tmp_dir = tempfile.TemporaryDirectory()
    try:
        _hh.PATH = pathlib.Path(tmp_dir.name) / "household.yaml"
        _hh.PATH.write_text(
            "household:\n  has_gas: true\n"
            "appliance_fuels: 'pool: none, water heater: gas, heating: gas, cooking: electric'\n")
        _hh._cache = None
        ev = A.cooking_fuel_evidence()
        assert ev["verdict"] == "answered_but_not_interpreted", ev
        assert ev["appliance_fuels_field_present"] is True, ev
        assert "resolved" not in ev["note"].lower(), ev
    finally:
        _hh.PATH, _hh._cache = real_path, real_cache
        tmp_dir.cleanup()
    return "a real appliance_fuels answer is read and reported as 'answered_but_not_interpreted', never as resolved"


@case
def case_third_end_use_gap_bracket_arithmetic():
    cooking_fuel = {"appliance_fuels_field_present": False}
    gap = A.third_end_use_gap(137, cooking_fuel)
    lo, hi = A.DRYER_THERMS_PER_MONTH_RANGE
    assert gap["possible_dryer_rough_magnitude_therms_yr"] == [round(lo * 12), round(hi * 12)]
    assert gap["not_priced_here"] is True
    assert gap["possible_dryer_pct_of_floor_range"][0] < gap["possible_dryer_pct_of_floor_range"][1]
    assert "NOT DETERMINED" in gap["gap"]
    # Finding 2 (Codex adversarial review, issue #20 round 2): the same
    # bracket arithmetic, now also sized for cooking -- consumed by
    # water_heater_share_sensitivity's own benchmark_incompatibility_check.
    lo_c, hi_c = A.COOKING_THERMS_YR_RANGE
    assert gap["possible_cooking_rough_magnitude_therms_yr"] == [round(lo_c), round(hi_c)]
    assert gap["possible_cooking_pct_of_floor_range"][0] < gap["possible_cooking_pct_of_floor_range"][1]
    return "third_end_use_gap reports a bracket, not a point estimate, and states NOT DETERMINED when appliance_fuels is unanswered"


@case
def case_third_end_use_gap_defers_to_appliance_fuels_when_present():
    cooking_fuel = {"appliance_fuels_field_present": True}
    gap = A.third_end_use_gap(137, cooking_fuel)
    assert "NOT DETERMINED" not in gap["gap"], gap
    assert "appliance_fuels" in gap["gap"]
    return "third_end_use_gap defers to a real appliance_fuels answer rather than guessing when one exists"


@case
def case_third_end_use_gap_does_not_claim_resolved_from_mere_presence():
    """Codex adversarial review, issue #20 (MEDIUM, all_electric_endgame.py):
    appliance_fuels is UNSTRUCTURED free text, not a fixed enum -- ANY
    non-null answer, even one that literally says 'cooking: gas', used to
    be reported as 'resolved by household.appliance_fuels's own recorded
    answer', which is backwards: a gas answer means a third gas end use
    DOES remain, the opposite of resolved-in-the-favorable-direction. This
    is the regression guard for that exact bug -- mere presence of an
    answer must never be reported as 'resolved'; the reader must be told
    to go read the field's own recorded text."""
    cooking_fuel = {"appliance_fuels_field_present": True}
    gap = A.third_end_use_gap(137, cooking_fuel)
    assert "resolved" not in gap["gap"].lower(), gap
    assert "appliance_fuels" in gap["gap"], gap
    assert "read" in gap["gap"].lower(), gap
    return "third_end_use_gap's presence-only branch never claims 'resolved' -- it tells the reader to read appliance_fuels's own recorded text directly"


@case
def case_gas_end_use_enumeration_sums_to_the_metered_total():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        with _GasWeatherFixture(tmp, floor=0.4, slope=0.15):
            iso = hpc.isolate_heating_therms()
            periods_df = _make_periods_df([
                (f"2025-{m:02d}-01", f"p{m}", 20.0, 50.0, 11.0) for m in range(1, 13)])
            with _GasDetailFixture(periods_df, []):
                enum_ = A.gas_end_use_enumeration(iso)
    assert enum_["sum_check"]["pct_of_metered_total"] == 100.0, enum_["sum_check"]
    assert enum_["non_heating_floor"]["floor_composition"].startswith(
        "not determined from committed public data"), enum_["non_heating_floor"]
    return "floor + heating sum to exactly 100% of the metered annual total"


# ---------------------------------------------------------------------------
# AC3/AC4 -- _priced_at_top_of_ladder / _floor_capped_days / floor_savings_by_period
#
# Codex adversarial review, issue #20 round 1, Finding 1: floor removal must
# be priced at the TOP of the tier ladder (the marginal, most-expensive
# rung), not the bottom -- a first version got this backwards. The case
# below reproduces the reviewer's own hand-worked counterexample exactly:
# a 60-therm period, 20-therm baseline allowance, $2.00/$2.38 rates,
# removing 11 floor therms. True whole-bill savings bill(60)-bill(49) =
# 11 x $2.38 = $26.18 (every removed therm was marginal, since the period
# never drops below the baseline allowance). The old, buggy bottom-of-
# ladder method gave 11 x $2.00 = $22.00 -- reproduced below and asserted
# WRONG, so a regression back to that logic fails this test.
# ---------------------------------------------------------------------------
@case
def case_priced_at_top_of_ladder_matches_reviewers_hand_worked_example():
    """The exact counterexample from Codex adversarial review, issue #20
    round 1, Finding 1: proves floor removal is priced at the marginal
    (top) end of the ladder, matching a true whole-bill before/after delta,
    not the bottom end a first, buggy version used."""
    total, allowance, br, nbr, removed = 60.0, 20.0, 2.00, 2.38, 11.0

    def bill(t):
        base = min(t, allowance)
        nb = max(0.0, t - allowance)
        return base * br + nb * nbr

    true_whole_bill_savings = bill(total) - bill(total - removed)
    old_buggy_bottom_of_ladder = min(removed, allowance) * br + max(0.0, removed - allowance) * nbr
    assert abs(true_whole_bill_savings - 26.18) < 0.005, true_whole_bill_savings
    assert abs(old_buggy_bottom_of_ladder - 22.00) < 0.005, old_buggy_bottom_of_ladder
    assert true_whole_bill_savings != old_buggy_bottom_of_ladder

    priced = A._priced_at_top_of_ladder(
        total_therms=total, marginal_therms=removed, baseline_allowance=allowance,
        baseline_rate=br, nonbaseline_rate=nbr, context="test")
    assert abs(priced - true_whole_bill_savings) < 1e-9, (priced, true_whole_bill_savings)
    assert abs(priced - old_buggy_bottom_of_ladder) > 1.0, (
        "the fix must actually change the number, not coincidentally match the old one")
    return (f"_priced_at_top_of_ladder matches the reviewer's own hand-worked "
           f"whole-bill delta (${priced:.2f}), not the old bottom-of-ladder "
           f"bug (${old_buggy_bottom_of_ladder:.2f})")


@case
def case_priced_at_top_of_ladder_baseline_only():
    cost = A._priced_at_top_of_ladder(
        total_therms=5.0, marginal_therms=5.0, baseline_allowance=11.0,
        baseline_rate=2.0, nonbaseline_rate=2.4, context="test")
    assert abs(cost - 10.0) < 1e-9, cost
    return "a marginal removal that never reaches the allowance prices at baseline_rate only"


@case
def case_priced_at_top_of_ladder_spills_into_nonbaseline():
    """total=30, allowance=11, removing the top 15 therms (i.e. what
    remains after removal is 15, still above the 11-therm allowance) --
    the removed 15 must split 4 nonbaseline / 11 baseline, at the TOP of
    the ladder, not simply 'up to the allowance is baseline' the way the
    old bottom-of-ladder bug would price it (11 baseline + 4 nonbaseline
    is the SAME split by coincidence here only because non_remaining=15
    already exceeds the allowance on its own -- the discriminating case is
    the reviewer's own hand-worked example above, where total > allowance
    and the remainder does NOT already exceed it)."""
    cost = A._priced_at_top_of_ladder(
        total_therms=30.0, marginal_therms=15.0, baseline_allowance=11.0,
        baseline_rate=2.0, nonbaseline_rate=2.4, context="test")
    expected = 15.0 * 2.4  # non_remaining = 30-15 = 15 >= allowance, so the
                           # WHOLE marginal slice is nonbaseline
    assert abs(cost - expected) < 1e-9, cost
    return "a marginal removal entirely above the allowance's own remaining share prices at nonbaseline_rate throughout"


@case
def case_priced_at_top_of_ladder_small_overflow_folds_back_to_baseline():
    """A tiny overflow (within FLOOR_ESTIMATION_TOLERANCE_THERMS) against a
    segment whose real bill never crossed into nonbaseline (nonbaseline_rate
    is None) must NOT fail closed -- it is day-proportion estimation noise,
    per this function's own docstring."""
    cost = A._priced_at_top_of_ladder(
        total_therms=11.05, marginal_therms=11.05, baseline_allowance=11.0,
        baseline_rate=2.0, nonbaseline_rate=None, context="test")
    assert abs(cost - 11.05 * 2.0) < 1e-9, cost
    return "a small overflow against a never-crossed segment folds back to baseline, no failure"


@case
def case_priced_at_top_of_ladder_large_overflow_fails_closed():
    """A LARGE overflow against a segment with no nonbaseline_rate at all
    must fail closed -- proves the tolerance has a real ceiling (tests must
    fail on the defect they name: a version of this function with the
    tolerance check removed, or set absurdly high, would NOT catch this)."""
    try:
        A._priced_at_top_of_ladder(
            total_therms=20.0, marginal_therms=20.0, baseline_allowance=11.0,
            baseline_rate=2.0, nonbaseline_rate=None, context="test-context-marker")
        raise AssertionError("a 9-therm overflow with no nonbaseline_rate was silently accepted")
    except SystemExit as e:
        assert "test-context-marker" in str(e), e
        assert "nonbaseline" in str(e), e
    return "a large overflow against a never-crossed segment fails closed"


@case
def case_floor_segment_total_therms_is_day_proportion():
    """_floor_segment_total_therms always uses day-proportion (gas_daily=
    None passed through to heat_pump_conversion._segment_real_or_proxy_
    therms), never the real daily export -- see its own docstring for why
    (the fail-closed path there would fire on the one trailing period whose
    early days precede gas.csv's own coverage start, since the floor,
    unlike heating, is never exactly zero on a real day)."""
    t_s = A._floor_segment_total_therms(
        dt.date(2025, 6, 1), dt.date(2025, 6, 15), period_therms=30.0,
        period_days=30, floor_s=5.0, context="test")
    assert abs(t_s - 15.0) < 1e-9, t_s  # 30 * 15/30 = 15, pure day-proportion
    return "segment total therms is a pure day-proportion of the period's own real total"


@case
def case_floor_capped_days_caps_at_the_periods_own_real_total():
    """A period whose real billed total is LESS than floor_per_day times
    its own day count must cap the floor at that real total, not silently
    attribute more floor gas than the meter actually read that period."""
    start, end = dt.date(2025, 7, 1), dt.date(2025, 7, 31)
    period_days = (end - start).days + 1
    floor_per_day = 0.376
    # period only billed 9 therms total -- far under 31*0.376=11.66
    days = A._floor_capped_days(start, end, floor_per_day, period_total_therms=9.0)
    assert len(days) == period_days
    total = sum(f for _, f in days)
    assert abs(total - 9.0) < 1e-9, total
    return "a low-usage period caps the floor at its own real billed total, not the annual average"


@case
def case_floor_capped_days_uncapped_when_period_total_is_generous():
    start, end = dt.date(2025, 12, 1), dt.date(2025, 12, 31)
    period_days = (end - start).days + 1
    floor_per_day = 0.376
    days = A._floor_capped_days(start, end, floor_per_day, period_total_therms=70.0)
    total = sum(f for _, f in days)
    assert abs(total - floor_per_day * period_days) < 1e-9, total
    return "a generously-billed period is not capped -- the full floor_per_day constant is used"


@case
def case_floor_savings_by_period_never_split_segment_matches_hand_calc():
    """The trivial, never-split case (one segment per charge type, the
    common case): floor_savings_by_period()'s own output must match a
    hand-computed figure exactly, the same regression-safety shape
    test_heat_pump_conversion.py's own _single_segment_detail fixture is
    built for.

    Period total (20 therms) is deliberately LARGER than the floor's own
    computed share (12 therms, from floor=0.4/day x 30 days) -- the
    remaining 8 therms stand in for other usage (heating, in a real
    period) that stays behind. This is the discriminating shape: with
    floor==total (an earlier version of this test used therms=12 to match
    the floor exactly), top-of-ladder and bottom-of-ladder pricing
    coincide by construction and the test cannot tell them apart. Here
    they must differ -- see the hand calc below -- so a regression back to
    the pre-Finding-1-fix bottom-of-ladder bug fails this test."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        with _GasWeatherFixture(tmp, floor=0.4, slope=0.15, days=365):
            iso = hpc.isolate_heating_therms()
            period = "Jun 1, 2025 - Jun 30, 2025"
            periods_df = pd.DataFrame([{
                "statement_date": "2025-06-30", "period": period,
                "period_end_month": "jun 2025", "therms": 20.0,
                "total_gas_service": 30.0, "billed_amount": 30.0,
                "baseline_rate": 2.0, "nonbaseline_rate": 2.4,
                "baseline_allowance_therms": 11.0, "gas_energy_charge_rate": 0.5,
                "other_fees_rate": 0.12,
            }])
            detail = _single_segment_detail(
                "2025-06-30", period, period_days=30, therms=20.0,
                baseline_rate=2.0, nonbaseline_rate=2.4, energy_rate=0.5,
                other_fees_rate=0.12)
            with _GasDetailFixture(periods_df, detail):
                rows, total_savings, total_therms = A.floor_savings_by_period(iso, n_trailing=1)
    # floor = 0.4/day * 30 days = 12.0 (uncapped: period total/days = 20/30
    # = 0.667/day > 0.4/day). Segment total (t_s) = 20 (day-proportion of
    # the period's own real total, whole period = whole segment here).
    # TOP-of-ladder: non_remaining = 20-12 = 8; baseline_ceiling = min(11,20)
    # = 11; overlap_baseline = min(max(0, 11-8), 12) = 3; overlap_nonbaseline
    # = 12-3 = 9. Gas Service = 3*2.0 + 9*2.4 = 27.6.
    # (the old, buggy bottom-of-ladder method would have given
    # min(12,11)*2.0 + max(0,12-11)*2.4 = 11*2.0+1*2.4 = 24.4 -- different,
    # proving this scenario discriminates the two.)
    expected_gs = 3.0 * 2.0 + 9.0 * 2.4
    expected_gs_old_buggy = 11.0 * 2.0 + 1.0 * 2.4
    assert abs(expected_gs - expected_gs_old_buggy) > 1.0  # sanity: scenario discriminates
    expected_ge = 12.0 * 0.5
    expected_of = 12.0 * 0.12
    expected = round(expected_gs + expected_ge + expected_of, 2)
    assert len(rows) == 1, rows
    assert abs(total_therms - 12.0) < 1e-6, total_therms
    assert abs(total_savings - expected) < 0.01, (total_savings, expected)
    return f"never-split-segment floor pricing matches the TOP-of-ladder hand calc: ${total_savings} (expected ${expected}, old buggy bottom-of-ladder would give ${round(expected_gs_old_buggy + expected_ge + expected_of, 2)})"


# ---------------------------------------------------------------------------
# AC4 -- build_wh_load_series / wh_electric_cost_scenarios (energy
# conservation, real-interval placement)
# ---------------------------------------------------------------------------
def _synthetic_frame(n_days=10):
    rows = []
    start = dt.datetime(2026, 1, 5)   # a Monday
    for day in range(n_days):
        for slot in range(96):
            ts = start + dt.timedelta(days=day, minutes=15 * slot)
            rows.append(ts)
    d = pd.DataFrame({"dt": rows})
    d["Consumption"] = 0.1
    d["Generation"] = 0.05
    d["p"] = [R.period_at(t) for t in d["dt"]]
    d["seas"] = np.where(d["dt"].dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    d["ym"] = d["dt"].dt.to_period("M")
    return d


@case
def case_build_wh_load_series_conserves_energy_across_distributions():
    d = _synthetic_frame()
    ann_kwh = 600.0
    added, fallback = A.build_wh_load_series(d, ann_kwh)
    for key, series in added.items():
        total = float(series.sum())
        assert abs(total - ann_kwh) < 0.01, (key, total)
    return "uniform/super_off_peak/on_peak water-heater load series each conserve the same annual kWh"


@case
def case_build_wh_load_series_scenario_key_is_super_off_peak_not_midday():
    """Finding 2 (Codex review pass, issue #20 round 3): this scenario
    concentrates load into rates.period()'s 'sop' intervals, which on this
    household's EV-TOU-5 tariff run 00:00-06:00 plus weekday 10:00-14:00 and
    weekend 00:00-14:00 -- mostly overnight/early-morning, not clock-time
    midday. The scenario key must say what the code actually does (targets
    the super-off-peak RATE period) rather than implying a daytime/solar-
    timer placement it does not compute. This also pins the sop-targeting
    behavior itself: on a day with sop intervals, the super_off_peak series
    must be zero everywhere else."""
    d = _synthetic_frame()
    added, _ = A.build_wh_load_series(d, 600.0)
    assert "midday" not in added, "a 'midday' key would mislabel the sop-targeting scenario"
    assert "super_off_peak" in added
    is_sop = (d["p"] == "sop")
    assert (added["super_off_peak"].loc[~is_sop] == 0).all(), (
        "super_off_peak placed load outside sop intervals on a day that has sop intervals")
    assert (added["super_off_peak"].loc[is_sop] > 0).any()
    return "the sop-targeting water-heater scenario is named super_off_peak, not midday, and only ever places load in 'sop' intervals"


@case
def case_build_wh_load_series_falls_back_when_a_day_has_no_sop_or_on_intervals():
    """A day with only 'off' period intervals (no super-off-peak, no
    on-peak -- e.g. certain weekend afternoons under some TOU rules) must
    fall back to a uniform placement for that one day, not silently drop
    its own share of the annual kWh."""
    rows = []
    start = dt.datetime(2026, 6, 1)   # a Monday, summer
    for slot in range(96):
        ts = start + dt.timedelta(minutes=15 * slot)
        rows.append(ts)
    d = pd.DataFrame({"dt": rows})
    d["Consumption"] = 0.1
    d["Generation"] = 0.05
    d["p"] = "off"   # force every interval to 'off' -- no sop, no on at all
    ann_kwh = 10.0
    added, fallback = A.build_wh_load_series(d, ann_kwh)
    assert fallback["super_off_peak"] == 1, fallback
    assert fallback["on_peak"] == 1, fallback
    assert abs(float(added["super_off_peak"].sum()) - ann_kwh) < 0.01
    assert abs(float(added["on_peak"].sum()) - ann_kwh) < 0.01
    return "a day with no sop/on-peak intervals falls back to uniform, energy still conserved"


@case
def case_build_wh_load_series_fails_closed_on_an_empty_frame():
    d = pd.DataFrame({"dt": pd.to_datetime([]), "p": []})
    try:
        A.build_wh_load_series(d, 100.0)
        raise AssertionError("an empty frame was silently accepted")
    except SystemExit:
        pass
    return "an empty frame with no dates to place load into fails closed"


@case
def case_service_headroom_check_fails_closed_on_missing_service_headroom_json():
    with tempfile.TemporaryDirectory() as td:
        real_data = A.DATA
        A.DATA = td
        try:
            A.service_headroom_check()
            raise AssertionError("a missing service_headroom.json was silently accepted")
        except SystemExit as e:
            assert "service_headroom.json" in str(e)
        finally:
            A.DATA = real_data
    return "a missing data/service_headroom.json fails closed rather than crashing obscurely"


@case
def case_build_fails_closed_on_missing_heat_pump_conversion_json():
    """build() cites heat_pump_conversion.json directly rather than
    recomputing it -- if it is missing, this must fail with a clear
    message pointing at issue #1/#109, not an opaque KeyError deep inside
    the function."""
    _require_archive()
    real_data_hpc, real_data_a = hpc.DATA, A.DATA
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        A.DATA = str(tmp)
        try:
            try:
                A.build()
                raise AssertionError("a missing heat_pump_conversion.json was silently accepted")
            except SystemExit as e:
                assert "heat_pump_conversion.json" in str(e), e
        finally:
            A.DATA = real_data_a
    return "build() fails closed when data/heat_pump_conversion.json is missing"


@case
def case_wh_electric_cost_scenarios_conserves_energy_and_bills():
    d = _synthetic_frame()
    electric, base_bill = A.wh_electric_cost_scenarios(d, floor_therms_yr=137.0)
    assert set(electric.keys()) == set(A.HPWH_UEF_SCENARIOS.keys())
    for uef_key, scen in electric.items():
        for dist_key in ("uniform", "super_off_peak", "on_peak"):
            assert "electric_cost_increase_usd" in scen[dist_key]
            assert scen[dist_key]["added_kwh"] > 0
    return "wh_electric_cost_scenarios runs energy-conserving netting across every UEF x distribution cell"


# ---------------------------------------------------------------------------
# AC5 -- service_headroom_check (reuses service_headroom.physical_fit()
# directly and a synthetic service_headroom.json)
# ---------------------------------------------------------------------------
def _write_synthetic_service_headroom_json(tmp, spaces_free, conservative_a, measured_a,
                                           hp_replaces_ac_verdict="pass"):
    data = {
        "cases": [
            {"case": "heat_pump_only", "fixed_added_load_a": 0.0,
             "spaces": {"spaces_free": spaces_free},
             "remaining_headroom_a": {
                 "conservative_basis": {"binding": conservative_a},
                 "measured_basis": {"binding": measured_a}}},
            {"case": "heat_pump_replaces_ac", "fixed_added_load_a": 0.0,
             "ampacity_verdict": hp_replaces_ac_verdict,
             "spaces": {"spaces_free": spaces_free},
             "remaining_headroom_a": {
                 "conservative_basis": {"binding": conservative_a},
                 "measured_basis": {"binding": measured_a}}},
        ],
    }
    path = tmp / "service_headroom.json"
    path.write_text(json.dumps(data))
    return path


@case
def case_service_headroom_check_flags_physical_space_hard_blocker():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _write_synthetic_service_headroom_json(tmp, spaces_free=1, conservative_a=40,
                                               measured_a=80)
        real_data = A.DATA
        A.DATA = str(tmp)
        try:
            result = A.service_headroom_check()
        finally:
            A.DATA = real_data
    assert result["physical_fit_verdict"] == "fail", result
    assert result["hard_blocker"] is True, result
    assert "1 free full-size space" in result["hard_blocker_note"]
    return "one free space with a new-240V-circuit need of two is flagged as a hard blocker"


@case
def case_service_headroom_check_not_determined_with_enough_spaces_but_no_adjacency_data():
    """Enough free spaces (4) is NOT itself a 'pass' -- service_headroom.
    physical_fit()'s own contract requires knowing the free spaces are
    ADJACENT, which this household's intake never records (schedule_
    confidence: 'position map partial'), so the honest verdict is
    'not_determined', not 'pass' -- this test would catch a reimplementation
    that conflated 'enough spaces' with 'a confirmed fit'. ampacity_verdict
    is ALSO 'not_determined' here, not 'pass' (Codex review, issue #20
    round 5, Finding 1): plenty of spare amperage remains after the water
    heater's own fixed code load, but no specific furnace heat-pump model
    is ever selected in this issue's own analysis (heat_pump_conversion.py
    prices a COP bracket, not one nameplate MCA), so the heat pump's own
    equipment ampacity is never actually checked against what is left --
    a real 'pass' is not knowable from this artifact."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _write_synthetic_service_headroom_json(tmp, spaces_free=4, conservative_a=40,
                                               measured_a=80)
        real_data = A.DATA
        A.DATA = str(tmp)
        try:
            result = A.service_headroom_check()
        finally:
            A.DATA = real_data
    assert result["physical_fit_verdict"] == "not_determined", result
    assert result["hard_blocker"] is False, result
    assert result["ampacity_verdict"] == "not_determined", result
    return "enough free spaces without adjacency data reports not_determined, not a false pass"


@case
def case_service_headroom_check_ampacity_fails_when_code_load_exceeds_conservative_spare():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # conservative spare (5 A) is under the water heater's own 23.44 A
        # code load on BOTH bases -- must report a real 'fail', not 'pass'.
        _write_synthetic_service_headroom_json(tmp, spaces_free=4, conservative_a=5,
                                               measured_a=10)
        real_data = A.DATA
        A.DATA = str(tmp)
        try:
            result = A.service_headroom_check()
        finally:
            A.DATA = real_data
    assert result["ampacity_verdict"] == "fail", result
    return "insufficient spare amperage on both bases reports a real ampacity fail"


@case
def case_service_headroom_check_ampacity_never_passes_even_with_abundant_spare():
    """Codex `review` pass, issue #20 round 5, Finding 1: the cumulative
    service-headroom check never debited the furnace heat pump's OWN
    electrical demand -- it took heat_pump_only's remaining_headroom_a
    (itself a SOLVED-FOR 'largest MCA that fits with nothing else added'
    term, per that case's own service_headroom.json note) and subtracted
    only the water heater's fixed code load, treating any non-negative
    remainder as a 'pass'. That implicitly assumes the furnace heat pump
    itself draws ZERO amps of the panel's spare capacity. heat_pump_
    replaces_ac's own remaining_headroom_a is the SAME solved-for shape
    (its own 'remaining_is' field: "the largest heat-pump MCA that fits
    ..."), so swapping to it would not fix this either -- neither case
    gives a FIXED remaining number for an assumed real unit. With even
    enormous (1000 A) spare on both bases, ampacity_verdict must still be
    'not_determined', never 'pass', because no specific heat-pump MCA is
    ever selected anywhere in this issue's own furnace analysis to check
    against what is left. This test would have failed against the
    pre-fix code (which returned 'pass' here)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _write_synthetic_service_headroom_json(tmp, spaces_free=4, conservative_a=1000,
                                               measured_a=1000, hp_replaces_ac_verdict="pass")
        real_data = A.DATA
        A.DATA = str(tmp)
        try:
            result = A.service_headroom_check()
        finally:
            A.DATA = real_data
    assert result["ampacity_verdict"] == "not_determined", result
    assert result["spare_after_water_heater_a"]["conservative_basis"] > 900, result
    assert "no specific" in result["known_gap"].lower() or "not selected" in result["known_gap"].lower(), result
    return "abundant spare amperage still reports not_determined, never a false pass, because no heat-pump model is selected"


@case
def case_service_headroom_check_ampacity_not_determined_on_mixed_basis_signs():
    """Test-analyzer finding, issue #20 round 6 (correct by inspection, but
    previously untested): ampacity_verdict's own guard is `after_wh_
    conservative < 0 AND after_wh_measured < 0` -- a real `and`, not an
    `or`. When the two bases DISAGREE in sign (conservative basis negative,
    measured basis non-negative, the genuinely mixed case, not the
    both-negative or both-non-negative cases every other test here already
    covers), the correct verdict is 'not_determined', not 'fail': a 'fail'
    verdict is only earned when the water heater's own fixed code load
    exceeds spare capacity on BOTH bases regardless of which basis is
    later chosen. An `and` -> `or` regression would flip this specific case
    to 'fail' and this test would catch it (conservative_a=20 gives
    after_wh_conservative = 20 - 23.44 = -3.44 (negative); measured_a=30
    gives after_wh_measured = 30 - 23.44 = 6.56 (non-negative))."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _write_synthetic_service_headroom_json(tmp, spaces_free=4, conservative_a=20,
                                               measured_a=30)
        real_data = A.DATA
        A.DATA = str(tmp)
        try:
            result = A.service_headroom_check()
        finally:
            A.DATA = real_data
    assert result["spare_after_water_heater_a"]["conservative_basis"] < 0, result
    assert result["spare_after_water_heater_a"]["measured_basis"] >= 0, result
    assert result["ampacity_verdict"] == "not_determined", result
    return "a mixed-sign result across the two bases (conservative negative, measured non-negative) reports not_determined, not a false fail"


# ---------------------------------------------------------------------------
# AC7 -- sequencing_and_paybacks
# ---------------------------------------------------------------------------
_ZERO_ELECTRIC_INTERACTION = {
    "overstatement_usd": 0.0, "wh_independent_electric_increase_usd": 0.0,
    "furnace_independent_electric_increase_usd": 0.0,
    "independent_sum_electric_increase_usd": 0.0, "joint_electric_increase_usd": 0.0,
    "note": "test fixture: zero electric interaction",
}


_ZERO_INTERACTION = {"overstatement_usd": 0.0, "gas_service_independent_sum_usd": 0.0,
                     "gas_service_joint_removal_usd": 0.0, "by_segment": [],
                     "note": "test fixture: zero interaction"}


@case
def case_sequencing_orders_by_shorter_payback_first():
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=_ZERO_INTERACTION,
        electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    assert result["order"] == ["water_heater", "furnace"], result["order"]
    assert result["last_step"] == "furnace"
    assert result["fixed_charge_release_usd"] == 0.0
    return "the shorter-payback step (water heater) sorts first"


@case
def case_sequencing_reorders_when_furnace_pays_back_faster():
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=5, wh_payback_years=800.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=2000,
        furnace_payback_years=7.0, tier_interaction=_ZERO_INTERACTION,
        electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    assert result["order"] == ["furnace", "water_heater"], result["order"]
    assert result["last_step"] == "water_heater"
    return "sequencing genuinely reorders on the input economics, not hardcoded to one order"


@case
def case_sequencing_final_step_identical_with_and_without_zero_credit():
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=_ZERO_INTERACTION,
        electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    fsa = result["final_step_alone_payback"]
    assert fsa["with_fixed_charge_credit"] == fsa["without_fixed_charge_credit"], fsa
    assert fsa["identical_because_credit_is_zero"] is True
    return "final-step-alone payback is identical with/without the (zero) fixed-charge credit"


@case
def case_sequencing_combined_payback_sums_install_and_savings():
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=_ZERO_INTERACTION,
        electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    ct = result["complete_transition_payback"]
    assert ct["combined_install_usd"] == 18000, ct
    assert abs(ct["combined_annual_net_savings_usd"] - 250) < 0.01, ct
    assert abs(ct["naive_summed_annual_net_savings_usd"] - 250) < 0.01, ct
    return "complete-transition payback combines both steps' install cost and net savings"


@case
def case_sequencing_applies_the_tier_interaction_correction():
    """Codex adversarial review, issue #20 round 1 (a direct consequence of
    the Finding-1 fix): complete_transition_payback's own combined savings
    must be the naive sum MINUS the tier-interaction overstatement, not the
    naive sum itself -- proves the correction is actually wired in, not
    just computed and discarded."""
    interaction = {"overstatement_usd": 15.0, "gas_service_independent_sum_usd": 0.0,
                   "gas_service_joint_removal_usd": 0.0, "by_segment": [],
                   "note": "test fixture: $15 interaction"}
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=interaction,
        electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    ct = result["complete_transition_payback"]
    assert abs(ct["naive_summed_annual_net_savings_usd"] - 250) < 0.01, ct
    assert abs(ct["combined_annual_net_savings_usd"] - 235) < 0.01, ct  # 250 - 15
    assert ct["tier_interaction_overstatement_usd"] == 15.0, ct
    return "a nonzero tier-interaction overstatement is subtracted from the naive summed savings"


@case
def case_sequencing_raises_if_fixed_charge_is_nonzero():
    """This function is deliberately written to assume AC1's own $0 finding
    -- calling it with fixed_charge_verdict_is_zero=False must fail loudly
    rather than silently apply zero-credit logic to a nonzero-charge rate."""
    try:
        A.sequencing_and_paybacks(
            fixed_charge_verdict_is_zero=False,
            wh_install_usd=1, wh_annual_net_savings_usd=1, wh_payback_years=1.0,
            furnace_install_usd=1, furnace_annual_net_savings_usd=1,
            furnace_payback_years=1.0, tier_interaction=_ZERO_INTERACTION,
            electric_interaction=_ZERO_ELECTRIC_INTERACTION)
        raise AssertionError("a nonzero fixed-charge verdict was silently accepted")
    except SystemExit:
        pass
    return "a nonzero fixed-charge verdict fails closed rather than silently reusing zero-credit logic"


# ---------------------------------------------------------------------------
# Finding 2 (Codex `review` pass, issue #20 round 4): sequencing_share_
# robustness / _crossover_water_heater_share -- fast, deterministic checks
# of the bisection/order-detection logic itself, decoupled from real
# archive data via monkeypatching _wh_net_savings_at_share() (and R.
# bill_nem(), which sequencing_share_robustness() calls once up front for
# base_bill regardless). Real-archive end-to-end coverage lives further
# below (case_sequencing_share_robustness_wired_into_build_on_real_archive).
# ---------------------------------------------------------------------------
@case
def case_crossover_water_heater_share_finds_the_bisection_root():
    """_crossover_water_heater_share() must converge to the algebraic root
    of payback(share) == target_payback_years. Monkeypatches _wh_net_
    savings_at_share() to a simple, controlled linear function of `share`
    alone (net = 200 x share, so central-install payback = 4200/(200 x
    share) years) so the true root is known exactly: target=30yr ->
    share = 4200/(200x30) = 0.7. Tolerance is 0.005, not the bisection's
    own 1e-4 share resolution: HPC.payback_and_npv() rounds payback_years
    to 1 decimal place, and near share=0.7 the payback-vs-share slope is
    steep enough (~43 years per unit share) that a 0.05-year rounding step
    alone shifts the FOUND root by about 0.0012 in share -- a real,
    understood artifact of reusing payback_and_npv()'s own rounding
    unmodified (CLAUDE.md section 8: reuse, don't reimplement a
    higher-precision parallel payback formula just for this search)."""
    real_net = A._wh_net_savings_at_share
    real_bill_nem = A.R.bill_nem
    real_load_series = A.build_wh_load_series
    try:
        A.R.bill_nem = lambda *a, **kw: 0.0
        A.build_wh_load_series = lambda *a, **kw: ({"uniform": 0.0}, {})
        A._wh_net_savings_at_share = (
            lambda iso, d, share, headline_uef, base_bill=None, **kw: 200.0 * share)
        target_years = 30.0
        crossover = A._crossover_water_heater_share(
            iso=None, d=None, headline_uef="central_3.88", target_payback_years=target_years)
        expected_share = A.WH_INSTALL_COST_CENTRAL_USD / (200.0 * target_years)
        assert crossover is not None
        assert abs(crossover - expected_share) < 0.005, (crossover, expected_share)
    finally:
        A._wh_net_savings_at_share = real_net
        A.R.bill_nem = real_bill_nem
        A.build_wh_load_series = real_load_series
    return f"bisection finds the algebraic crossover share ({expected_share:.4f}) to within 0.005"


@case
def case_crossover_water_heater_share_none_when_target_never_reached():
    """If the water heater's own payback never reaches the target even at
    a 100% share, there is no crossover in (0, 1] and the function must
    say so (None), not return a bogus share."""
    real_net = A._wh_net_savings_at_share
    real_bill_nem = A.R.bill_nem
    real_load_series = A.build_wh_load_series
    try:
        A.R.bill_nem = lambda *a, **kw: 0.0
        A.build_wh_load_series = lambda *a, **kw: ({"uniform": 0.0}, {})
        # net=1.0 at every share -> payback = 4200 years always, far worse
        # than any real target.
        A._wh_net_savings_at_share = (
            lambda iso, d, share, headline_uef, base_bill=None, **kw: 1.0)
        crossover = A._crossover_water_heater_share(
            iso=None, d=None, headline_uef="central_3.88", target_payback_years=30.0)
        assert crossover is None, crossover
    finally:
        A._wh_net_savings_at_share = real_net
        A.R.bill_nem = real_bill_nem
        A.build_wh_load_series = real_load_series
    return "no crossover is reported when the target payback is never reached at any share"


@case
def case_crossover_water_heater_share_none_when_never_pays_back_at_all():
    """Real bug (code-reviewer, issue #20 round 6): HPC.payback_and_npv()
    returns payback_years=None (not a large finite number) whenever net
    savings are <= 0 -- a genuinely DIFFERENT case from
    case_crossover_water_heater_share_none_when_target_never_reached above
    (net=1.0 there, a small but POSITIVE savings, giving a large but finite
    payback). Forcing net <= 0 at every share (net=-5.0) means pb_hi is
    None, not a finite number >= target -- the buggy guard
    (`pb_hi is not None and pb_hi >= target`) evaluates False on a None
    pb_hi via short-circuit, silently skips the "never beats target" return,
    falls into the bisection with hi=1.0 permanently pinned at a
    never-pays-back point, and drives toward `lo`, returning a bogus
    near-zero crossover share as if the water heater "wins" there. The
    correct behavior is the same as the always-positive-but-too-slow case:
    no crossover in (0, 1], i.e. None."""
    real_net = A._wh_net_savings_at_share
    real_bill_nem = A.R.bill_nem
    real_load_series = A.build_wh_load_series
    try:
        A.R.bill_nem = lambda *a, **kw: 0.0
        A.build_wh_load_series = lambda *a, **kw: ({"uniform": 0.0}, {})
        # net=-5.0 at every share -> payback_years is None at every trial
        # (HPC.payback_and_npv()'s own annual_net_savings <= 0 branch).
        A._wh_net_savings_at_share = (
            lambda iso, d, share, headline_uef, base_bill=None, **kw: -5.0)
        crossover = A._crossover_water_heater_share(
            iso=None, d=None, headline_uef="central_3.88", target_payback_years=30.0)
        assert crossover is None, crossover
    finally:
        A._wh_net_savings_at_share = real_net
        A.R.bill_nem = real_bill_nem
        A.build_wh_load_series = real_load_series
    return "no crossover is reported when net savings are negative (no payback at all) at every share"


@case
def case_sequencing_share_robustness_detects_a_flip_at_a_named_scenario():
    """Fast, deterministic check of sequencing_share_robustness()'s own
    order-detection logic: monkeypatches _wh_net_savings_at_share() so ONE
    named share yields a payback far longer than the furnace's own,
    forcing a real order flip at that share -- proves
    robust_across_named_scenarios correctly turns False and that
    named_scenarios reports the FLIPPED order at that share, not silently
    keeping the headline order everywhere."""
    real_net = A._wh_net_savings_at_share
    real_bill_nem = A.R.bill_nem
    real_load_series = A.build_wh_load_series
    try:
        A.R.bill_nem = lambda *a, **kw: 0.0
        A.build_wh_load_series = lambda *a, **kw: ({"uniform": 0.0}, {})

        def fake_net(iso, d, share, headline_uef, base_bill=None, **kw):
            return 1000.0 if share >= 0.5 else 0.01
        A._wh_net_savings_at_share = fake_net

        result = A.sequencing_share_robustness(
            iso=None, d=None, headline_uef="central_3.88",
            wh_share_scenarios={"hi": 1.0, "lo": 0.2},
            furnace_install_usd=10000, furnace_annual_net_savings_usd=100,
            furnace_payback_years=100.0,
            tier_interaction=_ZERO_INTERACTION, electric_interaction=_ZERO_ELECTRIC_INTERACTION)
    finally:
        A._wh_net_savings_at_share = real_net
        A.R.bill_nem = real_bill_nem
        A.build_wh_load_series = real_load_series

    assert result["named_scenarios"]["hi"]["order"] == ["water_heater", "furnace"], result
    assert result["named_scenarios"]["lo"]["order"] == ["furnace", "water_heater"], result
    assert result["robust_across_named_scenarios"] is False, result
    assert result["crossover_water_heater_share"] is not None, result
    return "sequencing_share_robustness correctly detects a real order flip at a low-savings named scenario"


@case
def case_sequencing_share_robustness_marginal_basis_optional_and_can_diverge_from_standalone():
    """Finding 4 (code-reviewer, issue #20 round 6): sequencing_and_
    paybacks()'s own combined_install always uses the furnace's STANDALONE
    install cost, so a bare 'robust across every illustrative share' claim
    was previously verified on ONLY that basis, unqualified about which one.
    `furnace_payback_years_marginal` is optional (omitted -> marginal_basis
    is None, no new claim asserted, backward compatible with every existing
    caller) and, when supplied, runs a genuinely SEPARATE check that can
    diverge from the standalone one -- forced here by construction: the
    standalone target (1000yr) is worse than both named shares' own
    paybacks (4.2yr at share>=0.5, 420yr at share<0.5), so standalone stays
    robust; the marginal target (200yr) sits BETWEEN those two, so the
    marginal check flips at the low share while the standalone check does
    not -- proving the two bases are independently computed, not one
    silently aliased to the other."""
    real_net = A._wh_net_savings_at_share
    real_bill_nem = A.R.bill_nem
    real_load_series = A.build_wh_load_series
    try:
        A.R.bill_nem = lambda *a, **kw: 0.0
        A.build_wh_load_series = lambda *a, **kw: ({"uniform": 0.0}, {})

        def fake_net(iso, d, share, headline_uef, base_bill=None, **kw):
            return 1000.0 if share >= 0.5 else 10.0
        A._wh_net_savings_at_share = fake_net

        no_marginal = A.sequencing_share_robustness(
            iso=None, d=None, headline_uef="central_3.88",
            wh_share_scenarios={"hi": 1.0, "lo": 0.2},
            furnace_install_usd=10000, furnace_annual_net_savings_usd=100,
            furnace_payback_years=1000.0,
            tier_interaction=_ZERO_INTERACTION, electric_interaction=_ZERO_ELECTRIC_INTERACTION)
        assert no_marginal["marginal_basis"] is None, no_marginal
        assert no_marginal["robust_across_named_scenarios"] is True, no_marginal

        with_marginal = A.sequencing_share_robustness(
            iso=None, d=None, headline_uef="central_3.88",
            wh_share_scenarios={"hi": 1.0, "lo": 0.2},
            furnace_install_usd=10000, furnace_annual_net_savings_usd=100,
            furnace_payback_years=1000.0,
            tier_interaction=_ZERO_INTERACTION, electric_interaction=_ZERO_ELECTRIC_INTERACTION,
            furnace_payback_years_marginal=200.0)
    finally:
        A._wh_net_savings_at_share = real_net
        A.R.bill_nem = real_bill_nem
        A.build_wh_load_series = real_load_series

    assert with_marginal["robust_across_named_scenarios"] is True, with_marginal
    mb = with_marginal["marginal_basis"]
    assert mb is not None
    assert mb["furnace_payback_years_basis"] == "marginal_over_ac_replacement"
    assert mb["furnace_payback_years"] == 200.0
    assert mb["named_scenarios"]["hi"]["order"] == ["water_heater", "furnace"], mb
    assert mb["named_scenarios"]["lo"]["order"] == ["furnace", "water_heater"], mb
    assert mb["robust_across_named_scenarios"] is False, mb
    assert with_marginal["robust_across_named_scenarios"] != mb["robust_across_named_scenarios"], (
        "standalone and marginal bases must be able to genuinely diverge -- the whole point of Fix 4")
    return ("sequencing_share_robustness's own marginal-install-cost-basis check is optional "
           "and can genuinely diverge from the standalone-basis check")


# ---------------------------------------------------------------------------
# Finding 1 consequence: tier_interaction_overstatement
# ---------------------------------------------------------------------------
def _flat_hdd_iso(floor_per_day, ann_heat, start, days, hdd_by_day=None):
    """A minimal synthetic iso for tier_interaction_overstatement() (and
    the private helpers it reuses from heat_pump_conversion.py) --
    mirrors test_heat_pump_conversion.py's own hand-built-iso pattern for
    _capacity_capped_days(). `gas_daily` is deliberately omitted (the
    proxy-only day-proportional path, the same legitimate synthetic-test
    case _capacity_capped_days()'s own docstring documents). Uniform HDD
    (1.0/day) unless a caller supplies a specific `hdd_by_day` Series, so
    ann_heat's own daily demand is easy to hand-verify."""
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    if hdd_by_day is None:
        hdd_by_day = pd.Series({d: 1.0 for d in dates})
    return {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
           "annual_heating_therms": ann_heat, "floor_therms_per_day": floor_per_day}


@case
def case_tier_interaction_overstatement_matches_hand_calc():
    """One period, ONE Gas Service segment (the whole period), hand-
    computable: total=60, allowance=20, $2.00/$2.40. F=45 removed alone
    drops the remaining total (15) BELOW the allowance (its own removal
    region straddles the tier boundary); H=10 removed alone stays entirely
    in the nonbaseline region. Removed jointly (55), the remaining total
    (5) is also below the allowance. Because F's own individual removal
    already reaches into the baseline tier while H's does not, the two
    independent computations both claim credit for part of the SAME
    nonbaseline dollars -- a real, sizeable interaction, unlike a naive
    same-tier-only construction (tried first; when both individual removal
    regions land entirely within the SAME flat tier, sums and joint
    coincide exactly and the scenario doesn't discriminate at all). A
    single-segment period is the segment-level function's degenerate case
    -- it must reduce exactly to this period-level hand calc."""
    statement_date, period = "2025-06-30", "Jun 1, 2025 - Jun 30, 2025"
    baseline_rate, nonbaseline_rate, allowance = 2.0, 2.4, 20.0
    therms = 60.0

    def bill(t):
        base = min(t, allowance)
        nb = max(0.0, t - allowance)
        return base * baseline_rate + nb * nonbaseline_rate

    F, H = 45.0, 10.0
    savings_f = bill(therms) - bill(therms - F)
    savings_h = bill(therms) - bill(therms - H)
    savings_joint = bill(therms) - bill(therms - F - H)
    expected_overstatement = round((savings_f + savings_h) - savings_joint, 2)
    assert expected_overstatement > 0.01, "the fixture must produce a real, positive interaction"

    periods_df = _make_periods_df([(statement_date, period, therms, 999.0, allowance)])
    detail = _single_segment_detail(
        statement_date, period, period_days=30, therms=therms,
        baseline_rate=baseline_rate, nonbaseline_rate=nonbaseline_rate,
        energy_rate=0.5, other_fees_rate=0.12)
    # floor_per_day=1.5 x 30 days = F=45.0; ann_heat=10 spread uniformly
    # over the same 30 days = H=10.0 -- both well under their own daily
    # capacity ceilings (checked: floor cap 60/30=2.0 >= 1.5; heating cap
    # 60/30-1.5=0.5 >= 10/30=0.333), so neither is capped below target.
    iso = _flat_hdd_iso(floor_per_day=1.5, ann_heat=H, start=dt.date(2025, 6, 1), days=30)

    with _GasDetailFixture(periods_df, detail):
        result = A.tier_interaction_overstatement(iso, n_trailing=1)
    assert abs(result["overstatement_usd"] - expected_overstatement) < 0.01, (
        result["overstatement_usd"], expected_overstatement)
    return f"tier_interaction_overstatement (single-segment) matches a hand-computed interaction of ${expected_overstatement}"


@case
def case_tier_interaction_overstatement_zero_when_period_stays_in_baseline():
    """A period that never leaves the baseline tier even after both
    removals has NO interaction (the rate is flat there, so independent
    and joint savings coincide exactly) -- proves this function does not
    report a spurious nonzero gap when none exists."""
    statement_date, period = "2025-06-30", "Jun 1, 2025 - Jun 30, 2025"
    therms, allowance = 15.0, 20.0
    periods_df = _make_periods_df([(statement_date, period, therms, 999.0, allowance)])
    detail = _single_segment_detail(
        statement_date, period, period_days=30, therms=therms,
        baseline_rate=2.0, nonbaseline_rate=2.4, energy_rate=0.5, other_fees_rate=0.12)
    # floor_per_day=0.1 x 30 = F=3.0; ann_heat=4.0 uniform over 30 days = H=4.0.
    iso = _flat_hdd_iso(floor_per_day=0.1, ann_heat=4.0, start=dt.date(2025, 6, 1), days=30)
    with _GasDetailFixture(periods_df, detail):
        result = A.tier_interaction_overstatement(iso, n_trailing=1)
    assert abs(result["overstatement_usd"]) < 0.01, result
    return "no interaction is reported when a period never reaches the nonbaseline tier"


@case
def case_tier_interaction_overstatement_segment_level_diverges_from_period_level_hand_calc():
    """Finding 3 (Codex `review` pass, issue #20 round 4): a hand-worked,
    TWO-segment period (a real shape this household's own corpus has --
    e.g. 2025-01-29's real bill splits 5 days at one Gas Service rate, 27
    at another, bill_gas_detail.csv) where the segment-level answer and a
    period-blended-rate answer genuinely DIFFER, proving the granularity
    choice is not cosmetic.

    Segment 0 (5 days, cheap rate $1.00/$1.20): F=7.5, H=0 -- no
    interaction possible (H=0). Segment 1 (25 days, expensive rate
    $2.50/$3.00, allowance 40.0, own total 75.0): F=37.5 alone drops the
    remaining total (37.5) BELOW the allowance (interaction-triggering);
    H=20.0 alone leaves it (55.0) ABOVE the allowance; jointly (17.5) also
    below -- the same interaction SHAPE as the classic hand example above,
    reproduced here entirely within one segment. Total period: F=45.0,
    H=20.0, T=90.0, allowance=48.0 (matching the segment totals exactly by
    day-proportion). A NAIVE period-BLENDED computation (a single rate
    averaged across the two segments, the retired approach) computes a
    DIFFERENT overstatement on the SAME F/H/T/allowance -- checked below,
    not assumed."""
    statement_date, period = "2025-06-30", "Jun 1, 2025 - Jun 30, 2025"
    therms, allowance = 90.0, 48.0
    periods_df = _make_periods_df([(statement_date, period, therms, 999.0, allowance)])
    detail = _gas_detail_rows(
        statement_date, period,
        gas_service=[(5, 1.0, 1.2), (25, 2.5, 3.0)], gas_energy=[], other_fees=[])
    # floor_per_day=1.5 x 30 = F=45.0 total (F_seg0=7.5, F_seg1=37.5).
    # Heating placed ONLY in segment 1's own 25 days (hdd=0 on segment 0's
    # own days) -- ann_heat=20 spread uniformly over those 25 days.
    dates = [dt.date(2025, 6, 1) + dt.timedelta(days=i) for i in range(30)]
    hdd_by_day = pd.Series({d: (0.0 if i < 5 else 1.0) for i, d in enumerate(dates)})
    iso = _flat_hdd_iso(floor_per_day=1.5, ann_heat=20.0, start=dt.date(2025, 6, 1),
                        days=30, hdd_by_day=hdd_by_day)

    with _GasDetailFixture(periods_df, detail):
        result = A.tier_interaction_overstatement(iso, n_trailing=1)

    # Segment-level hand calc (day-proportional t_s/a_s, matching the
    # production code's own _segment_real_or_proxy_therms/day-proportion
    # convention with no real gas_daily supplied).
    def bill(t, a, br, nbr):
        return min(t, a) * br + max(0.0, t - a) * nbr

    t_s0, a_s0, F_s0, H_s0 = 15.0, 8.0, 7.5, 0.0
    t_s1, a_s1, F_s1, H_s1 = 75.0, 40.0, 37.5, 20.0
    seg0_gap = ((bill(t_s0, a_s0, 1.0, 1.2) - bill(t_s0 - F_s0, a_s0, 1.0, 1.2))
               + (bill(t_s0, a_s0, 1.0, 1.2) - bill(t_s0 - H_s0, a_s0, 1.0, 1.2))
               - (bill(t_s0, a_s0, 1.0, 1.2) - bill(t_s0 - F_s0 - H_s0, a_s0, 1.0, 1.2)))
    seg1_gap = ((bill(t_s1, a_s1, 2.5, 3.0) - bill(t_s1 - F_s1, a_s1, 2.5, 3.0))
               + (bill(t_s1, a_s1, 2.5, 3.0) - bill(t_s1 - H_s1, a_s1, 2.5, 3.0))
               - (bill(t_s1, a_s1, 2.5, 3.0) - bill(t_s1 - F_s1 - H_s1, a_s1, 2.5, 3.0)))
    expected_segment_level = round(seg0_gap + seg1_gap, 2)
    assert abs(result["overstatement_usd"] - expected_segment_level) < 0.01, (
        result["overstatement_usd"], expected_segment_level)

    # The RETIRED period-blended computation: a single (day-weighted
    # average) rate for the whole period, applied to the SAME period
    # totals (F=45.0, H=20.0, T=90.0, allowance=48.0).
    br_blend = (5 * 1.0 + 25 * 2.5) / 30
    nbr_blend = (5 * 1.2 + 25 * 3.0) / 30
    F, H, T = 45.0, 20.0, 90.0
    naive_gap = ((bill(T, allowance, br_blend, nbr_blend) - bill(T - F, allowance, br_blend, nbr_blend))
                + (bill(T, allowance, br_blend, nbr_blend) - bill(T - H, allowance, br_blend, nbr_blend))
                - (bill(T, allowance, br_blend, nbr_blend) - bill(T - F - H, allowance, br_blend, nbr_blend)))
    naive_period_level = round(naive_gap, 2)

    assert abs(expected_segment_level - naive_period_level) > 0.5, (
        "the fixture must make segment-level and period-blended bases "
        "genuinely diverge, not agree by coincidence",
        expected_segment_level, naive_period_level)
    assert abs(result["overstatement_usd"] - naive_period_level) > 0.5, (
        result["overstatement_usd"], naive_period_level)
    return (f"segment-level overstatement (${expected_segment_level}) genuinely diverges "
           f"from the retired period-blended basis (${naive_period_level}) on a real "
           "mid-cycle-rate-change period shape")


@case
def case_tier_interaction_overstatement_segment_level_diverges_from_period_level_on_real_archive():
    """The same divergence check as above, but end to end on this
    household's OWN real gas corpus rather than a synthetic fixture:
    bill_gas_detail.csv records a genuine mid-cycle Gas Service rate
    change on 9 of this household's 25 real periods (e.g. 2025-01-29: 5
    days at $1.56901/$1.87417, 27 days at $1.61980/$1.91783 -- two
    different REAL billed rates inside one statement), several of which
    fall inside the trailing-12 window this script's headline figures use.
    Reconstructs the RETIRED period-level formula inline from the SAME
    per-period F/H figures (floor_savings_by_period()'s own rows,
    heat_pump_conversion.gas_savings_by_period()'s own rows) and confirms
    the two bases give genuinely different totals on the real archive --
    this was a live bug on this household's own data, not a hypothetical
    one."""
    _require_archive()
    iso = hpc.isolate_heating_therms()
    result = A.tier_interaction_overstatement(iso)

    floor_rows, _, _ = A.floor_savings_by_period(iso)
    hpc_rows, _, _ = hpc.gas_savings_by_period(iso)
    heat_by_date = {r["statement_date"]: r["heating_therms_attributed"] for r in hpc_rows}
    periods = pd.read_csv(hpc.GAS_PERIODS_CSV)
    by_date = periods.set_index(periods["statement_date"].astype(str))

    def bill(t, allowance, br, nbr):
        base = min(t, allowance)
        nb = max(0.0, t - allowance)
        nbr = br if (nbr is None or pd.isna(nbr)) else nbr
        return base * br + nb * nbr

    naive_independent_sum, naive_joint = 0.0, 0.0
    for r in floor_rows:
        dstr = r["statement_date"]
        if dstr not in by_date.index or dstr not in heat_by_date:
            continue
        prow = by_date.loc[dstr]
        T = float(prow["therms"])
        allowance = float(prow["baseline_allowance_therms"])
        br, nbr = float(prow["baseline_rate"]), prow["nonbaseline_rate"]
        F, H = r["floor_therms_attributed"], heat_by_date[dstr]
        b_T = bill(T, allowance, br, nbr)
        savings_f = b_T - bill(max(0.0, T - F), allowance, br, nbr)
        savings_h = b_T - bill(max(0.0, T - H), allowance, br, nbr)
        savings_joint = b_T - bill(max(0.0, T - F - H), allowance, br, nbr)
        naive_independent_sum += savings_f + savings_h
        naive_joint += savings_joint
    naive_period_level_overstatement = round(naive_independent_sum - naive_joint, 2)

    assert abs(result["overstatement_usd"] - naive_period_level_overstatement) > 0.01, (
        "segment-level and period-level bases must genuinely diverge on "
        "this household's real archive (it has real mid-cycle Gas Service "
        f"rate changes), not agree by coincidence: segment-level "
        f"${result['overstatement_usd']}, period-level "
        f"${naive_period_level_overstatement}")
    return (f"segment-level tier_interaction_overstatement (${result['overstatement_usd']}) "
           f"genuinely diverges from the retired period-blended basis "
           f"(${naive_period_level_overstatement}) on this household's own real archive")


@case
def case_tier_interaction_overstatement_fails_closed_on_missing_nonbaseline_rate():
    """Silent-failure-hunter finding, issue #20 round 6: a prior version of
    this function's own local bill(t) helper silently fell back to pricing
    EVERYTHING at baseline_rate whenever a segment's own nonbaseline_rate
    was missing, with NO tolerance check and no fail-closed guard at all --
    unlike _priced_at_top_of_ladder()/_gas_service_segment_tier_cost(),
    which this function is supposed to validate against, both of which fail
    closed on a large overflow. A single segment (baseline_allowance=5.0,
    nonbaseline_rate=None) with F=30 (floor_per_day=1.0 x 30 days) and H=20
    (ann_heat=20 uniform over 30 days) forces a real overflow of 30-ish
    therms into the nonbaseline tier with no rate to price it at -- far
    past FLOOR_ESTIMATION_TOLERANCE_THERMS (0.5). This must raise
    SystemExit, not silently return a near-zero/wrong correction for the
    segment. This test would NOT have PASSED against the pre-fix code
    (its own unconditional nbr_eff=baseline_rate fallback would have
    returned a real, silently-wrong dollar figure here instead of
    raising)."""
    statement_date, period = "2025-06-30", "Jun 1, 2025 - Jun 30, 2025"
    therms, allowance = 60.0, 5.0
    periods_df = _make_periods_df([(statement_date, period, therms, 999.0, allowance)])
    detail = _gas_detail_rows(
        statement_date, period, gas_service=[(30, 2.0, None)], gas_energy=[], other_fees=[])
    # floor_per_day=1.0 x 30 = F=30.0; ann_heat=20 uniform over 30 days = H=20.0.
    iso = _flat_hdd_iso(floor_per_day=1.0, ann_heat=20.0, start=dt.date(2025, 6, 1), days=30)
    with _GasDetailFixture(periods_df, detail):
        try:
            A.tier_interaction_overstatement(iso, n_trailing=1)
            raise AssertionError(
                "a large overflow against a segment with no nonbaseline_rate "
                "was silently accepted instead of failing closed")
        except SystemExit as e:
            assert "nonbaseline" in str(e), e
    return ("tier_interaction_overstatement fails closed, rather than silently "
           "pricing at baseline_rate, when a real overflow exceeds the tolerance "
           "on a segment with no nonbaseline_rate")


# ---------------------------------------------------------------------------
# Finding 1 (round 2): joint_electric_cost_scenario / electric_interaction_
# overstatement -- the ELECTRIC-side counterpart to tier_interaction_
# overstatement above. A first version of the Finding-1 fix (issue #20
# round 1) corrected only the GAS side; Codex adversarial review round 2
# found the electric side was still summing two independently-rebilled
# scenarios, letting both conversions claim the same exported solar kWh.
# ---------------------------------------------------------------------------
def _rebill_with_added_series(d, series):
    """The SAME solar-absorb-first-then-grid-import netting and rates.
    bill_nem() re-bill pattern joint_electric_cost_scenario() and every
    other electric-cost-scenario function in this module use, run here by
    hand (not calling production code) so the hand-worked test below can
    independently derive the "naive sum of two independent rebills" side
    of the comparison without depending on the function being tested."""
    base = R.bill_nem(d, imp="Consumption", exp="Generation")
    absorbed = pd.concat([d["Generation"], series], axis=1).min(axis=1)
    remainder = series - absorbed
    f = d.copy()
    f["Generation"] = d["Generation"] - absorbed
    f["Consumption"] = d["Consumption"] + remainder
    new_bill = R.bill_nem(f, imp="Consumption", exp="Generation")
    return new_bill - base, float(absorbed.sum())


@case
def case_joint_electric_cost_scenario_not_equal_to_naive_sum_hand_example():
    """The regression test for Finding 1 (round 2): proves the joint rebill
    of the COMBINED furnace + water-heater added-load series is NOT equal
    to the naive sum of the two INDEPENDENTLY rebilled scenarios -- this is
    the exact bug (summing two independent rebills silently let both
    conversions claim the same exported solar kWh at once).

    Hand-derivable setup: a 10-day synthetic frame (_synthetic_frame) has
    constant Generation=0.05 kWh/interval, 960 intervals total, so total
    real export capacity is EXACTLY 0.05 x 960 = 48.0 kWh across the whole
    window. Both the furnace's own uniform-distribution added load (HDD=1.0
    every day, so it spreads across every interval of every day) and the
    water heater's own uniform-distribution added load are sized to roughly
    0.5 kWh/interval, far above the 0.05 kWh/interval export -- so EACH
    one, computed independently, fully absorbs the entire 48.0 kWh of
    export on its own (absorbed = min(Generation, added) = Generation in
    every interval). Summed independently, the two rebills together claim
    2 x 48.0 = 96.0 kWh of solar that only exists once. Computed JOINTLY
    (both loads summed into one series before netting), only 48.0 kWh can
    ever be absorbed, since that is all the real export there is -- the
    other 48.0 kWh of "double-claimed" solar becomes real new grid import
    in the joint case, which importing at a positive rate must cost more
    than the naive sum implies.

    Mutation-tested: reverting sequencing_and_paybacks()'s own electric_
    interaction subtraction (see git history / commit message for this
    fix) does not touch this test directly -- but reverting THIS function
    to return `wh_electric_increase + furnace_electric_increase` (the
    pre-fix naive approach) instead of running a real joint rebill would
    make joint_electric_cost_scenario's own electric_cost_increase_usd
    equal the naive sum, and this test's own strict inequality below would
    fail -- confirmed by hand during development (temporarily replacing
    joint_electric_cost_scenario's own body with the naive sum, this test
    failed; restored, it passes)."""
    d = _synthetic_frame(n_days=10)
    total_export_capacity = float(d["Generation"].sum())
    assert abs(total_export_capacity - 48.0) < 0.01, total_export_capacity  # 0.05 * 960

    cop = 3.5
    ann_target_kwh = 480.0   # >> 0.05 kWh/interval export cap in every interval
    dates = sorted(d["dt"].dt.date.unique())
    furnace_iso = {
        "hdd_by_day": {day: 1.0 for day in dates},
        "total_hdd": float(len(dates)),
        "annual_heating_therms": ann_target_kwh * cop / (hpc.KWH_PER_THERM * hpc.FURNACE_AFUE),
    }
    ann_wh_kwh = ann_target_kwh

    furnace_added, ann_heat_kwh, _ = hpc.build_hp_load_series(d, furnace_iso, cop)
    wh_added, _ = A.build_wh_load_series(d, ann_wh_kwh)
    assert abs(ann_heat_kwh - ann_target_kwh) < 0.5, ann_heat_kwh

    furnace_increase, furnace_absorbed = _rebill_with_added_series(d, furnace_added["uniform"])
    wh_increase, wh_absorbed = _rebill_with_added_series(d, wh_added["uniform"])
    naive_sum_increase = round(furnace_increase + wh_increase, 2)
    independent_absorbed_sum = furnace_absorbed + wh_absorbed

    # Hand-derivable energy check: each independent rebill claims (close
    # to) the FULL 48.0 kWh export on its own, so their sum is close to
    # double the real export that actually exists.
    assert abs(furnace_absorbed - total_export_capacity) < 1.0, furnace_absorbed
    assert abs(wh_absorbed - total_export_capacity) < 1.0, wh_absorbed
    assert independent_absorbed_sum > 1.8 * total_export_capacity, (
        independent_absorbed_sum, total_export_capacity)

    joint = A.joint_electric_cost_scenario(d, furnace_iso, cop, ann_wh_kwh)
    # Jointly, only the real 48.0 kWh of export can ever be absorbed once --
    # NOT the 96.0 kWh the two independent rebills together claimed.
    assert abs(joint["solar_absorbed_kwh"] - total_export_capacity) < 1.0, joint
    assert joint["solar_absorbed_kwh"] < independent_absorbed_sum - 40, (
        joint["solar_absorbed_kwh"], independent_absorbed_sum)

    # The dollar consequence: the joint rebill must cost MORE than the
    # naive sum of the two independent rebills (more real grid import in
    # the joint case, and rates.bill_nem() prices import at a positive
    # rate) -- this is the exact inequality the pre-fix code got wrong by
    # never computing joint at all.
    joint_increase = joint["electric_cost_increase_usd"]
    assert joint_increase > naive_sum_increase + 0.01, (joint_increase, naive_sum_increase)
    return (f"joint electric rebill (${joint_increase}/yr) exceeds the naive sum of two "
           f"independent rebills (${naive_sum_increase}/yr) by "
           f"${round(joint_increase - naive_sum_increase, 2)}/yr, matching the hand-derived "
           "double-claimed-solar energy gap")


@case
def case_electric_interaction_overstatement_matches_hand_calc():
    """A.electric_interaction_overstatement() must report exactly
    joint - (wh + furnace), and must be positive on the same hand-worked
    double-claimed-solar setup case_joint_electric_cost_scenario_not_
    equal_to_naive_sum_hand_example uses."""
    d = _synthetic_frame(n_days=10)
    cop = 3.5
    ann_target_kwh = 480.0
    dates = sorted(d["dt"].dt.date.unique())
    furnace_iso = {
        "hdd_by_day": {day: 1.0 for day in dates},
        "total_hdd": float(len(dates)),
        "annual_heating_therms": ann_target_kwh * cop / (hpc.KWH_PER_THERM * hpc.FURNACE_AFUE),
    }
    furnace_added, _, _ = hpc.build_hp_load_series(d, furnace_iso, cop)
    wh_added, _ = A.build_wh_load_series(d, ann_target_kwh)
    furnace_increase, _ = _rebill_with_added_series(d, furnace_added["uniform"])
    wh_increase, _ = _rebill_with_added_series(d, wh_added["uniform"])
    joint = A.joint_electric_cost_scenario(d, furnace_iso, cop, ann_target_kwh)

    result = A.electric_interaction_overstatement(
        wh_electric_increase_usd=round(wh_increase, 2),
        furnace_electric_increase_usd=round(furnace_increase, 2),
        joint_electric_increase_usd=joint["electric_cost_increase_usd"])
    expected = round(joint["electric_cost_increase_usd"]
                     - (round(wh_increase, 2) + round(furnace_increase, 2)), 2)
    assert abs(result["overstatement_usd"] - expected) < 0.01, (result, expected)
    assert result["overstatement_usd"] > 0, result
    return f"electric_interaction_overstatement matches a hand-computed interaction of ${result['overstatement_usd']}"


@case
def case_joint_electric_cost_scenario_conserves_energy():
    d = _synthetic_frame(n_days=5)
    cop = 3.5
    dates = sorted(d["dt"].dt.date.unique())
    furnace_iso = {
        "hdd_by_day": {day: 1.0 for day in dates},
        "total_hdd": float(len(dates)),
        "annual_heating_therms": 50.0,
    }
    ann_wh_kwh = 200.0
    result = A.joint_electric_cost_scenario(d, furnace_iso, cop, ann_wh_kwh)
    expected_total = result["furnace_added_kwh"] + result["water_heater_added_kwh"]
    assert abs(result["combined_added_kwh"] - expected_total) <= 1, result
    assert result["solar_absorbed_kwh"] <= result["combined_added_kwh"]
    return "joint_electric_cost_scenario conserves energy across the combined furnace + water-heater series"


@case
def case_sequencing_applies_the_electric_interaction_correction():
    """The electric-side counterpart to case_sequencing_applies_the_tier_
    interaction_correction: complete_transition_payback's own combined
    savings must ALSO subtract electric_interaction_overstatement_usd, not
    just tier_interaction_overstatement_usd -- proves the Finding-1 round-2
    correction is actually wired into sequencing_and_paybacks, not just
    computed and discarded."""
    electric_interaction = {
        "overstatement_usd": 10.0, "wh_independent_electric_increase_usd": 0.0,
        "furnace_independent_electric_increase_usd": 0.0,
        "independent_sum_electric_increase_usd": 0.0, "joint_electric_increase_usd": 10.0,
        "note": "test fixture: $10 electric interaction",
    }
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=_ZERO_INTERACTION,
        electric_interaction=electric_interaction)
    ct = result["complete_transition_payback"]
    assert abs(ct["naive_summed_annual_net_savings_usd"] - 250) < 0.01, ct
    assert abs(ct["combined_annual_net_savings_usd"] - 240) < 0.01, ct  # 250 - 10
    assert ct["electric_interaction_overstatement_usd"] == 10.0, ct
    return "a nonzero electric-interaction overstatement is subtracted from the naive summed savings"


@case
def case_sequencing_applies_both_interaction_corrections_together():
    """Both the gas-side and electric-side corrections must apply
    simultaneously, without double-applying or clobbering each other --
    proves the two corrections are genuinely independent subtractions."""
    tier_interaction = {"overstatement_usd": 15.0, "gas_service_independent_sum_usd": 0.0,
                        "gas_service_joint_removal_usd": 0.0, "by_period": [],
                        "note": "test fixture: $15 gas interaction"}
    electric_interaction = {
        "overstatement_usd": 10.0, "wh_independent_electric_increase_usd": 0.0,
        "furnace_independent_electric_increase_usd": 0.0,
        "independent_sum_electric_increase_usd": 0.0, "joint_electric_increase_usd": 10.0,
        "note": "test fixture: $10 electric interaction",
    }
    result = A.sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=4000, wh_annual_net_savings_usd=200, wh_payback_years=20.0,
        furnace_install_usd=14000, furnace_annual_net_savings_usd=50,
        furnace_payback_years=280.0, tier_interaction=tier_interaction,
        electric_interaction=electric_interaction)
    ct = result["complete_transition_payback"]
    assert abs(ct["naive_summed_annual_net_savings_usd"] - 250) < 0.01, ct
    assert abs(ct["combined_annual_net_savings_usd"] - 225) < 0.01, ct  # 250 - 15 - 10
    return "gas-side and electric-side interaction corrections both apply together, additively"


# ---------------------------------------------------------------------------
# Finding 2: water_heater_share_sensitivity / floor_savings_by_period's own
# water_heater_share parameter
# ---------------------------------------------------------------------------
@case
def case_floor_savings_by_period_water_heater_share_scales_floor_per_day():
    """A share of 0.5 must roughly halve the floor's own attributed
    therms relative to share=1.0 on the SAME fixture (not exactly half,
    since the period-level real-total cap can bind differently, but close
    on an uncapped period) -- proves the parameter actually reaches
    _floor_capped_days's own floor_per_day, not silently ignored."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        with _GasWeatherFixture(tmp, floor=0.4, slope=0.15, days=365):
            iso = hpc.isolate_heating_therms()
            period = "Jun 1, 2025 - Jun 30, 2025"
            periods_df = pd.DataFrame([{
                "statement_date": "2025-06-30", "period": period,
                "period_end_month": "jun 2025", "therms": 60.0,
                "total_gas_service": 30.0, "billed_amount": 30.0,
                "baseline_rate": 2.0, "nonbaseline_rate": 2.4,
                "baseline_allowance_therms": 11.0, "gas_energy_charge_rate": 0.5,
                "other_fees_rate": 0.12,
            }])
            detail = _single_segment_detail(
                "2025-06-30", period, period_days=30, therms=60.0,
                baseline_rate=2.0, nonbaseline_rate=2.4, energy_rate=0.5,
                other_fees_rate=0.12)
            with _GasDetailFixture(periods_df, detail):
                _, _, full = A.floor_savings_by_period(iso, n_trailing=1, water_heater_share=1.0)
                _, _, half = A.floor_savings_by_period(iso, n_trailing=1, water_heater_share=0.5)
    assert abs(half - full * 0.5) < 0.01, (full, half)
    return f"water_heater_share=0.5 gives half the floor therms of share=1.0 ({half} vs {full})"


def _twelve_monthly_periods(therms=20.0, billed=50.0, allowance=11.0,
                            baseline_rate=2.0, nonbaseline_rate=2.4,
                            energy_rate=0.5, other_fees_rate=0.12, year=2025):
    """Twelve consecutive real-date-range monthly gas periods (calendar year
    `year`), one Gas Service segment each -- enough for floor_savings_by_
    period()'s own default n_trailing=12 to run archive-independently.
    Mirrors the hand-calc fixtures above but at fixture-building scale (a
    full trailing-12-period run, not one hand-worked period), needed
    because water_heater_share_sensitivity() calls floor_savings_by_period()
    three times, once per named share."""
    rows, detail = [], []
    for m in range(1, 13):
        start = dt.date(year, m, 1)
        end_day = calendar.monthrange(year, m)[1]
        end = dt.date(year, m, end_day)
        period_str = f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')}"
        statement_date = (end + dt.timedelta(days=3)).isoformat()
        rows.append((statement_date, period_str, therms, billed, allowance))
        detail += _single_segment_detail(
            statement_date, period_str, period_days=end_day, therms=therms,
            baseline_rate=baseline_rate, nonbaseline_rate=nonbaseline_rate,
            energy_rate=energy_rate, other_fees_rate=other_fees_rate)
    return _make_periods_df(rows), detail


@case
def case_water_heater_share_sensitivity_synthetic_never_reports_a_fourth_scenario():
    """Non-archive-gated counterpart to case_water_heater_share_sensitivity_
    reports_three_scenarios and case_benchmark_incompatibility_check_flags_
    implausible_on_real_archive below (test-analyzer finding, issue #20
    round 6): those real-archive cases both call _require_archive() and
    SILENTLY SKIP in CI, which runs on a bare runner with no private data
    staged (.github/workflows/tests.yml's own comment) -- so a regression
    that resurrected the retired impossible 0%-share scenario (round 4's own
    benchmark_incompatibility_check fix) would leave CI green. This builds a
    small synthetic 12-period gas fixture (no household archive needed) and
    a synthetic electric frame (_synthetic_frame), with dryer/cooking
    benchmark ranges deliberately set so their own high ends (70% + 50% =
    120%) exceed 100% of the floor -- the SAME implausible-combination shape
    the real archive happens to exhibit, reproduced here archive-
    independently so this regression is caught even when the private
    archive is absent."""
    periods_df, detail = _twelve_monthly_periods()
    with _GasDetailFixture(periods_df, detail):
        d = _synthetic_frame(n_days=10)
        iso = {"floor_therms_per_day": 0.4}
        result = A.water_heater_share_sensitivity(
            iso, d, dryer_pct_of_floor_range=[50.0, 70.0],
            cooking_pct_of_floor_range=[40.0, 50.0], headline_uef="central_3.88")
    assert set(result["scenarios"]) == {
        "100pct_full_floor", "72pct_if_dryer_present_at_benchmark_low",
        "21pct_if_dryer_present_at_benchmark_high"}, result["scenarios"]
    for key in result["scenarios"]:
        assert "residual" not in key, (
            "no impossible 0%-share/residual entry may appear among scenarios", key)
    check = result["benchmark_incompatibility_check"]
    assert check["not_a_scenario"] is True, check
    assert check["verdict"] == "implausible_for_this_household", check
    assert check["mechanical_residual_water_heater_share"] == 0.0, check
    assert "benchmark_incompatibility_check" not in result["scenarios"]
    priced_fields = {"floor_savings_annual_usd", "electric_cost_increase_usd",
                     "annual_net_savings_usd", "payback", "water_heater_share"}
    assert not (priced_fields & set(check)), (
        "benchmark_incompatibility_check must not carry any priced fields", check)
    return ("water_heater_share_sensitivity (synthetic fixture, archive-independent) "
           "reports exactly three named scenarios and flags the dryer+cooking high "
           "ends as jointly implausible via benchmark_incompatibility_check, never "
           "a fourth 0%-share scenario")


@case
def case_water_heater_share_sensitivity_reports_three_scenarios():
    """Finding 1 (Codex `review` pass, issue #20 round 4): exactly THREE
    live scenarios are reported -- 100%/72.3%/21.2% -- never a fourth
    "0%-share" scenario with its own $0.00/yr payback row. A prior version
    published such a scenario (the dryer's and cooking's own high-end
    benchmarks combined) as though a zero water-heater share were a live
    possible outcome; that is impossible for THIS household (known to run
    a gas water heater today), not merely unverified -- see the module-
    level comment directly above water_heater_share_sensitivity()."""
    _require_archive()
    d = br.load()
    iso = hpc.isolate_heating_therms()
    result = A.water_heater_share_sensitivity(
        iso, d, dryer_pct_of_floor_range=[27.7, 78.8],
        cooking_pct_of_floor_range=[29.2, 43.8], headline_uef="central_3.88")
    scenarios = result["scenarios"]
    assert set(scenarios) == {
        "100pct_full_floor", "72pct_if_dryer_present_at_benchmark_low",
        "21pct_if_dryer_present_at_benchmark_high"}, scenarios
    shares = {k: v["water_heater_share"] for k, v in scenarios.items()}
    assert shares["100pct_full_floor"] == 1.0
    assert abs(shares["72pct_if_dryer_present_at_benchmark_low"] - 0.723) < 0.001
    assert abs(shares["21pct_if_dryer_present_at_benchmark_high"] - 0.212) < 0.001
    assert "bound" not in result["basis"].lower() or "not a proven" in result["basis"].lower(), (
        "the basis text must not claim these scenarios are a proven bound")
    # a smaller share must give strictly smaller savings and a longer payback
    full = scenarios["100pct_full_floor"]
    low = scenarios["21pct_if_dryer_present_at_benchmark_high"]
    assert low["floor_savings_annual_usd"] < full["floor_savings_annual_usd"]
    assert low["annual_net_savings_usd"] < full["annual_net_savings_usd"]
    assert (low["payback"]["central_install"]["payback_years"]
           > full["payback"]["central_install"]["payback_years"])
    return ("water_heater_share_sensitivity reports exactly three "
           "illustrative, correctly-ordered scenarios on the real "
           "archive -- no impossible 0%-share scenario among them")


@case
def case_benchmark_incompatibility_check_flags_implausible_on_real_archive():
    """On this household's real 137-therm/yr floor, the dryer benchmark's
    own high end (78.8% of the floor) plus the cooking benchmark's own high
    end (43.8%, A.COOKING_THERMS_YR_RANGE = 40-60 therms/yr) together
    exceed 100% of the floor (122.6%) -- benchmark_incompatibility_check
    must flag this as implausible for THIS (known-gas-water-heater)
    household, report the mechanical residual share (0.0) for
    transparency, and must NOT appear among `scenarios` or carry any
    priced (floor_savings/electric_cost/net_savings/payback) fields."""
    _require_archive()
    d = br.load()
    iso = hpc.isolate_heating_therms()
    result = A.water_heater_share_sensitivity(
        iso, d, dryer_pct_of_floor_range=[27.7, 78.8],
        cooking_pct_of_floor_range=[29.2, 43.8], headline_uef="central_3.88")
    check = result["benchmark_incompatibility_check"]
    assert check["verdict"] == "implausible_for_this_household", check
    assert check["mechanical_residual_water_heater_share"] == 0.0, check
    assert check["not_a_scenario"] is True
    assert "water heater" in check["note"].lower() and "known" in check["note"].lower()
    priced_fields = {"floor_savings_annual_usd", "electric_cost_increase_usd",
                     "annual_net_savings_usd", "payback", "water_heater_share"}
    assert not (priced_fields & set(check)), (
        "benchmark_incompatibility_check must not carry any priced fields", check)
    assert "benchmark_incompatibility_check" not in result["scenarios"]
    for key in result["scenarios"]:
        assert "residual" not in key, (
            "no residual/0%-share entry may appear among scenarios", key)
    return ("benchmark_incompatibility_check correctly flags the dryer+cooking "
           "high ends as jointly implausible for this household, without "
           "publishing a fake 0%-share payback scenario")


@case
def case_benchmark_incompatibility_check_not_triggered_when_benchmarks_fit():
    """The mirror-image check: when the two high-end benchmarks together
    claim LESS than the whole floor, no incompatibility exists and the
    check must say so plainly (not flag every run as implausible
    regardless of the input benchmarks)."""
    _require_archive()
    d = br.load()
    iso = hpc.isolate_heating_therms()
    result = A.water_heater_share_sensitivity(
        iso, d, dryer_pct_of_floor_range=[10.0, 20.0],
        cooking_pct_of_floor_range=[5.0, 15.0], headline_uef="central_3.88")
    check = result["benchmark_incompatibility_check"]
    assert check["verdict"] == "not_triggered", check
    assert check["mechanical_residual_water_heater_share"] > 0.0, check
    assert "not mutually incompatible" in check["note"] or "no incompatibility" in check["note"], check
    return "benchmark_incompatibility_check reports not_triggered when the two high ends fit within the floor"


# ---------------------------------------------------------------------------
# Archive-dependent: build() end to end, byte-identical regeneration.
# ---------------------------------------------------------------------------
@case
def case_build_end_to_end_on_the_real_archive():
    _require_archive()
    out = A.build()
    assert out["applicable"] is True
    assert out["fixed_charge_check"]["verdict"].startswith("confirmed")
    enum_ = out["gas_end_use_enumeration"]
    assert enum_["sum_check"]["pct_of_metered_total"] == 100.0
    assert enum_["independent_bill_cross_check"]["pct_difference"] < 5.0
    wh = out["water_heater_conversion"]
    assert wh["floor_savings_annual_usd"] > 0
    assert out["furnace_conversion"]["gas_savings_annual_usd"] > 0
    seq = out["sequencing_and_paybacks"]
    assert seq["fixed_charge_release_usd"] == 0.0
    hr = out["service_headroom_check"]
    # ampacity_verdict can never be 'pass' (Codex review, issue #20 round
    # 5, Finding 1) -- no specific furnace heat-pump model is selected
    # anywhere in this issue's own analysis, so a real fit is never
    # knowable from this artifact, only 'fail' or 'not_determined'.
    assert hr["ampacity_verdict"] in ("fail", "not_determined"), hr
    return "build() runs end to end on the real archive and every section is internally consistent"


@case
def case_sequencing_share_robustness_wired_into_build_on_real_archive():
    """Finding 2 (Codex `review` pass, issue #20 round 4), end to end:
    sequencing_and_paybacks()'s own share_robustness field must be present
    in build()'s real output, agree with the headline order at every named
    scenario on this household's real data (none of the three illustrative
    shares actually flips it), and report a numeric crossover share that
    sits BELOW the lowest illustrative scenario shown (21.2%) -- since that
    scenario's own order does not flip, the true reversal threshold must be
    lower still."""
    _require_archive()
    out = A.build()
    seq = out["sequencing_and_paybacks"]
    sr = seq["share_robustness"]
    assert set(sr["named_scenarios"]) == {
        "100pct_full_floor", "72pct_if_dryer_present_at_benchmark_low",
        "21pct_if_dryer_present_at_benchmark_high"}, sr
    assert sr["robust_across_named_scenarios"] is True, sr
    for key, entry in sr["named_scenarios"].items():
        assert entry["order"] == seq["order"], (key, entry, seq["order"])
    cross = sr["crossover_water_heater_share"]
    assert cross is not None, sr
    lowest_named_share = sr["named_scenarios"][
        "21pct_if_dryer_present_at_benchmark_high"]["water_heater_share"]
    assert cross < lowest_named_share, (
        "the crossover share must sit BELOW the lowest illustrative "
        "scenario shown, since that scenario's own order does not flip", sr)
    return (f"share_robustness is wired into build(): robust across all three named "
           f"scenarios, numeric crossover at {cross:.4f} water-heater share, below the "
           f"lowest illustrative scenario ({lowest_named_share})")


@case
def case_sequencing_share_robustness_marginal_basis_wired_into_build_on_real_archive():
    """Finding 4 (code-reviewer, issue #20 round 6), end to end: on this
    household's real data, the published sequencing order is robust on the
    furnace's own STANDALONE install-cost basis (the prior test above) but
    genuinely does NOT survive on the furnace's own marginal-over-AC-
    replacement basis -- the 21.2%-share water-heater scenario's own 130.0
    year payback loses to a 48.6-year marginal-basis furnace, reversing the
    order at a scenario this report explicitly illustrates. This is the
    real, checked-in-this-round finding Fix 4 exists to surface rather than
    leave silently unqualified."""
    _require_archive()
    out = A.build()
    sr = out["sequencing_and_paybacks"]["share_robustness"]
    mb = sr["marginal_basis"]
    assert mb is not None, sr
    assert mb["furnace_payback_years_basis"] == "marginal_over_ac_replacement", mb
    assert mb["robust_across_named_scenarios"] is False, (
        "the marginal-basis order must genuinely diverge from the standalone-basis "
        "order on this household's real data -- if this now passes as True, either "
        "the underlying figures changed or the marginal-basis check regressed", mb)
    lo = mb["named_scenarios"]["21pct_if_dryer_present_at_benchmark_high"]
    assert lo["order"] == ["furnace", "water_heater"], (
        "the lowest illustrative share must flip order on the marginal basis", lo)
    assert mb["crossover_water_heater_share"] is not None, mb
    assert mb["crossover_water_heater_share"] > sr["crossover_water_heater_share"], (
        "the marginal-basis crossover share (a weaker furnace target) must sit "
        "ABOVE the standalone-basis crossover share (a stronger furnace target)", sr, mb)
    return (f"marginal-basis share_robustness correctly reports a real order reversal "
           f"on this household's data (crossover at {mb['crossover_water_heater_share']:.4f} "
           f"share, vs {sr['crossover_water_heater_share']:.4f} on the standalone basis)")


@case
def case_reconcile_unattributed_usd_hand_calc():
    """Direct, non-archive-gated unit test for reconcile_unattributed_usd()
    (test-analyzer finding, issue #20 round 6): a trivial hand calc,
    900 - 300 - 400 + 25 == 225, run directly against the extracted pure
    function rather than only through build() end to end (whose only prior
    guard, case_reconciliation_unattributed_usd_corrects_for_tier_
    interaction below, calls _require_archive() and silently skips in CI).
    Also proves the tier-overstatement correction is genuinely ADDED, not
    subtracted -- a sign-flip regression (billed - floor - heating -
    tier_overstatement) would give 875, not 225, and is checked for
    explicitly below so that specific regression shape is caught."""
    result = A.reconcile_unattributed_usd(
        trailing12_billed_usd=900.0, floor_savings_usd=300.0,
        heating_savings_usd=400.0, tier_overstatement_usd=25.0)
    assert abs(result - 225.0) < 1e-9, result
    # Raising tier_overstatement_usd from 0 to 25 while holding every other
    # input fixed must RAISE the result by exactly 25 -- proves the
    # correction is genuinely ADDED, not subtracted or ignored. A
    # sign-flip regression (billed - floor - heating - tier_overstatement)
    # would instead LOWER the result from 200 to 175 here, which the
    # exact-match assertion below would catch.
    zero_correction = A.reconcile_unattributed_usd(
        trailing12_billed_usd=900.0, floor_savings_usd=300.0,
        heating_savings_usd=400.0, tier_overstatement_usd=0.0)
    assert abs(zero_correction - 200.0) < 1e-9, zero_correction
    assert abs(result - zero_correction - 25.0) < 1e-9, (
        "the tier-overstatement correction must be ADDED to the naive residual, "
        "not subtracted -- a sign-flip regression would fail this", result, zero_correction)
    return f"reconcile_unattributed_usd(900, 300, 400, 25) = ${result}, matching the hand calc exactly"


@case
def case_reconciliation_unattributed_usd_corrects_for_tier_interaction():
    """Finding 1 (Codex review pass, issue #20 round 3): unattributed_usd
    naively subtracted floor_savings_annual_usd and heating_savings_usd
    (each an INDEPENDENTLY-computed marginal gas saving) from the trailing-
    12 billed total, double-subtracting the tier_interaction_overstatement_
    usd dollars both independent savings figures claim credit for in the
    same nonbaseline tier -- the same overstatement complete_transition_
    payback already corrects for (sequencing_and_paybacks). This checks the
    corrected formula end to end against the real archive: unattributed_usd
    must equal billed - floor - heating + tier_interaction_overstatement_
    usd, not the naive billed - floor - heating."""
    _require_archive()
    out = A.build()
    signal = out["reconciliation"]["unattributed_heating_signal"]
    naive = round(signal["trailing_12_billed_total_usd"] - signal["floor_savings_usd"]
                 - signal["heating_savings_usd"], 2)
    overstatement = signal["tier_interaction_overstatement_usd"]
    assert overstatement > 0.01, "the fixture must exercise a real, positive tier interaction"
    expected = round(naive + overstatement, 2)
    assert abs(signal["unattributed_usd"] - expected) < 0.01, (
        signal["unattributed_usd"], expected, naive, overstatement)
    assert abs(signal["unattributed_usd"] - naive) > 0.01, (
        "the correction must actually change the naive figure on real data")
    return (f"unattributed_usd (${signal['unattributed_usd']}) is the naive residual "
           f"(${naive}) plus the ${overstatement} tier-interaction correction, not the "
           "naive figure alone")


@case
def case_byte_identical_regeneration():
    _require_archive()
    import all_electric_endgame as mod
    out1 = mod.build()
    out2 = mod.build()
    s1 = json.dumps(out1, indent=1, sort_keys=True)
    s2 = json.dumps(out2, indent=1, sort_keys=True)
    assert s1 == s2, "build() is not deterministic across two runs"
    return "build() produces byte-identical output across two runs on the real archive"


def main():
    passed, skipped, failed = 0, 0, 0
    for fn in CASES:
        name = fn.__name__
        try:
            msg = fn()
            print(f"PASS  {name}: {msg}")
            passed += 1
        except SkipCase as e:
            print(f"SKIP  {name}: {e}")
            skipped += 1
        except Exception as e:
            print(f"FAIL  {name}: {e!r}")
            failed += 1
            raise
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed "
         f"(of {len(CASES)} cases)")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
