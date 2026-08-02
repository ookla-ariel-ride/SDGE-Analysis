#!/usr/bin/env python3
"""Perfect-foresight dispatch bound (issue #13) — how much value does the
greedy dispatch policy leave on the table?

battery_dispatch_policies.py's "greedy" policy is a threshold heuristic: serve
every import priced above the battery's stored-energy cost. Nobody has checked
how close that gets to the best ANY controller could do on this house's own
measured year, at the same hardware (13.5 kWh, 11.5 kW, 90% round-trip) and the
same EV-spillover exclusion (>=2.5 kW outside on-peak is never battery-served).

This script computes the TRUE annual-bill-minimizing dispatch as a linear
program, not a heuristic and not naive price arbitrage. The distinction
matters: the bill is NOT a simple per-kWh price. `rates.bill_nem_monthly` nets
import against export per (month, season, TOU-period) bucket -- 36 buckets
across the year -- charging the bucket's NET position at the higher "energy"
rate if it lands positive, crediting it at the lower "credit" rate (no PCIA) if
negative, and separately charges non-bypassable costs (NBC) on GROSS monthly
imports regardless of netting. A naive optimizer that just chases the biggest
per-interval price spread can genuinely produce a schedule that BILLS WORSE,
because it doesn't account for which side of zero a bucket's net position ends
up on, or that NBC keeps accruing even when energy is "free" to net out.

LP formulation, per 15-minute interval i (n = 35,040 intervals/year):
  charge_i, discharge_i        >= 0, each <= power_kw/4 (kWh this interval)
  imp_i, exp_i                 >= 0 (post-battery grid import/export)
  soc_i                        in [0, cap]  (state of charge after interval i)
  soc_init                     in [0, cap]  (state of charge before interval 0)

Constraints:
  net-flow:   imp_i - exp_i - charge_i + discharge_i = house_net_i
              (house_net_i = Consumption_i - Generation_i, fixed data)
  continuity: soc_i = soc_{i-1} + charge_i*ETA - discharge_i/ETA
              (soc_{-1} := soc_init)
  cyclic:     soc_init = soc_{n-1}
              -- a steady annual cycle, not a one-time year-1 boundary
              windfall/deficit (issue #12's Codex review caught exactly this
              class of bug in the heuristic dispatch script; built in here
              from the start rather than fixed after the fact)
  EV exclusion: discharge_i forced to 0 wherever kW_i >= 2.5 outside on-peak
              (the same rule battery_dispatch_policies.run_batt applies)

Objective (minimize; BSC is dispatch-invariant, excluded):
  For each of the ~36 (month, season, period) buckets b with net_b = x_b - y_b
  (x_b, y_b >= 0, the standard convex-piecewise-linear-as-LP split):
      sum_b [ energy_rate_b * x_b  -  credit_rate_b * y_b ]
    + NBC * sum_i(imp_i)
  This is the EXACT structure of rates.bill_nem_monthly, not an approximation
  of it -- verified directly by re-billing the LP's own resulting imp/exp
  series through rates.bill_nem and asserting the two agree to within $1.

Day-ahead rolling-horizon variant: each day's local LP is solved against a
persistence forecast, but its bucket-net constraint is offset by the REAL
(already-realized, never forecast) net position accrued earlier that same
month in that bucket -- a day-ahead controller genuinely knows this, since
it is the past. On this house's own data this made no numeric difference to
the committed artifact (every month here already nets solidly positive well
before any single day's own choice could flip a bucket's sign), but the
mechanism is real and tested directly, not assumed correct because it
happened not to move this year's numbers.

Output: perfect_foresight_dispatch.json. This script fully regenerates the
committed artifact.
"""
import json
import os

import numpy as np
import scipy.sparse as sp
from scipy.optimize import linprog

import behavior_rebuild as br
import rates as R
from battery_dispatch_policies import billed

CAP_KWH = 13.5
POWER_KW = 11.5
ETA = np.sqrt(0.90)
NBC = R.NBC


def repo_root():
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = base
        for _ in range(8):
            if os.path.exists(os.path.join(p, "data", "plan_results.csv")):
                return p
            p = os.path.dirname(p)
    raise SystemExit("repo root not found (data/plan_results.csv)")


