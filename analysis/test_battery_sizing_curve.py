#!/usr/bin/env python3
"""Behavioural tests for battery_sizing_curve.py (issue #12).

battery_sizing_curve.py imports behavior_rebuild.py, which reads
private/household.yaml at ITS OWN module top level and fails closed (SystemExit)
if that file is absent -- the same situation test_dsgs_vpp_backtest.py and
test_cca_bundled_counterfactual.py already solved. Applied here too: point
household.PATH at a synthetic, invented household BEFORE importing, so this whole
file imports cleanly on any checkout, private/ or not. Cases that need the REAL
measured Green Button year or a byte-identical regeneration of the committed
artifact still gate on the private archive and SKIP rather than fail.

Run from the repo root:  ./.venv/bin/python analysis/test_battery_sizing_curve.py
"""
import glob
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

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

import rates as R                            # noqa: E402
import behavior_rebuild as br                 # noqa: E402
from battery_dispatch_policies import run_batt, billed  # noqa: E402
import battery_sizing_curve as bsc            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"
ARTIFACT = ROOT / "data" / "battery_sizing_curve.json"

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


EPS = 1e-6


# ---------------------------------------------------------------------------
# (a) synthetic-frame unit tests -- no archive needed at all
# ---------------------------------------------------------------------------
def _synthetic_day(consumption_kw=0.0, generation_kw=0.0, weekday=True):
    start = pd.Timestamp("2026-01-07") if weekday else pd.Timestamp("2026-01-10")
    dtr = pd.date_range(start, periods=96, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = [R.period_at(ts) for ts in d.dt]
    d["seas"] = "W"
    imp0 = np.full(96, consumption_kw * 0.25)
    gen0 = np.full(96, generation_kw * 0.25)
    return d, imp0, gen0


@case
def case_energy_grid_spans_the_required_range_and_includes_both_products():
    assert min(bsc.ENERGY_GRID) <= 5, "energy grid must reach down to 5 kWh"
    assert max(bsc.ENERGY_GRID) >= 40, "energy grid must reach up to 40 kWh"
    steps = [b - a for a, b in zip(bsc.ENERGY_GRID, bsc.ENERGY_GRID[1:])]
    assert max(steps) <= 5 + EPS, f"a gap in the energy grid exceeds 5 kWh: {steps}"
    assert 13.5 in bsc.ENERGY_GRID, "the base Powerwall 3 (13.5 kWh) must be an exact grid point"
    assert 27.0 in bsc.ENERGY_GRID, "PW3+Expansion (27 kWh) must be an exact grid point"
    return f"energy grid {bsc.ENERGY_GRID} spans 5-40 kWh and includes both shipping configs"


@case
def case_power_grid_spans_the_required_range_and_includes_the_shipping_power():
    assert min(bsc.POWER_GRID) <= 5, "power grid must reach down to 5 kW"
    assert max(bsc.POWER_GRID) >= 15, "power grid must reach up to 15 kW"
    assert 11.5 in bsc.POWER_GRID, "both shipping configs run at 11.5 kW; must be an exact grid point"
    return f"power grid {bsc.POWER_GRID} spans 5-15 kW and includes the shipping power"


@case
def case_cost_model_is_reproducible_from_the_reports_own_anchors():
    slope, intercept = np.polyfit(bsc.COST_ANCHORS_KWH, bsc.COST_ANCHORS_USD, 1)
    assert abs(slope - bsc.COST_SLOPE_USD_PER_KWH) < EPS
    assert abs(intercept - bsc.COST_INTERCEPT_USD) < EPS
    assert bsc.COST_SLOPE_USD_PER_KWH > 0, "cost must rise with capacity"
    return (f"cost fit reproduces from its anchors: "
            f"${bsc.COST_SLOPE_USD_PER_KWH:.2f}/kWh + ${bsc.COST_INTERCEPT_USD:.2f}")


@case
def case_cost_anchors_are_only_the_two_same_power_configs():
    """Codex adversarial review (second pass): fitting all four quoted configs
    together confounds energy cost with power cost, since only the two Tesla
    configs share the energy sweep's own 11.5 kW reference power. The fit must
    use ONLY those two -- the other two are documented context, never anchors."""
    pairs = sorted(zip(bsc.COST_ANCHORS_KWH.tolist(), bsc.COST_ANCHORS_USD.tolist()))
    expected = sorted([(13.5, 14500.0), (27.0, 20400.0)])
    assert pairs == expected, f"cost anchors drifted from the same-power pair: {pairs}"
    return "cost-fit anchors are exactly the two same-power (11.5 kW) shipping configs"


@case
def case_excluded_cost_context_names_the_two_different_power_configs_and_why():
    excluded = {(c["kwh"], c["kw"]): c["excluded_reason"] for c in bsc.EXCLUDED_COST_CONTEXT}
    assert (5.0, 3.8) in excluded and "11.5" in excluded[(5.0, 3.8)]
    assert (10.0, 7.1) in excluded and "11.5" in excluded[(10.0, 7.1)]
    return "the two different-power report configs are excluded from the fit, with a named reason"


@case
def case_cost_fit_passes_through_both_shipping_configs_exactly():
    """Because both shipping configs are the fit's only two anchors, the
    fitted cost at 13.5 kWh and 27 kWh must equal their own real quoted cost
    exactly (zero residual) -- unlike the retired 4-anchor blended fit."""
    assert abs(bsc.cost_usd(13.5) - 14500.0) < EPS
    assert abs(bsc.cost_usd(27.0) - 20400.0) < EPS
    return "the fitted cost at both shipping configs matches their real quoted cost exactly"


@case
def case_check_conservation_passes_on_a_real_dispatch_run():
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    imp0 = np.where((d.hour.values >= 16) & (d.hour.values < 21), 3.0, 0.5) * 0.25
    gen0 = np.where((d.hour.values >= 10) & (d.hour.values < 15), 4.0, 0.0) * 0.25
    imp2, exp2, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5)
    bsc._check_conservation(d, imp0, gen0, imp2, exp2, served, thru, 13.5)
    return "a real synthetic dispatch run passes the conservation check"


