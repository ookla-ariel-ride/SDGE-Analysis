#!/usr/bin/env python3
"""Price the always-on load (issue #17): what does the quiet-night floor cost,
and what is cutting it worth?

Context (verify against sources, don't trust -- CLAUDE.md section 0). TECHNICAL.md
section 3.11 (the `phantom` key in `data/extra_results.json`, itself an in-session
computation with no committed generator -- see that section's closing note) reports
a ~1 kW overnight import floor from 44 EV-free quiet nights and DE-PRIORITIZES it:
the owner identifies the cause as home-lab compute (owner-attested, not measured),
so no action is taken and the floor is never costed. This script closes that gap:
it re-measures the floor directly from interval data (not by reading the phantom
key's hand-recorded numbers), prices it two independent ways, and reconciles them.

What "measured" covers and what it does not. The floor's MAGNITUDE is measured
from two independent real instruments: the overnight import series (15-minute
Green Button export, no solar to mask it) and the whole-home consumption meter
(Enphase SAM 8760, which sees self-consumed solar and so is the only instrument
that can see the floor at midday, where an import-only measurement cannot -- see
`away_days` corroboration in the report's section 9, which makes the same point).
The floor's CAUSE (home-lab computer systems) is owner-attested only -- nobody
here re-verified it with a plug-meter study or device-level monitoring. Every
confidence label in the output artifact keeps these two claims separate.

Methodology, acceptance-criterion by acceptance-criterion (issue #17):

1. Full series, not one number. `night_floor.daily_series` reports EVERY calendar
   night in the measured window (median 1-5am import power when the night is
   EV-free, `null` and flagged when it isn't -- same EV exclusion rule TECHNICAL.md
   already documents: max 1-5am demand >= 2 kW means an EV charged that night and
   the interval cannot isolate the floor). `hour_of_day.profile` is a SEPARATE,
   independently-sourced 24-hour distribution (p10/median/p90 of whole-home
   consumption by hour of day, from the SAM meter, which sees the day floor an
   import-only measurement cannot) -- not derived from the night series.

2. Two independent pricing methods, reconciled. Both price the SAME physical
   removal -- a constant floor_kw subtracted from every 15-minute interval's
   load, with any energy that cannot reduce import (because solar was already
   covering it) instead flowing to increased export (see `_split_floor` below,
   the CLAUDE.md 1b "move energy physically" mechanic applied to a load that is
   removed rather than shifted) -- via two genuinely different computations:
     (a) `pricing.method_a_price_map`: a flat multiply-and-sum against
         rates.allin()/rates.credit() (cross-checked against the committed
         `data/extra_results.json -> price_map` the issue cites -- they must
         agree to the cent, since price_map was built from the same rates
         module), applied PER INTERVAL using that interval's own import/export
         sign. No monthly aggregation, no NEM netting.
     (b) `pricing.method_b_rebill`: the canonical monthly-netting billing engine
         (rates.bill_nem, the same engine battery_dispatch_policies.py and
         behavior_rebuild.py use) re-bills the counterfactual year with the
         floor removed, and the delta from the actual billed year is the price.
   These disagree by design whenever a (year-month, season, period) bucket's
   AGGREGATE monthly net sign differs from the per-interval sign method (a)
   assumes -- method (a) prices an interval as "import" or "export" using only
   that interval's own flow, while method (b)'s netting prices the marginal unit
   at whatever sign the WHOLE MONTH's bucket nets to. `pricing.reconciliation`
   reports the gap in dollars and the exact buckets whose sign flips between the
   billed year and the counterfactual, so the gap is explained, not asserted away.

3. Daytime opportunity cost kept separate. Every pricing method above reports
   `avoided_import_usd` (grid electricity the floor forces the household to buy,
   priced at the season/period IMPORT rate) and `displaced_export_usd` (NEM
   export credit forgone because the floor consumes solar that would otherwise
   be exported, priced at the season/period EXPORT credit rate) as separate
   fields -- never summed before being reported, per CLAUDE.md 1b.

4. Sensitivity in $/yr per 100 W. `sensitivity_per_100w` re-bills (method b)
   at every 100 W step from 100 W to MAX_REDUCTION_W and reports both the
   marginal $/100W AT the currently-measured floor level and the GENERAL
   average slope across the whole tested range (a linear fit), stating which
   is which -- the two agree closely only if the removal stays inside a TOU/
   netting regime the buckets do not flip across (see `linearity_note`).

5. Battery interaction, quantified. Re-runs battery_dispatch_policies.run_batt
   (same greedy policy, same Powerwall 3 config, same steady-state convergence
   pattern tou_structure_stress.py established for exactly this reason: a
   one-time year-1 SOC boundary condition would fold an uncosted charge/discharge
   asymmetry into the very delta this script reports) on the baseline import/
   export series and on the floor-removed counterfactual, and reports the
   battery's OWN marginal saving in both cases -- not a hand-wave.

6. Confidence labels. `confidence_labels` distinguishes the measured LOAD
   (real, two independent instruments) from the attested CAUSE (owner's word),
   and separately labels the pricing and battery-interaction sections as
   modeled (they assume the measured floor magnitude holds constant across all
   8,760 hours of the year, which is plausible for continuously-running compute
   but is not itself separately verified at sub-daily resolution beyond the
   measured night window).

7. Artifact + committed script, byte-identical regeneration. This script writes
   data/quiet_night_floor.json directly (repo-root discovery + atomic tmp-then-
   replace, the same convention tou_structure_stress.py and rates_history.py
   use) so the CLAUDE.md section 9 gate applies unchanged.

Inputs (same working-directory convention as every other generator in this
package): usage.csv (behavior_rebuild.load()), samA.csv/samB.csv (Enphase SAM
8760, current/prior calendar year), and the committed data/extra_results.json
(read-only, for the price_map cross-check only -- never the operative source of
a rate; every rate here comes from rates.py directly).
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import behavior_rebuild as br
import battery_dispatch_policies as bdp
import rates as R

SEASONS = ("S", "W")
PERIODS = ("on", "off", "sop")

NIGHT_START_H, NIGHT_END_H = 1.0, 5.0      # matches TECHNICAL.md 3.11's "1-5am"
EV_NIGHT_GATE_KW = 2.0                     # same EV-exclusion threshold documented there

CAP_KWH = 13.5             # bare Powerwall 3, matching battery_dispatch_policies.py's
POWER_KW = 11.5            # "pw3"/"mid" config -- this script isolates the FLOOR's
CHARGE_KW = bdp.CHARGE_KW  # effect alone, so it deliberately does NOT stack the EV
                           # behavior shift (battery_dispatch_policies.py's
                           # post_behavior block) on top; see battery_interaction().
ETA = np.sqrt(0.90)
STEADY_STATE_TOL_KWH = 0.01
STEADY_STATE_MAX_ITERS = 8

STEP_W = 100
MAX_REDUCTION_W = 1200      # brackets the measured ~1.0-1.1 kW floor with headroom
PRICE_MAP_TOL = 0.0005      # cent-level agreement expected between rates.py and
                            # the committed extra_results.json price_map


def repo_root():
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains analysis/ and data/")


# --------------------------------------------------------------------- pricing
def price_map_from_rates():
    """The canonical price map, computed straight from rates.py (never read
    from the extra_results.json copy as the operative source -- CLAUDE.md's
    "one canonical rates module" rule). Matches rates.allin()/rates.credit(),
    which are exactly what price_map's committed values were built from."""
    return {f"{s}_{p}": {"import": round(R.allin(s, p), 4),
                         "export": round(R.credit(s, p), 4)}
            for s in SEASONS for p in PERIODS}


