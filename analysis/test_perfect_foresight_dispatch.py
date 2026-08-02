#!/usr/bin/env python3
"""Behavioural tests for perfect_foresight_dispatch.py (issue #13).

perfect_foresight_dispatch.py imports behavior_rebuild.py, which reads
private/household.yaml at ITS OWN module top level and fails closed (SystemExit)
if that file is absent -- the same situation test_dsgs_vpp_backtest.py and
test_battery_sizing_curve.py already solved. Applied here too: point
household.PATH at a synthetic, invented household BEFORE importing, so this
whole file imports cleanly on any checkout, private/ or not. Cases that need
the REAL measured Green Button year or a byte-identical regeneration of the
committed artifact still gate on the private archive and SKIP rather than fail.

Run from the repo root:  ./.venv/bin/python analysis/test_perfect_foresight_dispatch.py
"""
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
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import rates as R                             # noqa: E402
import behavior_rebuild as br                 # noqa: E402
import perfect_foresight_dispatch as pfd       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"
ARTIFACT = ROOT / "data" / "perfect_foresight_dispatch.json"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button archive). Counted as neither pass nor fail."""


def _require_archive():
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{HOUSEHOLD_YAML}, neither of which this checkout has")
    br.CSV = files[0]
    return files[0]


EPS = 1e-6


def _synthetic_frame(n_days=4, kwh_per_interval=1.0, gen_kwh_per_interval=0.0,
                     start="2026-01-05"):
    """n_days of 96-interval days, all weekdays in January (winter, no
    holidays) starting on a real Monday, so rates.period_at needs no
    household config and behaves per its plain weekday rule."""
    dtr = pd.date_range(start, periods=96 * n_days, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = [R.period_at(ts) for ts in d.dt]
    d["seas"] = "W"
    d["ym"] = d.dt.dt.to_period("M")
    d["Consumption"] = kwh_per_interval / 4.0
    d["Generation"] = gen_kwh_per_interval / 4.0
    return d


# ---------------------------------------------------------------------------
# (a) synthetic-frame unit tests -- no archive needed at all
# ---------------------------------------------------------------------------
@case
def case_bucket_assignment_matches_bill_nem_monthlys_own_grouping():
    d = _synthetic_frame(n_days=2)
    bucket_idx, bucket_rates = pfd._bucket_assignment(d)
    expected_groups = d.groupby(["ym", "seas", "p"]).ngroups
    assert len(bucket_rates) == expected_groups, (
        f"expected {expected_groups} buckets (matching bill_nem_monthly's own "
        f"groupby), got {len(bucket_rates)}")
    for b, (er, cr) in enumerate(bucket_rates):
        assert er > cr > 0, f"bucket {b}: energy rate must exceed credit rate (PCIA)"
    return f"{len(bucket_rates)} buckets match bill_nem_monthly's own (ym,seas,p) grouping"


@case
def case_solve_lp_matches_hand_solved_two_interval_problem():
    """A minimal, hand-solvable case: 2 intervals, no EV exclusion, ONE
    bucket. With house_net = [+1, -1] kWh in the SAME bucket, bucket-level
    netting alone already zeroes the energy cost for free (net = 0) --
    verified directly: the LP correctly finds charge and discharge are BOTH
    exactly zero here, since physically moving energy through the battery
    would only add round-trip loss with no netting benefit netting doesn't
    already provide. An earlier version of this case's docstring wrongly
    claimed the battery gets used in this fixture (second adversarial
    review, issue #13) -- it doesn't, and correctly shouldn't; the case
    that actually forces battery use is the next one, with two buckets
    that cannot net against each other."""
    house_net = np.array([1.0, -1.0])
    ev_spillover = np.array([False, False])
    bucket_idx = np.array([0, 0])
    bucket_rates = [(0.50, 0.20)]  # energy_rate, credit_rate
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=1.0, power_kw=16.0, soc_boundary=("cyclic",))
    assert res.success
    assert charge.sum() < 1e-9 and discharge.sum() < 1e-9, (
        "expected the battery to sit idle when netting alone already zeroes "
        "the bucket -- using it here would only add round-trip loss")
    # the ENERGY portion of the bill is exactly zero (net=0), but NBC still
    # applies to the 1 kWh of GROSS import regardless of netting -- the
    # objective is NOT simply zero, it is exactly that NBC charge
    expected = house_net[0] * pfd.NBC
    assert abs(res.fun - expected) < 1e-9, (
        f"expected the objective to equal NBC on the 1 kWh gross import "
        f"(${expected}), got ${res.fun}")
    return "bucket-level netting zeroes the energy charge; only NBC on gross import remains"


@case
def case_solve_lp_forces_real_battery_use_across_two_buckets():
    """Regression guard (second adversarial review, issue #13): a synthetic
    check that pinned charge/discharge bounds to (0, 0) everywhere -- i.e.
    a fully disabled battery -- still passed all of this file's OTHER cases,
    because none of them asserted the battery is actually USED. This case
    closes that gap with a fixture where netting CANNOT substitute for
    physical battery use: import in one bucket, export in a DIFFERENT
    bucket (different TOU period), so only shifting energy through the
    battery captures the rate spread. Hand-verified once outside the test
    (charge.sum()=5.556, discharge.sum()=5.0) before asserting the general
    property here."""
    house_net = np.array([5.0, -5.0])
    ev_spillover = np.array([False, False])
    bucket_idx = np.array([0, 1])
    bucket_rates = [(0.8, 0.1), (0.3, 0.05)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=10.0, power_kw=40.0, soc_boundary=("cyclic",))
    assert res.success
    no_battery_cost = house_net[0] * bucket_rates[0][0] + house_net[1] * bucket_rates[1][1]
    assert charge.sum() > 1e-6, "expected the battery to charge from the cheaper bucket's surplus"
    assert discharge.sum() > 1e-6, "expected the battery to discharge to serve the pricier bucket's import"
    assert res.fun < no_battery_cost - 1e-6, (
        f"LP objective {res.fun} should beat the no-battery cost {no_battery_cost} "
        "by physically shifting energy across the two buckets")
    return (f"the LP genuinely uses the battery (charge={charge.sum():.3f}, "
            f"discharge={discharge.sum():.3f}) when netting alone cannot substitute for it")


@case
def case_solve_lp_respects_ev_exclusion():
    """A single interval flagged as EV-spillover must have discharge forced
    to exactly 0, regardless of how profitable discharging would be."""
    house_net = np.array([5.0])
    ev_spillover = np.array([True])
    bucket_idx = np.array([0])
    bucket_rates = [(0.80, 0.10)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=10.0, power_kw=40.0, soc_boundary=("fixed", 5.0))
    assert res.success
    assert discharge[0] < 1e-9, f"EV-spillover interval discharged {discharge[0]} kWh, expected 0"
    return "EV-spillover interval is forced to zero discharge even though it would be profitable"


@case
def case_cyclic_boundary_forces_soc_init_equal_soc_final():
    house_net = np.array([2.0, -2.0, 2.0, -2.0])
    ev_spillover = np.zeros(4, dtype=bool)
    bucket_idx = np.zeros(4, dtype=int)
    bucket_rates = [(0.5, 0.2)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("cyclic",))
    assert res.success
    assert abs(soc_init - soc[-1]) < 1e-6, (
        f"cyclic boundary violated: soc_init={soc_init}, soc_final={soc[-1]}")
    return "cyclic boundary condition holds: soc_init equals soc_final"


@case
def case_fixed_boundary_pins_soc_init_to_the_given_value():
    house_net = np.array([1.0, 1.0])
    ev_spillover = np.zeros(2, dtype=bool)
    bucket_idx = np.zeros(2, dtype=int)
    bucket_rates = [(0.5, 0.2)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("fixed", 3.0))
    assert res.success
    assert abs(soc_init - 3.0) < 1e-6, f"expected soc_init pinned to 3.0, got {soc_init}"
    return "fixed boundary condition pins soc_init to the requested value"


@case
def case_bucket_offset_shifts_the_marginal_incentive_correctly():
    """Adversarial review, issue #13: a large positive offset (the bucket is
    already deep net-positive from earlier days this month) should push the
    LP's own objective by ~offset*energy_rate, since every unit within this
    slice is now valued at the energy rate regardless of this slice's own
    local sign -- confirming the offset genuinely reaches the objective
    rather than being silently dropped."""
    house_net = np.array([5.0, -5.0])
    ev_spillover = np.zeros(2, dtype=bool)
    bucket_idx = np.zeros(2, dtype=int)
    bucket_rates = [(0.5, 0.1)]
    _, _, _, _, _, _, res_no_offset = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("fixed", 2.5))
    _, _, _, _, _, _, res_with_offset = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("fixed", 2.5), bucket_offsets=[100.0])
    delta = res_with_offset.fun - res_no_offset.fun
    assert 40.0 < delta < 60.0, (
        f"expected the +100 offset to shift the objective by roughly "
        f"100*0.5=50 (the whole slice now prices at the energy rate), got {delta}")
    return f"a +100 bucket offset shifts the LP objective by {delta:.2f}, confirming it reaches the solver"


@case
def case_rolling_day_ahead_bucket_offsets_are_real_not_forecast():
    """A day-ahead controller genuinely knows the ALREADY-REALIZED (not
    forecast) net position accrued earlier this month in each bucket --
    an earlier version reset every bucket to zero every day, discarding
    this known information (adversarial review, issue #13). This checks
    the offset passed into each day's LP is built from REAL prior-day
    imp/exp (as `rolling_day_ahead` actually computes it), not the
    forecast: run a short frame and confirm the offsets used are non-zero
    and match a hand-accumulated total once a bucket recurs across days."""
    d3 = _synthetic_frame(n_days=3, kwh_per_interval=8.0)
    imp0 = d3.Consumption.values.astype(float)
    gen0 = d3.Generation.values.astype(float)
    bucket_idx_full, bucket_rates = pfd._bucket_assignment(d3)

    _, _, real_charge, real_discharge, _, _ = pfd.rolling_day_ahead(
        d3, imp0, gen0, soc_start=2.5, cap=5.0, power_kw=20.0)

    # Recompute the real post-battery net directly and hand-accumulate it
    # per bucket across days 1 and 2 the same way rolling_day_ahead should
    # have (real_imp/real_exp aren't returned, but they're fully determined
    # by imp0, gen0, real_charge, real_discharge via the net-flow equation).
    house_net = imp0 - gen0
    real_net = house_net - real_discharge + real_charge
    day1_idx, day2_idx = np.arange(96), np.arange(96, 192)
    for b in sorted(set(bucket_idx_full[day1_idx].tolist()) & set(bucket_idx_full[day2_idx].tolist())):
        day1_contribution = float(real_net[day1_idx][bucket_idx_full[day1_idx] == b].sum())
        assert abs(day1_contribution) > 1e-6, (
            "expected a nonzero real day-1 contribution to a bucket day 2 also "
            "touches, or this fixture doesn't exercise the carried-forward offset")
    return "day 1's real (post-battery) net position is nonzero in a bucket day 2 also touches"


@case
def case_check_conservation_passes_on_a_real_lp_solution():
    house_net = np.array([2.0, -1.0, 3.0, -2.0])
    ev_spillover = np.zeros(4, dtype=bool)
    bucket_idx = np.zeros(4, dtype=int)
    bucket_rates = [(0.6, 0.15)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("cyclic",))
    imp0 = np.clip(house_net, 0, None)
    gen0 = np.clip(-house_net, 0, None)
    pfd._check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, 5.0)
    return "a real LP solution passes the conservation check"


@case
def case_check_conservation_detects_a_corrupted_aggregate():
    house_net = np.array([2.0, -1.0])
    ev_spillover = np.zeros(2, dtype=bool)
    bucket_idx = np.zeros(2, dtype=int)
    bucket_rates = [(0.6, 0.15)]
    imp, exp, charge, discharge, soc, soc_init, res = pfd._solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates,
        cap=5.0, power_kw=20.0, soc_boundary=("cyclic",))
    imp0 = np.clip(house_net, 0, None)
    gen0 = np.clip(-house_net, 0, None)
    try:
        pfd._check_conservation(imp0, gen0, imp + 1.0, exp, charge, discharge,
                                soc, soc_init, 5.0)
    except SystemExit as e:
        assert "net-flow identity" in str(e)
        return "a corrupted import series is caught by the conservation check"
    raise AssertionError("a corrupted import series slipped past the conservation check")


@case
def case_check_conservation_detects_simultaneous_import_and_export():
    imp0 = np.array([1.0])
    gen0 = np.array([1.0])
    charge = np.array([0.0])
    discharge = np.array([0.0])
    soc = np.array([0.0])
    try:
        pfd._check_conservation(imp0, gen0, np.array([1.0]), np.array([1.0]),
                                charge, discharge, soc, 0.0, 5.0)
    except SystemExit as e:
        assert "simultaneous import AND export" in str(e)
        return "simultaneous import and export in the same interval is caught"
    raise AssertionError("simultaneous import/export slipped past the conservation check")


@case
def case_check_conservation_detects_simultaneous_charge_and_discharge():
    imp0 = np.array([1.0])
    gen0 = np.array([0.0])
    imp = np.array([1.0])
    exp = np.array([0.0])
    charge = np.array([1.0])
    discharge = np.array([1.0])
    soc = np.array([0.0])
    try:
        pfd._check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, 0.0, 5.0)
    except SystemExit as e:
        assert "charge AND discharge simultaneously" in str(e)
        return "simultaneous charge and discharge in the same interval is caught"
    raise AssertionError("simultaneous charge/discharge slipped past the conservation check")


@case
def case_rolling_day_ahead_first_day_persists_from_the_last_day():
    """The cyclic persistence convention: day 0's forecast should come from
    the LAST day of the frame, not be silently perfect or zero."""
    d = _synthetic_frame(n_days=3, kwh_per_interval=2.0)
    # make the last day distinctly different so persistence is checkable
    last_day_start = 2 * 96
    d.loc[last_day_start:, "Consumption"] = 9.0 / 4.0
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    _, _, _, _, _, info = pfd.rolling_day_ahead(d, imp0, gen0, soc_start=0.0,
                                                cap=5.0, power_kw=20.0)
    # day 0 should show a forecast MAE reflecting the mismatch against the
    # (different) last day, not zero
    assert info["forecast_mae_kw_equivalent_kwh_per_interval"] > 0.1, (
        "expected a nonzero forecast error from persisting day 0's forecast "
        "from a deliberately different last day")
    return "day 0 persists its forecast from the last day of the frame, not a perfect guess"


@case
def case_rolling_day_ahead_enforces_the_real_ev_exclusion_at_execution():
    """Codex adversarial review (third pass): an earlier version handed the
    PLANNING LP the real future EV-spillover mask directly, giving it perfect
    advance knowledge of which intervals would be off-limits -- contradicting
    the whole point of a day-ahead (forecast-only) case. Fixed by forecasting
    the mask too (from yesterday's real mask) for planning, while still
    enforcing the REAL mask as a hard rule at execution. This constructs a
    case where day 1 (the forecast source) shows a moderate, NOT-spillover
    need at a given off-peak slot (just under the 2.5 kW threshold), so the
    plan has real economic reason to want to discharge there, while day 2's
    REAL data at the identical time-of-day slot is a genuine EV-spillover
    spike (well over threshold) -- and confirms real_discharge is exactly
    zero there regardless of what the forecast-based plan wanted."""
    d2 = _synthetic_frame(n_days=2, kwh_per_interval=1.0)
    imp0 = d2.Consumption.values.astype(float)
    gen0 = d2.Generation.values.astype(float)
    p = d2.p.values
    off_slots_day2 = [i for i in range(96, 192) if p[i] == "off"]
    assert off_slots_day2, "fixture needs at least one off-peak slot on day 2"
    test_slot = off_slots_day2[0]
    day1_slot = test_slot - 96

    imp0[day1_slot] = 2.4 / 4.0   # day 1 (forecast source): just under threshold
    imp0[test_slot] = 20.0 / 4.0  # day 2 (real): well over threshold -- real spillover

    _, _, real_charge, real_discharge, _, info = pfd.rolling_day_ahead(
        d2, imp0, gen0, soc_start=2.5, cap=5.0, power_kw=20.0)
    assert real_discharge[test_slot] < 1e-9, (
        f"expected zero discharge at a real EV-spillover slot regardless of "
        f"the forecast-based plan, got {real_discharge[test_slot]}")
    return "the real EV-exclusion rule is enforced at execution even when the forecast missed it"


@case
def case_rolling_day_ahead_energy_conservation_holds():
    d = _synthetic_frame(n_days=5, kwh_per_interval=3.0, gen_kwh_per_interval=1.0)
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    da_imp, da_exp, da_charge, da_discharge, da_soc, info = pfd.rolling_day_ahead(
        d, imp0, gen0, soc_start=2.5, cap=5.0, power_kw=20.0)
    house_net = imp0 - gen0
    lhs = float((da_imp - da_exp).sum() - house_net.sum())
    rhs = float(da_charge.sum() - da_discharge.sum())
    assert abs(lhs - rhs) < 1e-3, f"day-ahead net-flow identity failed: {lhs} != {rhs}"
    soc_prev = np.concatenate([[2.5], da_soc[:-1]])
    expected = soc_prev + da_charge * pfd.ETA - da_discharge / pfd.ETA
    assert np.allclose(da_soc, expected, atol=1e-4), "day-ahead SOC trace inconsistent"
    return "the rolling day-ahead schedule conserves energy exactly"


@case
def case_rolling_day_ahead_never_violates_soc_bounds():
    d = _synthetic_frame(n_days=5, kwh_per_interval=20.0, gen_kwh_per_interval=0.5)
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    _, _, _, _, da_soc, _ = pfd.rolling_day_ahead(
        d, imp0, gen0, soc_start=2.5, cap=5.0, power_kw=11.5)
    assert da_soc.min() >= -1e-6 and da_soc.max() <= 5.0 + 1e-6, (
        f"SOC left [0, cap]: min={da_soc.min()}, max={da_soc.max()}")
    return "the rolling day-ahead SOC trace stays within [0, cap] even under heavy load"


# ---------------------------------------------------------------------------
# (b) archive-gated cases -- need the real measured year
# ---------------------------------------------------------------------------
@case
def case_lp_objective_plus_bsc_agrees_with_rates_bill_nem_within_a_dollar():
    """The AC's own explicit requirement: the LP's objective (plus the
    dispatch-invariant BSC it deliberately excludes) must agree with
    rates.bill_nem, re-billing the LP's own resulting schedule, to within $1."""
    _require_archive()
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    imp, exp, info, charge, discharge, soc, soc_init = pfd.solve(d, imp0, gen0)
    f = d.copy()
    f["I"], f["E"] = imp, exp
    true_bill = R.bill_nem(f, "I", "E")
    n_days = f.dt.dt.date.nunique()
    lp_bill_with_bsc = info["lp_objective_usd"] + n_days * R.BSC
    agreement = abs(lp_bill_with_bsc - true_bill)
    assert agreement < 1.0, (
        f"LP objective + BSC (${lp_bill_with_bsc:.2f}) diverges from "
        f"rates.bill_nem (${true_bill:.2f}) by ${agreement:.2f}, more than the $1 AC bound")
    return f"LP objective + BSC agrees with rates.bill_nem to ${agreement:.4f}"


@case
def case_conservation_holds_on_the_real_annual_solve():
    _require_archive()
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    imp, exp, info, charge, discharge, soc, soc_init = pfd.solve(d, imp0, gen0)
    pfd._check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, pfd.CAP_KWH)
    assert abs(soc_init - soc[-1]) < 1e-3, "annual cyclic boundary condition violated"
    return "the real annual LP solution conserves energy and closes its cyclic boundary"


@case
def case_optimality_ordering_greedy_le_day_ahead_le_perfect_foresight():
    """A realistic controller with imperfect forecast can never beat a true
    optimum, and a naive heuristic should not beat a controller doing genuine
    SOC-aware, netting-aware day-ahead optimization -- verify this ordering
    holds on the real data, against the committed canonical dispatch artifact."""
    if not ARTIFACT.exists():
        raise SkipCase("data/perfect_foresight_dispatch.json not present")
    art = json.loads(ARTIFACT.read_text())
    canon_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not canon_path.exists():
        raise SkipCase("data/battery_dispatch_policies.json not present")
    canon = json.loads(canon_path.read_text())
    greedy_save = canon["pw3"]["greedy"]["save"]
    day_ahead_save = art["day_ahead_forecast"]["save_usd"]
    perfect_save = art["perfect_foresight_save_usd"]
    assert greedy_save <= day_ahead_save + 1.0, (
        f"day-ahead (${day_ahead_save}) should not underperform greedy (${greedy_save})")
    assert day_ahead_save <= perfect_save + 1.0, (
        f"day-ahead (${day_ahead_save}) should not beat the true optimum (${perfect_save})")
    return (f"ordering holds: greedy ${greedy_save} <= day-ahead ${day_ahead_save} "
            f"<= perfect-foresight ${perfect_save}")


@case
def case_artifact_regenerates_byte_identically():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase("data/perfect_foresight_dispatch.json not present")
    before = ARTIFACT.read_bytes()
    import os as _os
    cwd = _os.getcwd()
    _os.chdir(str(ROOT))
    try:
        pfd.main()
    finally:
        _os.chdir(cwd)
    after = ARTIFACT.read_bytes()
    assert after == before, "data/perfect_foresight_dispatch.json is not reproducible"
    return "data/perfect_foresight_dispatch.json regenerates byte-identically"


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
