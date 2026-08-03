#!/usr/bin/env python3
"""Battery sizing curve (issue #12) — sweep energy and power, not two products.

§6 previously only compared two shipping configs (13.5 kWh Powerwall 3, 27 kWh
Powerwall 3 + Expansion), both at 11.5 kW. That answers "which of these two",
never "how much storage this house actually wants". This script re-runs the
same price-aware ("greedy") dispatch from battery_dispatch_policies.py across
an ENERGY grid (5-40 kWh, holding power at 11.5 kW — the rate both shipping
Tesla configs share) and a POWER grid (5-15 kW, holding energy at 13.5 kWh —
the base Powerwall 3), on both current behavior and post-behavior (EV-shifted)
load, using the same canonical engine (rates.bill_nem, monthly per-period NEM
netting, NBC on gross imports) and the same EV-spillover exclusion rule
(>=2.5 kW outside on-peak is never battery-served) at every grid point.

CHARGE vs. DISCHARGE power (issue #40): Tesla's own datasheet gives the
Powerwall 3 a DIFFERENT continuous charge rating (5 kW) from its continuous
discharge rating (11.5 kW, REF_POWER_KW below) -- see research/battery-
research-notes.md. The ENERGY sweep holds discharge power at REF_POWER_KW
(11.5 kW) at every capacity, so every point on it is genuinely this
household's real Powerwall-3-family hardware (expansion units share the base
unit's inverter and charge port, per the same research notes) and uses the
real 5 kW charge cap throughout. The POWER sweep instead VARIES the
discharge rating itself (5-15 kW) to ask "what if this hardware could
discharge faster or slower" -- only its OWN REF_POWER_KW=11.5 grid point is
the real, cited Powerwall 3; the other points are hypothetical inverters
with no cited charge rating of their own, so the 5 kW charge cap is applied
ONLY at that one real point on the power sweep, not assumed for the others
(inventing a charge rating for a hypothetical 5 kW or 15 kW discharge unit
would violate CLAUDE.md's no-invented-rate rule).

Both shipping configs' own capacity/power (13.5 kWh, 27 kWh, 11.5 kW) are added
to the grids exactly, not interpolated, so they are genuinely ON the swept
curve rather than approximated from neighboring points.

Cost model: a linear fit ($ vs kWh) across ONLY the two real quoted configs that
share the energy sweep's own reference power (11.5 kW) — PW3 $14,500/13.5 kWh
and PW3+Expansion $20,400/27 kWh, the same anchors already cited in the
report's §6 table (CLAUDE.md §0: a real quote, not an invented rate). The
report's other two quoted configs (IQ 5P $8,500/5 kWh at 3.8 kW, IQ 10C
$13,000/10 kWh at 7.1 kW) are deliberately EXCLUDED from this fit: their own kW
differs from the energy sweep's 11.5 kW reference, so blending them in would
price some of their own lower power capability into a per-kWh rate applied to
a fixed-11.5kW sweep — confounding energy cost with power cost (Codex
adversarial review, second pass). An earlier version of this script fit all
four configs together ($512.80/kWh blended), materially higher than the
same-power pair's $437.04/kWh, because the smaller units' own lower power
capability was silently priced into the per-kWh rate.

Knee: because the cost fit is linear, the marginal cost of one more kWh is the
same constant (the fit's slope) at every point on the energy grid. The knee is
pre-declared, before the sweep runs, as the smallest energy-grid point whose
OWN marginal kWh (vs the previous grid point) fails to pay back that constant
marginal cost within 10 years — the Powerwall 3 warranty term already cited in
§6 — rather than a threshold picked by eye after seeing the curve.

Sensitivity: which dimension "binds" is reported as a LOCAL ELASTICITY at the
shared reference point (13.5 kWh, 11.5 kW) — an unequal-spacing 3-point
(Lagrange) derivative through the reference's own row and its two immediate
neighbors in each sweep, normalized to percent-change-in-save per percent-
change-in-the-dimension. This is a genuinely local derivative property, immune
to how far either grid happens to extend beyond that neighborhood. An earlier
version compared raw top-to-bottom dollar spans across the two (differently
sized, differently ranged) grids instead — Codex adversarial review, second
pass: that comparison is not apples-to-apples and can flip under an arbitrary
choice of grid endpoints, so it was replaced. A version after that used a
plain secant between the reference's two neighbors, which is only centered AT
the reference when the grid is symmetric around it — not true for either grid
here — so it was replaced again with the proper unequal-spacing formula
(Codex adversarial review, third pass).

Output: battery_sizing_curve.json. This script fully regenerates the artifact.
"""
import json
import os

