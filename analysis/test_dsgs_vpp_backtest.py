#!/usr/bin/env python3
"""Behavioural tests for dsgs_vpp_backtest.py (issue #10, Phase 1).

dsgs_vpp_backtest.py imports behavior_rebuild.py (directly, and transitively via
battery_dispatch_policies.py), which reads private/household.yaml at ITS OWN module
top level and fails closed (SystemExit) if that file is absent -- the same situation
test_nem3_grandfathering.py and test_carbon_dispatch_tradeoff.py already solved. Applied
here too: point household.PATH at a synthetic, invented household BEFORE importing, so
this whole file imports cleanly on any checkout, private/ or not. Cases that need the
REAL measured Green Button year, the REAL committed event calendar, or the REAL 30 MB
raw CEC xlsx still gate on their own precondition and SKIP rather than fail when this
checkout lacks them, matching test_nem3_grandfathering.py's SkipCase convention.

Run from the repo root:  ./.venv/bin/python analysis/test_dsgs_vpp_backtest.py
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

# Same fix as test_nem3_grandfathering.py, for the same reason: point the intake
# loader at a synthetic, invented household before the transitive import of
# behavior_rebuild fires. Values are invented; nothing here depends on them except
# the cases that explicitly load the real archive below.
import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import rates as R                       # noqa: E402
import behavior_rebuild as br           # noqa: E402
import battery_dispatch_policies as bp  # noqa: E402
import dsgs_vpp_backtest as vb          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no private
    Green Button archive, no committed event calendar, or no raw CEC xlsx). Counted
    as neither pass nor fail."""


def _require_archive():
    """Only for cases that need the REAL measured year (br.load() on the actual
    data, or a byte-identical regeneration of the committed JSON built from it).
    The module import above already succeeded unconditionally using the synthetic
    household, so this gates DATA, not importability."""
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{HOUSEHOLD_YAML}, neither of which this checkout has")
    br.CSV = files[0]
    return files[0]


def _require_calendar():
    if not vb.CALENDAR_CSV.exists():
        raise SkipCase(f"needs the committed {vb.CALENDAR_CSV}, which this "
                       "checkout does not have (run --build-calendar first)")


def _require_raw_xlsx():
    if not vb.RAW_XLSX.exists():
        raise SkipCase(f"needs the private raw archive {vb.RAW_XLSX}, which this "
                       "checkout does not have")


EPS = 1e-6


# ---------------------------------------------------------------------------
# (a) synthetic-frame unit tests of run_batt_vpp -- no archive needed at all
# ---------------------------------------------------------------------------
def _synthetic_day(consumption_kw=0.0, generation_kw=0.0, weekday=True):
    """96 15-min intervals, one calendar day, constant load/generation. weekday=True
    picks a real Wednesday (2026-01-07); weekday=False a real Saturday (2026-01-10) --
    both far from any DST transition and outside any tariff holiday, so rates.period_at
    needs no household config and behaves exactly per rates.period()'s plain rule."""
    start = pd.Timestamp("2026-01-07") if weekday else pd.Timestamp("2026-01-10")
    dtr = pd.date_range(start, periods=96, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = [R.period_at(ts) for ts in d.dt]
    imp0 = np.full(96, consumption_kw * 0.25)
    gen0 = np.full(96, generation_kw * 0.25)
    return d, imp0, gen0


@case
def case_run_batt_vpp_matches_run_batt_with_empty_event_set():
    """With no event hours at all, run_batt_vpp must behave IDENTICALLY to
    battery_dispatch_policies.run_batt's 'greedy' policy -- byte-for-byte, not just
    close. This is the "close variant" claim the module docstring makes; this case is
    what makes it true rather than asserted."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=1.0)
    imp_a, exp_a, _served, _thru = bp.run_batt(d, imp0, gen0, vb.CAP, "greedy")
    imp_b, exp_b, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0, gen0, vb.CAP, set(), 0.20)
    assert np.array_equal(imp_a, imp_b), "imp diverges with an empty event set"
    assert np.array_equal(exp_a, exp_b), "exp diverges with an empty event set"
    assert event_kwh.sum() == 0.0, "an empty event set must force zero extra discharge"
    return "run_batt_vpp(event_set=set()) is byte-identical to run_batt('greedy')"


@case
def case_run_batt_vpp_matches_run_batt_across_a_realistic_mixed_day():
    """Same equivalence, but with a load/generation shape that exercises every branch
    of the greedy control flow (solar surplus charging, sop grid top-up, on-peak
    discharge) -- not just the always-discharge shape of the constant-load fixture."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    rng = np.random.default_rng(0)
    # house load: 0.5 kW baseline + a 3 kW evening bump (16-21h)
    imp0 = np.where((d.hour.values >= 16) & (d.hour.values < 21), 3.0, 0.5) * 0.25
    # solar: a midday bump (10-15h)
    gen0 = np.where((d.hour.values >= 10) & (d.hour.values < 15), 4.0, 0.0) * 0.25
    imp_a, exp_a, _served, _thru = bp.run_batt(d, imp0.copy(), gen0.copy(), vb.CAP, "greedy")
    imp_b, exp_b, _soc, event_kwh, _bau = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, set(), 0.20)
    assert np.array_equal(imp_a, imp_b)
    assert np.array_equal(exp_a, exp_b)
    assert event_kwh.sum() == 0.0
    return "run_batt_vpp matches run_batt across solar+evening-load branches too"


