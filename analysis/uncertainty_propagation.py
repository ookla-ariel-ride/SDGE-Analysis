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
   the real output). Routed through the SAME calibrated soil_slope as
   soiling (see CALIBRATION below and save1_of()'s own docstring), not
   applied as a direct 1:1 multiplier on the dollar saving: a production
   measurement discrepancy and a soiling-driven generation loss are
   uncertainty about the identical physical quantity (how much the array
   actually generated), and soiling's own calibration already measured how
   weakly a generation-level change moves the battery's marginal saving
   (adversarial review pass 2, finding 2 — an earlier draft assumed a full
   1:1 response, overstating this lever's impact by roughly 1/soil_slope).
   PROD_SIGMA is computed AT RUNTIME from
   data/threeway_production_validation.csv (365 days, two independent
   monitoring sources for the same array — PVOutput and the Enphase meter):
   the ANNUAL relative difference between the two full-year totals, not the
   larger day-to-day relative difference, because the annual gap tracks the
   MEAN daily gap rather than shrinking by 1/sqrt(365) — evidence the two
   meters disagree SYSTEMATICALLY (a persistent calibration/loss-accounting
   gap) rather than each day being an independent noisy draw that would
   average out. Both statistics are recorded in the output for inspection.

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
each lever; both pre- and post-behavior calibration runs land on nearly
identical fractional slopes (see the "calibration" block of the output
artifact for the actual numbers), which is itself the evidence that a single
slope, applied to whichever pre/post-behavior blend a given Monte Carlo draw
lands on, is a reasonable simplification rather than a fabricated shortcut.

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
# real-engine calibration (requires the private Green Button archive)
# ---------------------------------------------------------------------------
def dispatch_calibration():
    """Recompute the pre-/post-behavior 13.5 kWh battery marginal at nominal
    RTE/soiling, plus the RTE- and soiling-saving sensitivity slopes, from the
    real dispatch engine. Requires usage.csv/samA.csv/samB.csv and
    private/household.yaml in the working directory's ancestry (the standard
    private/verify sandbox) — this is the one function in this module that
    touches private data, exactly mirroring deep_analyses.py's own module-
    level read of usage.csv.

    Returns a dict: pre, mid (nominal marginals, $/yr), rte_slope,
    soil_slope (fractional-factor slopes), and the raw calibration points
    for the artifact's own "calibration" section.
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
            i2, e2, served, thru = bp.run_batt(d, imp_base, gen, CAP_KWH, "greedy", soc0=soc0)
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
        must see the identical generation input."""
        gen = gen0 * gen_scale
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
        i2, e2, _, _ = bp.run_batt(d, imp_base, gen0, CAP_KWH, "greedy")
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
    # Only two DISTINCT points: lossA == 0.0 is the same state as the nominal
    # (0.0) point by construction above, so including it a second time would
    # be a duplicate dict key, not a third calibration point.
    soil_points_mid = {0.0: mid_nominal,
                       lossB: marginal(imp_sh, gen_scale=1 - lossB)}
    soil_points_pre = {0.0: pre_nominal,
                       lossB: marginal(imp0, gen_scale=1 - lossB)}

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
    soil_slope_mid = slope_of(soil_points_mid, 0.0, mid_nominal)
    soil_slope_pre = slope_of(soil_points_pre, 0.0, pre_nominal)

    return {
        "pre_nominal": pre_nominal,
        "mid_nominal": mid_nominal,
        "pre_nominal_single_pass": pre_nominal_single_pass,
        "mid_nominal_single_pass": mid_nominal_single_pass,
        "behavior_save": float(base - b_sh),
        "lossA": lossA,
        "lossB": lossB,
        "rte_slope": (rte_slope_mid + rte_slope_pre) / 2,
        "rte_slope_mid": rte_slope_mid,
        "rte_slope_pre": rte_slope_pre,
        "soil_slope": (soil_slope_mid + soil_slope_pre) / 2,
        "soil_slope_mid": soil_slope_mid,
        "soil_slope_pre": soil_slope_pre,
        "rte_points_mid": rte_points_mid,
        "rte_points_pre": rte_points_pre,
        "soil_points_mid": soil_points_mid,
        "soil_points_pre": soil_points_pre,
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


def save1_of(c, rte, loss, prod_noise, pre, mid, rte_slope, soil_slope):
    """prod_noise is a multiplicative factor on TRUE generation (1.0 = the
    measured value is right; >1 = true generation is higher than measured).
    Adversarial review pass 2, finding 2: an earlier draft multiplied prod_noise
    directly into the dollar saving, implicitly assuming a 1:1 saving response
    to a generation change -- inconsistent with the CALIBRATED soil_slope,
    which found a real generation-scale change (soiling) moves the battery
    marginal by only ~0.057x the fractional change, not 1:1 (most of a
    generation swing changes exports/self-consumption directly, and only a
    damped fraction interacts with the battery's own arbitrage timing).
    Production-measurement uncertainty is uncertainty about the SAME physical
    quantity (true generation level) soiling calibration already measured the
    sensitivity of, so it is routed through the identical soil_slope rather
    than assumed proportional: a prod_noise of (1-x) is treated exactly like a
    soiling loss fraction of x, and a prod_noise of (1+x) like a loss of -x."""
    base_marginal = c * mid + (1 - c) * pre
    rte_factor = 1 + rte_slope * (rte - RTE_NOM)
    soil_factor = 1 + soil_slope * loss
    prod_factor = 1 + soil_slope * (1 - prod_noise)
    return base_marginal * rte_factor * soil_factor * prod_factor


def full_monte_carlo(pre, mid, rte_slope, soil_slope, lossA, lossB, prod_sigma,
                      N=5000, seed=43):
    esc, fade, price, c, rte, loss, prod_noise = draw_inputs(
        N, seed, rte_slope, soil_slope, lossA, lossB, prod_sigma)
    save1 = save1_of(c, rte, loss, prod_noise, pre, mid, rte_slope, soil_slope)
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
def tornado(pre, mid, rte_slope, soil_slope, lossA, lossB, prod_sigma):
    esc_nom = (ESC_LO + ESC_HI) / 2
    fade_nom = (FADE_LO + FADE_HI) / 2
    price_nom = (PRICE_LO + PRICE_HI) / 2
    c_nom = EV_PERSIST_A / (EV_PERSIST_A + EV_PERSIST_B)   # Beta(2,1) mean = 0.667
    rte_nom = RTE_NOM
    loss_nom = (lossA + lossA + lossB) / 3                  # triangular mean
    prod_nom = 1.0

    def save1(c=c_nom, rte=rte_nom, loss=loss_nom, prod=prod_nom):
        return save1_of(c, rte, loss, prod, pre, mid, rte_slope, soil_slope)

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
                     "swing_yr": round(rng_hi - rng_lo, 1)}
    ranked = sorted(tor, key=lambda k: -tor[k]["swing_yr"])
    return {"nominal_payback_yr": round(float(nominal_payback), 1), "levers": tor,
            "ranked_by_swing": ranked}


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
    rte_slope, soil_slope = calib["rte_slope"], calib["soil_slope"]
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

    mc = full_monte_carlo(pre, mid, rte_slope, soil_slope, lossA, lossB,
                          prod_sigma, N=N_full, seed=seed_full)
    tor = tornado(pre, mid, rte_slope, soil_slope, lossA, lossB, prod_sigma)

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
                          "per that artifact's per_period.summer/winter verdicts"},
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
            "generation_proxy_limitation": (
                "The soiling and production-measurement-spread calibrations "
                "scale the Green Button 'Generation' column (net grid EXPORT, "
                "not gross PV production -- self-consumed solar never crosses "
                "the meter and is invisible in this dataset) directly by the "
                "loss/noise fraction, rather than reconstructing true gross "
                "production from an independent load source and reallocating "
                "the shortfall against export before spilling into import "
                "(Codex review pass 1, finding 1). Because true production "
                "during an exporting interval equals export plus whatever "
                "load was simultaneously self-consumed, this likely "
                "UNDERSTATES the magnitude of both slopes somewhat (a given "
                "fractional production loss removes more energy than a "
                "same-fraction cut to export alone would). The calibrated "
                "slopes are already small (see soil_slope below); a full fix "
                "needs the household's independent gross-load series (samA/"
                "samB) to reconstruct production and is filed as a follow-up "
                "rather than attempted here (issue #60)."),
            "rte_slope_mid": calib["rte_slope_mid"],
            "rte_slope_pre": calib["rte_slope_pre"],
            "rte_slope_used": rte_slope,
            "soil_slope_mid": calib["soil_slope_mid"],
            "soil_slope_pre": calib["soil_slope_pre"],
            "soil_slope_used": soil_slope,
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
                      "generation scaled for soiling draws), then averaged "
                      "across the pre- and post-behavior calibration runs "
                      "since both land on nearly identical fractional slopes"),
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