def check_price_map_against_extra_results(root, computed):
    """Fail-closed cross-check against the issue's own cited source
    (`data/extra_results.json -> price_map`). A mismatch beyond rounding means
    either rates.py drifted from the bills or that artifact is stale -- either
    way, silently trusting one over the other would misprice everything below."""
    path = root / "data" / "extra_results.json"
    if not path.exists():
        raise SystemExit(f"{path} not found -- needed to cross-check price_map "
                         "(issue #17's own cited source)")
    committed = json.loads(path.read_text())["price_map"]
    bad = []
    for key, want in computed.items():
        got = committed.get(key)
        if got is None:
            bad.append(f"{key}: missing from committed price_map")
            continue
        for side in ("import", "export"):
            if abs(got[side] - want[side]) > PRICE_MAP_TOL:
                bad.append(f"{key}.{side}: rates.py gives {want[side]}, "
                          f"committed price_map gives {got[side]}")
    if bad:
        raise SystemExit("price_map cross-check failed (rates.py vs committed "
                         "data/extra_results.json):\n  " + "\n  ".join(bad))
    return committed


# ---------------------------------------------------------------- night floor
def night_floor_series(d):
    """Full per-night series (issue AC1) using TECHNICAL.md 3.11's own method:
    for each calendar date, the 1-5am import-power interval group; a night with
    max power >= EV_NIGHT_GATE_KW carried an EV charge and cannot isolate the
    floor (excluded, not zeroed); a quiet night's floor is its median 1-5am
    import power, its cycling signature is the within-night std."""
    d = d.copy()
    d["date"] = d.dt.dt.date
    d["kw"] = d.Consumption.astype(float) * 4.0
    night = d[(d.hour >= NIGHT_START_H) & (d.hour < NIGHT_END_H)]

    daily = []
    quiet_kw = []
    quiet_std = []
    quiet_by_month = {}
    for date, g in night.groupby("date"):
        max_kw = float(g.kw.max())
        ev_night = max_kw >= EV_NIGHT_GATE_KW
        row = {"date": str(date), "ev_night": bool(ev_night)}
        if ev_night:
            row["median_kw"] = None
            row["within_night_std_kw"] = None
        else:
            med = float(g.kw.median())
            std = float(g.kw.std(ddof=0))
            row["median_kw"] = round(med, 4)
            row["within_night_std_kw"] = round(std, 4)
            quiet_kw.append(med)
            quiet_std.append(std)
            quiet_by_month.setdefault(date.month, []).append(med)
        daily.append(row)

    daily.sort(key=lambda r: r["date"])
    quiet_kw_arr = np.array(quiet_kw)
    stats = {
        "nights_total": len(daily),
        "quiet_nights": len(quiet_kw),
        "ev_nights": len(daily) - len(quiet_kw),
        "median_kw": round(float(np.median(quiet_kw_arr)), 4),
        "p10_kw": round(float(np.percentile(quiet_kw_arr, 10)), 4),
        "p90_kw": round(float(np.percentile(quiet_kw_arr, 90)), 4),
        "cycling_within_night_std_kw_median": round(float(np.median(quiet_std)), 4),
        "monthly_median_kw": {str(m): round(float(np.median(v)), 4)
                              for m, v in sorted(quiet_by_month.items())},
        "window_hours": [NIGHT_START_H, NIGHT_END_H],
        "ev_gate_kw": EV_NIGHT_GATE_KW,
    }
    return daily, stats


