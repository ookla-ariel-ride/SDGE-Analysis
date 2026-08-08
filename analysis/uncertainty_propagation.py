#!/usr/bin/env python3
"""Comprehensive uncertainty propagation for the battery decision (issue #15).

WHY THIS SUPERSEDES, BUT MUST STILL REPRODUCE, deep_analyses.py's Monte Carlo
-----------------------------------------------------------------------------
deep_analyses.py's `monte_carlo` block draws exactly THREE inputs (annual rate
escalation, battery capacity fade, install cost) around a single point base
case (`post_behavior.mid.battery_marginal`) and reports a payback distribution
plus a 10-yr NPV at one discount rate. That is real uncertainty propagation,
but it is not the uncertainty this project has actually measured: issue #4's
tou_spread.py found the escalation TREND itself "not determined" (a structural
break, not a trend — see data/tou_spread.json's per_period verdicts), the
three-way production validation (data/threeway_production_validation.csv)
shows two independent monitoring sources disagreeing on the same physical
production by several percent, the soiling analysis (data/soiling_results.json)
has two genuinely different rate estimates depending on which evidence window
you trust, round-trip efficiency is a nameplate spec never independently
measured here, and the battery-marginal base case itself is conditional on an
EV-charging behavior shift persisting, which is a real, unquantified risk.

This script draws all SEVEN of those inputs (see INPUT DISTRIBUTIONS below),
propagates them through the same physical battery-payback question, and
reports the answer as a probability distribution rather than a point estimate.
It does NOT replace deep_analyses.py or its artifact (both stay byte-identical
— this script never imports or edits deep_analyses.py) — it supersedes the
DECISION-RELEVANT USE of the old Monte Carlo, while reproducing it exactly as
a verified special case (see `legacy_reproduction()` and
test_uncertainty_propagation.py's dedicated case) when its OWN inputs are
restricted to the old 3-input set with every other lever pinned inert.

INPUT DISTRIBUTIONS AND THEIR EVIDENTIAL BASIS
-----------------------------------------------------------------------------
1. escalation           Uniform(0.00, 0.12) — ESTIMATED (a bounding scenario
   range), NOT measured. data/tou_spread.json's own per_period.summer/winter
   verdicts are BOTH "not determined": the apparent escalation series is
   carried by a single structural break, not a survives-scrutiny trend. What
   IS in that artifact is battery.uniform_ladder, a bounding scenario ladder
   at 3/5/8/12%; this band spans the ladder's full range (0% floor kept from
   deep_analyses.py's own existing 0-10% band; 12% ceiling taken from the
   ladder's own top scenario, asserted equal to it below so a future
   regeneration of tou_spread.json cannot drift silently out of sync with
   this script's hardcoded ceiling).
2. degradation (fade)   Uniform(0.005, 0.025)/yr — carried forward UNCHANGED
   from deep_analyses.py's own Monte Carlo. Evidential basis: manufacturer
   (Tesla Powerwall 3) warranty degradation curve. This is BATTERY capacity
   fade, not solar panel degradation (~0.5-1.0%/yr, a separate, already
   published figure — index.html §9 — that answers a different question and
   is deliberately NOT one of the seven inputs here).
3. install cost         Uniform(12500, 17000) — carried forward UNCHANGED.
   Evidential basis: quoted installer cost bound; no better evidence exists.
4. EV behavior persistence   Beta(2, 1) compliance fraction c in [0, 1],
   blending the battery_dispatch_policies.json PRE-behavior marginal saving
   (pw3.greedy.save, the battery serving the UNSHIFTED load, c=0) and the
   POST-behavior marginal saving (post_behavior.mid.battery_marginal, the
   battery serving the load AFTER the EV-shift behavior holds, c=1) — the
   ONLY two compliance points the pipeline actually computes; no continuum of
   partial compliance is measured. This is a MODELED, not-yet-implemented
   change: §7 of the report recommends the EV-charging fix as a still-pending
   action ("do it this week"), not something this household has actually
   sustained -- an earlier draft of this docstring wrongly called it "already
   an OBSERVED, completed behavior" to justify a more confident Beta(4,1)
   prior (mean 0.8), which overstated what evidence actually supports (Codex
   review pass 3 finding). What IS real, if indirect, evidence: this
   household's OWN baseline (unshifted) Green Button record already shows
   most EV charging lands in favorable windows without any intervention --
   only 2,618 of ~13,100 kWh/yr of EV charging is currently mis-timed
   (on-peak or daytime off-peak; see behavior_rebuild.py's own session
   detection, reported in index.html §7), i.e. ~80% is already fine, so only
   a MINORITY of sessions need to change. That weakly supports expecting
   better-than-a-coin-flip compliance for a low-effort, largely-already-
   adopted pattern, but is not proof the specific remaining shift will be
   sustained. Beta(2,1) (mean 0.667, mode 1.0) is a milder skew than the
   retired Beta(4,1), reflecting that weaker, indirect basis. ESTIMATED — no
   repeated-year compliance measurement exists to calibrate the shape itself.
5. soiling / production loss   Triangular(low=0, mode=0, high=lossB), where
   lossB = (annual_lost_kwh under data/soiling_results.json's scenario_B_
   2024_cleaning_evidence MINUS annual_lost_kwh under scenario_A_this_years_
   evidence) / annual_generation_kwh -- an INCREMENTAL fraction, not scenario
   B's raw loss. gen0 (the Green Button Generation column) is THIS YEAR'S
   actual, already-soiled measured production; scenario A IS "this year's
   evidence", so gen0 already reflects roughly scenario A's own loss. An
   earlier draft scaled gen0 by scenario A's or B's RAW loss fraction, which
   subtracted scenario A's already-embedded loss a second time (Codex review
   pass 2 finding). The distribution's low/mode of 0 represents "soiling
   holds at roughly its current (scenario A) rate" (the observed baseline
   itself); its upper tail represents "soiling worsens further, to scenario
   B's dirtier rate" -- the report's own "split evidence" characterization,
   reframed as a distance FROM the observed state rather than two competing
   absolute loss levels applied to the same series. This fractional
   generation loss is converted into a battery-marginal-saving derate via
   `soiling_factor()`, calibrated from REAL dispatch reruns (see CALIBRATION
   below), not an assumed proportionality.
6. round-trip efficiency (RTE)   Uniform(0.85, 0.95). No independent RTE
   measurement exists in this repo for this household's Powerwall 3;
   battery_dispatch_policies.py hardcodes the nameplate ETA = sqrt(0.90) (90%
   round trip). This is an ENGINEERING ESTIMATE band (+/- 5 points around the
   manufacturer spec, covering spec-sheet tolerance and thermal/age effects),
   explicitly not measured from this household's data, converted into a
   saving derate via `rte_factor()` (see CALIBRATION below).
7. production measurement spread   Normal(mean=1.0, sd=PROD_SIGMA), a factor
   on TRUE generation (uncertainty about which of the two meters is closer to
   the real output). Routed through the SAME calibrated soiling slopes as
   soiling itself -- soil_slope_loss for a measured-generation shortfall,
   soil_slope_surplus for a measured-generation surplus (issue #89; see
   CALIBRATION below and save1_of()'s own docstring) -- not applied as a
   direct 1:1 multiplier on the dollar saving: a production measurement
   discrepancy and a soiling-driven generation change are uncertainty about
   the identical physical quantity (how much the array actually generated),
   and soiling's own calibration already measured how weakly a generation-
   level change moves the battery's marginal saving (adversarial review
   pass 2, finding 2 — an earlier draft assumed a full 1:1 response,
   overstating this lever's impact by roughly 1/soil_slope).
   PROD_SIGMA is computed AT RUNTIME from
   data/threeway_production_validation.csv (365 days, two independent
   monitoring sources for the same array — PVOutput and the Enphase meter):
   the ANNUAL relative difference between the two full-year totals, not the
   larger day-to-day relative difference, because the annual gap tracks the
   MEAN daily gap rather than shrinking by 1/sqrt(365) — evidence the two
   meters disagree SYSTEMATICALLY (a persistent calibration/loss-accounting
   gap) rather than each day being an independent noisy draw that would
   average out. Both statistics are recorded in the output for inspection.

ISSUE #59: TWO SCOPE QUESTIONS FROM #15'S REVIEW, RESOLVED
-----------------------------------------------------------------------------
#15's adversarial review (pass 3 of 3) raised two further questions, ruled out
of that issue's scope box and tracked separately here. Both resolve to "no
change to the model" — this section states why, with the evidence checked,
rather than leaving the gap undocumented.

(a) DISPATCH-POLICY ADHERENCE RISK. This is a DIFFERENT question from the
"dispatch_policy" already addressed in reconcile_tornado()'s notes above (the
household's CHOICE among evening/twowin/greedy, correctly held fixed at
greedy as a decision, not an uncertain input) — this is whether, having
CHOSEN greedy, the Powerwall's own automation actually EXECUTES it reliably.
Real-world software/automation can fail to follow its configured schedule
(app settings not saving, a unit needing a manual reboot, etc.). Checked
(WebSearch, 2026-08) for a citable number to build a distribution from
against these specific sources, none of which quantifies Powerwall dispatch-
schedule adherence:
  - solarinsure.com/tesla-powerwall-reliability-study — the one quantitative
    Powerwall "reliability" figure found (a ~0.93% failure rate) is a
    warranty-claims HARDWARE failure rate (unit died / needed replacement),
    not a measure of whether a working unit follows its own schedule, and
    carries no disclosed sample size.
  - calmac.org/publications/PY2024_SCE_DR_Program_Report_ELRP_FINAL_PUBLIC.pdf
    — CPUC's ELRP demand-response load-impact evaluation computes
    realization rates but aggregates across technologies without isolating
    residential battery storage.
  - A PG&E-sponsored residential-battery VPP pilot study (dret-ca.com,
    "PGE-Residential-Battery-as-Virtual-Power-Plant-VPP-Study.pdf") looked
    like a plausible source but no dispatch-compliance/no-show metric could
    be confirmed in it.
  - No Tesla-published spec, and no Wood Mackenzie or EnergySage report,
    addressing this specific question was found. What exists beyond the
    above is anecdotal (forum reports of app/automation glitches, a 2025
    Powerwall-2-recall class action over unrelated OTA charge-limiting) with
    no adherence rate attached to any of it.
DECISION: not modeled. There is no number here to build a distribution from
without inventing one, which CLAUDE.md §0 prohibits — this is the "not
determined, state what would settle it" case, not a "model it anyway" case,
bounded to the sources actually checked above, not a claim that no such
number could ever exist anywhere. What would settle it: a published
dispatch-compliance/no-show metric from Tesla, a utility VPP program, or an
independent monitoring study.

(b) TWO-SIDED ESCALATION DISTRIBUTION. `esc` in payback_of()/npv_of() below
scales save1 (the battery's marginal SAVING, itself proportional to the
on-peak/off-peak/super-off-peak SPREAD it arbitrages) by a SINGLE uniform
factor (1+esc)^yr — i.e. it models the SPREAD/margin structure scaling
proportionally at one blended rate, not each TOU period escalating at its
own independently-measured rate. The decision-relevant question is
therefore whether the SPREAD trend (not any one period's own absolute
level) is measurably rising, falling, or undetermined — and
data/tou_spread.json is this repo's own dedicated, structural-break-tested
tool for exactly that question. Its verdict: "not determined" in BOTH
seasons (battery.per_period.summer/winter). That is a genuine "unknown, not
zero, not positive" finding for the spread itself, not evidence for either
a positive OR a zero-floored distribution.
  AN EARLIER DRAFT OF THIS SECTION GOT THIS WRONG (Codex adversarial
  review, issue #59, first pass) — it argued per-TOU-cell ABSOLUTE-level
  trends (on-peak delivery rates rising, individually, with CIs excluding
  zero) as evidence that keeping the 0% floor was itself evidence-backed.
  That conflates "the price level households pay in each period has risen"
  with "the arbitrage margin a battery captures has risen" — two different
  quantities. Winter on-peak and winter off-peak, for example, moved on
  nearly identical trajectories over the same window (rate_first $0.26687,
  rate_last $0.31174 for BOTH; escalation_pct_yr 11.3%/yr vs 12.06%/yr,
  nearly the same rate), which is exactly a case where both periods rising
  together leaves the SPREAD between them roughly flat rather than
  widening — the per-cell reasoning, applied uniformly, does not actually
  establish the spread rose. That claim is RETRACTED here.
  What the per-cell data DOES still show, honestly stated: this repo has
  no measured evidence, at ANY level of rigor, of a genuinely NEGATIVE
  spread/margin trend for this household's own tariff. The composite
  spread-level finding is "not determined" (unknown direction, not a
  negative estimate); the individual TOU cells with CIs excluding zero all
  point to rising absolute levels, which is at least consistent with a
  non-shrinking (if not provably widening) spread, and none shows a
  significant absolute decline except super-off-peak, whose own CI crosses
  zero and which is a charging-cost (not arbitrage-margin) period besides.
  External precedent (WebSearch, 2026-08, not previously in this repo):
  California IOU electric rates HAVE fallen in a real, documented, multi-
  year episode — PG&E's residential rates dropped in several separate
  2024-2025 rate actions (~$12/mo lower by Oct 2025 for a typical customer):
  pge.com/en/newsroom/currents/energy-savings/pg-e-electric-bills-down-
  from-last-year--expected-to-drop-again-.html; nasdaq.com/press-release/
  pge-lower-electric-prices-jan-1-fourth-decrease-two-years-2025-12-30.
  Driven by an IDENTIFIED, specific mechanism: AB 1054 wildfire-safety
  capital costs rolling off the rate base (docs.cpuc.ca.gov/PublishedDocs/
  Efile/G000/M523/K181/523181110.PDF). This shows a CA IOU rate decline
  is a real, not hypothetical, kind of event, with a real, citable
  magnitude attached to it. But the mechanism is not yet shown to apply
  comparably to SDG&E specifically — the same research found SDG&E's
  electric delivery rate rose (+0.5c/kWh) over a comparable recent window
  even as its gas transportation rate fell, and a 2023 SDG&E residential
  rate-DESIGN proposal showing a small (-0.3%) change was a revenue-neutral
  reallocation proposal, not a confirmed realized bill decrease. PG&E's own
  magnitude is therefore not validly transferable to an SDG&E-specific
  negative-escalation bound: using it would fabricate an SDG&E number from
  a different utility's evidence, which CLAUDE.md §0 prohibits exactly as
  much as inventing one from nothing.
  DECISION: the existing Uniform(0.00, 0.12) floor-at-zero convention is
  KEPT, but honestly re-labeled: it is an INHERITED assumption (from
  deep_analyses.py's original design), NOT one this repo's evidence proves
  correct. No rigorous SDG&E-specific negative-spread magnitude exists
  anywhere in this repo or in the external research done for this issue to
  build a defensible lower band from; replacing an inherited-but-unproven
  zero floor with a DIFFERENT, fabricated negative number (whether
  self-invented or borrowed from PG&E's unrelated mechanism) would trade
  one unevidenced assumption for another, not improve on it. This is
  explicitly a LIMITATION of the current model, stated as such rather than
  silently carried forward: what would settle it is a season-and-TOU-period
  battery-savings trend computed by rerunning the actual dispatch/billing
  engine at each period's own separately-measured historical rate path (not
  a single blended scalar), or a longer bill corpus that lets tou_spread.py's
  structural-break test resolve the spread trend with adequate power. Both
  are out of this issue's own scope box (a dispatch-rerun-based escalation
  model is a design change to how `esc` works, not a distribution-tuning
  fix); filed as a follow-up (issue #87) rather than attempted here.
  ISSUE #87 RESOLUTION: investigated implementing the per-TOU-period model.
  data/tou_spread.json's own delivery_cell_escalation resolves ON-PEAK to a
  tight, zero-excluding, POSITIVE trend in both seasons (winter 11.37%/yr,
  CI [7.75, 15.11], r-squared 0.973; summer 7.66%/yr, CI [1.73, 13.94]).
  SUPER-OFF-PEAK -- the charging leg, and the other side of the arbitrage
  spread a battery captures -- has a large NEGATIVE point estimate in both
  seasons (~-21%/yr) but a CI so wide it crosses zero by a wide margin
  ([-61.89, 62.02] summer, [-49.24, 20.68] winter): not a resolved trend,
  just a noisy one. (summer_off_peak is separately unresolved: 1 vintage.)
  Combining a confidently-known on-peak trend with an unresolved
  super-off-peak point estimate to build a per-period spread trend would
  manufacture a specific-looking widening number that is really just
  whichever central estimate the noisy leg happened to land on -- exactly
  the per-cell-as-clean-input error the (b) retraction above already caught
  once, applied to a trend instead of a level. The spread-level
  structural-break test -- which differences the two legs directly, so
  common-mode shocks cancel and the wide super-off-peak uncertainty carries
  straight through rather than getting hidden inside a confident-looking
  on-peak number -- is the honest version of this same question, and it
  already can't resolve a trend from the same underlying data (only 3
  independent post-break price levels in summer, 4 in winter; too few to
  both locate a breakpoint and estimate a slope). A per-period model built
  from these per-cell trends would not be mathematically equivalent to
  `esc`'s existing blended scalar -- the two legs' point estimates plainly
  differ -- but it would inherit AT LEAST as much uncertainty as the direct
  spread test already found inadequate, since super-off-peak's own
  wide-crossing-zero CI is the actual bottleneck either way. Building it
  now would trade one blended, honestly-labeled-as-unproven scalar for a
  differently-shaped but no-more-resolved number. Resolved as AC1's
  documented-gap branch: a longer bill corpus (tou_spread.py's own
  estimate: reaching into 2028 would roughly double the independent units)
  is needed before this is worth building. Guarded by
  test_uncertainty_propagation.py's case_spread_trend_is_still_not_
  determined_so_esc_stays_a_blended_scalar, which reads tou_spread.json's
  own verdict and post_break.adequate fields and FAILS once the corpus
  grows enough to resolve them -- that failure, not a calendar date, is the
  signal to revisit this with a real per-period model.

CALIBRATION: RTE- AND SOILING-SAVING SENSITIVITY, FROM THE REAL ENGINE
-----------------------------------------------------------------------------
Rather than assume a linear/proportional relationship between RTE (or
soiling-driven generation loss) and the battery-marginal saving, this script
reruns the ACTUAL dispatch engine (battery_dispatch_policies.run_batt +
.billed, imported read-only — never edited, never monkeypatched permanently)
at a small number of real parameter values: RTE in {0.85, 0.90, 0.95} and
generation scaled by {1, 1-lossA, 1-lossB}, on both the pre- and post-behavior
load. ETA is a MODULE-LEVEL constant that run_batt reads by name at call time
(not a function parameter), so a temporary `battery_dispatch_policies.ETA =
...` override for the duration of one calibration call, restored immediately
after, changes its behavior without editing the file — exactly the kind of
runtime override CLAUDE.md's read-only convention for existing generators is
meant to allow (no line of battery_dispatch_policies.py is modified, and its
own committed artifact never touches this script). The resulting six-to-eight
real dispatch reruns fit a linear factor(x) = 1 + slope*(x - nominal) for
each lever, SEPARATELY for the pre- and post-behavior calibration runs (issue
#107: previously averaged into one slope and applied to the already
c-blended base_marginal, which never exactly reproduced either real
calibration point at c=0/c=1 -- see save1_of()'s own docstring and the
"calibration.mid_pre_slope_unaveraging_fix" block of the output artifact for
the quantified before/after). Each side's own slope is applied to that
side's own nominal value FIRST, and the two resulting dollar figures are
blended by c afterward, so a c=1 (pure post-behavior) draw uses ONLY the
mid-behavior calibration, never a value contaminated by pre-behavior data.

CORRELATION STRUCTURE: ASSUMED INDEPENDENT — STATED BIAS DIRECTION
-----------------------------------------------------------------------------
All seven draws are independent random variables (their own separate rng.*
calls). No correlation between them is measured anywhere in this repo, so
none is modeled numerically. Two plausible real correlations both point the
SAME direction:
  (a) Escalation and soiling could positively correlate — a warmer/drier
      climate pattern that raises wildfire-cost-driven rate escalation could
      also raise soiling accumulation (fewer cleansing rain events).
  (b) Round-trip efficiency and capacity fade both trend with cell aging and
      heat exposure — a hard-cycled or hot-climate pack would tend to show
      BOTH low RTE and high fade together, not independently.
Modeling all seven as independent therefore likely UNDERSTATES the true
probability of the worst-of-both-worlds tail (e.g., high escalation together
with high soiling, or low RTE together with high fade) relative to reality,
because independent sampling under-represents scenarios where multiple bad
draws share a common root cause. This is a stated, unquantified bias — no
data in this repo measures either correlation, so no numeric correction is
applied (CLAUDE.md §0: state what would settle it rather than guess a value).

REPRODUCTION OF THE LEGACY 3-INPUT MONTE CARLO AS A VERIFIED SPECIAL CASE
-----------------------------------------------------------------------------
`legacy_reproduction()` is a byte-for-byte reimplementation of
deep_analyses.py's monte_carlo block: same three draws in the same order from
a `numpy.random.default_rng(42)` instance (esc, then fade, then price), same
per-draw year-by-year payback/NPV loop, same rounding. It takes NO other
input — the other four levers are not drawn at all (not merely fixed; the RNG
stream is not touched by them), so the (esc, fade, price) arrays are bit-
identical to deep_analyses.py's, and the resulting summary statistics
reproduce data/deep_results.json's monte_carlo block to float-equality after
matching its own rounding. test_uncertainty_propagation.py asserts this
directly against the committed artifact — not "close", checked.

TORNADO RECONCILIATION AGAINST data/extended_results.json's tornado_battery
-----------------------------------------------------------------------------
extended_findings.py's tornado sweeps four DIFFERENT things: install_cost,
dispatch_policy (a discrete design CHOICE among evening/twowin/greedy, not an
uncertain physical input), post_behavior (a 2-point sensitivity: G vs
G_POST), and escalation_5yr_avg (an average-uplift approximation over a
narrower 0-8% band). This script's tornado only overlaps that ranking on
install_cost and escalation (differently banded) and generalizes post_behavior
into the continuous ev_persistence lever; it adds three physical levers
(soiling, RTE, production spread) the old tornado never quantified at all, and
omits dispatch_policy entirely because it is a decision the household makes,
not an uncertain input to propagate. See the "tornado" block's own
"reconciliation" field for the numeric comparison actually computed at
regeneration time.

Run from private/verify (Green Button, samA/samB and household.yaml staged,
per the repo's standard sandbox pattern): copy this file in beside
usage.csv/samA.csv/samB.csv/rates.py/behavior_rebuild.py/
battery_dispatch_policies.py and run it. Writes uncertainty_results.json to
the CWD. Finds the repo root by walking up from the CWD, then from this
file's own location, exactly like deep_analyses.py/extended_findings.py, so
it also locates the committed data/ artifacts it cross-checks against.
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _repo_root():
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"

WARRANTY_YR = 10
MIDTERM_YR = 15
HORIZON_YR = 25          # "never" = not repaid within this many years — the
                         # same outer horizon deep_analyses.py's own loop uses
                         # (range(1, 26)), kept identical here for consistency.
DISC_RATES = (0.04, 0.07)

ESC_LO, ESC_HI = 0.00, 0.12
FADE_LO, FADE_HI = 0.005, 0.025
PRICE_LO, PRICE_HI = 12500.0, 17000.0
RTE_LO, RTE_NOM, RTE_HI = 0.85, 0.90, 0.95
EV_PERSIST_A, EV_PERSIST_B = 2.0, 1.0     # Beta(2,1) shape params
CAP_KWH = 13.5
STEADY_STATE_TOL_KWH = 0.01
STEADY_STATE_MAX_ITERS = 8


# ---------------------------------------------------------------------------
# committed-artifact readers (read-only; never written to)
# ---------------------------------------------------------------------------
def _read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(f"{path}: cannot read ({type(e).__name__}: {e})")


def _committed(name):
    path = DATA / name
    if not path.is_file():
        raise SystemExit(f"missing committed artifact {path}; regenerate it first")
    return _read_json(path)


# ---------------------------------------------------------------------------
# issue #60: gross-production reconstruction (SAM 8760 + energy balance),
# so a soiling/production-measurement scenario scales GROSS production, not
# the Green Button Generation column (net export) directly. Reimplemented
# locally rather than imported from threeway_production_validation.py's own
# load_sam_hourly(), matching this repo's established convention for this
# exact situation (see _steady_state_run()'s own docstring above).
# ---------------------------------------------------------------------------
SAM_FILES = (("samB.csv", 2025), ("samA.csv", 2026))   # same files/years/
                                                        # truncation-at-last-
                                                        # nonzero-row contract
                                                        # as threeway_
                                                        # production_
                                                        # validation.py


def _load_sam_hourly():
    """{(date, hour): whole-home gross-load kWh} from the two Enphase SAM
    8760 exports staged for this sandbox -- identical contract to
    threeway_production_validation.py's load_sam_hourly() (see that
    function's docstring for the zero-padding-truncation reasoning), kept
    as a separate local copy rather than a cross-module import."""
    import csv as _csv
    out = {}
    for fname, year in SAM_FILES:
        if not pathlib.Path(fname).is_file():
            raise SystemExit(
                f"uncertainty_propagation: missing {fname} -- gross-"
                "production reconstruction (issue #60) needs both SAM 8760 "
                "exports staged beside usage.csv, per the standard "
                "private/verify sandbox.")
        with open(fname, newline="") as fh:
            rd = _csv.DictReader(fh)
            if rd.fieldnames != ["kWh"]:
                raise SystemExit(
                    f"uncertainty_propagation: {fname} has columns "
                    f"{rd.fieldnames}, expected ['kWh']")
            vals = [float(r["kWh"]) for r in rd]
        is_leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        expect = 8784 if is_leap else 8760
        if len(vals) != expect:
            raise SystemExit(
                f"uncertainty_propagation: {fname} has {len(vals)} rows, "
                f"expected {expect} for {year}")
        last = -1
        for i, v in enumerate(vals):
            if v != 0.0:
                last = i
        if last < 0:
            raise SystemExit(f"uncertainty_propagation: {fname} is all "
                             "zeros -- it carries no measurement")
        base = dt.datetime(year, 1, 1)
        for i in range(last + 1):
            ts = base + dt.timedelta(hours=i)
            key = (ts.date(), ts.hour)
            if key in out:
                raise SystemExit(
                    f"uncertainty_propagation: {fname} duplicates an hour "
                    f"({key}) already supplied by another SAM file -- the "
                    "two files' calendar years overlap")
            out[key] = vals[i]
    return out


def reconstruct_gross_production(d, imp0, gen0):
    """Issue #60: (P, D) -- per-15-minute-interval GROSS PV production and
    the household's own physical load, reconstructed from the SAM 8760
    hourly series and the energy-balance identity, so a fractional
    production LOSS can be applied to production itself and correctly
    reallocated against export first, spilling into import only once
    export is exhausted -- instead of the prior approach (scaling the
    Green Button Generation/export column directly), which implicitly
    treated export as if it WERE gross production and could never spill
    into import at all.

    WHAT THE SAM 8760 FILES ACTUALLY ARE (Codex adversarial review, issue
    #60, first pass -- an earlier draft of this function got this wrong):
    _load_sam_hourly()'s own docstring says "whole-home GROSS-LOAD kWh",
    identical to threeway_production_validation.py's load_sam_hourly() --
    this is Enphase's CT-metered whole-home CONSUMPTION series, NOT PV
    production. The earlier draft assigned SAM's raw hourly value directly
    to P (production), which would have claimed ~29,866 kWh/yr of PV
    production against this repo's own validated meter_derived total of
    16,459.2 kWh/yr (TECHNICAL.md section on threeway_production_
    validation.py) -- an ~82% overstatement the algebraic energy-
    conservation check could never catch, because that check only verifies
    internal consistency of P, D and the reallocation math, not that P
    itself means what it claims to. Caught before regenerating any artifact
    or report figure from it.

    Fixed: gross PV production is DERIVED per hour via the SAME identity
    threeway_production_validation.py's own derive_daily() uses --
    `pv_hour = max(sam_load_hour - import_hour + export_hour, 0.0)` (an
    energy-balance fact: whatever the house didn't draw from local
    production came from the grid as import, and whatever local production
    exceeded the house's own draw was exported, so production equals load
    plus export minus import, floored at 0 since production cannot be
    negative) -- computed here directly from this SAME script's own
    already-loaded 15-minute d/imp0/gen0 (grouped into hours), not
    re-derived from a second, separately-loaded Green Button read the way
    threeway_production_validation.py's own gb_hourly loader does, since
    that would risk the two disagreeing on which 15-minute rows exist.

    RESAMPLING (AC1): SAM's own native resolution is hourly (8760 rows/yr)
    -- solar production genuinely does not vary in a way SAM itself
    resolves below that, so each hour's DERIVED pv_hour is allocated across
    however many 15-minute intervals actually belong to that hour in `d`
    (normally 4; a DST day can carry 3 or 5 -- see rates.expected_day_
    hours() -- handled generically by the REAL count for that hour, not a
    hardcoded 4, so DST days need no special-casing or exclusion, unlike
    threeway_production_validation.py's own stricter validation use of this
    same SAM data, which excludes DST days entirely for a different,
    higher-precision purpose). NOT a flat pv_hour/N split (Codex
    adversarial review, issue #60, second pass -- an earlier draft's flat
    split ignored real intra-hour shape and produced physically-impossible
    negative loads on 407 intervals): each net-EXPORTING interval within
    the hour gets its own net export as a floor first, and only the
    remaining production is spread evenly across the hour's intervals --
    see the allocation loop below for the exact guarantee this gives. This
    is still a stated ESTIMATE of intra-hour shape, not a measurement, just
    a tighter one that cannot produce a negative implied load.

    D (household load) is returned for DIAGNOSTIC purposes ONLY -- a sanity
    check that P is physically plausible (D = P[i] - (gen0[i] - imp0[i])
    must never be negative; see the nonnegative-load test) -- it is NOT an
    input to scale_production() below (Codex review, issue #60: an earlier
    draft routed the reallocation through D and a frozen or gen_scale-
    scaled "overlap" component derived from it, which handled a
    simultaneous-import-and-export interval -- 2206 of 35040 in this
    household's real data -- inconsistently depending on whether the
    interval was net-exporting or net-importing overall; see
    scale_production()'s own docstring for the simpler, correct model that
    replaced it).
    """
    # Issue #60 (Codex adversarial review, third pass): the SAM 8760 export
    # is a FLAT 24-hours-a-day grid indexed from Jan 1, never adjusted for
    # DST -- this repo's own established, documented fact (service_
    # headroom.py's "DST" section, threeway_production_validation.py's own
    # dst_dates_in()/derive_daily() exclusion), NOT something this function
    # gets to assume away. Green Button `d` is true wall clock (23 real
    # hours on the spring-forward day, 25 on fall-back -- rates.
    # expected_day_hours()). Joining the two by bare (date, hour) on either
    # transition date silently pairs SAM's flat-clock hour against the
    # WRONG real wall-clock hour (an earlier draft of this function did
    # exactly that, unnoticed by any test since it changes only ~48 of
    # 35,040 intervals a year). Fixed the same way this repo's own
    # precedent handles it: the two DST transition dates are EXCLUDED from
    # the SAM join entirely, taking a conservative, explicitly-labeled
    # fallback instead (P = max(net, 0), i.e. D = 0 -- no self-consumption
    # modeled for those ~48 intervals, so a soiling loss there behaves
    # exactly like the OLD export-only scaling did, never worse) rather
    # than trusting a misaligned join for 2 days out of 365.
    import rates as R
    dst_dates = set()
    for y in sorted({dt_.year for dt_ in d.dt.dt.date}):
        dst_dates |= set(R.dst_transition_sundays(y))

    sam_hourly = _load_sam_hourly()
    dates = d.dt.dt.date.values
    hours = d.dt.dt.hour.values
    n = len(d)
    net = gen0 - imp0
    is_dst = np.array([dd in dst_dates for dd in dates])

    # Group this script's OWN already-loaded 15-minute rows by (date, hour)
    # -- sums of imp0/gen0, and how many quarter-intervals actually belong
    # to that hour (4 on a normal day) -- DST-day rows excluded here, they
    # take the fallback above instead.
    hour_imp = {}
    hour_gen = {}
    hour_count = {}
    hour_members = {}
    for i in range(n):
        if is_dst[i]:
            continue
        key = (dates[i], int(hours[i]))
        hour_imp[key] = hour_imp.get(key, 0.0) + imp0[i]
        hour_gen[key] = hour_gen.get(key, 0.0) + gen0[i]
        hour_count[key] = hour_count.get(key, 0) + 1
        hour_members.setdefault(key, []).append(i)

    missing = [k for k in hour_members if k not in sam_hourly]
    if missing:
        raise SystemExit(
            f"uncertainty_propagation: {len(missing)} hour(s) in the "
            "measured window have no matching SAM 8760 hour (issue #60) -- "
            f"first few: {sorted(missing)[:5]}. samA.csv/samB.csv do not "
            "fully cover the measured window; stage a SAM export for the "
            "missing year(s).")

    # Issue #60 (Codex adversarial review, second pass): a FLAT hour_total/4
    # split (an earlier draft of this function) does not track real
    # intra-hour production shape, and produced 407 intervals (out of
    # 35,040) with a physically-impossible NEGATIVE implied household load
    # (D = P - net < 0, i.e. this interval's own measured net export
    # exceeded its flat share of the hour's production) -- a genuine defect,
    # not just an approximation error to wave away, since D is meant to be
    # a physical load. Fixed with a constrained allocation that keeps every
    # interval's own D >= 0 by construction: give each net-EXPORTING
    # interval (net = gen0-imp0 > 0) exactly its own net export as a floor
    # (D = 0 there, the tightest nonnegative bound), then distribute
    # whatever production remains in the hour evenly across ALL of that
    # hour's intervals (net-importing ones trivially keep D >= 0 for any
    # nonnegative addition, since D = P - net with net <= 0 there already).
    # This still sums to pv_h exactly (energy-conservation at the hourly
    # level is unaffected) and is verified against real data to leave ZERO
    # deficit hours (see the SystemExit guard below, which fires only if a
    # future year's data ever needs the production estimate to fall below
    # what its own net-export peaks already require -- not observed in
    # this household's measured year).
    P = np.empty(n)
    P[is_dst] = np.maximum(net[is_dst], 0.0)   # DST-day fallback, see above
    for key, members in hour_members.items():
        sam_h = sam_hourly[key]
        imp_h = hour_imp[key]
        exp_h = hour_gen[key]
        pv_h = max(sam_h - imp_h + exp_h, 0.0)
        pos_net = {i: max(net[i], 0.0) for i in members}
        sum_pos_net = sum(pos_net.values())
        remainder = pv_h - sum_pos_net
        if remainder < 0:
            raise SystemExit(
                f"uncertainty_propagation: hour {key}'s SAM-derived "
                f"production ({pv_h:.4f} kWh) is less than the sum of its "
                f"own net-exporting intervals ({sum_pos_net:.4f} kWh) -- "
                "the hourly production estimate cannot cover what the "
                "15-minute data already shows was exported that hour, so "
                "no nonnegative-load allocation exists for this hour "
                "(issue #60's allocation guarantees D >= 0 only when this "
                "does not happen; it does not happen anywhere in this "
                "household's measured year, so this is a real data "
                "anomaly to investigate, not silently patched over).")
        share = remainder / hour_count[key]
        for i in members:
            P[i] = pos_net[i] + share

    # D is a DIAGNOSTIC quantity only (the household load P implies, used to
    # verify P itself is physically plausible -- see the nonnegative-load
    # test) -- scale_production() below no longer computes anything from it
    # (Codex review, issue #60: see scale_production()'s own docstring for
    # why D/overlap turned out to be the wrong abstraction for the actual
    # reallocation math).
    D = P - net
    return P, D


def scale_production(P, gen0, gen_scale, imp_base=None):
    """(import_delta, new_export) at a scaled gross production P*gen_scale.

    THE MODEL (Codex review, issue #60 -- this replaced a D/overlap-based
    formula that handled a simultaneous-import-and-export interval (2,206
    of 35,040 in this household's real data) inconsistently: an earlier
    version scaled the "overlap" component down WITH gen_scale, which
    correctly let export shrink on a net-importing overlap interval, but
    ALSO wrongly let IMPORT shrink on a net-EXPORTING overlap interval
    whenever export alone had enough margin to absorb the whole loss --
    physically backwards, since a production LOSS can only leave import
    the same or make it WORSE, never better). The correct, simpler model:
    export is directly tied to production, so a production shortfall
    (`loss = P * (1 - gen_scale)`, positive for a loss, negative for a
    surplus) is absorbed by REDUCING the ALREADY-MEASURED export (`gen0`)
    first, however much of it there is (including any simultaneous-flow
    "overlap" portion -- gen0 already IS the real gross export for that
    interval, nothing to separately track), floored at 0; only once export
    is fully exhausted does the remaining ("excess") loss spill into
    import, which otherwise stays completely untouched by a production
    change. This makes import_delta MONOTONIC in gen_scale by construction
    (never negative for a loss, gen_scale <= 1) -- the property the
    D/overlap formula's overlap-scaling violated.

    gen0 -- the REAL measured export -- is used directly rather than a
    scenario-specific reconstruction: gen0 is already treated as
    independent of which imp_base scenario (real vs. EV-shifted) is under
    test everywhere else in this module (the SAME gen0 feeds both
    marginal(imp0) and marginal(imp_sh) calls), so import_delta -- built
    only from P and gen0, never from imp_base -- is consistent with that
    existing convention by construction, not a new assumption: EV-shifting
    moves WHEN import happens, not the household's solar export, so a
    production-driven adjustment has no reason to depend on it either.

    import_delta is NOT a full import series -- it is the CHANGE a
    production scenario causes, meant to be ADDED onto whichever imp_base
    a caller is testing (imp0 or imp_sh) and clipped at 0.

    At gen_scale=1.0, loss=0, so new_export=gen0 and import_delta=0
    exactly -- reproduces the ORIGINAL (imp0, gen0) exactly for any
    imp_base, which is what keeps the nominal case, and therefore every
    existing committed calibration figure, byte-identical to before this
    fix (verified in test_uncertainty_propagation.py, not just assumed).

    Energy conservation holds per-interval, not just in aggregate:
    (gen0 - new_export) + import_delta == loss always (a lost kWh is
    EITHER less export OR more import, the same invariant the artifact's
    own energy_conservation_check verifies).

    issue #89: gen_scale > 1.0 (a production SURPLUS) IS now modeled, the
    mirror image of the loss-side rule above: the surplus first reduces the
    scenario's existing IMPORT (self-consumption absorbs it, avoiding
    retail/NBC charges), interval by interval, floored so import never goes
    negative (`np.minimum(imp_base, surplus)` -- an interval can never give
    back more import than it actually has), and only once import is fully
    exhausted does the remaining ("residual") surplus spill into MORE
    export. This is why `imp_base` -- deliberately NOT an input to the
    loss-side branch above (see the gen0-independent-of-imp_base reasoning
    there) -- IS required here: a surplus's self-consumption offset is
    bounded by each interval's own actual import, which the loss-side
    branch never needs to know. A caller requesting gen_scale > 1.0 without
    supplying imp_base fails closed rather than silently mishandling it.
    Energy conservation holds per-interval here too: surplus ==
    (import reduction) + (export increase), the same shape as the loss
    side's (gen0 - new_export) + import_delta == loss identity."""
    if gen_scale > 1.0:
        if imp_base is None:
            raise SystemExit(
                "uncertainty_propagation.scale_production: gen_scale > 1.0 "
                f"(got {gen_scale}) needs imp_base -- a production surplus "
                "reduces the scenario's own import first (self-consumption "
                "absorbing it) before spilling into more export, so the "
                "scenario's import series must be supplied; see this "
                "function's own docstring.")
        surplus = P * (gen_scale - 1.0)
        absorbed_by_import = np.minimum(imp_base, surplus)
        import_delta = -absorbed_by_import
        residual_surplus = surplus - absorbed_by_import
        new_export = gen0 + residual_surplus
        return import_delta, new_export
    loss = P * (1.0 - gen_scale)
    new_export = np.maximum(gen0 - loss, 0.0)
    import_delta = np.maximum(loss - gen0, 0.0)
    return import_delta, new_export


def dispatch_calibration():
    """Recompute the pre-/post-behavior 13.5 kWh battery marginal at nominal
    RTE/soiling, plus the RTE- and soiling-saving sensitivity slopes, from the
    real dispatch engine. Requires usage.csv/samA.csv/samB.csv and
    private/household.yaml in the working directory's ancestry (the standard
    private/verify sandbox) — this is the one function in this module that
    touches private data, exactly mirroring deep_analyses.py's own module-
    level read of usage.csv.

    Returns a dict: pre, mid (nominal marginals, $/yr), rte_slope_mid/pre and
    soil_slope_loss_mid/pre / soil_slope_surplus_mid/pre (fractional-factor
    slopes, kept separate per mid/pre side -- issue #89: soil slopes also
    split by loss/surplus side since scale_production() is asymmetric; issue
    #107: no averaged single slope per lever any more, each side's own
    slope is applied to that side's own nominal value before save1_of()
    blends the two dollar figures by c), the raw calibration points, and
    mid_pre_slope_unaveraging_fix (issue #107's own before/after
    quantification) for the artifact's own "calibration" section.
    """
    import behavior_rebuild as br
    import battery_dispatch_policies as bp

    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    base = bp.billed(d, imp0, gen0)

    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions), sop_idx, sop_ts)
    b_sh = bp.billed(d, imp_sh, gen0)

    # issue #60: gross production (P), reconstructed once from the real
    # measured year -- held fixed and reused for every gen_scale scenario
    # below (soiling and, via save1_of()'s shared soil_slope_loss/
    # soil_slope_surplus routing, production-measurement-spread too),
    # instead of the prior gen0*gen_scale approximation. The second return
    # value (household load) is diagnostic-only (see reconstruct_gross_production()'s own docstring
    # and test_uncertainty_propagation.py's own nonnegative-load check),
    # not used below.
    P, _ = reconstruct_gross_production(d, imp0, gen0)

    def _steady_state_run(imp_base, gen, eta):
        """run_batt always starts at soc0=cap/2 and runs the year once -- a
        one-time year-1 boundary condition, not a steady annual cycle, whose
        drift can differ across calibration points (Codex review pass 1,
        finding 2 -- the identical class of boundary-condition issue
        tou_structure_stress.py's own `_steady_state_battery` fixed for issue
        #14, reimplemented locally here rather than importing that module's
        internal helper across a script boundary, matching this repo's own
        established convention for this exact fix). Iterates run_batt,
        feeding each pass's ending SOC forward as the next pass's starting
        SOC, until they converge to within STEADY_STATE_TOL_KWH kWh."""
        soc0 = CAP_KWH / 2
        for _ in range(STEADY_STATE_MAX_ITERS):
            i2, e2, served, thru = bp.run_batt(d, imp_base, gen, CAP_KWH, "greedy", soc0=soc0,
                                               charge_kw=bp.CHARGE_KW)
            soc_final = soc0 + thru - served / eta
            if abs(soc_final - soc0) < STEADY_STATE_TOL_KWH:
                return i2, e2
            soc0 = soc_final
        raise SystemExit(
            "uncertainty_propagation: calibration SOC did not converge to a "
            f"steady annual cycle within {STEADY_STATE_MAX_ITERS} iterations "
            f"-- last diff {soc_final - soc0:.4f} kWh")

    def marginal(imp_base, rte=RTE_NOM, gen_scale=1.0):
        """Battery-alone marginal saving at a given RTE and generation scale.
        The no-battery baseline is recomputed AT THE SAME gen_scale (adversarial
        review pass 1, finding 1): billing a scaled-generation battery run
        against an unscaled-generation baseline silently folded the direct
        cost of lost solar into what was supposed to be an isolated battery
        effect, contaminating the soiling slope. Both sides of the subtraction
        must see the identical generation input.

        Issue #60: gen_scale now scales GROSS production (P), reallocated
        against export first and spilling into import only once export is
        exhausted (scale_production()), instead of scaling the Green Button
        Generation/export column directly (which could never spill into
        import at all, understating a production loss's true cost). The
        resulting import_delta is added onto imp_base and clipped at 0, so
        it composes with whichever scenario (real imp0 or EV-shifted
        imp_sh) is under test, matching the existing gen-independent-of-
        imp_base convention. At gen_scale=1.0 import_delta is exactly zero
        and gen equals gen0 exactly (see scale_production()'s own
        docstring), so this is a no-op at nominal -- the byte-identity
        every existing committed figure depends on.

        Issue #89: gen_scale > 1.0 (a production SURPLUS) is now real too,
        mirrored against this same scenario's own imp_base (passed through
        to scale_production() below) so the surplus reduces THIS scenario's
        import before spilling into more export -- imp_base is only ever
        read here for that surplus branch; gen_scale <= 1.0 ignores it
        exactly as before."""
        import_delta, gen = scale_production(P, gen0, gen_scale, imp_base=imp_base)
        imp_base = np.maximum(imp_base + import_delta, 0.0)
        bill_base = bp.billed(d, imp_base, gen)
        eta = np.sqrt(rte)
        orig_eta = bp.ETA
        bp.ETA = eta
        try:
            i2, e2 = _steady_state_run(imp_base, gen, eta)
            b2 = bp.billed(d, i2, e2)
        finally:
            bp.ETA = orig_eta
        return float(bill_base - b2)

    def _single_pass_marginal(imp_base):
        """The EXACT method battery_dispatch_policies.py's own top-level driver
        uses for the committed pw3.greedy.save/post_behavior.mid.battery_
        marginal figures (run_batt called with no soc0 -- a single pass from
        cap/2, never converged to a steady annual cycle). Used ONLY for the
        tie-out check below: comparing a steady-state-converged recomputation
        against a single-pass committed figure would show a spurious ~$1
        drift that is really just the two methods' known, small SOC-boundary
        difference, not a stale artifact. battery_dispatch_policies.py itself
        is out of this issue's scope to change, so the committed figures stay
        single-pass; this script's OWN calibration (pre_nominal/mid_nominal
        below) uses the steady-state method throughout for internal
        consistency across every calibration point, matching Codex review
        pass 1 finding 2's fix."""
        i2, e2, _, _ = bp.run_batt(d, imp_base, gen0, CAP_KWH, "greedy", charge_kw=bp.CHARGE_KW)
        return float(bp.billed(d, imp_base, gen0) - bp.billed(d, i2, e2))

    pre_nominal_single_pass = _single_pass_marginal(imp0)
    mid_nominal_single_pass = _single_pass_marginal(imp_sh)
    pre_nominal = marginal(imp0)
    mid_nominal = marginal(imp_sh)

    soiling = _committed("soiling_results.json")["annual_economics"]
    gen_kwh = soiling["annual_generation_kwh"]
    lost_A = soiling["scenario_A_this_years_evidence"]["annual_lost_kwh"]
    lost_B = soiling["scenario_B_2024_cleaning_evidence"]["annual_lost_kwh"]
    # gen0 (the Green Button Generation column) is THIS YEAR'S actual, already-
    # soiled measured production -- scenario A IS "this year's evidence", so
    # gen0 already reflects roughly scenario A's own loss, not a hypothetical
    # clean baseline. Scaling gen0 by (1 - lost_A/gen_kwh) as an earlier draft
    # did would subtract scenario A's loss a SECOND time on top of production
    # that already embeds it (Codex review pass 2 finding). lossA is therefore
    # 0 by construction (the observed baseline already IS the scenario-A
    # state); lossB is the INCREMENTAL further loss to reach scenario B's
    # worse, dirtier state RELATIVE TO that same observed baseline, not
    # scenario B's loss applied on top of an already-scenario-A-reduced series.
    lossA = 0.0
    lossB = (lost_B - lost_A) / gen_kwh

    rte_points_mid = {RTE_LO: marginal(imp_sh, rte=RTE_LO),
                      RTE_NOM: mid_nominal,
                      RTE_HI: marginal(imp_sh, rte=RTE_HI)}
    rte_points_pre = {RTE_LO: marginal(imp0, rte=RTE_LO),
                      RTE_NOM: pre_nominal,
                      RTE_HI: marginal(imp0, rte=RTE_HI)}
    # lossA == 0.0 is the same state as the nominal (0.0) point by
    # construction above, so including it a second time would be a
    # duplicate dict key, not a third calibration point. Issue #89 adds the
    # real THIRD point: the mirrored surplus scenario (gen_scale=1+lossB,
    # keyed at -lossB to match save1_of()'s own (1 - prod_noise) sign
    # convention for a surplus-like input) -- a genuine third real dispatch
    # rerun, not an extrapolation, giving three points per side (surplus,
    # nominal, loss) instead of two.
    loss_point_mid = marginal(imp_sh, gen_scale=1 - lossB)
    surplus_point_mid = marginal(imp_sh, gen_scale=1 + lossB)
    loss_point_pre = marginal(imp0, gen_scale=1 - lossB)
    surplus_point_pre = marginal(imp0, gen_scale=1 + lossB)
    soil_points_mid = {-lossB: surplus_point_mid,
                       0.0: mid_nominal,
                       lossB: loss_point_mid}
    soil_points_pre = {-lossB: surplus_point_pre,
                       0.0: pre_nominal,
                       lossB: loss_point_pre}

    def slope_of(points, nominal_x, nominal_y):
        xs = np.array(sorted(points))
        ys = np.array([points[x] for x in xs])
        # fractional factor y/nominal_y as a function of (x - nominal_x)
        frac = ys / nominal_y
        A = np.vstack([xs - nominal_x, np.ones_like(xs)]).T
        m, b = np.linalg.lstsq(A, frac, rcond=None)[0]
        return float(m)  # intercept b is ~1.0 by construction at x=nominal_x

    rte_slope_mid = slope_of(rte_points_mid, RTE_NOM, mid_nominal)
    rte_slope_pre = slope_of(rte_points_pre, RTE_NOM, pre_nominal)
    # issue #89: fit the loss-side and surplus-side slopes SEPARATELY, each
    # from exactly the two relevant points (nominal + the one on that side)
    # -- NOT a single 3-point line across both sides, since scale_
    # production()'s physical model is genuinely piecewise (a loss reduces
    # export first; a surplus reduces import first), not one straight line
    # through all three points.
    soil_slope_loss_mid = slope_of({0.0: mid_nominal, lossB: loss_point_mid},
                                    0.0, mid_nominal)
    soil_slope_surplus_mid = slope_of({-lossB: surplus_point_mid, 0.0: mid_nominal},
                                       0.0, mid_nominal)
    soil_slope_loss_pre = slope_of({0.0: pre_nominal, lossB: loss_point_pre},
                                    0.0, pre_nominal)
    soil_slope_surplus_pre = slope_of({-lossB: surplus_point_pre, 0.0: pre_nominal},
                                       0.0, pre_nominal)

    # issue #60 AC2: verify the reallocation is energy-conserving, not just
    # plausible-looking -- every kWh the lossB scenario removes from GROSS
    # production must show up as EITHER reduced export OR increased import,
    # with nothing lost or created. Checked here, not just asserted, and
    # exposed in the artifact for AC3.
    lossB_import_delta, gen_at_lossB = scale_production(P, gen0, 1 - lossB)
    production_lost_kwh = float(lossB * P.sum())
    export_reduction_kwh = float(gen0.sum() - gen_at_lossB.sum())
    import_increase_kwh = float(lossB_import_delta.sum())
    conservation_gap_kwh = production_lost_kwh - (export_reduction_kwh + import_increase_kwh)

    # issue #60 AC3: compare against the PRE-FIX approach's own figures --
    # frozen HISTORICAL constants (the committed data/uncertainty_results.
    # json values immediately before this fix), NOT read live from
    # _committed(): reading the live committed artifact would make this
    # field self-referential and non-reproducible after the FIRST
    # regeneration following this fix (a second regenerate-and-diff would
    # compare "old" against itself, silently drifting the artifact on every
    # run and breaking the CLAUDE.md section 9 byte-identity gate -- caught
    # by running the regeneration twice before committing, not assumed).
    OLD_SOIL_SLOPE_MID_EXPORT_ONLY = 0.05605402062021063
    OLD_SOIL_SLOPE_PRE_EXPORT_ONLY = 0.05634980307893865

    production_reconstruction = {
        "method": (
            "issue #60: gross production (P) reconstructed per hour from "
            "the SAM 8760 hourly series via P_hour = max(sam_load_hour - "
            "import_hour + export_hour, 0), the same identity threeway_"
            "production_validation.py's own derive_daily() uses (SAM's own "
            "hourly files are whole-home GROSS LOAD, not production "
            "directly) -- allocated within each hour so every net-"
            "exporting interval gets at least its own net export (keeping "
            "the diagnostic implied-household-load figure non-negative "
            "everywhere, not a flat per-quarter split); the two DST "
            "transition dates take a conservative fallback instead of a "
            "misaligned SAM join (SAM's export is a flat 24-hour clock, "
            "Green Button is true wall clock). A gen_scale (soiling/"
            "production-measurement) scenario reduces the MEASURED export "
            "(gen0) directly by the production shortfall (loss = P*(1-"
            "gen_scale)), floored at 0, spilling any remainder into import "
            "only once export is fully exhausted -- export is tied to "
            "production and absorbs a shortfall first; import is load-"
            "driven and only ever grows, never shrinks, from a production "
            "loss -- instead of scaling the Green Button Generation/export "
            "column proportionally, which could never spill into import "
            "at all."),
        "gross_production_kwh": round(float(P.sum()), 1),
        "energy_conservation_check": {
            "at_lossB": lossB,
            "production_lost_kwh": round(production_lost_kwh, 2),
            "export_reduction_kwh": round(export_reduction_kwh, 2),
            "import_increase_kwh": round(import_increase_kwh, 2),
            "gap_kwh": round(conservation_gap_kwh, 4),
            "note": ("production_lost_kwh must equal export_reduction_kwh + "
                     "import_increase_kwh (every lost kWh is EITHER less "
                     "export OR more import, nothing vanishes) -- gap_kwh "
                     "is that check's residual, expected to be ~0"),
        },
        "old_vs_new_soil_slope": {
            "old_export_only_scaling": {
                "soil_slope_mid": OLD_SOIL_SLOPE_MID_EXPORT_ONLY,
                "soil_slope_pre": OLD_SOIL_SLOPE_PRE_EXPORT_ONLY,
                "as_of": ("frozen historical constant: this repo's committed "
                          "data/uncertainty_results.json value immediately "
                          "before issue #60's fix, NOT re-derived at "
                          "runtime -- see this field's own docstring note"),
            },
            "new_gross_production_reallocation": {
                # issue #89: this comparison is against the LOSS-side slope
                # specifically (the directly analogous quantity to the old,
                # single, loss-fit OLD_SOIL_SLOPE_MID/PRE_EXPORT_ONLY
                # constants above) -- not the surplus-side slope, which has
                # no counterpart in the pre-issue-60 export-only approach.
                "soil_slope_mid": soil_slope_loss_mid,
                "soil_slope_pre": soil_slope_loss_pre,
            },
            "note": (
                "Confirms the issue's own 'likely understates' hypothesis, "
                "with a quantified mechanism: at this household's lossB "
                f"({lossB:.4f}), {import_increase_kwh:.0f} of "
                f"{production_lost_kwh:.0f} lost kWh/yr (~"
                f"{100*import_increase_kwh/production_lost_kwh:.0f}%) was "
                "being SELF-CONSUMED (invisible to the old export-only "
                "scaling), not exported -- losing it mostly increases "
                "IMPORT, billed near the full retail rate, not just export, "
                "billed at the lower NEM credit rate. The old approach both "
                "undercounted the lost energy's magnitude (it only ever "
                "saw the smaller export column) AND mispriced what it did "
                "count (attributing it all to the export-credit rate). "
                "soil_slope_mid/pre here are the LOSS-side slope "
                "specifically -- the analogous quantity to the old, single "
                "loss-fit figure -- not the surplus-side slope issue #89 "
                "adds (see surplus_slope_fix below)."),
        },
        # issue #89 AC3: what the OLD one-sided approach (extrapolating the
        # loss-fit slope to the surplus side) would have predicted at the
        # real surplus point, versus what a REAL third dispatch rerun at
        # that point (surplus_point_mid, gen_scale=1+lossB) actually shows
        # -- quantifying the discrepancy this fix eliminates, not just
        # asserting it shrinks.
        "surplus_slope_fix": {
            "method": (
                "Before this fix, save1_of() applied ONE slope (fit only "
                "from a loss-side dispatch rerun) linearly to both "
                "directions, extrapolating it to the surplus side rather "
                "than measuring the surplus side directly. This fix adds a "
                "real third dispatch rerun at the mirrored surplus "
                "scenario (gen_scale=1+lossB) and fits a genuinely separate "
                "soil_slope_surplus from it. old_extrapolated_estimate "
                "below is what the retired one-sided approach would have "
                "predicted at that same surplus point -- "
                "mid_nominal * (1 + soil_slope_loss_mid * (-lossB)) -- "
                "compared against real_surplus_marginal, the REAL measured "
                "value now used directly instead."),
            "soil_slope_loss_mid": soil_slope_loss_mid,
            "soil_slope_surplus_mid": soil_slope_surplus_mid,
            "old_extrapolated_estimate": round(
                mid_nominal * (1 + soil_slope_loss_mid * (-lossB)), 2),
            "real_surplus_marginal": round(surplus_point_mid, 2),
            "discrepancy_usd": round(
                mid_nominal * (1 + soil_slope_loss_mid * (-lossB)) - surplus_point_mid, 2),
            "discrepancy_pct_of_mid_nominal": round(
                100 * (mid_nominal * (1 + soil_slope_loss_mid * (-lossB)) - surplus_point_mid)
                / mid_nominal, 4),
            "resolution": (
                "The ONE-SIDED EXTRAPOLATION discrepancy quantified above is "
                "eliminated by construction: save1_of() now uses "
                "soil_slope_surplus, fit directly from this real surplus "
                "dispatch rerun, for every surplus-like draw (prod_noise > "
                "1, i.e. (1 - prod_noise) < 0) instead of linearly "
                "extrapolating the loss-side slope, for any draw magnitude "
                "-- not just at this particular lossB point. A SEPARATE, "
                "smaller residual from averaging soil_slope_loss/surplus "
                "across mid/pre before applying them to the c-blended "
                "base_marginal (the same convention rte_slope used to "
                "follow) is now ALSO fixed -- see mid_pre_slope_"
                "unaveraging_fix below (issue #107)."),
        },
    }

    # issue #107: quantifies the mid/pre-averaging residual save1_of() used
    # to carry for EVERY slope-based lever (not just soil), and what
    # applying each side's own slope to that side's own nominal value --
    # rather than one averaged slope to the c-blended base_marginal --
    # actually closes. Computed here, live, from the same real calibration
    # points surplus_slope_fix above uses, not hand-typed from a prior run.
    # A top-level sibling of production_reconstruction, not nested inside
    # it, since this covers RTE too, not just soiling/production.
    mid_pre_slope_unaveraging_fix = {
        "method": (
            "Before this fix, save1_of() computed ONE slope per lever "
            "(rte_slope, soil_slope_loss, soil_slope_surplus), each "
            "averaged across the mid- and pre-behavior calibration "
            "runs, and applied it to the ALREADY c-blended "
            "base_marginal = c*mid + (1-c)*pre. At c=1 (pure post-"
            "behavior) this used a slope that was HALF pre-behavior; "
            "the real mid-only calibration point was never exactly "
            "reproduced. Fixed by applying each side's OWN slope to "
            "that side's OWN nominal value FIRST, then blending the "
            "two resulting dollar figures by c: c*(mid*factor_mid) + "
            "(1-c)*(pre*factor_pre)."),
        "soil_surplus_point_mid": {
            "real": round(surplus_point_mid, 2),
            "old_averaged_slope_prediction": round(
                mid_nominal * (1 + ((soil_slope_surplus_mid + soil_slope_surplus_pre) / 2)
                               * (-lossB)), 2),
            "new_own_side_slope_prediction": round(
                mid_nominal * (1 + soil_slope_surplus_mid * (-lossB)), 2),
            "note": ("the new prediction matches 'real' exactly (both "
                     "sides of a 2-point fit, {nominal, surplus}, pass "
                     "through both points by construction) -- the old "
                     "averaged-slope prediction does not, since it used "
                     "a slope that was half pre-behavior at a pure-mid "
                     "(c=1) scenario."),
        },
        # issue #107 review, round 2: the pre-side (c=0) exactness claim
        # was asserted in prose ("symmetrically at c=0") but never actually
        # quantified anywhere in the artifact -- this block closes that gap
        # with the same structure as the mid-side block above, using
        # pre_nominal/soil_slope_surplus_pre instead of mid_nominal/
        # soil_slope_surplus_mid.
        "soil_surplus_point_pre": {
            "real": round(surplus_point_pre, 2),
            "old_averaged_slope_prediction": round(
                pre_nominal * (1 + ((soil_slope_surplus_mid + soil_slope_surplus_pre) / 2)
                               * (-lossB)), 2),
            "new_own_side_slope_prediction": round(
                pre_nominal * (1 + soil_slope_surplus_pre * (-lossB)), 2),
            "note": ("the pre-side mirror of soil_surplus_point_mid above, "
                     "at c=0 (pure pre-behavior) instead of c=1: the new "
                     "prediction matches 'real' exactly for the identical "
                     "exact-2-point-fit reason."),
        },
        "rte_points_mid": {
            rte: {
                "real": round(v, 2),
                "old_averaged_slope_prediction": round(
                    mid_nominal * (1 + ((rte_slope_mid + rte_slope_pre) / 2)
                                   * (rte - RTE_NOM)), 2),
                "new_own_side_slope_prediction": round(
                    mid_nominal * (1 + rte_slope_mid * (rte - RTE_NOM)), 2),
            } for rte, v in rte_points_mid.items()
        },
        "rte_points_pre": {
            rte: {
                "real": round(v, 2),
                "old_averaged_slope_prediction": round(
                    pre_nominal * (1 + ((rte_slope_mid + rte_slope_pre) / 2)
                                   * (rte - RTE_NOM)), 2),
                "new_own_side_slope_prediction": round(
                    pre_nominal * (1 + rte_slope_pre * (rte - RTE_NOM)), 2),
            } for rte, v in rte_points_pre.items()
        },
        "rte_residual_note": (
            "Unlike the soil slopes, RTE_LO/RTE_NOM/RTE_HI are fit "
            "together by LEAST SQUARES across all THREE points (not two "
            "separate 2-point fits), so even the new own-side slope "
            "does not exactly reproduce RTE_LO/RTE_HI -- a real "
            "dispatch relationship is not perfectly linear across all "
            "three RTE draws, and a single best-fit line cannot pass "
            "through three non-collinear points exactly. This fix "
            "removes the mid/pre-AVERAGING contribution to that gap "
            "(what issue #107 targets) but does not and cannot remove "
            "the separate least-squares-fit residual itself. That "
            "residual does NOT shrink uniformly, and the direction is NOT "
            "even consistent between the mid and pre sides (Codex review, "
            "issue #107, pass 1 -- round 1's own claim was checked only "
            "on the mid side and was WRONG for pre, the exact class of "
            "error this project's own review process exists to catch): at "
            "this household's real calibration, the mid side gets WORSE "
            "at RTE_LO and BETTER at RTE_HI, while the pre side is the "
            "OPPOSITE -- BETTER at RTE_LO and WORSE at RTE_HI (the old "
            "averaged-slope error happens to partially cancel the "
            "independent 3-point-fit residual on whichever side/point it "
            "coincidentally lines up with, with no consistent pattern "
            "across sides). Both sides remain well under 0.2% either way "
            "regardless of direction -- see rte_points_mid/rte_points_pre "
            "above for the exact old/new numbers, live, not summarized as "
            "uniformly anything. The symmetric soil_surplus_point_pre/"
            "rte_points_pre blocks (issue #107 review, round 2) confirm "
            "the c=0 side is exact for soil_slope_surplus the same way "
            "c=1 is, not just asserted in prose without a matching "
            "quantification."),
    }

    return {
        "pre_nominal": pre_nominal,
        "mid_nominal": mid_nominal,
        "pre_nominal_single_pass": pre_nominal_single_pass,
        "mid_nominal_single_pass": mid_nominal_single_pass,
        "behavior_save": float(base - b_sh),
        "lossA": lossA,
        "lossB": lossB,
        # issue #107: no averaged single slope per lever any more -- each
        # side (mid/pre) keeps its own slope, applied to that side's own
        # nominal value before the two dollar figures are blended by c (see
        # save1_of()'s own docstring). issue #89: soil_slope_loss/surplus
        # were already split by SIDE (loss/surplus) rather than one
        # undifferentiated soil_slope, since scale_production()'s physical
        # relationship is genuinely piecewise; issue #107 additionally stops
        # averaging each side's own mid/pre pair.
        "rte_slope_mid": rte_slope_mid,
        "rte_slope_pre": rte_slope_pre,
        "soil_slope_loss_mid": soil_slope_loss_mid,
        "soil_slope_loss_pre": soil_slope_loss_pre,
        "soil_slope_surplus_mid": soil_slope_surplus_mid,
        "soil_slope_surplus_pre": soil_slope_surplus_pre,
        "rte_points_mid": rte_points_mid,
        "rte_points_pre": rte_points_pre,
        "soil_points_mid": soil_points_mid,
        "soil_points_pre": soil_points_pre,
        "production_reconstruction": production_reconstruction,
        "mid_pre_slope_unaveraging_fix": mid_pre_slope_unaveraging_fix,
    }