def _bucket_assignment(d):
    """One bucket per (ym, seas, p) combination actually present in the data
    -- the same grouping rates.bill_nem_monthly iterates over. Returns
    (bucket_idx array of length n, list of (energy_rate, credit_rate) per
    bucket, in bucket-index order)."""
    keys = list(zip(d.ym.astype(str), d.seas, d.p))
    uniq = sorted(set(keys))
    key_to_idx = {k: i for i, k in enumerate(uniq)}
    bucket_idx = np.array([key_to_idx[k] for k in keys], dtype=np.int64)
    rates = [(R.energy(seas, p), R.credit(seas, p)) for (_ym, seas, p) in uniq]
    return bucket_idx, rates


def _solve_lp(house_net, ev_spillover, bucket_idx, bucket_rates, cap, power_kw,
              soc_boundary, bucket_offsets=None):
    """Core LP builder/solver, shared by the annual perfect-foresight run and
    the day-ahead rolling-horizon run. `soc_boundary` is either
    ("cyclic",) -- soc_init is free but must equal the final soc (a steady
    cycle over this slice), or ("fixed", value) -- soc_init is pinned to
    `value` (the real carried-over SOC) and the final soc is left free,
    bounded only by [0, cap] (the day-ahead case: the controller inherits a
    real starting charge and is not required to return to it by day's end).
    `bucket_offsets`, if given, is a per-bucket REAL (already-realized, not
    forecast) net position already accrued this month before this slice --
    a day-ahead controller genuinely knows this (it is the past), so it is
    added into each bucket's net-position constraint rather than starting
    every day's local LP as if the bucket were fresh (adversarial review,
    issue #13: an earlier version reset every bucket to zero each day,
    conflating real forecast error with an avoidable loss of already-known
    information). Defaults to all zeros, which is exactly correct for the
    annual solve, where every bucket genuinely starts the frame at zero.
    Returns (imp, exp, charge, discharge, soc, soc_init, lp_result)."""
    n = len(house_net)
    P = power_kw / 4.0
    n_b = len(bucket_rates)

    CH, DC, IMP, EXP, SOC = 0, n, 2 * n, 3 * n, 4 * n
    SOC_INIT = 5 * n
    X, Y = SOC_INIT + 1, SOC_INIT + 1 + n_b
    n_vars = SOC_INIT + 1 + 2 * n_b

    c = np.zeros(n_vars)
    c[IMP:IMP + n] = NBC
    for b, (er, cr) in enumerate(bucket_rates):
        c[X + b] = er
        c[Y + b] = -cr

    # (1) net-flow: imp_i - exp_i - charge_i + discharge_i = house_net_i
    rows1 = np.repeat(np.arange(n), 4)
    cols1 = np.empty(4 * n, dtype=np.int64)
    cols1[0::4] = IMP + np.arange(n)
    cols1[1::4] = EXP + np.arange(n)
    cols1[2::4] = CH + np.arange(n)
    cols1[3::4] = DC + np.arange(n)
    data1 = np.tile([1.0, -1.0, -1.0, 1.0], n)
    b1 = house_net

    # (2) continuity: soc_i - soc_{i-1} - charge_i*ETA + discharge_i/ETA = 0
    rows2 = np.repeat(np.arange(n), 4)
    cols2 = np.empty(4 * n, dtype=np.int64)
    cols2[0::4] = SOC + np.arange(n)
    prev = np.where(np.arange(n) == 0, SOC_INIT, SOC + np.arange(n) - 1)
    cols2[1::4] = prev
    cols2[2::4] = CH + np.arange(n)
    cols2[3::4] = DC + np.arange(n)
    data2 = np.tile([1.0, -1.0, -ETA, 1.0 / ETA], n)
    b2 = np.zeros(n)

    # (3) SOC boundary: cyclic (soc_init = soc_{n-1}) or fixed (soc_init = value)
    rows3 = np.array([0, 0]) if soc_boundary[0] == "cyclic" else np.array([0])
    if soc_boundary[0] == "cyclic":
        cols3 = np.array([SOC_INIT, SOC + n - 1])
        data3 = np.array([1.0, -1.0])
        b3 = np.array([0.0])
    else:
        cols3 = np.array([SOC_INIT])
        data3 = np.array([1.0])
        b3 = np.array([float(soc_boundary[1])])

    # (4) bucket net split: offset_b + sum(imp_i) - sum(exp_i) - x_b + y_b = 0
    # per bucket (offset_b is the real, already-realized net position accrued
    # earlier this month -- zero for the annual solve, where every bucket
    # genuinely starts the frame empty)
    rows4 = np.concatenate([bucket_idx, bucket_idx, np.arange(n_b), np.arange(n_b)])
    cols4 = np.concatenate([IMP + np.arange(n), EXP + np.arange(n),
                            X + np.arange(n_b), Y + np.arange(n_b)])
    data4 = np.concatenate([np.ones(n), -np.ones(n), -np.ones(n_b), np.ones(n_b)])
    b4 = -np.asarray(bucket_offsets, dtype=float) if bucket_offsets is not None else np.zeros(n_b)

    n_row3 = len(b3)
    A_eq = sp.coo_matrix(
        (np.concatenate([data1, data2, data3, data4]),
         (np.concatenate([rows1, rows2 + n, rows3 + 2 * n,
                          rows4 + 2 * n + n_row3]),
          np.concatenate([cols1, cols2, cols3, cols4]))),
        shape=(2 * n + n_row3 + n_b, n_vars)).tocsr()
    b_eq = np.concatenate([b1, b2, b3, b4])

    bounds = [(0, P)] * n
    bounds += [(0, 0) if ev_spillover[i] else (0, P) for i in range(n)]
    bounds += [(0, None)] * n
    bounds += [(0, None)] * n
    bounds += [(0, cap)] * n
    bounds += [(0, cap)]
    bounds += [(0, None)] * n_b
    bounds += [(0, None)] * n_b

    res = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise SystemExit(f"perfect_foresight_dispatch: LP did not solve: {res.message}")

    x = res.x
    imp = x[IMP:IMP + n]
    exp = x[EXP:EXP + n]
    charge = x[CH:CH + n]
    discharge = x[DC:DC + n]
    soc = x[SOC:SOC + n]
    soc_init = x[SOC_INIT]
    return imp, exp, charge, discharge, soc, soc_init, res


