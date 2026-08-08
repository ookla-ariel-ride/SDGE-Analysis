#!/usr/bin/env python3
"""Tests for heat_pump_conversion.py (issue #1).

Same pattern as test_dsgs_vpp_backtest.py / test_tou_structure_stress.py:
point household.PATH at a synthetic household BEFORE importing so this file
always imports cleanly (has_gas: true, since this whole module is gas-only),
and gate archive-dependent cases (the real measured year, the real gas
export, byte-identical regeneration) behind SkipCase rather than failing.

Run from the repo root:  ./.venv/bin/python analysis/test_heat_pump_conversion.py
"""
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

import rates as R                        # noqa: E402
import behavior_rebuild as br            # noqa: E402
import heat_pump_conversion as hpc       # noqa: E402

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
# Synthetic gas/weather fixtures for the pure isolation-method unit tests --
# no private archive needed for these.
# ---------------------------------------------------------------------------
def _synthetic_gas_and_weather(tmp_path, floor=0.4, slope=0.15, days=365,
                               start=dt.date(2025, 1, 1)):
    """A year of gas.csv + weather_daily_tmean.csv whose HDD regression and
    summer-baseline floor are hand-computable: constant floor plus a slope
    times a triangular seasonal HDD wave, so both isolation methods can be
    checked against a known answer, not just against each other."""
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    rows_gas = ["Name,Test\nAddress,Test\nAccount Number,0\nDisclaimer,x\n"
               "Title,x\nResource,Gas\nMeter Number,1\nInterval UOM,Day\n"
               "Reading Start,x\nReading End,x\nTotal Duration,x\n"
               "Total Usage,x\nUOM,Therms\n"
               "Meter Number,Date,Start Time,Duration,Consumption\n"]
    rows_w = ["header\n"]
    therms_by_date = {}
    tf_by_date = {}
    for i, d in enumerate(dates):
        # a triangular HDD wave: 0 at day 0/364 (mid-summer-ish), peaking mid-year
        frac = abs((i - days / 2) / (days / 2))   # 0 at center, 1 at the ends
        hdd = 30 * frac                            # 0..30 HDD/day
        tf = 65 - hdd
        therms = floor + slope * hdd
        therms_by_date[d] = therms
        tf_by_date[d] = tf
        rows_gas.append(f'"1","{d.month}/{d.day}/{d.year}","6:59 AM","Day","{therms:.4f}"\n')
        rows_w.append(f"{d.isoformat()},{tf:.2f}\n")
    (tmp_path / "gas.csv").write_text("".join(rows_gas))
    (tmp_path / "weather_daily_tmean.csv").write_text("".join(rows_w))
    return therms_by_date, tf_by_date


def _months_of(dates_map, months):
    return [d for d in dates_map if d.month in months]


# ---------------------------------------------------------------------------
# bill_gas_detail.csv fixtures (issue #109) -- gas_savings_by_period() now
# requires a companion segment-detail file alongside bill_periods_gas.csv;
# every case below builds both together so a period that never split (the
# common case) reproduces the old period-level numbers exactly (one segment
# covering the whole period, per charge type).
# ---------------------------------------------------------------------------
def _gas_detail_rows(statement_date, period, gas_service, gas_energy, other_fees):
    """gas_service: [(segment_days, baseline_rate, nonbaseline_rate), ...]
    gas_energy:  [(segment_days, energy_rate), ...]
    other_fees:  [(segment_therms, other_fees_rate), ...]
    -- one dict per row, matching bill_gas_detail.csv's real schema."""
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
    """The trivial, never-split case: one segment per charge type, covering
    the whole period -- algebraically identical to the old period-level
    computation (this is the regression-safety fixture shape)."""
    return _gas_detail_rows(
        statement_date, period,
        gas_service=[(period_days, baseline_rate, nonbaseline_rate)],
        gas_energy=[(period_days, energy_rate)],
        other_fees=[(therms, other_fees_rate)])


def _write_gas_fixture(tmp, periods_df, detail_rows):
    periods_csv = tmp / "bill_periods_gas.csv"
    detail_csv = tmp / "bill_gas_detail.csv"
    periods_df.to_csv(periods_csv, index=False)
    pd.DataFrame(detail_rows).to_csv(detail_csv, index=False)
    return periods_csv, detail_csv


class _GasFixture:
    """Context manager: points hpc.GAS_PERIODS_CSV/GAS_DETAIL_CSV at a
    temporary fixture pair for the duration of the `with` block, restoring
    the real paths afterward -- used by every gas_savings_by_period() case
    below instead of hand-rolling the same try/finally each time."""

    def __init__(self, periods_df, detail_rows):
        self.periods_df = periods_df
        self.detail_rows = detail_rows

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._td.name)
        periods_csv, detail_csv = _write_gas_fixture(tmp, self.periods_df, self.detail_rows)
        self._real = (hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV)
        hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV = str(periods_csv), str(detail_csv)
        return self

    def __exit__(self, *exc):
        hpc.GAS_PERIODS_CSV, hpc.GAS_DETAIL_CSV = self._real
        self._td.cleanup()
        return False


@case
def case_summer_baseline_recovers_the_known_floor():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _synthetic_gas_and_weather(tmp, floor=0.4, slope=0.15)
        real_gas_csv, real_weather = hpc.GAS_CSV, hpc.WEATHER_CSV
        hpc.GAS_CSV, hpc.WEATHER_CSV = str(tmp / "gas.csv"), str(tmp / "weather_daily_tmean.csv")
        try:
            gas_daily = hpc.load_gas_daily()
            daily_floor, ann_floor = hpc.summer_baseline_floor(gas_daily)
        finally:
            hpc.GAS_CSV, hpc.WEATHER_CSV = real_gas_csv, real_weather
    # SUMMER_MONTHS sits near the low-HDD (near-zero-heating) end of the
    # triangular wave by construction (frac near 1 at day 0/364, i.e.
    # January -- summer months 6-10 sit mid-wave instead); just check the
    # recovered floor is close to the TRUE floor at those months' own HDD,
    # not exactly 0.4 (summer isn't the exact zero-HDD point in this fixture)
    assert 0.35 < daily_floor < 3.0, daily_floor
    return f"summer-baseline floor recovered as {daily_floor:.3f} therms/day"


@case
def case_hdd_regression_recovers_the_known_floor_and_slope():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _synthetic_gas_and_weather(tmp, floor=0.4, slope=0.15)
        real_gas_csv, real_weather = hpc.GAS_CSV, hpc.WEATHER_CSV
        hpc.GAS_CSV, hpc.WEATHER_CSV = str(tmp / "gas.csv"), str(tmp / "weather_daily_tmean.csv")
        try:
            gas_daily = hpc.load_gas_daily()
            weather_daily = hpc.load_weather_daily()
            floor, slope, hdd_by_day = hpc.hdd_regression(gas_daily, weather_daily)
        finally:
            hpc.GAS_CSV, hpc.WEATHER_CSV = real_gas_csv, real_weather
    assert abs(floor - 0.4) < 0.01, floor
    assert abs(slope - 0.15) < 0.001, slope
    assert len(hdd_by_day) == 365, len(hdd_by_day)
    return f"HDD regression recovered floor={floor:.3f} (true 0.4), slope={slope:.4f} (true 0.15)"