def production_spread_stats(csv_path=None):
    """Empirical production-measurement spread from the committed three-way
    validation CSV — no private data required, and no change to that file
    (issue #37's job, not this one's)."""
    import pandas as pd
    path = csv_path or (DATA / "threeway_production_validation.csv")
    df = pd.read_csv(path)
    rel_daily = (df["pvoutput"] - df["enphase_meter"]) / df["enphase_meter"]
    ann_rel = (df["pvoutput"].sum() - df["enphase_meter"].sum()) / df["enphase_meter"].sum()
    return {
        "days": int(len(df)),
        "daily_rel_diff_mean": float(rel_daily.mean()),
        "daily_rel_diff_std": float(rel_daily.std()),
        "annual_rel_diff": float(ann_rel),
        "prod_sigma_used": float(abs(ann_rel)),
        "prod_sigma_rationale": (
            "annual (systematic) relative gap between the two full-year "
            "totals, not the larger daily std, because the annual gap "
            "tracks the mean daily gap rather than shrinking by "
            "1/sqrt(n) -- evidence of a persistent meter-to-meter "
            "disagreement rather than independent daily noise"),
    }


# ---------------------------------------------------------------------------
# pure math: deterministic payback / NPV of one scenario
# ---------------------------------------------------------------------------
def payback_of(save1, esc, fade, price, horizon=HORIZON_YR):
    """Simple payback year of a single (save1, esc, fade, price) scenario, or
    NaN if the cumulative undiscounted saving never reaches price within
    `horizon` years. Mirrors deep_analyses.py's own inner loop exactly."""
    cum = 0.0
    for yr in range(1, horizon + 1):
        s_yr = save1 * ((1 + esc) ** (yr - 1)) * ((1 - fade) ** (yr - 1))
        cum += s_yr
        if cum >= price:
            return yr - 1 + (price - (cum - s_yr)) / s_yr
    return float("nan")


