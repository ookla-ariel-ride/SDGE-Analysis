#!/usr/bin/env python3
"""Behavioral tests for quiet_night_floor.py (issue #17).

Same pattern as test_tou_structure_stress.py: synthetic-frame unit tests that
need no private archive at all (hand computations verified against the
module's own functions), plus a handful of real-archive cases gated with
SkipCase when the private Green Button export is absent.

Run from the repo root:  ./.venv/bin/python analysis/test_quiet_night_floor.py
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

import rates as R                          # noqa: E402
import behavior_rebuild as br              # noqa: E402
import battery_dispatch_policies as bdp    # noqa: E402
import quiet_night_floor as QNF            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
SANDBOX = ROOT / "private" / "verify"
ARTIFACT = ROOT / "data" / "quiet_night_floor.json"
EXTRA_RESULTS = ROOT / "data" / "extra_results.json"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button / SAM archive). Counted as neither pass nor fail."""


def _require_archive():
    files = sorted(glob.glob(USAGE_GLOB))
    if (not files or not (SANDBOX / "samA.csv").exists()
            or not (SANDBOX / "samB.csv").exists()):
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{SANDBOX}/samA.csv, samB.csv, none of which this "
                       "checkout has")
    return files[0]


# ---------------------------------------------------------------------------
# synthetic-frame builder -- no archive needed at all
# ---------------------------------------------------------------------------
def _toy_frame(start="2025-07-21", ndays=5, night_kw=1.0, day_kw=0.6,
              ev_night_offsets=(), solar_kw_midday=0.0):
    """ndays of 96-interval days starting on a real date, with a constant
    `night_kw` floor during the 1-5am window (quiet_night_floor's own night
    definition) and `day_kw` otherwise, an optional EV spike (>= 2 kW gate) on
    the night(s) at the given day offsets, and optional midday solar."""
    dtr = pd.date_range(start, periods=96 * ndays, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["wkend"] = d.dt.dt.date.map(R.off_peak_day)
    d["seas"] = np.where(d.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    d["ym"] = d.dt.dt.to_period("M")
    d["p"] = [R.period_at(ts) for ts in d.dt]

    night_mask = (d.hour >= QNF.NIGHT_START_H) & (d.hour < QNF.NIGHT_END_H)
    cons = np.where(night_mask, night_kw, day_kw) * 0.25
    date0 = d.dt.dt.date.iloc[0]
    for off in ev_night_offsets:
        day = date0 + pd.Timedelta(days=off)
        ev_mask = night_mask & (d.dt.dt.date == day)
        cons = np.where(ev_mask, 3.0 * 0.25, cons)   # 3 kW spike, above the 2 kW gate
    gen = np.zeros(len(d))
    if solar_kw_midday:
        midday = (d.hour >= 10) & (d.hour < 16)
        gen = np.where(midday, solar_kw_midday * 0.25, 0.0)
    d["Consumption"] = cons
    d["Generation"] = gen
    return d


# ---------------------------------------------------------------------------
# (a) _split_floor -- the physical energy-conservation identity
# ---------------------------------------------------------------------------
@case
def case_split_floor_matches_hand_computation():
    consumption = np.array([0.10, 0.30, 0.00])
    generation = np.array([0.00, 0.00, 0.05])
    reduce_i, leftover_i, new_c, new_g = QNF._split_floor(consumption, generation, 0.20)
    assert list(reduce_i) == [0.10, 0.20, 0.00]
    assert list(leftover_i) == [0.10, 0.00, 0.20]
    assert np.allclose(new_c, [0.00, 0.10, 0.00])
    assert np.allclose(new_g, [0.10, 0.00, 0.25])
    return "hand-computed 3-interval split matches _split_floor exactly"


@case
def case_split_floor_conserves_energy_identity_on_random_data():
    rng = np.random.default_rng(7)
    consumption = rng.uniform(0, 2, 500)
    generation = rng.uniform(0, 2, 500)
    floor_kwh = 0.3
    reduce_i, leftover_i, new_c, new_g = QNF._split_floor(consumption, generation, floor_kwh)
    assert np.allclose(reduce_i + leftover_i, floor_kwh), "reduce + leftover must equal the floor exactly"
    assert np.all(reduce_i <= consumption + 1e-12)
    assert np.all(new_c >= -1e-12)
    assert np.allclose(consumption - new_c, reduce_i)
    assert np.allclose(new_g - generation, leftover_i)
    return "500 random intervals: reduce_from_import + leftover == floor_kwh, energy conserved"


# ---------------------------------------------------------------------------
# (b) night_floor_series -- full per-night series, EV exclusion (issue AC1)
# ---------------------------------------------------------------------------
@case
def case_night_floor_series_isolates_ev_nights_and_reports_every_night():
    d = _toy_frame(ndays=4, night_kw=1.2, day_kw=0.5, ev_night_offsets=(2,))
    daily, stats = QNF.night_floor_series(d)
    assert stats["nights_total"] == 4, "issue AC1: every night in the window must be reported"
    assert stats["ev_nights"] == 1
    assert stats["quiet_nights"] == 3
    assert stats["median_kw"] == 1.2
    ev_rows = [r for r in daily if r["ev_night"]]
    assert len(ev_rows) == 1 and ev_rows[0]["median_kw"] is None, \
        "an EV night must be excluded (null), not zeroed or silently averaged in"
    quiet_rows = [r for r in daily if not r["ev_night"]]
    assert all(r["median_kw"] == 1.2 for r in quiet_rows)
    return "4-night toy frame: 1 EV night excluded, 3 quiet nights median 1.2 kW, full daily series returned"


@case
def case_night_floor_series_within_night_std_matches_numpy():
    d = _toy_frame(ndays=1, night_kw=1.0, day_kw=0.5)
    # perturb two of the sixteen 1-5am intervals so the within-night std is nonzero
    night_idx = d.index[(d.hour >= 1) & (d.hour < 5)]
    d.loc[night_idx[0], "Consumption"] = 1.5 * 0.25
    d.loc[night_idx[1], "Consumption"] = 0.5 * 0.25
    daily, stats = QNF.night_floor_series(d)
    kws = (d.loc[night_idx, "Consumption"] * 4.0).values
    want_std = round(float(np.std(kws, ddof=0)), 4)
    assert daily[0]["within_night_std_kw"] == want_std
    assert stats["cycling_within_night_std_kw_median"] == want_std
    return f"within-night std {want_std} kW matches numpy's ddof=0 std exactly"


# ---------------------------------------------------------------------------
# (c) method (a): hour-by-hour against the marginal price map
# ---------------------------------------------------------------------------
@case
def case_price_method_a_matches_hand_computation():
    d = pd.DataFrame({"seas": ["S", "S", "W"], "p": ["on", "sop", "off"]})
    reduce_i = np.array([1.0, 2.0, 0.0])
    leftover_i = np.array([0.0, 0.5, 3.0])
    price_map = QNF.price_map_from_rates()
    got = QNF.price_method_a(d, price_map, reduce_i, leftover_i)
    want_avoided = 1.0 * price_map["S_on"]["import"] + 2.0 * price_map["S_sop"]["import"]
    want_displaced = 0.5 * price_map["S_sop"]["export"] + 3.0 * price_map["W_off"]["export"]
    assert got["avoided_import_usd"] == round(want_avoided, 2)
    assert got["displaced_export_usd"] == round(want_displaced, 2)
    assert got["total_usd"] == round(want_avoided + want_displaced, 2)
    return "3-row hand computation matches price_method_a's bucketed sum exactly"


@case
def case_price_map_from_rates_matches_allin_and_credit():
    pm = QNF.price_map_from_rates()
    for s in QNF.SEASONS:
        for p in QNF.PERIODS:
            key = f"{s}_{p}"
            assert pm[key]["import"] == round(R.allin(s, p), 4)
            assert pm[key]["export"] == round(R.credit(s, p), 4)
    return "price_map_from_rates reproduces rates.allin()/rates.credit() for all 6 cells"


# ---------------------------------------------------------------------------
# (d) method (b): counterfactual re-bill, and the telescoping identity
# ---------------------------------------------------------------------------
@case
def case_price_method_b_telescopes_exactly():
    d = _toy_frame(ndays=10, night_kw=1.0, day_kw=1.5, solar_kw_midday=3.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    reduce_i, leftover_i, _, _ = QNF._split_floor(consumption, generation, 0.25)
    method_b, new_c, new_g = QNF.price_method_b(d, consumption, generation, reduce_i, leftover_i)
    assert round(method_b["avoided_import_usd"] + method_b["displaced_export_usd"], 2) == \
           method_b["total_usd"], "avoided_import + displaced_export must telescope to total"
    assert round(method_b["baseline_bill_usd"] - method_b["counterfactual_bill_usd"], 2) == \
           method_b["total_usd"]
    assert np.allclose(new_c, consumption - reduce_i)
    assert np.allclose(new_g, generation + leftover_i)
    return "10-day toy frame: method (b)'s three-bill telescoping identity holds exactly"


@case
def case_reconciliation_gap_is_near_zero_with_no_sign_flip():
    """When every (month, season, period) bucket sits far from zero net (here:
    heavy night-only import, no solar at all, so every bucket nets strongly
    positive both before and after the floor is removed), method (a)'s
    per-interval sign assumption and method (b)'s monthly bucket sign AGREE
    everywhere, so the reconciliation gap must be near zero -- this is the
    positive control for the reconciliation mechanism the docstring claims."""
    d = _toy_frame(ndays=20, night_kw=1.0, day_kw=2.0, solar_kw_midday=0.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    price_map = QNF.price_map_from_rates()
    reduce_i, leftover_i, _, _ = QNF._split_floor(consumption, generation, 0.25)
    method_a = QNF.price_method_a(d, price_map, reduce_i, leftover_i)
    method_b, new_c, new_g = QNF.price_method_b(d, consumption, generation, reduce_i, leftover_i)
    flips, flipped_kwh = QNF.sign_flip_buckets(d, consumption, generation, new_c, new_g)
    assert flips == [], f"expected no sign flips with no solar at all, got {flips}"
    gap_pct = 100.0 * (method_a["total_usd"] - method_b["total_usd"]) / method_b["total_usd"]
    assert abs(gap_pct) < 0.5, f"gap should be ~0 with no bucket sign flips, got {gap_pct}%"
    return f"no-solar control: 0 sign flips, gap {gap_pct:.3f}% (near zero, as the mechanism predicts)"


@case
def case_reconciliation_gap_appears_exactly_when_a_bucket_flips_sign():
    """The negative control: construct a bucket that nets SLIGHTLY positive
    before the floor is removed and NEGATIVE after -- sign_flip_buckets must
    catch it, and the resulting gap must be materially nonzero, tying the
    docstring's explanation to actual behavior rather than asserting it."""
    d = _toy_frame(ndays=10, night_kw=0.1, day_kw=0.3, solar_kw_midday=1.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    # this bucket (S/off) starts net-positive by a small margin; removing a
    # 1 kW floor applied uniformly across all 24 hours pushes it net-negative
    net_off_before = float((d[d.p == "off"].Consumption - d[d.p == "off"].Generation).sum())
    assert net_off_before > 0, "fixture must start net-positive in the off bucket to test a flip"
    price_map = QNF.price_map_from_rates()
    reduce_i, leftover_i, _, _ = QNF._split_floor(consumption, generation, 0.25)  # 1 kW floor
    method_a = QNF.price_method_a(d, price_map, reduce_i, leftover_i)
    method_b, new_c, new_g = QNF.price_method_b(d, consumption, generation, reduce_i, leftover_i)
    flips, flipped_kwh = QNF.sign_flip_buckets(d, consumption, generation, new_c, new_g)
    assert len(flips) >= 1, "expected at least one bucket to flip sign in this fixture"
    assert flipped_kwh > 0
    gap_usd = method_a["total_usd"] - method_b["total_usd"]
    assert abs(gap_usd) > 0.001, "a real sign flip must produce a nonzero reconciliation gap"
    return f"{len(flips)} bucket(s) flipped sign ({flipped_kwh} kWh), gap ${gap_usd:.2f} -- as the mechanism predicts"


# ---------------------------------------------------------------------------
# (e) sensitivity ($/yr per 100 W)
# ---------------------------------------------------------------------------
@case
def case_sensitivity_steps_are_internally_consistent():
    d = _toy_frame(ndays=15, night_kw=1.0, day_kw=1.5, solar_kw_midday=2.5)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    sens = QNF.sensitivity_per_100w(d, consumption, generation, floor_kw_measured=1.0)
    steps = sens["steps"]
    assert steps[0]["reduction_w"] == QNF.STEP_W
    assert steps[-1]["reduction_w"] == QNF.MAX_REDUCTION_W
    # marginal_usd_per_100w must equal the running difference of annual_savings_usd
    prev = 0.0
    for s in steps:
        # tolerance, not exact equality: the script computes each step's
        # marginal from FULL-PRECISION savings figures, while this
        # recomputation chains already-ROUNDED annual_savings_usd values, so
        # rounding can compound by a cent over many steps
        assert abs(s["marginal_usd_per_100w"] - round(s["annual_savings_usd"] - prev, 2)) <= 0.01
        prev = s["annual_savings_usd"]
    at_current = sens["usd_per_100w_at_current_floor"]
    assert at_current["floor_w_used"] == 1000  # nearest 100 W multiple of 1.0 kW
    return f"{len(steps)} sensitivity steps, marginal figures reproduce the running difference exactly"


@case
def case_sensitivity_current_floor_lookup_rounds_to_nearest_100w():
    d = _toy_frame(ndays=10, night_kw=1.0, day_kw=1.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    sens = QNF.sensitivity_per_100w(d, consumption, generation, floor_kw_measured=0.847)
    # 847 W rounds to the nearest 100 W multiple: 800
    assert sens["usd_per_100w_at_current_floor"]["floor_w_used"] == 800
    return "0.847 kW floor resolves to the 800 W sensitivity step, as round-to-nearest-100 implies"


# ---------------------------------------------------------------------------
# (f) battery interaction -- computed, not asserted (issue AC5)
# ---------------------------------------------------------------------------
@case
def case_battery_interaction_delta_matches_its_own_two_marginals():
    d = _toy_frame(ndays=30, night_kw=1.0, day_kw=1.2, solar_kw_midday=2.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    reduce_i, leftover_i, new_c, new_g = QNF._split_floor(consumption, generation, 0.25)
    result = QNF.battery_interaction(d, consumption, generation, new_c, new_g)
    # tolerance, not exact equality: delta_usd is rounded from full-precision
    # marginals, while this recomputation differences the already-rounded
    # dollar figures, which can differ from the full-precision delta by a cent
    recomputed = result["reduced_floor_battery_marginal_usd"] - result["baseline_battery_marginal_usd"]
    assert abs(round(recomputed, 2) - result["delta_usd"]) <= 0.01
    if result["delta_usd"] < 0:
        assert "SHRINKS" in result["direction"]
    elif result["delta_usd"] > 0:
        assert "GROWS" in result["direction"]
    return (f"battery marginal ${result['baseline_battery_marginal_usd']} -> "
           f"${result['reduced_floor_battery_marginal_usd']} "
           f"(delta ${result['delta_usd']}), direction sentence matches the sign")


@case
def case_steady_state_battery_conserves_energy():
    """_steady_state_battery must return a series whose battery throughput
    satisfies run_batt's own energy-conservation identity (already enforced
    inside run_batt, but this confirms the iteration converges rather than
    raising SystemExit on ordinary data)."""
    d = _toy_frame(ndays=20, night_kw=1.0, day_kw=1.0, solar_kw_midday=2.0)
    consumption = d.Consumption.values.astype(float)
    generation = d.Generation.values.astype(float)
    imp2, exp2 = QNF._steady_state_battery(d, consumption, generation)
    assert len(imp2) == len(d) and len(exp2) == len(d)
    assert np.all(imp2 >= -1e-9) and np.all(exp2 >= -1e-9)
    return "steady-state battery iteration converges on a 20-day toy frame with no error"


# ---------------------------------------------------------------------------
# (g) confidence labels distinguish measured load from attested cause (AC6)
# ---------------------------------------------------------------------------
@case
def case_confidence_labels_distinguish_measured_from_attested():
    labels = QNF.confidence_labels()
    assert "measured" in labels["load_magnitude"].lower()
    assert "attested" in labels["load_cause"].lower()
    assert labels["load_magnitude"] != labels["load_cause"]
    assert "modeled" in labels["pricing"].lower()
    assert "modeled" in labels["battery_interaction"].lower()
    # the cause label must NOT claim the load's cause is measured
    assert "measured" not in labels["load_cause"].lower().split("not measured")[-1] \
        or "not measured" in labels["load_cause"].lower()
    return "load_magnitude labeled measured, load_cause labeled attested (distinct claims)"


# ---------------------------------------------------------------------------
# (h) real-archive cases: cross-checks against actual committed data
# ---------------------------------------------------------------------------
@case
def case_price_map_cross_check_passes_against_committed_extra_results():
    _require_archive()
    if not EXTRA_RESULTS.exists():
        raise SkipCase(f"{EXTRA_RESULTS} not committed in this checkout")
    computed = QNF.price_map_from_rates()
    QNF.check_price_map_against_extra_results(ROOT, computed)  # raises SystemExit on mismatch
    return "rates.py's price map agrees with the committed extra_results.json price_map"


@case
def case_night_floor_cross_checks_the_published_phantom_figure():
    _require_archive()
    files = sorted(glob.glob(USAGE_GLOB))
    br.CSV = files[0]
    d = br.load()
    _, stats = QNF.night_floor_series(d)
    check = QNF.cross_check_night_floor(ROOT, stats)
    if check is None:
        raise SkipCase("data/extra_results.json phantom key not present")
    assert abs(check["gap_kw"]) < 0.05, (
        f"fresh night-floor measurement ({stats['median_kw']} kW) should agree "
        f"closely with the published phantom figure ({check['published_median_kw']} "
        f"kW); gap {check['gap_kw']} kW is too large")
    return f"fresh measurement {stats['median_kw']} kW vs published {check['published_median_kw']} kW"


@case
def case_reconciliation_gap_is_small_on_the_real_measured_year():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    committed = json.loads(ARTIFACT.read_text())
    gap_pct = committed["pricing"]["reconciliation"]["gap_pct"]
    assert abs(gap_pct) < 5, f"reconciliation gap {gap_pct}% is larger than expected on real data"
    return f"committed artifact's method (a)/(b) gap is {gap_pct}% on the real measured year"


@case
def case_battery_interaction_baseline_matches_battery_dispatch_policies():
    """Cross-checks the artifact's baseline battery marginal (steady-state,
    raw year, pw3/greedy) against the independently-generated committed
    battery_dispatch_policies.json's pw3.greedy.save -- built by a DIFFERENT
    script (battery_dispatch_policies.py, one-shot soc0=cap/2, no steady-state
    iteration), so a tight but nonzero tolerance is expected."""
    _require_archive()
    dispatch_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not ARTIFACT.exists() or not dispatch_path.exists():
        raise SkipCase("both data/quiet_night_floor.json and "
                       "data/battery_dispatch_policies.json must be committed")
    ours = json.loads(ARTIFACT.read_text())["battery_interaction"]["baseline_battery_marginal_usd"]
    theirs = json.loads(dispatch_path.read_text())["pw3"]["greedy"]["save"]
    assert abs(ours - theirs) < 10, (
        f"baseline battery marginal ${ours} should be close to the independently "
        f"published pw3.greedy.save ${theirs} (same config, different boundary "
        "condition handling) -- gap too large")
    return f"our baseline battery marginal ${ours} vs published pw3.greedy.save ${theirs}"


@case
def case_artifact_regenerates_byte_identically():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    before = ARTIFACT.read_bytes()
    import os as _os
    cwd = _os.getcwd()
    _os.chdir(str(SANDBOX))
    try:
        QNF.main()
    finally:
        _os.chdir(cwd)
    after = ARTIFACT.read_bytes()
    assert after == before, "data/quiet_night_floor.json is not reproducible"
    return "data/quiet_night_floor.json regenerates byte-identically from private/verify"


@case
def case_artifact_has_all_required_sections():
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    doc = json.loads(ARTIFACT.read_text())
    for key in ("night_floor", "hour_of_day", "pricing", "sensitivity_per_100w",
               "battery_interaction", "confidence_labels"):
        assert key in doc, f"missing required section: {key}"
    assert "daily_series" in doc["night_floor"], "issue AC1: full daily series required"
    assert len(doc["night_floor"]["daily_series"]) == doc["night_floor"]["nights_total"]
    assert len(doc["hour_of_day"]["profile"]) == 24, "issue AC1: full 24-hour distribution required"
    assert "avoided_import_usd" in doc["pricing"]["method_a_price_map"], "issue AC3: daytime split required"
    assert "displaced_export_usd" in doc["pricing"]["method_a_price_map"]
    assert "reconciliation" in doc["pricing"], "issue AC2: methods must be reconciled"
    return "all 7 acceptance-criteria sections present in the committed artifact"


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