def solve(d, imp0, gen0, cap=CAP_KWH, power_kw=POWER_KW):
    """Whole-year perfect-foresight LP (cyclic SOC boundary). Returns
    (imp, exp, result_dict, charge, discharge, soc, soc_init)."""
    n = len(d)
    house_net = imp0 - gen0
    kw = imp0 * 4.0
    p = d.p.values
    ev_spillover = (kw >= 2.5) & (p != "on")
    bucket_idx, bucket_rates = _bucket_assignment(d)

    imp, exp, charge, discharge, soc, soc_init, res = _solve_lp(
        house_net, ev_spillover, bucket_idx, bucket_rates, cap, power_kw, ("cyclic",))

    return imp, exp, {
        "lp_status": res.message,
        "lp_objective_usd": round(res.fun, 2),
        "n_intervals": n,
        "n_buckets": len(bucket_rates),
        "charge_kwh_total": round(float(charge.sum()), 2),
        "discharge_kwh_total": round(float(discharge.sum()), 2),
        "soc_init_kwh": round(float(soc_init), 4),
        "soc_final_kwh": round(float(soc[-1]), 4),
        "cycles_per_day": round(float(charge.sum()) * ETA / cap / 365, 4),
    }, charge, discharge, soc, soc_init


def rolling_day_ahead(d, imp0, gen0, soc_start, cap=CAP_KWH, power_kw=POWER_KW):
    """Realistic middle case: day-ahead PERSISTENCE forecast (yesterday's
    actual house-net profile, time-of-day aligned, stands in for tomorrow's
    forecast -- the day boundaries are set by calendar date, so DST-length
    days persist from a day of the same length where one exists, else fall
    back to using the day's own actual profile since no same-length
    persistence source exists that day) rather than perfect knowledge of that
    day's own actual data. Each day's PLANNED charge/discharge schedule is
    computed by solving a same-day-scoped LP (SOC fixed at the real
    carried-over starting level, not required to return to it by day's end --
    unlike the annual cyclic run, a single day is a slice of a longer
    sequence, not a closed loop) against the FORECAST, then that exact
    schedule is re-clipped against REAL feasibility (SOC/power bounds) and
    applied to the REAL data to get the day's true realized import/export --
    a walk-forward backtest, not a hypothetical.

    Returns (imp, exp, info_dict). `soc_start` should be the whole-year
    optimizer's own converged soc_init/soc_final (the steady operating level
    for this exact hardware on this house), so the rolling run starts from a
    principled level rather than an arbitrary new assumption."""
    n = len(d)
    P = power_kw / 4.0
    house_net = imp0 - gen0
    kw = imp0 * 4.0
    p = d.p.values
    ev_spillover = (kw >= 2.5) & (p != "on")
    bucket_idx_full, bucket_rates = _bucket_assignment(d)

    dates = d.dt.dt.date.values
    uniq_dates = sorted(set(dates))
    day_idx = [np.where(dates == dt)[0] for dt in uniq_dates]
    n_days = len(day_idx)

    forecast_house_net = house_net.copy()
    persisted = np.zeros(n, dtype=bool)
    n_dst_mismatch_days = 0
    for i in range(n_days):
        idx = day_idx[i]
        prev_idx = day_idx[i - 1]  # i=0 wraps to the LAST day (cyclic persistence source)
        if len(prev_idx) == len(idx):
            forecast_house_net[idx] = house_net[prev_idx]
            persisted[idx] = True
        else:
            # a DST-length mismatch with its persistence source -- no
            # same-length profile to persist from, so this day's forecast
            # falls back to its own actual data (perfect for that one day
            # only; excluded from the error stats below, not silently blended in)
            n_dst_mismatch_days += 1

    err = forecast_house_net[persisted] - house_net[persisted]
    forecast_mae = float(np.abs(err).mean())
    forecast_rmse = float(np.sqrt((err ** 2).mean()))

    real_imp = np.zeros(n)
    real_exp = np.zeros(n)
    real_charge = np.zeros(n)
    real_discharge = np.zeros(n)
    real_soc_trace = np.zeros(n)
    soc_now = float(soc_start)
    # Real (already-realized, never forecast) net position accrued so far in
    # each bucket -- a day-ahead controller genuinely knows this, since it is
    # the past. Keyed by the GLOBAL bucket id, which already encodes the
    # month, so no separate month-boundary reset is needed: a bucket for a
    # later month is a different key, starting at 0.0 the first time it's
    # touched (adversarial review, issue #13: an earlier version reset every
    # bucket to zero EVERY DAY, discarding this known information and
    # conflating genuine forecast error with an avoidable loss of it).
    bucket_cumulative = {}
    for i in range(n_days):
        idx = day_idx[i]
        local_present = sorted(set(bucket_idx_full[idx].tolist()))
        remap = {orig: local for local, orig in enumerate(local_present)}
        local_bucket_idx = np.array([remap[b] for b in bucket_idx_full[idx]])
        local_bucket_rates = [bucket_rates[b] for b in local_present]
        local_offsets = [bucket_cumulative.get(orig, 0.0) for orig in local_present]

        _, _, planned_charge, planned_discharge, _, _, _ = _solve_lp(
            forecast_house_net[idx], ev_spillover[idx], local_bucket_idx,
            local_bucket_rates, cap, power_kw, ("fixed", soc_now),
            bucket_offsets=local_offsets)

        day_real_imp = np.zeros(len(idx))
        day_real_exp = np.zeros(len(idx))
        for k in range(len(idx)):
            d_actual = min(planned_discharge[k], soc_now * ETA, P)
            c_actual = min(planned_charge[k], (cap - soc_now) / ETA, P)
            soc_now = soc_now + c_actual * ETA - d_actual / ETA
            real_net = house_net[idx[k]] - d_actual + c_actual
            day_real_imp[k] = max(0.0, real_net)
            day_real_exp[k] = max(0.0, -real_net)
            real_imp[idx[k]] = day_real_imp[k]
            real_exp[idx[k]] = day_real_exp[k]
            real_charge[idx[k]] = c_actual
            real_discharge[idx[k]] = d_actual
            real_soc_trace[idx[k]] = soc_now

        # carry the REAL (post-battery) net position forward into each
        # touched bucket's running total, using genuinely-realized data --
        # never the forecast, which is only used to plan today's schedule
        day_buckets = bucket_idx_full[idx]
        for orig in local_present:
            mask = day_buckets == orig
            bucket_cumulative[orig] = (bucket_cumulative.get(orig, 0.0)
                                       + float(day_real_imp[mask].sum() - day_real_exp[mask].sum()))

    return real_imp, real_exp, real_charge, real_discharge, real_soc_trace, {
        "forecast_method": ("persistence: yesterday's actual house-net profile "
                            "(Consumption - Generation), time-of-day aligned, "
                            "used as tomorrow's forecast; the first day of the "
                            "year persists from the last day (cyclic)"),
        "forecast_mae_kw_equivalent_kwh_per_interval": round(forecast_mae, 4),
        "forecast_rmse_kwh_per_interval": round(forecast_rmse, 4),
        "n_days": n_days,
        "n_days_with_dst_length_mismatch": n_dst_mismatch_days,
        "soc_start_kwh": round(float(soc_start), 4),
        "soc_end_kwh": round(float(soc_now), 4),
    }