def npv_of(save1, esc, fade, price, disc, horizon):
    """Standard NPV: -price + PV of the savings stream over `horizon` years.
    (This is the ordinary per-draw definition; the legacy artifact's own
    npv10_at_4pct_median uses a different, non-standard median(npv)-
    median(price) convention, reproduced separately and only inside
    legacy_reproduction() for exact-match purposes.)"""
    pv = 0.0
    for yr in range(1, horizon + 1):
        s_yr = save1 * ((1 + esc) ** (yr - 1)) * ((1 - fade) ** (yr - 1))
        pv += s_yr / ((1 + disc) ** yr)
    return pv - price


# ---------------------------------------------------------------------------
# legacy special case: exact reimplementation of deep_analyses.py's block
# ---------------------------------------------------------------------------
def legacy_reproduction(base_save, seed=42, N=5000):
    rng = np.random.default_rng(seed)
    esc = rng.uniform(0.00, 0.10, N)
    fade = rng.uniform(0.005, 0.025, N)
    price = rng.uniform(12500, 17000, N)
    payback = np.full(N, np.nan)
    npv10 = np.zeros(N)
    for i in range(N):
        cum = 0.0
        s = base_save
        for yr in range(1, 26):
            s_yr = base_save * ((1 + esc[i]) ** (yr - 1)) * ((1 - fade[i]) ** (yr - 1))
            cum += s_yr
            if np.isnan(payback[i]) and cum >= price[i]:
                payback[i] = yr - 1 + (price[i] - (cum - s_yr)) / s_yr
            if yr <= 10:
                npv10[i] += s_yr / (1.04 ** yr)
    return {
        "payback_median": round(float(np.nanmedian(payback)), 1),
        "payback_p10": round(float(np.nanpercentile(payback, 10)), 1),
        "payback_p90": round(float(np.nanpercentile(payback, 90)), 1),
        "prob_payback_within_warranty10yr": round(float(np.mean(payback <= 10)), 3),
        "npv10_at_4pct_median": round(float(np.median(npv10) - np.median(price))),
    }


