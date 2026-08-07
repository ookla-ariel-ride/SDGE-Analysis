#!/usr/bin/env python3
"""Tests for uncertainty_propagation.py (issue #15).

uncertainty_propagation.py's own module-level imports are private-data-free
(json/pathlib/sys/numpy only) -- behavior_rebuild/battery_dispatch_policies
are imported LAZILY, inside dispatch_calibration(), so the module itself
imports cleanly on any checkout. The household.PATH stub below is still
applied first, matching the repo convention (test_battery_sizing_curve.py),
so this file behaves the same way on a checkout with no private data at all:
cases that need the real archive gate on its presence and SKIP rather than
fail. This checkout DOES have the private archive staged, so those cases run
for real, not just import-clean.

Run from the repo root:  ./.venv/bin/python analysis/test_uncertainty_propagation.py
"""
import glob
import json
import os
import pathlib
import sys
import tempfile

import numpy as np

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

import behavior_rebuild as br  # noqa: E402
import uncertainty_propagation as up  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"
DATA = ROOT / "data"
# issue #60: samA.csv/samB.csv are read via BARE relative filenames inside
# uncertainty_propagation.py, matching threeway_production_validation.py's
# own load_sam_hourly() contract exactly (the private/verify sandbox's own
# documented run convention -- see this module's CALIBRATION docstring
# section) -- so any case that calls dispatch_calibration()/build() must
# os.chdir() into SANDBOX first, the SAME pattern test_threeway_production_
# validation.py's own archive-gated cases already use.
SANDBOX = ROOT / "private" / "verify"

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


def _require_sam():
    """issue #60: samA.csv/samB.csv, staged at SANDBOX by stage-private-
    data.sh, needed by any case that calls dispatch_calibration()/build()."""
    if not (SANDBOX / "samA.csv").is_file() or not (SANDBOX / "samB.csv").is_file():
        raise SkipCase(f"needs {SANDBOX}/samA.csv and samB.csv, which this "
                       "checkout does not have")