def cross_check_night_floor(root, stats):
    """Compares this fresh measurement against the already-published (but
    generator-less, per TECHNICAL.md 3.11) `phantom` figures -- not a
    dependency, a corroboration reported alongside the fresh numbers."""
    path = root / "data" / "extra_results.json"
    if not path.exists():
        return None
    phantom = json.loads(path.read_text()).get("phantom")
    if not phantom:
        return None
    return {
        "published_median_kw": phantom.get("baseload_kw_median"),
        "this_run_median_kw": stats["median_kw"],
        "gap_kw": round(stats["median_kw"] - phantom.get("baseload_kw_median", 0.0), 4),
        "published_quiet_nights": phantom.get("quiet_nights"),
        "this_run_quiet_nights": stats["quiet_nights"],
        "note": ("published in data/extra_results.json's phantom key, an "
                "in-session computation with no committed generator "
                "(TECHNICAL.md 3.11); this script re-measures independently "
                "rather than reading that figure as an input"),
    }


# ------------------------------------------------------------- hour-of-day
def _stitched_sam_load(root_cwd, window_start, window_end):
    """Whole-home consumption (kWh == kW at hourly resolution), stitched from
    the two calendar-year SAM 8760 exports the same way deep_analyses.py's
    vacation-detection block does, sliced to the analysis window."""
    b = pd.read_csv("samB.csv").iloc[:, 0].astype(float).values  # prior year
    a = pd.read_csv("samA.csv").iloc[:, 0].astype(float).values  # current year
    idx_b = pd.date_range(f"{window_start.year}-01-01", periods=8760, freq="h")
    idx_a = pd.date_range(f"{window_end.year}-01-01", periods=8760, freq="h")
    load = pd.concat([pd.Series(b, index=idx_b), pd.Series(a, index=idx_a)])
    load = load[(load.index >= pd.Timestamp(window_start)) &
               (load.index < pd.Timestamp(window_end))]
    return load