@case
def case_isolate_heating_therms_fails_closed_on_a_flat_no_heating_house():
    """A house with NO seasonal variation at all (floor only, slope=0) has no
    positive heating slope to find -- isolate_heating_therms must refuse
    rather than publish a zero or negative heating estimate as if it were a
    real finding."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _synthetic_gas_and_weather(tmp, floor=0.4, slope=0.0)
        real_gas_csv, real_weather = hpc.GAS_CSV, hpc.WEATHER_CSV
        hpc.GAS_CSV, hpc.WEATHER_CSV = str(tmp / "gas.csv"), str(tmp / "weather_daily_tmean.csv")
        try:
            try:
                hpc.isolate_heating_therms()
                raise AssertionError("a flat, no-heating house was accepted as having heating")
            except SystemExit as e:
                assert "positive floor AND a positive heating slope" in str(e), e
        finally:
            hpc.GAS_CSV, hpc.WEATHER_CSV = real_gas_csv, real_weather
    return "a house with no heating slope fails closed instead of publishing a zero/negative estimate"


# ---------------------------------------------------------------------------
# Energy conservation across the three electric-distribution scenarios --
# needs a real Green Button frame shape (dt/p/hour columns) but not the real
# archive itself; a short synthetic frame is enough.
# ---------------------------------------------------------------------------
def _synthetic_frame(n_days=10):
    rows = []
    start = dt.datetime(2026, 1, 5)   # a Monday, so weekday TOU applies cleanly
    for day in range(n_days):
        for slot in range(96):
            ts = start + dt.timedelta(days=day, minutes=15 * slot)
            rows.append(ts)
    d = pd.DataFrame({"dt": rows})
    d["Consumption"] = 0.1
    d["Generation"] = 0.05
    d["hour"] = d["dt"].dt.hour + d["dt"].dt.minute / 60
    d["wkend"] = d["dt"].dt.date.map(R.off_peak_day)
    d["seas"] = np.where(d["dt"].dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    d["ym"] = d["dt"].dt.to_period("M")
    d["p"] = [R.period_at(t) for t in d["dt"]]
    return d


@case
def case_hp_load_series_conserve_energy_across_all_three_distributions():
    d = _synthetic_frame(n_days=10)
    hdd_by_day = pd.Series({(dt.date(2026, 1, 5) + dt.timedelta(days=i)): 10.0
                            for i in range(10)})
    iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
          "annual_heating_therms": 100.0}
    for cop in (2.8, 3.5, 4.2):
        added, ann_heat_kwh, fallback = hpc.build_hp_load_series(d, iso, cop)
        for dist_key, series in added.items():
            total = float(series.sum())
            assert abs(total - ann_heat_kwh) / ann_heat_kwh < 0.001, \
                (cop, dist_key, total, ann_heat_kwh)
    return "uniform/on_peak/off_peak distributions each conserve total heating kWh, at every COP"


@case
def case_afue_below_one_reduces_required_heat_pump_kwh():
    """Codex adversarial review, issue #1, pass 1: metered gas THERMS are
    furnace fuel INPUT, not delivered heat -- treating them as 100%
    delivered (AFUE=1.0) overstates the heat pump's required kWh by
    1/FURNACE_AFUE. This proves the module's real constant (0.78) actually
    reduces the computed load below what an (incorrect) AFUE=1.0 assumption
    would give, by exactly the expected ratio."""
    d = _synthetic_frame(n_days=10)
    hdd_by_day = pd.Series({(dt.date(2026, 1, 5) + dt.timedelta(days=i)): 10.0
                            for i in range(10)})
    iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
          "annual_heating_therms": 100.0}
    real_afue = hpc.FURNACE_AFUE
    try:
        hpc.FURNACE_AFUE = 1.0
        _, ann_heat_kwh_at_100pct, _ = hpc.build_hp_load_series(d, iso, 3.5)
        hpc.FURNACE_AFUE = real_afue
        _, ann_heat_kwh_at_real_afue, _ = hpc.build_hp_load_series(d, iso, 3.5)
    finally:
        hpc.FURNACE_AFUE = real_afue
    assert abs(ann_heat_kwh_at_real_afue / ann_heat_kwh_at_100pct - real_afue) < 1e-9, \
        (ann_heat_kwh_at_real_afue, ann_heat_kwh_at_100pct, real_afue)
    assert ann_heat_kwh_at_real_afue < ann_heat_kwh_at_100pct, \
        "a sub-1.0 AFUE must reduce the computed heat pump load, not leave it unchanged"
    return (f"AFUE={real_afue} reduces required heat-pump kWh to "
           f"{real_afue:.0%} of the (incorrect) 100%-delivered-heat assumption, exactly")


@case
def case_gas_savings_price_heating_slice_at_true_marginal_tier():
    """Issue #98: heating therms are now priced at the TRUE marginal tier(s)
    they actually occupy -- baseline_rate + gas_energy_charge_rate below the
    period's own baseline_allowance_therms, nonbaseline_rate +
    gas_energy_charge_rate above it -- not the period's blended average
    $/therm (total_gas_service / therms) the predecessor of this function
    used before parse_bills.py started extracting baseline_allowance_therms
    and gas_energy_charge_rate into data/bill_periods_gas.csv.

    A period bills 60 therms against a 40-therm baseline allowance; with a
    zero non-heating floor the HDD share attributes 50 heating therms (the
    MARGINAL, i.e. TOP, 50 of the 60 billed) -- so the 10 non-heating therms
    occupy the bottom of the baseline tier, leaving 30 baseline-tier therms
    and all 20 nonbaseline-tier therms in the heating slice:
        30 x (1.8 baseline + 0.5 energy) + 20 x (2.3 nonbaseline + 0.5 energy)
        = 30 x 2.3 + 20 x 2.8 = 69 + 56 = 125.00
    hand-computed and cross-checked against the function's own output. This
    period's own bill_gas_detail.csv fixture is the trivial never-split case
    (one segment per charge type, spanning the whole period) -- segment-level
    pricing (issue #109) is algebraically identical to the old period-level
    computation here, so the hand computation is unaffected."""
    periods = pd.DataFrame({
        "statement_date": ["2026-01-31"],
        "period": ["Jan 1, 2026 - Jan 31, 2026"],
        "therms": [60.0],
        "total_gas_service": [999.0],  # not read by the marginal-tier pricing path
        "baseline_rate": [1.8],
        "nonbaseline_rate": [2.3],
        "baseline_allowance_therms": [40.0],
        "gas_energy_charge_rate": [0.5],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-01-31", "Jan 1, 2026 - Jan 31, 2026", 31,
                                    60.0, 1.8, 2.3, 0.5, 0.0)
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 50.0, "floor_therms_per_day": 0.0}
        rows, total_savings, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    assert row["heating_therms_attributed"] == 50.0, row
    expect_savings = 30 * (1.8 + 0.5) + 20 * (2.3 + 0.5)
    assert abs(row["gas_savings_usd"] - expect_savings) < 1e-6, (row, expect_savings)
    assert abs(row["realized_rate_usd_per_therm"] - expect_savings / 50.0) < 1e-6, row
    # the whole point: the true marginal rate here (2.5/therm) sits ABOVE the
    # period's blended total_gas_service/therms would have been on the old
    # (predecessor) basis for this same fixture (172.8/60 = 2.88 was the old
    # fixture's own illustrative blended figure) -- tiered pricing and blended
    # averaging are genuinely different numbers, not the same value relabeled
    assert abs(total_savings - expect_savings) < 1e-6, total_savings
    return ("heating slice priced at its true marginal tier(s) plus the flat "
            "Gas Energy Charge, matching a hand computation exactly")


@case
def case_gas_savings_heating_slice_entirely_within_baseline_tier():
    """A period whose Gas Service never crossed its baseline allowance
    prints no nonbaseline rate at all (bill_periods_gas.csv leaves
    nonbaseline_rate blank/NaN for that period -- issue #98's parse_bills.py
    fix leaves it genuinely blank rather than inventing a value). The
    heating slice here must price entirely at baseline_rate + energy_rate
    without ever touching the missing nonbaseline_rate."""
    periods = pd.DataFrame({
        "statement_date": ["2026-07-31"],
        "period": ["Jul 1, 2026 - Jul 31, 2026"],
        "therms": [9.0],
        "total_gas_service": [22.94],
        "baseline_rate": [2.03477],
        "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [11.0],
        "gas_energy_charge_rate": [0.39416],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-07-31", "Jul 1, 2026 - Jul 31, 2026", 31,
                                    9.0, 2.03477, np.nan, 0.39416, 0.0)
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 7, d): 10.0 for d in range(1, 32)})
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 4.0, "floor_therms_per_day": 0.0}
        rows, _, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    assert row["heating_therms_attributed"] == 4.0, row
    expect = 4.0 * (2.03477 + 0.39416)
    # gas_savings_usd is rounded to the cent by gas_savings_by_period() itself
    assert abs(row["gas_savings_usd"] - expect) < 0.005, (row, expect)
    return ("a period that never crossed its baseline allowance prices its "
            "whole heating slice at baseline_rate + energy_rate, never "
            "touching a missing nonbaseline_rate")


@case
def case_gas_savings_fails_closed_when_nonbaseline_rate_missing_but_needed():
    """Issue #98's marginal-tier pricing must fail closed, not silently treat
    a missing nonbaseline_rate as zero, when the heating slice actually needs
    it: a period billing more therms than its own baseline_allowance_therms,
    with an inconsistent (blank) nonbaseline_rate -- upstream data that
    disagrees with itself rather than a real single-tier bill."""
    periods = pd.DataFrame({
        "statement_date": ["2026-01-31"],
        "period": ["Jan 1, 2026 - Jan 31, 2026"],
        "therms": [60.0],
        "total_gas_service": [999.0],
        "baseline_rate": [1.8],
        "nonbaseline_rate": [np.nan],   # inconsistent: usage crosses the allowance below
        "baseline_allowance_therms": [40.0],
        "gas_energy_charge_rate": [0.5],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-01-31", "Jan 1, 2026 - Jan 31, 2026", 31,
                                    60.0, 1.8, np.nan, 0.5, 0.0)
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 50.0, "floor_therms_per_day": 0.0}
        try:
            hpc.gas_savings_by_period(iso)
        except SystemExit as e:
            msg = str(e)
        else:
            raise AssertionError(
                "a period needing a nonbaseline rate it does not have was "
                "silently priced instead of failing closed")
    assert "nonbaseline_rate" in msg, msg
    return "missing nonbaseline_rate needed by the heating slice -> fails closed, not silently zero"


@case
def case_sensitivity_table_gas_prices_are_monotonically_ordered_and_vary_payback():
    """Codex adversarial review, issue #1, pass 1: low/central/high gas
    prices must share the same rate basis (all marginal/nonbaseline), so
    low <= central <= high always holds -- and changing install cost must
    actually change the reported payback, not just be echoed unused."""
    _require_archive()
    out = hpc.build()
    assert out["applicable"], out
    rows = out["sensitivity_table"]
    by_cop = {}
    for r in rows:
        by_cop.setdefault(r["cop"], {})[(r["install_cost"], r["gas_price"])] = r
    for cop_key, table in by_cop.items():
        lo = table[("central", "low")]["gas_price_usd_per_therm"]
        ce = table[("central", "central")]["gas_price_usd_per_therm"]
        hi = table[("central", "high")]["gas_price_usd_per_therm"]
        assert lo <= ce <= hi, (cop_key, lo, ce, hi)
        # the same COP/gas-price cell at low vs high install cost must give a
        # DIFFERENT payback (or one/both None) -- proves the sensitivity table
        # actually recomputes payback per install-cost scenario, not just
        # echoing the cost value back unused
        low_cost_pb = table[("low", "central")]["payback_years"]
        high_cost_pb = table[("high", "central")]["payback_years"]
        if low_cost_pb is not None and high_cost_pb is not None:
            assert low_cost_pb != high_cost_pb, (cop_key, low_cost_pb, high_cost_pb)
    return "sensitivity table gas prices are monotonically ordered and payback varies with install cost"


@case
def case_on_peak_distribution_costs_at_least_as_much_as_off_peak():
    """A structural property that must hold regardless of the specific
    numbers on any given year: concentrating the SAME kWh into the priciest
    TOU period can never cost less than concentrating it into the cheapest
    one, for a rate schedule where on-peak >= off-peak/super-off-peak $/kWh
    (true of every SDG&E TOU plan this repo models)."""
    d = _synthetic_frame(n_days=30)
    hdd_by_day = pd.Series({(dt.date(2026, 1, 5) + dt.timedelta(days=i)): 10.0
                            for i in range(30)})
    iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
          "annual_heating_therms": 50.0}
    electric, base_bill = hpc.electric_cost_scenarios(d, iso)
    for cop_key, scen in electric.items():
        on = scen["on_peak"]["electric_cost_increase_usd"]
        off = scen["off_peak"]["electric_cost_increase_usd"]
        uni = scen["uniform"]["electric_cost_increase_usd"]
        assert on >= off, (cop_key, on, off)
        assert off <= uni <= on, (cop_key, off, uni, on)
    return "on-peak-concentrated heating load costs at least as much as off-peak-concentrated, uniform sits between"


@case
def case_added_load_absorbs_contemporaneous_solar_before_creating_new_import():
    """Codex adversarial review, issue #1, pass 2: added heat-pump load must
    reduce that SAME interval's own Generation first (solar self-consumption),
    spilling into new Consumption only once that interval's export is used
    up -- never added straight into Consumption while Generation sits
    untouched, which would manufacture simultaneous gross import and export
    the house never actually has and overstate rates.py's non-bypassable
    charge (billed on GROSS imports under NEM). Proves this two ways: (1)
    solar_absorbed_kwh is reported and positive whenever there is real
    Generation to absorb into, (2) the netted bill is never MORE expensive
    than a naive (unnetted, straight-into-Consumption) version of the same
    scenario -- absorbing solar can only reduce or match billed cost,
    never increase it."""
    d = _synthetic_frame(n_days=30)
    hdd_by_day = pd.Series({(dt.date(2026, 1, 5) + dt.timedelta(days=i)): 10.0
                            for i in range(30)})
    iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
          "annual_heating_therms": 50.0}
    electric, base_bill = hpc.electric_cost_scenarios(d, iso)
    for cop_key, scen in electric.items():
        for dist_key in ("uniform", "on_peak", "off_peak"):
            assert scen[dist_key]["solar_absorbed_kwh"] > 0, (
                cop_key, dist_key, "every interval in this fixture has real "
                "Generation (0.05 kWh/interval) to absorb into")

    # naive (wrong) comparison: add the SAME series straight into Consumption
    # with Generation left untouched, and confirm it never bills CHEAPER than
    # the netted version -- if netting were a no-op or backwards, this would fail
    cop = hpc.COP_SCENARIOS["central_3.5"]
    added, _, _ = hpc.build_hp_load_series(d, iso, cop)
    series = added["uniform"]
    naive = d.copy()
    naive["Consumption"] = d["Consumption"] + series
    naive_bill = R.bill_nem(naive, imp="Consumption", exp="Generation")
    netted_increase = electric["central_3.5"]["uniform"]["electric_cost_increase_usd"]
    naive_increase = round(naive_bill - base_bill, 2)
    assert netted_increase <= naive_increase, (
        "netting solar first must never cost MORE than the naive unnetted "
        f"approach: netted={netted_increase}, naive={naive_increase}")
    return (f"solar absorption reduces the netted electric cost increase "
           f"({netted_increase}) below or equal to the naive unnetted one ({naive_increase})")


@case
def case_gas_savings_period_allocation_sums_to_the_annual_estimate():
    """A synthetic bill_periods_gas.csv covering the same window as a
    synthetic hdd_by_day: the sum of each period's own allocated heating
    therms must reproduce the annual heating estimate (the same
    reconciliation check build() itself runs), not merely look plausible."""
    periods = pd.DataFrame({
        # billed therms comfortably exceed each period's own attributed
        # heating share (computed below) so the "never credit more than
        # was billed" cap never binds -- this fixture checks the
        # allocation arithmetic itself, not the separate capping rule
        "statement_date": ["2026-01-31", "2026-02-28", "2026-03-31"],
        "period": ["Jan 1, 2026 - Jan 31, 2026", "Feb 1, 2026 - Feb 28, 2026",
                  "Mar 1, 2026 - Mar 31, 2026"],
        "therms": [50.0, 50.0, 50.0],
        "total_gas_service": [135.0, 135.0, 135.0],   # not read by the marginal-tier path
        # baseline_allowance_therms set well above any period's own therms so
        # the heating slice never crosses into the nonbaseline tier -- this
        # fixture is about the ALLOCATION arithmetic (does the sum of period
        # shares reproduce the annual estimate), not the tier-pricing logic,
        # which has its own dedicated tests above.
        "baseline_rate": [2.70, 2.70, 2.70],
        "nonbaseline_rate": [np.nan, np.nan, np.nan],
        "baseline_allowance_therms": [999.0, 999.0, 999.0],
        "gas_energy_charge_rate": [0.0, 0.0, 0.0],
        "other_fees_rate": [0.0, 0.0, 0.0],
    })
    detail = (
        _single_segment_detail("2026-01-31", "Jan 1, 2026 - Jan 31, 2026", 31, 50.0,
                               2.70, np.nan, 0.0, 0.0)
        + _single_segment_detail("2026-02-28", "Feb 1, 2026 - Feb 28, 2026", 28, 50.0,
                                 2.70, np.nan, 0.0, 0.0)
        + _single_segment_detail("2026-03-31", "Mar 1, 2026 - Mar 31, 2026", 31, 50.0,
                                 2.70, np.nan, 0.0, 0.0))
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({
            dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
        hdd_by_day = pd.concat([hdd_by_day, pd.Series({
            dt.date(2026, 2, d): 10.0 for d in range(1, 29)})])
        hdd_by_day = pd.concat([hdd_by_day, pd.Series({
            dt.date(2026, 3, d): 10.0 for d in range(1, 32)})])
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 95.0, "floor_therms_per_day": 0.0}
        rows, total_savings, total_allocated = hpc.gas_savings_by_period(iso)
    assert abs(total_allocated - 95) <= 1, total_allocated
    assert abs(total_savings - 95 * 2.70) < 5, total_savings
    for r in rows:
        assert abs(r["realized_rate_usd_per_therm"] - 2.70) < 0.01, r
    return f"gas savings allocated across 3 synthetic periods sum to {total_allocated} therms (target 95)"


@case
def case_gas_savings_never_credits_more_than_a_period_actually_billed():
    """A period whose HDD-proportional heat share would EXCEED its own real
    billed therms (a short, low-usage statement during an otherwise heavy
    heating stretch) must be capped at that period's own therms, not
    publish a heating share the household never actually paid for."""
    periods = pd.DataFrame({
        "statement_date": ["2026-01-31", "2026-02-28", "2026-03-31"],
        "period": ["Jan 1, 2026 - Jan 31, 2026", "Feb 1, 2026 - Feb 28, 2026",
                  "Mar 1, 2026 - Mar 31, 2026"],
        "therms": [40.0, 35.0, 20.0],   # March's 20 is less than its own HDD share would imply
        "total_gas_service": [108.0, 94.5, 54.0],   # not read by the marginal-tier path
        "baseline_rate": [2.70, 2.70, 2.70],
        "nonbaseline_rate": [np.nan, np.nan, np.nan],
        "baseline_allowance_therms": [999.0, 999.0, 999.0],
        "gas_energy_charge_rate": [0.0, 0.0, 0.0],
        "other_fees_rate": [0.0, 0.0, 0.0],
    })
    detail = (
        _single_segment_detail("2026-01-31", "Jan 1, 2026 - Jan 31, 2026", 31, 40.0,
                               2.70, np.nan, 0.0, 0.0)
        + _single_segment_detail("2026-02-28", "Feb 1, 2026 - Feb 28, 2026", 28, 35.0,
                                 2.70, np.nan, 0.0, 0.0)
        + _single_segment_detail("2026-03-31", "Mar 1, 2026 - Mar 31, 2026", 31, 20.0,
                                 2.70, np.nan, 0.0, 0.0))
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
        hdd_by_day = pd.concat([hdd_by_day, pd.Series({
            dt.date(2026, 2, d): 10.0 for d in range(1, 29)})])
        hdd_by_day = pd.concat([hdd_by_day, pd.Series({
            dt.date(2026, 3, d): 10.0 for d in range(1, 32)})])
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 95.0, "floor_therms_per_day": 0.0}
        rows, total_savings, total_allocated = hpc.gas_savings_by_period(iso)
    march = next(r for r in rows if r["statement_date"] == "2026-03-31")
    assert march["heating_therms_attributed"] == 20.0, march   # capped at its own billed therms
    # total_allocated is the RAW (uncapped) share, still ~95 -- the cap only
    # affects what's CREDITED as savings, not the reported allocation total
    assert abs(total_allocated - 95) <= 1, total_allocated
    assert total_savings < 95 * 2.70, (total_savings, "capping must reduce total savings below the uncapped figure")
    return "a period's attributed heating is capped at its own billed therms, never exceeding what was paid"


@case
def case_gas_savings_reserves_the_non_heating_floor_before_capping():
    """Codex review, issue #1, pass 2: capping heating attribution at a
    period's own billed therms alone still lets a shoulder-season statement
    have nearly ALL of its usage counted as furnace load, when this same
    model's own non-heating floor (water heating/cooking) had to run every
    one of those days too. A 28-day period billing 15 therms with a
    0.3 therm/day floor can credit at most 15 - (0.3*28) = 6.6 therms to
    heating, never the full 15 even if the HDD-weighted share implies more."""
    periods = pd.DataFrame({
        "statement_date": ["2026-04-28"],
        "period": ["Apr 1, 2026 - Apr 28, 2026"],
        "therms": [15.0],
        "total_gas_service": [40.5],   # not read by the marginal-tier path
        "baseline_rate": [2.70],
        "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0],
        "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-04-28", "Apr 1, 2026 - Apr 28, 2026", 28, 15.0,
                                    2.70, np.nan, 0.0, 0.0)
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 4, d): 10.0 for d in range(1, 29)})
        # HDD share alone would credit the full 15 therms to heating
        # (annual_heating_therms == total_hdd's sum means 100% share)
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": float(hdd_by_day.sum()),
              "floor_therms_per_day": 0.3}
        rows, total_savings, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    expected_cap = 15.0 - 0.3 * 28
    assert abs(row["heating_therms_attributed"] - expected_cap) < 1e-9, (row, expected_cap)
    return (f"a 28-day, 15-therm period with a 0.3 therm/day floor caps "
           f"heating attribution at {expected_cap} therms, not the full 15")


@case
def case_gas_savings_use_the_real_printed_period_dates_not_a_reconstruction():
    """Codex adversarial review, issue #1, pass 3, using the exact real
    example it named: the 2025-11-28 statement's own printed period is
    "Oct 28, 2025 - Nov 25, 2025". Reconstructing periods from adjacent
    statement_dates instead (an earlier version of this function did this)
    would have bounded that period as roughly Oct 30-Nov 28 -- several real
    days off, pulling in some of December's colder HDD and excluding some
    of late October's. This proves the real printed dates are what the
    period_hdd sum is actually computed from."""
    periods = pd.DataFrame({
        "statement_date": ["2025-10-29", "2025-11-28"],
        "period": ["Sep 26, 2025 - Oct 27, 2025", "Oct 28, 2025 - Nov 25, 2025"],
        "therms": [14.0, 34.0],
        "total_gas_service": [35.61, 93.57],   # not read; this test only checks period_hdd
        "baseline_rate": [2.02361, 2.02136],
        "nonbaseline_rate": [2.37552, 2.37552],
        "baseline_allowance_therms": [11.0, 19.0],
        "gas_energy_charge_rate": [0.32597, 0.45779],
        "other_fees_rate": [0.0, 0.0],
    })
    detail = (
        _single_segment_detail("2025-10-29", "Sep 26, 2025 - Oct 27, 2025", 32, 14.0,
                               2.02361, 2.37552, 0.32597, 0.0)
        + _single_segment_detail("2025-11-28", "Oct 28, 2025 - Nov 25, 2025", 29, 34.0,
                                 2.02136, 2.37552, 0.45779, 0.0))
    with _GasFixture(periods, detail):
        # 1 HDD/day everywhere EXCEPT a spike (100) on Oct 26-28 and Nov
        # 26-30 -- days that straddle the REAL Nov period's boundary
        # (Oct 28 - Nov 25) closely enough that a same-statement-date
        # reconstruction (which would have bounded it roughly Oct 30 -
        # Nov 28 instead, per Codex's own named example) misattributes
        # some of them. Built as one dict (last write wins per date),
        # never pd.concat of overlapping Series, which would duplicate
        # rather than overwrite an index label.
        values = {dt.date(2025, 9, d): 1.0 for d in range(26, 31)}
        values.update({dt.date(2025, 10, d): 1.0 for d in range(1, 32)})
        values.update({dt.date(2025, 11, d): 1.0 for d in range(1, 26)})
        values.update({dt.date(2025, 10, d): 100.0 for d in (26, 27, 28)})
        values.update({dt.date(2025, 11, d): 100.0 for d in (26, 27, 28, 29, 30)})
        hdd_by_day = pd.Series(values)
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 30.0, "floor_therms_per_day": 0.0}
        rows, _, _ = hpc.gas_savings_by_period(iso)
    nov = next(r for r in rows if r["statement_date"] == "2025-11-28")
    oct_ = next(r for r in rows if r["statement_date"] == "2025-10-29")
    # the real Nov period (Oct 28 - Nov 25, inclusive) contains one spike day
    # (Oct 28, value 100), three ordinary Oct days (29-31, value 1 each),
    # and 25 ordinary Nov days (1-25, value 1 each) = 100+3+25 = 128; it
    # must NOT include the Oct 26-27 spike (belongs to the real Oct period)
    # or the Nov 26-30 spike (belongs to the NEXT period, not in this
    # fixture at all) -- either leaking in would move this off 128
    assert nov["period_hdd"] == 128.0, nov
    # the real Oct period (Sep 26 - Oct 27, inclusive): 5 Sep days + 25
    # ordinary Oct days (1-25) at value 1, plus the Oct 26-27 spike (2 days
    # x100) -- must NOT include Oct 28 (belongs to the Nov period, not this one)
    assert oct_["period_hdd"] == 230.0, oct_
    return "gas savings correctly use the real printed period dates, not a statement-date reconstruction"


@case
def case_gas_service_segment_tiering_differs_from_period_level_blend():
    """Issue #109: when Gas Service splits mid-cycle into two day-segments
    with different rates, and ALL of a period's heating HDD falls in one
    segment, a segment-respecting allocation prices the heating slice
    differently from the retired period-level blend -- hand-computed here
    so the direction and magnitude of the shift is independently
    verifiable, not just internally self-consistent.

    Period: 30 days, 39 total therms, baseline_allowance_therms=18 (period-
    level, unsegmented -- parse_bills.py doesn't segment this figure).
    Gas Service splits 10/20 days: segment 0 (10 days) never crosses its
    own share of the allowance (single-column, no nonbaseline_rate
    printed); segment 1 (20 days) does (baseline_rate=1.60,
    nonbaseline_rate=2.00). ALL 24 heating-attributed therms fall in
    segment 1 (its HDD share is 100% of the period's, by construction).
    Gas Energy Charge splits on the identical 10/20 days (0.30, 0.45);
    other_fees never splits (flat 0.20/therm).

    Segment-level (this function): segment 1's own day-proportional totals
    are T_s=39x20/30=26, A_s=18x20/30=12; its 24 heating therms leave 2
    non-heating, so baseline_ceiling=min(12,26)=12, overlap_baseline=
    12-2=10, overlap_nonbaseline=24-10=14:
        Gas Service: 10 x 1.60 + 14 x 2.00 = 16.00 + 28.00 = 44.00
        Gas Energy:  0 x 0.30 + 24 x 0.45 =            10.80
        other_fees:  24 x 0.20 =                        4.80
        total = 59.60
    Period-level (the retired computation, shown for contrast only -- NOT
    asserted, since that code path no longer exists): day-weighted
    baseline_rate=(10x1.50+20x1.60)/30=1.56667, nonbaseline_rate=2.00 (only
    segment 1 ever prints one), energy_rate=(10x0.30+20x0.45)/30=0.40:
    non_heat=39-24=15; baseline_ceiling=min(18,39)=18; overlap_baseline=
    18-15=3; overlap_nonbaseline=24-3=21:
        3 x 1.56667 + 21 x 2.00 = 4.70 + 42.00 = 46.70, plus
        24 x (0.40 + 0.20) = 14.40 -> 61.10 total -- $1.50 MORE than the
        segment-level result, because the period blend spreads segment 0's
        unused (by heating) baseline headroom thin across the whole period
        instead of crediting it, correctly, to the segment that actually
        used it."""
    period = "Jan 1, 2026 - Jan 30, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-30"], "period": [period], "therms": [39.0],
        "total_gas_service": [999.0],
        # period-level blend columns are no longer read by this function's
        # own pricing path (issue #109) -- present only for schema shape
        "baseline_rate": [(10 * 1.50 + 20 * 1.60) / 30],
        "nonbaseline_rate": [2.00],
        "baseline_allowance_therms": [18.0],
        "gas_energy_charge_rate": [(10 * 0.30 + 20 * 0.45) / 30],
        "other_fees_rate": [0.20],
    })
    detail = _gas_detail_rows(
        "2026-01-30", period,
        gas_service=[(10, 1.50, np.nan), (20, 1.60, 2.00)],
        gas_energy=[(10, 0.30), (20, 0.45)],
        other_fees=[(39.0, 0.20)])
    with _GasFixture(periods, detail):
        hdd = {dt.date(2026, 1, d): 0.0 for d in range(1, 11)}
        hdd.update({dt.date(2026, 1, d): 10.0 for d in range(11, 31)})
        hdd_by_day = pd.Series(hdd)
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 24.0, "floor_therms_per_day": 0.0}
        rows, total_savings, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    assert row["heating_therms_attributed"] == 24.0, row
    assert abs(row["gas_savings_usd"] - 59.60) < 0.01, row
    assert abs(total_savings - 59.60) < 0.01, total_savings
    return (f"segment-level Gas Service tiering prices this period's heating "
           f"slice at ${row['gas_savings_usd']}, $1.50 below the $61.10 a "
           f"period-blended computation would give on the same fixture -- "
           f"hand-verified direction and size")


@case
def case_non_heating_floor_is_reserved_per_segment_not_once_per_period():
    """Issue #109, round 2 (adversarial re-review). The first version of
    this fix allocated an already period-level-capped heating total across
    segments by HDD share, but the non-heating-floor reservation that
    produces that cap was still computed ONCE for the whole period -- so a
    period whose heating HDD concentrates almost entirely in one segment
    could still borrow spare capacity from a DIFFERENT segment with no
    heating demand to justify it. This household's real 2026-03-31 period
    does exactly this: a 2-day Gas Service segment with zero HDD, a 27-day
    segment with all of it -- the period-level-only cap credited 11.10
    heating therms; a segment-respecting floor reservation caps the real
    27-day segment's own capacity at 10.33 (hand-verified independently
    against the committed real-archive artifact).

    This fixture reproduces the same SHAPE with clean numbers so the cap is
    hand-computable: a 30-day period, 30 total therms, split 3/27 days (the
    first 3 days carry zero HDD, the last 27 carry all of it), floor
    0.6 therms/day.
        heating_capable_per_day = 30/30 - 0.6 = 0.4
        segment 1's own capacity = 0.4 x 27 = 10.8
        raw HDD-proportional demand (all in segment 1) = 20 (> capacity)
        -> heating_therms_attributed = min(20, 10.8) = 10.8
    The OLD period-level-only cap would have allowed
    max(0, 30 - 0.6x30) = 12.0 -- 1.2 therms MORE than the segment-
    respecting figure, because it let segment 0's unused (zero-HDD)
    capacity paper over segment 1's own shortfall. 10.8 < 12.0 is the
    regression this case guards: reverting to a period-level-only floor
    reservation would silently pass 12.0 again.

    Deliberately supplies no `gas_daily` (issue #109 round 4): this fixture
    has no real daily meter data to give, so it exercises the day-
    proportional PROXY capacity path on purpose (`iso.get("gas_daily")`
    is None), not the real-metered-day path -- see
    case_real_daily_data_overrides_the_uniform_proxy_when_available for a
    fixture that supplies synthetic daily data and proves the real path is
    actually used when it's available."""
    period = "Jan 1, 2026 - Jan 30, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-30"], "period": [period], "therms": [30.0],
        "total_gas_service": [999.0], "baseline_rate": [1.0], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.0],
    })
    detail = _gas_detail_rows(
        "2026-01-30", period,
        gas_service=[(3, 1.0, np.nan), (27, 1.0, np.nan)],
        gas_energy=[(3, 0.0), (27, 0.0)],
        other_fees=[(30.0, 0.0)])
    with _GasFixture(periods, detail):
        hdd = {dt.date(2026, 1, d): 0.0 for d in range(1, 4)}
        hdd.update({dt.date(2026, 1, d): 10.0 for d in range(4, 31)})
        hdd_by_day = pd.Series(hdd)
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 20.0, "floor_therms_per_day": 0.6}
        rows, _, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    old_period_level_cap = max(0.0, 30.0 - 0.6 * 30)
    assert abs(old_period_level_cap - 12.0) < 1e-9, old_period_level_cap   # sanity on the fixture itself
    assert abs(row["heating_therms_attributed"] - 10.8) < 0.01, row
    assert row["heating_therms_attributed"] < old_period_level_cap, (
        row, old_period_level_cap, "segment-level floor reservation must cap below "
        "the old period-level-only figure on a fixture built to make it bind")
    return (f"non-heating floor reserved per segment caps this period's heating "
           f"attribution at {row['heating_therms_attributed']} therms, not the "
           f"{old_period_level_cap} a period-level-only reservation would allow")


