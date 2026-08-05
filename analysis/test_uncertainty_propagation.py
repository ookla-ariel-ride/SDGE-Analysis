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
    kwargs = dict(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope=-1.0,
                  lossA=0.013, lossB=0.066, prod_sigma=0.02, N=500, seed=99)
    r1 = up.full_monte_carlo(**kwargs)
    r2 = up.full_monte_carlo(**kwargs)
    assert r1 == r2, "two full_monte_carlo() calls with the same seed must match exactly"
    return "full_monte_carlo is deterministic given a fixed seed"


@case
def case_full_monte_carlo_reports_required_probabilities_and_npv_shape():
    """AC3 (warranty/15yr/never probabilities) and AC4 (NPV at two discount
    rates) as a structural contract, on synthetic calibration inputs."""
    r = up.full_monte_carlo(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope=-1.0,
                            lossA=0.013, lossB=0.066, prod_sigma=0.02, N=500, seed=7)
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
    tor = up.tornado(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope=-1.0,
                     lossA=0.013, lossB=0.066, prod_sigma=0.02)
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
    tor = up.tornado(pre=2329.0, mid=2238.0, rte_slope=0.5, soil_slope=-1.0,
                     lossA=0.013, lossB=0.066, prod_sigma=0.02)
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
                            soil_slope=-1000.0, lossA=0.013, lossB=0.066,
                            prod_sigma=0.02, N=50, seed=1)
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
    sensitivity by roughly 1/soil_slope)."""
    pre, mid, rte_slope, soil_slope = 2329.0, 2238.0, 0.55, 0.057
    for x in (0.0, 0.02, 0.0568, 0.10):
        via_soiling = up.save1_of(c=1.0, rte=0.90, loss=x, prod_noise=1.0,
                                  pre=pre, mid=mid, rte_slope=rte_slope,
                                  soil_slope=soil_slope)
        via_prod_shortfall = up.save1_of(c=1.0, rte=0.90, loss=0.0, prod_noise=1 - x,
                                         pre=pre, mid=mid, rte_slope=rte_slope,
                                         soil_slope=soil_slope)
        via_prod_surplus = up.save1_of(c=1.0, rte=0.90, loss=0.0, prod_noise=1 + x,
                                       pre=pre, mid=mid, rte_slope=rte_slope,
                                       soil_slope=soil_slope)
        assert abs(via_soiling - via_prod_shortfall) < 1e-9, (
            f"x={x}: soiling loss and an equal-fraction prod_noise shortfall "
            f"must produce the identical save1 ({via_soiling} vs {via_prod_shortfall})")
        via_soiling_negative = up.save1_of(c=1.0, rte=0.90, loss=-x, prod_noise=1.0,
                                           pre=pre, mid=mid, rte_slope=rte_slope,
                                           soil_slope=soil_slope)
        assert abs(via_soiling_negative - via_prod_surplus) < 1e-9, (
            f"x={x}: a negative loss (generation surplus) and an equal-fraction "
            f"prod_noise surplus must produce the identical save1 "
            f"({via_soiling_negative} vs {via_prod_surplus})")
    return "soiling and production-measurement uncertainty share one calibrated generation-sensitivity, in both directions"


@case
def case_scale_production_reproduces_measured_flows_exactly_at_gen_scale_1():
    """issue #60: the identity scale_production() depends on for byte-
    identity at nominal (gen_scale=1.0) -- max(net,0)+overlap == gen0 and
    max(-net,0)+overlap == imp0 for EVERY interval -- checked on a
    synthetic array that DELIBERATELY includes an interval with
    SIMULTANEOUS nonzero import AND export (index 3 below), a real ~6% case
    in this household's own measured data (2206 of 35040 intervals), not
    just a theoretical edge case this test shouldn't skip covering."""
    imp0 = np.array([0.0, 2.0, 0.0, 1.0])
    gen0 = np.array([0.0, 0.0, 3.0, 0.5])   # index 3: overlap (both > 0)
    net = gen0 - imp0
    overlap = np.minimum(imp0, gen0)
    assert overlap[3] == 0.5, "fixture must actually exercise the overlap>0 case"
    P = np.array([1.0, 1.0, 3.0, 2.0])      # arbitrary gross production
    D = P - net                             # the identity reconstruct_gross_production() uses
    import_delta, new_export = up.scale_production(P, D, overlap, imp0, 1.0)
    assert np.allclose(import_delta, 0.0), import_delta
    assert np.allclose(new_export, gen0), (new_export, gen0)
    return "scale_production reproduces measured import/export exactly at gen_scale=1.0, including a simultaneous-flow interval"