def hour_of_day_profile(window_start, window_end):
    """A SEPARATE, independently-instrumented 24-hour distribution (issue AC1):
    whole-home consumption sees self-consumed solar, so it is the only signal
    that can show the floor persisting through daylight hours, where an
    import-only measurement reads near zero because solar is covering it."""
    load = _stitched_sam_load(None, window_start, window_end)
    df = load.reset_index()
    df.columns = ["ts", "kw"]
    df["hour"] = df.ts.dt.hour

    profile = []
    for h in range(24):
        sub = df[df.hour == h].kw
        profile.append({
            "hour": h,
            "p10_kw": round(float(sub.quantile(0.10)), 4),
            "median_kw": round(float(sub.median()), 4),
            "p90_kw": round(float(sub.quantile(0.90)), 4),
        })
    midday = [r for r in profile if 10 <= r["hour"] < 14]
    midday_floor_p10_kw = round(float(np.mean([r["p10_kw"] for r in midday])), 4)
    night_hours = [r for r in profile if NIGHT_START_H <= r["hour"] < NIGHT_END_H]
    night_median_kw = round(float(np.mean([r["median_kw"] for r in night_hours])), 4)
    return profile, {
        "source": "Enphase SAM 8760 whole-home consumption (samA.csv/samB.csv)",
        "midday_10to14_p10_kw": midday_floor_p10_kw,
        "night_1to5_median_kw": night_median_kw,
        "note": ("the midday p10 (a low percentile of whole-home consumption "
                "when solar is most abundant) is the day-side analog of the "
                "night-import floor; the two independent instruments "
                "corroborating each other supports (but does not prove) that "
                "the load runs continuously rather than only at night"),
    }


# --------------------------------------------------------------- floor split
def _split_floor(consumption, generation, floor_kwh_per_interval):
    """Physically splits a constant per-interval floor removal into the energy
    that reduces grid import (avoided_import) and whatever is left over, which
    increases export (displaced_export) -- CLAUDE.md 1b's "move energy
    physically" mechanic, applied to energy being REMOVED rather than shifted.
    A floor running continuously needs no destination-placement machinery the
    way a movable EV/appliance load does: it is already spread across every
    interval, so it is removed from every interval directly, in the SAME
    interval it would have been drawn.

    reduce_from_import + leftover == floor_kwh_per_interval for every interval
    by construction (an identity, not an approximation): whatever the floor's
    own energy does not shave off measured import must instead have been
    self-consumed solar that removing the floor frees to export."""
    reduce_from_import = np.minimum(consumption, floor_kwh_per_interval)
    leftover = floor_kwh_per_interval - reduce_from_import
    new_consumption = consumption - reduce_from_import
    new_generation = generation + leftover
    return reduce_from_import, leftover, new_consumption, new_generation


def price_method_a(d, price_map, reduce_from_import, leftover):
    """Hour-by-hour against the marginal price map -- no monthly netting, no
    NEM aggregation: each interval's own import/export sign and its own
    season/period cell decide its price."""
    f = d[["seas", "p"]].copy()
    f["reduce"] = reduce_from_import
    f["leftover"] = leftover
    avoided_import = 0.0
    displaced_export = 0.0
    for s in SEASONS:
        for p in PERIODS:
            key = f"{s}_{p}"
            sub = f[(f.seas == s) & (f.p == p)]
            avoided_import += sub["reduce"].sum() * price_map[key]["import"]
            displaced_export += sub["leftover"].sum() * price_map[key]["export"]
    return {
        "avoided_import_usd": round(float(avoided_import), 2),
        "displaced_export_usd": round(float(displaced_export), 2),
        "total_usd": round(float(avoided_import + displaced_export), 2),
    }