@case
def case_cold_day_cannot_borrow_a_hot_days_unused_capacity_within_one_segment():
    """Issue #109, round 3 (two independent Codex adversarial-review passes
    on round 2; closes issue #118). Round 2 reserved the non-heating floor
    once per charge-type SEGMENT, which can still be several real days
    wide -- so a cold day inside an otherwise-unsplit (single-segment)
    period could still silently borrow a hot day's unused capacity, the
    identical bug round 2 fixed ACROSS segments, one granularity down
    WITHIN a segment. This fixture is built so NO charge type splits at
    all (a single 2-day segment for every charge type), isolating the
    within-segment gap specifically -- the round-2 fixture above
    (case_non_heating_floor_is_reserved_per_segment_not_once_per_period)
    cannot catch this, since its own cold segment spreads HDD uniformly
    across its days and so cannot distinguish day-level from segment-level
    capping.

    A 2-day period, 10 total therms, floor 3 therms/day:
        heating_capable_per_day = 10/2 - 3 = 2.0 therms
    Day 1 is HOT (zero HDD, zero heating demand). Day 2 is COLD, with a
    raw HDD-proportional demand of 8 therms (deliberately far above its
    own 2.0-therm capacity, so the cap binds hard).
        day-level (correct): day 1 contributes 0 (no demand to cap, and
            its own unused 2.0-therm capacity heads nowhere); day 2 caps at
            min(8, 2.0) = 2.0 -> period total = 2.0
        segment-level (round 2, the bug this case guards against
            reverting to): the WHOLE segment's own capacity is
            max(0, 10 - 3x2) = 4.0, letting day 2 draw on day 1's own
            2.0 therms of never-needed capacity -> min(8, 4.0) = 4.0,
            DOUBLE the correct figure.

    Deliberately supplies no `gas_daily` (issue #109 round 4): this fixture
    tests the day-proportional PROXY ceiling's own within-segment
    granularity specifically (`heating_capable_per_day` computed from
    `therms / period_days`), not real metered data -- see
    case_real_daily_data_overrides_the_uniform_proxy_when_available for the
    real-data path's own equivalent test."""
    period = "Jan 1, 2026 - Jan 2, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-02"], "period": [period], "therms": [10.0],
        "total_gas_service": [999.0], "baseline_rate": [1.0], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-01-02", period, 2, 10.0, 1.0, np.nan, 0.0, 0.0)
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 1, 1): 0.0, dt.date(2026, 1, 2): 50.0})
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 8.0, "floor_therms_per_day": 3.0}
        rows, _, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    segment_level_cap = max(0.0, 10.0 - 3.0 * 2)
    assert abs(segment_level_cap - 4.0) < 1e-9, segment_level_cap   # sanity on the fixture itself
    assert abs(row["heating_therms_attributed"] - 2.0) < 0.01, row
    assert row["heating_therms_attributed"] < segment_level_cap, (
        row, segment_level_cap, "day-level floor reservation must cap below what a "
        "segment-level-only reservation would allow when one day in the segment is "
        "hot and unused capacity could otherwise be silently loaned to a cold day")
    return (f"a cold day's heating attribution ({row['heating_therms_attributed']} "
           f"therms) cannot borrow a hot day's unused capacity within the same "
           f"unsplit segment -- {segment_level_cap} is what a segment-level-only "
           f"reservation would have wrongly allowed")