import numpy as np

import behavior_rebuild as br
from battery_dispatch_policies import billed, run_batt, CHARGE_KW

ENERGY_GRID = sorted({5, 10, 13.5, 15, 20, 25, 27, 30, 35, 40})
POWER_GRID = sorted({5.0, 7.5, 10.0, 11.5, 12.5, 15.0})
REF_POWER_KW = 11.5    # both shipping Tesla configs run at this power
REF_ENERGY_KWH = 13.5  # base Powerwall 3
KNEE_PAYBACK_YEARS = 10  # PW3 warranty term already cited in index.html §6

# Cost-fit anchors: ONLY the two real quoted configs at the energy sweep's own
# reference power (11.5 kW) -- see docstring. The report's other two quoted
# configs run at a different kW and are excluded from the fit for that reason,
# kept here purely as documented context (never used in cost_usd()).
COST_ANCHORS_KWH = np.array([13.5, 27.0])
COST_ANCHORS_USD = np.array([14500.0, 20400.0])
_FIT = np.polyfit(COST_ANCHORS_KWH, COST_ANCHORS_USD, 1)
COST_SLOPE_USD_PER_KWH = float(_FIT[0])
COST_INTERCEPT_USD = float(_FIT[1])

EXCLUDED_COST_CONTEXT = [
    {"kwh": 5.0, "kw": 3.8, "cost_usd": 8500.0,
     "excluded_reason": "kW (3.8) differs from the energy sweep's 11.5 kW reference"},
    {"kwh": 10.0, "kw": 7.1, "cost_usd": 13000.0,
     "excluded_reason": "kW (7.1) differs from the energy sweep's 11.5 kW reference"},
]

SHIPPING_PRODUCTS = [
    {"name": "1x Tesla Powerwall 3", "kwh": 13.5, "kw": 11.5, "cost_usd": 14500},
    {"name": "Powerwall 3 + Expansion", "kwh": 27.0, "kw": 11.5, "cost_usd": 20400},
]


def repo_root():
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = base
        for _ in range(8):
            if os.path.exists(os.path.join(p, "data", "plan_results.csv")):
                return p
            p = os.path.dirname(p)
    raise SystemExit("repo root not found (data/plan_results.csv)")


def cost_usd(kwh):
    return COST_INTERCEPT_USD + COST_SLOPE_USD_PER_KWH * kwh


def _check_conservation(d, imp0, gen0, imp2, exp2, served, thru, cap):
    """CLAUDE.md 1b: the battery must move energy, never create or lose it.
    Total grid import change (- discharge served, + grid top-up import) plus
    total export change (- solar routed to charging) must equal the loss the
    round-trip efficiency actually charges — nothing else."""
    eta = np.sqrt(0.90)
    grid_topup = float((imp2 - imp0).clip(min=0).sum())
    discharge_relief = float((imp0 - imp2).clip(min=0).sum())
    if abs(discharge_relief - served) > 1e-6:
        raise SystemExit(
            f"battery_sizing_curve: discharge relief {discharge_relief:.4f} kWh "
            f"!= served {served:.4f} kWh at cap={cap} — energy accounted for on "
            "the import side does not match what run_batt reports it served")
    charged_from_solar = float((gen0 - exp2).clip(min=0).sum())
    # thru is pack-side (post-round-trip-efficiency) charge throughput; solar +
    # grid top-up are measured AC-side (pre-ETA), so thru must equal ETA times
    # their sum — no charge energy may appear or vanish between the AC bus and
    # the pack beyond the modeled round-trip loss.
    if abs(eta * (charged_from_solar + grid_topup) - thru) > 1e-6:
        raise SystemExit(
            f"battery_sizing_curve: charge throughput mismatch at cap={cap} — "
            f"ETA*(solar {charged_from_solar:.4f} + grid top-up {grid_topup:.4f}) kWh "
            f"!= reported throughput {thru:.4f} kWh")


STEADY_STATE_TOL_KWH = 0.01
STEADY_STATE_MAX_ITERS = 8