@case
def case_check_conservation_detects_a_corrupted_served_value():
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    imp0 = np.where((d.hour.values >= 16) & (d.hour.values < 21), 3.0, 0.5) * 0.25
    gen0 = np.where((d.hour.values >= 10) & (d.hour.values < 15), 4.0, 0.0) * 0.25
    imp2, exp2, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5)
    try:
        bsc._check_conservation(d, imp0, gen0, imp2, exp2, served + 1.0, thru, 13.5)
    except SystemExit as e:
        assert "discharge relief" in str(e)
        return "a corrupted served value is caught by the conservation check"
    raise AssertionError("a corrupted served value slipped past the conservation check")


@case
def case_check_conservation_detects_a_corrupted_throughput_value():
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    imp0 = np.where((d.hour.values >= 16) & (d.hour.values < 21), 3.0, 0.5) * 0.25
    gen0 = np.where((d.hour.values >= 10) & (d.hour.values < 15), 4.0, 0.0) * 0.25
    imp2, exp2, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5)
    try:
        bsc._check_conservation(d, imp0, gen0, imp2, exp2, served, thru + 1.0, 13.5)
    except SystemExit as e:
        assert "throughput mismatch" in str(e)
        return "a corrupted throughput value is caught by the conservation check"
    raise AssertionError("a corrupted throughput value slipped past the conservation check")


@case
def case_marginal_helper_computes_correct_finite_differences():
    rows = [{"kwh": 5, "save_usd": 100.0}, {"kwh": 10, "save_usd": 250.0},
            {"kwh": 20, "save_usd": 300.0}]
    m = bsc._marginal(rows, "kwh")
    assert m[0] is None
    assert abs(m[1] - (250.0 - 100.0) / 5) < EPS
    assert abs(m[2] - (300.0 - 250.0) / 10) < EPS
    return f"marginal helper: {m}"


@case
def case_find_knee_returns_none_when_every_point_clears_the_threshold():
    rows = [{"kwh": k} for k in (5, 10, 15)]
    huge_marginal = bsc.COST_SLOPE_USD_PER_KWH  # payback exactly 1 year
    knee = bsc._find_knee(rows, [None, huge_marginal, huge_marginal])
    assert knee is None, "a marginal saving that always clears the threshold must not flag a knee"
    return "no knee flagged when every point pays back within the threshold"