def price_method_b(d, consumption, generation, reduce_from_import, leftover):
    """Re-bill the counterfactual year with the floor removed (monthly
    per-period NEM netting, NBC on gross imports -- rates.bill_nem, the same
    engine every published battery/behavior figure in this repo uses). The
    avoided-import and displaced-export channels are isolated by billing THREE
    series and differencing, so the split reflects the REAL netting mechanics
    (including any monthly sign flip) rather than a per-interval assumption:
      bill0: the actual billed year
      bill1: import reduced, export left AT ITS ACTUAL value (isolates the
             avoided-import channel exactly as the engine would price it)
      bill2: import reduced AND export increased by the leftover (the full
             counterfactual)
    bill0-bill1 + bill1-bill2 telescopes to bill0-bill2 exactly."""
    new_consumption = consumption - reduce_from_import
    new_generation = generation + leftover

    f0 = d.copy(); f0["imp"] = consumption; f0["exp"] = generation
    f1 = d.copy(); f1["imp"] = new_consumption; f1["exp"] = generation
    f2 = d.copy(); f2["imp"] = new_consumption; f2["exp"] = new_generation

    bill0 = br.bill(f0, "imp", "exp")
    bill1 = br.bill(f1, "imp", "exp")
    bill2 = br.bill(f2, "imp", "exp")

    avoided_import = bill0 - bill1
    displaced_export = bill1 - bill2
    return {
        "baseline_bill_usd": round(bill0, 2),
        "counterfactual_bill_usd": round(bill2, 2),
        "avoided_import_usd": round(avoided_import, 2),
        "displaced_export_usd": round(displaced_export, 2),
        "total_usd": round(bill0 - bill2, 2),
    }, new_consumption, new_generation


def sign_flip_buckets(d, consumption, generation, new_consumption, new_generation):
    """Diagnostic explaining the method (a)/(b) reconciliation gap: the
    (year-month, season, period) buckets whose AGGREGATE monthly net sign
    differs between the billed year and the floor-removed counterfactual.
    Method (a) prices every interval by its OWN sign; method (b)'s monthly
    netting prices the marginal unit by the BUCKET's sign -- a flip is exactly
    where the two computations must disagree, and this reports every one
    rather than asserting the gap is small."""
    f0 = d.copy(); f0["imp"] = consumption; f0["exp"] = generation
    f2 = d.copy(); f2["imp"] = new_consumption; f2["exp"] = new_generation
    net0 = f0.groupby(["ym", "seas", "p"]).apply(
        lambda g: float(g["imp"].sum() - g["exp"].sum()), include_groups=False)
    net2 = f2.groupby(["ym", "seas", "p"]).apply(
        lambda g: float(g["imp"].sum() - g["exp"].sum()), include_groups=False)
    flips = []
    for key in sorted(net0.index, key=lambda k: (str(k[0]), k[1], k[2])):
        n0 = net0[key]
        n2 = float(net2.get(key, n0))
        if n0 == 0 or n2 == 0:
            continue
        if (n0 > 0) != (n2 > 0):
            flips.append({
                "year_month": str(key[0]), "season": key[1], "period": key[2],
                "baseline_net_kwh": round(n0, 2),
                "counterfactual_net_kwh": round(n2, 2),
            })
    total_kwh_in_flipped_buckets = sum(abs(f["baseline_net_kwh"]) for f in flips)
    return flips, round(total_kwh_in_flipped_buckets, 2)