# ---------------------------------------------------------------------------
# the comprehensive 7-input Monte Carlo
# ---------------------------------------------------------------------------
def draw_inputs(N, seed, rte_slope, soil_slope, lossA, lossB, prod_sigma):
    rng = np.random.default_rng(seed)
    esc = rng.uniform(ESC_LO, ESC_HI, N)
    fade = rng.uniform(FADE_LO, FADE_HI, N)
    price = rng.uniform(PRICE_LO, PRICE_HI, N)
    c = rng.beta(EV_PERSIST_A, EV_PERSIST_B, N)
    rte = rng.uniform(RTE_LO, RTE_HI, N)
    loss = rng.triangular(lossA, lossA, lossB, N)
    prod_noise = rng.normal(1.0, prod_sigma, N)
    return esc, fade, price, c, rte, loss, prod_noise


def save1_of(c, rte, loss, prod_noise, pre, mid,
             rte_slope_mid, rte_slope_pre,
             soil_slope_loss_mid, soil_slope_loss_pre,
             soil_slope_surplus_mid, soil_slope_surplus_pre):
    """prod_noise is a multiplicative factor on TRUE generation (1.0 = the
    measured value is right; >1 = true generation is higher than measured).
    Adversarial review pass 2, finding 2: an earlier draft multiplied prod_noise
    directly into the dollar saving, implicitly assuming a 1:1 saving response
    to a generation change -- inconsistent with the CALIBRATED soil slopes,
    which found a real generation-scale change (soiling) moves the battery
    marginal by only a fraction of the fractional change, not 1:1 (most of a
    generation swing changes exports/self-consumption directly, and only a
    damped fraction interacts with the battery's own arbitrage timing).
    Production-measurement uncertainty is uncertainty about the SAME physical
    quantity (true generation level) soiling calibration already measured the
    sensitivity of, so it is routed through the same calibration rather than
    assumed proportional.

    PIECEWISE BY DESIGN (issue #89, resolving the KNOWN LIMITATION flagged in
    issue #60's second review pass): a production loss and a production
    surplus are no longer the same physical relationship since issue #60's
    scale_production() made the loss/surplus reallocation deliberately
    ASYMMETRIC (a loss reduces export first; a surplus reduces import
    first). soil_slope_loss and soil_slope_surplus are each fit from a real
    dispatch rerun on their own side (gen_scale=1-lossB and gen_scale=
    1+lossB respectively, both against the SAME nominal point) -- not one
    slope extrapolated across both. This household's own most recently
    regenerated calibration fit soil_slope_loss_mid=+0.2176/
    soil_slope_loss_pre=+0.1695 and soil_slope_surplus_mid=+0.3404/
    soil_slope_surplus_pre=+0.2807 -- see data/uncertainty_results.json's
    calibration section for the current values (the surplus-side slope
    came out genuinely steeper in magnitude than the loss-side one here,
    roughly 1.56x -- confirmed against this module's own real dispatch
    reruns, not assumed from issue #89's own illustrative filing numbers,
    which used a smaller ~1.06x ballpark before this fix's real third
    rerun existed to check it against); dispatch_calibration()'s new
    surplus_slope_fix block shows what the old one-sided extrapolation
    would have predicted at the real surplus point versus the real
    measured value, and by how much this fix closes that gap (to exactly
    zero, by construction, since the real surplus point is now used
    directly rather than extrapolated).

    COMBINED BEFORE SELECTING A SIDE (Codex review, issue #89, pass 1):
    `loss` and `prod_noise` both perturb the SAME physical quantity (true
    generation relative to nominal) -- a first draft of this piecewise
    design applied them as two INDEPENDENT multiplicative factors, each
    separately choosing its own slope side by its own sign. That was exact
    while both sides shared one slope (pre-#89), but with genuinely
    different loss/surplus slopes it introduces a first-order spurious
    bias whenever the two draws partially offset (e.g. a soiling-loss draw
    coinciding with an equal-and-opposite production-measurement-surplus
    draw): quantified at this household's real prod_sigma/lossB via a
    2M-draw simulation, a +0.28% mean bias across the Monte Carlo's own
    draw distribution. Fixed by combining them into ONE shortfall variable
    BEFORE any slope is selected: true_relative_generation =
    (1 - loss) * prod_noise (soiling reduces true generation by `loss`;
    prod_noise then represents how far the MEASURED baseline sits from
    that true value), so combined_x = 1 - true_relative_generation =
    loss + x - loss*x where x = 1 - prod_noise (the exact combination, not
    a first-order approximation) -- ONE slope side is selected from
    combined_x's own sign, and ONE factor is applied. np.where() makes
    this vectorized-safe for both an array `loss`/`prod_noise` (the Monte
    Carlo's own draw_inputs() output) and plain scalar floats (tornado(),
    escalation_downside_sensitivity(), and this file's own direct test
    calls) -- confirmed by both call shapes in test_uncertainty_
    propagation.py.

    MID/PRE WEIGHTED BY c, NOT AVERAGED BEFORE BLENDING (issue #107): every
    slope-based factor used to be averaged across mid/pre
    (rte_slope=(rte_slope_mid+rte_slope_pre)/2, same pattern for the soil
    slopes) and applied to the ALREADY c-blended base_marginal
    (c*mid+(1-c)*pre) -- so at c=1 (pure post-behavior), the result used a
    slope that was HALF pre-behavior, and never exactly reproduced the real
    mid-only calibration point; symmetrically at c=0. Fixed by applying
    each side's OWN slope to that side's OWN nominal value first, THEN
    blending the two resulting dollar figures by c:
    c*(mid*factor_mid) + (1-c)*(pre*factor_pre) instead of
    (c*mid+(1-c)*pre)*factor_avg. At c=1/c=0 this exactly reproduces the
    real soil_slope_loss/soil_slope_surplus calibration points (each fit
    from exactly 2 real dispatch points -- {nominal, loss} or {surplus,
    nominal} -- so a 2-point line always passes through both exactly):
    confirmed to residual 0.0 (to float precision) at this household's own
    calibration, versus the old averaged-slope approach's ~$3.53/0.16% gap
    at the real surplus point. The RTE lever is NOT exactly reproduced at
    RTE_LO/RTE_HI even with this fix: rte_slope_mid/rte_slope_pre are each
    fit by LEAST SQUARES across THREE points (RTE_LO/RTE_NOM/RTE_HI, not
    two), which leaves an inherent best-fit residual (~0.13% at this
    household's real calibration) independent of mid/pre averaging -- a
    single straight line generally cannot pass through three real
    (non-collinear) points exactly. This fix removes the mid/pre-averaging
    contribution to that gap (the specific thing issue #107 targets) but
    does not and cannot remove the 3-point-fit residual itself; see
    dispatch_calibration()'s own docstring and data/uncertainty_results.
    json's calibration.mid_pre_slope_unaveraging_fix for the quantified
    before/after at both RTE and soil calibration points."""
    rte_factor_mid = 1 + rte_slope_mid * (rte - RTE_NOM)
    rte_factor_pre = 1 + rte_slope_pre * (rte - RTE_NOM)
    # Codex review, issue #89, pass 1: loss and prod_noise both perturb the
    # SAME physical quantity (true generation relative to nominal), so they
    # must be combined into ONE shortfall variable before a slope side is
    # selected, not applied as two independent multiplicative factors each
    # choosing its own side. Before this fix (equal slopes on both sides)
    # this cost nothing; with genuinely different loss/surplus slopes, two
    # independent factors introduce a first-order spurious bias whenever
    # loss and prod_noise partially offset (e.g. a soiling loss coinciding
    # with an equal-and-opposite production-measurement surplus draw) --
    # quantified at this household's real prod_sigma/lossB: a +0.28% mean
    # bias across the Monte Carlo's own draw distribution, confirmed by a
    # 2M-draw simulation, eliminated by this combination.
    # true_relative_generation = (1 - loss) * prod_noise (soiling reduces
    # the true physical generation by `loss`; prod_noise then represents
    # how far the MEASURED baseline is from that true value) ->
    # combined_x = 1 - true_relative_generation = loss + x - loss*x, the
    # exact (not first-order-approximated) combined shortfall, where
    # x = 1 - prod_noise.
    x = 1 - prod_noise
    combined_x = loss + x - loss * x
    combined_x_arr = np.asarray(combined_x)
    soil_slope_mid_for = np.where(combined_x_arr >= 0, soil_slope_loss_mid, soil_slope_surplus_mid)
    soil_slope_pre_for = np.where(combined_x_arr >= 0, soil_slope_loss_pre, soil_slope_surplus_pre)
    soil_factor_mid = 1 + soil_slope_mid_for * combined_x
    soil_factor_pre = 1 + soil_slope_pre_for * combined_x
    mid_adjusted = mid * rte_factor_mid * soil_factor_mid
    pre_adjusted = pre * rte_factor_pre * soil_factor_pre
    return c * mid_adjusted + (1 - c) * pre_adjusted