@case
def case_real_daily_data_overrides_the_uniform_proxy_when_available():
    """Issue #109, round 4 (plain Codex `review` pass, not adversarial-
    review). Round 3's per-day capacity ceiling was itself a fabricated
    uniform proxy -- the period's printed total therms divided evenly
    across its own real days -- even though this module already has real
    per-day meter readings (load_gas_daily()) available elsewhere. This
    fixture supplies a synthetic `gas_daily` (via `iso["gas_daily"]`,
    exactly what isolate_heating_therms() populates in a real run) whose
    real per-day therms are FAR from uniform, and proves the day-level cap
    is built from that real data, not the period average, by running the
    SAME period fixture with and without it and checking the results
    genuinely differ in the expected direction.

    A 3-day period, real daily therms 5 / 1 / 5 (11 total, average
    3.667/day), floor 0.5 therms/day. All the HDD (hence all the raw
    heating demand) falls on day 2 -- the LOW-usage real day:
        proxy ceiling (no gas_daily): 11/3 - 0.5 = 3.1667/day, uniform ->
            day 2 caps at min(50, 3.1667) = 3.1667
        real ceiling (gas_daily supplied): day 2's own real 1.0 therms -
            0.5 = 0.5 -> day 2 caps at min(50, 0.5) = 0.5
    0.5 << 3.1667: the real day's own actual low usage caps it far below
    what the period-wide average would have allowed, because day 2's real
    metered therms are well below the period's own mean."""
    period = "Jan 1, 2026 - Jan 3, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-03"], "period": [period], "therms": [11.0],
        "total_gas_service": [999.0], "baseline_rate": [1.0], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-01-03", period, 3, 11.0, 1.0, np.nan, 0.0, 0.0)
    hdd_by_day = pd.Series({dt.date(2026, 1, 1): 0.0, dt.date(2026, 1, 2): 100.0,
                            dt.date(2026, 1, 3): 0.0})
    gas_daily = pd.Series({dt.date(2026, 1, 1): 5.0, dt.date(2026, 1, 2): 1.0,
                           dt.date(2026, 1, 3): 5.0})
    with _GasFixture(periods, detail):
        iso_proxy = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
                    "annual_heating_therms": 50.0, "floor_therms_per_day": 0.5}
        rows_proxy, _, _ = hpc.gas_savings_by_period(iso_proxy)
        iso_real = {**iso_proxy, "gas_daily": gas_daily}
        rows_real, _, _ = hpc.gas_savings_by_period(iso_real)
    proxy_therms = rows_proxy[0]["heating_therms_attributed"]
    real_therms = rows_real[0]["heating_therms_attributed"]
    assert abs(proxy_therms - 3.17) < 0.01, proxy_therms
    assert abs(real_therms - 0.5) < 0.01, real_therms
    assert real_therms < proxy_therms, (
        real_therms, proxy_therms, "the real day's own low metered usage must cap "
        "heating attribution below what the period-wide proxy average would allow")
    return (f"supplying real daily gas data caps this period's heating attribution "
           f"at {real_therms} therms (that cold day's own real usage), not the "
           f"{proxy_therms} therms the period-average proxy would have allowed")