# ---------------------------------------------------------------- sensitivity
def sensitivity_per_100w(d, consumption, generation, floor_kw_measured):
    steps = []
    prev_savings = 0.0
    for w in range(STEP_W, MAX_REDUCTION_W + STEP_W, STEP_W):
        floor_kwh = (w / 1000.0) * 0.25
        reduce_i, leftover_i, new_c, new_g = _split_floor(consumption, generation, floor_kwh)
        f0 = d.copy(); f0["imp"] = consumption; f0["exp"] = generation
        f2 = d.copy(); f2["imp"] = new_c; f2["exp"] = new_g
        savings = br.bill(f0, "imp", "exp") - br.bill(f2, "imp", "exp")
        marginal = savings - prev_savings
        steps.append({"reduction_w": w, "annual_savings_usd": round(savings, 2),
                     "marginal_usd_per_100w": round(marginal, 2)})
        prev_savings = savings

    ws = np.array([s["reduction_w"] for s in steps], dtype=float)
    savings_arr = np.array([s["annual_savings_usd"] for s in steps])
    slope, intercept = np.polyfit(ws, savings_arr, 1)
    fit = slope * ws + intercept
    max_dev = float(np.max(np.abs(savings_arr - fit)))
    max_dev_pct = round(100.0 * max_dev / savings_arr[-1], 2) if savings_arr[-1] else 0.0

    current_w = int(round(floor_kw_measured * 1000 / STEP_W)) * STEP_W
    current_w = min(max(current_w, STEP_W), MAX_REDUCTION_W)
    at_current = next(s for s in steps if s["reduction_w"] == current_w)

    return {
        "basis": (f"method b (full monthly re-bill) at {STEP_W} W steps from "
                 f"{STEP_W} to {MAX_REDUCTION_W} W"),
        "steps": steps,
        "usd_per_100w_at_current_floor": {
            "floor_w_used": current_w,
            "value_usd": at_current["marginal_usd_per_100w"],
            "note": ("the marginal $/100W step nearest the measured floor "
                    f"({floor_kw_measured * 1000:.0f} W) -- the rate a household "
                    "actually near this floor level should expect from the "
                    "NEXT 100 W removed"),
        },
        "usd_per_100w_general_average": {
            "value_usd": round(float(slope) * 100, 2),
            "note": (f"linear-fit slope across the full {STEP_W}-{MAX_REDUCTION_W} W "
                    "range -- a general marginal rate, not specific to the "
                    "current floor level"),
        },
        "linearity_note": (
            f"max deviation from the linear fit is ${max_dev:.2f} "
            f"({max_dev_pct}% of total savings at {MAX_REDUCTION_W} W) across the "
            "tested range -- " +
            ("effectively linear over this range" if max_dev_pct < 2
             else "measurably nonlinear; see sign_flip_buckets for why")),
    }


# ------------------------------------------------------------ battery
def _steady_state_battery(d, imp0, gen0):
    """Same steady-annual-cycle convergence tou_structure_stress.py established
    (reimplemented locally rather than importing that script's underscore-
    prefixed internal helper across a module boundary, matching its own stated
    convention): run_batt's soc0=cap/2 default is a one-time year-1 boundary
    condition, not a steady cycle, and a floor-reduced counterfactual that
    happens to end the year meaningfully fuller or emptier than the baseline
    would fold un-costed boundary energy into the very delta this function
    exists to measure."""
    soc0 = CAP_KWH / 2
    for _ in range(STEADY_STATE_MAX_ITERS):
        imp2, exp2, served, thru = bdp.run_batt(
            d, imp0, gen0, CAP_KWH, "greedy", power_kw=POWER_KW,
            charge_kw=CHARGE_KW, soc0=soc0)
        soc_final = soc0 + thru - served / ETA
        if abs(soc_final - soc0) < STEADY_STATE_TOL_KWH:
            return imp2, exp2
        soc0 = soc_final
    raise SystemExit("quiet_night_floor: battery SOC did not converge to a "
                     f"steady annual cycle within {STEADY_STATE_MAX_ITERS} iterations")