def full_monte_carlo(pre, mid, rte_slope_mid, rte_slope_pre,
                      soil_slope_loss_mid, soil_slope_loss_pre,
                      soil_slope_surplus_mid, soil_slope_surplus_pre,
                      lossA, lossB, prod_sigma, N=5000, seed=43):
    # draw_inputs()'s own `rte_slope`/`soil_slope` parameters are dead code
    # (unused in its body -- confirmed by reading it -- out of scope for
    # this issue to clean up). It can no longer take a single value per
    # lever, so it gets whichever of the two new slopes per lever is more
    # directly analogous to the old ones (the mid-side); still unused below.
    esc, fade, price, c, rte, loss, prod_noise = draw_inputs(
        N, seed, rte_slope_mid, soil_slope_loss_mid, lossA, lossB, prod_sigma)
    save1 = save1_of(c, rte, loss, prod_noise, pre, mid,
                      rte_slope_mid, rte_slope_pre,
                      soil_slope_loss_mid, soil_slope_loss_pre,
                      soil_slope_surplus_mid, soil_slope_surplus_pre)
    if np.any(save1 <= 0):
        raise SystemExit("full_monte_carlo: a draw produced a non-positive "
                          "year-1 battery saving -- band too wide or a sign "
                          "error; refusing to publish a payback computed "
                          "against zero-or-negative savings")
    payback = np.full(N, np.nan)
    npv = {dr: {10: np.zeros(N), MIDTERM_YR: np.zeros(N)} for dr in DISC_RATES}
    for i in range(N):
        cum = 0.0
        for yr in range(1, HORIZON_YR + 1):
            s_yr = save1[i] * ((1 + esc[i]) ** (yr - 1)) * ((1 - fade[i]) ** (yr - 1))
            cum += s_yr
            if np.isnan(payback[i]) and cum >= price[i]:
                payback[i] = yr - 1 + (price[i] - (cum - s_yr)) / s_yr
            for dr in DISC_RATES:
                disc_s = s_yr / ((1 + dr) ** yr)
                if yr <= 10:
                    npv[dr][10][i] += disc_s
                if yr <= MIDTERM_YR:
                    npv[dr][MIDTERM_YR][i] += disc_s
    for dr in DISC_RATES:
        for h in (10, MIDTERM_YR):
            npv[dr][h] -= price

    def pct(arr, p):
        return round(float(np.percentile(arr, p)))

    # A finite N-draw Monte Carlo observing zero failures does not itself prove
    # a true probability of exactly 1.0 (adversarial review pass 1, finding 2:
    # "100%" overstates certainty a 5,000-draw sample cannot establish). Report
    # the raw counts alongside a one-sided 95% Clopper-Pearson bound so a
    # near-1-or-0 point estimate carries its own finite-sample caveat.
    from scipy import stats as _stats

    def _clopper_pearson_bounds(k, n, alpha=0.05):
        lower = float(_stats.beta.ppf(alpha, k, n - k + 1)) if k > 0 else 0.0
        upper = float(_stats.beta.ppf(1 - alpha, k + 1, n - k)) if k < n else 1.0
        return lower, upper

    n_within_10 = int(np.sum(payback <= WARRANTY_YR))
    n_within_15 = int(np.sum(payback <= MIDTERM_YR))
    n_never = int(np.sum(np.isnan(payback)))
    ci10_lo, _ = _clopper_pearson_bounds(n_within_10, N)
    ci15_lo, _ = _clopper_pearson_bounds(n_within_15, N)
    _, ci_never_hi = _clopper_pearson_bounds(n_never, N)

    out = {
        "N": N,
        "seed": seed,
        "payback_median": round(float(np.nanmedian(payback)), 1),
        "payback_p10": round(float(np.nanpercentile(payback, 10)), 1),
        "payback_p90": round(float(np.nanpercentile(payback, 90)), 1),
        "prob_within_warranty_10yr": round(float(np.mean(payback <= WARRANTY_YR)), 3),
        "prob_within_15yr": round(float(np.mean(payback <= MIDTERM_YR)), 3),
        "prob_never_within_25yr": round(float(np.mean(np.isnan(payback))), 3),
        "n_within_warranty_10yr": n_within_10,
        "n_within_15yr": n_within_15,
        "n_never_within_25yr": n_never,
        "prob_within_warranty_10yr_ci95_lower": round(ci10_lo, 4),
        "prob_within_15yr_ci95_lower": round(ci15_lo, 4),
        "prob_never_within_25yr_ci95_upper": round(ci_never_hi, 4),
        "finite_sample_caveat": (
            f"{n_within_10}/{N} modeled draws repaid within the {WARRANTY_YR}-yr "
            "warranty; a finite Monte Carlo sample observing zero failures does "
            "not itself establish a true probability of exactly 1.0 -- the "
            "one-sided 95% Clopper-Pearson lower bound above is the decision-"
            "relevant, sample-size-honest statement (and the mirrored upper "
            "bound for 'never')."),
        "epistemic_caveat": (
            "The Clopper-Pearson bound above quantifies ONLY sampling error "
            "within this exact model -- it is conditional on the seven input "
            "distributions (and their independence assumption) being correct, "
            "several of which are themselves labeled 'estimated' rather than "
            "'measured' in the inputs section above (adversarial review pass "
            "2, finding 1). It is not an unconditional real-world probability "
            "of repayment: it does not cover uncertainty in the model's own "
            "form or input ranges. Report this as a conditional, model-"
            "relative statement, never as a bare real-world guarantee."),
        "npv": {
            f"{int(dr*100)}pct": {
                "10yr": {"median": pct(npv[dr][10], 50), "p10": pct(npv[dr][10], 10),
                         "p90": pct(npv[dr][10], 90)},
                "15yr": {"median": pct(npv[dr][MIDTERM_YR], 50), "p10": pct(npv[dr][MIDTERM_YR], 10),
                         "p90": pct(npv[dr][MIDTERM_YR], 90)},
            } for dr in DISC_RATES
        },
        "save1_median": round(float(np.median(save1))),
        "save1_p10": round(float(np.percentile(save1, 10))),
        "save1_p90": round(float(np.percentile(save1, 90))),
    }
    return out