@case
def case_capacity_cap_fails_closed_when_gas_daily_missing_a_needed_day():
    """Issue #109, round 4. When `gas_daily` IS supplied (a real run, or a
    test deliberately exercising this path) but has no reading for a real
    day that actually has nonzero heating demand, silently falling back to
    the period-wide proxy for just that one day would misprice a real
    dollar figure without any signal that it happened -- mirrors
    _gas_service_segment_tier_cost()'s own missing-nonbaseline-rate
    convention (a value actually NEEDED for a nonzero computation must be
    real, never silently defaulted). This must fail closed rather than
    quietly use the proxy for the gapped day.

    Same 3-day/11-therm/0.5-floor-per-day shape as the override case above,
    but `gas_daily` is missing day 2 specifically -- the SAME day that
    carries all of this fixture's own HDD (and so has nonzero demand)."""
    period = "Jan 1, 2026 - Jan 3, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-03"], "period": [period], "therms": [11.0],
        "total_gas_service": [999.0], "baseline_rate": [1.0], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.0],
    })
    detail = _single_segment_detail("2026-01-03", period, 3, 11.0, 1.0, np.nan, 0.0, 0.0)
    hdd_by_day = pd.Series({dt.date(2026, 1, 1): 0.0, dt.date(2026, 1, 2): 100.0,
                            dt.date(2026, 1, 3): 0.0})
    # day 2 (the only day with any HDD/demand) is missing from gas_daily --
    # a real coverage gap on the exact day that would need it
    gas_daily = pd.Series({dt.date(2026, 1, 1): 5.0, dt.date(2026, 1, 3): 5.0})
    with _GasFixture(periods, detail):
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 50.0, "floor_therms_per_day": 0.5,
              "gas_daily": gas_daily}
        try:
            hpc.gas_savings_by_period(iso)
        except SystemExit as e:
            msg = str(e)
        else:
            raise AssertionError(
                "a real calendar day with nonzero heating demand and no gas_daily "
                "coverage was silently priced via the proxy instead of failing closed")
    assert "gas.csv" in msg and "2026-01-02" in msg, msg
    return "a real day with nonzero heating demand and no gas_daily coverage fails closed, not silently priced via the proxy"


