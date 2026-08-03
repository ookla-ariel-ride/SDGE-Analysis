#!/usr/bin/env python3
"""Perfect-foresight dispatch bound (issue #13) — how much value does the
greedy dispatch policy leave on the table?

battery_dispatch_policies.py's "greedy" policy is a threshold heuristic: serve
every import priced above the battery's stored-energy cost. Nobody has checked
how close that gets to the best ANY controller could do on this house's own
measured year, at the same hardware (13.5 kWh, 11.5 kW discharge / 5 kW charge --
Tesla's own datasheet, issue #40, see research/battery-research-notes.md -- 90%
round-trip) and the
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
  grid_topup_i      >= 0                      (extra grid import to charge)
  solar_absorbed_i  in [0, gen0_i]             (solar surplus retained to
                                               charge instead of exported)
  discharge_i       in [0, imp0_i]             (serves import; can reduce it
                                               to zero but never manufactures
                                               NEW export beyond gen0_i)
  soc_i             in [0, cap]  (state of charge after interval i)
  soc_init          in [0, cap]  (state of charge before interval 0)
  imp_i := imp0_i - discharge_i + grid_topup_i        )  DERIVED, not free
  exp_i := gen0_i - solar_absorbed_i                  )  variables -- see below

Constraints:
  combined bidirectional power cap, NORMALIZED (issue #40 correction):
              discharge_i / power_kw + (grid_topup_i + solar_absorbed_i) / charge_kw
                  <= 1/4
              A single Powerwall 3 has ONE bidirectional AC inverter with
              shared AC-side bandwidth: it cannot be asked to run a full
              charge duty cycle AND a full discharge duty cycle in the same
              interval, regardless of whether its charge and discharge RATES
              are equal (Codex adversarial review, fourth pass -- see
              TECHNICAL.md's "Do not ship: combined bidirectional power cap"
              for that original fix's own account of why). That prior fix
              assumed one shared power_kw; generalizing it to two
              independently-rated directions (Tesla's own datasheet gives
              genuinely different continuous ratings for a single Powerwall
              3's charge and discharge directions -- 5 kW charge vs. 11.5 kW
              discharge, see research/battery-research-notes.md) means the
              SHARE of each direction's OWN maximum rate a given interval
              consumes must sum to at most one interval's worth of duty
              cycle -- not that the raw kWh sum stays under one shared
              number, which would silently permit simultaneous full-rate
              charge AND full-rate discharge whenever the two rates differ
              (exactly the bug the fourth-pass review fixed, reintroduced by
              a first, WRONG attempt at this generalization that used two
              INDEPENDENT constraints instead -- see the correction note
              near _solve_lp). When charge_kw == power_kw (the symmetric
              default, charge_kw=None) this reduces algebraically to exactly
              discharge_i + grid_topup_i + solar_absorbed_i <= power_kw/4 --
              the ORIGINAL fourth-pass constraint, unchanged, not a looser
              one: verified by regenerating the committed artifact
              byte-identically at the symmetric default. discharge_i's own
              upper bound, min(imp0_i, power_kw/4), separately combines its
              per-source cap with the no-manufactured-export rule above;
              grid_topup_i/solar_absorbed_i are similarly bounded by
              charge_kw/4 per source below -- both are INDIVIDUAL per-flow
              caps, distinct from the combined row above.
  continuity: soc_i = soc_{i-1} + (grid_topup_i+solar_absorbed_i)*ETA
                       - discharge_i/ETA
              (soc_{-1} := soc_init)
  cyclic:     soc_init = soc_{n-1}
              -- a steady annual cycle, not a one-time year-1 boundary
              windfall/deficit (issue #12's Codex review caught exactly this
              class of bug in the heuristic dispatch script; built in here
              from the start rather than fixed after the fact)
  EV exclusion: discharge_i forced to 0 wherever kW_i >= 2.5 outside on-peak
              (the same rule battery_dispatch_policies.run_batt applies)

Deriving imp_i/exp_i from imp0_i/gen0_i plus explicit, capped battery
actions (rather than as free variables tied only to the signed net
imp0_i-gen0_i) is deliberate, matching run_batt's own convention exactly:
an earlier version used free imp_i/exp_i variables, which silently
discarded the ~6.3% of real intervals that carry BOTH gross import and
gross export simultaneously (collapsing them to a signed net erases that
gross import for free, understating the NBC genuinely owed on it with no
battery action required), and let the optimizer manufacture brand-new
export beyond what the house ever generated -- a capability the shipping
greedy policy's own discharge cap never uses, undermining the "same
hardware, same envelope" comparison this whole bound depends on (Codex
adversarial review, third pass). Both are now fixed by construction: an
interval with no battery action reproduces imp0_i/gen0_i exactly, gross
flows included, and discharge/solar-absorption are capped so the battery
can never move imp_i or exp_i outside [0, imp0_i] / [0, gen0_i].

Objective (minimize; BSC is dispatch-invariant, excluded):
  NBC*sum(imp0_i) [a constant, added back after solving]
  + sum_i [ NBC*grid_topup_i - NBC*discharge_i ]
  + for each of the ~36-39 (month, season, period) buckets b with
    net_b = x_b - y_b (x_b, y_b >= 0, the standard convex-piecewise-
    linear-as-LP split):
      sum_b [ energy_rate_b * x_b  -  credit_rate_b * y_b ]
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

The EV-spillover exclusion mask used to PLAN each day is also forecast (a
persistence guess from yesterday's real mask, time-of-day aligned), not the
real future mask -- an earlier version handed the planning LP the real
future EV mask directly, giving it perfect advance knowledge of exactly
which intervals would be off-limits, contradicting the "day-ahead forecast
quality, not perfect knowledge" premise the whole variant exists to test
(Codex adversarial review, third pass; roughly half of this house's true
EV-spillover intervals cannot be reliably anticipated from a persistence
guess alone). The REAL mask is still enforced as a hard rule at EXECUTION
time regardless of what the plan called for, the same hard-feasibility
spirit as the SOC/power re-clip.

Isolating forecast error from the myopic single-day horizon: each day's
local LP is solved independently, with SOC fixed at the real start-of-day
level but left FREE at day's end -- unlike the annual solve's cyclic
boundary, no value is placed on SOC held past midnight. This means the
day-ahead run's shortfall versus the true annual optimum has two possible
causes -- imperfect (persistence) forecasting, and the myopic horizon
itself -- and an earlier version of this report attributed the ENTIRE
shortfall to forecast pre-commitment without ruling out the horizon
confound first (Codex adversarial review, fourth pass). `rolling_day_ahead`
therefore also runs with `perfect=True` (plans against REAL same-day data
instead of a persistence forecast, everything else about the day-by-day
architecture unchanged), isolating the horizon effect on its own: on this
house's data it is $8.35/yr, versus $826.61/yr from forecast error --
the horizon confound turned out real to check but small in practice.

Day 0 and any DST-length-mismatched days have no genuine prior day to
persist from within a single year of data, so their "forecast" falls back
to their own real same-day values -- not an out-of-sample forecast. This
is disclosed as a leak (`n_days_with_leaked_future_information`, 5 of 365
days here) rather than silently counted as ordinary persistence, and its
dollar impact is bounded directly (`leak_sensitivity_usd`) by re-billing
with those specific days' dispatch replaced by no battery action at all.

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
from battery_dispatch_policies import billed, CHARGE_KW

CAP_KWH = 13.5
POWER_KW = 11.5   # continuous DISCHARGE, Tesla's own datasheet
# CHARGE_KW (5 kW continuous charge, issue #40) imported from battery_
# dispatch_policies.py, not re-declared, so this script cannot drift onto a
# different figure than the canonical dispatch engine.
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


def _solve_lp(imp0, gen0, ev_spillover, bucket_idx, bucket_rates, cap, power_kw,
              soc_boundary, bucket_offsets=None, charge_kw=None):
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
    every day's local LP as if the bucket were fresh. Defaults to all zeros,
    exactly correct for the annual solve, where every bucket genuinely
    starts the frame at zero.

    Decision variables are `grid_topup` (extra grid import to charge, >=0),
    `solar_absorbed` (solar surplus retained to charge instead of exported,
    in [0, gen0_i]), and `discharge` (in [0, imp0_i] -- capped at the
    interval's OWN gross import, so the battery can reduce import to zero
    but can never manufacture NEW export beyond the baseline gen0_i).
    `imp`/`exp` are DERIVED, not free variables: imp_i = imp0_i - discharge_i
    + grid_topup_i, exp_i = gen0_i - solar_absorbed_i. This is deliberate,
    matching battery_dispatch_policies.run_batt's own convention exactly
    (Codex adversarial review, third pass): an earlier version instead used
    free imp_i/exp_i variables tied only to the SIGNED net (imp0_i-gen0_i),
    which (a) silently discarded the ~6.3% of real intervals that carry
    BOTH gross import and gross export simultaneously -- collapsing them to
    a signed net erases that gross import for free, understating the NBC
    genuinely owed on it, with no battery action required to "earn" that
    saving -- and (b) let the optimizer manufacture brand-new export beyond
    what the house ever actually generated, a capability the shipping
    greedy policy's own discharge cap (`min(imp[i], soc*ETA, pwrq)`) never
    uses, undermining the "same hardware, same envelope" comparison this
    whole bound depends on. Both are fixed by construction here, not by a
    runtime check: discharge_i's own upper bound is `min(imp0_i, power_kw/4)`
    and solar_absorbed_i's is `min(gen0_i, charge_kw/4)`, so imp_i and exp_i
    can never move outside [0, imp0_i] / [0, gen0_i] respectively, and an
    interval with no battery action at all reproduces imp0_i/gen0_i exactly,
    gross flows included.

    charge_kw (issue #40) is the CHARGE-direction power rating (grid_topup_i
    + solar_absorbed_i bounded per-source by charge_kw/4), separate from
    power_kw's DISCHARGE rating (discharge_i bounded by power_kw/4);
    defaults to None, which reuses power_kw for both directions -- byte-for-
    byte the prior symmetric behavior. Tesla's own datasheet gives these as
    genuinely different figures for a single Powerwall 3 unit with no
    expansions (see research/battery-research-notes.md and battery_dispatch_
    policies.py's module docstring for the citation). The two directions
    also share ONE combined, NORMALIZED power-cap row (see the module
    docstring's "combined bidirectional power cap" section) -- NOT two
    independent per-direction rows: a first attempt at this generalization
    used two independent rows and silently reintroduced the exact bug the
    combined constraint was built to prevent (simultaneous full-rate charge
    AND full-rate discharge through what is physically one inverter);
    corrected here to the normalized form, which reduces to the original,
    unmodified fourth-pass constraint whenever charge_kw == power_kw.
    Returns (imp, exp, charge, discharge, soc, soc_init, lp_result)."""
    n = len(imp0)
    P_dis = power_kw / 4.0
    P_chg = (power_kw if charge_kw is None else charge_kw) / 4.0
    n_b = len(bucket_rates)
    house_net = imp0 - gen0

    GT, SA, DC, SOC = 0, n, 2 * n, 3 * n
    SOC_INIT = 4 * n
    X, Y = SOC_INIT + 1, SOC_INIT + 1 + n_b
    n_vars = SOC_INIT + 1 + 2 * n_b

    # NBC applies to the DERIVED imp_i = imp0_i - discharge_i + grid_topup_i.
    # The imp0_i term is a constant (added back to the objective after
    # solving, since linprog's c@x has no constant term); grid_topup_i and
    # -discharge_i are the decision-variable coefficients.
    imp0_nbc_constant = float(NBC * imp0.sum())
    c = np.zeros(n_vars)
    c[GT:GT + n] = NBC
    c[DC:DC + n] = -NBC
    for b, (er, cr) in enumerate(bucket_rates):
        c[X + b] = er
        c[Y + b] = -cr

    # (1) continuity: soc_i - soc_{i-1} - (grid_topup_i+solar_absorbed_i)*ETA
    #                 + discharge_i/ETA = 0
    rows1 = np.repeat(np.arange(n), 4)
    cols1 = np.empty(4 * n, dtype=np.int64)
    cols1[0::4] = SOC + np.arange(n)
    prev = np.where(np.arange(n) == 0, SOC_INIT, SOC + np.arange(n) - 1)
    cols1[1::4] = prev
    cols1[2::4] = GT + np.arange(n)  # grid_topup contributes with SA below
    cols1[3::4] = DC + np.arange(n)
    data1 = np.tile([1.0, -1.0, -ETA, 1.0 / ETA], n)
    b1 = np.zeros(n)
    # solar_absorbed's own contribution to continuity (same -ETA coefficient
    # as grid_topup, added as a second block reusing the same n rows)
    rows1b = np.arange(n)
    cols1b = SA + np.arange(n)
    data1b = np.full(n, -ETA)

    # (2) SOC boundary: cyclic (soc_init = soc_{n-1}) or fixed (soc_init = value)
    rows2 = np.array([0, 0]) if soc_boundary[0] == "cyclic" else np.array([0])
    if soc_boundary[0] == "cyclic":
        cols2 = np.array([SOC_INIT, SOC + n - 1])
        data2 = np.array([1.0, -1.0])
        b2 = np.array([0.0])
    else:
        cols2 = np.array([SOC_INIT])
        data2 = np.array([1.0])
        b2 = np.array([float(soc_boundary[1])])

    # (3) bucket net split: house_net_baseline_b - sum(discharge_i)
    #     + sum(grid_topup_i) + sum(solar_absorbed_i) - x_b + y_b = -offset_b
    # (house_net_baseline_b is a CONSTANT per bucket, precomputed from the
    # real imp0/gen0 -- the gross decomposition this whole fix preserves)
    house_net_baseline = np.zeros(n_b)
    np.add.at(house_net_baseline, bucket_idx, house_net)
    offsets = np.asarray(bucket_offsets, dtype=float) if bucket_offsets is not None else np.zeros(n_b)
    rows3 = np.concatenate([bucket_idx, bucket_idx, bucket_idx, np.arange(n_b), np.arange(n_b)])
    cols3 = np.concatenate([DC + np.arange(n), GT + np.arange(n), SA + np.arange(n),
                            X + np.arange(n_b), Y + np.arange(n_b)])
    data3 = np.concatenate([-np.ones(n), np.ones(n), np.ones(n), -np.ones(n_b), np.ones(n_b)])
    b3 = -offsets - house_net_baseline

    n_row2 = len(b2)
    A_eq = sp.coo_matrix(
        (np.concatenate([data1, data1b, data2, data3]),
         (np.concatenate([rows1, rows1b, rows2 + n, rows3 + n + n_row2]),
          np.concatenate([cols1, cols1b, cols2, cols3]))),
        shape=(n + n_row2 + n_b, n_vars)).tocsr()
    b_eq = np.concatenate([b1, b2, b3])

    # (4) Combined bidirectional power cap, NORMALIZED (issue #40
    # correction -- see the module docstring's full account and the
    # correction note below). A single Powerwall 3 has ONE bidirectional AC
    # inverter with shared AC-side bandwidth: charging and discharging in
    # the SAME interval draw on that same shared hardware, so the two
    # directions cannot each independently run a full duty cycle at once
    # regardless of whether their RATES differ. Expressed as the share of
    # each direction's OWN maximum rate a given interval consumes, summed:
    #     discharge_i / power_kw + (grid_topup_i + solar_absorbed_i) / charge_kw
    #         <= 1/4
    # (both sides in hours: dividing an interval's kWh by a kW rating gives
    # the fraction of an hour spent at that rating; a 15-minute interval has
    # 1/4 hour of combined duty cycle to spend, split between the two
    # directions in proportion to each one's own maximum rate.) When
    # charge_kw == power_kw (charge_kw=None, the symmetric default) this
    # reduces algebraically to exactly
    #     discharge_i + grid_topup_i + solar_absorbed_i <= power_kw/4
    # -- the ORIGINAL fourth-pass constraint, unchanged, not a looser one.
    #
    # CORRECTION: a first attempt at generalizing this constraint to
    # asymmetric rates split it into two INDEPENDENT rows (discharge_i <=
    # power_kw/4 and grid_topup_i+solar_absorbed_i <= charge_kw/4 as
    # separate inequalities). That silently reintroduced the exact bug the
    # fourth-pass review fixed: at asymmetric rates it permits an interval
    # to charge at its full charge_kw rate AND discharge at its full
    # power_kw rate simultaneously, demanding the shared inverter deliver
    # both directions' full combined throughput at once -- physically
    # impossible for one bidirectional inverter, and NOT what the fourth-
    # pass fix's own "one combined budget" reasoning ever allowed even
    # before the datasheet split existed. Confirmed as a real regression:
    # the independent-rows version failed to reproduce the committed
    # artifact byte-identically even at charge_kw=None (day-ahead save
    # reverted from the documented $1,711.28/yr back to the pre-fourth-pass
    # $1,711.13/yr) -- exactly the bug that fix diagnosed, un-fixed. The
    # normalized single-row form above is the correct generalization: it
    # reduces to the untouched original constraint at symmetric rates
    # (verified: byte-identical artifact regeneration) and correctly caps
    # the COMBINED duty cycle, not two independent budgets, at asymmetric
    # ones. The REGRESSION GUARDS are test_perfect_foresight_dispatch.py's
    # case_solve_lp_combined_power_cap_row_is_normalized_not_two_independent_rows
    # and case_solve_lp_symmetric_normalized_row_is_bit_identical_to_the_
    # fourth_pass_constraint (both inspect the actual A_ub matrix handed to
    # the solver and correctly fail when the bug is reinstated).
    # case_solve_lp_does_not_choose_simultaneous_full_rate_charge_and_
    # discharge_at_symmetric_power is a smoke check only -- an independent
    # code-reviewer agent (PR #69) found it still passes against the
    # reinstated bug on its own scenario, since that scenario's own optimum
    # avoids the failure mode regardless of whether the cap is enforced.
    # Scaled by power_kw (equivalent to the 1/4-hour form above, multiplied
    # through by power_kw on both sides) rather than left in raw hours, so
    # that at the symmetric default the discharge coefficient is the
    # LITERAL float 1.0 (power_kw/power_kw, exact under IEEE754 for any
    # finite nonzero power_kw -- not a new division that merely evaluates
    # to something close to 1.0) and the RHS is the SAME P_dis expression
    # already computed above, not a freshly-rounded 0.25 -- so the LP row
    # handed to the solver is BIT-IDENTICAL to the untouched pre-issue-40
    # combined constraint whenever charge_kw is None, not just
    # mathematically equivalent to it. This matters because the day-ahead
    # rolling case solves many small, likely-degenerate per-day LPs: two
    # constraint matrices that are mathematically equivalent but numerically
    # DIFFERENT (e.g. raw hours vs. this power-scaled form) can lead a
    # simplex solver to a different tied-optimal vertex with the SAME
    # objective value but a DIFFERENT realized charge/discharge split --
    # confirmed empirically while building this fix (the raw-hours form
    # reproduced the annual figure exactly but drifted the day-ahead save
    # by tens of cents even at charge_kw=None). Giving the solver the
    # identical row, not merely an equivalent one, removes that risk
    # entirely rather than relying on the solver happening to break ties
    # the same way.
    charge_ratio = power_kw / (power_kw if charge_kw is None else charge_kw)
    rows4 = np.repeat(np.arange(n), 3)
    cols4 = np.empty(3 * n, dtype=np.int64)
    cols4[0::3] = GT + np.arange(n)
    cols4[1::3] = SA + np.arange(n)
    cols4[2::3] = DC + np.arange(n)
    data4 = np.tile([charge_ratio, charge_ratio, 1.0], n)
    A_ub = sp.coo_matrix((data4, (rows4, cols4)), shape=(n, n_vars)).tocsr()
    b_ub = np.full(n, P_dis)

    bounds = [(0, P_chg)] * n                                          # grid_topup
    bounds += [(0, min(float(gen0[i]), P_chg)) for i in range(n)]      # solar_absorbed
    bounds += [(0, 0) if ev_spillover[i] else (0, min(float(imp0[i]), P_dis))
              for i in range(n)]                                       # discharge
    bounds += [(0, cap)] * n                                           # soc
    bounds += [(0, cap)]                                                # soc_init
    bounds += [(0, None)] * n_b                                        # x
    bounds += [(0, None)] * n_b                                        # y

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise SystemExit(f"perfect_foresight_dispatch: LP did not solve: {res.message}")
    res.fun = res.fun + imp0_nbc_constant  # restore the constant NBC-on-imp0 term

    x = res.x
    grid_topup = x[GT:GT + n]
    solar_absorbed = x[SA:SA + n]
    discharge = x[DC:DC + n]
    soc = x[SOC:SOC + n]
    soc_init = x[SOC_INIT]
    imp = imp0 - discharge + grid_topup
    exp = gen0 - solar_absorbed
    charge = grid_topup + solar_absorbed
    return imp, exp, charge, discharge, soc, soc_init, res