def _check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, cap):
    """Exact algebraic identities, not a heuristic decomposition. An earlier
    version tried to split discharge into "import relief" vs "solar absorbed"
    using per-interval clip(min=0) sums -- that silently assumed discharge only
    ever REDUCES import, never CREATES new export beyond gen0. A true optimum
    legitimately does the latter too (discharging into an interval that is
    already net-exporting, to capture a bucket's credit rate), which the
    heuristic mis-flagged as a conservation failure. The checks below instead
    verify the net-flow equation's own aggregate consequence directly -- true
    by construction if and only if the LP's constraints were built correctly,
    with no assumption about which direction any individual interval moves."""
    eta = ETA
    # aggregate net-flow identity: sum(imp-exp) - sum(house_net) = charge_sum - discharge_sum
    house_net = imp0 - gen0
    lhs = float((imp - exp).sum() - house_net.sum())
    rhs = float(charge.sum() - discharge.sum())
    if abs(lhs - rhs) > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: aggregate net-flow identity failed "
            f"({lhs:.4f} != {rhs:.4f}) -- the LP's own constraints are "
            "inconsistent with its solution")
    # no interval should show BOTH import and export simultaneously by more
    # than a solver rounding tolerance (wasteful, never optimal, and not
    # something the LP's constraints forbid directly -- worth confirming
    # empirically rather than assuming)
    both = np.minimum(imp, exp)
    if both.max() > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: {int((both > 1e-3).sum())} interval(s) "
            f"show simultaneous import AND export (max {both.max():.4f} kWh)")
    # no interval should charge AND discharge simultaneously (pure round-trip
    # loss with no benefit; never optimal, worth confirming empirically)
    both_batt = np.minimum(charge, discharge)
    if both_batt.max() > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: {int((both_batt > 1e-3).sum())} "
            f"interval(s) charge AND discharge simultaneously "
            f"(max {both_batt.max():.4f} kWh)")
    soc_prev = np.concatenate([[soc_init], soc[:-1]])
    expected = soc_prev + charge * eta - discharge / eta
    if not np.allclose(soc, expected, atol=1e-4):
        raise SystemExit("perfect_foresight_dispatch: SOC continuity violated")
    if abs(soc_init - soc[-1]) > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: cyclic boundary violated "
            f"(soc_init={soc_init:.4f}, soc_final={soc[-1]:.4f})")
    if soc.min() < -1e-6 or soc.max() > cap + 1e-6:
        raise SystemExit("perfect_foresight_dispatch: SOC bound violated")