def _in_sandbox(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) with CWD temporarily at SANDBOX -- the
    os.chdir/finally dance dispatch_calibration()/build() need (issue #60),
    centralized here so every call site doesn't repeat it."""
    _require_sam()
    cwd = os.getcwd()
    os.chdir(str(SANDBOX))
    try:
        return fn(*args, **kwargs)
    finally:
        os.chdir(cwd)


EPS = 1e-9


def _committed(name):
    return json.loads((DATA / name).read_text())


# ---------------------------------------------------------------------------
# (a) pure-math unit tests -- no archive, no private data at all
# ---------------------------------------------------------------------------
@case
def case_payback_of_matches_hand_computation():
    # flat $1000/yr saving, no escalation, no fade, $3000 price -> exactly 3 yr
    pb = up.payback_of(1000.0, 0.0, 0.0, 3000.0)
    assert abs(pb - 3.0) < EPS, f"expected exactly 3.0 yr, got {pb}"
    # a price that is never reached within the horizon is NaN, not an error
    pb_never = up.payback_of(1.0, 0.0, 0.0, 1_000_000.0, horizon=5)
    assert np.isnan(pb_never), "a price never reached within the horizon must be NaN"
    return "payback_of reproduces a hand-computed flat-saving payback and returns NaN when unreached"


@case
def case_npv_of_matches_hand_computation():
    # flat $1000/yr for 2 years at 0% discount, minus $1500 price -> +$500
    npv = up.npv_of(1000.0, 0.0, 0.0, 1500.0, disc=0.0, horizon=2)
    assert abs(npv - 500.0) < EPS, f"expected exactly 500.0, got {npv}"
    return "npv_of matches a hand-computed zero-discount NPV"


@case
def case_esc_hi_matches_committed_tou_spread_ladder_ceiling():
    """AC1's escalation band cites data/tou_spread.json's own uniform_ladder
    ceiling; this pins the hardcoded ESC_HI to that artifact so a future
    regeneration of tou_spread.json cannot silently drift out of sync with
    this script's documented evidential basis."""
    ladder = _committed("tou_spread.json")["battery"]["uniform_ladder"]
    ladder_max_pct = max(float(k.rstrip("%")) for k in ladder)
    assert abs(ladder_max_pct / 100 - up.ESC_HI) < EPS, (
        f"ESC_HI={up.ESC_HI} no longer matches tou_spread.json's ladder "
        f"ceiling ({ladder_max_pct}%)")
    return f"ESC_HI ({up.ESC_HI}) matches tou_spread.json's uniform_ladder ceiling"


@case
def case_spread_trend_is_still_not_determined_so_esc_stays_a_blended_scalar():
    """Issue #87: `esc` scales save1 by ONE blended factor rather than each
    TOU period's own separately-measured rate path. On-peak resolves to a
    tight, zero-excluding, POSITIVE trend in both seasons (winter
    11.37%/yr, CI [7.75, 15.11]; summer 7.66%/yr, CI [1.73, 13.94]).
    Super-off-peak -- the charging leg, the other side of the arbitrage
    spread -- has a large NEGATIVE point estimate (~-21%/yr) but a CI wide
    enough to cross zero by a wide margin in both seasons: noisy, not
    resolved. Combining a confident on-peak trend with an unresolved
    super-off-peak point estimate to build a per-period spread trend would
    manufacture a specific-looking number that is really just whichever
    central estimate the noisy leg landed on. The spread-level
    structural-break test -- which differences the two legs directly, so
    that uncertainty carries through instead of hiding inside a confident
    on-peak number (see the (b) docstring above payback_of/npv_of) --
    currently returns "not determined" in both seasons: only 3 (summer) /
    4 (winter) independent post-break price levels exist, too few to both
    locate a breakpoint and estimate a slope from. A per-period model built
    from these per-cell trends would not be equivalent to esc's blended
    scalar -- the two legs' point estimates plainly differ -- but it would
    inherit at least as much uncertainty as the direct spread test already
    found inadequate, so issue #87 resolved as "document the gap" rather
    than "build the model". If a longer bill corpus ever gives
    tou_spread.py's test enough power to determine the spread trend,
    THIS CHECK FAILS -- that is the signal to revisit #87 for real
    per-period modeling, not to update this assertion."""
    spread = _committed("tou_spread.json")["delivery_spread"]
    for season in ("summer", "winter"):
        s = spread[season]
        assert s["verdict"] == "not determined", (
            f"tou_spread.json's {season} spread trend is no longer "
            f"'not determined' (now {s['verdict']!r}) -- the bill corpus has "
            f"grown enough to resolve it; issue #87's per-period escalation "
            f"model should be revisited with this new evidence, not deferred "
            f"again")
        post_break = s.get("post_break") or {}
        assert post_break.get("adequate") is False, (
            f"tou_spread.json's {season} post-break spread estimate is now "
            f"'adequate' (or post_break's shape changed to {post_break!r}) -- "
            f"same signal as above, issue #87 should be revisited")

    # The docstring's own claim -- on-peak is resolved and positive,
    # super-off-peak is unresolved -- verified against the cells directly
    # (Codex review, issue #87: "the test never verifies it").
    cells = _committed("tou_spread.json")["delivery_cell_escalation"]

    def _excludes_zero(ci):
        return ci is not None and not (ci[0] <= 0 <= ci[1])

    # The docstring quotes exact rates/CIs/r-squared/distinct-levels; pin
    # every one so a moderate (not just sign-flipping) drift in
    # tou_spread.json fails this case too, not just a wholesale reversal
    # (Codex review, issue #87: "either pin the quoted values or remove
    # exact figures from the prose").
    QUOTED = {
        "winter_on_peak": {"escalation_pct_yr": 11.37, "escalation_ci95_pct_yr": [7.75, 15.11], "r2": 0.973},
        "summer_on_peak": {"escalation_pct_yr": 7.66, "escalation_ci95_pct_yr": [1.73, 13.94]},
        "summer_super_off_peak": {"escalation_ci95_pct_yr": [-61.89, 62.02]},
        "winter_super_off_peak": {"escalation_ci95_pct_yr": [-49.24, 20.68]},
    }
    for cell_name, expected in QUOTED.items():
        actual = cells[cell_name]
        for field, exp_val in expected.items():
            act_val = actual[field]
            if isinstance(exp_val, list):
                assert all(abs(a - e) < 0.01 for a, e in zip(act_val, exp_val)), (
                    f"{cell_name}.{field} drifted from the quoted {exp_val} "
                    f"to {act_val} -- update the docstring's cited figures")
            else:
                assert abs(act_val - exp_val) < 0.01, (
                    f"{cell_name}.{field} drifted from the quoted {exp_val} "
                    f"to {act_val} -- update the docstring's cited figures")
    QUOTED_DISTINCT_LEVELS = {"summer": 3, "winter": 4}
    for season, expected_levels in QUOTED_DISTINCT_LEVELS.items():
        actual_levels = (spread[season].get("post_break") or {}).get("distinct_levels")
        assert actual_levels == expected_levels, (
            f"{season}'s post_break.distinct_levels drifted from the quoted "
            f"{expected_levels} to {actual_levels} -- update the docstring's "
            f"cited figures")

    for season in ("summer", "winter"):
        on_peak_ci = cells[f"{season}_on_peak"]["escalation_ci95_pct_yr"]
        assert _excludes_zero(on_peak_ci), (
            f"{season}_on_peak's CI {on_peak_ci} no longer excludes zero -- "
            f"the docstring's 'on-peak is a resolved, positive trend' claim "
            f"needs re-checking")
        assert cells[f"{season}_on_peak"]["escalation_pct_yr"] > 0, (
            f"{season}_on_peak's point estimate is no longer positive -- "
            f"same re-check")
        sop_ci = cells[f"{season}_super_off_peak"]["escalation_ci95_pct_yr"]
        assert not _excludes_zero(sop_ci), (
            f"{season}_super_off_peak's CI {sop_ci} now excludes zero -- it "
            f"has become a RESOLVED trend, not the noisy one this docstring "
            f"describes; issue #87's per-period model should be revisited "
            f"with this new evidence")

    return ("the spread trend remains 'not determined' in both seasons, "
            "on-peak stays a resolved positive trend and super-off-peak "
            "stays unresolved, so deferring a per-TOU-period escalation "
            "model (issue #87) remains the evidence-based call -- esc's "
            "single blended scalar stays the explicitly-flagged, unproven "
            "INHERITED assumption it already was, not itself proven correct")


@case
def case_production_spread_stats_from_committed_csv():
    """AC1's production-measurement-spread input: sanity-check the empirical
    stats computed from the real, committed three-way validation CSV (no
    private archive needed -- this file is public data/)."""
    stats = up.production_spread_stats()
    assert stats["days"] == 365, f"expected 365 days, got {stats['days']}"
    assert 0.0 < stats["prod_sigma_used"] < 0.10, (
        f"production sigma {stats['prod_sigma_used']} outside a sane 0-10% band")
    assert stats["prod_sigma_used"] == abs(stats["annual_rel_diff"]), (
        "prod_sigma_used must be the ANNUAL (systematic) relative gap, per "
        "the documented rationale, not the larger daily std")
    return f"production spread stats from the real CSV: sigma={stats['prod_sigma_used']:.4f}"


@case
def case_full_monte_carlo_is_deterministic_given_seed():
    """Byte-identical regeneration (AC8) depends on the RNG stream being
    fully determined by the seed and nothing else -- checked directly here on
    synthetic calibration inputs (no archive needed)."""
    kwargs = dict(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope_loss=-1.0,
                  soil_slope_surplus=-1.2, lossA=0.013, lossB=0.066,
                  prod_sigma=0.02, N=500, seed=99)
    r1 = up.full_monte_carlo(**kwargs)
    r2 = up.full_monte_carlo(**kwargs)
    assert r1 == r2, "two full_monte_carlo() calls with the same seed must match exactly"
    return "full_monte_carlo is deterministic given a fixed seed"


@case
def case_full_monte_carlo_reports_required_probabilities_and_npv_shape():
    """AC3 (warranty/15yr/never probabilities) and AC4 (NPV at two discount
    rates) as a structural contract, on synthetic calibration inputs."""
    r = up.full_monte_carlo(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope_loss=-1.0,
                            soil_slope_surplus=-1.2, lossA=0.013, lossB=0.066,
                            prod_sigma=0.02, N=500, seed=7)
    for k in ("prob_within_warranty_10yr", "prob_within_15yr", "prob_never_within_25yr"):
        assert k in r, f"missing required probability field {k!r}"
        assert 0.0 <= r[k] <= 1.0, f"{k}={r[k]} not a probability"
    assert r["prob_within_warranty_10yr"] <= r["prob_within_15yr"], (
        "P(within 10yr) must not exceed P(within 15yr)")
    assert set(r["npv"].keys()) == {f"{int(dr*100)}pct" for dr in up.DISC_RATES}, (
        f"expected NPV at exactly {up.DISC_RATES}, got {list(r['npv'])}")
    for dr_key, block in r["npv"].items():
        for horizon_key in ("10yr", "15yr"):
            assert set(block[horizon_key]) == {"median", "p10", "p90"}, (
                f"npv[{dr_key}][{horizon_key}] missing median/p10/p90")
    return "full_monte_carlo reports the three required probabilities and NPV at two discount rates"


@case
def case_tornado_reconciles_against_extended_results_tornado_battery():
    """AC6: reconcile against data/extended_results.json's own tornado_battery
    ranking (a real, committed artifact -- no private archive needed)."""
    old_tb = _committed("extended_results.json")["tornado_battery"]
    tor = up.tornado(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope_loss=-1.0,
                     soil_slope_surplus=-1.2, lossA=0.013, lossB=0.066, prod_sigma=0.02)
    rec = up.reconcile_tornado(tor, old_tb)
    assert rec["old_ranked_by_swing"] == old_tb["ranked_by_swing"]
    assert rec["new_ranked_by_swing"] == tor["ranked_by_swing"]
    assert len(rec["notes"]) >= 4, "reconciliation must discuss more than one lever"
    joined = " ".join(rec["notes"])
    assert "dispatch_policy" in joined, "must explain the excluded dispatch_policy lever"
    assert "install_cost" in joined, "must reconcile the one directly-shared lever"
    return "tornado reconciliation cites the old ranking and explains every divergence"


@case
def case_tornado_levers_cover_all_seven_ac1_inputs():
    tor = up.tornado(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope_loss=-1.0,
                     soil_slope_surplus=-1.2, lossA=0.013, lossB=0.066, prod_sigma=0.02)
    expected = {"install_cost", "escalation", "degradation", "ev_persistence",
                "soiling", "round_trip_efficiency", "production_measurement_spread"}
    assert set(tor["levers"]) == expected, (
        f"tornado levers {set(tor['levers'])} != the seven AC1 inputs {expected}")
    for name, lever in tor["levers"].items():
        assert lever["swing_yr"] >= 0, f"{name}: negative swing"
    return "the tornado sweeps exactly the seven AC1-required inputs"


@case
def case_legacy_reproduction_matches_committed_deep_results_exactly():
    """AC5, the load-bearing test: restrict the new model's RNG stream to
    EXACTLY the old three inputs (no others drawn at all) and check it
    reproduces data/deep_results.json's committed monte_carlo block to
    float-equality, not merely 'close'. Uses only committed, public
    artifacts -- no private archive needed.

    Tolerance: 1e-9 (== bit-for-bit up to double-precision arithmetic order).
    This is appropriate, not brittle, for stochastic Monte Carlo output
    regenerated by a DIFFERENT but ALGORITHMICALLY IDENTICAL code path with a
    FIXED seed: a fixed-seed numpy.random.default_rng draws the exact same
    float64 stream every time on the same numpy version, and the arithmetic
    performed on that stream here is line-for-line identical to
    deep_analyses.py's own loop, so anything above float noise indicates a
    real algorithmic or base-input drift, not sampling variance.
    """
    old_deep = _committed("deep_results.json")
    old_mc = old_deep["monte_carlo"]
    dispatch = _committed("battery_dispatch_policies.json")
    base_save = float(dispatch["post_behavior"]["mid"]["battery_marginal"])
    legacy = up.legacy_reproduction(base_save, seed=42, N=5000)
    for k in old_mc:
        assert abs(legacy[k] - old_mc[k]) < EPS, (
            f"{k}: new={legacy[k]} old={old_mc[k]} -- legacy_reproduction() "
            "has drifted from deep_analyses.py's own monte_carlo block")
    return ("legacy_reproduction(), fed the committed post_behavior.mid."
            "battery_marginal, reproduces every field of data/deep_results."
            "json's monte_carlo block exactly")


@case
def case_full_monte_carlo_rejects_a_non_positive_saving_draw():
    """A defensive fail-closed check: if a caller's calibration inputs are so
    extreme that a draw's year-1 saving would be non-positive, the model must
    refuse to publish a payback computed against it rather than silently
    emitting an infinite or nonsensical payback."""
    try:
        up.full_monte_carlo(pre=2329.0, mid=2238.0, rte_slope=-1000.0,
                            soil_slope_loss=-1000.0, soil_slope_surplus=-1000.0,
                            lossA=0.013, lossB=0.066, prod_sigma=0.02, N=50, seed=1)
    except SystemExit as e:
        assert "non-positive" in str(e)
        return "an absurd calibration slope that drives savings negative is caught, not silently published"
    raise AssertionError("a non-positive year-1 saving draw was not caught")


@case
def case_production_spread_and_soiling_get_consistent_generation_sensitivity():
    """Adversarial review pass 2, finding 2: production-measurement uncertainty
    and soiling are both uncertainty about the same physical quantity (true
    generation level), so an x-fraction generation shortfall from either
    source must move save1_of() by the identical amount -- a prod_noise of
    (1-x) must equal a soiling loss of x, and a prod_noise of (1+x) must equal
    a loss of -x. This is a regression test against re-introducing the
    original bug (prod_noise multiplied 1:1 into the dollar saving,
    overstating this lever's impact relative to soiling's calibrated
    sensitivity by roughly 1/soil_slope).

    Issue #89 update: the model is now genuinely PIECEWISE (soil_slope_loss
    != soil_slope_surplus by physical design), so this test's original
    all-one-slope symmetry assumption no longer holds AS STATED -- the
    intent it actually needs to keep verifying is that soiling and
    prod_noise share the SAME routing mechanism (an x-fraction shortfall
    from either source hits soil_slope_loss; an x-fraction surplus from
    either source hits soil_slope_surplus), not that both directions use
    the identical NUMBER. Uses deliberately DIFFERENT soil_slope_loss/
    soil_slope_surplus values below so a bug that silently collapsed the
    two back into one slope, or that routed by the wrong sign, would be
    caught, not hidden by both slopes happening to match."""
    pre, mid, rte_slope = 2329.0, 2238.0, 0.55
    soil_slope_loss, soil_slope_surplus = 0.057, 0.091   # deliberately different
    for x in (0.0, 0.02, 0.0568, 0.10):
        via_soiling = up.save1_of(c=1.0, rte=0.90, loss=x, prod_noise=1.0,
                                  pre=pre, mid=mid, rte_slope=rte_slope,
                                  soil_slope_loss=soil_slope_loss,
                                  soil_slope_surplus=soil_slope_surplus)
        via_prod_shortfall = up.save1_of(c=1.0, rte=0.90, loss=0.0, prod_noise=1 - x,
                                         pre=pre, mid=mid, rte_slope=rte_slope,
                                         soil_slope_loss=soil_slope_loss,
                                         soil_slope_surplus=soil_slope_surplus)
        via_prod_surplus = up.save1_of(c=1.0, rte=0.90, loss=0.0, prod_noise=1 + x,
                                       pre=pre, mid=mid, rte_slope=rte_slope,
                                       soil_slope_loss=soil_slope_loss,
                                       soil_slope_surplus=soil_slope_surplus)
        assert abs(via_soiling - via_prod_shortfall) < 1e-9, (
            f"x={x}: soiling loss and an equal-fraction prod_noise shortfall "
            f"must produce the identical save1 ({via_soiling} vs {via_prod_shortfall}) "
            "-- both are loss-like (>= 0) inputs and must route to soil_slope_loss")
        via_soiling_negative = up.save1_of(c=1.0, rte=0.90, loss=-x, prod_noise=1.0,
                                           pre=pre, mid=mid, rte_slope=rte_slope,
                                           soil_slope_loss=soil_slope_loss,
                                           soil_slope_surplus=soil_slope_surplus)
        assert abs(via_soiling_negative - via_prod_surplus) < 1e-9, (
            f"x={x}: a negative loss (generation surplus) and an equal-fraction "
            f"prod_noise surplus must produce the identical save1 "
            f"({via_soiling_negative} vs {via_prod_surplus}) -- both are "
            "surplus-like (< 0) inputs and must route to soil_slope_surplus")
        if x > 0:
            # issue #89's whole point: with genuinely different loss/surplus
            # slopes, the loss-side and surplus-side results must actually
            # DIFFER -- a regression test against silently routing both
            # directions through the same slope again.
            assert via_soiling != via_soiling_negative, (
                f"x={x}: loss-side and surplus-side save1 must differ when "
                "soil_slope_loss != soil_slope_surplus, got identical "
                f"{via_soiling} for both -- the piecewise routing isn't "
                "actually piecewise")
    return ("soiling and production-measurement uncertainty share one "
           "calibrated routing mechanism, but now correctly apply GENUINELY "
           "different loss-side/surplus-side slopes (issue #89)")


@case
def case_save1_of_piecewise_routing_is_correct_on_vectorized_mixed_sign_arrays():
    """Round 3 adversarial review, issue #89: every existing correctness
    check above calls save1_of() with SCALAR loss/prod_noise. The actual
    production hot path (full_monte_carlo(), 5000 draws) calls it with
    numpy ARRAYS carrying a mix of loss-like and surplus-like values in the
    SAME vectorized call -- np.where()'s array-broadcasting behavior is not
    exercised by any scalar-only test. Builds a real mixed-sign array (loss
    values and prod_noise-derived x both spanning positive and negative)
    and checks the vectorized result against an elementwise scalar
    recomputation, not just that it runs without crashing."""
    pre, mid, rte_slope = 2329.0, 2238.0, 0.55
    soil_slope_loss, soil_slope_surplus = 0.057, 0.091   # deliberately different
    rng = np.random.default_rng(0)
    N = 5000
    loss = rng.uniform(-0.10, 0.10, N)          # mixed sign, like a real draw
    prod_noise = rng.normal(1.0, 0.05, N)        # mixed sign via (1 - prod_noise)
    c = np.full(N, 1.0)
    rte = np.full(N, 0.90)

    vectorized = up.save1_of(c, rte, loss, prod_noise, pre, mid, rte_slope,
                             soil_slope_loss, soil_slope_surplus)
    assert loss.min() < 0 < loss.max(), "fixture must span both signs of loss"
    assert (1 - prod_noise).min() < 0 < (1 - prod_noise).max(), (
        "fixture must span both signs of (1 - prod_noise)")

    scalar_recompute = np.array([
        up.save1_of(1.0, 0.90, float(loss[i]), float(prod_noise[i]), pre, mid,
                   rte_slope, soil_slope_loss, soil_slope_surplus)
        for i in range(N)
    ])
    assert np.allclose(vectorized, scalar_recompute, atol=1e-9), (
        f"vectorized save1_of() disagrees with an elementwise scalar "
        f"recomputation -- max abs diff "
        f"{np.max(np.abs(vectorized - scalar_recompute))}")

    # And confirm the piecewise routing is REALLY happening inside the
    # vectorized call, not just that it happens to agree with itself: at
    # least some draws must differ from what the LOSS-side-only slope
    # would have produced (the pre-#89 bug's exact shape).
    all_loss_slope = up.save1_of(c, rte, loss, prod_noise, pre, mid, rte_slope,
                                 soil_slope_loss, soil_slope_loss)
    assert not np.allclose(vectorized, all_loss_slope), (
        "the vectorized result is indistinguishable from applying the "
        "loss-side slope to everything -- piecewise routing isn't actually "
        "differentiating surplus-like draws within the array")
    return ("save1_of()'s vectorized np.where piecewise routing matches an "
           "elementwise scalar recomputation on a 5000-element mixed-sign "
           "array, and is verifiably distinct from a single-slope fallback")


@case
def case_scale_production_reproduces_measured_flows_exactly_at_gen_scale_1():
    """issue #60: the identity scale_production() depends on for byte-
    identity at nominal (gen_scale=1.0) -- loss=0, so new_export=gen0 and
    import_delta=0 for EVERY interval, checked on a synthetic array that
    DELIBERATELY includes an interval with SIMULTANEOUS nonzero import AND
    export (index 3 below), a real ~6% case in this household's own
    measured data (2206 of 35040 intervals), not just a theoretical edge
    case this test shouldn't skip covering -- the new formula does not
    even need to treat that case specially (see scale_production()'s own
    docstring), which this proves rather than assumes."""
    gen0 = np.array([0.0, 0.0, 3.0, 0.5])   # index 3: simultaneous-flow interval
    P = np.array([1.0, 1.0, 3.0, 2.0])      # arbitrary gross production
    import_delta, new_export = up.scale_production(P, gen0, 1.0)
    assert np.allclose(import_delta, 0.0), import_delta
    assert np.allclose(new_export, gen0), (new_export, gen0)
    return "scale_production reproduces measured export exactly at gen_scale=1.0, including a simultaneous-flow interval"


@case
def case_scale_production_reallocates_a_loss_against_export_before_import():
    """issue #60 AC2: a production LOSS must first eat into EXPORT, and
    only start increasing IMPORT once export hits zero -- not scale export
    proportionally while import stays frozen (the original bug), and NOT
    decrease import under a loss either (Codex review, issue #60: an
    earlier fix attempt let import shrink on a net-exporting overlap
    interval whenever export alone had margin to absorb the loss --
    physically backwards, since a production loss can only leave import
    the same or make it worse). Single synthetic interval: production P=5,
    nominal export=3. At gen_scale=0.8, loss=1 -> export drops to 2,
    import untouched (small loss, absorbed entirely by export). At
    gen_scale=0.2, loss=4 -> export must hit exactly 0 and the 1 kWh
    excess must spill into import."""
    P = np.array([5.0])
    gen0 = np.array([3.0])
    _, gen_at_1 = up.scale_production(P, gen0, 1.0)
    assert gen_at_1[0] == 3.0
    delta_08, gen_08 = up.scale_production(P, gen0, 0.8)
    assert gen_08[0] == 2.0 and delta_08[0] == 0.0, (
        "a small loss must be absorbed entirely by export, not spill into import yet")
    delta_02, gen_02 = up.scale_production(P, gen0, 0.2)
    assert gen_02[0] == 0.0 and delta_02[0] == 1.0, (
        "once the loss exceeds export, export must hit exactly 0 and the "
        "excess must spill into import")
    return "scale_production reallocates a loss against export first, spilling into import only once export is exhausted"


@case
def case_scale_production_never_decreases_import_under_a_loss():
    """issue #60, Codex review (final pass): a production LOSS must never
    DECREASE import -- less production available can only require the
    same or MORE grid draw to meet a fixed load, never less. An earlier
    fix attempt (scaling a "simultaneous flow" component down with
    gen_scale) violated this on a net-EXPORTING interval with enough
    export margin to fully absorb the loss: Codex's own concrete example
    -- P=5, imp0=1, gen0=4, gen_scale=0.8 -- should reduce export from 4
    to 3 while leaving import at 1 exactly (the loss, 1 kWh, is smaller
    than gen0, 4 kWh, so it's fully absorbed by export alone); the earlier
    formula instead produced export=2.8, import=0.8 -- import going DOWN
    because of a production LOSS, which is not physically possible."""
    P = np.array([5.0])
    gen0 = np.array([4.0])
    imp0 = np.array([1.0])
    import_delta, new_export = up.scale_production(P, gen0, 0.8)
    assert new_export[0] == 3.0, new_export
    assert import_delta[0] == 0.0, (
        f"import must stay exactly at its baseline when export alone "
        f"absorbs the whole loss, got a delta of {import_delta[0]} "
        f"(new import would be {imp0[0] + import_delta[0]}, not {imp0[0]})")
    return "scale_production never decreases import under a production loss, matching Codex's own worked example"


@case
def case_scale_production_reallocates_a_surplus_against_import_before_export():
    """issue #89: gen_scale > 1.0 (a production SURPLUS) is now modeled,
    mirroring case_scale_production_reallocates_a_loss_against_export_
    before_import above -- a surplus first reduces the scenario's own
    IMPORT (self-consumption absorbs it), and only once import is fully
    exhausted does the remainder spill into MORE export -- the mirror
    image of the loss-side rule. Single synthetic interval: production
    P=5, nominal export=3, scenario import=2. At gen_scale=1.2, surplus=1
    -> import drops to 1 (absorbed_by_import=1), export untouched (small
    surplus, fully absorbed by import). At gen_scale=1.6, surplus=3 ->
    import must hit exactly 0 and the 1 kWh excess must spill into export
    (3 -> 4)."""
    P = np.array([5.0])
    gen0 = np.array([3.0])
    imp_base = np.array([2.0])
    delta_1, gen_1 = up.scale_production(P, gen0, 1.0, imp_base=imp_base)
    assert delta_1[0] == 0.0 and gen_1[0] == 3.0
    delta_small, gen_small = up.scale_production(P, gen0, 1.2, imp_base=imp_base)
    assert abs(delta_small[0] - -1.0) < 1e-9 and abs(gen_small[0] - 3.0) < 1e-9, (
        "a small surplus must be absorbed entirely by import, not spill "
        f"into export yet (got delta={delta_small[0]}, export={gen_small[0]})")
    delta_large, gen_large = up.scale_production(P, gen0, 1.6, imp_base=imp_base)
    assert abs(delta_large[0] - -2.0) < 1e-9 and abs(gen_large[0] - 4.0) < 1e-9, (
        "once the surplus exceeds available import, import must hit "
        f"exactly 0 (delta=-imp_base) and the excess must spill into "
        f"export (got delta={delta_large[0]}, export={gen_large[0]})")
    return "scale_production reallocates a surplus against import first, spilling into export only once import is exhausted"


@case
def case_scale_production_never_increases_import_under_a_surplus():
    """issue #89, mirroring case_scale_production_never_decreases_import_
    under_a_loss: a production SURPLUS must never INCREASE import -- more
    production available can only require the same or LESS grid draw,
    never more -- and must never drive import negative. Checked across
    random synthetic scenarios (including intervals where the scenario's
    own import is already 0, a net-exporting interval, where the entire
    surplus must spill straight to export with zero import effect), and
    that surplus == (import reduction) + (export increase) exactly, the
    surplus-side mirror of the loss-side energy-conservation identity."""
    rng = np.random.default_rng(0)
    P = rng.uniform(0, 10, 200)
    gen0 = rng.uniform(0, 8, 200)
    imp_base = rng.uniform(0, 5, 200)
    for gen_scale in (1.01, 1.1, 1.5, 2.0):
        import_delta, new_export = up.scale_production(P, gen0, gen_scale, imp_base=imp_base)
        assert np.all(import_delta <= 1e-9), (
            f"gen_scale={gen_scale}: a surplus must never increase import, "
            f"got a positive import_delta up to {import_delta.max()}")
        assert np.all(imp_base + import_delta >= -1e-9), (
            f"gen_scale={gen_scale}: import must never go negative after "
            "the surplus offset")
        surplus = P * (gen_scale - 1.0)
        conserved = -import_delta + (new_export - gen0)
        assert np.allclose(conserved, surplus, atol=1e-9), (
            f"gen_scale={gen_scale}: surplus must equal (import reduction) "
            "+ (export increase) exactly -- energy not conserved")
    return "a production surplus never increases import, never drives it negative, and conserves energy exactly"


@case
def case_scale_production_fails_closed_on_a_surplus_without_imp_base():
    """issue #89: gen_scale > 1.0 needs imp_base (a surplus reduces import
    first, mirroring the loss side's need for gen0), so a caller that
    forgets to pass it must fail closed, not silently mishandle the
    surplus as if it were export-only (the pre-#89 behavior this
    function's SystemExit guard used to apply unconditionally)."""
    P = np.array([5.0])
    gen0 = np.array([3.0])
    try:
        up.scale_production(P, gen0, 1.2)
        raise AssertionError("expected SystemExit for gen_scale > 1.0 without imp_base")
    except SystemExit as e:
        assert "imp_base" in str(e), str(e)
    return "scale_production fails closed on a production surplus with no imp_base supplied"


@case
def case_load_sam_hourly_fails_closed_on_a_wrong_row_count():
    """issue #60: _load_sam_hourly() must refuse a SAM export with the
    wrong number of hourly rows rather than silently misaligning every
    later timestamp against the wrong hour."""
    tmp_dir = tempfile.TemporaryDirectory()
    cwd = os.getcwd()
    os.chdir(tmp_dir.name)
    try:
        with open("samB.csv", "w") as f:
            f.write("kWh\n" + "\n".join(["1.0"] * 100))   # 2025 needs 8760
        with open("samA.csv", "w") as f:
            f.write("kWh\n" + "\n".join(["1.0"] * 8760))
        try:
            up._load_sam_hourly()
            raise AssertionError("expected SystemExit on a wrong SAM row count")
        except SystemExit as e:
            assert "8760" in str(e), str(e)
    finally:
        os.chdir(cwd)
        tmp_dir.cleanup()
    return "_load_sam_hourly fails closed on a wrong row count"


@case
def case_load_sam_hourly_fails_closed_on_a_missing_file():
    """issue #60: _load_sam_hourly() must name the missing file, not raise
    an opaque FileNotFoundError from deep inside open()."""
    tmp_dir = tempfile.TemporaryDirectory()
    cwd = os.getcwd()
    os.chdir(tmp_dir.name)
    try:
        with open("samB.csv", "w") as f:
            f.write("kWh\n" + "\n".join(["1.0"] * 8760))
        # samA.csv deliberately absent
        try:
            up._load_sam_hourly()
            raise AssertionError("expected SystemExit on a missing SAM file")
        except SystemExit as e:
            assert "samA.csv" in str(e), str(e)
    finally:
        os.chdir(cwd)
        tmp_dir.cleanup()
    return "_load_sam_hourly fails closed on a missing SAM file, naming it"


# ---------------------------------------------------------------------------
# (b) archive-gated: exercise the REAL dispatch engine and REAL household
# ---------------------------------------------------------------------------
@case
def case_reconstructed_production_matches_the_validated_meter_derived_total():
    """issue #60, Codex adversarial review first pass: an earlier draft
    assigned the SAM 8760 files' raw hourly value directly to P (gross
    production) -- but those files are whole-home GROSS-LOAD kWh (see
    _load_sam_hourly()'s own docstring, identical to threeway_production_
    validation.py's load_sam_hourly()), not production. That bug claimed
    ~29,866 kWh/yr of PV production against this repo's own VALIDATED
    meter_derived total (data/threeway_production_validation.csv, itself
    gated by a real correlation/ratio check against two independent
    references before publication) of 16,459.2 kWh/yr -- an ~82%
    overstatement the algebraic energy-conservation check alone could never
    catch, since that check only verifies P and D are mutually consistent,
    never that P means the right physical thing. This is the regression
    test for exactly that: reconstruct_gross_production()'s own annual
    total must land close to the independently-validated figure, not just
    be internally self-consistent."""
    _require_archive()
    import pandas as pd

    def _reconstruct():
        d = br.load()
        imp0 = d.Consumption.values.astype(float)
        gen0 = d.Generation.values.astype(float)
        P, D = up.reconstruct_gross_production(d, imp0, gen0)
        return float(P.sum())

    total_kwh = _in_sandbox(_reconstruct)
    validated_total = float(pd.read_csv(DATA / "threeway_production_validation.csv",
                                        index_col=0)["meter_derived"].sum())
    rel_diff = abs(total_kwh - validated_total) / validated_total
    # Generous tolerance: this script's own 365-day window differs from
    # threeway_production_validation.py's stricter 363-day (DST-excluded)
    # window by construction, so a few percent of genuine difference is
    # expected, not a bug -- but an order-of-magnitude miss (the actual
    # bug this test exists to catch) must fail loudly.
    assert rel_diff < 0.10, (
        f"reconstructed gross production ({total_kwh:.1f} kWh/yr) diverges "
        f"{rel_diff:.1%} from the validated meter_derived total "
        f"({validated_total:.1f} kWh/yr) -- if this is anywhere near the "
        "old ~82% miss, P is being computed from the wrong quantity again "
        "(SAM load treated as production, not derived via sam - import + "
        "export)")
    return (f"reconstructed production {total_kwh:.1f} kWh/yr is within "
           f"{rel_diff:.1%} of the validated meter_derived total "
           f"{validated_total:.1f} kWh/yr")


@case
def case_reconstructed_load_is_nonnegative_across_the_real_measured_year():
    """issue #60, Codex adversarial review second pass: a flat pv_hour/N
    disaggregation (an earlier draft) produced a physically-impossible
    NEGATIVE implied household load (D < 0) on 407 of this household's
    real 35,040 measured intervals. The fixed allocation (each net-
    exporting interval floored at its own net export first) is proven
    algebraically to guarantee D >= 0 whenever a hour's SAM-derived
    production covers that hour's own net-export peaks -- checked here
    against the REAL data, not just trusted from the proof, since the
    proof's own precondition (no "deficit hours") is an empirical fact
    about this household's data, not a mathematical certainty for all
    possible inputs."""
    _require_archive()

    def _reconstruct():
        d = br.load()
        imp0 = d.Consumption.values.astype(float)
        gen0 = d.Generation.values.astype(float)
        _, D = up.reconstruct_gross_production(d, imp0, gen0)
        return D
    D = _in_sandbox(_reconstruct)
    n_negative = int((D < 0).sum())
    assert n_negative == 0, (
        f"{n_negative} interval(s) have a negative implied household load "
        f"(min D = {D.min():.4f}) -- the nonnegative-load allocation "
        "guarantee has regressed")
    return f"reconstructed load is non-negative across all {len(D)} real measured intervals (min D = {D.min():.4f} kWh)"


@case
def case_dst_dates_are_excluded_from_the_sam_join_not_misaligned_against_it():
    """issue #60, Codex adversarial review third pass: the SAM 8760 export
    is a FLAT 24-hours-a-day grid (never adjusted for DST -- this repo's
    own documented fact, service_headroom.py's "DST" section and
    threeway_production_validation.py's own dst_dates_in() exclusion),
    while Green Button `d` is true wall clock (23 real hours on the
    spring-forward day, 25 on fall-back). An earlier draft joined SAM to
    Green Button by bare (date, hour) on BOTH transition dates too,
    silently pairing SAM's flat-clock hour against the wrong real
    wall-clock hour for ~48 of 35,040 intervals a year -- unnoticed by any
    energy-conservation or nonnegative-load check, since those only verify
    internal consistency, not which physical hour was actually joined.
    Fixed the same way this repo's own precedent handles it: DST dates are
    excluded from the SAM join entirely, taking a conservative fallback
    (P = max(net, 0), no self-consumption modeled) instead. Verified
    directly here: every interval on this household's two real DST
    transition dates must have P == max(net, 0) exactly (the fallback's
    own signature), not a value derived from a misaligned SAM lookup."""
    _require_archive()
    import rates as R

    def _reconstruct():
        d = br.load()
        imp0 = d.Consumption.values.astype(float)
        gen0 = d.Generation.values.astype(float)
        P, D = up.reconstruct_gross_production(d, imp0, gen0)
        dates = d.dt.dt.date.values
        net = gen0 - imp0
        dst_dates = set()
        for y in sorted({dt_.year for dt_ in d.dt.dt.date}):
            dst_dates |= set(R.dst_transition_sundays(y))
        mask = np.array([dd in dst_dates for dd in dates])
        return D[mask], P[mask], net[mask], int(mask.sum())
    D_dst, P_dst, net_dst, n_dst = _in_sandbox(_reconstruct)
    assert n_dst > 0, "fixture must actually contain a DST transition date"
    # The fallback's own defining signature: P = max(net, 0) exactly (never
    # a SAM-hourly-derived value). D = P - net follows from that -- D is
    # NOT always 0 (only when net >= 0; a net-importing interval correctly
    # gets D = -net = imp0-gen0, its own measured net import treated as
    # pure load since P=0 is assumed there) -- so P is the right thing to
    # check directly, not D.
    assert np.allclose(P_dst, np.maximum(net_dst, 0.0)), (
        "DST-date P must equal max(net, 0), the documented fallback, not "
        "a SAM-derived value")
    assert np.all(D_dst >= -1e-9), (
        f"DST-date D must still be non-negative under the fallback, got "
        f"min {D_dst.min()}")
    return f"{n_dst} DST-transition-date intervals correctly use the max(net,0) fallback, not a misaligned SAM join"


@case
def case_dispatch_calibration_matches_committed_battery_dispatch_policies():
    _require_archive()
    calib = _in_sandbox(up.dispatch_calibration)
    dispatch = _committed("battery_dispatch_policies.json")
    committed_pre = float(dispatch["pw3"]["greedy"]["save"])
    committed_mid = float(dispatch["post_behavior"]["mid"]["battery_marginal"])
    # The tie-out against the committed artifact compares the SINGLE-PASS
    # recomputation (matching battery_dispatch_policies.py's own method
    # exactly), not the steady-state pre_nominal/mid_nominal used for
    # calibration -- Codex review pass 1, finding 2 fixed the calibration to
    # use a converged SOC boundary, which legitimately differs from the
    # committed artifact's single-pass figure by ~$1-2 (see dispatch_
    # calibration()'s _single_pass_marginal docstring).
    assert abs(calib["pre_nominal_single_pass"] - committed_pre) < 1.0, (
        f"recomputed pre-behavior marginal {calib['pre_nominal_single_pass']:.2f} "
        f"disagrees with committed pw3.greedy.save {committed_pre} by >$1")
    assert abs(calib["mid_nominal_single_pass"] - committed_mid) < 1.0, (
        f"recomputed post-behavior marginal {calib['mid_nominal_single_pass']:.2f} "
        f"disagrees with committed post_behavior.mid.battery_marginal {committed_mid} by >$1")
    # The steady-state figures (used for calibration everywhere else) must
    # stay close to the single-pass ones -- a boundary-condition fix should
    # be a small correction, not a wholesale change to the marginal.
    assert abs(calib["pre_nominal"] - calib["pre_nominal_single_pass"]) < 5.0, (
        "steady-state pre_nominal diverges implausibly far from the single-pass "
        "figure -- investigate before trusting the convergence")
    assert abs(calib["mid_nominal"] - calib["mid_nominal_single_pass"]) < 5.0, (
        "steady-state mid_nominal diverges implausibly far from the single-pass "
        "figure -- investigate before trusting the convergence")
    # Higher round-trip efficiency must raise the marginal saving -- unambiguous:
    # less energy is lost per cycle, full stop.
    assert calib["rte_slope_mid"] > 0, "higher RTE must raise the battery marginal saving"
    # Soiling's sign is NOT asserted a priori. An earlier draft assumed "more
    # loss must lower the marginal saving" and hardcoded that as a test -- but
    # that draft's marginal() had the finding-1 bug (mismatched generation
    # baseline), and its "confirming" negative slope was an artifact of the
    # SAME bug, not independent verification. Less midday solar surplus means
    # some previously net-exporting intervals become small net importers that
    # the battery can now discharge into; whether that nets out above or below
    # the lost-solar-charging effect is genuinely ambiguous without running the
    # real engine -- exactly why this script calibrates from real reruns
    # instead of assuming a sign.
    #
    # issue #60: soil_slope's own MAGNITUDE grew roughly 4.4x once soiling
    # correctly hits GROSS production (reallocated against export first,
    # spilling into import) instead of scaling the smaller Generation/export
    # column alone -- expected and correct (some of a production loss at
    # this household turns out to have been self-consumed, not exported;
    # see production_reconstruction's own energy-conservation numbers), not
    # a regression of the "small effect" reasoning below. What that reasoning
    # was actually always about is the REALIZED swing at the household's own
    # real loss magnitude (soil_slope_loss * lossB), which the fix leaves
    # genuinely small (still "a few percent") even though the raw per-unit
    # slope no longer is -- so this checks the realized swing, not the raw
    # slope. Issue #89: checked on the LOSS-side slope specifically (the
    # direct descendant of the pre-#89 single soil_slope, still fit against
    # the same +lossB point) -- the new surplus-side slope is checked
    # separately in case_dispatch_calibration_fits_a_real_third_surplus_
    # point_distinct_from_loss below.
    realized_swing = abs(calib["soil_slope_loss_mid"] * calib["lossB"])
    assert realized_swing < 0.15, (
        f"soiling's REALIZED swing at this household's own lossB "
        f"({calib['lossB']:.4f}) is {realized_swing:.4f} ({realized_swing:.1%}) "
        "-- implausibly large for a small realistic loss fraction; "
        "investigate before trusting the calibration")
    same_sign = (calib["soil_slope_loss_mid"] > 0) == (calib["soil_slope_loss_pre"] > 0)
    assert same_sign, (
        f"pre- ({calib['soil_slope_loss_pre']}) and post-behavior "
        f"({calib['soil_slope_loss_mid']}) soiling loss-side slopes disagree "
        "in sign -- not internally consistent enough to average into one slope")
    surplus_same_sign = (calib["soil_slope_surplus_mid"] > 0) == (calib["soil_slope_surplus_pre"] > 0)
    assert surplus_same_sign, (
        f"pre- ({calib['soil_slope_surplus_pre']}) and post-behavior "
        f"({calib['soil_slope_surplus_mid']}) soiling surplus-side slopes "
        "disagree in sign -- not internally consistent enough to average "
        "into one slope")
    return (f"real dispatch calibration's single-pass figures match the committed "
            f"artifact within $1 (steady-state pre={calib['pre_nominal']:.2f}, "
            f"mid={calib['mid_nominal']:.2f}) "
            "with a correctly-signed RTE sensitivity and an internally-"
            f"consistent, small soiling sensitivity (loss={calib['soil_slope_loss_mid']:+.4f}, "
            f"surplus={calib['soil_slope_surplus_mid']:+.4f})")


@case
def case_dispatch_calibration_fits_a_real_third_surplus_point_distinct_from_loss():
    """Issue #89 AC1/AC3: a real THIRD dispatch rerun at the mirrored surplus
    scenario (gen_scale=1+lossB) must now exist alongside the nominal and
    loss points -- soil_points_mid/soil_points_pre must have 3 entries,
    including a -lossB key (the sign convention save1_of()'s own
    (1 - prod_noise) already uses for a surplus-like input) -- and the
    fitted soil_slope_surplus_mid/pre must be a genuinely DIFFERENT real
    number from soil_slope_loss_mid/pre, not a placeholder or an accidental
    copy. The issue's OWN filing used an illustrative ~6% steeper ballpark
    (a rough pre-fix estimate, not a real dispatch rerun of the self-
    consumption-first surplus model this fix actually implements); this
    household's real regenerated numbers, from the real self-consumption-
    first dispatch rerun, land around 1.5-1.7x (55-70% steeper) instead --
    checked here against THIS run's own regenerated numbers, not the
    issue's frozen illustrative figures, per this issue's own explicit
    instruction not to assert the filing's ballpark verbatim."""
    _require_archive()
    calib = _in_sandbox(up.dispatch_calibration)
    lossB = calib["lossB"]
    for name, points in (("mid", calib["soil_points_mid"]), ("pre", calib["soil_points_pre"])):
        assert len(points) == 3, (
            f"soil_points_{name} must have exactly 3 real calibration points "
            f"(surplus/nominal/loss), got {len(points)}: {points}")
        assert -lossB in points, (
            f"soil_points_{name} is missing the surplus calibration point "
            f"at -lossB ({-lossB}): {points}")
        assert 0.0 in points and lossB in points, (
            f"soil_points_{name} is missing the nominal (0.0) or loss "
            f"(+lossB) point: {points}")
    for side in ("mid", "pre"):
        loss_slope = calib[f"soil_slope_loss_{side}"]
        surplus_slope = calib[f"soil_slope_surplus_{side}"]
        assert loss_slope != surplus_slope, (
            f"soil_slope_surplus_{side} must be a genuinely distinct real "
            f"number from soil_slope_loss_{side} (issue #89's whole point), "
            f"got identical {loss_slope}")
        ratio = abs(surplus_slope) / abs(loss_slope)
        assert 1.2 < ratio < 2.0, (
            f"soil_slope_surplus_{side}/soil_slope_loss_{side} magnitude "
            f"ratio {ratio:.4f} is outside a plausible band around this "
            "household's own regenerated ~1.5-1.7x finding -- investigate "
            "before trusting the calibration")
    fix = calib["production_reconstruction"]["surplus_slope_fix"]
    assert fix["real_surplus_marginal"] != fix["old_extrapolated_estimate"], (
        "the real surplus marginal must differ from the old one-sided "
        "extrapolated estimate, or this fix changed nothing")
    return (f"real 3rd surplus calibration point exists and fits a "
            f"genuinely distinct slope: loss_mid={calib['soil_slope_loss_mid']:+.4f} "
            f"vs surplus_mid={calib['soil_slope_surplus_mid']:+.4f} "
            f"(ratio {abs(calib['soil_slope_surplus_mid'])/abs(calib['soil_slope_loss_mid']):.3f})")


@case
def case_production_reconstruction_conserves_energy_and_shows_the_understated_direction():
    """issue #60 AC2/AC3, verified from the public committed artifact (no
    archive needed to check semantics already computed and committed):
    every lost kWh must show up as EITHER less export OR more import (the
    energy-conservation check itself, not just trusted from the generator's
    own arithmetic), and the corrected soil_slope must be LARGER in
    magnitude than the old export-only-scaling figure -- confirming the
    issue's own 'likely understates' hypothesis with committed numbers, not
    just asserting the direction was checked."""
    if not DATA.joinpath("uncertainty_results.json").is_file():
        raise SkipCase("needs the committed uncertainty_results.json")
    result = _committed("uncertainty_results.json")
    pr = result["calibration"]["production_reconstruction"]
    cc = pr["energy_conservation_check"]
    assert abs(cc["gap_kwh"]) < 0.01, (
        f"production_lost_kwh must equal export_reduction_kwh + "
        f"import_increase_kwh to within rounding, got gap {cc['gap_kwh']} kWh")
    assert abs(cc["export_reduction_kwh"] + cc["import_increase_kwh"] - cc["production_lost_kwh"]) < 0.02
    old = pr["old_vs_new_soil_slope"]["old_export_only_scaling"]
    new = pr["old_vs_new_soil_slope"]["new_gross_production_reallocation"]
    assert abs(new["soil_slope_mid"]) > abs(old["soil_slope_mid"]), (
        "the corrected soil_slope_mid must be LARGER in magnitude than "
        f"the old export-only figure ({new['soil_slope_mid']} vs "
        f"{old['soil_slope_mid']}) -- confirms understatement, not just "
        "asserts it")
    assert abs(new["soil_slope_pre"]) > abs(old["soil_slope_pre"])
    # Regression guard: old_export_only_scaling MUST be a frozen historical
    # constant, not read live from the committed artifact -- an earlier
    # draft of this fix read _committed("uncertainty_results.json") for
    # "old", which is self-referential and silently drifts the artifact on
    # every subsequent regeneration (caught by running the regeneration
    # twice before committing, not by any single-run check like this one --
    # but this at least pins the frozen value so a future edit can't
    # silently swap it back for a live lookup without this test noticing
    # the value stops matching the known historical constant).
    assert old["soil_slope_mid"] == 0.05605402062021063, (
        "old_export_only_scaling.soil_slope_mid must stay pinned to the "
        f"frozen pre-fix historical constant, got {old['soil_slope_mid']} -- "
        "if this changed, check it wasn't switched back to a live "
        "_committed() lookup (non-reproducible, see issue #60 history)")
    return (f"energy conservation holds (gap {cc['gap_kwh']} kWh) and the fix "
           f"confirms understatement: soil_slope_mid {old['soil_slope_mid']} "
           f"-> {new['soil_slope_mid']}")


@case
def case_soil_slope_two_sided_fix_is_disclosed_and_matches_the_live_check():
    """issue #89: the old one-sided-extrapolation LIMITATION note is retired
    now that the fix is in -- this checks the artifact discloses the
    RESOLUTION (a real third dispatch rerun + two genuinely separate
    slopes), citing the SAME live-computed discrepancy dispatch_
    calibration()'s own production_reconstruction.surplus_slope_fix block
    reports, not a stale hardcoded number left over from the old note."""
    if not DATA.joinpath("uncertainty_results.json").is_file():
        raise SkipCase("needs the committed uncertainty_results.json")
    result = _committed("uncertainty_results.json")
    calib = result["calibration"]
    note = calib["soil_slope_two_sided_fix"]
    assert "issue #89" in note.lower(), note
    fix = calib["production_reconstruction"]["surplus_slope_fix"]
    assert f"${fix['discrepancy_usd']:,.2f}" in note, (
        "the resolution note must cite the actual regenerated discrepancy "
        f"dollar figure ({fix['discrepancy_usd']}), not a stale or "
        f"invented one: {note!r}")
    assert fix["real_surplus_marginal"] != fix["old_extrapolated_estimate"], (
        "the real surplus marginal must differ from the retired one-sided "
        "extrapolated estimate, or nothing was actually fixed")
    assert calib["soil_slope_loss_mid"] != calib["soil_slope_surplus_mid"], (
        "the artifact's own loss/surplus slopes must be genuinely distinct "
        "-- the whole point of this fix")
    return "the two-sided soil-slope fix is disclosed in the artifact, citing the live regenerated discrepancy figure"


@case
def case_build_end_to_end_is_deterministic_and_self_consistent():
    _require_archive()
    out1 = _in_sandbox(up.build)
    out2 = _in_sandbox(up.build)
    s1 = json.dumps(out1, sort_keys=True, default=str)
    s2 = json.dumps(out2, sort_keys=True, default=str)
    assert s1 == s2, "build() must be byte-identical across repeated runs on the same inputs"
    assert out1["legacy_reproduction"]["matches"] is True, (
        "build()'s own legacy-reproduction cross-check failed")
    inputs = out1["inputs"]
    expected_inputs = {"escalation", "degradation_fade", "install_cost",
                       "ev_behavior_persistence", "soiling_loss_fraction",
                       "round_trip_efficiency", "production_measurement_spread"}
    assert set(inputs) == expected_inputs, (
        f"artifact inputs {set(inputs)} != the seven AC1 inputs {expected_inputs}")
    for name, spec in inputs.items():
        assert spec.get("evidential_basis"), f"{name}: no evidential_basis recorded"
    corr = out1["correlation_assumption"]
    assert "independ" in corr.lower() and "understate" in corr.lower(), (
        "correlation_assumption must state the independence assumption AND its bias direction")
    mc = out1["battery_marginal_only_full_model"]
    assert 0.0 <= mc["prob_within_warranty_10yr"] <= mc["prob_within_15yr"] <= 1.0
    return "build() is deterministic end-to-end and its artifact satisfies AC1-AC6's structural requirements"


@case
def case_dispatch_adherence_and_escalation_sidedness_are_documented_not_modeled():
    """Issue #59: both scope questions from #15's review resolve to 'no new
    distribution, documented reasoning instead' -- checked here so the
    documentation can't silently vanish in a future edit. Codex adversarial
    review, issue #59, second pass: an earlier version of this case called
    _require_archive() before checking either note, so it SKIPPED (not ran)
    in any real public checkout/CI and never actually verified the committed
    artifact. Reads data/uncertainty_results.json directly instead -- no
    archive needed, matching case_esc_hi_matches_committed_tou_spread_ladder_
    ceiling's own public-data pattern above -- so this genuinely runs in CI.
    Guards the SPECIFIC claim each note makes, not just that a note exists:
    the dispatch note must say adherence is NOT modeled (not accidentally
    claim the opposite), and the escalation note must not silently drop the
    reasoning for why the floor stays at 0%, nor silently re-assert the
    retracted per-cell-alone claim a first adversarial-review pass caught."""
    result = _committed("uncertainty_results.json")
    dispatch_note = result["dispatch_policy_adherence_note"]
    assert "issue #59" in dispatch_note.lower()
    assert "not modeled" in dispatch_note.lower(), (
        "the dispatch-adherence note must say this is NOT modeled -- if a "
        "future edit actually implements a distribution, this note (and "
        "this assertion) need to be updated together, not silently")
    esc_note = result["escalation_two_sided_evidence_note"]
    assert "issue #59" in esc_note.lower()
    assert "0% floor" in esc_note and "kept" in esc_note.lower(), (
        "the escalation note must state the 0% floor decision explicitly")
    # Codex adversarial review, issue #59, first pass: an earlier draft
    # claimed per-TOU-cell absolute-level trends (on-peak rising) PROVED the
    # floor correct -- a category error, since `esc` scales the SPREAD-
    # driven saving uniformly, not any one period's own level, and the
    # repo's own dedicated spread-level tool (tou_spread.json) reports that
    # as "not determined". The note must tie the decision to the SPREAD
    # question specifically (evidence semantics, not just tone words) and
    # must NOT reassert the retracted claim that per-cell evidence alone
    # settles it.
    assert "spread" in esc_note.lower() and "not determined" in esc_note.lower(), (
        "the escalation note must tie the 0% floor decision to the SPREAD-"
        "level 'not determined' finding, not just individual TOU-cell "
        f"levels: {esc_note!r}")
    assert "inherited" in esc_note.lower(), (
        "the note must be honest that the floor is an INHERITED assumption, "
        f"not one this repo's evidence proves correct: {esc_note!r}")
    # The escalation input's own short evidential_basis must not contradict
    # the fuller note by implying the floor is proven/evidence-backed
    # outright (Codex adversarial review, issue #59, second pass).
    esc_basis = result["inputs"]["escalation"]["evidential_basis"].lower()
    assert "inherited" in esc_basis and "unproven" in esc_basis, (
        "the escalation input's own evidential_basis must not contradict "
        f"the fuller note by implying the floor is proven: {esc_basis!r}")
    # ESC_LO/ESC_HI themselves must not have silently drifted while this
    # documentation was added -- the whole point of issue #59 is that the
    # floor choice was RE-JUSTIFIED, not changed.
    assert up.ESC_LO == 0.00 and up.ESC_HI == 0.12, (
        "issue #59 kept the existing escalation band; ESC_LO/ESC_HI must "
        f"not have drifted, got ({up.ESC_LO}, {up.ESC_HI})")
    return "dispatch-adherence and escalation-sidedness are both documented as checked-and-not-modeled, per issue #59, verified from the public committed artifact"


@case
def case_escalation_downside_sensitivity_is_labeled_not_a_probability_and_monotonic():
    """Codex adversarial review, issue #59, third pass: documenting the 0%
    floor as inherited/unproven while the Monte Carlo can never sample a
    negative escalation draw hides the downside's actual consequence from a
    reader. escalation_downside_sensitivity is a plain what-if grid, not a
    new probability-weighted input -- checked here for two things: (1) it
    is explicitly labeled as carrying no evidence-backed weight (so a
    reader can't mistake it for a Monte Carlo percentile), and (2) payback
    years actually get WORSE as the grid moves more negative (a sign/
    monotonicity sanity check -- if a more negative escalation scenario
    ever produced a SHORTER payback, that would mean payback_of()'s own
    compounding is broken, not that negative escalation somehow helps)."""
    result = _committed("uncertainty_results.json")
    sens = result["escalation_downside_sensitivity"]
    disclaimer = sens["not_a_probability_distribution"].lower()
    assert "no evidence-backed weight" in disclaimer and "probability" in disclaimer, (
        "the downside grid must explicitly disclaim being a probability "
        f"distribution: {sens['not_a_probability_distribution']!r}")
    grid = sens["grid"]
    assert set(grid) == {f"{p:+.0%}" for p in up.ESC_DOWNSIDE_GRID_PCT}
    paybacks = [grid[f"{p:+.0%}"]["payback_yr"] for p in sorted(up.ESC_DOWNSIDE_GRID_PCT)]
    assert paybacks == sorted(paybacks, reverse=True), (
        "payback years must be monotonically WORSE (longer) as the grid "
        f"moves more negative -- got {paybacks} for pcts "
        f"{sorted(up.ESC_DOWNSIDE_GRID_PCT)}")
    # This household's own real base case (Codex's concrete ask: show what
    # the downside actually costs, not just that one exists): at 0% this
    # must match warranty-repaying, and the grid must contain at least one
    # scenario that does NOT, or the grid is too narrow to show a real
    # consequence at all.
    assert grid["+0%"]["within_10yr_warranty"] is True
    assert any(not row["within_10yr_warranty"] for row in grid.values()), (
        "the downside grid must span far enough to show at least one "
        "scenario that misses the 10-yr warranty repay, or it doesn't "
        "actually demonstrate a consequence")
    # A first draft of this grid used the raw post-behavior `mid` marginal
    # instead of tornado()'s own Beta(2,1)-blended nominal save1, so its own
    # +0% point silently disagreed with the figure the code claimed it
    # matched -- caught in an independent review pass, not by any automated
    # one. Guard the fix directly: this grid's +0% point (esc=0, same as
    # ESC_LO) must equal tornado()'s escalation lever's own ESC_LO payback
    # endpoint (its range is sorted ascending, and ESC_LO gives the WORSE/
    # longer payback of the two, so it's the range's upper bound) exactly,
    # not just approximately.
    esc_lever_hi = result["tornado"]["levers"]["escalation"]["payback_range_yr"][1]
    assert grid["+0%"]["payback_yr"] == esc_lever_hi, (
        "escalation_downside_sensitivity's +0% point must equal tornado()'s "
        f"own escalation-lever ESC_LO payback exactly: {grid['+0%']['payback_yr']} "
        f"!= {esc_lever_hi}")
    return (f"escalation downside grid is labeled non-probabilistic and "
           f"monotonic across {sorted(grid)}")


@case
def case_build_output_is_json_serializable():
    _require_archive()
    out = _in_sandbox(up.build)
    # round-trips through json.dumps/loads with no numpy scalar leakage
    reparsed = json.loads(json.dumps(out, default=str))
    assert isinstance(reparsed, dict) and "battery_marginal_only_full_model" in reparsed
    return "build()'s output round-trips cleanly through JSON"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran = 0
    skipped = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