def battery_interaction(d, consumption, generation, new_consumption, new_generation):
    """Whether a lower floor makes the battery case better or worse, and by how
    much (issue AC5) -- computed by actually re-running the dispatch engine on
    both series, not asserted. Deliberately isolates the floor's own effect: no
    EV-shift behavior model is stacked on top (battery_dispatch_policies.py's
    post_behavior block), so this is the battery's marginal value on the RAW
    measured year vs. the SAME raw year with only the floor removed."""
    i0, e0 = _steady_state_battery(d, consumption, generation)
    baseline_marginal = bdp.billed(d, consumption, generation) - bdp.billed(d, i0, e0)

    i1, e1 = _steady_state_battery(d, new_consumption, new_generation)
    reduced_bill_no_batt = br.bill(
        d.assign(imp=new_consumption, exp=new_generation), "imp", "exp")
    reduced_marginal = reduced_bill_no_batt - bdp.billed(d, i1, e1)

    delta = reduced_marginal - baseline_marginal
    delta_pct = round(100.0 * delta / baseline_marginal, 2) if baseline_marginal else None
    if delta < -0.005:
        direction = ("removing the floor SHRINKS the battery's own marginal "
                     "saving: the floor persists into the 4-9pm on-peak window "
                     "the battery discharges into, so a smaller floor leaves "
                     "less expensive import for the battery to displace")
    elif delta > 0.005:
        direction = ("removing the floor GROWS the battery's own marginal "
                     "saving: the freed capacity/power headroom lets the "
                     "battery serve more of the remaining import")
    else:
        direction = "removing the floor leaves the battery's marginal saving essentially unchanged"

    return {
        "config": "13.5 kWh Powerwall 3 (bare unit), greedy policy, raw measured "
                  "year (no EV-shift behavior model stacked on top -- isolates "
                  "the floor's own effect on the battery)",
        "baseline_battery_marginal_usd": round(baseline_marginal, 2),
        "reduced_floor_battery_marginal_usd": round(reduced_marginal, 2),
        "delta_usd": round(delta, 2),
        "delta_pct": delta_pct,
        "direction": direction,
    }


def confidence_labels():
    """Issue AC6: distinguishes the measured LOAD from the attested CAUSE, and
    separately labels the modeled sections. A standalone function (not inlined
    into main()) so it is directly testable without running the full pipeline."""
    return {
        "load_magnitude": ("measured -- night_floor is read directly from "
                          "15-minute Green Button import data (no solar to "
                          "mask it); hour_of_day is read directly from the "
                          "Enphase SAM 8760 whole-home consumption meter, an "
                          "independent instrument that sees self-consumed "
                          "solar and so is the only one that can see a "
                          "daytime floor"),
        "load_cause": ("attested, not measured -- the owner identifies the "
                      "floor as home-lab computer systems (TECHNICAL.md "
                      "3.11); this script does not verify that with a "
                      "plug-meter study or device-level monitoring, and "
                      "reports the load's cost regardless of cause"),
        "pricing": ("modeled -- both pricing methods apply the measured "
                   "floor_kw as a CONSTANT across all 8,760 hours of the "
                   "year, an assumption (continuous compute load) that is "
                   "plausible but not separately verified outside the "
                   "measured 1-5am night window and the SAM hour-of-day "
                   "cross-check"),
        "battery_interaction": ("modeled -- battery_dispatch_policies.run_batt, "
                               "the same engine behind every published "
                               "battery figure in this repo, applied to a "
                               "counterfactual load series"),
    }