@case
def case_find_knee_flags_the_first_point_that_fails():
    rows = [{"kwh": 5}, {"kwh": 10}, {"kwh": 15}, {"kwh": 20}]
    good = bsc.COST_SLOPE_USD_PER_KWH / 5   # 5-yr payback, clears 10-yr threshold
    bad = bsc.COST_SLOPE_USD_PER_KWH / 20   # 20-yr payback, fails
    knee = bsc._find_knee(rows, [None, good, bad, good])
    assert knee is not None
    assert knee["kwh"] == 15, f"expected the knee at the first failing point (15), got {knee}"
    assert knee["prev_kwh"] == 10
    assert knee["threshold_years"] == bsc.KNEE_PAYBACK_YEARS
    return f"knee correctly flagged at the first point that fails the threshold: {knee}"


@case
def case_find_knee_treats_a_nonpositive_marginal_as_infinite_payback():
    rows = [{"kwh": 5}, {"kwh": 10}]
    knee = bsc._find_knee(rows, [None, 0.0])
    assert knee is not None and knee["marginal_payback_years"] == float("inf")
    return "a zero-or-negative marginal saving is treated as infinite payback, not divide-by-zero"


@case
def case_locate_products_refuses_a_product_not_on_the_grid():
    rows = [{"kwh": 5, "save_usd": 100.0, "kwh_served": 50.0}]
    orig = bsc.SHIPPING_PRODUCTS
    bsc.SHIPPING_PRODUCTS = [{"name": "test", "kwh": 13.5, "kw": 11.5, "cost_usd": 1}]
    try:
        try:
            bsc._locate_products(rows)
        except SystemExit as e:
            assert "not an exact grid point" in str(e)
            return "a shipping product missing from the swept grid fails closed"
        raise AssertionError("a product absent from the grid was silently accepted")
    finally:
        bsc.SHIPPING_PRODUCTS = orig


@case
def case_locate_products_refuses_a_product_off_the_reference_power():
    """energy_rows are all computed at REF_POWER_KW; a product quoted at a
    different kW must not be silently priced as if it ran at the reference
    power (adversarial review, issue #12)."""
    rows = [{"kwh": 13.5, "save_usd": 100.0, "kwh_served": 50.0}]
    orig = bsc.SHIPPING_PRODUCTS
    bsc.SHIPPING_PRODUCTS = [{"name": "test", "kwh": 13.5, "kw": 15.0, "cost_usd": 1}]
    try:
        try:
            bsc._locate_products(rows)
        except SystemExit as e:
            assert "reference power" in str(e)
            return "a shipping product off the reference power fails closed"
        raise AssertionError("a product off the reference power was silently accepted")
    finally:
        bsc.SHIPPING_PRODUCTS = orig


# ---------------------------------------------------------------------------
# (b) archive-gated cases -- need the real measured year
# ---------------------------------------------------------------------------
@case
def case_13_5kwh_current_behavior_point_matches_the_canonical_dispatch_artifact():
    _require_archive()
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    base = billed(d, imp0, gen0)
    imp2, exp2, served, thru = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5)
    save = base - billed(d, imp2, exp2)
    canon_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not canon_path.exists():
        raise SkipCase("data/battery_dispatch_policies.json not present")
    canon = json.loads(canon_path.read_text())
    assert abs(save - canon["pw3"]["greedy"]["save"]) < 1.0, (
        f"13.5 kWh / 11.5 kW point (${save:.2f}) diverges from the canonical "
        f"pw3/greedy artifact (${canon['pw3']['greedy']['save']})")
    return "the 13.5 kWh / 11.5 kW grid point agrees with the canonical dispatch artifact"


@case
def case_post_behavior_baseline_is_cheaper_than_current_behavior_by_the_ev_shift_amount():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    art = json.loads(ARTIFACT.read_text())
    delta = (art["current_behavior"]["baseline_bill_current_rates"]
             - art["post_behavior"]["baseline_bill_current_rates"])
    canon_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not canon_path.exists():
        raise SkipCase("data/battery_dispatch_policies.json not present")
    canon = json.loads(canon_path.read_text())
    assert abs(delta - canon["post_behavior"]["behavior_save"]) < 1.0, (
        f"post-behavior baseline should be cheaper by the EV-shift saving "
        f"(${canon['post_behavior']['behavior_save']}), got ${delta}")
    return f"post-behavior baseline is ${delta} cheaper, matching the EV-shift saving"


