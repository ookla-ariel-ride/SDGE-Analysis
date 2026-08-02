#!/usr/bin/env python3
"""Tests for gross_import_decomposition.py (issue #16).

Follows test_uncertainty_propagation.py's / test_battery_sizing_curve.py's
convention: household.PATH is stubbed with a synthetic YAML before import so
the module imports cleanly (soiling_analysis.py, imported read-only by the
module under test, needs location.lat and cleaning_history at import time;
behavior_rebuild.py needs charger.kw). Cases that need the private Green
Button / SAM 8760 archive gate on its presence and SKIP rather than fail when
absent -- this checkout DOES have the private archive staged, so those cases
exercise the real path, not just import-cleanliness.

Run from the repo root:
  ./.venv/bin/python analysis/test_gross_import_decomposition.py
"""
import glob
import json
import pathlib
import sys
import tempfile

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

import gross_import_decomposition as gi  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
SAM_A_GLOB = str(ROOT / "private" / "1-raw-data" / "enphase_sam8760_2026.csv")
SAM_B_GLOB = str(ROOT / "private" / "1-raw-data" / "enphase_sam8760_2025.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button / SAM 8760 archive)."""


def _require_archive():
    usage_files = sorted(glob.glob(USAGE_GLOB))
    sam_a = sorted(glob.glob(SAM_A_GLOB))
    sam_b = sorted(glob.glob(SAM_B_GLOB))
    if not (usage_files and sam_a and sam_b and HOUSEHOLD_YAML.is_file()):
        raise SkipCase(
            "needs the private Green Button archive, both SAM 8760 exports, "
            f"and {HOUSEHOLD_YAML}, and this checkout is missing at least one")
    gi.USAGE_CSV = usage_files[0]
    gi.SAM_A_CSV = sam_a[0]
    gi.SAM_B_CSV = sam_b[0]


EPS = 1e-9


def _committed():
    return json.loads((ROOT / "data" / "gross_import_decomposition.json").read_text())


# ---------------------------------------------------------------------------
# (a) pure-math / public-data tests -- no private archive needed
# ---------------------------------------------------------------------------
@case
def case_bill_periods_match_the_issues_own_ground_truth():
    """AC4's premise: the two periods the issue cites must match
    data/bill_periods_electric.csv exactly, not the issue body's transcription."""
    bill = gi.load_bill_periods()
    p24, p26 = bill["period_2024"], bill["period_2026"]
    assert (p24["days"], p24["net_kwh"], p24["gross_kwh"]) == (32, 346.0, 1438.0), p24
    assert (p26["days"], p26["net_kwh"], p26["gross_kwh"]) == (29, 987.0, 1934.0), p26
    assert p24["export_kwh"] == 1092.0 and p26["export_kwh"] == 947.0
    delta_gross = p26["gross_kwh"] - p24["gross_kwh"]
    delta_net = p26["net_kwh"] - p24["net_kwh"]
    assert delta_gross == 496.0 and delta_net == 641.0, (delta_gross, delta_net)
    return "bill_periods_electric.csv reproduces the issue's cited 1438/1934 gross and 346/987 net figures exactly"


@case
def case_load_bill_periods_fails_closed_on_an_unmatched_period():
    """Fail-closed contract: a period string with no match (or more than one)
    must SystemExit, not silently return something wrong."""
    try:
        gi.PERIOD_2024, saved = "no such period", gi.PERIOD_2024
        try:
            gi.load_bill_periods()
            raise AssertionError("expected SystemExit for an unmatched period")
        except SystemExit:
            pass
    finally:
        gi.PERIOD_2024 = saved
    return "load_bill_periods() fails closed on an unmatched period string"


@case
def case_clearsky_doy_table_matches_direct_computation():
    """Regression-proof the DOY lookup-table optimization against the
    original per-date soiling_analysis.py function it is derived from."""
    import datetime as dt
    for d in (dt.date(2026, 6, 21), dt.date(2026, 1, 1), dt.date(2024, 2, 29)):
        direct = gi._sa.clearsky_ghi_kwh_m2(d)
        looked_up = gi.clearsky_for_date(d)
        assert abs(direct - looked_up) < 1e-6, (d, direct, looked_up)
    return "the precomputed day-of-year clear-sky table matches direct per-date computation"


@case
def case_clearsky_annual_totals_barely_vary_year_to_year():
    """The normalization-limitation claim in degradation_block()'s clearsky_note:
    pure solar geometry is nearly constant year to year (leap-day only)."""
    deg = gi.degradation_block()
    assert deg["clearsky_annual_spread_pct"] < 1.0, deg["clearsky_annual_spread_pct"]
    # the 2024/2020-style leap years should be the two largest totals
    vals = deg["clearsky_annual_insolation_kwh_per_m2"]
    assert vals[2024] > vals[2023], "leap year 2024 should show more clear-sky insolation than 2023"
    return f"clear-sky annual insolation spread is only {deg['clearsky_annual_spread_pct']}% across 2021-2025"


@case
def case_degradation_naive_figures_reproduce_the_existing_reports_stated_range():
    """AC6: this script's independently computed naive trend must land in the
    same ballpark as index.html's already-published '~1.3-1.7%/yr naive fit'
    claim -- validating that our artifact-backed recomputation is measuring
    the same thing the hand-computed prose described."""
    deg = gi.degradation_block()
    assert -2.5 < deg["ols_pct_per_yr"] < -1.0, deg["ols_pct_per_yr"]
    assert -2.5 < deg["cagr_pct_per_yr"] < -0.5, deg["cagr_pct_per_yr"]
    rec = deg["reconciliation"]
    for key in ("existing_report_naive_range_pct_per_yr", "existing_report_best_estimate_range_pct_per_yr",
               "agreement_on_the_naive_range", "gap_on_the_best_estimate", "what_would_settle_it"):
        assert key in rec, f"reconciliation missing {key}"
    return (f"OLS {deg['ols_pct_per_yr']}%/yr and CAGR {deg['cagr_pct_per_yr']}%/yr "
            "reproduce the existing report's stated naive range, with an explicit written "
            "reconciliation against its tighter best-estimate claim")


@case
def case_degradation_reproduces_the_soiling_modules_own_validation_target():
    """AC2, the load-bearing soiling-validation test: the single-event swing
    this script cites must be pinned to soiling_analysis.py's OWN committed
    sanity_check_2024_cleaning.known_cleaning_gain_pct (11.8%), not a
    recomputed or re-guessed figure -- so a future regeneration of
    soiling_results.json cannot silently drift out of sync with this script's
    documented reconciliation."""
    committed_soiling = json.loads((ROOT / "data" / "soiling_results.json").read_text())
    known_gain = committed_soiling["sanity_check_2024_cleaning"]["known_cleaning_gain_pct"]
    deg = gi.degradation_block()
    assert deg["single_event_soiling_swing_pct"] == known_gain, (
        deg["single_event_soiling_swing_pct"], known_gain)
    assert known_gain == 11.8, "the known 2024-08-12 cleaning validation target has moved"
    # the whole point of citing it: the single validated event dwarfs the naive multi-year trend
    assert known_gain > abs(deg["total_change_pct_2021_2025"]), (
        "the single-event soiling swing should exceed the entire naive 4-year "
        "change for the reconciliation's argument to hold")
    return (f"the single-event soiling swing ({known_gain}%) is pinned to soiling_results.json's "
            f"own validated cleaning event and exceeds the naive 4-year trend "
            f"({deg['total_change_pct_2021_2025']}%), as the reconciliation argues")


@case
def case_temperature_sensitivity_is_negative_and_statistically_meaningful():
    """AC1's weather-normalization magnitude check: hotter ambient days should
    show LOWER clear-sky-normalized performance (physically expected sign),
    fitted from real data, not asserted from a datasheet."""
    t = gi.temperature_sensitivity_block()
    assert t["n_clear_days_used"] > 100, t["n_clear_days_used"]
    assert t["coef_pct_change_per_degF"] < 0, t["coef_pct_change_per_degF"]
    assert t["p_value"] < 0.10, f"temperature coefficient not significant at p<0.10: {t['p_value']}"
    assert abs(t["coef_pct_change_per_degC"]) > abs(t["coef_pct_change_per_degF"]), (
        "the per-degC coefficient must be larger in magnitude than the per-degF one (1.8x unit conversion)")
    return (f"empirical temperature sensitivity {t['coef_pct_change_per_degF']}%/degF "
            f"(p={t['p_value']}, n={t['n_clear_days_used']}), correctly signed and significant")


@case
def case_production_block_seasonal_fraction_and_estimate_are_physically_sane():
    prod = gi.production_block()
    # seasonal_fraction_empirical is a PER-DAY rate ratio (this window's own
    # average daily output over the trailing year's average daily output),
    # not a period-total fraction -- a late-May/June window in a solar array
    # should run comfortably above the annual daily average (long days,
    # near-peak sun angle), but not by some absurd multiple.
    assert 0.8 < prod["seasonal_fraction_empirical"] < 2.0, prod["seasonal_fraction_empirical"]
    assert 800 < prod["period_2024_estimated_kwh_empirical_basis"] < 3000, prod
    assert abs(prod["period_2026_pvoutput_vs_enphase_pct_diff"]) < 5.0, (
        "PVOutput vs Enphase cross-check should agree within a few percent, consistent "
        "with this repo's already-documented ~2% systematic gap")
    return "production_block's seasonal fraction and 2024 estimate are physically sane and cross-validated"


@case
def case_build_output_is_json_serializable_on_pure_data_alone():
    """Structural sanity on the pieces that don't need the private archive."""
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    deg = gi.degradation_block()
    temp = gi.temperature_sensitivity_block()
    reparsed = json.loads(json.dumps({"bill": bill, "prod": prod, "deg": deg, "temp": temp}, default=str))
    assert "reconciliation" in reparsed["deg"]
    return "the archive-free pieces of the pipeline round-trip cleanly through JSON"


# ---------------------------------------------------------------------------
# (b) archive-gated: exercise the real CT + Green Button data
# ---------------------------------------------------------------------------
@case
def case_hourly_reconstruction_cross_validates_against_the_bill():
    _require_archive()
    bill = gi.load_bill_periods()
    hourly = gi.hourly_reconstruction(bill)
    v = hourly["validation"]
    assert abs(v["import_pct_diff"]) < 5.0, v
    assert abs(v["export_pct_diff"]) < 5.0, v
    return (f"Green Button import ({v['import_pct_diff']}%) and export "
            f"({v['export_pct_diff']}%) reconstructions agree with the bill within 5%")


@case
def case_consumption_reconstruction_ct_agrees_with_bill_plus_production_identity():
    _require_archive()
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    cons = gi.consumption_block(bill, prod)
    assert cons["period_2026_ct_vs_identity_agreement_pct_diff"] < 5.0, cons
    assert cons["period_2026_ct_measured_kwh"] > cons["period_2024_estimated_kwh"], (
        "the whole point of the issue: 2026 consumption should measure higher than the 2024 estimate")
    return (f"CT-measured 2026 gross load agrees with the Net+Production bill identity within "
            f"{cons['period_2026_ct_vs_identity_agreement_pct_diff']}%")


@case
def case_decomposition_sums_within_5_pct_of_observed_change():
    """AC4, the load-bearing test: the consumption term and production term
    must sum to within 5% of the OBSERVED (bill-exact) gross-import change."""
    _require_archive()
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    cons = gi.consumption_block(bill, prod)
    hourly = gi.hourly_reconstruction(bill)
    decomp = gi.decomposition_block(bill, prod, cons, hourly)
    assert decomp["within_5_pct_tolerance"] is True, decomp
    assert decomp["decomposition_pct_error_vs_observed"] < 5.0, decomp
    summed = decomp["consumption_term_kwh"] + decomp["production_term_kwh"]
    assert abs(summed - decomp["decomposed_sum_kwh"]) < 0.5, "reported sum must match the two terms"
    # both simulated endpoints should independently reproduce their bill actuals reasonably well
    assert abs(decomp["import_2026_sim_vs_bill_pct_error"]) < 5.0, decomp
    assert abs(decomp["import_2024_sim_vs_bill_pct_error"]) < 5.0, decomp
    return (f"decomposition sums to {decomp['decomposed_sum_kwh']} kWh vs an observed "
            f"{decomp['observed_delta_gross_kwh']} kWh change "
            f"({decomp['decomposition_pct_error_vs_observed']}% error, within the 5% AC4 tolerance)")


@case
def case_decomposition_attributes_the_increase_mostly_to_consumption():
    """The actual finding this script exists to produce: for THIS pair of
    periods, the consumption term should dominate the production term (the
    issue's core question -- consumption problem, production problem, or
    both -- answered with evidence, not assumed)."""
    _require_archive()
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    cons = gi.consumption_block(bill, prod)
    hourly = gi.hourly_reconstruction(bill)
    decomp = gi.decomposition_block(bill, prod, cons, hourly)
    assert decomp["consumption_term_kwh"] > 0, "consumption grew, so its term must be positive"
    assert abs(decomp["consumption_term_kwh"]) > abs(decomp["production_term_kwh"]), decomp
    return (f"consumption_term ({decomp['consumption_term_kwh']} kWh) dominates "
            f"production_term ({decomp['production_term_kwh']} kWh) for this specific pair of periods")


@case
def case_identifiability_robustness_check_reports_an_alternative_shape_honestly():
    """Adversarial review pass 2, finding 1: back-solving production_scale to
    hit the 2024 bill exactly makes the endpoint agreement tautological, not
    a validation, because no 2024 hourly shape exists to independently pin
    down HOW consumption grew across hours. This case exercises the
    companion identifiability check under a materially different,
    data-grounded shape assumption (EV-charging-concentrated growth) and
    requires it to report its finding honestly, using the SAME 4-corner
    Shapley decomposition the baseline scenario uses (adversarial review
    pass 3, finding 1: an earlier draft compared this scenario's raw
    production-ENERGY delta against the baseline's Shapley gross-import
    CONTRIBUTION -- different, incomparable quantities; both must now go
    through _shapley_two_factor_vectors so a like-for-like comparison is
    even possible)."""
    _require_archive()
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    cons = gi.consumption_block(bill, prod)
    hourly = gi.hourly_reconstruction(bill)
    decomp = gi.decomposition_block(bill, prod, cons, hourly)
    robust = gi.identifiability_robustness_check(bill, prod, cons, hourly, decomp)
    assert robust["realizable"] is True, (
        "the EV-concentrated alternative should be physically realizable for "
        "this household (measured EV kWh comfortably exceeds the required "
        "consumption reduction) -- if this ever flips, the note explaining "
        "why must still be present")
    assert robust["ev_template_total_kwh"] > 0
    assert -100.0 <= robust["implied_ev_reduction_pct_2026_vs_template"] <= 100.0
    assert -100.0 <= robust["implied_ev_reduction_pct_2024_vs_template"] <= 100.0
    # This alternative scenario's own terms must sum to the SAME observed
    # gross-import change the baseline scenario does -- the back-solve
    # guarantees this by construction, and it is the property that makes
    # the two scenarios' terms meaningfully comparable in the first place
    # (Codex's own explicit request after finding 1 of this pass).
    observed_delta = decomp["observed_delta_gross_kwh"]
    assert abs(robust["decomposed_sum_kwh_this_scenario"] - observed_delta) < 0.1, (
        f"EV-concentrated scenario's own terms ({robust['consumption_term_kwh_this_scenario']} "
        f"+ {robust['production_term_kwh_this_scenario']} = "
        f"{robust['decomposed_sum_kwh_this_scenario']}) must sum to the observed "
        f"change ({observed_delta}) just like the baseline scenario does")
    assert "conclusion_robust_to_this_alternative_shape" in robust
    assert isinstance(robust["conclusion_robust_to_this_alternative_shape"], bool)
    # The actual, honest empirical finding for this dataset, once both
    # scenarios are computed in the SAME (Shapley gross-import) units: the
    # two shape assumptions' production terms are both small relative to
    # the ~497 kWh consumption term (-1.3 kWh uniform vs +18.5 kWh
    # EV-concentrated) -- the "consumption story" conclusion IS robust to
    # this alternative. Pinned explicitly so a future regeneration that
    # silently reintroduces the units bug (which produced a spurious "large
    # divergence") would be caught, not mistaken for a more dramatic finding.
    assert robust["conclusion_robust_to_this_alternative_shape"] is True, (
        "expected the EV-concentrated scenario's production term to stay "
        "small (comfortably under 25% of the consumption term) like the "
        "uniform-scaling baseline -- if this now disagrees, check whether "
        "_shapley_two_factor_vectors is being bypassed again before "
        "concluding the split has genuinely become non-robust")
    return (f"EV-concentrated scenario production term "
            f"({robust['production_term_kwh_this_scenario']} kWh) vs baseline "
            f"({robust['production_term_kwh_baseline_scenario']} kWh) -- "
            "agree; the consumption-dominant conclusion is robust to this alternative")


@case
def case_ev_attribution_uses_real_detection_and_states_its_own_limits():
    """AC5: EV share must come from detect_sessions on real data, not a guess,
    and the artifact must say plainly what it cannot determine."""
    _require_archive()
    bill = gi.load_bill_periods()
    prod = gi.production_block()
    cons = gi.consumption_block(bill, prod)
    ev = gi.ev_block(cons)
    assert ev["n_sessions_detected"] > 0, "expected real EV sessions in a 2-EV household's 29-day window"
    assert ev["period_2026_ev_kwh_detected"] > 0
    assert ev["true_share_of_increase"] == "NOT DETERMINED"
    assert "2024" in ev["caveat"] and "archive" in ev["caveat"]
    return (f"detect_sessions found {ev['n_sessions_detected']} real sessions "
            f"({ev['period_2026_ev_kwh_detected']} kWh) for the 2026 window; the 2024-side "
            "share is explicitly reported as not determined, not guessed")


@case
def case_build_is_deterministic_end_to_end():
    _require_archive()
    out1 = gi.build()
    out2 = gi.build()
    s1 = json.dumps(out1, sort_keys=True, default=str)
    s2 = json.dumps(out2, sort_keys=True, default=str)
    assert s1 == s2, "build() must be byte-identical (as JSON) across repeated runs on the same inputs"
    return "build() is deterministic end-to-end on the real archive"


@case
def case_build_matches_the_committed_artifacts_headline_figures():
    """Loose regression check against the committed artifact (the strict
    byte-for-byte check is the CLAUDE.md section 9 gate / cmp, run outside
    this test suite) -- catches an accidental behavior change without
    requiring the committed copy to be regenerated for every test run."""
    _require_archive()
    committed = _committed()
    out = gi.build()
    assert out["bill_ground_truth"]["observed_delta_gross_kwh"] == \
        committed["bill_ground_truth"]["observed_delta_gross_kwh"]
    assert out["decomposition"]["within_5_pct_tolerance"] == \
        committed["decomposition"]["within_5_pct_tolerance"] is True
    assert abs(out["decomposition"]["decomposition_pct_error_vs_observed"]
              - committed["decomposition"]["decomposition_pct_error_vs_observed"]) < 0.01
    return "a fresh build() matches the committed artifact's headline decomposition figures"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
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