def _steady_state_run(d, imp0, gen0, cap, power, policy="greedy", charge_kw=None):
    """run_batt always starts at soc0=cap/2 and runs the measured year once --
    a one-time year-1 boundary condition, not a steady annual cycle. On the
    real data this house's greedy dispatch always saturates against a hard
    boundary (0 or cap) within the year, so the STARTING soc0 has no lasting
    effect once it does -- but until it converges, larger capacities carry a
    larger absolute amount of un-costed "free" starting charge (current-
    behavior, which ends the year near-empty) or un-recovered "stranded"
    ending charge (post-behavior, which can end the year over 90% full),
    contaminating the reported annual savings, marginals, and knee with an
    arbitrary boundary effect that grows with capacity (Codex adversarial
    review, third pass). Fixed here by iterating run_batt, feeding each pass's
    ending SOC forward as the next pass's starting SOC, until they converge to
    within STEADY_STATE_TOL_KWH -- a steady annual charge/discharge cycle.

    charge_kw (issue #40) is the CHARGE-direction cap, separate from `power`
    (the DISCHARGE cap this function sweeps); defaults to None, which makes
    run_batt reuse `power` for both directions, byte-for-byte unchanged from
    before this parameter existed."""
    eta = np.sqrt(0.90)
    soc0 = cap / 2
    for it in range(STEADY_STATE_MAX_ITERS):
        imp2, exp2, served, thru = run_batt(d, imp0, gen0, cap, policy, power_kw=power,
                                            charge_kw=charge_kw, soc0=soc0)
        soc_final = soc0 + thru - served / eta
        if abs(soc_final - soc0) < STEADY_STATE_TOL_KWH:
            return imp2, exp2, served, thru, soc0, soc_final, it + 1
        soc0 = soc_final
    raise SystemExit(
        f"battery_sizing_curve: SOC did not converge to a steady annual cycle "
        f"within {STEADY_STATE_MAX_ITERS} iterations at cap={cap}, power={power} "
        f"-- last diff {soc_final - soc0:.4f} kWh")


def _sweep(d, imp0, gen0, base_bill, grid, dim, charge_kw=None):
    """dim: 'energy' sweeps ENERGY_GRID at REF_POWER_KW; 'power' sweeps
    POWER_GRID at REF_ENERGY_KWH. Returns one row per grid point.

    charge_kw (issue #40) is applied differently per dimension, per the
    module docstring's CHARGE vs. DISCHARGE section: the ENERGY sweep holds
    discharge power at REF_POWER_KW throughout, so every point is real cited
    Powerwall-3-family hardware and gets charge_kw at every point; the POWER
    sweep varies the discharge rating itself, so charge_kw is applied ONLY
    at its REF_POWER_KW grid point (the one real, cited Powerwall 3) --
    every other power-sweep point is a hypothetical inverter with no cited
    charge rating and stays symmetric (charge_kw=None) rather than
    inventing one. Default None preserves the prior symmetric-power
    behavior exactly at every point on both sweeps."""
    rows = []
    for x in grid:
        cap, power = (x, REF_POWER_KW) if dim == "energy" else (REF_ENERGY_KWH, x)
        point_charge_kw = charge_kw if (dim == "energy" or power == REF_POWER_KW) else None
        imp2, exp2, served, thru, soc0, soc_final, iters = _steady_state_run(
            d, imp0, gen0, cap, power, charge_kw=point_charge_kw)
        _check_conservation(d, imp0, gen0, imp2, exp2, served, thru, cap)
        save = base_bill - billed(d, imp2, exp2)
        row = {
            "kwh" if dim == "energy" else "kw": x,
            "save_usd": round(save, 2),
            "kwh_served": round(served, 2),
            "cycles_per_day": round(thru / cap / 365, 4),
            "steady_state_soc0_kwh": round(soc0, 3),
            "steady_state_boundary_imbalance_kwh": round(soc_final - soc0, 4),
            "steady_state_iterations": iters,
        }
        rows.append(row)
    return rows


def _marginal(rows, key):
    """Marginal $/yr per added unit between consecutive (sorted) grid points."""
    out = [None]
    for i in range(1, len(rows)):
        dx = rows[i][key] - rows[i - 1][key]
        dy = rows[i]["save_usd"] - rows[i - 1]["save_usd"]
        out.append(round(dy / dx, 2) if dx else None)
    return out


def _find_knee(energy_rows, marginals):
    """First grid point (i>=1) whose OWN marginal kWh fails a 10-yr payback at
    the fitted constant marginal cost. None if every point still clears it."""
    for i in range(1, len(energy_rows)):
        m = marginals[i]
        if m is None:
            continue
        if m <= 0:
            payback = float("inf")
        else:
            payback = COST_SLOPE_USD_PER_KWH / m
        if payback > KNEE_PAYBACK_YEARS:
            return {
                "kwh": energy_rows[i]["kwh"],
                "prev_kwh": energy_rows[i - 1]["kwh"],
                "marginal_save_usd_per_kwh": m,
                "marginal_cost_usd_per_kwh": COST_SLOPE_USD_PER_KWH,
                "marginal_payback_years": round(payback, 1),
                "threshold_years": KNEE_PAYBACK_YEARS,
                "rule": (f"first grid point whose own marginal kWh (vs the "
                         f"previous point) pays back the fitted ${COST_SLOPE_USD_PER_KWH:,.0f}/kWh "
                         f"marginal cost in more than {KNEE_PAYBACK_YEARS} years "
                         "(the Powerwall 3 warranty term)"),
            }
    return None