@case
def case_sensitivity_is_energy_not_power_for_this_house():
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    art = json.loads(ARTIFACT.read_text())
    for scen in ("current_behavior", "post_behavior"):
        s = art[scen]["sensitivity"]
        assert s["energy_elasticity"] > s["power_elasticity"], (
            f"{scen}: expected energy elasticity to exceed power elasticity given "
            f"this house's load shape, got energy={s['energy_elasticity']} "
            f"power={s['power_elasticity']}")
        assert s["binds"] == "energy"
    return "both scenarios report energy, not power, as the binding constraint (elasticity)"


@case
def case_sensitivity_elasticity_is_a_local_property_not_a_raw_span():
    """Codex adversarial review (second pass): a raw top-to-bottom span between
    two arbitrarily-chosen grid endpoints can flip under a different choice of
    endpoints without any real change in sensitivity. The elasticity computed
    here must instead come from a centered difference AT the shared reference
    point using only its immediate neighbors -- verify this directly by
    reproducing the calculation from a synthetic 3-point grid and confirming it
    is unaffected by a 4th point added far outside that neighborhood."""
    rows = [{"kwh": 10, "save_usd": 100.0}, {"kwh": 13.5, "save_usd": 150.0},
            {"kwh": 15, "save_usd": 160.0}]
    elasticity_a, save_at_ref_a = bsc._local_elasticity(rows, "kwh", 13.5)
    rows_with_extra = rows + [{"kwh": 1000, "save_usd": 999999.0}]
    elasticity_b, save_at_ref_b = bsc._local_elasticity(rows_with_extra, "kwh", 13.5)
    assert abs(elasticity_a - elasticity_b) < EPS, (
        "a far-away extra grid point changed the local elasticity -- it should "
        "depend only on the reference point's immediate neighbors")
    return "elasticity is unaffected by a grid point far outside the reference neighborhood"


@case
def case_elasticity_derivative_is_exact_on_an_asymmetric_quadratic_grid():
    """Codex adversarial review (third pass): a plain secant between the two
    neighbors estimates the derivative at their MIDPOINT, not at the
    reference, whenever the grid is asymmetric around the reference (true for
    both real grids here). The corrected unequal-spacing 3-point formula must
    instead be EXACT for any quadratic through the three points -- verify
    against the known analytic derivative of f(x) = x^2 (f'(x) = 2x) on a
    deliberately asymmetric grid (h0=2, h1=1, not equal)."""
    rows = [{"kwh": 1.0, "save_usd": 1.0 ** 2}, {"kwh": 3.0, "save_usd": 3.0 ** 2},
            {"kwh": 4.0, "save_usd": 4.0 ** 2}]
    elasticity, save_at_ref = bsc._local_elasticity(rows, "kwh", 3.0)
    analytic_derivative = 2 * 3.0  # d/dx[x^2] at x=3
    analytic_elasticity = analytic_derivative * 3.0 / (3.0 ** 2)
    assert abs(elasticity - analytic_elasticity) < EPS, (
        f"expected the exact quadratic derivative-based elasticity "
        f"{analytic_elasticity}, got {elasticity} -- the unequal-spacing "
        "formula must be exact for a quadratic, not merely close")
    # a plain secant between the neighbors would have given a visibly
    # different (wrong) answer, confirming this grid actually exercises the
    # asymmetry the fix addresses:
    wrong_secant_slope = (rows[2]["save_usd"] - rows[0]["save_usd"]) / (rows[2]["kwh"] - rows[0]["kwh"])
    wrong_secant_elasticity = wrong_secant_slope * 3.0 / save_at_ref
    assert abs(wrong_secant_elasticity - analytic_elasticity) > 0.1, (
        "this grid is not asymmetric enough to distinguish the correct "
        "formula from the old (wrong) secant -- strengthen the fixture")
    return "the corrected elasticity formula is exact on an asymmetric quadratic grid"


