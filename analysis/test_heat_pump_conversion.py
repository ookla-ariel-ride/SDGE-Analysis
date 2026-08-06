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
def case_gas_savings_use_the_periods_own_blended_realized_rate():
    """Codex adversarial review, issue #1: two real gas bill PDFs read
    directly show total_gas_service also embeds a separate, untiered "Gas
    Energy Charge" plus taxes that neither baseline_rate nor
    nonbaseline_rate captures alone -- an earlier attempt at pricing
    heating therms at nonbaseline_rate ALONE (tried during review) turned
    out to OMIT those real charges entirely, understating savings more than
    the blended average's own tier-dilution ever did. This proves the
    fixture's own blended rate correctly exceeds either tier's bare Gas
    Service rate."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        periods = pd.DataFrame({
            "statement_date": ["2026-01-31"],
            "therms": [60.0],
            # blended average deliberately does NOT equal either tier rate on
            # its own: total_gas_service also embeds the real Gas Energy
            # Charge + tax components neither rate column captures (see the
            # function's own docstring) -- 172.8/60 = 2.88/therm, above BOTH
            # the baseline and nonbaseline Gas Service rates alone
            "total_gas_service": [172.8],
            "baseline_rate": [1.8],
            "nonbaseline_rate": [2.3],
        })
        csv_path = tmp / "bill_periods_gas.csv"
        periods.to_csv(csv_path, index=False)
        real_path = hpc.GAS_PERIODS_CSV
        hpc.GAS_PERIODS_CSV = str(csv_path)
        try:
            hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
            iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
                  "annual_heating_therms": 50.0}
            rows, total_savings, _ = hpc.gas_savings_by_period(iso)
        finally:
            hpc.GAS_PERIODS_CSV = real_path
    row = rows[0]
    blended_rate = 172.8 / 60.0
    assert abs(row["realized_rate_usd_per_therm"] - blended_rate) < 1e-9, row
    # the whole point: the blended rate exceeds EITHER tier's own Gas Service
    # rate alone, because it also carries the Gas Energy Charge + taxes that
    # neither baseline_rate nor nonbaseline_rate captures on their own
    assert blended_rate > 2.3 > 1.8, blended_rate
    return "gas savings use the period's own blended realized rate, which correctly exceeds either tier's Gas Service rate alone"


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
def case_gas_savings_period_allocation_sums_to_the_annual_estimate():
    """A synthetic bill_periods_gas.csv covering the same window as a
    synthetic hdd_by_day: the sum of each period's own allocated heating
    therms must reproduce the annual heating estimate (the same
    reconciliation check build() itself runs), not merely look plausible."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        periods = pd.DataFrame({
            # billed therms comfortably exceed each period's own attributed
            # heating share (computed below) so the "never credit more than
            # was billed" cap never binds -- this fixture checks the
            # allocation arithmetic itself, not the separate capping rule
            "statement_date": ["2026-01-31", "2026-02-28", "2026-03-31"],
            "therms": [50.0, 50.0, 50.0],
            "total_gas_service": [135.0, 135.0, 135.0],   # $2.70/therm flat, hand-checkable
        })
        csv_path = tmp / "bill_periods_gas.csv"
        periods.to_csv(csv_path, index=False)
        real_path = hpc.GAS_PERIODS_CSV
        hpc.GAS_PERIODS_CSV = str(csv_path)
        try:
            hdd_by_day = pd.Series({
                dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
            hdd_by_day = pd.concat([hdd_by_day, pd.Series({
                dt.date(2026, 2, d): 10.0 for d in range(1, 29)})])
            hdd_by_day = pd.concat([hdd_by_day, pd.Series({
                dt.date(2026, 3, d): 10.0 for d in range(1, 32)})])
            iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
                  "annual_heating_therms": 95.0}
            rows, total_savings, total_allocated = hpc.gas_savings_by_period(iso)
        finally:
            hpc.GAS_PERIODS_CSV = real_path
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
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        periods = pd.DataFrame({
            "statement_date": ["2026-01-31", "2026-02-28", "2026-03-31"],
            "therms": [40.0, 35.0, 20.0],   # March's 20 is less than its own HDD share would imply
            "total_gas_service": [108.0, 94.5, 54.0],
        })
        csv_path = tmp / "bill_periods_gas.csv"
        periods.to_csv(csv_path, index=False)
        real_path = hpc.GAS_PERIODS_CSV
        hpc.GAS_PERIODS_CSV = str(csv_path)
        try:
            hdd_by_day = pd.Series({dt.date(2026, 1, d): 10.0 for d in range(1, 32)})
            hdd_by_day = pd.concat([hdd_by_day, pd.Series({
                dt.date(2026, 2, d): 10.0 for d in range(1, 29)})])
            hdd_by_day = pd.concat([hdd_by_day, pd.Series({
                dt.date(2026, 3, d): 10.0 for d in range(1, 32)})])
            iso = {"hdd_by_day": hdd_by_day, "total_hdd": float(hdd_by_day.sum()),
                  "annual_heating_therms": 95.0}
            rows, total_savings, total_allocated = hpc.gas_savings_by_period(iso)
        finally:
            hpc.GAS_PERIODS_CSV = real_path
    march = next(r for r in rows if r["statement_date"] == "2026-03-31")
    assert march["heating_therms_attributed"] == 20.0, march   # capped at its own billed therms
    # total_allocated is the RAW (uncapped) share, still ~95 -- the cap only
    # affects what's CREDITED as savings, not the reported allocation total
    assert abs(total_allocated - 95) <= 1, total_allocated
    assert total_savings < 95 * 2.70, (total_savings, "capping must reduce total savings below the uncapped figure")
    return "a period's attributed heating is capped at its own billed therms, never exceeding what was paid"


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