def _locate_products(energy_rows):
    """energy_rows were all computed at REF_POWER_KW, so a product can only be
    located here if it actually runs at that reference power -- otherwise its
    savings/payback would be silently mislabeled as if drawn from its own kW."""
    by_kwh = {r["kwh"]: r for r in energy_rows}
    out = []
    for prod in SHIPPING_PRODUCTS:
        if prod["kw"] != REF_POWER_KW:
            raise SystemExit(
                f"battery_sizing_curve: shipping product {prod['name']} runs at "
                f"{prod['kw']} kW, not the energy sweep's reference power "
                f"({REF_POWER_KW} kW) -- its savings here would be silently "
                "mislabeled as if drawn from its own kW")
        r = by_kwh.get(prod["kwh"])
        if r is None:
            raise SystemExit(
                f"battery_sizing_curve: shipping product {prod['name']} "
                f"({prod['kwh']} kWh) is not an exact grid point")
        out.append({
            "name": prod["name"], "kwh": prod["kwh"], "kw": prod["kw"],
            "quoted_cost_usd": prod["cost_usd"], "save_usd": r["save_usd"],
            "kwh_served": r["kwh_served"],
            "payback_years": round(prod["cost_usd"] / r["save_usd"], 2) if r["save_usd"] > 0 else None,
        })
    return out


def _local_elasticity(rows, key, ref_value):
    """Elasticity of savings w.r.t. `key`, evaluated AT ref_value via the
    proper unequal-spacing 3-point (Lagrange) derivative through the
    reference's own row and its two immediate neighbors: (% change in save)
    / (% change in the dimension). A local derivative property -- unlike a
    raw top-to-bottom span, it does not depend on how far either grid extends
    beyond that neighborhood (Codex adversarial review, second pass).

    An earlier version used a plain secant between the two neighbors (hi.save
    - lo.save) / (hi.key - lo.key), which estimates the derivative at the
    MIDPOINT of the two neighbors, not at the reference itself, whenever the
    grid is not symmetric around the reference -- true for both grids here
    (energy neighbors sit 3.5 kWh below / 1.5 kWh above 13.5 kWh; power
    neighbors sit 1.5 kW below / 1.0 kW above 11.5 kW). The formula below
    (h0 = ref-lo, h1 = hi-ref) reduces to the ordinary centered difference
    when h0 == h1, and is EXACT (not merely a better approximation) for any
    quadratic through the three points -- verified directly in
    analysis/test_battery_sizing_curve.py against a known f(x) = x^2 (Codex
    adversarial review, third pass)."""
    idx = next(i for i, r in enumerate(rows) if r[key] == ref_value)
    if idx == 0 or idx == len(rows) - 1:
        raise SystemExit(
            f"battery_sizing_curve: the reference point {ref_value} for {key} "
            "has no flanking grid point on one side -- cannot take a "
            "derivative; widen the grid")
    lo, ref, hi = rows[idx - 1], rows[idx], rows[idx + 1]
    h0 = ref[key] - lo[key]
    h1 = hi[key] - ref[key]
    f0, f1, f2 = lo["save_usd"], ref["save_usd"], hi["save_usd"]
    slope = (f0 * (-h1) / (h0 * (h0 + h1))
             + f1 * (h1 - h0) / (h0 * h1)
             + f2 * h0 / (h1 * (h0 + h1)))
    return slope * ref_value / f1, f1


