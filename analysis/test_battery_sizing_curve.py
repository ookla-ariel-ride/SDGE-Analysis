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
import suite_runner  # noqa: E402

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
    documented tolerance (TECHNICAL.md cites $2.15 as the observed max, as of
    issue #40's Powerwall 3 charge/discharge split, bare unit vs. with-
    expansion) so a future change can't silently let steady-state and
    single-pass drift far apart without anyone noticing."""
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
# issue #40 -- charge and discharge power are now DISTINCT parameters, not one
# symmetric power_kw serving both directions. Tesla's own official 2025
# Powerwall 3 Datasheet gives 11.5 kW continuous DISCHARGE and 5 kW continuous
# CHARGE (single unit, no expansions) as genuinely different figures -- see
# research/battery-research-notes.md for the citation.
# ---------------------------------------------------------------------------
@case
def case_run_batt_charge_and_discharge_power_are_named_and_tracked_distinctly():
    """AC5: the two directions must be named distinctly wherever both are
    used, not one variable serving double duty. run_batt's power_kw
    (discharge) and charge_kw (charge) are separate parameters; passing them
    different values must produce a different result than passing them the
    same value, proving they are actually wired to different code paths, not
    just two names for the same number."""
    import inspect
    sig = inspect.signature(run_batt)
    assert "power_kw" in sig.parameters, "run_batt must expose a discharge-power parameter"
    assert "charge_kw" in sig.parameters, "run_batt must expose a SEPARATE charge-power parameter"
    assert sig.parameters["charge_kw"].default is None, \
        "charge_kw must default to None (reuse power_kw) for exact backward compatibility"

    # A SINGLE 15-minute interval (not a full day) with a large solar surplus
    # and an empty battery: with a whole day's worth of intervals available,
    # even a lower charge rate eventually tops the battery off via overnight
    # grid top-up, masking any rate difference in cumulative throughput. One
    # interval isolates the per-interval RATE, which is what charge_kw caps.
    dtr = pd.date_range("2026-01-07 12:00", periods=1, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = [R.period_at(ts) for ts in d.dt]
    d["seas"] = "W"
    imp0 = np.full(1, 0.0)
    gen0 = np.full(1, 5.0)  # 5 kWh surplus this interval, above both caps' per-interval kWh

    _, _, served_sym, thru_sym = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5, soc0=0.0)
    _, _, served_asym, thru_asym = run_batt(
        d, imp0, gen0, 13.5, "greedy", power_kw=11.5, charge_kw=5.0, soc0=0.0)
    # discharge is identical (charge_kw does not touch the discharge branch);
    # throughput (charging) must be LOWER with the tighter 5 kW charge cap on
    # this fixture, proving charge_kw actually gates a different code path
    # than power_kw rather than being cosmetic.
    assert abs(served_sym - served_asym) < EPS, \
        "charge_kw must not affect discharge -- served kWh changed"
    assert thru_asym < thru_sym - EPS, \
        "a tighter charge_kw must reduce charging throughput on a fixture " \
        "whose solar surplus exceeds both caps"
    return "run_batt's power_kw (discharge) and charge_kw (charge) are distinct and independently wired"


@case
def case_charge_kw_defaults_to_power_kw_for_exact_backward_compatibility():
    """Every call site that predates issue #40 does not pass charge_kw; this
    must reproduce byte-for-byte (not just approximately) the symmetric
    behavior that existed before charge_kw was added."""
    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    imp0 = np.where((d.hour.values >= 16) & (d.hour.values < 21), 3.0, 0.5) * 0.25
    gen0 = np.where((d.hour.values >= 10) & (d.hour.values < 15), 4.0, 0.0) * 0.25
    r_default = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5)
    r_explicit = run_batt(d, imp0, gen0, 13.5, "greedy", power_kw=11.5, charge_kw=11.5)
    for a, b in zip(r_default[:2], r_explicit[:2]):
        assert np.array_equal(a, b), "charge_kw=None must exactly match charge_kw=power_kw"
    assert r_default[2] == r_explicit[2] and r_default[3] == r_explicit[3]
    return "charge_kw=None reproduces charge_kw=power_kw exactly"


@case
def case_steady_state_run_and_sweep_thread_charge_kw_through_to_run_batt():
    """battery_sizing_curve.py's own _steady_state_run/_sweep wrappers must
    pass charge_kw through to run_batt rather than silently dropping it.
    _steady_state_run's own SOC-convergence iteration makes an economic
    (savings/throughput) probe unreliable here -- repeated annual passes with
    an unlimited surplus available each pass converge to the same terminal
    SOC regardless of per-interval rate, given enough iterations -- so this
    checks the actual PLUMBING directly: a spy standing in for run_batt
    records the charge_kw it was called with, proving it is threaded through
    rather than silently dropped."""
    import inspect
    assert "charge_kw" in inspect.signature(bsc._steady_state_run).parameters
    assert "charge_kw" in inspect.signature(bsc._sweep).parameters

    d, imp0, gen0 = _synthetic_day(consumption_kw=0.0)
    calls = []
    real_run_batt = bsc.run_batt

    def spy(*args, **kwargs):
        calls.append(kwargs.get("charge_kw"))
        return real_run_batt(*args, **kwargs)

    bsc.run_batt = spy
    try:
        bsc._steady_state_run(d, imp0, gen0, 13.5, 11.5, charge_kw=5.0)
    finally:
        bsc.run_batt = real_run_batt
    assert calls, "run_batt was never called"
    assert all(c == 5.0 for c in calls), \
        f"_steady_state_run did not thread charge_kw=5.0 through to run_batt: {calls}"
    return "_steady_state_run/_sweep thread charge_kw through to run_batt, not dropped"


@case
def case_sweep_routes_the_expansion_charge_rate_only_above_the_bare_unit_reference():
    """issue #40 Finding 1 (Codex adversarial review): an earlier version of
    _sweep() applied CHARGE_KW (the BARE-unit 5 kW rate) uniformly to every
    energy-grid point, including 27 kWh and above -- capacities that require
    at least one expansion pack and so are cited at CHARGE_KW_WITH_EXPANSION
    (8 kW) instead. This spies on run_batt (via _steady_state_run, which
    _sweep calls once per grid point) to prove the ENERGY sweep actually
    switches constants at the REF_ENERGY_KWH (13.5 kWh) boundary in
    production code, not just in a docstring: every point at or below 13.5
    kWh must be called with CHARGE_KW, every point above it with
    CHARGE_KW_WITH_EXPANSION. The POWER sweep never leaves the bare-unit
    capacity, so it must never receive the expansion rate at all."""
    from battery_dispatch_policies import CHARGE_KW, CHARGE_KW_WITH_EXPANSION
    assert CHARGE_KW != CHARGE_KW_WITH_EXPANSION, \
        "bare-unit and with-expansion charge rates must be distinct constants"
    assert CHARGE_KW == 5.0 and CHARGE_KW_WITH_EXPANSION == 8.0

    d, imp0, gen0 = _synthetic_day(consumption_kw=1.0, generation_kw=6.0)
    d["ym"] = d.dt.dt.strftime("%Y-%m")   # billed() groups by this; _synthetic_day omits it
    base_bill = billed(d, imp0, gen0)
    calls = []
    real_run_batt = bsc.run_batt

    def spy(*args, **kwargs):
        cap = args[3] if len(args) > 3 else kwargs.get("cap")
        calls.append((cap, kwargs.get("charge_kw")))
        return real_run_batt(*args, **kwargs)

    bsc.run_batt = spy
    try:
        energy_rows = bsc._sweep(d, imp0, gen0, base_bill, bsc.ENERGY_GRID, "energy",
                                  charge_kw=CHARGE_KW, charge_kw_with_expansion=CHARGE_KW_WITH_EXPANSION)
        energy_calls = list(calls)
        calls.clear()
        power_rows = bsc._sweep(d, imp0, gen0, base_bill, bsc.POWER_GRID, "power",
                                 charge_kw=CHARGE_KW)
        power_calls = list(calls)
    finally:
        bsc.run_batt = real_run_batt

    for cap, chg in energy_calls:
        if cap <= bsc.REF_ENERGY_KWH:
            assert chg == CHARGE_KW, \
                f"energy-grid point {cap} kWh (at or below the bare-unit reference) " \
                f"must use CHARGE_KW ({CHARGE_KW}), got {chg}"
        else:
            assert chg == CHARGE_KW_WITH_EXPANSION, \
                f"energy-grid point {cap} kWh (above the bare-unit reference, " \
                f"requires expansion) must use CHARGE_KW_WITH_EXPANSION " \
                f"({CHARGE_KW_WITH_EXPANSION}), got {chg}"
    assert any(cap == 27.0 for cap, _ in energy_calls), \
        "energy grid must include the 27 kWh expansion point for this test to be meaningful"
    assert all(chg != CHARGE_KW_WITH_EXPANSION for _, chg in power_calls), \
        "the power sweep never leaves the bare-unit 13.5 kWh capacity, so it " \
        "must never be called with the expansion charge rate"
    assert energy_rows and power_rows
    return ("_sweep routes CHARGE_KW_WITH_EXPANSION to every energy-grid point "
            "above 13.5 kWh (including the real 27 kWh product) and CHARGE_KW "
            "at or below it; the power sweep never uses the expansion rate")


@case
def case_power_sweep_holds_charge_kw_fixed_at_every_point_not_symmetric_with_discharge():
    """Codex adversarial review, fourth pass: the power sweep is supposed to
    isolate discharge power as the SOLE varying dimension. An earlier version
    passed charge_kw=CHARGE_KW only at the one real REF_POWER_KW anchor and
    let every OTHER power-sweep point fall back to charge_kw=None (symmetric
    -- charge power silently tracking whatever hypothetical discharge power
    that point swept to), a second variable moving alongside the named one
    and confounding the power-elasticity derivative specifically. Spies on
    run_batt (via _steady_state_run) across the FULL power grid and asserts
    every single call receives the SAME charge_kw, regardless of the power_kw
    (discharge) value varying underneath it -- the actual regression this
    finding is about, not just that the module's constants exist."""
    from battery_dispatch_policies import CHARGE_KW
    d, imp0, gen0 = _synthetic_day(consumption_kw=1.0, generation_kw=6.0)
    d["ym"] = d.dt.dt.strftime("%Y-%m")
    base_bill = billed(d, imp0, gen0)
    calls = []
    real_run_batt = bsc.run_batt

    def spy(*args, **kwargs):
        calls.append((kwargs.get("power_kw"), kwargs.get("charge_kw")))
        return real_run_batt(*args, **kwargs)

    bsc.run_batt = spy
    try:
        power_rows = bsc._sweep(d, imp0, gen0, base_bill, bsc.POWER_GRID, "power",
                                 charge_kw=CHARGE_KW)
    finally:
        bsc.run_batt = real_run_batt

    # _steady_state_run may call run_batt more than once per grid point while
    # converging SOC, so len(calls) >= len(POWER_GRID), not necessarily equal.
    assert len(calls) >= len(bsc.POWER_GRID), \
        f"expected at least one run_batt call per power-grid point, got {len(calls)}"
    powers_seen = {p for p, _ in calls}
    assert len(powers_seen) == len(bsc.POWER_GRID), \
        "power_kw did not actually vary across the sweep -- test fixture is broken"
    charges_seen = {c for _, c in calls}
    assert charges_seen == {CHARGE_KW}, (
        f"every power-sweep point must be called with the SAME fixed "
        f"charge_kw ({CHARGE_KW}) regardless of its own varying discharge "
        f"power -- got distinct charge_kw values across the sweep: {calls}")
    assert power_rows
    return ("every power-sweep point uses the same fixed charge_kw "
            f"({CHARGE_KW}) across all {len(bsc.POWER_GRID)} discharge-power "
            "points -- charge no longer tracks the swept discharge dimension")