@case
def case_scale_production_reallocates_a_loss_against_export_before_import():
    """issue #60 AC2: a production LOSS must first eat into EXPORT (the
    surplus beyond load), and only start increasing IMPORT once export
    hits zero -- not scale export proportionally while import stays frozen
    (the bug this issue fixes). Single synthetic interval: production P=5,
    load D=2 -> nominal export=3, import=0. At gen_scale=0.8, P'=4 -> still
    export=2, import untouched (the loss is small enough to be absorbed
    entirely by export). At gen_scale=0.2, P'=1 -> D=2 exceeds P', so
    export must hit exactly 0 and the FULL 1 kWh shortfall must spill into
    import."""
    P = np.array([5.0])
    D = np.array([2.0])
    overlap = np.array([0.0])
    imp0 = np.array([0.0])   # nominal: P > D, so imp0=0 by construction
    _, gen_at_1 = up.scale_production(P, D, overlap, imp0, 1.0)
    assert gen_at_1[0] == 3.0
    delta_08, gen_08 = up.scale_production(P, D, overlap, imp0, 0.8)
    assert gen_08[0] == 2.0 and delta_08[0] == 0.0, (
        "a small loss must be absorbed entirely by export, not spill into import yet")
    delta_02, gen_02 = up.scale_production(P, D, overlap, imp0, 0.2)
    assert gen_02[0] == 0.0 and delta_02[0] == 1.0, (
        "once production drops below load, export must hit exactly 0 and "
        "the shortfall must spill into import")
    return "scale_production reallocates a loss against export first, spilling into import only once export is exhausted"


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
    # issue #60: soil_slope's own MAGNITUDE grew roughly 18x once soiling
    # correctly hits GROSS production (reallocated against export first,
    # spilling into import) instead of scaling the smaller Generation/export
    # column alone -- expected and correct (most of a production loss at
    # this household turns out to have been self-consumed, not exported;
    # see production_reconstruction's own energy-conservation numbers), not
    # a regression of the "small effect" reasoning below. What that reasoning
    # was actually always about is the REALIZED swing at the household's own
    # real loss magnitude (soil_slope * lossB), which the fix leaves genuinely
    # small (still "a few percent") even though the raw per-unit slope no
    # longer is -- so this checks the realized swing, not the raw slope.
    realized_swing = abs(calib["soil_slope_mid"] * calib["lossB"])
    assert realized_swing < 0.15, (
        f"soiling's REALIZED swing at this household's own lossB "
        f"({calib['lossB']:.4f}) is {realized_swing:.4f} ({realized_swing:.1%}) "
        "-- implausibly large for a small realistic loss fraction; "
        "investigate before trusting the calibration")
    same_sign = (calib["soil_slope_mid"] > 0) == (calib["soil_slope_pre"] > 0)
    assert same_sign, (
        f"pre- ({calib['soil_slope_pre']}) and post-behavior "
        f"({calib['soil_slope_mid']}) soiling slopes disagree in sign -- not "
        "internally consistent enough to average into one slope")
    return (f"real dispatch calibration's single-pass figures match the committed "
            f"artifact within $1 (steady-state pre={calib['pre_nominal']:.2f}, "
            f"mid={calib['mid_nominal']:.2f}) "
            "with a correctly-signed RTE sensitivity and an internally-"
            f"consistent, small soiling sensitivity ({calib['soil_slope_mid']:+.4f})")


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