# ---------------------------------------------------------------------------
# tornado: deterministic one-lever-at-a-time sweep, same methodology as
# extended_findings.py's tornado_battery (hold everything else at a nominal
# scenario, vary one lever across its own band, rank by payback-year swing)
# ---------------------------------------------------------------------------
def tornado(pre, mid, rte_slope_mid, rte_slope_pre,
            soil_slope_loss_mid, soil_slope_loss_pre,
            soil_slope_surplus_mid, soil_slope_surplus_pre,
            lossA, lossB, prod_sigma):
    esc_nom = (ESC_LO + ESC_HI) / 2
    fade_nom = (FADE_LO + FADE_HI) / 2
    price_nom = (PRICE_LO + PRICE_HI) / 2
    c_nom = EV_PERSIST_A / (EV_PERSIST_A + EV_PERSIST_B)   # Beta(2,1) mean = 0.667
    rte_nom = RTE_NOM
    loss_nom = (lossA + lossA + lossB) / 3                  # triangular mean
    prod_nom = 1.0

    def save1(c=c_nom, rte=rte_nom, loss=loss_nom, prod=prod_nom):
        return save1_of(c, rte, loss, prod, pre, mid,
                        rte_slope_mid, rte_slope_pre,
                        soil_slope_loss_mid, soil_slope_loss_pre,
                        soil_slope_surplus_mid, soil_slope_surplus_pre)

    def pb(save, esc=esc_nom, fade=fade_nom, price=price_nom):
        return payback_of(save, esc, fade, price)

    nominal_payback = pb(save1())

    levers = {
        "install_cost": (pb(save1(), price=PRICE_LO), pb(save1(), price=PRICE_HI)),
        "escalation": (pb(save1(), esc=ESC_LO), pb(save1(), esc=ESC_HI)),
        "degradation": (pb(save1(), fade=FADE_HI), pb(save1(), fade=FADE_LO)),
        "ev_persistence": (pb(save1(c=0.0)), pb(save1(c=1.0))),
        "soiling": (pb(save1(loss=lossB)), pb(save1(loss=lossA))),
        "round_trip_efficiency": (pb(save1(rte=RTE_LO)), pb(save1(rte=RTE_HI))),
        "production_measurement_spread": (
            pb(save1(prod=1 - 1.645 * prod_sigma)),
            pb(save1(prod=1 + 1.645 * prod_sigma))),
    }
    tor = {}
    for name, (lo, hi) in levers.items():
        rng_lo, rng_hi = sorted((float(lo), float(hi)))
        tor[name] = {"payback_range_yr": [round(rng_lo, 1), round(rng_hi, 1)],
                     "swing_yr": round(rng_hi - rng_lo, 1),
                     # issue #89: the two-sided soil-slope fix (plus the
                     # Codex-review combined-shortfall correction, pass 1)
                     # moves this lever's swing at full precision
                     # (production_measurement_spread: 0.0680 -> 0.0761 yr)
                     # but not at the published 1dp rounding above -- exposed
                     # unrounded here so the shift AC4 asks to "report
                     # explicitly" is visible in the artifact itself, not
                     # just a commit message.
                     "swing_yr_precise": rng_hi - rng_lo}
    ranked = sorted(tor, key=lambda k: -tor[k]["swing_yr"])
    return {"nominal_payback_yr": round(float(nominal_payback), 1), "levers": tor,
            "ranked_by_swing": ranked}