@case
def case_sensitivity_refuses_a_reference_point_with_no_flanking_neighbor():
    rows = [{"kwh": 13.5, "save_usd": 100.0}, {"kwh": 15, "save_usd": 110.0}]
    try:
        bsc._local_elasticity(rows, "kwh", 13.5)
    except SystemExit as e:
        assert "flanking grid point" in str(e)
        return "a reference point at the edge of the grid fails closed rather than indexing out of range"
    raise AssertionError("a reference point with no lower neighbor was silently accepted")


@case
def case_a_knee_is_found_in_both_scenarios_and_sits_inside_the_grid():
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    art = json.loads(ARTIFACT.read_text())
    for scen in ("current_behavior", "post_behavior"):
        knee = art[scen]["knee"]
        assert knee is not None, f"{scen}: expected a knee within the swept range"
        assert knee["kwh"] in bsc.ENERGY_GRID
        assert knee["prev_kwh"] in bsc.ENERGY_GRID
        assert knee["marginal_payback_years"] > knee["threshold_years"]
    return "both scenarios locate a knee within the swept energy grid"


@case
def case_shipping_products_payback_matches_index_html_s6_range():
    """§6 cites the 13.5 kWh post-behavior payback as ~6.2-6.5 yr; the sizing
    curve's own shipping-product lookup must agree with that range."""
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    art = json.loads(ARTIFACT.read_text())
    pw3 = next(p for p in art["post_behavior"]["shipping_products_on_curve"]
               if p["kwh"] == 13.5)
    assert 5.5 <= pw3["payback_years"] <= 7.5, (
        f"post-behavior PW3 payback {pw3['payback_years']} yr is outside the "
        "range §6 already cites (~6.2-6.5 yr, plus fit slack)")
    return f"post-behavior PW3 payback {pw3['payback_years']} yr matches §6's cited range"


@case
def case_steady_state_shipping_saves_stay_close_to_the_canonical_single_pass_artifact():
    """The steady-state correction (Codex adversarial review, third pass)
    deliberately makes this artifact's shipping-product savings differ
    slightly from battery_dispatch_policies.json's own pw3/pw3x figures --
    that canonical artifact stays single-pass by design (correcting it is a
    separate concern, out of this issue's scope). Lock the gap to a small,
    documented tolerance (TECHNICAL.md cites $2.14 as the observed max) so a
    future change can't silently let steady-state and single-pass drift far
    apart without anyone noticing."""
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    canon_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not canon_path.exists():
        raise SkipCase("data/battery_dispatch_policies.json not present")
    art = json.loads(ARTIFACT.read_text())
    canon = json.loads(canon_path.read_text())
    pairs = [
        ("current 13.5kWh", art["current_behavior"]["shipping_products_on_curve"][0]["save_usd"],
         canon["pw3"]["greedy"]["save"]),
        ("post 13.5kWh", art["post_behavior"]["shipping_products_on_curve"][0]["save_usd"],
         canon["post_behavior"]["mid"]["battery_marginal"]),
        ("current 27kWh", art["current_behavior"]["shipping_products_on_curve"][1]["save_usd"],
         canon["pw3x"]["greedy"]["save"]),
        ("post 27kWh", art["post_behavior"]["shipping_products_on_curve"][1]["save_usd"],
         canon["post_behavior"]["high"]["battery_marginal"]),
    ]
    worst = 0.0
    for name, steady, single_pass in pairs:
        diff = abs(steady - single_pass)
        worst = max(worst, diff)
        assert diff < 3.0, (
            f"{name}: steady-state (${steady}) vs canonical single-pass "
            f"(${single_pass}) diverge by ${diff:.2f}, more than the documented tolerance")
    return f"steady-state shipping saves stay within ${worst:.2f} of the canonical single-pass artifact"


@case
def case_artifact_regenerates_byte_identically():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase("data/battery_sizing_curve.json not present")
    before = ARTIFACT.read_bytes()
    import os as _os
    cwd = _os.getcwd()
    _os.chdir(str(ROOT))
    try:
        bsc.main()
    finally:
        _os.chdir(cwd)
    after = ARTIFACT.read_bytes()
    assert after == before, "data/battery_sizing_curve.json is not reproducible"
    return "data/battery_sizing_curve.json regenerates byte-identically"


# ---------------------------------------------------------------------------
def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