@case
def case_energy_elasticity_charge_held_fixed_diagnostic_is_close_to_the_published_number():
    """Codex adversarial review, fourth pass: the published energy elasticity's
    flanking point above the 13.5 kWh reference (15 kWh) correctly uses the
    real with-expansion charge rate (8 kW) while the reference point uses the
    bare-unit rate (5 kW) -- a small second variable in that one derivative.
    _energy_elasticity_charge_held_fixed re-derives the same local elasticity
    with the flanking point's charge rate counterfactually held at 5 kW, as a
    diagnostic on how much of the confound is baked into the published number.
    This is a live (not committed-artifact-only) check against the real
    archive: the two variants must agree within a documented tolerance (the
    confound is expected to be small relative to the ~150-190x energy-vs-
    power gap that drives the report's conclusion), and the qualitative
    conclusion -- energy elasticity is at least an order of magnitude larger
    than power elasticity -- must survive under EITHER variant."""
    _require_archive()
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    base_bill = billed(d, imp0, gen0)
    from battery_dispatch_policies import CHARGE_KW, CHARGE_KW_WITH_EXPANSION
    energy_rows = bsc._sweep(d, imp0, gen0, base_bill, bsc.ENERGY_GRID, "energy",
                              charge_kw=CHARGE_KW, charge_kw_with_expansion=CHARGE_KW_WITH_EXPANSION)
    e_marg = bsc._marginal(energy_rows, "kwh")
    for r, m in zip(energy_rows, e_marg):
        r["marginal_save_usd_per_kwh"] = m
    power_rows = bsc._sweep(d, imp0, gen0, base_bill, bsc.POWER_GRID, "power", charge_kw=CHARGE_KW)

    published, _ = bsc._local_elasticity(energy_rows, "kwh", bsc.REF_ENERGY_KWH)
    diagnostic, hi_save = bsc._energy_elasticity_charge_held_fixed(d, imp0, gen0, base_bill, energy_rows)
    power_elasticity, _ = bsc._local_elasticity(power_rows, "kw", bsc.REF_POWER_KW)

    RELATIVE_TOLERANCE = 0.02   # documented: the confound moves the number <2%
    rel_diff = abs(diagnostic - published) / abs(published)
    assert rel_diff < RELATIVE_TOLERANCE, (
        f"charge-held-fixed diagnostic ({diagnostic}) differs from the "
        f"published energy elasticity ({published}) by {rel_diff:.1%}, "
        f"outside the documented {RELATIVE_TOLERANCE:.0%} tolerance -- the "
        "confound may be larger than TECHNICAL.md's stated small effect")

    MIN_ORDER_OF_MAGNITUDE = 10  # energy must dominate power by at least 10x
    if abs(power_elasticity) > 1e-6:
        assert abs(published / power_elasticity) > MIN_ORDER_OF_MAGNITUDE
        assert abs(diagnostic / power_elasticity) > MIN_ORDER_OF_MAGNITUDE
    # else: power_elasticity is a true/floating-point zero -- energy trivially
    # dominates (nothing to divide by), consistent with the artifact's own
    # null-ratio handling in _scenario().
    return (f"charge-held-fixed diagnostic ({diagnostic:.4f}) is within "
            f"{rel_diff:.2%} of the published energy elasticity ({published:.4f}); "
            "energy still dominates power by >10x under either variant")