@case
def case_event_hour_delivers_the_efficiency_adjusted_headroom_from_an_idle_full_battery():
    """Zero load/generation all day: BAU charges to full during the first sop window
    (0-6h) and then sits idle (no import to discharge against). An event declared at
    HE21 (20:00-21:00, already inside the ordinary on-peak discharge window but with
    nothing for BAU to serve) forces discharge out of an otherwise-untouched battery,
    capped by min(4*PWRQ, (cap - reserve_kwh) * ETA) -- NOT the raw 11.5 kWh nameplate,
    because delivering output costs MORE soc than the output itself (soc -= extra/ETA):
    a 20% reserve on a full battery does not free up a full nameplate-hour of output,
    only its round-trip-efficiency-adjusted equivalent. This is the same soc*ETA output
    cap run_batt's own disch_win branch uses (dd = min(imp[i], soc*ETA, PWRQ))."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0, generation_kw=0.0)
    date = d.dt.dt.date.iloc[0]
    event_set = {(date, 20)}   # HE21 -> floor_hour 20
    imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, event_set, 0.20)
    mask = (d.dt.dt.date.values == date) & (np.floor(d.hour.values).astype(int) == 20)
    total = float((event_kwh[mask] + bau_kwh[mask]).sum())
    reserve_kwh = 0.20 * vb.CAP
    expected = min(4 * vb.PWRQ, (vb.CAP - reserve_kwh) * vb.ETA)
    assert abs(total - expected) < 1e-4, f"expected {expected} kWh, got {total}"
    assert bau_kwh[mask].sum() == 0.0, "BAU should have discharged nothing on its own here"
    return f"idle full battery delivers {total:.4f} kWh (efficiency-adjusted headroom cap)"


@case
def case_high_reserve_caps_event_discharge_partway_through_the_hour():
    """A 90% reserve on a full (13.5 kWh) battery leaves only (cap - reserve) * ETA of
    deliverable output. The event hour must deliver exactly that much (well under one
    15-min interval's 2.875 kWh power limit) and NOTHING more once the reserve floor is
    reached -- the SOC-constrained miss mechanism the issue asks for, and soc must land
    EXACTLY at the reserve floor, never below it."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0, generation_kw=0.0)
    date = d.dt.dt.date.iloc[0]
    event_set = {(date, 20)}
    imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, event_set, 0.90)
    mask = (d.dt.dt.date.values == date) & (np.floor(d.hour.values).astype(int) == 20)
    total = float((event_kwh[mask] + bau_kwh[mask]).sum())
    reserve_kwh = 0.90 * vb.CAP
    expected = (vb.CAP - reserve_kwh) * vb.ETA
    assert abs(total - expected) < 1e-6, f"expected {expected} kWh, got {total}"
    idxs = np.where(mask)[0]
    soc_after_event_hour = soc_start[idxs[-1] + 1]
    assert soc_after_event_hour >= reserve_kwh - 1e-6, (
        f"soc {soc_after_event_hour} dipped below the reserve floor {reserve_kwh}")
    return f"a 90% reserve caps the event hour at {expected:.4f} kWh, not the full nameplate"


@case
def case_full_reserve_produces_a_complete_miss():
    """reserve_frac=1.0 on a full battery leaves zero headroom: the event hour must
    deliver exactly 0 kWh -- the complete-miss case."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0, generation_kw=0.0)
    date = d.dt.dt.date.iloc[0]
    event_set = {(date, 20)}
    imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, event_set, 1.0)
    mask = (d.dt.dt.date.values == date) & (np.floor(d.hour.values).astype(int) == 20)
    total = float((event_kwh[mask] + bau_kwh[mask]).sum())
    assert total < 1e-9, f"expected a complete miss (0 kWh), got {total}"
    return "a 100% reserve on a full battery produces a complete miss (0 kWh delivered)"


@case
def case_soc_never_leaves_the_physical_bounds():
    """Across a full day with events declared on every hour (the adversarial case:
    constant forced dispatch attempts), SOC must never go negative or exceed cap,
    regardless of the reserve setting."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=1.0, generation_kw=0.5)
    date = d.dt.dt.date.iloc[0]
    event_set = {(date, hh) for hh in range(24)}
    for reserve in (0.0, 0.2, 0.5, 0.9, 1.0):
        imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
            d, imp0.copy(), gen0.copy(), vb.CAP, event_set, reserve)
        assert (soc_start >= -1e-9).all(), f"negative SOC at reserve={reserve}"
        assert (soc_start <= vb.CAP + 1e-9).all(), f"SOC exceeded cap at reserve={reserve}"
    return "SOC stays within [0, cap] across all-day event forcing at 5 reserve levels"