ESC_DOWNSIDE_GRID_PCT = (0.00, -0.03, -0.06, -0.09, -0.12)


def escalation_downside_sensitivity(pre, mid, rte_slope_mid, rte_slope_pre,
                                     soil_slope_loss_mid, soil_slope_loss_pre,
                                     soil_slope_surplus_mid, soil_slope_surplus_pre,
                                     lossA, lossB):
    """Issue #59 (Codex adversarial review, third pass): documenting the 0%
    floor as an unproven, inherited assumption while the Monte Carlo can
    still never SAMPLE a negative escalation draw leaves a reader unable to
    see what a real downside would cost -- labeling the limitation is not
    the same as showing its consequence. This is NOT a new probability
    distribution (no evidence supports weighting these scenarios, which is
    exactly why ESC_LO/full_monte_carlo/tornado are unchanged by this
    function); it is a plain, labeled WHAT-IF grid, mirroring dsgs_vpp_
    backtest.py's own precedent for an additive, clearly-scoped sensitivity
    that never touches the primary probability-weighted figures. Computed at
    the EXACT SAME nominal save1/fade/price tornado()'s escalation LEVER
    itself sweeps (Beta(2,1)-blended EV persistence, nominal RTE/soiling/
    production -- an earlier draft used the raw post-behavior `mid` alone
    instead of this blended nominal save1, a self-inconsistency caught in
    review: that draft's own +0% grid point disagreed with what it CLAIMED
    to match by over a year). This grid's +0% point (esc=0, same as
    ESC_LO) is IDENTICAL, by construction, to tornado()'s own escalation
    lever's ESC_LO payback endpoint -- NOT to tornado()'s overall
    nominal_payback_yr, which uses esc_nom = (ESC_LO+ESC_HI)/2 = 6% rather
    than 0% for the escalation dimension specifically, a genuinely
    different scenario point, not a second inconsistency to fix."""
    fade_nom = (FADE_LO + FADE_HI) / 2
    price_nom = (PRICE_LO + PRICE_HI) / 2
    c_nom = EV_PERSIST_A / (EV_PERSIST_A + EV_PERSIST_B)
    loss_nom = (lossA + lossA + lossB) / 3
    save1_nom = save1_of(c_nom, RTE_NOM, loss_nom, 1.0, pre, mid,
                          rte_slope_mid, rte_slope_pre,
                          soil_slope_loss_mid, soil_slope_loss_pre,
                          soil_slope_surplus_mid, soil_slope_surplus_pre)
    grid = {}
    for pct in ESC_DOWNSIDE_GRID_PCT:
        pb = payback_of(save1_nom, pct, fade_nom, price_nom)
        grid[f"{pct:+.0%}"] = {
            "payback_yr": (round(float(pb), 1) if not np.isnan(pb) else None),
            "within_10yr_warranty": (bool(pb <= WARRANTY_YR) if not np.isnan(pb) else False),
        }
    return {
        "not_a_probability_distribution": (
            "This grid holds no evidence-backed weight for any point -- it "
            "exists because a reader cannot judge a downside risk that is "
            "described but never shown. See dispatch_policy_adherence_note "
            "and escalation_two_sided_evidence_note for why no probability-"
            "weighted negative-escalation input is modeled instead."),
        "base_case": "the exact same nominal save1/fade/price tornado()'s escalation lever sweeps (Beta(2,1)-blended EV persistence, nominal RTE/soiling/production) -- this grid's +0% point equals tornado()'s escalation lever's ESC_LO payback endpoint by construction, not tornado()'s overall nominal_payback_yr (which uses esc=6%, not 0%, for the escalation dimension)",
        "grid": grid,
        # issue #107 review, round 2: grid's own payback_yr is rounded to
        # 1dp, which can hide a real but small caller-level bug (e.g. a
        # mid/pre slope argument swap) if it happens not to cross a
        # rounding boundary for a given household's own calibration
        # numbers -- confirmed directly: swapping soil_slope_loss_mid/pre
        # at this function's own call site shifts save1_nom by ~$0.61 at
        # this household's real calibration, with EVERY grid point's
        # rounded payback_yr unchanged. Exposed here unrounded, mirroring
        # tornado()'s existing swing_yr_precise convention for the
        # identical reason.
        "save1_nom_precise": save1_nom,
    }


def reconcile_tornado(new_tornado, old_tornado_battery):
    old_ranked = old_tornado_battery["ranked_by_swing"]
    old_levers = old_tornado_battery["levers"]
    common = [k for k in ("install_cost",) if k in old_levers]
    notes = []
    for k in common:
        notes.append(
            f"{k}: old swing {old_levers[k]['swing_yr']} yr "
            f"(range {old_levers[k]['payback_range_yr']}) vs new swing "
            f"{new_tornado['levers'][k]['swing_yr']} yr "
            f"(range {new_tornado['levers'][k]['payback_range_yr']}) -- "
            "same lever (install price), same band, so a close match is "
            "expected; both models read savings from the same "
            "post_behavior.mid.battery_marginal-rooted base case.")
    notes.append(
        "escalation: old model's 'escalation_5yr_avg' swept 0-8% with an "
        f"average-uplift approximation (swing {old_levers.get('escalation_5yr_avg', {}).get('swing_yr')} yr); "
        f"this model sweeps the full 0-12% ladder-bound range directly "
        f"(swing {new_tornado['levers']['escalation']['swing_yr']} yr) -- a "
        "wider band on the same lever, so a larger swing here is an "
        "expected reordering, not a disagreement.")
    notes.append(
        "ev_persistence generalizes the old model's 2-point 'post_behavior' "
        f"lever (swing {old_levers.get('post_behavior', {}).get('swing_yr')} yr, "
        "G vs G_POST) into a continuous Beta(2,1) blend across the SAME two "
        f"endpoints; this model's swing ({new_tornado['levers']['ev_persistence']['swing_yr']} yr) "
        "should be of the same order since it is rooted in the identical "
        "pair of measured marginals.")
    notes.append(
        "dispatch_policy (old model's largest lever, "
        f"swing {old_levers.get('dispatch_policy', {}).get('swing_yr')} yr) has no "
        "counterpart here: it is a discrete DESIGN CHOICE the household "
        "makes (evening/twowin/greedy dispatch), not an uncertain physical "
        "input to propagate, so this Monte Carlo holds it fixed at greedy "
        "(the recommended policy) throughout, matching the old model's own "
        "base case.")
    notes.append(
        "soiling, round_trip_efficiency and production_measurement_spread "
        "have no counterpart in the old tornado at all -- they are new "
        "levers this issue adds, not previously quantified anywhere in the "
        "repo.")
    return {
        "old_ranked_by_swing": old_ranked,
        "new_ranked_by_swing": new_tornado["ranked_by_swing"],
        "notes": notes,
    }


