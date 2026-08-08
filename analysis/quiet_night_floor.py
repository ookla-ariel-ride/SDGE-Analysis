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
   quiet, `null` and flagged `excluded_high_demand` when it isn't -- a NEW,
   independently-designed per-night rule: max 1-5am power >= HIGH_DEMAND_GATE_KW
   excludes the whole night. This is NOT read off any existing documented rule
   -- see `night_floor_series`'s docstring for why a per-night gate suits an
   all-night floor better than the repo's existing per-interval one
   (TECHNICAL.md 3.5 item 2 / deep_analyses.py), and `night_floor.
   selection_caveat` for the resulting exclusion rate, which the field name is
   deliberately neutral about (a dryer or a heat-pump cycle trips the same
   gate an EV charge does)). `hour_of_day.profile` is a SEPARATE,
   independently-sourced 24-hour distribution (p10/median/p90 of whole-home
   consumption by hour of day, from the SAM meter, which sees the day floor an
   import-only measurement cannot) -- not derived from the night series.

2. Two independent pricing methods, reconciled. Both price the SAME physical
   removal -- a constant floor_kw subtracted from every 15-minute interval's
   load, with any energy that cannot reduce import (because solar was already
   covering it) instead flowing to increased export where solar is actually
   metered there, and simply dropped otherwise (see `_split_floor` and
   `floor_assumption_violations` below, the CLAUDE.md 1b "move energy
   physically" mechanic applied to a load that is removed rather than shifted,
   with its own quantified limitation) -- via two genuinely different
   computations:
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
   These disagree in a small, fully explained way: `pricing.gap_decomposition`
   (see `gap_decomposition`'s own docstring) shows the PRIMARY mechanism is
   PCIA being priced differently inside buckets whose net sign does not
   change, with sign flips (`sign_flip_buckets`) a smaller, secondary
   contributor -- an independent review (PR #77) showed an earlier version of
   this module had that backwards, naming sign flips as the sole cause when a
   per-bucket hand decomposition proved the PCIA term dominates and the
   sign-flip term can carry the opposite sign of the total gap. See
   `pricing.reconciliation.scope_of_agreement` for what this reconciliation
   does and does not validate -- notably NOT the physical floor-allocation
   model in `_split_floor`, where finding 2 above lives.

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

The 43-vs-44 quiet-night discrepancy (issue #114), investigated -- including a
correction to this investigation's own first pass (Codex adversarial review):
that pass checked EV presence only WITHIN the 43 nights this script's own gate
already selected, which cannot test what an independently-applied EV-free rule
would select from all 365 nights, and its "would cut the count to 5" claim was
wrong as a result. Re-run correctly below: `behavior_rebuild.detect_sessions`
cross-referenced against EVERY one of the 365 measured nights, not just the 43
already accepted by HIGH_DEMAND_GATE_KW.
  - TECHNICAL.md 3.11's "44 EV-free quiet nights" phrasing suggests phantom's
    rule is EV-charging-session absence, not (or not only) this script's own
    demand-magnitude gate. Classifying all 365 nights by "zero EV kWh in the
    window" ALONE (no demand gate at all) gives: 1-5am window 69 nights,
    0-5am 49, 0-6am 42, 1-6am 59, 9pm-6am (the literal "overnight" reading)
    40 -- several of these land close to 43/44 in COUNT, which is the
    opposite of "falsified": a pure EV-absence rule is a live, plausible
    candidate, not a ruled-out one. But none of these EV-free-only variants
    reproduces phantom's own median/p10 (1.025/0.785 kW): every one comes out
    higher on both (e.g. the closest-by-count 0-6am variant gives median
    1.08, p10 0.822 -- notably above phantom's 1.025/0.785), so EV-absence
    ALONE is not a sufficient rule either; some further filtering (a demand
    component, a different EV-detection threshold, or something else) would
    still be needed to land on phantom's own reported shape, not just its
    count.
  - A real gate-boundary case exists: 2026-05-03's 04:45 interval reads
    exactly 0.500 kWh (2.00 kW at the 4x scaling here), landing exactly ON
    `HIGH_DEMAND_GATE_KW` under this script's `>=` comparison, which excludes
    it. A `>` comparison (or an equally defensible rule using a gate a
    hundredth of a kW higher) would flip this one night to quiet -- a
    concrete illustration of how a single interval's exact metered value can
    move the count by one at this threshold. It is NOT confirmed as THE night
    phantom's original rule kept differently: including it moves May's
    monthly median from 0.845 kW to 0.85 kW, while phantom's own May figure
    (monthly_kw["5"]) is 0.845 -- the same value this script already
    reproduces exactly without that night. Since the issue's own diff of the
    two artifacts singles out JULY as the one month whose median differs
    (1.04 here vs 1.035 published), the missing 44th night -- if there is a
    single one -- more likely falls in July, not May.
  - Every demand-gate/window variant tried on top of this script's own rule
    (0-6am, 1-6am, 0-5am windows; gate 1.9/2.5 kW) leaves July's quiet-night
    count at exactly 3 or drops it to 0 -- no tested variant produces a 4th
    July quiet night that way. Every other July night's 1-5am max power sits
    at 12 kW or higher, nowhere near any plausible demand-gate value, so the
    July gap is not a simple threshold/window tweak on the DEMAND-GATE axis
    specifically -- it may still be reachable on the EV-detection axis (a
    different EV-session threshold or window than tried above), which was
    not exhaustively swept.
  - `phantom` has no lost script to recover: `git log --diff-filter=A` on
    `data/extra_results.json` shows it was added directly as a data file
    (commit 29f8573, "Add soiling, cleaning-study, carbon, and extras data
    outputs") with no accompanying generator, ever, in this repo's history --
    consistent with `analysis/extra_results.py`'s own documentation that
    `phantom` is a one-time in-session computation this repo has no
    reproducible record of.
  Conclusion: the exact night(s) and exact rule responsible for the 44-vs-43
  gap are NOT recoverable from currently available evidence -- honestly
  stated here rather than guessed, and corrected from this investigation's
  own first pass, which overclaimed a "falsified" verdict from an
  under-powered test. What IS established with evidence: (a) an EV-session-
  absence rule is a live, count-plausible candidate for phantom's own
  "EV-free" description, not a ruled-out one, but (b) EV-absence alone
  doesn't reproduce phantom's median/p10 shape, so the true rule -- if it is
  recoverable at all -- combines EV-detection with some other filter neither
  this script's demand gate nor a pure EV-free rule alone captures. No
  artifact was changed as a result of this investigation -- this script's own
  43/1.03 figures are its own honest, reproducible measurement, and `phantom`
  stays frozen per issue #34/PR #103's deliberate decision (CLAUDE.md section
  0: a discrepancy this small, once genuinely investigated and found
  unreconcilable with available evidence, gets documented, not papered over
  or guessed away in either direction).

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

NIGHT_START_H, NIGHT_END_H = 1.0, 5.0      # a NEW per-night window (see night_floor_series
                                            # docstring) -- NOT read off any existing rule
HIGH_DEMAND_GATE_KW = 2.0                  # renamed from EV_NIGHT_GATE_KW (PR #77 review,
                                            # finding 4): a causally-neutral threshold name --
                                            # any high-power interval trips it, not only an EV

CAP_KWH = 13.5             # bare Powerwall 3. No canonical constant exists for this in
                           # battery_dispatch_policies.py (13.5 appears there only as a
                           # literal inside main()'s per-config loop, the same way
                           # tou_structure_stress.py's own CAP_KWH is a local literal too)
                           # -- so, unlike POWER_KW/CHARGE_KW below, there is nothing to
                           # import here (PR #77 review nitpick).
POWER_KW = bdp.PWRQ * 4    # bdp.PWRQ = 11.5/4 is bdp's OWN canonical discharge-power
                           # constant (quarter-hour-adjusted); importing it here (PR #77
                           # review nitpick) means a future change to bdp's discharge
                           # rating propagates instead of silently diverging from a second
                           # hardcoded 11.5.
CHARGE_KW = bdp.CHARGE_KW  # bare-unit Powerwall 3 continuous charge rating, imported the
                           # same way -- this script isolates the FLOOR's effect alone, so
                           # it deliberately does NOT stack the EV behavior shift
                           # (battery_dispatch_policies.py's post_behavior block) on top;
                           # see battery_interaction().
ETA = bdp.ETA              # bdp's own canonical round-trip-efficiency constant, imported
                           # the same way rather than re-declared (PR #77 review nitpick)
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
    """Full per-night series (issue AC1). A NEW, independently-designed
    per-night rule (PR #77 review, finding 3 -- an earlier version wrongly
    claimed this was inherited from TECHNICAL.md 3.11, which documents no
    extraction rule at all, only the phantom key's result values; the
    repo's EXISTING rule, TECHNICAL.md 3.5 item 2 / deep_analyses.py, is a
    different, per-INTERVAL filter: 3-5am window, Consumption <= 0.5 kWh per
    interval, 25th percentile -- not reused here): for each calendar date,
    the 1-5am import-power interval group; a night whose max power in that
    window reaches HIGH_DEMAND_GATE_KW carried some high-demand event (EV
    charging is the obvious candidate, but a dryer, a heat-pump cycle, or a
    well pump would trip the same gate -- the field name below is
    deliberately causally neutral) and cannot isolate the floor for that
    whole night (excluded, not zeroed); a quiet night's floor is its median
    1-5am import power, its cycling signature is the within-night std. A
    per-NIGHT gate (rather than the existing per-INTERVAL one) is the more
    appropriate shape for isolating an ALL-NIGHT continuous floor: a
    per-interval filter can admit a night with a brief spike as 'quiet' so
    long as its other intervals stay low, letting spillover contaminate the
    aggregate, while a per-night gate excludes the whole night once any
    interval crosses the threshold."""
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
        excluded = max_kw >= HIGH_DEMAND_GATE_KW
        row = {"date": str(date), "excluded_high_demand": bool(excluded)}
        if excluded:
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
    nights_total = len(daily)
    excluded_nights = nights_total - len(quiet_kw)
    stats = {
        "nights_total": nights_total,
        "quiet_nights": len(quiet_kw),
        "excluded_nights": excluded_nights,
        "excluded_night_fraction": round(excluded_nights / nights_total, 4) if nights_total else None,
        "median_kw": round(float(np.median(quiet_kw_arr)), 4),
        "p10_kw": round(float(np.percentile(quiet_kw_arr, 10)), 4),
        "p90_kw": round(float(np.percentile(quiet_kw_arr, 90)), 4),
        "cycling_within_night_std_kw_median": round(float(np.median(quiet_std)), 4),
        "monthly_median_kw": {str(m): round(float(np.median(v)), 4)
                              for m, v in sorted(quiet_by_month.items())},
        "window_hours": [NIGHT_START_H, NIGHT_END_H],
        "exclusion_gate_kw": HIGH_DEMAND_GATE_KW,
        "selection_caveat": (
            "the floor is measured on the subsample of nights whose 1-5am "
            "window never crossed the exclusion gate -- an "
            f"{round(100 * excluded_nights / nights_total, 1) if nights_total else 0}% "
            "exclusion rate (PR #77 review, finding 4). 'excluded_high_demand' "
            "names the MEASURED behavior (a high-power interval occurred), "
            "not a cause: an EV charging session is the obvious candidate on "
            "this household (see confidence_labels.load_cause), but a "
            "dryer, a heat-pump defrost cycle, or a well pump would trip the "
            "identical gate. Report the exclusion rate alongside the floor "
            "figure rather than treating the kept ~"
            f"{round(100 * len(quiet_kw) / nights_total, 1) if nights_total else 0}% "
            "as the whole story."),
    }
    return daily, stats


def cross_check_night_floor(root, stats):
    """Compares this fresh measurement against the already-published (but
    generator-less, per TECHNICAL.md 3.11) `phantom` figures -- not a
    dependency, a corroboration reported alongside the fresh numbers. The two
    are ALLOWED to disagree (this script's 43-quiet-night rule is a new,
    independently-designed per-night gate -- see night_floor_series's
    docstring -- not a reimplementation of whatever produced `phantom`). The
    resulting small gap (43 vs 44 nights, ~0.005 kW on the median) was
    investigated for issue #114; see the module docstring's "43-vs-44
    quiet-night discrepancy" section for the evidence gathered (a pure
    EV-session-absence rule is count-plausible but doesn't reproduce
    phantom's own median/p10 shape on its own; the exact rule and night(s)
    are not recoverable from available evidence, since `phantom` predates
    this repo's generator convention and no prior script for it exists
    anywhere in this repo's git history)."""
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
                "(TECHNICAL.md 3.11); this script re-measures independently, "
                "with its own independently-designed per-night rule (see "
                "night_floor.selection_caveat and the module docstring), "
                "rather than reading that figure as an input"),
    }


# ------------------------------------------------------------- hour-of-day
def _stitched_sam_load(window_start, window_end):
    """Whole-home consumption (kWh == kW at hourly resolution), stitched from
    the two calendar-year SAM 8760 exports the same way deep_analyses.py's
    vacation-detection block does, sliced to the analysis window. Hardcodes
    periods=8760 the same way deep_analyses.py does -- a calendar year that
    happens to be a leap year would silently misalign the last day or so of
    stitched index against wall-clock dates (PR #77 review nitpick, low
    priority: neither this script's real analysis window nor the SAM export
    format itself carries a Feb 29 row today)."""
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
    load = _stitched_sam_load(window_start, window_end)
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
    sam_night_median_kw = round(float(np.mean([r["median_kw"] for r in night_hours])), 4)
    return profile, {
        "source": "Enphase SAM 8760 whole-home consumption (samA.csv/samB.csv)",
        "midday_10to14_p10_kw": midday_floor_p10_kw,
        "sam_night_1to5_median_kw_for_reference_only": sam_night_median_kw,
        "note": ("the midday p10 (a low percentile of whole-home consumption "
                "when solar is most abundant) is the day-side analog of the "
                "night-import floor, and is the ONLY figure here used as "
                "corroboration; the two independent instruments agreeing "
                "supports (but does not prove) that the load runs "
                "continuously rather than only at night. "
                "sam_night_1to5_median_kw_for_reference_only is the SAME "
                "SAM instrument's own 1-5am median, shown for reference "
                "only (PR #77 review nitpick: an earlier version placed this "
                "next to the corroboration note in a way that could be "
                "misread as a second, independent corroborating figure -- it "
                "is not; it is just the SAM meter's own night reading, not a "
                "cross-instrument comparison)."),
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

    NOT an identity (PR #77 review, finding 2 -- corrected from an earlier
    version that claimed it was): leftover can only be credited as freed solar
    export in an interval that actually HAS metered generation already. Where
    Consumption < floor_kwh_per_interval AND Generation == 0 in the SAME
    interval, crediting the shortfall as freed export would invent energy that
    was never metered -- 2,234 of the flagged intervals in the real measured
    year are literally before 6am or after 7pm, when solar is physically
    impossible, so the shortfall there cannot be self-consumed solar; it is
    simply the constant-floor assumption exceeding the interval's actual load.
    That shortfall is DROPPED here (clamped to zero, not credited), making
    every downstream dollar figure conservative by roughly its value rather
    than inflated by it -- see floor_assumption_violations() for the
    quantified size of this residual, which callers should report alongside
    any headline figure built from this split."""
    reduce_from_import = np.minimum(consumption, floor_kwh_per_interval)
    raw_leftover = floor_kwh_per_interval - reduce_from_import
    leftover = np.where(generation > 0, raw_leftover, 0.0)
    new_consumption = consumption - reduce_from_import
    new_generation = generation + leftover
    return reduce_from_import, leftover, new_consumption, new_generation


def floor_assumption_violations(d, consumption, generation, floor_kwh_per_interval, price_map):
    """Quantifies the residual _split_floor's docstring now names (PR #77
    review, finding 2): intervals where the constant floor_kw assumption
    implies more energy than either measured import could supply or measured
    solar could have freed. Reports the count, the kWh dropped, how much of it
    falls in hours where solar is physically impossible (before 6am or after
    7pm -- the reviewer's own boundary), and what that energy would have been
    worth at the season/period export rate had it been (wrongly) credited --
    i.e. how conservative the headline pricing is because of this clamp."""
    reduce_from_import = np.minimum(consumption, floor_kwh_per_interval)
    raw_leftover = floor_kwh_per_interval - reduce_from_import
    violated_mask = (raw_leftover > 0) & (generation == 0)
    violated_kwh = float(raw_leftover[violated_mask].sum())
    night_mask = (d.hour.values < 6) | (d.hour.values >= 19)
    violated_night_kwh = float(raw_leftover[violated_mask & night_mask].sum())

    f = d[["seas", "p"]].copy()
    f["violated"] = np.where(violated_mask, raw_leftover, 0.0)
    violated_usd = 0.0
    for s in SEASONS:
        for p in PERIODS:
            key = f"{s}_{p}"
            sub = f[(f.seas == s) & (f.p == p)]
            violated_usd += sub["violated"].sum() * price_map[key]["export"]

    return {
        "intervals": int(violated_mask.sum()),
        "intervals_physically_impossible_as_solar": int((violated_mask & night_mask).sum()),
        "kwh": round(violated_kwh, 2),
        "kwh_physically_impossible_as_solar": round(violated_night_kwh, 2),
        "usd_dropped_at_export_rate": round(float(violated_usd), 2),
        "note": ("intervals where Consumption < floor and Generation == 0 in "
                "the SAME interval -- the constant-floor assumption implies "
                "more energy than this interval's metered import or solar can "
                "account for. _split_floor DROPS this shortfall rather than "
                "crediting it as freed export, so every pricing figure built "
                "from this split is conservative by roughly usd_dropped_at_"
                "export_rate, not inflated by it. The physically_impossible_"
                "as_solar counts (before 6am or after 7pm, when solar cannot "
                "exist) confirm the shortfall is a real limit of assuming a "
                "constant floor, not merely daytime self-consumption timing "
                "noise."),
    }


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


def _bucket_net(d, imp, exp):
    """(year-month, season, period) -> net kWh (imp - exp) for a frame built
    off `d`'s own ym/seas/p columns. Both callers below group the SAME `d`
    (only the imp/exp values change between baseline and counterfactual), so
    the two resulting Series always share an identical index -- there is no
    "bucket present in one but not the other" case to guard against."""
    f = d.copy(); f["imp"] = imp; f["exp"] = exp
    return f.groupby(["ym", "seas", "p"]).apply(
        lambda g: float(g["imp"].sum() - g["exp"].sum()), include_groups=False)


def sign_flip_buckets(d, consumption, generation, new_consumption, new_generation):
    """Diagnostic (one part of the reconciliation, not the dominant one -- see
    gap_decomposition for the actual primary mechanism, PR #77 review finding
    1): the (year-month, season, period) buckets whose AGGREGATE monthly net
    sign differs between the billed year and the floor-removed counterfactual.
    Reports every one rather than asserting the gap is small."""
    net0 = _bucket_net(d, consumption, generation)
    net2 = _bucket_net(d, new_consumption, new_generation)
    flips = []
    for key in sorted(net0.index, key=lambda k: (str(k[0]), k[1], k[2])):
        n0 = net0[key]
        n2 = net2[key]  # net0/net2 share an index by construction -- see _bucket_net
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


def gap_decomposition(d, consumption, generation, new_consumption, new_generation,
                      reduce_from_import, leftover):
    """The ACTUAL, verified mechanism behind the method (a)/(b) reconciliation
    gap (PR #77 review, finding 1 -- corrects an earlier version of this
    module that named sign flips as the sole cause, which an independent
    per-bucket hand decomposition proved false: the sign-flip term is small
    and can even carry the OPPOSITE sign of the total gap).

    The dominant term is PCIA (rates.PCIA, $/kWh) being priced differently by
    the two methods inside a bucket whose sign does NOT change:
      - a bucket that stays net-POSITIVE throughout: monthly netting (method
        b) values an extra exported kWh (leftover) at rates.energy() = credit
        + PCIA, because that kWh is really just offsetting more of the same
        net-positive import. Method (a)'s price_map prices every leftover kWh
        at the plain rates.credit() (no PCIA) regardless of the bucket's
        state -- undervaluing it by PCIA per kWh (contributes -PCIA per kWh
        to gap = a - b).
      - a bucket that stays net-NEGATIVE throughout: netting values an avoided
        -import kWh (reduce_from_import) at rates.credit() (no PCIA), since
        it is really just deepening the same net-negative export. Method (a)
        prices every reduce_from_import kWh at rates.allin() (WITH PCIA)
        regardless of the bucket's state -- overvaluing it by PCIA per kWh
        (contributes +PCIA per kWh to gap = a - b).
    Both effects are exact and linear wherever the bucket's sign does not
    change between baseline and counterfactual (verified: CLAUDE.md section 0
    requires reconciling two disagreeing methods, not merely re-asserting the
    old story with more confidence -- this decomposition is checked directly
    by test_quiet_night_floor.case_reconciliation_gap_is_explained_by_pcia_
    not_sign_flips, which builds a fixture with ZERO sign flips and a nonzero
    gap that ONLY the PCIA mechanism predicts). Buckets whose sign DOES flip
    are genuinely nonlinear here and are not decomposed further -- whatever
    gap remains after subtracting pcia_effect_usd is the sign-flip residual,
    reported alongside sign_flip_buckets rather than folded silently in."""
    net0 = _bucket_net(d, consumption, generation)
    net2 = _bucket_net(d, new_consumption, new_generation)

    key_df = d[["ym", "seas", "p"]].copy()
    keys = list(zip(key_df.ym, key_df.seas, key_df.p))
    net0_row = np.array([net0[k] for k in keys])
    net2_row = np.array([net2[k] for k in keys])

    still_net_positive = (net0_row > 0) & (net2_row > 0)
    still_net_negative = (net0_row < 0) & (net2_row < 0)

    leftover_in_positive_kwh = float(np.asarray(leftover)[still_net_positive].sum())
    reduce_in_negative_kwh = float(np.asarray(reduce_from_import)[still_net_negative].sum())

    pcia_effect_usd = -R.PCIA * leftover_in_positive_kwh + R.PCIA * reduce_in_negative_kwh
    return {
        "leftover_in_still_net_positive_buckets_kwh": round(leftover_in_positive_kwh, 2),
        "reduce_in_still_net_negative_buckets_kwh": round(reduce_in_negative_kwh, 2),
        "pcia_usd_per_kwh": R.PCIA,
        "pcia_effect_usd": round(pcia_effect_usd, 2),
        "formula": ("pcia_effect_usd = -PCIA * leftover_in_still_net_positive_buckets_kwh "
                   "+ PCIA * reduce_in_still_net_negative_buckets_kwh"),
    }


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
            "note": (f"the marginal $/100W at the sensitivity step nearest the "
                    f"measured floor ({floor_kw_measured * 1000:.0f} W rounds "
                    f"to the {current_w} W step) -- an estimate of the rate a "
                    "household near this floor level should expect per "
                    "additional 100 W removed, not the exact marginal at the "
                    "household's own precise wattage (PR #77 review nitpick: "
                    "an earlier version of this note claimed this was "
                    "exactly 'the next 100W', which overstated the precision "
                    "of a value read off a 100 W grid rather than computed "
                    "at the household's own exact floor level)"),
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
        "caveat": ("small confound (PR #77 review nitpick): run_batt's greedy "
                  "EV-spillover gate (kw = imp*4 >= 2.5 kW is treated as "
                  "non-battery-servable house/EV load) is evaluated on EACH "
                  "series' OWN import values, so some intervals become "
                  "servable ONLY in the floor-removed counterfactual purely "
                  "because subtracting the floor pushed their import under "
                  "2.5 kW -- not because of any real behavioral change. "
                  "Freezing the gate on the baseline series instead moves "
                  "this delta by roughly $26/yr in the conservative "
                  "direction (a smaller magnitude reduction in the battery's "
                  "marginal saving); not corrected here since the gate is "
                  "run_batt's own shared, unmodified logic."),
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
                      "floor as home-lab computer systems (report prose, "
                      "index.html section 13; NOT independently recorded in "
                      "any structured data file or TECHNICAL.md -- corrected "
                      "citation, PR #77 review finding 3 caught an earlier "
                      "version wrongly pointing this at TECHNICAL.md 3.11, "
                      "which contains no such attribution either); this "
                      "script does not verify that with a plug-meter study "
                      "or device-level monitoring, and reports the load's "
                      "cost regardless of cause"),
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
    check_price_map_against_extra_results(root, computed_price_map)  # raises on mismatch

    floor_kw = night_stats["median_kw"]
    floor_kwh_per_interval = floor_kw * 0.25
    reduce_i, leftover_i, new_consumption, new_generation = _split_floor(
        consumption, generation, floor_kwh_per_interval)
    violations = floor_assumption_violations(
        d, consumption, generation, floor_kwh_per_interval, computed_price_map)

    method_a = price_method_a(d, computed_price_map, reduce_i, leftover_i)
    method_b, mb_new_c, mb_new_g = price_method_b(d, consumption, generation, reduce_i, leftover_i)
    flips, flipped_kwh = sign_flip_buckets(d, consumption, generation, mb_new_c, mb_new_g)
    decomposition = gap_decomposition(d, consumption, generation, mb_new_c, mb_new_g,
                                      reduce_i, leftover_i)

    gap_usd = round(method_a["total_usd"] - method_b["total_usd"], 2)
    gap_pct = round(100.0 * gap_usd / method_b["total_usd"], 2) if method_b["total_usd"] else None
    sign_flip_residual_usd = round(gap_usd - decomposition["pcia_effect_usd"], 2)

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
            "floor_assumption_violations": violations,
            "reconciliation": {
                "gap_usd": gap_usd,
                "gap_pct": gap_pct,
                "avoided_import_gap_usd": round(
                    method_a["avoided_import_usd"] - method_b["avoided_import_usd"], 2),
                "displaced_export_gap_usd": round(
                    method_a["displaced_export_usd"] - method_b["displaced_export_usd"], 2),
                "gap_decomposition": decomposition,
                "sign_flip_residual_usd": sign_flip_residual_usd,
                "sign_flip_buckets": flips,
                "sign_flip_buckets_kwh": flipped_kwh,
                "explanation": (
                    "the PRIMARY mechanism is PCIA (rates.PCIA, "
                    f"${R.PCIA}/kWh) being priced differently by the two "
                    "methods inside buckets whose net sign does NOT change: "
                    "method (a)'s flat price_map prices every leftover "
                    "(displaced-export) kWh at the plain export credit rate "
                    "and every reduce (avoided-import) kWh at the full "
                    "import rate, regardless of whether that (month, season, "
                    "period) bucket is a net importer or net exporter for "
                    "the month; method (b)'s monthly netting instead prices "
                    "the marginal kWh at whatever the BUCKET's own net sign "
                    "implies, which folds PCIA into a leftover kWh sitting "
                    "inside an already net-positive bucket (undervalued by "
                    "method (a), -PCIA/kWh) and strips PCIA from a reduce "
                    "kWh sitting inside an already net-negative bucket "
                    "(overvalued by method (a), +PCIA/kWh) -- see "
                    "gap_decomposition for the exact kWh on each side and "
                    "CLAUDE.md section 0. This pcia_effect_usd accounts for "
                    f"${decomposition['pcia_effect_usd']} of the "
                    f"${gap_usd} total gap. The SECONDARY, smaller "
                    "contributor is the sign_flip_residual_usd "
                    f"(${sign_flip_residual_usd}): buckets where the "
                    "aggregate net sign genuinely flips between the billed "
                    "year and the counterfactual are nonlinear and not "
                    "decomposed further -- see sign_flip_buckets for exactly "
                    f"which {len(flips)} bucket(s), totalling {flipped_kwh} "
                    "kWh, those are. An earlier version of this module and "
                    "artifact named sign flips as the SOLE cause of this gap; "
                    "an independent review (PR #77) showed that explanation "
                    "was wrong -- the sign-flip term alone is smaller than "
                    "the total gap and can carry the opposite sign -- and "
                    "this explanation replaces it, verified against a "
                    "fixture with zero sign flips and a nonzero gap the PCIA "
                    "mechanism alone predicts "
                    "(test_quiet_night_floor.case_reconciliation_gap_is_"
                    "explained_by_pcia_not_sign_flips)."),
                "scope_of_agreement": (
                    "what this reconciliation does and does not prove: both "
                    "methods start from the IDENTICAL _split_floor allocation "
                    "and draw every rate constant from the SAME rates.py "
                    "module, so their agreement (or this small, now-explained "
                    "gap) validates only the NETTING/AGGREGATION treatment "
                    "(monthly re-bill vs. flat per-interval pricing) -- it "
                    "does not independently validate the rate constants "
                    "themselves (both methods would inherit an error in "
                    "rates.py identically) and it does NOT validate the "
                    "physical floor-allocation model in _split_floor, which "
                    "pricing.floor_assumption_violations shows is where a "
                    "larger, separately-quantified limitation actually "
                    "lives."),
            },
        },
        "sensitivity_per_100w": sensitivity,
        "battery_interaction": battery,
        "confidence_labels": confidence_labels(),
        "notes": {
            "engine": "rates.bill_nem (monthly per-period NEM netting, NBC on gross imports)",
            "quiet_night_method": (
                "a NEW, independently-designed per-night rule (PR #77 review, "
                "finding 3 -- an earlier version wrongly cited this as "
                "inherited from TECHNICAL.md 3.11, which documents no "
                "extraction rule at all, only the phantom key's result "
                "values): 1-5am median import power per calendar night, "
                "excluding any night whose max 1-5am power >= 2 kW. This "
                "per-night rule is NOT the same as TECHNICAL.md 3.5 item 2's "
                "existing per-INTERVAL rule (deep_analyses.py: 3-5am window, "
                "Consumption <= 0.5 kWh per interval, 25th percentile) -- a "
                "per-interval filter can admit a night with a brief high-"
                "demand spike as 'quiet' as long as its OTHER intervals stay "
                "low, letting spillover contaminate the aggregate; a "
                "per-night gate excludes the WHOLE night once any interval in "
                "it crosses the threshold, which is the more appropriate "
                "shape for isolating an all-night continuous floor. The "
                "resulting per-night median/p10/p90 matches the phantom "
                "key's own values closely (see night_floor."
                "cross_check_extra_results_json) even though this script is "
                "the first committed generator for either rule."),
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