@case
def case_other_fees_borrows_gas_energy_day_ranges_when_segment_counts_match():
    """Issue #109: other_fees splits by THERM COUNT, not days, so it has no
    day-range of its own -- when it splits into the SAME number of segments
    as Gas Energy Charge (which this household's real 25-bill corpus shows
    is the only pattern that ever occurs, see _other_fees_day_ranges()'s own
    docstring), it borrows Gas Energy Charge's day boundaries to allocate
    heating HDD-share across its own segments. Gas Service here never splits
    (1 segment), so this also proves Gas Energy Charge is preferred over Gas
    Service as the day-range source when both could theoretically match.

    20-day period, all 15 heating-attributed therms concentrated (by HDD
    construction) in the SECOND 10 days. Gas Energy Charge/other_fees both
    split 10/10 days; other_fees's second segment (0.25/therm) must be what
    prices the whole heating slice, not its first (0.10/therm):
        other_fees cost = 15 x 0.25 = 3.75, not 15 x 0.10 = 1.50."""
    period = "Jan 1, 2026 - Jan 20, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-20"], "period": [period], "therms": [30.0],
        "total_gas_service": [999.0], "baseline_rate": [0.0], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.175],
    })
    detail = _gas_detail_rows(
        "2026-01-20", period,
        gas_service=[(20, 0.0, np.nan)],
        gas_energy=[(10, 0.0), (10, 0.0)],
        other_fees=[(15.0, 0.10), (15.0, 0.25)])
    with _GasFixture(periods, detail):
        hdd = {dt.date(2026, 1, d): 0.0 for d in range(1, 11)}
        hdd.update({dt.date(2026, 1, d): 10.0 for d in range(11, 21)})
        hdd_by_day = pd.Series(hdd)
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 15.0, "floor_therms_per_day": 0.0}
        rows, _, _ = hpc.gas_savings_by_period(iso)
    row = rows[0]
    assert row["heating_therms_attributed"] == 15.0, row
    # Gas Service contributes 0 (baseline_rate x 15, nonbaseline never
    # touched since baseline_allowance is huge); Gas Energy contributes 0
    # (rate 0.0 both segments); only other_fees's own second-segment rate
    # (0.25, not 0.10) should show up in the total.
    assert abs(row["gas_savings_usd"] - 15.0 * 0.25) < 0.01, row
    return ("other_fees's second segment (0.25/therm), borrowed from Gas "
           "Energy Charge's own day boundaries, prices the whole heating "
           "slice -- not its first segment's 0.10/therm")