@case
def case_committed_sizing_curve_artifact_27kwh_point_matches_the_expansion_rate_not_the_bare_rate():
    """Regression guard against issue #40 Finding 1 recurring silently: this
    pins the committed artifact's 27 kWh shipping-product save/served figures
    to the values this household's data produces under the correct
    CHARGE_KW_WITH_EXPANSION (8 kW) rate, which happen to be DIFFERENT from
    (and, on this data, slightly higher than) what the wrong uniform-5 kW-
    everywhere bug produced (save_usd 2792.51, the pre-fix committed value).
    A future regeneration that silently reverts to applying the bare-unit
    rate above 13.5 kWh would reproduce the old 2792.51 figure and fail this
    exact-match check, even though nothing else about the artifact's shape
    would look wrong."""
    if not ARTIFACT.is_file():
        raise SkipCase(f"{ARTIFACT} not present")
    data = json.loads(ARTIFACT.read_text())
    ship = data["current_behavior"]["shipping_products_on_curve"]
    exp_row = next((r for r in ship if abs(r["kwh"] - 27.0) < EPS), None)
    bare_row = next((r for r in ship if abs(r["kwh"] - 13.5) < EPS), None)
    assert exp_row is not None and bare_row is not None, \
        "committed artifact must list both shipping products"
    WRONG_UNIFORM_5KW_SAVE = 2792.51   # the pre-Finding-1-fix committed value
    assert abs(exp_row["save_usd"] - WRONG_UNIFORM_5KW_SAVE) > 0.10, (
        f"27 kWh shipping product's save_usd ({exp_row['save_usd']}) matches the "
        f"OLD uniform-bare-unit-charge-rate figure ({WRONG_UNIFORM_5KW_SAVE}) -- "
        "this is the exact signature of Finding 1 (CHARGE_KW applied above the "
        "bare-unit reference instead of CHARGE_KW_WITH_EXPANSION) recurring")
    assert exp_row["save_usd"] == 2792.85, \
        f"27 kWh shipping product's save_usd drifted from the known-correct " \
        f"value (2792.85): {exp_row['save_usd']}"
    assert bare_row["save_usd"] == 2327.42, \
        "13.5 kWh (bare-unit) shipping product's save_usd should be unaffected " \
        f"by the expansion-rate fix: {bare_row['save_usd']}"
    return ("committed sizing-curve artifact's 27 kWh point (save_usd=2792.85) "
            "reflects CHARGE_KW_WITH_EXPANSION, not the old uniform bare-unit "
            "figure (2792.51)")