def solve(d, imp0, gen0, cap=CAP_KWH, power_kw=POWER_KW, charge_kw=None):
    """Whole-year perfect-foresight LP (cyclic SOC boundary). Returns
    (imp, exp, result_dict, charge, discharge, soc, soc_init).

    charge_kw (issue #40) is threaded straight through to _solve_lp's own
    charge-direction parameter; default None preserves the prior symmetric
    (power_kw both ways) behavior exactly."""
    n = len(d)
    kw = imp0 * 4.0
    p = d.p.values
    ev_spillover = (kw >= 2.5) & (p != "on")
    bucket_idx, bucket_rates = _bucket_assignment(d)

    imp, exp, charge, discharge, soc, soc_init, res = _solve_lp(
        imp0, gen0, ev_spillover, bucket_idx, bucket_rates, cap, power_kw, ("cyclic",),
        charge_kw=charge_kw)

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


def rolling_day_ahead(d, imp0, gen0, soc_start, cap=CAP_KWH, power_kw=POWER_KW,
                       perfect=False, charge_kw=None):
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

    `perfect=True` swaps the persistence forecast for the REAL same-day
    imp0/gen0/EV-mask, holding every other piece of the architecture fixed
    (day-by-day solve, SOC fixed at the real start-of-day level and left
    free at day's end, real bucket offsets carried forward). This isolates
    the "myopic single-day horizon" effect (each day's LP places NO value on
    SOC held past midnight, so it has no incentive to preserve charge for
    tomorrow morning) from forecast quality: comparing this run against the
    persistence run (perfect=False) attributes their gap to forecast error
    alone, and comparing THIS run against the full annual cyclic optimum
    attributes the remaining gap to the horizon effect alone (Codex
    adversarial review, fourth pass -- an earlier version's report language
    attributed the day-ahead persistence run's ENTIRE shortfall vs greedy to
    forecast pre-commitment, without isolating this confound first).

    Returns (imp, exp, charge, discharge, soc_trace, info_dict). `soc_start`
    should be the whole-year optimizer's own converged soc_init/soc_final
    (the steady operating level for this exact hardware on this house), so
    the rolling run starts from a principled level rather than an arbitrary
    new assumption.

    charge_kw (issue #40) is the CHARGE-direction power cap, threaded through
    to each day's planning LP (_solve_lp) and to the execution-time re-clip
    below; defaults to None, which reuses power_kw for both directions --
    byte-for-byte the prior symmetric behavior."""
    n = len(d)
    P_dis = power_kw / 4.0
    P_chg = (power_kw if charge_kw is None else charge_kw) / 4.0
    house_net = imp0 - gen0  # only for the forecast-error stats below
    p = d.p.values
    # The REAL EV-exclusion mask -- known only in hindsight. A day-ahead
    # controller does not get to see this in advance any more than it sees
    # the real house_net; using it directly to plan (as an earlier version
    # did) hands the LP perfect future knowledge of exactly which intervals
    # are off-limits, undercutting the "day-ahead forecast quality rather
    # than perfect knowledge" premise (Codex adversarial review, third
    # pass). It IS still enforced as a hard rule at EXECUTION time below --
    # a real controller would never actually discharge into real EV
    # spillover even if its plan (based on an imperfect forecast) called for
    # it, exactly as the SOC/power re-clip already enforces real feasibility.
    real_ev_spillover = (imp0 * 4.0 >= 2.5) & (p != "on")
    bucket_idx_full, bucket_rates = _bucket_assignment(d)

    dates = d.dt.dt.date.values
    uniq_dates = sorted(set(dates))
    day_idx = [np.where(dates == dt)[0] for dt in uniq_dates]
    n_days = len(day_idx)

    # Forecast imp0/gen0 SEPARATELY (not just their signed difference) -- the
    # same gross-flow-preserving fix as the annual solve applies here too:
    # a day-ahead controller's forecast is of the two real, separate meters,
    # not of a pre-netted quantity, and _solve_lp needs the separate series
    # to preserve simultaneous-flow intervals and cap discharge at the
    # (forecast) gross import correctly.
    forecast_imp0 = imp0.copy()
    forecast_gen0 = gen0.copy()
    # The EV-exclusion mask used for PLANNING is forecast the same way as the
    # load itself (yesterday's actual mask, time-of-day aligned) -- not the
    # real future mask. This is what makes the day-ahead case genuinely
    # day-ahead: it does not know in advance which future intervals will see
    # real EV spillover, only what happened at the same time yesterday.
    forecast_ev_spillover = real_ev_spillover.copy()
    persisted = np.zeros(n, dtype=bool)
    leaked = np.zeros(n, dtype=bool)
    n_dst_mismatch_days = 0
    if perfect:
        # perfect=True: no persistence forecast at all -- see the docstring.
        # forecast_imp0/gen0/ev_spillover already equal the real arrays
        # (copied above), so nothing else in this block runs.
        pass
    else:
        for i in range(n_days):
            idx = day_idx[i]
            prev_idx = day_idx[i - 1]  # i=0 wraps to the LAST day of the SAME year
            if len(prev_idx) == len(idx):
                forecast_imp0[idx] = imp0[prev_idx]
                forecast_gen0[idx] = gen0[prev_idx]
                forecast_ev_spillover[idx] = real_ev_spillover[prev_idx]
                if i == 0:
                    # Day 0's "prior day" is day 364 of this SAME year -- real
                    # data that, in a genuine walk-forward deployment, would
                    # not exist yet on day 0 (it is the far future relative to
                    # day 0's true chronological position, not a real
                    # yesterday). This is a modeling convenience (a single
                    # year has no real prior year to draw from), not a
                    # genuine out-of-sample persistence source, so it is
                    # excluded from BOTH the forecast-error stats below AND
                    # counted separately in the disclosed leak total (Codex
                    # adversarial review, fourth pass: an earlier version
                    # counted this day as ordinary "persisted", silently
                    # including one day of unusually accurate -- because
                    # same-year, same-season -- forecast in both the error
                    # stats and the billed result without disclosure).
                    leaked[idx] = True
                else:
                    persisted[idx] = True
            else:
                # a DST-length mismatch with its persistence source -- no
                # same-length profile to persist from, so this day's forecast
                # falls back to its own actual data (perfect for that one day
                # only; excluded from the error stats below AND counted in
                # the disclosed leak total, not silently blended into either)
                n_dst_mismatch_days += 1
                leaked[idx] = True

    forecast_house_net = forecast_imp0 - forecast_gen0
    err = forecast_house_net[persisted] - house_net[persisted]
    forecast_mae = float(np.abs(err).mean()) if persisted.any() else 0.0
    forecast_rmse = float(np.sqrt((err ** 2).mean())) if persisted.any() else 0.0
    # n_leaked_days is only a meaningful "accidental leak" concept for the
    # persistence forecast; the perfect=True run is INTENTIONALLY full
    # information every day, so it reports 0 here (the "perfect" flag above
    # already communicates that plainly).
    n_leaked_days = 0 if perfect else int(leaked[[idx[0] for idx in day_idx]].sum())

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
            forecast_imp0[idx], forecast_gen0[idx], forecast_ev_spillover[idx],
            local_bucket_idx, local_bucket_rates, cap, power_kw, ("fixed", soc_now),
            bucket_offsets=local_offsets, charge_kw=charge_kw)

        day_real_imp = np.zeros(len(idx))
        day_real_exp = np.zeros(len(idx))
        for k in range(len(idx)):
            gi = idx[k]
            # the REAL EV-exclusion rule is still enforced at execution,
            # regardless of what the forecast-based plan called for -- the
            # same hard-feasibility spirit as the SOC/power clip just below.
            # discharge is ALSO capped at the REAL gross import (never the
            # forecast's), so execution can never manufacture new export
            # beyond the real gen0 even if the plan (built on an imperfect
            # forecast) implied it could (Codex adversarial review, third
            # pass -- the same gross-flow-preserving fix as the LP itself:
            # imp/exp are derived from the real imp0/gen0 plus explicit,
            # correctly-capped battery actions, never from a collapsed
            # signed net, so a no-action interval reproduces imp0/gen0
            # exactly, simultaneous flows included).
            # d_actual/c_actual only ever clip DOWN from the plan (never up),
            # so the executed pair automatically inherits the planning LP's
            # combined, NORMALIZED duty-cycle guarantee (discharge/power_kw +
            # charge/charge_kw <= 1/4, see _solve_lp's docstring) without a
            # separate combined check here: each term in that sum can only
            # shrink when its own numerator shrinks, so their sum can only
            # shrink too -- verified directly in _rolling_conservation_check
            # rather than assumed.
            planned_d = 0.0 if real_ev_spillover[gi] else planned_discharge[k]
            d_actual = min(planned_d, imp0[gi], soc_now * ETA, P_dis)
            c_actual = min(planned_charge[k], (cap - soc_now) / ETA, P_chg)
            solar_used = min(c_actual, gen0[gi])   # solar-first priority
            grid_extra = c_actual - solar_used
            soc_now = soc_now + c_actual * ETA - d_actual / ETA
            day_real_imp[k] = imp0[gi] - d_actual + grid_extra
            day_real_exp[k] = gen0[gi] - solar_used
            real_imp[gi] = day_real_imp[k]
            real_exp[gi] = day_real_exp[k]
            real_charge[gi] = c_actual
            real_discharge[gi] = d_actual
            real_soc_trace[gi] = soc_now

        # carry the REAL (post-battery) net position forward into each
        # touched bucket's running total, using genuinely-realized data --
        # never the forecast, which is only used to plan today's schedule
        day_buckets = bucket_idx_full[idx]
        for orig in local_present:
            mask = day_buckets == orig
            bucket_cumulative[orig] = (bucket_cumulative.get(orig, 0.0)
                                       + float(day_real_imp[mask].sum() - day_real_exp[mask].sum()))

    return real_imp, real_exp, real_charge, real_discharge, real_soc_trace, leaked, {
        "perfect": perfect,
        "forecast_method": (
            "none -- perfect same-day information, single-day horizon "
            "(isolates the myopic-horizon effect from forecast quality)"
            if perfect else
            "persistence: yesterday's actual house-net profile "
            "(Consumption - Generation), time-of-day aligned, "
            "used as tomorrow's forecast; day 0 (no real prior day exists "
            "within a single year) and DST-length-mismatched days fall back "
            "to their own actual data and are disclosed separately as "
            "leaked, not counted as genuine persistence"),
        "forecast_mae_kw_equivalent_kwh_per_interval": round(forecast_mae, 4),
        "forecast_rmse_kwh_per_interval": round(forecast_rmse, 4),
        "n_days": n_days,
        "n_days_with_dst_length_mismatch": n_dst_mismatch_days,
        "n_days_with_leaked_future_information": n_leaked_days,
        "soc_start_kwh": round(float(soc_start), 4),
        "soc_end_kwh": round(float(soc_now), 4),
    }


def _check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, cap,
                         power_kw=POWER_KW, charge_kw=None):
    """Exact algebraic identities, not a heuristic decomposition, checked
    against the gross-flow-preserving formulation (Codex adversarial review,
    third pass): imp/exp are DERIVED from imp0/gen0 plus explicit, capped
    battery actions (never a collapsed signed net), so simultaneous gross
    import AND export in the SAME interval is now EXPECTED and CORRECT
    whenever the real data itself has it (~6.3% of intervals, per
    battery_dispatch_policies.py's own count) -- an earlier version's "no
    simultaneous import/export" check would have fired on exactly the
    intervals this fix exists to preserve, so it is replaced below with
    checks against the actual invariants the new formulation guarantees:
    exp never exceeds gen0 (no manufactured export) and discharge never
    exceeds imp0 (never reduces import below zero on its own)."""
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
    # export must never exceed the real gross generation -- the battery may
    # only ever absorb solar that would have been exported, never manufacture
    # NEW export beyond what the house actually generated
    over_export = exp - gen0
    if over_export.max() > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: {int((over_export > 1e-3).sum())} "
            f"interval(s) export more than gen0 (max excess "
            f"{over_export.max():.4f} kWh) -- the battery manufactured export")
    # discharge must never exceed the real gross import -- the battery may
    # only ever reduce import down to zero, never flip an interval to export
    over_discharge = discharge - imp0
    if over_discharge.max() > 1e-3:
        raise SystemExit(
            f"perfect_foresight_dispatch: {int((over_discharge > 1e-3).sum())} "
            f"interval(s) discharge more than imp0 (max excess "
            f"{over_discharge.max():.4f} kWh) -- discharge exceeded the "
            "gross import it is capped at")
    # Charging AND discharging in the SAME interval is only wasteful (pure
    # round-trip loss, no benefit) when that interval has just ONE real flow
    # to work with -- if imp0 and gen0 are BOTH genuinely nonzero at once (a
    # real simultaneous-flow interval, ~6.3% of them per battery_dispatch_
    # policies.py's own count), discharging to serve the real import while
    # separately storing the real solar surplus are two independent,
    # individually rational actions, not one wasted round trip. Confirmed
    # directly: every interval with both nonzero on the real data has real
    # simultaneous imp0>0 and gen0>0 to justify it (verified while building
    # this check; an earlier, stricter version of this check was itself
    # wrong -- it assumed "both nonzero" always meant waste, true only in
    # the retired collapsed-net formulation, not in this gross-preserving one).
    both_batt = np.minimum(charge, discharge)
    single_flow = ~((imp0 > 1e-6) & (gen0 > 1e-6))
    wasteful = (both_batt > 1e-3) & single_flow
    if wasteful.any():
        raise SystemExit(
            f"perfect_foresight_dispatch: {int(wasteful.sum())} interval(s) "
            f"charge AND discharge simultaneously with only ONE real flow "
            f"present (max {both_batt[wasteful].max():.4f} kWh) -- pure "
            "round-trip loss with no independent justification")
    # The battery has ONE physical bidirectional inverter: charging and
    # discharging share that same hardware, so their COMBINED duty cycle in
    # a single interval must never exceed the interval's own 1/4 hour --
    # charging at full charge-rate while simultaneously discharging at full
    # discharge-rate would demand the shared inverter deliver both
    # directions' full combined throughput at once (Codex adversarial
    # review, fourth pass; generalized to asymmetric charge/discharge rates
    # for issue #40 -- see _solve_lp's docstring for the normalized formula
    # and the CORRECTION note explaining why this must be ONE combined,
    # normalized check and not two independent per-direction caps). This is
    # distinct from the "wasteful simultaneous action" check above, which is
    # about economic rationality; this one is a hard physical limit that
    # applies even when both actions are independently justified by real
    # simultaneous flows.
    charge_kw_eff = power_kw if charge_kw is None else charge_kw
    combined_duty = discharge / power_kw + charge / charge_kw_eff
    over_combined = combined_duty - 0.25
    if over_combined.max() > 1e-6:
        raise SystemExit(
            f"perfect_foresight_dispatch: {int((over_combined > 1e-6).sum())} "
            f"interval(s) combine charge+discharge above the battery's "
            f"combined duty cycle (discharge/{power_kw}kW + charge/"
            f"{charge_kw_eff}kW must stay <= 1/4; max excess "
            f"{over_combined.max():.6f}) -- physically impossible "
            "throughput through a single bidirectional inverter")
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


def _rolling_conservation_check(imp0, gen0, imp, exp, charge, discharge, soc_trace,
                                soc_init, soc_end_reported, power_kw, charge_kw=None):
    """Shared conservation check for both rolling_day_ahead variants
    (persistence forecast and perfect-same-day-information): the exact
    aggregate net-flow identity (same form as _check_conservation's annual
    check), the SOC trace's internal consistency, and the same ONE combined,
    NORMALIZED charge/discharge duty-cycle cap _check_conservation enforces
    (issue #40 generalized the prior single combined budget to asymmetric
    rates via a normalized per-direction-share formula, NOT two independent
    per-direction caps -- see _solve_lp's docstring for the formula and the
    CORRECTION note explaining why two independent caps is the wrong,
    previously-shipped-then-reverted generalization). A rolling run is NOT a
    closed cycle (soc_end generally != soc_start, since each day only
    targets a fixed START, not a round trip), so this is checked directly
    from the actual charge/discharge trace, not inferred from a soc-drift
    shortcut. Returns (ok: bool, diagnostic_str: str)."""
    house_net = imp0 - gen0
    lhs = float((imp - exp).sum() - house_net.sum())
    rhs = float(charge.sum() - discharge.sum())
    identity_ok = abs(lhs - rhs) < 1e-3
    soc_prev = np.concatenate([[soc_init], soc_trace[:-1]])
    expected_soc = soc_prev + charge * ETA - discharge / ETA
    soc_trace_ok = np.allclose(soc_trace, expected_soc, atol=1e-4)
    soc_end_ok = abs(soc_trace[-1] - soc_end_reported) < 1e-3
    # ONE combined, normalized physical limit (not two independent ones):
    # execution can only clip DOWN from the planned charge/discharge (each
    # min()'d against real SOC/power feasibility independently), and
    # clipping either quantity DOWN can only decrease its own term in the
    # normalized sum below, so the executed trace inherits the planning
    # LP's own combined guarantee -- but this is checked directly rather
    # than assumed.
    charge_kw_eff = power_kw if charge_kw is None else charge_kw
    combined_duty = discharge / power_kw + charge / charge_kw_eff
    power_ok = combined_duty.max() <= 0.25 + 1e-6
    ok = identity_ok and soc_trace_ok and soc_end_ok and power_ok
    diag = (f"net-flow identity {lhs:.4f} vs {rhs:.4f} (ok={identity_ok}), "
            f"soc trace ok={soc_trace_ok}, soc end ok={soc_end_ok}, "
            f"combined charge/discharge duty-cycle cap ok={power_ok}")
    return ok, diag


def main():
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    base_bill = billed(d, imp0, gen0)

    imp, exp, info, charge, discharge, soc, soc_init = solve(d, imp0, gen0, charge_kw=CHARGE_KW)
    _check_conservation(imp0, gen0, imp, exp, charge, discharge, soc, soc_init, CAP_KWH,
                        charge_kw=CHARGE_KW)

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
        "charge_kw": CHARGE_KW,
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
    da_imp, da_exp, da_charge, da_discharge, da_soc_trace, da_leaked, da_info = rolling_day_ahead(
        d, imp0, gen0, soc_init, charge_kw=CHARGE_KW)
    fd = d.copy()
    fd["I"], fd["E"] = da_imp, da_exp
    da_bill = R.bill_nem(fd, "I", "E")
    da_save = base_bill - da_bill

    da_ok, da_diag = _rolling_conservation_check(
        imp0, gen0, da_imp, da_exp, da_charge, da_discharge, da_soc_trace, soc_init,
        da_info["soc_end_kwh"], POWER_KW, charge_kw=CHARGE_KW)
    out["day_ahead_forecast"] = {
        **da_info,
        "bill_usd": round(da_bill, 2),
        "save_usd": round(da_save, 2),
        "energy_conservation_check_passed": bool(da_ok),
    }
    if not da_ok:
        raise SystemExit(
            "perfect_foresight_dispatch: day-ahead energy conservation check failed "
            f"({da_diag})")

    # Sensitivity of the persistence day-ahead save to the days whose
    # "forecast" leaked real future information (day 0 -- no genuine prior
    # day exists within a single year -- and any DST-length-mismatched
    # days): re-bill with those specific days' battery action replaced by
    # NO action at all (imp=imp0, exp=gen0), isolating exactly what those
    # days' (unrealistically well-informed) dispatch is worth in dollars,
    # rather than leaving the leak's scale undisclosed (Codex adversarial
    # review, fourth pass).
    da_imp_delaked = np.where(da_leaked, imp0, da_imp)
    da_exp_delaked = np.where(da_leaked, gen0, da_exp)
    fd_delaked = d.copy()
    fd_delaked["I"], fd_delaked["E"] = da_imp_delaked, da_exp_delaked
    da_save_delaked = base_bill - R.bill_nem(fd_delaked, "I", "E")
    leak_sensitivity_usd = round(da_save - da_save_delaked, 2)
    out["day_ahead_forecast"]["leak_sensitivity_usd"] = leak_sensitivity_usd
    out["day_ahead_forecast"]["leak_sensitivity_note"] = (
        f"{da_info['n_days_with_leaked_future_information']} of "
        f"{da_info['n_days']} days ({100 * da_info['n_days_with_leaked_future_information'] / da_info['n_days']:.1f}%) "
        "used real (not forecast) same-day data because no genuine prior day "
        "exists for them within a single year; replacing just those days' "
        "dispatch with no battery action at all changes the reported "
        f"day-ahead save by ${leak_sensitivity_usd:.2f} -- the maximum this "
        "leak could plausibly be inflating the headline figure by.")

    # -- day-ahead, PERFECT same-day information, same myopic single-day
    # horizon: isolates the "no value placed on SOC held past midnight"
    # effect from forecast quality (Codex adversarial review, fourth pass:
    # an earlier version attributed day-ahead's ENTIRE shortfall vs greedy
    # to forecast pre-commitment without ruling out this confound first). --
    ph_imp, ph_exp, ph_charge, ph_discharge, ph_soc_trace, _, ph_info = rolling_day_ahead(
        d, imp0, gen0, soc_init, perfect=True, charge_kw=CHARGE_KW)
    fph = d.copy()
    fph["I"], fph["E"] = ph_imp, ph_exp
    ph_bill = R.bill_nem(fph, "I", "E")
    ph_save = base_bill - ph_bill
    ph_ok, ph_diag = _rolling_conservation_check(
        imp0, gen0, ph_imp, ph_exp, ph_charge, ph_discharge, ph_soc_trace, soc_init,
        ph_info["soc_end_kwh"], POWER_KW, charge_kw=CHARGE_KW)
    if not ph_ok:
        raise SystemExit(
            "perfect_foresight_dispatch: perfect-horizon energy conservation "
            f"check failed ({ph_diag})")

    forecast_cost_usd = round(ph_save - da_save, 2)
    horizon_cost_usd = round(save - ph_save, 2)
    out["day_ahead_perfect_horizon"] = {
        **ph_info,
        "bill_usd": round(ph_bill, 2),
        "save_usd": round(ph_save, 2),
        "energy_conservation_check_passed": bool(ph_ok),
        "note": ("same day-by-day architecture as day_ahead_forecast (SOC fixed "
                 "at the real start-of-day level, free at day's end, real "
                 "bucket offsets carried forward) but with PERFECT same-day "
                 "information instead of a persistence forecast -- isolates "
                 "the myopic single-day horizon effect (no value placed on "
                 "SOC held past midnight) from forecast quality"),
    }

    out["purchasing_statement"] = {
        "greedy_save_usd": canon["pw3"]["greedy"]["save"] if canon else None,
        "day_ahead_save_usd": round(da_save, 2),
        "day_ahead_perfect_horizon_save_usd": round(ph_save, 2),
        "perfect_foresight_save_usd": round(save, 2),
        "day_ahead_value_over_greedy_usd": (
            round(da_save - canon["pw3"]["greedy"]["save"], 2) if canon else None),
        "remaining_gap_day_ahead_to_perfect_usd": round(save - da_save, 2),
        "gap_attributed_to_forecast_error_usd": forecast_cost_usd,
        "gap_attributed_to_myopic_horizon_usd": horizon_cost_usd,
        "note": ("the shipping greedy policy already captures the large majority "
                 "of the theoretical maximum at this hardware (the optimality gap "
                 "above is the full remaining upside from ANY controller, however "
                 "smart). Day-ahead's shortfall versus the true optimum splits "
                 f"into two isolated effects: ${forecast_cost_usd:.2f}/yr from "
                 "imperfect (persistence) forecasting, holding the single-day "
                 f"planning horizon fixed, and ${horizon_cost_usd:.2f}/yr from "
                 "that single-day horizon itself (each day's local plan places "
                 "no value on SOC held past midnight, so it has no incentive to "
                 "preserve charge for tomorrow morning), holding forecast "
                 "quality fixed at perfect -- neither a better forecast alone "
                 "nor a longer planning horizon alone would close the whole "
                 "gap to the true optimum; no shipping product changes the "
                 "controller, only the hardware, so closing the remaining gap "
                 "is a software/firmware question, not a purchasing one, and "
                 "this data does not show day-ahead forecasting alone is the "
                 "answer"),
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