# --------------------------------------------------------------------- main
def main():
    root = repo_root()
    d = br.load()
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)

    daily_series, night_stats = night_floor_series(d)
    night_cross_check = cross_check_night_floor(root, night_stats)

    window_start = (br.WINDOW_END - dt.timedelta(days=365)).date()
    window_end = br.WINDOW_END.date()
    hour_profile, hour_stats = hour_of_day_profile(window_start, window_end)

    computed_price_map = price_map_from_rates()
    committed_price_map = check_price_map_against_extra_results(root, computed_price_map)

    floor_kw = night_stats["median_kw"]
    floor_kwh_per_interval = floor_kw * 0.25
    reduce_i, leftover_i, new_consumption, new_generation = _split_floor(
        consumption, generation, floor_kwh_per_interval)

    method_a = price_method_a(d, computed_price_map, reduce_i, leftover_i)
    method_b, mb_new_c, mb_new_g = price_method_b(d, consumption, generation, reduce_i, leftover_i)
    flips, flipped_kwh = sign_flip_buckets(d, consumption, generation, mb_new_c, mb_new_g)

    gap_usd = round(method_a["total_usd"] - method_b["total_usd"], 2)
    gap_pct = round(100.0 * gap_usd / method_b["total_usd"], 2) if method_b["total_usd"] else None

    sensitivity = sensitivity_per_100w(d, consumption, generation, floor_kw)
    battery = battery_interaction(d, consumption, generation, new_consumption, new_generation)

    out = {
        "method": __doc__.strip().split("\n\n")[0],
        "window": {"start": str(window_start), "end": str(window_end)},
        "night_floor": {**night_stats, "daily_series": daily_series,
                        "cross_check_extra_results_json": night_cross_check},
        "hour_of_day": {"profile": hour_profile, **hour_stats},
        "pricing": {
            "floor_kw_priced": round(floor_kw, 4),
            "floor_kw_basis": ("the measured quiet-night median import power "
                              "(night_floor.median_kw), applied as a constant "
                              "across all 8,760 hours of the year -- see "
                              "confidence_labels.pricing"),
            "price_map_source": ("rates.allin()/rates.credit(), cross-checked "
                                 "against the committed data/extra_results.json "
                                 "price_map (issue #17's cited source) to within "
                                 f"${PRICE_MAP_TOL}/kWh"),
            "price_map": computed_price_map,
            "method_a_price_map": method_a,
            "method_b_rebill": method_b,
            "reconciliation": {
                "gap_usd": gap_usd,
                "gap_pct": gap_pct,
                "avoided_import_gap_usd": round(
                    method_a["avoided_import_usd"] - method_b["avoided_import_usd"], 2),
                "displaced_export_gap_usd": round(
                    method_a["displaced_export_usd"] - method_b["displaced_export_usd"], 2),
                "sign_flip_buckets": flips,
                "sign_flip_buckets_kwh": flipped_kwh,
                "explanation": (
                    "method (a) prices every 15-minute interval by its OWN "
                    "import/export sign against a flat season/period rate; "
                    "method (b)'s monthly NEM netting prices the marginal kWh "
                    "by the (month, season, period) BUCKET's aggregate sign. "
                    "The non-bypassable-charge term is linear in total gross "
                    "import regardless of bucketing, so it contributes no gap; "
                    "the entire reconciliation gap traces to buckets where "
                    f"the two sign conventions disagree -- {len(flips)} bucket(s) "
                    f"totalling {flipped_kwh} kWh in this measured year. A small "
                    "gap concentrated in a handful of near-zero-net buckets is "
                    "the expected signature of this mechanism, not an error in "
                    "either method."),
            },
        },
        "sensitivity_per_100w": sensitivity,
        "battery_interaction": battery,
        "confidence_labels": confidence_labels(),
        "notes": {
            "engine": "rates.bill_nem (monthly per-period NEM netting, NBC on gross imports)",
            "quiet_night_method": "TECHNICAL.md 3.11: 1-5am median import power, EV nights excluded (max 1-5am power >= 2 kW)",
        },
    }

    tmp = root / "data" / "quiet_night_floor.json.tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
    tmp.replace(root / "data" / "quiet_night_floor.json")
    print("wrote data/quiet_night_floor.json")
    print(f"night floor: {floor_kw} kW median ({night_stats['quiet_nights']} quiet "
         f"nights of {night_stats['nights_total']})")
    print(f"method a total: ${method_a['total_usd']}/yr, method b total: "
         f"${method_b['total_usd']}/yr, gap: ${gap_usd} ({gap_pct}%)")
    print(f"sensitivity: ${sensitivity['usd_per_100w_at_current_floor']['value_usd']}"
         "/yr per 100W at the current floor")
    print(f"battery interaction: {battery['delta_usd']}/yr ({battery['delta_pct']}%)")


if __name__ == "__main__":
    main()