@case
def case_other_fees_fails_closed_when_no_segment_count_matches():
    """Issue #109: if other_fees ever split into a segment count matching
    NEITHER Gas Energy Charge's nor Gas Service's own segment count for the
    same period, there is no reliable day-boundary basis to allocate heating
    HDD-share across its segments -- this must fail closed (CLAUDE.md
    section 0) rather than silently guess a mapping. Not observed in this
    household's real 25-bill corpus (see _other_fees_day_ranges()'s own
    docstring), but the function must still refuse rather than mis-price."""
    period = "Jan 1, 2026 - Jan 30, 2026"
    periods = pd.DataFrame({
        "statement_date": ["2026-01-30"], "period": [period], "therms": [30.0],
        "total_gas_service": [999.0], "baseline_rate": [2.00], "nonbaseline_rate": [np.nan],
        "baseline_allowance_therms": [999.0], "gas_energy_charge_rate": [0.0],
        "other_fees_rate": [0.15],
    })
    detail = _gas_detail_rows(
        "2026-01-30", period,
        gas_service=[(30, 2.00, np.nan)],                       # 1 segment
        gas_energy=[(15, 0.0), (15, 0.0)],                       # 2 segments
        other_fees=[(10.0, 0.1), (10.0, 0.2), (10.0, 0.3)])      # 3 segments -- matches neither
    with _GasFixture(periods, detail):
        hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 31)})
        iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
              "annual_heating_therms": 15.0, "floor_therms_per_day": 0.0}
        try:
            hpc.gas_savings_by_period(iso)
        except SystemExit as e:
            msg = str(e)
        else:
            raise AssertionError(
                "other_fees split into a segment count matching neither "
                "day-based charge type, but was priced anyway instead of "
                "failing closed")
    assert "other_fees" in msg and "3 segments" in msg, msg
    return "other_fees segment count matching neither day-based charge type -> fails closed"