def main():
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    base_bill = billed(d, imp0, gen0)

    imp, exp, info, charge, discharge, soc, soc_init = solve(d, imp0, gen0)
    _check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, CAP_KWH)

    f = d.copy()
    f["I"], f["E"] = imp, exp
    true_bill = R.bill_nem(f, "I", "E")
    save = base_bill - true_bill

    # The LP objective deliberately excludes BSC (a fixed per-day charge,
    # invariant to dispatch): add it back for the AC's own required check
    # (agreement with rates.bill_nem, which DOES include BSC) rather than
    # comparing the two on different bases.
    n_days = f.dt.dt.date.nunique()
    lp_bill_with_bsc = info["lp_objective_usd"] + n_days * R.BSC

    canon = None
    canon_path = os.path.join(repo_root(), "data", "battery_dispatch_policies.json")
    if os.path.exists(canon_path):
        canon = json.load(open(canon_path))

    out = {
        "method": ("linear program minimizing the exact rates.bill_nem_monthly "
                   "objective (36 month/season/period netting buckets + NBC on "
                   "gross imports), solved via scipy.optimize.linprog (HiGHS), "
                   "at identical capacity/power/efficiency/EV-exclusion "
                   "constraints as battery_dispatch_policies.run_batt's greedy "
                   "policy, with a cyclic (steady-state) SOC boundary"),
        "cap_kwh": CAP_KWH,
        "power_kw": POWER_KW,
        "eta": float(ETA),
        "baseline_bill_current_rates": round(base_bill),
        "perfect_foresight_bill": round(true_bill, 2),
        "perfect_foresight_save_usd": round(save, 2),
        "lp_info": info,
        "verification": {
            "lp_objective_usd_excl_bsc": info["lp_objective_usd"],
            "n_days": int(n_days),
            "lp_objective_plus_bsc_usd": round(lp_bill_with_bsc, 2),
            "rebilled_via_rates_bill_nem_usd": round(true_bill, 2),
            "agreement_usd": round(abs(lp_bill_with_bsc - true_bill), 4),
        },
    }
    # AC's own explicit requirement: the LP's objective, re-billed through the
    # canonical engine, must agree to within $1 -- enforced HERE (fail closed
    # on every run), not only in a separate test that could go stale.
    if out["verification"]["agreement_usd"] > 1.0:
        raise SystemExit(
            "perfect_foresight_dispatch: LP objective diverges from "
            f"rates.bill_nem by ${out['verification']['agreement_usd']:.2f}, "
            "more than the required $1 agreement")
    if canon is not None:
        greedy_save = canon["pw3"]["greedy"]["save"]
        gap_usd = save - greedy_save
        out["greedy_comparison"] = {
            "greedy_save_usd": greedy_save,
            "perfect_foresight_save_usd": round(save, 2),
            "optimality_gap_usd": round(gap_usd, 2),
            "optimality_gap_pct_of_greedy": round(100 * gap_usd / greedy_save, 2),
        }

    # -- day-ahead forecast (realistic middle case) --
    da_imp, da_exp, da_charge, da_discharge, da_soc_trace, da_info = rolling_day_ahead(
        d, imp0, gen0, soc_init)
    fd = d.copy()
    fd["I"], fd["E"] = da_imp, da_exp
    da_bill = R.bill_nem(fd, "I", "E")
    da_save = base_bill - da_bill

    # Exact aggregate net-flow identity (same form as _check_conservation's
    # annual-run check): sum(imp-exp) - sum(house_net) = charge_sum - discharge_sum.
    # Day-ahead is NOT a closed cycle (soc_end generally != soc_start, since
    # each day only targets a fixed START, not a round trip), so this is
    # checked directly from the actual charge/discharge trace, not inferred
    # from a soc-drift shortcut.
    house_net = imp0 - gen0
    lhs = float((da_imp - da_exp).sum() - house_net.sum())
    rhs = float(da_charge.sum() - da_discharge.sum())
    da_conservation_ok = abs(lhs - rhs) < 1e-3
    # the accumulated SOC trace must also match the charge/discharge history
    # exactly, and the last traced value must equal the info dict's own
    # reported ending SOC
    soc_prev = np.concatenate([[soc_init], da_soc_trace[:-1]])
    expected_soc = soc_prev + da_charge * ETA - da_discharge / ETA
    soc_trace_ok = np.allclose(da_soc_trace, expected_soc, atol=1e-4)
    soc_end_ok = abs(da_soc_trace[-1] - da_info["soc_end_kwh"]) < 1e-3
    da_conservation_ok = da_conservation_ok and soc_trace_ok and soc_end_ok

    out["day_ahead_forecast"] = {
        **da_info,
        "bill_usd": round(da_bill, 2),
        "save_usd": round(da_save, 2),
        "energy_conservation_check_passed": bool(da_conservation_ok),
    }
    if not da_conservation_ok:
        raise SystemExit(
            "perfect_foresight_dispatch: day-ahead energy conservation check failed "
            f"(net-flow identity {lhs:.4f} vs {rhs:.4f}, soc trace ok={soc_trace_ok}, "
            f"soc end ok={soc_end_ok})")

    out["purchasing_statement"] = {
        "greedy_save_usd": canon["pw3"]["greedy"]["save"] if canon else None,
        "day_ahead_save_usd": round(da_save, 2),
        "perfect_foresight_save_usd": round(save, 2),
        "day_ahead_value_over_greedy_usd": (
            round(da_save - canon["pw3"]["greedy"]["save"], 2) if canon else None),
        "remaining_gap_day_ahead_to_perfect_usd": round(save - da_save, 2),
        "note": ("what a smarter (day-ahead-forecast) controller is worth over "
                 "the shipping greedy policy, and what is still left even after "
                 "that upgrade, at the same 13.5 kWh / 11.5 kW hardware -- "
                 "no shipping product changes the CONTROLLER, only the hardware, "
                 "so this gap is a software/firmware question, not a purchasing one"),
    }

    root = repo_root()
    tmp = os.path.join(root, "data", "perfect_foresight_dispatch.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(root, "data", "perfect_foresight_dispatch.json"))
    print("wrote data/perfect_foresight_dispatch.json")
    print("verification:", out["verification"])
    if canon is not None:
        print("greedy_comparison:", out["greedy_comparison"])


if __name__ == "__main__":
    main()