def build(N_full=5000, seed_full=43, N_legacy=5000, seed_legacy=42):
    calib = dispatch_calibration()
    pre, mid = calib["pre_nominal"], calib["mid_nominal"]
    # issue #107: mid-/pre-specific slopes, no longer averaged into one
    # value before being applied to the c-blended base_marginal -- see
    # save1_of()'s own docstring for why this matters.
    rte_slope_mid, rte_slope_pre = calib["rte_slope_mid"], calib["rte_slope_pre"]
    soil_slope_loss_mid, soil_slope_loss_pre = (
        calib["soil_slope_loss_mid"], calib["soil_slope_loss_pre"])
    soil_slope_surplus_mid, soil_slope_surplus_pre = (
        calib["soil_slope_surplus_mid"], calib["soil_slope_surplus_pre"])
    lossA, lossB = calib["lossA"], calib["lossB"]

    # cross-check against the committed dispatch artifact (same fail-loud
    # convention as deep_analyses.py's _base_save; this is a regression pin
    # against a REGENERABLE committed artifact, not an invented value). Uses
    # the SINGLE-PASS recomputation, matching battery_dispatch_policies.py's
    # own (out-of-scope-to-change) method exactly -- comparing the STEADY-
    # STATE pre/mid (used everywhere else in this module, Codex review pass 1
    # finding 2) against a single-pass committed figure would show a
    # spurious ~$1 gap that is really just the two methods' known SOC-
    # boundary difference, not a stale artifact.
    committed_dispatch = _committed("battery_dispatch_policies.json")
    committed_pre = float(committed_dispatch["pw3"]["greedy"]["save"])
    committed_mid = float(committed_dispatch["post_behavior"]["mid"]["battery_marginal"])
    pre_sp = calib["pre_nominal_single_pass"]
    mid_sp = calib["mid_nominal_single_pass"]
    if abs(pre_sp - committed_pre) > 1.0 or abs(mid_sp - committed_mid) > 1.0:
        raise SystemExit(
            "uncertainty_propagation: this run's recomputed pre/mid battery "
            f"marginals (${pre_sp:,.2f}/${mid_sp:,.2f}) disagree with the "
            f"committed battery_dispatch_policies.json (${committed_pre:,.2f}/"
            f"${committed_mid:,.2f}) by more than $1 -- the committed dispatch "
            "artifact is stale relative to this run's usage.csv/household.yaml, "
            "or the calibration re-simulation has drifted from run_batt's own "
            "behavior. Regenerate battery_dispatch_policies.json first.")

    tou_spread = _committed("tou_spread.json")
    ladder_max_pct = max(float(k.rstrip("%")) for k in tou_spread["battery"]["uniform_ladder"])
    if abs(ladder_max_pct / 100 - ESC_HI) > 1e-9:
        raise SystemExit(
            f"uncertainty_propagation: ESC_HI={ESC_HI} no longer matches "
            f"tou_spread.json's own uniform_ladder ceiling ({ladder_max_pct}%) "
            "-- update ESC_HI (and this script's docstring) to track it.")

    prod_stats = production_spread_stats()
    prod_sigma = prod_stats["prod_sigma_used"]

    mc = full_monte_carlo(pre, mid, rte_slope_mid, rte_slope_pre,
                          soil_slope_loss_mid, soil_slope_loss_pre,
                          soil_slope_surplus_mid, soil_slope_surplus_pre,
                          lossA, lossB, prod_sigma, N=N_full, seed=seed_full)
    tor = tornado(pre, mid, rte_slope_mid, rte_slope_pre,
                 soil_slope_loss_mid, soil_slope_loss_pre,
                 soil_slope_surplus_mid, soil_slope_surplus_pre,
                 lossA, lossB, prod_sigma)
    esc_downside = escalation_downside_sensitivity(pre, mid, rte_slope_mid, rte_slope_pre,
                                                   soil_slope_loss_mid, soil_slope_loss_pre,
                                                   soil_slope_surplus_mid, soil_slope_surplus_pre,
                                                   lossA, lossB)

    old_deep = _committed("deep_results.json")
    # deep_analyses.py's own _base_save() reads the COMMITTED, already-rounded
    # post_behavior.mid.battery_marginal (an integer dollar figure written by
    # battery_dispatch_policies.py) as its base case -- not an unrounded
    # recomputation. Reproducing it exactly means starting from that same
    # rounded figure, not this script's own higher-precision `mid` (which
    # differs from it by a few cents and would shift a stochastic NPV sum by
    # a few dollars after 5000 draws, exactly as observed before this fix).
    legacy = legacy_reproduction(committed_mid, seed=seed_legacy, N=N_legacy)
    old_mc = old_deep["monte_carlo"]
    diffs = {k: (legacy[k] - old_mc[k]) for k in old_mc}
    matches = all(abs(v) < 1e-9 for v in diffs.values())
    if not matches:
        raise SystemExit(
            "uncertainty_propagation: legacy_reproduction() no longer "
            f"reproduces data/deep_results.json's monte_carlo block exactly "
            f"(diffs {diffs}) -- the base_save input (post_behavior.mid."
            "battery_marginal) or the reimplemented algorithm has drifted "
            "from deep_analyses.py's own block.")

    old_extended = _committed("extended_results.json")
    reconciliation = reconcile_tornado(tor, old_extended["tornado_battery"])

    out = {
        "meta": {
            "N_full_monte_carlo": N_full,
            "seed_full_monte_carlo": seed_full,
            "N_legacy_reproduction": N_legacy,
            "seed_legacy_reproduction": seed_legacy,
            "warranty_yr": WARRANTY_YR,
            "midterm_yr": MIDTERM_YR,
            "horizon_yr_for_never": HORIZON_YR,
            "discount_rates": list(DISC_RATES),
        },
        "inputs": {
            "escalation": {"dist": "Uniform", "low": ESC_LO, "high": ESC_HI,
                          "evidential_basis": "estimated -- data/tou_spread.json's "
                          "battery.uniform_ladder bounding range (3/5/8/12%); the "
                          "underlying escalation TREND is itself 'not determined' "
                          "per that artifact's per_period.summer/winter verdicts. "
                          "issue #59: the 0% floor was CHECKED against a two-sided "
                          "alternative (see this module's own docstring, section "
                          "'ISSUE #59', part (b), and escalation_two_sided_"
                          "evidence_note below), but that check did NOT find "
                          "evidence PROVING the floor correct -- it remains an "
                          "INHERITED, unproven modeling assumption from "
                          "deep_analyses.py's original design, not an evidence-"
                          "backed one; a real, stated limitation, not resolved "
                          "by this issue"},
            "degradation_fade": {"dist": "Uniform", "low": FADE_LO, "high": FADE_HI,
                                 "evidential_basis": "manufacturer (Powerwall 3) "
                                 "warranty degradation curve; unchanged from "
                                 "deep_analyses.py's existing Monte Carlo"},
            "install_cost": {"dist": "Uniform", "low": PRICE_LO, "high": PRICE_HI,
                             "evidential_basis": "quoted installer cost bound; "
                             "unchanged from deep_analyses.py's existing Monte Carlo"},
            "ev_behavior_persistence": {"dist": "Beta", "a": EV_PERSIST_A,
                                        "b": EV_PERSIST_B, "mean": EV_PERSIST_A / (EV_PERSIST_A + EV_PERSIST_B),
                                        "evidential_basis": "estimated blend between "
                                        "battery_dispatch_policies.json's pw3.greedy.save "
                                        "(no behavior, c=0) and post_behavior.mid."
                                        "battery_marginal (full behavior, c=1) -- the only "
                                        "two compliance points the pipeline computes. This is "
                                        "a MODELED, not-yet-implemented change (the report "
                                        "recommends it as pending, not observed as sustained); "
                                        "the mild skew toward c=1 reflects only the indirect "
                                        "evidence that ~80% of this household's EV charging "
                                        "already lands in favorable windows unshifted (Codex "
                                        "review pass 3 finding: an earlier draft wrongly "
                                        "called this an already-observed, completed behavior "
                                        "to justify a more confident prior)"},
            "soiling_loss_fraction": {"dist": "Triangular", "low": lossA, "mode": lossA,
                                      "high": lossB, "evidential_basis":
                                      "data/soiling_results.json's split evidence, reframed "
                                      "relative to the observed baseline (Codex review pass "
                                      "2): 0 = holds at scenario_A_this_years_evidence's rate "
                                      "(already embedded in the measured Generation column), "
                                      "lossB = the INCREMENTAL loss to reach scenario_B_2024_"
                                      "cleaning_evidence's worse rate"},
            "round_trip_efficiency": {"dist": "Uniform", "low": RTE_LO, "high": RTE_HI,
                                      "evidential_basis": "engineering estimate around "
                                      "the Powerwall 3 nameplate 90% round-trip spec "
                                      "(battery_dispatch_policies.py's ETA=sqrt(0.90)); "
                                      "not independently measured from this household's data"},
            "production_measurement_spread": {"dist": "Normal", "mean": 1.0,
                                              "sd": prod_sigma, "evidential_basis":
                                              "empirical -- data/threeway_production_"
                                              "validation.csv's annual relative gap "
                                              "between two independent monitoring sources",
                                              "stats": prod_stats},
        },
        "correlation_assumption": (
            "All seven inputs are drawn independently. No correlation between "
            "them is measured anywhere in this repo. Two plausible real "
            "correlations both point the SAME direction: (a) escalation and "
            "soiling could positively correlate (a warmer/drier pattern "
            "raising both wildfire-driven rate escalation and soiling "
            "accumulation); (b) round-trip efficiency and capacity fade both "
            "trend with cell aging/heat, so a hard-cycled or hot-climate pack "
            "would tend to show both together. Modeling all seven as "
            "independent therefore likely UNDERSTATES the true probability of "
            "the worst-of-both-worlds tail (e.g. high escalation with high "
            "soiling, or low RTE with high fade) relative to reality -- "
            "independent sampling under-represents scenarios where multiple "
            "bad draws share a common root cause. No numeric correction is "
            "applied; no data in this repo quantifies either correlation."
        ),
        "dispatch_policy_adherence_note": (
            "Issue #59: whether the Powerwall's own automation reliably "
            "EXECUTES the chosen greedy dispatch policy (distinct from WHICH "
            "policy to choose, already addressed above) is NOT modeled here. "
            "Checked for a citable adherence/no-show rate from Tesla, an "
            "industry report, or an independent monitoring study; none "
            "exists -- the one quantitative Powerwall reliability figure "
            "found (a warranty-claims hardware failure rate, not a software/"
            "automation adherence rate) answers a different question, and "
            "CPUC ELRP load-impact evaluations aggregate across technologies "
            "without isolating residential battery storage. CLAUDE.md's "
            "no-guessing rule means this is left 'not determined' rather "
            "than modeled from an invented number -- see this module's own "
            "docstring, section 'ISSUE #59', part (a) for the full check."),
        "escalation_two_sided_evidence_note": (
            "Issue #59: the 0% floor on the escalation input above was "
            "checked against a two-sided alternative, not assumed. `esc` "
            "scales the battery's SAVING (proportional to the TOU SPREAD it "
            "arbitrages) uniformly, so the decision-relevant question is the "
            "SPREAD trend, not any one period's own absolute level -- and "
            "data/tou_spread.json's dedicated, structural-break-tested "
            "spread analysis reports that as 'not determined' in BOTH "
            "seasons, genuinely unknown rather than evidence for a positive "
            "or a zero-floored range. An earlier draft of this note wrongly "
            "cited per-TOU-cell absolute-level trends (on-peak rates rising) "
            "as evidence the floor was itself proven correct -- RETRACTED "
            "(Codex adversarial review, issue #59, first pass): winter "
            "on-peak and winter off-peak moved on nearly identical "
            "trajectories over the same window, which is exactly a case "
            "where both periods rising together leaves the spread roughly "
            "flat, not evidence it widened. What remains true, stated "
            "honestly: no cell or spread-level figure in this repo, at any "
            "rigor, shows a statistically significant NEGATIVE trend for "
            "this household's own tariff (super-off-peak's negative point "
            "estimate has a 95% CI crossing zero). Externally, a real CA "
            "IOU electric-rate decline has happened (PG&E, 2024-2025, "
            "driven by AB 1054 wildfire-capex cost roll-off, a real citable "
            "magnitude) but that mechanism is not shown to apply to SDG&E "
            "specifically -- SDG&E's own electric delivery rate rose over a "
            "comparable window in the same research -- so PG&E's number "
            "isn't validly transferable into an SDG&E-specific negative "
            "bound without fabricating one. DECISION: the 0% floor is kept, "
            "but re-labeled honestly as an INHERITED assumption from "
            "deep_analyses.py's original design, not one this repo's "
            "evidence proves correct -- a real, stated LIMITATION of this "
            "model, not a resolved question. Replacing it with a different "
            "unevidenced negative number (self-invented or borrowed from "
            "PG&E) would trade one unproven assumption for another. See "
            "this module's own docstring, section 'ISSUE #59', part (b) "
            "for the full check, and section 'ISSUE #87 RESOLUTION' for why "
            "a real per-TOU-period dispatch-rerun escalation model was "
            "investigated and deferred (not built): on-peak resolves to a "
            "confident positive trend but super-off-peak's own trend is "
            "unresolved (a large negative point estimate, wide-crossing-"
            "zero CI), so a per-period model would inherit at least as "
            "much uncertainty as the direct spread test already found "
            "inadequate, not less; guarded by "
            "test_uncertainty_propagation.py's case_spread_trend_is_still_"
            "not_determined_so_esc_stays_a_blended_scalar, which fails once "
            "that changes. Because documenting this limitation is "
            "not the same as showing its consequence (Codex adversarial "
            "review, issue #59, third pass), see escalation_downside_"
            "sensitivity below for what a range of negative escalation "
            "rates would actually cost, as a labeled what-if, not a "
            "probability-weighted claim."),
        "escalation_downside_sensitivity": esc_downside,
        "calibration": {
            "pre_nominal": round(pre, 2),
            "mid_nominal": round(mid, 2),
            "pre_nominal_single_pass": round(pre_sp, 2),
            "mid_nominal_single_pass": round(mid_sp, 2),
            "steady_state_vs_single_pass_note": (
                "pre_nominal/mid_nominal use a steady-state-converged SOC "
                "boundary (Codex review pass 1, finding 2); pre_nominal_"
                "single_pass/mid_nominal_single_pass use the same single-pass "
                "method as the committed battery_dispatch_policies.json (which "
                "this issue does not modify) and are what the $1 tie-out below "
                "checks against -- the ~$1-2 gap between the two conventions "
                "is the known, expected size of this SOC-boundary effect, not "
                "a discrepancy to resolve further."),
            "committed_pre": committed_pre,
            "committed_mid": committed_mid,
            "generation_proxy_limitation_resolved": (
                "Issue #60 (previously a stated limitation here, filed from "
                "Codex review pass 1 finding 1 on issue #15): the soiling "
                "and production-measurement-spread calibrations previously "
                "scaled the Green Button 'Generation' column (net grid "
                "EXPORT, not gross PV production) directly, understating "
                "the true impact -- FIXED. See production_reconstruction "
                "below for the resolved method, an energy-conservation "
                "check, and the old-vs-new comparison confirming the "
                "'likely understates' hypothesis with a quantified "
                "mechanism (soil_slope_mid [loss-side] rose from "
                f"{calib['production_reconstruction']['old_vs_new_soil_slope']['old_export_only_scaling']['soil_slope_mid']} "
                f"to {calib['soil_slope_loss_mid']:.4f})."),
            "production_reconstruction": calib["production_reconstruction"],
            "soil_slope_two_sided_fix": (
                "Issue #89 (previously a stated limitation here, filed from "
                "Codex review pass 2 on issue #60): soil_slope_mid/pre used "
                "to be fit from a real dispatch rerun at ONE production LOSS "
                "point and applied linearly to both losses and surpluses "
                "(roughly half of production_measurement_spread's own Monte "
                "Carlo draws, prod_noise>1) -- FIXED. dispatch_calibration() "
                "now runs a real THIRD dispatch rerun at the mirrored "
                "surplus scenario (gen_scale=1+lossB) every time it "
                "regenerates, and fits soil_slope_surplus_mid/pre from it "
                "directly instead of extrapolating the loss-side slope: "
                f"soil_slope_loss_mid={calib['soil_slope_loss_mid']:.4f} vs "
                f"soil_slope_surplus_mid={calib['soil_slope_surplus_mid']:.4f} "
                "(the surplus-side slope is genuinely steeper in magnitude, "
                "not a rounding artifact of the same underlying number). "
                "See production_reconstruction.surplus_slope_fix for the "
                "quantified before/after: extrapolating the old loss-side "
                "slope to the real surplus point would have predicted "
                f"${calib['production_reconstruction']['surplus_slope_fix']['old_extrapolated_estimate']:,.2f} "
                "against the real measured "
                f"${calib['production_reconstruction']['surplus_slope_fix']['real_surplus_marginal']:,.2f} "
                "-- a "
                f"${calib['production_reconstruction']['surplus_slope_fix']['discrepancy_usd']:,.2f} "
                "("
                f"{calib['production_reconstruction']['surplus_slope_fix']['discrepancy_pct_of_mid_nominal']:.2f}%) "
                "gap that save1_of()'s new piecewise soil_slope_loss/"
                "soil_slope_surplus routing eliminates by construction, for "
                "every surplus-like draw at any magnitude, not just at this "
                "particular lossB point -- specifically the ONE-SIDED "
                "EXTRAPOLATION gap, not every discrepancy save1_of() has: a "
                "separate, smaller residual (Codex review, issue #89, pass "
                "2-3: ~$3.5, ~0.16%) that USED TO remain from averaging "
                "soil_slope_loss/surplus across mid/pre before applying "
                "them to the c-blended base_marginal (the same convention "
                "rte_slope used to follow) is now RESOLVED -- see "
                "mid_pre_slope_unaveraging_fix below (issue #107)."),
            "mid_pre_slope_unaveraging_fix": calib["mid_pre_slope_unaveraging_fix"],
            "rte_slope_mid": calib["rte_slope_mid"],
            "rte_slope_pre": calib["rte_slope_pre"],
            "soil_slope_loss_mid": calib["soil_slope_loss_mid"],
            "soil_slope_loss_pre": calib["soil_slope_loss_pre"],
            "soil_slope_surplus_mid": calib["soil_slope_surplus_mid"],
            "soil_slope_surplus_pre": calib["soil_slope_surplus_pre"],
            "lossA": lossA,
            "lossB": lossB,
            "rte_calibration_points_mid": calib["rte_points_mid"],
            "rte_calibration_points_pre": calib["rte_points_pre"],
            "soil_calibration_points_mid": calib["soil_points_mid"],
            "soil_calibration_points_pre": calib["soil_points_pre"],
            "method": ("linear factor(x) = 1 + slope*(x - nominal) fit by "
                      "least squares to 3 REAL dispatch reruns per lever per "
                      "behavior state (battery_dispatch_policies.run_batt/"
                      ".billed, ETA temporarily overridden for RTE draws, "
                      "generation scaled for soiling/production draws). "
                      "Issue #107: each side's (mid/pre) own slope is kept "
                      "separate and applied to that side's own nominal "
                      "value, then the two resulting dollar figures are "
                      "blended by c -- no longer averaged into one slope "
                      "before being applied to an already c-blended "
                      "base_marginal (see save1_of()'s own docstring and "
                      "mid_pre_slope_unaveraging_fix above). RTE's 3 points "
                      "(RTE_LO/RTE_NOM/RTE_HI) fit ONE slope across the "
                      "full range per side (RTE's response is expected to "
                      "be one continuous relationship) -- this fit is not "
                      "exact through all 3 points (see mid_pre_slope_"
                      "unaveraging_fix.rte_residual_note), unrelated to the "
                      "mid/pre-averaging issue #107 fixes. Issue #89: "
                      "soiling/production's 3 points (surplus at -lossB, "
                      "nominal at 0, loss at +lossB) fit TWO separate "
                      "2-point slopes per side instead -- soil_slope_loss "
                      "from {nominal, loss} and soil_slope_surplus from "
                      "{surplus, nominal}, each exact by construction -- "
                      "because scale_production()'s own reallocation is "
                      "genuinely piecewise (a loss reduces export first; a "
                      "surplus reduces import first), not one straight "
                      "line through all three points."),
        },
        "battery_marginal_only_full_model": mc,
        "tornado": {**tor, "reconciliation_vs_extended_results_tornado_battery": reconciliation},
        "legacy_reproduction": {
            "new_model_restricted_to_3_inputs": legacy,
            "committed_deep_results_monte_carlo": old_mc,
            "diffs": diffs,
            "matches": matches,
            "tolerance_note": ("exact equality (< 1e-9) is expected and "
                              "checked, not merely 'close': legacy_"
                              "reproduction() draws the SAME three inputs in "
                              "the SAME order from the SAME seeded "
                              "numpy.random.default_rng(42) instance and runs "
                              "the SAME algorithm as deep_analyses.py's own "
                              "monte_carlo block, so the two are bit-"
                              "identical up to the rounding both apply -- a "
                              "real discrepancy would mean this script's base "
                              "input or algorithm has drifted, not stochastic "
                              "noise (a fixed seed has none)."),
        },
    }
    return out


def main():
    out = build()
    with open("uncertainty_results.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out["battery_marginal_only_full_model"], indent=1))
    print("legacy reproduction matches committed deep_results.json:",
          out["legacy_reproduction"]["matches"])
    print("tornado ranked by swing:", out["tornado"]["ranked_by_swing"])


if __name__ == "__main__":
    main()