@case
def case_committed_artifact_power_sweep_confound_fix_is_reflected():
    """Regression guard on the committed artifact for the power sweep's own
    charge-power confound fix (Codex adversarial review, fourth pass): pins
    power_elasticity to the value produced once charge_kw is held fixed at
    every power-sweep point (0.0025 current-behavior; ~0, not the old
    0.0004, post-behavior -- floating-point noise around a true zero once
    the confound is removed), the ratio fields' correct null-handling when
    power_elasticity has nothing meaningful to divide by, and that the
    diagnostic elasticity field is present and close to the published one."""
    if not ARTIFACT.is_file():
        raise SkipCase(f"{ARTIFACT} not present")
    data = json.loads(ARTIFACT.read_text())
    cur = data["current_behavior"]["sensitivity"]
    post = data["post_behavior"]["sensitivity"]

    OLD_SYMMETRIC_POWER_ELASTICITY_CUR = 0.003    # pre-fourth-pass-fix committed value
    OLD_SYMMETRIC_POWER_ELASTICITY_POST = 0.0004  # pre-fourth-pass-fix committed value
    assert cur["power_elasticity"] != OLD_SYMMETRIC_POWER_ELASTICITY_CUR, (
        "current-behavior power_elasticity still matches the OLD symmetric-"
        "charge-power figure -- the fourth-pass confound fix may have reverted")
    assert cur["power_elasticity"] == 0.0025, cur["power_elasticity"]
    assert post["power_elasticity"] != OLD_SYMMETRIC_POWER_ELASTICITY_POST
    assert abs(post["power_elasticity"]) < 1e-6, post["power_elasticity"]

    # ratio fields: current has a real, finite ratio; post's power_elasticity
    # is a true zero, so its ratio fields must be null, not a huge fabricated
    # number produced by dividing by floating-point noise.
    assert cur["energy_elasticity_ratio_to_power_real"] is not None
    assert cur["energy_elasticity_ratio_to_power_real"] > 100
    assert post["energy_elasticity_ratio_to_power_real"] is None
    assert post["ratio_null_note"], "post-behavior must explain why its ratio is null"

    # diagnostic field present and within the documented tolerance of published
    for block in (cur, post):
        diag = block["energy_elasticity_charge_held_fixed_diagnostic"]
        pub = block["energy_elasticity"]
        rel = abs(diag - pub) / abs(pub)
        assert rel < 0.02, (diag, pub, rel)
    return ("committed artifact reflects the power-sweep charge-fixed fix "
            "(power_elasticity 0.0025 current / ~0 post, correct null-ratio "
            "handling) and the energy-elasticity diagnostic is within 2% of "
            "published in both scenarios")


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
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
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