@case
def case_reserve_floor_holds_against_combined_ordinary_and_event_discharge():
    """Regression for the "reserve honored only in isolation" defect: the fixtures
    above all use consumption_kw=0.0, so imp[i] is always 0 and the ordinary
    (BAU-equivalent) disch_win branch never actually discharges anything during an
    event hour -- the bug (the ordinary branch drawing against the FULL soc, with no
    reserve floor, even inside a declared event hour) was invisible to them.

    This case uses REAL non-zero house load (3 kW, like the existing evening-load
    fixture) with a single declared event hour (HE20, floor_hour 19) that falls AFTER
    three PRIOR, non-event evening hours (16, 17, 18) of ordinary discharge have
    already drawn SOC down from full to just above the 20% reserve floor (verified
    below: SOC enters the event hour at ~4.01 kWh, above the 2.70 kWh floor). Without
    the fix, the event hour's own ordinary discharge keeps draining unconstrained and
    SOC ends the hour at ~1.12 kWh -- well below the floor (confirmed against a
    pre-fix copy of this function during development: it drains to ~0.33 kWh by the
    start of the following hour). With the fix, SOC is clipped at exactly the 2.70 kWh
    floor for the rest of that hour and the interval immediately after it.

    Deliberately NOT asserted for hours beyond the one immediately following the
    event hour: per the issue's own framing ("a backup reserve reduces dispatchable
    capacity during events"), the reserve protects capacity FOR declared event hours,
    not a standing floor on ordinary day-to-day arbitrage outside them -- a
    later, non-event hour is free to draw SOC below the floor, and does (by design,
    not a bug)."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=3.0, generation_kw=0.0)
    date = d.dt.dt.date.iloc[0]
    reserve_frac = 0.20
    event_set = {(date, 19)}   # HE20 (19:00-19:59), the 3rd of 5 evening discharge
                               # hours (16-21) -- late enough that prior non-event
                               # hours have already drawn SOC down near the floor,
                               # early enough that SOC enters the hour above it.
    imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, event_set, reserve_frac)
    reserve_kwh = reserve_frac * vb.CAP
    h = np.floor(d.hour.values).astype(int)
    idxs = np.where(h == 19)[0]

    soc_entering = soc_start[idxs[0]]
    assert soc_entering > reserve_kwh, (
        f"fixture precondition failed: SOC must enter the event hour ({soc_entering}) "
        f"ABOVE the reserve floor ({reserve_kwh}) for this to be a meaningful "
        "regression case")
    assert bau_kwh[idxs].sum() > 0, (
        "fixture must actually exercise the ordinary/BAU discharge branch during the "
        "event hour, or this case can't catch the bug it targets")

    during_and_just_after = np.append(soc_start[idxs], soc_start[idxs[-1] + 1])
    assert (during_and_just_after >= reserve_kwh - EPS).all(), (
        f"SOC dropped below the {reserve_kwh} kWh reserve floor during the event "
        f"hour: {during_and_just_after}")
    return (f"SOC enters the event hour at {soc_entering:.4f} kWh (above the "
            f"{reserve_kwh:.2f} kWh floor) and is held at exactly the floor for the "
            "rest of the hour, with real house load driving ordinary discharge")


@case
def case_solar_surplus_during_an_event_hour_does_not_round_trip_through_the_battery():
    """Regression for Finding 2 (issue #10 second adversarial review): within a SINGLE
    interval, the pre-fix code could charge the battery from solar surplus (the
    export-charging branch fires whenever exp[i] > 0 and imp[i] == 0, even inside a
    declared event hour) and then IMMEDIATELY discharge that same energy again via the
    event-forcing block a few lines below, counting the full amount as new
    "event_discharge" -- round-tripping energy that would have been exported directly
    anyway (at an ETA round-trip loss) while inflating demonstrated capacity/revenue as
    if it were genuinely new battery output. Confirmed against the real 2025 backtest
    before this fix: 12 intervals showed a charge-from-solar and an event-forced
    discharge in the same interval, totaling 5.32 kWh charged and 25.52 kWh
    event-discharged in those intervals alone.

    Fixture: the same three ordinary evening discharge hours (16, 17, 18 -- 3 kW load,
    identical to case_reserve_floor_holds_against_combined_ordinary_and_event_discharge)
    drain SOC from full down to the same ~4.01 kWh, then a declared event hour (HE20,
    floor_hour 19) carries a 2 kW solar surplus with ZERO concurrent consumption --
    exactly the (exp[i] > 0, imp[i] == 0, disch_win and is_event both True) condition
    that triggered the pre-fix bug.

    With the fix, solar surplus during the event hour must pass straight through to
    export (exp[i] == gen0[i] plus only the additional event-forced discharge the
    formula below predicts from PRIOR soc, i.e. from the value soc already held before
    this interval's solar arrived) -- never a smaller pass-through topped up by a
    different, round-tripped total. The two formulas differ measurably: computed by
    hand for this fixture, the pre-fix code would land the first event interval's
    export at ~1.696 kWh; the fix lands it at ~1.746 kWh (bigger, because none of the
    0.5 kWh solar was detoured through the battery's round-trip loss first)."""
    d, _imp0, _gen0 = _synthetic_day(consumption_kw=0.0, generation_kw=0.0)
    date = d.dt.dt.date.iloc[0]
    h = d.hour.values
    imp0 = np.where((h >= 16) & (h < 19), 3.0, 0.0) * 0.25
    gen0 = np.where((h >= 19) & (h < 20), 2.0, 0.0) * 0.25
    reserve_frac = 0.20
    reserve_kwh = reserve_frac * vb.CAP
    event_set = {(date, 19)}   # HE20 (19:00-19:59)
    imp, exp, soc_start, event_kwh, bau_kwh = vb.run_batt_vpp(
        d, imp0.copy(), gen0.copy(), vb.CAP, event_set, reserve_frac)

    idxs = np.where((h >= 19) & (h < 20))[0]
    assert (gen0[idxs] > 0).all(), "fixture precondition: solar surplus during the event hour"
    assert (imp0[idxs] == 0).all(), "fixture precondition: zero concurrent consumption"
    soc_entering = soc_start[idxs[0]]
    assert soc_entering > reserve_kwh, (
        f"fixture precondition failed: SOC must enter the event hour ({soc_entering}) "
        f"above the reserve floor ({reserve_kwh}) for this to exercise the bug")

    any_event_discharge = False
    for j in idxs:
        expected_extra = min(vb.PWRQ, max(soc_start[j] - reserve_kwh, 0.0) * vb.ETA)
        assert bau_kwh[j] == 0.0, (
            f"interval {j}: imp0 is zero here, so the ordinary/BAU discharge branch "
            f"must deliver nothing (got {bau_kwh[j]})")
        assert abs(exp[j] - (gen0[j] + expected_extra)) < 1e-6, (
            f"interval {j}: exp={exp[j]} does not equal raw solar ({gen0[j]}) plus the "
            f"event-forced increment computed from PRIOR soc ({expected_extra}) -- "
            "solar surplus was charged into the battery and partially round-tripped "
            "instead of passing straight through")
        assert abs(event_kwh[j] - expected_extra) < 1e-6, (
            f"interval {j}: event_discharge={event_kwh[j]} != {expected_extra}")
        if expected_extra > 1e-9:
            any_event_discharge = True
    assert any_event_discharge, (
        "fixture must still exercise genuine event-forced discharge from prior SOC, "
        "or this case can't distinguish a fix from simply disabling event dispatch")
    return (f"solar surplus ({gen0[idxs].sum():.2f} kWh) passes straight through to "
            f"export during the event hour ({event_kwh[idxs].sum():.4f} kWh of "
            "event-forced discharge from prior SOC layered on top, none of it a "
            "charge-then-discharge round trip)")


# ---------------------------------------------------------------------------
# (b) build_calendar() fail-closed / correctness -- synthetic xlsx, no private
#     archive needed (openpyxl fixtures built in a tempdir)
# ---------------------------------------------------------------------------
_HOURLY_COLS = [
    "Aggregation Identifier (anonymized)", "UDC (anonymized)",
    "Option 3 Resource duration (hours)", "Customer Class", "Storage Type", "Month",
    "Date", "Hour End (1-24)", "Interval End Time (Pacific)", "Is Option 3 Event Hour?",
    "Is Option 3 Event Day?", "Option 3 Event Type", "Is Weekday Non-Holiday?",
    "Net aggregated discharge (MW)", "Prescriptive baseline (MW)",
    "Demonstrated Net Discharge (MW)", "CAISO LMP ($/MWh)",
]
_MONTHLY_COLS = [
    "Aggregation Identifier (anonymized)", "UDC (anonymized)",
    "Option 3 Resource duration (hours)", "Customer Class", "Storage Type",
    "2025 Participation month", "Total Nominal Battery Storage Size (MWh)",
    "Total Nominal Battery Power (MW)", "Number of sites in aggregation",
    "Number of individual batteries", "DSGS Prescriptive Baseline (MW)",
    "Provider self-reported capacity estimate (MW)", "Monthly Capacity Payment ($)",
    "Demonstrated Capacity (MW)", "Monthly energy-only payment ($)",
    "Total Monthly Settlement ($) (approx.)",
]


def _hourly_row(agg="Aggregation-001", is_event=True, event_type="Test Capacity",
                 date="2025-07-15", hour_end=20, lmp=50.0):
    return {
        "Aggregation Identifier (anonymized)": agg, "UDC (anonymized)": "UDC 2",
        "Option 3 Resource duration (hours)": 2, "Customer Class": "Residential",
        "Storage Type": "Stationary", "Month": 7, "Date": pd.Timestamp(date),
        "Hour End (1-24)": hour_end, "Interval End Time (Pacific)": pd.Timestamp(date),
        "Is Option 3 Event Hour?": is_event, "Is Option 3 Event Day?": is_event,
        "Option 3 Event Type": event_type if is_event else None,
        "Is Weekday Non-Holiday?": True, "Net aggregated discharge (MW)": 0.001,
        "Prescriptive baseline (MW)": 0.0001, "Demonstrated Net Discharge (MW)": 0.0009,
        "CAISO LMP ($/MWh)": lmp,
    }


def _monthly_row(agg="Aggregation-001", month=7, power_mw=0.01, baseline_mw=0.00108,
                 demo_mw=0.001, payment=16.38 * 1.0):
    return {
        "Aggregation Identifier (anonymized)": agg, "UDC (anonymized)": "UDC 2",
        "Option 3 Resource duration (hours)": 2, "Customer Class": "Residential",
        "Storage Type": "Stationary", "2025 Participation month": month,
        "Total Nominal Battery Storage Size (MWh)": 0.02,
        "Total Nominal Battery Power (MW)": power_mw,
        "Number of sites in aggregation": 1, "Number of individual batteries": 1,
        "DSGS Prescriptive Baseline (MW)": baseline_mw,
        "Provider self-reported capacity estimate (MW)": demo_mw,
        "Monthly Capacity Payment ($)": payment, "Demonstrated Capacity (MW)": demo_mw,
        "Monthly energy-only payment ($)": 0.0,
        "Total Monthly Settlement ($) (approx.)": payment,
    }


def _write_fixture_xlsx(path, hourly_rows, monthly_rows):
    hourly = pd.DataFrame(hourly_rows, columns=_HOURLY_COLS)
    monthly = pd.DataFrame(monthly_rows, columns=_MONTHLY_COLS)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"note": ["synthetic fixture, not the real CEC file"]}).to_excel(
            xw, sheet_name="Data Dictionary", index=False)
        hourly.to_excel(xw, sheet_name="Hourly Discharge Dataset", index=False)
        monthly.to_excel(xw, sheet_name="Monthly Aggregation Dataset", index=False)


@case
def case_build_calendar_aborts_on_missing_raw_file():
    bogus = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    bogus.unlink()   # the file must NOT exist
    try:
        vb.build_calendar(xlsx_path=bogus)
    except SystemExit as exc:
        assert "missing raw CEC file" in str(exc), f"wrong refusal message: {exc}"
        return "build_calendar refuses a missing raw xlsx path"
    else:
        raise AssertionError("build_calendar accepted a nonexistent raw file")


@case
def case_build_calendar_rejects_disallowed_event_type():
    """The Data Dictionary says 'Capacity' (LMP-triggered) events did not occur in
    2025; if the raw file ever contained one, build_calendar must abort rather than
    silently fold it into the calendar and undermine the "no real events in 2025"
    finding this script states as fact."""
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    hourly = [_hourly_row(event_type="Capacity")]
    monthly = [_monthly_row()]
    _write_fixture_xlsx(tmp, hourly, monthly)
    try:
        vb.build_calendar(xlsx_path=tmp)
    except SystemExit as exc:
        assert "Capacity" in str(exc), f"wrong refusal message: {exc}"
        return "build_calendar refuses a disallowed 'Capacity' event type"
    else:
        raise AssertionError("build_calendar accepted a 'Capacity' event row")
    finally:
        tmp.unlink(missing_ok=True)


@case
def case_build_calendar_rejects_inconsistent_lmp_same_hour():
    """Two aggregations reporting the SAME (date, hour) but DIFFERENT CAISO LMP
    values would mean the one-DLAP-per-UDC assumption this script relies on does not
    hold; it must abort rather than silently pick one."""
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    hourly = [
        _hourly_row(agg="Aggregation-001", lmp=50.0),
        _hourly_row(agg="Aggregation-002", lmp=55.0),
    ]
    monthly = [_monthly_row(agg="Aggregation-001"), _monthly_row(agg="Aggregation-002")]
    _write_fixture_xlsx(tmp, hourly, monthly)
    try:
        vb.build_calendar(xlsx_path=tmp)
    except SystemExit as exc:
        assert "distinct CAISO" in str(exc), f"wrong refusal message: {exc}"
        return "build_calendar refuses inconsistent same-hour CAISO LMP values"
    else:
        raise AssertionError("build_calendar accepted inconsistent same-hour LMP values")
    finally:
        tmp.unlink(missing_ok=True)


@case
def case_build_calendar_majority_vote_ties_toward_test_capacity():
    """A 1-1 tie between 'Test Capacity' and 'Test Non-Capacity' for the same (date,
    hour) resolves to 'Test Capacity' (the documented, deliberate tie-break).

    Redirects vb.CALENDAR_CSV to a tempdir first: build_calendar() always writes to
    that path, and without redirecting it this synthetic 2-row fixture would clobber
    the REAL committed data/dsgs_event_calendar_2025.csv."""
    real_calendar = vb.CALENDAR_CSV
    tmp_dir = tempfile.TemporaryDirectory()
    vb.CALENDAR_CSV = pathlib.Path(tmp_dir.name) / "dsgs_event_calendar_2025.csv"
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    hourly = [
        _hourly_row(agg="Aggregation-001", event_type="Test Non-Capacity"),
        _hourly_row(agg="Aggregation-002", event_type="Test Capacity"),
    ]
    monthly = [_monthly_row(agg="Aggregation-001"), _monthly_row(agg="Aggregation-002")]
    _write_fixture_xlsx(tmp, hourly, monthly)
    try:
        cal, rates_, ratio = vb.build_calendar(xlsx_path=tmp)
        assert len(cal) == 1
        assert cal.iloc[0]["event_type"] == "Test Capacity", cal.iloc[0]["event_type"]
        return "a 1-1 event-type tie resolves to 'Test Capacity'"
    finally:
        vb.CALENDAR_CSV = real_calendar
        tmp.unlink(missing_ok=True)
        tmp_dir.cleanup()


@case
def case_build_calendar_writes_a_valid_csv_with_the_udc_caveat_comment():
    """End-to-end build_calendar() against a small synthetic fixture: the committed
    CSV path is written with a leading '#'-commented UDC-inference caveat line, and
    load_calendar() can read it straight back (comment='#' skips that line)."""
    real_calendar = vb.CALENDAR_CSV
    tmp_dir = tempfile.TemporaryDirectory()
    vb.CALENDAR_CSV = pathlib.Path(tmp_dir.name) / "dsgs_event_calendar_2025.csv"
    tmp_xlsx = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    try:
        hourly = [_hourly_row(date="2025-07-15", hour_end=20),
                  _hourly_row(date="2025-07-15", hour_end=21, is_event=False)]
        monthly = [_monthly_row()]
        _write_fixture_xlsx(tmp_xlsx, hourly, monthly)
        vb.build_calendar(xlsx_path=tmp_xlsx)
        assert vb.CALENDAR_CSV.exists()
        first_line = vb.CALENDAR_CSV.read_text().splitlines()[0]
        assert first_line.startswith("#") and "INFERENCE" in first_line, first_line
        cal = vb.load_calendar()
        assert list(cal.columns) == ["date", "hour_end", "event_type", "caiso_lmp_usd_per_mwh"]
        assert len(cal) == 1   # only the is_event=True row
        return "build_calendar writes a caveat-commented CSV that load_calendar reads back"
    finally:
        vb.CALENDAR_CSV = real_calendar
        tmp_xlsx.unlink(missing_ok=True)
        tmp_dir.cleanup()


@case
def case_build_calendar_fails_closed_on_rate_drift():
    """Regression for a third Codex review round: an updated raw workbook whose
    derived $/kW-month rate no longer matches the hardcoded MONTHLY_RATE_USD_PER_KW
    used to be only a printed NOTICE -- the CSV would still be published successfully,
    and every subsequent normal run would silently combine a fresh event calendar
    with a now-stale hardcoded rate. build_calendar() must now raise SystemExit
    instead, so a maintainer is forced to update the constant before the tool
    considers itself done."""
    real_calendar = vb.CALENDAR_CSV
    tmp_dir = tempfile.TemporaryDirectory()
    vb.CALENDAR_CSV = pathlib.Path(tmp_dir.name) / "dsgs_event_calendar_2025.csv"
    tmp_xlsx = pathlib.Path(tempfile.mkstemp(suffix=".xlsx")[1])
    try:
        hourly = [_hourly_row(date="2025-07-15", hour_end=20)]
        # payment=999 with power_mw=0.01 derives a rate wildly different from the
        # hardcoded MONTHLY_RATE_USD_PER_KW[7] = 16.38 -- the drift this case exists
        # to catch.
        monthly = [_monthly_row(payment=999.0)]
        _write_fixture_xlsx(tmp_xlsx, hourly, monthly)
        try:
            vb.build_calendar(xlsx_path=tmp_xlsx)
            raise AssertionError("expected build_calendar to raise SystemExit on rate drift")
        except SystemExit as e:
            assert "drift" in str(e).lower() or "hardcoded" in str(e).lower(), str(e)
        # The CSV must still have been written (it's independent of the Python rate
        # constants) -- only the constant-drift check fails, not the calendar build.
        assert vb.CALENDAR_CSV.exists(), (
            "the calendar CSV write must not be blocked by a rate-drift failure -- "
            "they're independent artifacts")
        return "build_calendar raises SystemExit on rate drift, after still writing the CSV"
    finally:
        vb.CALENDAR_CSV = real_calendar
        tmp_xlsx.unlink(missing_ok=True)
        tmp_dir.cleanup()


@case
def case_main_preserves_committed_per_aggregation_sensitivity_without_the_archive():
    """Regression for a third Codex review round: on a checkout without the private
    raw CEC archive (the NORMAL case for CI and any fresh clone, since this script
    is CI_RUNNABLE), an earlier version of main() overwrote the committed
    per_aggregation_sensitivity field with a "NOT COMPUTED" placeholder string on
    every run -- destroying the real 14-aggregation breakdown that backs the
    report's own published range, every time the archive-less path executed. Fixed:
    when the archive is absent but a committed artifact already exists, main()
    preserves that artifact's existing per_aggregation_sensitivity value instead of
    overwriting it with a placeholder."""
    real_raw_xlsx = vb.RAW_XLSX
    real_results_json = vb.RESULTS_JSON
    tmp_dir = tempfile.TemporaryDirectory()
    vb.RAW_XLSX = pathlib.Path(tmp_dir.name) / "does_not_exist.xlsx"
    vb.RESULTS_JSON = pathlib.Path(tmp_dir.name) / "dsgs_vpp_backtest.json"
    try:
        assert not vb.RAW_XLSX.exists()
        preserved_marker = {"net_usd_min": 12.34, "net_usd_max": 56.78,
                            "note": "a real, previously-committed breakdown"}
        vb.RESULTS_JSON.write_text(json.dumps({"per_aggregation_sensitivity": preserved_marker}))
        # Calls the REAL function main() uses, not a reimplementation of its logic --
        # `d` is unused on this branch (RAW_XLSX absent) so None is fine here.
        value = vb.per_aggregation_sensitivity_or_preserved(None)
        assert value == preserved_marker, (
            "the committed per_aggregation_sensitivity must be preserved, not "
            f"overwritten with a placeholder -- got {value}")
        return "an archive-less run preserves the existing committed per_aggregation_sensitivity"
    finally:
        vb.RAW_XLSX = real_raw_xlsx
        vb.RESULTS_JSON = real_results_json
        tmp_dir.cleanup()


# ---------------------------------------------------------------------------
# (c) committed-artifact shape/content checks -- public, no private archive
#     needed (deliberately NOT gated behind _require_archive, matching
#     test_nem3_grandfathering.py's digest-check case: these are the checks CI
#     can and should run on every push)
# ---------------------------------------------------------------------------
@case
def case_committed_calendar_csv_has_expected_shape():
    _require_calendar()
    cal = vb.load_calendar()
    assert set(cal.columns) == {"date", "hour_end", "event_type", "caiso_lmp_usd_per_mwh"}
    assert cal["event_type"].isin(["Test Capacity", "Test Non-Capacity"]).all(), (
        "committed calendar contains an event type other than Test Capacity/"
        "Test Non-Capacity -- the '2025 had no real events' finding would be false")
    assert cal["hour_end"].between(1, 24).all()
    assert (cal["caiso_lmp_usd_per_mwh"] > 0).all()
    dates = pd.to_datetime(cal["date"])
    assert dates.min() >= pd.Timestamp("2025-05-01")
    assert dates.max() <= pd.Timestamp("2025-10-31")
    assert not cal.duplicated(subset=["date", "hour_end"]).any(), (
        "duplicate (date, hour_end) in the committed calendar")
    first_line = vb.CALENDAR_CSV.read_text().splitlines()[0]
    assert first_line.startswith("#") and "INFERENCE" in first_line, (
        "committed calendar is missing its UDC-inference caveat comment line")
    return f"{len(cal)} event-hour rows, {dates.dt.date.nunique()} dates, all 2025-05..10"


@case
def case_committed_results_json_has_expected_sections():
    if not vb.RESULTS_JSON.exists():
        raise SkipCase(f"needs the committed {vb.RESULTS_JSON}")
    result = json.loads(vb.RESULTS_JSON.read_text())
    for k in ("hypothetical", "household_has_battery_today", "measured_window",
              "udc_identity_caveat", "finding_2025_no_real_emergency_events",
              "finding_2026_enrollment_eligibility", "grandfathering_interaction_finding",
              "backup_reserve_caveat", "payment_rate_source", "events_outside_window",
              "events_in_window", "miss_rate", "revenue", "opportunity_cost_note",
              "second_program_year_event_list_2024", "total_discharge_kwh_note",
              "partial_season_caveat", "per_aggregation_sensitivity",
              "partial_months_note"):
        assert k in result, f"results section missing: {k}"
    caveat = result["partial_season_caveat"]
    assert "NOT DETERMINED" in caveat and "PARTIAL-SEASON" in caveat, (
        "Defect-#3 fix: the partial-season caveat must say plainly that a "
        "full-season/annual figure is not determined, not just imply it")
    assert "VNEM" in result["grandfathering_interaction_finding"], (
        "AC6's grandfathering-interaction check must cite what was actually searched")
    assert result["hypothetical"] is True
    assert result["household_has_battery_today"] is False
    rev = result["revenue"]["reserve_20pct"]
    assert rev["gross_usd"] >= 0
    assert rev["net_usd"] == round(rev["gross_usd"] - rev["opportunity_cost_usd"], 2)
    assert rev["total_discharge_kwh"] > 0, "AC4 needs a reported kWh-exported figure"
    rev0 = result["revenue"]["reserve_0pct_sensitivity"]
    assert rev0["total_discharge_kwh"] >= rev["total_discharge_kwh"], (
        "0% reserve should deliver at least as much total kWh as 20% reserve")
    y2024 = result["second_program_year_event_list_2024"]
    assert y2024["program_year"] == 2024
    assert y2024["replayable"] is False
    miss = result["miss_rate"]["reserve_20pct"]
    assert 0 <= miss["misses"] <= miss["total"]
    return f"gross=${rev['gross_usd']:.2f} net=${rev['net_usd']:.2f} miss={miss}"


@case
def case_partial_calendar_month_contributes_zero_revenue():
    """Regression for issue #10's Codex review Finding 2: July 2025 has event hours on
    BOTH sides of the measured-window boundary (2025-07-22 pre-window, 2025-07-29..31
    in-window) -- confirmed directly against the committed calendar. DSGS's own Monthly
    DC is an LMP-weighted average over ALL of a month's event hours; pricing only the
    in-window subset at July's full published $/kW rate would misrepresent a partial
    month as a complete settlement. July must therefore contribute $0 to gross/net
    revenue in both reserve scenarios, while its in-window hours still appear in
    hour_detail (the dispatch/miss-rate simulation for the days that DO exist is valid,
    only the monthly capacity PAYMENT is not)."""
    if not vb.RESULTS_JSON.exists():
        raise SkipCase(f"needs the committed {vb.RESULTS_JSON}")
    result = json.loads(vb.RESULTS_JSON.read_text())
    note = result["partial_months_note"]
    assert "[7]" in note, f"expected July (month 7) to be the partial month, got: {note}"
    for scenario in ("reserve_20pct", "reserve_0pct_sensitivity"):
        monthly = result["revenue"][scenario]["monthly_gross_usd"]
        assert "7" not in monthly, (
            f"{scenario}: July must be excluded from monthly_gross_usd entirely, "
            f"not priced from its incomplete in-window subset -- got {monthly}")
    july_hours = [r for r in result["hour_detail"] if r["month"] == 7]
    assert len(july_hours) == 6, (
        "July's 6 in-window event hours (07-29, 07-30, 07-31, 2 hours each) must "
        "still appear in hour_detail even though they earn no monthly capacity "
        f"payment -- got {len(july_hours)}")
    return f"July excluded from revenue ({note[:60]}...), still present in hour_detail ({len(july_hours)} hours)"


@case
def case_opportunity_cost_excludes_partial_month_dispatch_effect():
    """Regression for a third adversarial-review round's finding: net_revenue must not
    net a partial month's (July's) event-forced dispatch bill effect against zero
    revenue for that month. Reproduced directly against the real archive: the full
    event set's opportunity cost (-$14.16 at 20% reserve) differs measurably from the
    priced-months-only opportunity cost (-$11.48) -- the committed artifact's
    opportunity_cost_usd must be the LATTER, not the former, even though hour_detail
    still reports July's dispatch outcomes (AC2 compliance)."""
    _require_archive()
    _require_calendar()
    d = br.load()
    cal = vb.load_calendar()
    result = vb.backtest(d, cal)
    committed_opp_cost = result["revenue"]["reserve_20pct"]["opportunity_cost_usd"]

    window_start = d.dt.min().date()
    window_end = d.dt.max().date()
    event_set, inwin, outwin = vb._event_set_and_hours(cal, window_start, window_end)
    partial_months = sorted(set(r.date.month for r in outwin.itertuples())
                             & set(r.date.month for r in inwin.itertuples()))
    assert partial_months, "this fixture is expected to have a partial month (July)"

    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    imp_bau, exp_bau, _, _ = bp.run_batt(d, imp0, gen0, vb.CAP, "greedy")
    bill_bau = bp.billed(d, imp_bau, exp_bau)

    imp_full, exp_full, _, _, _ = vb.run_batt_vpp(d, imp0, gen0, vb.CAP, event_set, 0.20)
    opp_cost_full_event_set = round(bp.billed(d, imp_full, exp_full) - bill_bau, 2)

    event_set_priced = {(dt_, h) for (dt_, h) in event_set if dt_.month not in partial_months}
    imp_priced, exp_priced, _, _, _ = vb.run_batt_vpp(d, imp0, gen0, vb.CAP, event_set_priced, 0.20)
    opp_cost_priced_only = round(bp.billed(d, imp_priced, exp_priced) - bill_bau, 2)

    assert abs(opp_cost_full_event_set - opp_cost_priced_only) > 0.5, (
        "this fixture's partial month must have a non-trivial dispatch effect, or this "
        "test isn't exercising the bug it's meant to catch")
    assert committed_opp_cost == opp_cost_priced_only, (
        f"backtest()'s reported opportunity_cost_usd ({committed_opp_cost}) must match "
        f"the priced-months-only dispatch ({opp_cost_priced_only}), not the full "
        f"event set including the partial month ({opp_cost_full_event_set})")
    return (f"opportunity cost ${committed_opp_cost} correctly excludes the partial "
            f"month's dispatch effect (full-event-set would have given "
            f"${opp_cost_full_event_set})")


@case
def case_events_outside_window_carry_zero_attributed_revenue():
    """The issue's core no-extrapolation requirement: events outside the measured
    window (pre-window 2025 events, and the entirely-unpublished 2026 season) must be
    counted, but the artifact's revenue figures must derive ONLY from in-window hours
    -- checked by recomputing revenue from hour_detail (which is itself restricted to
    in-window hours) and confirming it reproduces the reported gross figure."""
    if not vb.RESULTS_JSON.exists():
        raise SkipCase(f"needs the committed {vb.RESULTS_JSON}")
    result = json.loads(vb.RESULTS_JSON.read_text())
    out = result["events_outside_window"]
    assert out["pre_window_count"] >= 0
    assert "2026" in out["season_2026_note"]
    n_hour_detail = len(result["hour_detail"])
    assert n_hour_detail == result["events_in_window"]["count"], (
        "hour_detail must cover exactly the in-window events, no more")
    return (f"{out['pre_window_count']} pre-window event hours excluded; "
           f"2026 season gap disclosed; hour_detail covers exactly the "
           f"{n_hour_detail} in-window hours")


@case
def case_2026_enrollment_eligibility_finding_is_stated_plainly():
    """Regression for issue #10's third adversarial review: an earlier version of
    this finding wrongly concluded the household "could not join at all" by
    conflating the CEC's AGGREGATOR-level 2026 restriction with a household-level
    one. The corrected finding must describe the restriction accurately (aggregator
    eligibility + the Appendix A funding cap) and land on NOT DETERMINED for the
    household's own prospects, not a false certainty in either direction."""
    if not vb.RESULTS_JSON.exists():
        raise SkipCase(f"needs the committed {vb.RESULTS_JSON}")
    result = json.loads(vb.RESULTS_JSON.read_text())
    finding = result["finding_2026_enrollment_eligibility"]
    assert "October 2025" in finding and "2026" in finding
    assert "AGGREGATOR" in finding.upper()
    assert "NOT DETERMINED" in finding
    assert "retracted" in finding.lower(), (
        "the corrected finding must explicitly retract the earlier household-level "
        "misreading, not just quietly replace it")
    return "the 2026-eligibility finding correctly scopes the restriction to aggregators and lands on not-determined"


# ---------------------------------------------------------------------------
# (d) real-archive-gated: cross-check against the ALREADY-VALIDATED
#     battery_dispatch_policies.json figure, and byte-identical regeneration
# ---------------------------------------------------------------------------
@case
def case_bau_bill_matches_battery_dispatch_policies_committed_figure():
    """backtest()'s BAU (no-VPP) battery bill must agree with the ALREADY COMMITTED,
    ALREADY VALIDATED data/battery_dispatch_policies.json figure for the identical
    scenario (13.5 kWh, greedy policy): baseline_bill_current_rates - pw3.greedy.save.
    Two independently-computed figures for the same thing silently drifting apart is
    exactly the CLAUDE.md 3 failure mode this case exists to catch."""
    _require_archive()
    _require_calendar()
    committed = ROOT / "data" / "battery_dispatch_policies.json"
    if not committed.exists():
        raise SkipCase(f"needs the committed {committed}")
    ref = json.loads(committed.read_text())
    expected_bau_bill = ref["baseline_bill_current_rates"] - ref["pw3"]["greedy"]["save"]

    d = br.load()
    cal = vb.load_calendar()
    result = vb.backtest(d, cal)
    assert abs(result["bau_battery_bill_usd"] - expected_bau_bill) < 1.0, (
        result["bau_battery_bill_usd"], expected_bau_bill)
    return (f"BAU battery bill ${result['bau_battery_bill_usd']:,.2f} agrees with "
           f"battery_dispatch_policies.json's pw3/greedy figure (${expected_bau_bill:,.2f})")


@case
def case_artifact_regenerates_byte_identically():
    """main() now embeds per_aggregation_sensitivity (Defect-#2 fix), which needs the
    private raw CEC archive to compute -- so full byte-identical regeneration needs
    it too, not just the Green Button archive and the committed calendar."""
    _require_archive()
    _require_calendar()
    _require_raw_xlsx()
    path = vb.RESULTS_JSON
    before = path.read_bytes()
    vb.main()
    after = path.read_bytes()
    assert after == before, "data/dsgs_vpp_backtest.json is not reproducible"
    return "data/dsgs_vpp_backtest.json regenerates byte-identically"


@case
def case_per_aggregation_sensitivity_reports_a_real_range_not_the_union():
    """Defect-#2 fix: the committed artifact's headline gross/net/kWh/miss-rate
    figures come from the UNION of ~14 aggregations' event calendars -- a deliberate
    upper-bound-on-event-FREQUENCY scenario, not any single real household's actual
    schedule (a household on a single aggregation's calendar can have a HIGHER net
    revenue than the union: fewer, better-timed events can cost less in opportunity
    cost than the union's larger and less selectively-timed event set costs in
    return, so the union figure is not guaranteed to bound the per-aggregation range
    from above). This case checks that the artifact reports the per-individual-
    aggregation spread too (all ~14 isolated, independently backtested against their
    own calendar), with a sane internal shape (min <= every row <= max)."""
    _require_archive()
    _require_raw_xlsx()
    if not vb.RESULTS_JSON.exists():
        raise SkipCase(f"needs the committed {vb.RESULTS_JSON}")
    result = json.loads(vb.RESULTS_JSON.read_text())
    pas = result["per_aggregation_sensitivity"]
    assert isinstance(pas, dict), (
        "per_aggregation_sensitivity must be a computed dict when the raw archive "
        "is present, not the 'NOT COMPUTED' placeholder string")
    assert pas["n_aggregations"] >= 10, (
        f"expected ~14 isolatable aggregations, got {pas['n_aggregations']}")
    assert len(pas["per_aggregation"]) == pas["n_aggregations"]
    assert pas["net_usd_min"] <= pas["net_usd_max"]
    assert 0 <= pas["miss_rate_min"] <= pas["miss_rate_max"] <= 1
    for agg_id, row in pas["per_aggregation"].items():
        assert row["n_event_hours_in_window"] >= 0
        assert pas["net_usd_min"] - 1e-6 <= row["net_usd"] <= pas["net_usd_max"] + 1e-6, (
            agg_id, row["net_usd"])

    # cross-check via a fresh direct call (not just re-reading the committed JSON)
    d = br.load()
    fresh = vb.per_aggregation_sensitivity(d)
    assert fresh["n_aggregations"] == pas["n_aggregations"]
    assert abs(fresh["net_usd_min"] - pas["net_usd_min"]) < 0.01
    assert abs(fresh["net_usd_max"] - pas["net_usd_max"]) < 0.01

    union_net = result["revenue"]["reserve_20pct"]["net_usd"]
    return (f"{pas['n_aggregations']} aggregations isolated; net revenue "
            f"${pas['net_usd_min']:.2f}-${pas['net_usd_max']:.2f} vs. the "
            f"union-based headline ${union_net:.2f}")


@case
def case_committed_calendar_regenerates_byte_identically_from_raw_archive():
    _require_raw_xlsx()
    path = vb.CALENDAR_CSV
    before = path.read_bytes() if path.exists() else None
    vb.build_calendar()
    after = path.read_bytes()
    if before is not None:
        assert after == before, "data/dsgs_event_calendar_2025.csv is not reproducible"
    return "data/dsgs_event_calendar_2025.csv regenerates byte-identically from the raw archive"


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
        except Exception as exc:  # noqa: BLE001
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