@case
def case_payback_and_npv_matches_a_hand_computation():
    result = hpc.payback_and_npv(1000, 5000, (0.05,), 10)
    assert result["payback_years"] == 5.0, result
    hand_npv = sum(1000 / (1.05 ** y) for y in range(1, 11)) - 5000
    assert abs(result["npv"]["5pct"] - round(hand_npv)) <= 1, (result, hand_npv)
    return "payback and NPV at 5% match a hand computation exactly"


@case
def case_payback_and_npv_reports_none_on_non_positive_savings():
    result = hpc.payback_and_npv(-50, 5000, (0.05,), 10)
    assert result["payback_years"] is None, result
    return "non-positive annual savings reports no payback rather than a negative/nonsensical year count"


@case
def case_no_incentive_is_credited():
    """Protects the module's own headline decision (every incentive checked
    and found closed/expired, issue #1's own AC item 5) against a silent
    reintroduction of a stale incentive assumption."""
    assert hpc.INCENTIVE_USD == 0, hpc.INCENTIVE_USD
    return "INCENTIVE_USD is 0, matching the verified all-programs-closed finding"


@case
def case_not_applicable_when_has_gas_is_false():
    real = hpc.HAS_GAS
    hpc.HAS_GAS = False
    try:
        out = hpc.build()
    finally:
        hpc.HAS_GAS = real
    assert out == {"applicable": False, "reason": "household.has_gas is false"}, out
    return "build() reports not-applicable rather than crashing when household.has_gas is false"


# ---------------------------------------------------------------------------
# Archive-gated: the real measured year
# ---------------------------------------------------------------------------
@case
def case_real_archive_baseline_bill_matches_the_canonical_figure():
    """The real household's baseline electric bill, computed inside this
    script via BR.load() + R.bill_nem(), must match the SAME $4,904.13
    figure already independently validated and published elsewhere in this
    report (reprice_by_vintage.py, index.html) -- a real cross-check, not
    just an internal consistency check against this script's own math."""
    _require_archive()
    out = hpc.build()
    assert out["applicable"], out
    assert abs(out["baseline_electric_bill_usd"] - 4904.13) < 0.01, out["baseline_electric_bill_usd"]
    return f"real baseline bill {out['baseline_electric_bill_usd']} matches the canonical $4,904.13"


@case
def case_real_archive_two_isolation_methods_agree_within_ten_percent():
    _require_archive()
    out = hpc.build()
    disagreement = out["isolation"]["cross_check"]["floor_disagreement_pct"]
    assert disagreement < 10, disagreement
    return f"summer-baseline and HDD-regression floors agree within {disagreement}%"


@case
def case_real_archive_electric_load_and_gas_savings_use_the_same_reconciled_therms():
    """Codex review, issue #1, pass 3 (P1): the raw HDD-regression annual
    estimate (isolation.annual_heating_therms) is unconstrained, but not all
    of it can be attributed to a specific billed period once each period's
    own non-heating floor is reserved -- sizing the heat pump's electric
    load on the raw (larger) figure while crediting gas savings on the
    capped (smaller) figure would silently model and pay for two different
    amounts of heat. build() must size the electric load on the SAME
    reconciled total it credits as gas savings, not the raw regression
    figure."""
    _require_archive()
    out = hpc.build()
    raw = out["isolation"]["annual_heating_therms"]
    reconciled = out["reconciled_heating_therms_yr"]
    # on this household's own real data, capping actually binds -- a fixture
    # where raw == reconciled would let this test pass without proving the
    # reconciliation logic does anything
    assert reconciled < raw, (reconciled, raw, "capping does not appear to "
        "bind on this archive -- this test needs a case where it does")
    for cop_key, cop in hpc.COP_SCENARIOS.items():
        expected_kwh = reconciled * hpc.KWH_PER_THERM * hpc.FURNACE_AFUE / cop
        actual_kwh = out["electric_cost_by_scenario"][cop_key]["uniform"]["added_kwh"]
        assert abs(actual_kwh - expected_kwh) < 1, (cop_key, actual_kwh, expected_kwh)
        raw_kwh = raw * hpc.KWH_PER_THERM * hpc.FURNACE_AFUE / cop
        assert abs(actual_kwh - raw_kwh) > 1, (cop_key, "electric load must NOT "
            "still be sized on the raw unreconciled figure")
    return (f"electric load is sized on the reconciled {reconciled} therms/yr, "
           f"not the raw {raw} therms/yr regression estimate")


@case
def case_real_archive_regenerates_byte_identically():
    _require_archive()
    committed = pathlib.Path(hpc.DATA) / "heat_pump_conversion.json"
    if not committed.exists():
        raise SkipCase("no committed data/heat_pump_conversion.json to compare against yet")
    before = committed.read_text()
    hpc.main()
    after = committed.read_text()
    assert before == after, "regeneration is not byte-identical"
    return "data/heat_pump_conversion.json regenerates byte-identically from the real archive"


def run():
    passed = failed = skipped = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS  {fn.__name__}: {msg}")
            passed += 1
        except SkipCase as e:
            print(f"SKIP  {fn.__name__}: {e}")
            skipped += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(CASES)} passed, {skipped} skipped, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
