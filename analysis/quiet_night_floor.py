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
   The ladder's `reduction_w` axis counts WATTS REMOVED from the measured
   floor, not a resulting floor LEVEL, so the rate AT the measured floor is
   the FIRST rung (the next 100 W this household could strip), not the rung
   whose number matches the floor's wattage -- reading it the second way
   reports the tenth slice as if it were the first, and makes a household
   with a deeper floor look CHEAPER to improve (issue #173). Rungs above the
   measured floor cannot be fully delivered (`_split_floor` clamps to metered
   import and drops the rest), so every step carries
   `exceeds_measured_floor`, and `marginal_range` reports the reachable
   spread separately from the full-ladder one.

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
    demand-magnitude gate. Classifying nights by "zero EV kWh in the window"
    ALONE (no demand gate at all) gives: 1-5am window 69 (of 365 eligible),
    0-5am 49 (365), 0-6am 42 (365), 1-6am 59 (365), 9pm-6am -- the literal
    "overnight" reading -- 40 (of only 364 eligible: this wrapped window's
    own final calendar date has no "next day" data to read, so it's excluded
    as ineligible, not counted as non-quiet; see issue_114_investigation's
    own n_eligible_nights field) -- several of these land close to 43/44 in
    COUNT, which is the opposite of "falsified": a pure EV-absence rule is a
    live, plausible candidate, not a ruled-out one. But none of these
    EV-free-only variants reproduces phantom's own median/p10 (1.025/0.785
    kW): every one comes out
    higher on both (e.g. the closest-by-count 0-6am variant gives median
    1.08, p10 0.822 -- notably above phantom's 1.025/0.785), so EV-absence
    ALONE is not a sufficient rule either; some further filtering (a demand
    component, a different EV-detection threshold, or something else) would
    still be needed to land on phantom's own reported shape, not just its
    count.
  - A real gate-boundary case exists (verified as committed code, see
    `may_boundary_night` below -- /review found this was still hand-typed
    prose, the exact defect class this whole issue was filed over):
    2026-05-03's 04:45 interval reads exactly 0.500 kWh (2.00 kW at the 4x
    scaling here), landing exactly ON `HIGH_DEMAND_GATE_KW` under this
    script's `>=` comparison, which excludes it. A `>` comparison (or an
    equally defensible rule using a gate a hundredth of a kW higher) would
    flip this one night to quiet -- a concrete illustration of how a single
    interval's exact metered value can move the count by one at this
    threshold. It is NOT confirmed as THE night phantom's original rule kept
    differently: including it moves May's monthly median from 0.845 kW to
    0.85 kW, while phantom's own May figure (monthly_kw["5"]) is 0.845 --
    the same value this script already reproduces exactly without that
    night. Since the issue's own diff of the two artifacts singles out JULY
    as the one month whose median differs (1.04 here vs 1.035 published),
    the missing 44th night -- if there is a single one -- more likely falls
    in July, not May.
  - A 1-5am gate sweep (Codex adversarial review caught this investigation's
    own first-pass claim -- "every other July night's max power sits at 12 kW
    or higher" -- as factually wrong on the committed dataset: 2026-07-09
    peaks at 4.40 kW and 2025-07-25 at 6.64 kW, both well under 12).
    Re-run and actually executed, not hand-estimated: raising the gate DOES
    add a 4th July quiet night, but only at >=4.5 kW, where it also grows the
    TOTAL count to 62 (median 1.05, July median 1.045) -- far past phantom's
    44. The current 2.0 kW gate is the only value tested that keeps the total
    near phantom's 44 (43) while July stays at 3, not 4. Gate values in
    between (2.5-4.4 kW) grow the total (51-61) without ever adding a 4th
    July night. So a SINGLE uniform gate change cannot reach phantom's July
    figure without also blowing the total count far past 44 -- if phantom's
    rule really does differ from this script's specifically in July, it is
    not a simple uniform-gate difference; a per-month or non-gate mechanism
    would be needed, which was not tested here.
  - `phantom` has no lost script to recover: `git log --diff-filter=A` on
    `data/extra_results.json` shows it was added directly as a data file
    (commit 29f8573, "Add soiling, cleaning-study, carbon, and extras data
    outputs") with no accompanying generator, ever, in this repo's history --
    consistent with `analysis/extra_results.py`'s own documentation that
    `phantom` is a one-time in-session computation this repo has no
    reproducible record of.
  Conclusion: the exact night(s) and exact rule responsible for the 44-vs-43
  gap are NOT recoverable from currently available evidence -- honestly
  stated here rather than guessed, and corrected TWICE from this
  investigation's own first pass (Codex adversarial review round 1 caught an
  overclaimed "falsified" verdict from an under-powered test; round 2 caught
  a factually wrong "12 kW or higher" claim used to rule out a demand-gate
  explanation). What IS established with evidence: an EV-session-absence
  rule is a live, count-plausible candidate for phantom's "EV-free"
  description (several tested windows land within a few nights of 43/44),
  and EV-absence alone does NOT reproduce phantom's own median/p10 shape on
  its own (every tested window's median/p10 comes out measurably above
  phantom's 1.025/0.785 kW). What this evidence does NOT establish is WHY --
  whether the original rule combined EV-detection with something else,
  used a different EV-detection method/threshold than `detect_sessions`
  entirely, or was not EV-based at all despite the "EV-free" label. The
  available evidence bounds the space of plausible explanations; it does not
  select a single one, and this docstring does not claim otherwise. No
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


# Boundary/sweep values quoted in this module's own docstring (issue #114's
# investigation) and in TECHNICAL.md's 3.28 -- committed here as real code,
# not hand-typed prose, after two rounds of Codex adversarial review each
# caught a factual error in a hand-computed claim that had no committed
# script backing it (CLAUDE.md section 9: "a script per headline number").
EV_ABSENCE_WINDOWS = ((1, 5), (0, 5), (0, 6), (1, 6), (21, 6))
JULY_GATE_SWEEP_KW = (2.0, 2.5, 3.0, 3.5, 4.0, 4.4, 4.5, 5.0, 6.0, 6.64, 7.0)


def issue_114_investigation(d):
    """Issue #114: reproduces, as real executed code (not prose), every
    number this module's own docstring and TECHNICAL.md 3.28 cite about the
    43-vs-44 quiet-night discrepancy -- so those figures can be pinned by a
    test and can't silently go stale or be wrong the way this investigation's
    own first two hand-computed passes each were (both caught by Codex
    adversarial review, not by any test, because no test existed).

    `ev_absence_by_window`: for each (start_h, end_h) in EV_ABSENCE_WINDOWS
    (a window where `start_h > end_h`, e.g. (21, 6), wraps to the next
    calendar day, matching "9pm-6am"), the count of nights with ZERO
    EV-session kWh in that window -- classified independently of this
    module's own HIGH_DEMAND_GATE_KW gate, i.e. a pure EV-absence rule, not
    this script's demand-magnitude rule. Each entry's own `n_eligible_nights`
    states its real denominator: 365 for every non-wrapped window, but only
    364 for the wrapped one, whose own final calendar date has no "next day"
    to read (see `n` below) -- do not assume every window's `n` is out of
    the same 365. Includes median/p10 for the closest-by-count (0-6am)
    window, since that's the one the docstrings cite by shape, not just
    count.

    `july_gate_sweep`: for each gate in JULY_GATE_SWEEP_KW, applied to this
    module's own 1-5am-window/demand-gate rule (not the EV-absence rule
    above), the resulting total quiet-night count and July-only count/median
    -- the evidence for why a uniform gate change can't reach a 4th July
    quiet night without also overshooting the total count.

    `july_boundary_nights`: the two real nights (2026-07-09, 2025-07-25)
    whose 1-5am max power an earlier draft of this investigation wrongly
    claimed were "12 kW or higher" -- their real values, from this same
    run, so that specific error can never be silently reintroduced.

    `may_boundary_night`: the 2026-05-03 gate-boundary case this
    investigation's own docstring cites by hand (04:45's exact 1-5am kWh
    reading, and this script's real May quiet-night median with and without
    that night included) -- committed as real code (/review found this one
    was still hand-typed prose, the exact defect class this whole issue was
    filed over) rather than left unverified."""
    d = d.copy()
    d["date"] = d.dt.dt.date
    d["kw"] = d.Consumption.astype(float) * 4.0
    ev, _ = br.detect_sessions(d)
    d["evkw"] = ev

    all_dates = set(d["date"].unique())

    def ev_free_count(start_h, end_h):
        # Codex review round 1: the wrapped (21, 6)-style window's LAST
        # calendar date in the dataset has no "next day" data to read (the
        # archive ends at midnight on its own final date), so its own window
        # is truncated (observed: 2026-07-23 has 12 intervals, not the full
        # 36) -- counting that as a complete "EV-free night" would be an
        # artifact of where the data happens to end, not a real observation.
        #
        # Codex review round 2: a FIXED "hours * 4" expected-interval count
        # is the wrong test for that -- it also rejects genuine, complete
        # DST-transition nights (2025-11-02 fall-back has 20 real intervals
        # in a nominal 4-hour window; 2026-03-08 spring-forward has 12),
        # which are real, complete observations that happen to be a
        # different wall-clock length that day, not missing data. The two
        # cases look identical by interval COUNT alone but need opposite
        # handling, so check the actual cause instead: for a wrapped window,
        # exclude only when the NEXT calendar date isn't in the dataset at
        # all (the real archive-boundary case); DST-shortened/lengthened
        # nights always have their next date present and are kept, matching
        # night_floor_series()'s own convention of using whatever real
        # intervals a night actually has.
        wraps = start_h > end_h  # e.g. (21, 6) means 9pm today through 6am tomorrow
        free = []
        eligible = 0
        for date, g in d.groupby("date"):
            if wraps:
                if (date + dt.timedelta(days=1)) not in all_dates:
                    continue  # real archive-boundary truncation, not DST -- not eligible
                mask = ((d["date"] == date) & (d["hour"] >= start_h)) | \
                       ((d["date"] == date + dt.timedelta(days=1)) & (d["hour"] < end_h))
                night = d[mask]
            else:
                night = g[(g["hour"] >= start_h) & (g["hour"] < end_h)]
            if night.empty:
                continue
            eligible += 1
            if night["evkw"].sum() == 0:
                free.append((date, float(night["kw"].median())))
        return free, eligible

    ev_absence_by_window = {}
    for start_h, end_h in EV_ABSENCE_WINDOWS:
        wraps = start_h > end_h
        label = f"{start_h}-{end_h}h" + ("(+1d)" if wraps else "")
        free, eligible = ev_free_count(start_h, end_h)
        # Codex review round 3: the wrapped window's own archive-boundary
        # exclusion (above) means its `n` is drawn from 364 eligible nights,
        # not the full 365 -- reporting `n` alone next to the other windows'
        # 365-night counts would silently compare different denominators.
        # `n_eligible_nights` makes that explicit rather than letting a
        # reader assume every window's denominator is the same 365.
        entry = {"n": len(free), "n_eligible_nights": eligible}
        if start_h == 0 and end_h == 6:  # the closest-by-count window the docstrings cite by shape
            meds = np.array([m for _, m in free])
            entry["median_kw"] = round(float(np.median(meds)), 4)
            entry["p10_kw"] = round(float(np.percentile(meds, 10)), 4)
        ev_absence_by_window[label] = entry

    def gate_sweep_row(gate_kw):
        quiet = []
        for date, g in d.groupby("date"):
            night = g[(g["hour"] >= 1) & (g["hour"] < 5)]
            if not night.empty and night["kw"].max() < gate_kw:
                quiet.append((date, float(night["kw"].median())))
        meds = np.array([m for _, m in quiet])
        july = np.array([m for dd, m in quiet if dd.month == 7])
        return {
            "gate_kw": gate_kw,
            "n_total": len(quiet),
            "median_kw": round(float(np.median(meds)), 4) if len(meds) else None,
            "n_july": len(july),
            "median_july_kw": round(float(np.median(july)), 4) if len(july) else None,
        }

    def night_max_1_5am(date_str):
        target = dt.date.fromisoformat(date_str)
        night = d[(d["date"] == target) & (d["hour"] >= 1) & (d["hour"] < 5)]
        return round(float(night["kw"].max()), 2) if not night.empty else None

    def may_boundary_night():
        """This module's own gate (HIGH_DEMAND_GATE_KW, the SAME rule
        night_floor_series() uses, not the EV-absence or July-sweep rules
        above): 2026-05-03's 04:45 interval and its effect on May's own
        quiet-night median if that one night's `>=` gate exclusion were
        relaxed to `>`."""
        target = dt.date(2026, 5, 3)
        night = d[(d["date"] == target) & (d["hour"] >= 1) & (d["hour"] < 5)]
        if night.empty:
            return None
        interval_045 = night[night["hour"] == 4.75]  # `hour` is a fractional-hour
                                                     # float (4.75 == 4:45), not a
                                                     # separate hour/minute pair
        max_kw = float(night["kw"].max())
        median_kw = round(float(night["kw"].median()), 4)

        daily_series, _ = night_floor_series(d)
        may_meds_without = [r["median_kw"] for r in daily_series
                            if r["date"].startswith("2026-05") and r["median_kw"] is not None]
        may_median_without = round(float(np.median(may_meds_without)), 4) if may_meds_without else None
        may_median_with = round(float(np.median(may_meds_without + [median_kw])), 4) \
            if may_meds_without else None

        # /review found the prose's "this ONE night" phrasing (below) was an
        # unasserted uniqueness claim -- computed here, not just eyeballed,
        # so a future archive regeneration that adds a second night at
        # exactly the gate value elsewhere in the year fails the pin instead
        # of silently falsifying the docstring's "one night" framing.
        n_at_exact_gate = sum(
            1 for date, g in d.groupby("date")
            if not (n2 := g[(g["hour"] >= 1) & (g["hour"] < 5)]).empty
            and float(n2["kw"].max()) == HIGH_DEMAND_GATE_KW
        )

        return {
            "interval_04:45_kwh": round(float(interval_045["kw"].iloc[0]) / 4, 4)
                                 if not interval_045.empty else None,
            "night_max_kw": round(max_kw, 4),
            "night_median_kw_if_included": median_kw,
            "gate_kw": HIGH_DEMAND_GATE_KW,
            "excluded_under_current_gte_gate": max_kw >= HIGH_DEMAND_GATE_KW,
            "n_nights_at_exact_gate": n_at_exact_gate,
            "may_median_kw_without_this_night": may_median_without,
            "may_median_kw_with_this_night": may_median_with,
        }

    return {
        "ev_absence_by_window": ev_absence_by_window,
        "july_gate_sweep": [gate_sweep_row(g) for g in JULY_GATE_SWEEP_KW],
        "may_boundary_night": may_boundary_night(),
        "july_boundary_nights": {
            "2026-07-09": night_max_1_5am("2026-07-09"),
            "2025-07-25": night_max_1_5am("2025-07-25"),
        },
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
    """Re-bill (method b) at every 100 W step and report what each additional
    100 W of floor removal is worth.

    THE LADDER'S AXIS IS A REMOVAL, NOT A LEVEL. `reduction_w` counts WATTS
    SUBTRACTED from the household's measured floor: the 100 W rung is the FIRST
    100 W taken off the floor as it stands today, the 1000 W rung is the tenth
    such slice (a household that already stripped 900 W). The two readings are
    opposite ends of the same curve, and confusing them inverts the answer --
    an earlier version of this function mapped the measured floor LEVEL onto
    the removal axis (1030 W -> the 1000 W rung) and so published the marginal
    for the TENTH 100 W as "the rate at the current floor". The tell was that a
    household with a DEEPER floor got a LOWER reported rate. The rate a
    household at the measured floor should expect for its NEXT 100 W is
    steps[0]'s marginal, so `usd_per_100w_at_current_floor` reads the ladder's
    SMALLEST rung (issue #173).

    RUNGS ABOVE THE MEASURED FLOOR ARE NOT FULLY DELIVERED. `_split_floor`
    clamps each interval's requested reduction to that interval's metered
    import and DROPS the remainder wherever generation == 0 (see its docstring
    and floor_assumption_violations). A rung that asks for more watts than the
    house actually draws therefore removes less energy than it requests, and
    its marginal is a diluted average of real removal plus discarded energy --
    NOT tariff curvature. On the measured year the 1200 W rung drops ~4.6% of
    the requested kWh and clamps ~53% of intervals, against ~0.01% dropped at
    the 100 W rung. MAX_REDUCTION_W deliberately overshoots to bracket the
    floor with headroom, so the fix is to FLAG the overshoot, not to shorten
    the ladder: every step carries `exceeds_measured_floor`, `marginal_range`
    reports the reachable and full-ladder spreads separately, and
    `linearity_note` gives the deviation for both ranges."""
    steps = []
    prev_savings = 0.0
    measured_floor_w = int(round(floor_kw_measured * 1000))
    for w in range(STEP_W, MAX_REDUCTION_W + STEP_W, STEP_W):
        floor_kwh = (w / 1000.0) * 0.25
        reduce_i, leftover_i, new_c, new_g = _split_floor(consumption, generation, floor_kwh)
        f0 = d.copy(); f0["imp"] = consumption; f0["exp"] = generation
        f2 = d.copy(); f2["imp"] = new_c; f2["exp"] = new_g
        savings = br.bill(f0, "imp", "exp") - br.bill(f2, "imp", "exp")
        marginal = savings - prev_savings
        steps.append({"reduction_w": w, "annual_savings_usd": round(savings, 2),
                     "marginal_usd_per_100w": round(marginal, 2),
                     "exceeds_measured_floor": bool(w > measured_floor_w)})
        prev_savings = savings

    ws = np.array([s["reduction_w"] for s in steps], dtype=float)
    savings_arr = np.array([s["annual_savings_usd"] for s in steps])
    slope, intercept = np.polyfit(ws, savings_arr, 1)
    fit = slope * ws + intercept
    max_dev = float(np.max(np.abs(savings_arr - fit)))
    max_dev_pct = round(100.0 * max_dev / savings_arr[-1], 2) if savings_arr[-1] else 0.0

    # The rungs the metered load can actually supply: everything at or below the
    # measured floor. Never empty -- a floor below one step still gets steps[0],
    # which is the rung usd_per_100w_at_current_floor reports.
    reachable = [s for s in steps if not s["exceeds_measured_floor"]] or steps[:1]
    r_ws = np.array([s["reduction_w"] for s in reachable], dtype=float)
    r_savings = np.array([s["annual_savings_usd"] for s in reachable])
    if len(reachable) >= 2:
        r_slope, r_intercept = np.polyfit(r_ws, r_savings, 1)
        r_fit = r_slope * r_ws + r_intercept
        r_max_dev = float(np.max(np.abs(r_savings - r_fit)))
        r_max_dev_pct = round(100.0 * r_max_dev / r_savings[-1], 2) if r_savings[-1] else 0.0
        reachable_slope_usd = round(float(r_slope) * 100, 2)
        reachable_linearity = (f"over the reachable {STEP_W}-{int(r_ws[-1])} W rungs alone "
                              f"the max deviation is ${r_max_dev:.2f} ({r_max_dev_pct}% of "
                              f"savings at {int(r_ws[-1])} W)")
    else:
        reachable_slope_usd = None
        reachable_linearity = ("the reachable range holds a single rung, so it "
                              "admits no linear fit and no deviation figure")

    at_floor = steps[0]
    r_lo = min(reachable, key=lambda s: s["marginal_usd_per_100w"])
    r_hi = max(reachable, key=lambda s: s["marginal_usd_per_100w"])
    f_lo = min(steps, key=lambda s: s["marginal_usd_per_100w"])
    f_hi = max(steps, key=lambda s: s["marginal_usd_per_100w"])

    return {
        "basis": (f"method b (full monthly re-bill) at {STEP_W} W steps from "
                 f"{STEP_W} to {MAX_REDUCTION_W} W"),
        "measured_floor_w": measured_floor_w,
        "steps": steps,
        "usd_per_100w_at_current_floor": {
            "reduction_w": at_floor["reduction_w"],
            "value_usd": at_floor["marginal_usd_per_100w"],
            "note": (f"the marginal $/100W for the FIRST {STEP_W} W removed from "
                    f"the floor as measured ({measured_floor_w} W) -- the rate "
                    "this household should expect for the next 100 W it strips, "
                    "which is what a household standing at its own measured "
                    "floor is asking. `reduction_w` counts watts REMOVED from "
                    "that floor, not a resulting floor level: this is the "
                    "ladder's first slice, and the ladder's later rungs price "
                    "DEEPER removal (the 1000 W rung is the tenth 100 W, priced "
                    "for a household that already stripped 900 W), not this "
                    "one. See marginal_range for how far the rate moves across "
                    "the ladder."),
        },
        "marginal_range": {
            "reachable": {
                "min_usd": r_lo["marginal_usd_per_100w"],
                "min_at_reduction_w": r_lo["reduction_w"],
                "max_usd": r_hi["marginal_usd_per_100w"],
                "max_at_reduction_w": r_hi["reduction_w"],
                "through_reduction_w": reachable[-1]["reduction_w"],
            },
            "full_ladder": {
                "min_usd": f_lo["marginal_usd_per_100w"],
                "min_at_reduction_w": f_lo["reduction_w"],
                "max_usd": f_hi["marginal_usd_per_100w"],
                "max_at_reduction_w": f_hi["reduction_w"],
            },
            "note": ("the spread of the per-100W marginal, so the non-linearity "
                    "is readable without recomputing it from `steps`. The "
                    "REACHABLE range covers only the rungs at or below the "
                    f"measured floor ({measured_floor_w} W) -- the removal the "
                    "metered load can actually supply. The FULL LADDER also "
                    "includes rungs above that floor, where _split_floor clamps "
                    "the request to each interval's metered import and drops "
                    "the remainder: those rungs remove less energy than they "
                    "ask for, so the full-ladder minimum is depressed by "
                    "discarded energy and is not a tariff effect. Read the "
                    "reachable range for what this household faces; read the "
                    "full ladder only with that clamping in mind."),
        },
        "usd_per_100w_general_average": {
            "value_usd": round(float(slope) * 100, 2),
            "reachable_slope_usd": reachable_slope_usd,
            "note": (f"linear-fit slope across the full {STEP_W}-{MAX_REDUCTION_W} W "
                    "range -- a general marginal rate, not specific to the "
                    "current floor level. This fit spans rungs that exceed the "
                    f"measured floor ({measured_floor_w} W), where _split_floor "
                    "drops the energy the metered load cannot supply, so the "
                    "value is pulled DOWN by rungs the household cannot reach. "
                    "reachable_slope_usd is the same fit over the reachable "
                    "rungs only."),
        },
        "linearity_note": (
            f"max deviation from the linear fit is ${max_dev:.2f} "
            f"({max_dev_pct}% of total savings at {MAX_REDUCTION_W} W) across the "
            f"full {STEP_W}-{MAX_REDUCTION_W} W ladder -- " +
            ("effectively linear over this range" if max_dev_pct < 2
             else "measurably nonlinear; see sign_flip_buckets for why") +
            f". That full-ladder figure includes rungs above the measured floor "
            f"({measured_floor_w} W), whose marginals are diluted by energy "
            f"_split_floor drops rather than by tariff curvature; " +
            reachable_linearity + "."),
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
    issue_114 = issue_114_investigation(d)

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
                        "cross_check_extra_results_json": night_cross_check,
                        "issue_114_investigation": issue_114},
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
    at_floor = sensitivity["usd_per_100w_at_current_floor"]
    print(f"sensitivity: ${at_floor['value_usd']}/yr for the first "
         f"{at_floor['reduction_w']} W removed from the measured "
         f"{sensitivity['measured_floor_w']} W floor (the "
         f"{at_floor['reduction_w']} W rung)")
    print(f"battery interaction: {battery['delta_usd']}/yr ({battery['delta_pct']}%)")


if __name__ == "__main__":
    main()
