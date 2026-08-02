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
import copy
import glob
import json
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


# ---------------------------------------------------------------------------
# (b) archive-gated: exercise the REAL dispatch engine and REAL household
# ---------------------------------------------------------------------------
@case
def case_dispatch_calibration_matches_committed_battery_dispatch_policies():
    _require_archive()
    calib = up.dispatch_calibration()
    dispatch = _committed("battery_dispatch_policies.json")
    committed_pre = float(dispatch["pw3"]["greedy"]["save"])
    committed_mid = float(dispatch["post_behavior"]["mid"]["battery_marginal"])
    assert abs(calib["pre_nominal"] - committed_pre) < 1.0, (
        f"recomputed pre-behavior marginal {calib['pre_nominal']:.2f} disagrees "
        f"with committed pw3.greedy.save {committed_pre} by >$1")
    assert abs(calib["mid_nominal"] - committed_mid) < 1.0, (
        f"recomputed post-behavior marginal {calib['mid_nominal']:.2f} disagrees "
        f"with committed post_behavior.mid.battery_marginal {committed_mid} by >$1")
    # higher round-trip efficiency must raise the marginal saving; more
    # soiling-driven generation loss must lower it -- sign checks on the real
    # calibrated engine, not just curve-fit arithmetic
    assert calib["rte_slope_mid"] > 0, "higher RTE must raise the battery marginal saving"
    assert calib["soil_slope_mid"] < 0, "more soiling loss must lower the battery marginal saving"
    return (f"real dispatch calibration matches the committed artifact within $1 "
            f"(pre={calib['pre_nominal']:.2f}, mid={calib['mid_nominal']:.2f}) "
            "with correctly-signed RTE/soiling sensitivities")


@case
def case_build_end_to_end_is_deterministic_and_self_consistent():
    _require_archive()
    out1 = up.build()
    out2 = up.build()
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
def case_build_output_is_json_serializable():
    _require_archive()
    out = up.build()
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