def _scenario(d, imp0, gen0, label):
    base_bill = billed(d, imp0, gen0)
    energy_rows = _sweep(d, imp0, gen0, base_bill, ENERGY_GRID, "energy", charge_kw=CHARGE_KW)
    power_rows = _sweep(d, imp0, gen0, base_bill, POWER_GRID, "power", charge_kw=CHARGE_KW)
    e_marg = _marginal(energy_rows, "kwh")
    p_marg = _marginal(power_rows, "kw")
    for r, m in zip(energy_rows, e_marg):
        r["marginal_save_usd_per_kwh"] = m
        r["cost_usd"] = round(cost_usd(r["kwh"]), 2)
        r["asset_alone_payback_years"] = (
            round(r["cost_usd"] / r["save_usd"], 2) if r["save_usd"] > 0 else None)
    for r, m in zip(power_rows, p_marg):
        r["marginal_save_usd_per_kw"] = m
        r["cost_usd"] = None
        r["asset_alone_payback_years"] = None
    knee = _find_knee(energy_rows, e_marg)
    energy_elasticity, save_at_ref_e = _local_elasticity(energy_rows, "kwh", REF_ENERGY_KWH)
    power_elasticity, save_at_ref_p = _local_elasticity(power_rows, "kw", REF_POWER_KW)
    if abs(save_at_ref_e - save_at_ref_p) > 1e-6:
        raise SystemExit(
            "battery_sizing_curve: the energy and power sweeps disagree on "
            f"save_usd at the shared reference point ({save_at_ref_e} vs "
            f"{save_at_ref_p}) -- they should be the identical (13.5 kWh, "
            "11.5 kW) configuration")
    binding = "energy" if energy_elasticity >= power_elasticity else "power"
    return {
        "label": label,
        "baseline_bill_current_rates": round(base_bill),
        "energy_sweep_at_11.5kw": energy_rows,
        "power_sweep_at_13.5kwh": power_rows,
        "knee": knee,
        "shipping_products_on_curve": _locate_products(energy_rows),
        "sensitivity": {
            "save_at_reference_usd": round(save_at_ref_e, 2),
            "energy_elasticity": round(energy_elasticity, 4),
            "power_elasticity": round(power_elasticity, 4),
            "binds": binding,
            "note": ("elasticity = (% change in save) / (% change in the "
                     "dimension), an unequal-spacing 3-point derivative AT the "
                     "shared reference point (13.5 kWh, 11.5 kW) using the "
                     "reference's own row and the grid points immediately "
                     "flanking it in each sweep -- a local derivative property, "
                     "not a raw top-to-bottom span, so it does not depend on "
                     "how far either grid extends beyond that neighborhood"),
        },
    }


def main():
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)

    out = {
        "method": ("price-aware ('greedy') dispatch from battery_dispatch_policies.py, "
                   "re-run across an energy grid (5-40 kWh) at 11.5 kW and a power grid "
                   "(5-15 kW) at 13.5 kWh, on the measured year via rates.bill_nem, with "
                   "the identical >=2.5 kW-outside-on-peak EV-spillover exclusion at "
                   "every point"),
        "energy_grid_kwh": ENERGY_GRID,
        "power_grid_kw": POWER_GRID,
        "reference_power_kw": REF_POWER_KW,
        "reference_energy_kwh": REF_ENERGY_KWH,
        "cost_model": {
            "form": "cost_usd = intercept + slope * kwh",
            "slope_usd_per_kwh": round(COST_SLOPE_USD_PER_KWH, 2),
            "intercept_usd": round(COST_INTERCEPT_USD, 2),
            "anchors": [
                {"kwh": float(k), "kw": 11.5, "cost_usd": float(c)}
                for k, c in zip(COST_ANCHORS_KWH, COST_ANCHORS_USD)
            ],
            "excluded_from_fit": EXCLUDED_COST_CONTEXT,
            "note": ("fit to ONLY the two real quoted configs at the energy sweep's "
                     "own 11.5 kW reference power (both already in index.html §6); the "
                     "report's other two quoted configs (excluded_from_fit) run at a "
                     "different kW and are excluded to avoid blending power cost into "
                     "a per-kWh rate applied to a fixed-power sweep — the power sweep's "
                     "own cost and payback are separately not determined, not guessed"),
        },
        "current_behavior": _scenario(d, imp0, gen0, "current_behavior"),
    }

    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions), sop_idx, sop_ts)
    post = _scenario(d, imp_sh, gen0, "post_behavior")
    post["ev_kwh_moved"] = round(moved)
    out["post_behavior"] = post

    root = repo_root()
    tmp = os.path.join(root, "data", "battery_sizing_curve.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(root, "data", "battery_sizing_curve.json"))
    print("wrote data/battery_sizing_curve.json")
    print("current knee:", out["current_behavior"]["knee"])
    print("post-behavior knee:", out["post_behavior"]["knee"])
    print("current sensitivity:", out["current_behavior"]["sensitivity"])
    print("post-behavior sensitivity:", out["post_behavior"]["sensitivity"])


if __name__ == "__main__":
    main()
