#!/usr/bin/env python3
"""Tests for marginal_capacity_value.py (issue #190).

Same pattern as test_threeway_production_validation.py: synthetic-frame unit
cases that need no private archive, plus real-archive cases gated with
SkipCase when the sandbox inputs are absent. The issue's central criterion,
that the value of one more kW is a BILL DELTA from an interval-level
counterfactual and not a rate times a quantity, gets a synthetic case that
shows the two disagree wherever a bucket changes sign, and a real-archive case
that regenerates the committed artifact twice and compares bytes.

Run from the repo root:  ./.venv/bin/python analysis/test_marginal_capacity_value.py
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import numpy as np       # noqa: E402
import pandas as pd      # noqa: E402
import rates as R        # noqa: E402
import marginal_capacity_value as M  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "private" / "verify"
ARTIFACT = ROOT / "data" / "marginal_capacity_value.json"
HOUSEHOLD = ROOT / "private" / "household.yaml"
# The committed inputs the generator reads beside the private ones.
COMMITTED_INPUTS = ("threeway_production_validation.csv", "enphase_daily_production.csv",
                    "nem3_grandfathering.json")
# Every analysis module the generator imports, directly or through
# behavior_rebuild/threeway_production_validation.
MODULES = ("marginal_capacity_value.py", "household.py", "rates.py",
           "behavior_rebuild.py", "threeway_production_validation.py")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def _require_archive():
    for fname in ("usage.csv", "samA.csv", "samB.csv"):
        if not (SANDBOX / fname).is_file():
            raise SkipCase(f"needs {SANDBOX}/{fname}, which this checkout does not have")
    if not HOUSEHOLD.is_file():
        raise SkipCase(f"needs {HOUSEHOLD}, which this checkout does not have")


# ---------------------------------------------------------------------------
# Synthetic frames: a few real calendar days at 15 minutes, TOU and season
# assigned by rates.py exactly as behavior_rebuild.load() assigns them.
# ---------------------------------------------------------------------------
def _frame(days, imp_fn, exp_fn):
    rows = []
    for day in days:
        for q in range(96):
            ts = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=15 * q)
            rows.append((ts, imp_fn(ts), exp_fn(ts)))
    d = pd.DataFrame(rows, columns=["dt", "Consumption", "Generation"])
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["seas"] = np.where(d.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    d["ym"] = d.dt.dt.to_period("M")
    d["p"] = [R.period_at(t) for t in d.dt]
    return d


def _pv_by_hour(d, kwh_by_hour):
    """A derived production dict over every (date, hour) the frame carries."""
    return {(ts.date(), ts.hour): kwh_by_hour.get(ts.hour, 0.0)
            for ts in d.dt.dt.floor("h").drop_duplicates()}


# ---------------------------------------------------------------------------
# (a) the value is a bill delta, not a rate times a quantity
# ---------------------------------------------------------------------------
@case
def case_the_value_is_the_bill_delta_and_no_single_rate_times_the_kwh_reproduces_it():
    """The defect issue #190 names: pricing added production as kWh x a
    rate. On a frame where the increment both displaces imports and pushes
    one bucket net-negative, the engine's delta equals the tariff
    decomposition (netted kWh at energy(), surplus kWh at credit(), NBC on
    the offset imports) and equals NO cell of the price map times the added
    kWh, at either the surplus or the netting treatment."""
    days = [dt.date(2026, 4, 6) + dt.timedelta(days=i) for i in range(2)]   # Mon, Tue
    # Daytime import is small (0.05 kWh a quarter against a 0.075 kWh
    # increment, so every daylight interval both offsets an import and
    # exports), the evening imports keep on-peak net-positive, and the
    # off-peak bucket (weekday 6-10, 14-16, 21-24) nets only +0.2 kWh a day
    # before the increment lands 0.9 kWh a day in it, so it flips.
    d = _frame(days,
               lambda ts: 0.05 if 9 <= ts.hour < 16 else (0.6 if 16 <= ts.hour < 21 else 0.0),
               lambda ts: 0.05 if 14 <= ts.hour < 16 else 0.0)
    pv = _pv_by_hour(d, {h: 3.0 for h in range(9, 17)})
    hsums = M.hourly_meter(d)
    deltas, clipped, at_ceiling = M.increment_per_interval(d, pv, hsums, 10.0, 1.0)
    assert clipped == 0.0 and at_ceiling == 0
    imp0 = list(d.Consumption)
    exp0 = list(d.Generation)
    imp1, exp1, offset, export = M.apply_increment(imp0, exp0, deltas)
    base, _ = M.bill(d, imp0, exp0)
    after, _ = M.bill(d, imp1, exp1)
    delta = base - after
    dec = M.decompose(d, imp0, exp0, imp1, exp1, offset)
    assert abs(dec["total_usd"] - delta) < 1e-6, (dec, delta)
    assert "2026-04:W:off" in dec["buckets_pushed_net_negative"], dec
    assert dec["surplus_kwh"] > 0 and dec["netted_kwh"] > 0 and sum(offset) > 0 and sum(export) > 0
    added = sum(deltas)
    for fn in (R.credit, R.energy, R.allin):
        for s in ("S", "W"):
            for p in ("on", "off", "sop"):
                assert abs(fn(s, p) * added - delta) > 0.01, (
                    f"rates.{fn.__name__}({s!r}, {p!r}) x {added:.2f} kWh reproduces the "
                    f"bill delta ${delta:.2f}; the counterfactual is not distinguishable "
                    "from the rate-times-quantity shortcut on this frame")
    return (f"bill delta ${delta:.2f} = decomposition (${dec['netted_value_usd']} netted + "
            f"${dec['surplus_value_usd']} surplus + ${dec['nbc_saved_on_offset_imports_usd']} "
            f"NBC), and no price-map cell x {added:.1f} kWh comes within a cent of it")


@case
def case_decomposition_covers_all_three_bucket_signs():
    """A bucket that stays net-positive nets everything at energy(); one
    that starts net-negative settles everything at credit(); one that flips
    splits at zero. Each is driven on its own frame and reconciled against
    the engine."""
    day = [dt.date(2026, 1, 12)]   # a winter Monday
    results = {}
    # 1. stays positive: heavy import all day
    d = _frame(day, lambda ts: 2.0, lambda ts: 0.0)
    pv = _pv_by_hour(d, {12: 4.0})
    hs = M.hourly_meter(d)
    deltas, _, _ = M.increment_per_interval(d, pv, hs, 10.0, 1.0)
    i1, e1, off, _ = M.apply_increment(list(d.Consumption), list(d.Generation), deltas)
    dec = M.decompose(d, list(d.Consumption), list(d.Generation), i1, e1, off)
    b0, _ = M.bill(d, list(d.Consumption), list(d.Generation))
    b1, _ = M.bill(d, i1, e1)
    assert abs(dec["total_usd"] - (b0 - b1)) < 1e-6
    assert dec["surplus_kwh"] == 0.0 and dec["buckets_pushed_net_negative"] == []
    assert abs(dec["netted_value_usd"] - 0.4 * R.energy("W", "sop")) < 0.01, dec
    results["stays_positive"] = dec["netted_kwh"]
    # 2. already negative: exporting all day, no import
    d = _frame(day, lambda ts: 0.0, lambda ts: 1.0)
    hs = M.hourly_meter(d)
    deltas, _, _ = M.increment_per_interval(d, pv, hs, 10.0, 1.0)
    i1, e1, off, _ = M.apply_increment(list(d.Consumption), list(d.Generation), deltas)
    dec = M.decompose(d, list(d.Consumption), list(d.Generation), i1, e1, off)
    b0, _ = M.bill(d, list(d.Consumption), list(d.Generation))
    b1, _ = M.bill(d, i1, e1)
    assert abs(dec["total_usd"] - (b0 - b1)) < 1e-6
    assert dec["netted_kwh"] == 0.0 and dec["buckets_pushed_net_negative"] == []
    assert abs(dec["surplus_value_usd"] - 0.4 * R.credit("W", "sop")) < 0.01, dec
    results["already_negative"] = dec["surplus_kwh"]
    # 3. flips: the midday bucket nets exactly 0.1 kWh before the 0.4 kWh increment
    d = _frame(day, lambda ts: 0.025 if ts.hour == 12 else 0.0, lambda ts: 0.0)
    hs = M.hourly_meter(d)
    deltas, _, _ = M.increment_per_interval(d, pv, hs, 10.0, 1.0)
    i1, e1, off, _ = M.apply_increment(list(d.Consumption), list(d.Generation), deltas)
    dec = M.decompose(d, list(d.Consumption), list(d.Generation), i1, e1, off)
    b0, _ = M.bill(d, list(d.Consumption), list(d.Generation))
    b1, _ = M.bill(d, i1, e1)
    assert abs(dec["total_usd"] - (b0 - b1)) < 1e-6
    assert dec["buckets_pushed_net_negative"] == ["2026-01:W:sop"], dec
    assert abs(dec["netted_kwh"] - 0.1) < 1e-6 and abs(dec["surplus_kwh"] - 0.3) < 1e-6, dec
    assert abs(dec["nbc_saved_on_offset_imports_usd"] - 0.1 * R.NBC) < 0.006, dec
    return f"three bucket signs reconcile against rates.bill_nem_monthly: {results}"


# ---------------------------------------------------------------------------
# (b) placement: import offset first, export after, energy conserved
# ---------------------------------------------------------------------------
@case
def case_the_increment_offsets_the_intervals_import_before_it_exports():
    imp1, exp1, offset, export = M.apply_increment([1.0, 0.1, 0.0], [0.0, 0.0, 0.5],
                                                   [0.3, 0.3, 0.3])
    assert imp1 == [0.7, 0.0, 0.0], imp1
    assert [round(v, 9) for v in exp1] == [0.0, 0.2, 0.8], exp1
    assert offset == [0.3, 0.1, 0.0], offset
    assert [round(v, 9) for v in export] == [0.0, 0.2, 0.3], export
    assert abs(sum(offset) + sum(export) - 0.9) < 1e-12
    return "1.0/0.1/0.0 kWh imports with +0.3 kWh each -> 0.7/0/0 imports, +0/0.2/0.3 exports"


@case
def case_each_hours_increment_is_spread_over_its_own_intervals_and_dst_days_carry_none():
    """The increment for a (date, hour) is pv * added / kw_dc, split across
    the quarter-hours the frame carries; a day with no derived production
    (the excluded DST Sundays) contributes zero everywhere."""
    days = [dt.date(2025, 11, 1), dt.date(2025, 11, 2)]   # the Saturday, then fall-back Sunday
    d = _frame(days, lambda ts: 1.0, lambda ts: 0.0)
    pv = {(days[0], 12): 4.0, (days[0], 13): 2.0}          # nothing on the DST day
    hs = M.hourly_meter(d)
    deltas, _, _ = M.increment_per_interval(d, pv, hs, 8.0, 1.0)
    by_key = {}
    for ts, x in zip(d.dt, deltas):
        by_key.setdefault((ts.date(), ts.hour), []).append(x)
    assert [round(v, 9) for v in by_key[(days[0], 12)]] == [0.125] * 4, by_key[(days[0], 12)]
    assert [round(v, 9) for v in by_key[(days[0], 13)]] == [0.0625] * 4
    assert all(v == 0.0 for k, v in zip(d.dt, deltas) if k.date() == days[1])
    assert abs(sum(deltas) - 0.75) < 1e-12
    return "4 kWh and 2 kWh hours at +1 kW on 8 kW -> 0.5 and 0.25 kWh over four quarters; DST day zero"


@case
def case_the_fixed_ceiling_clips_only_hours_whose_scaled_energy_would_exceed_it():
    day = [dt.date(2026, 5, 4)]
    d = _frame(day, lambda ts: 0.0, lambda ts: 0.0)
    pv = {(day[0], 11): 5.0, (day[0], 12): 9.0}
    hs = M.hourly_meter(d)
    free, c0, n0 = M.increment_per_interval(d, pv, hs, 10.0, 1.0)
    capped, c1, n1 = M.increment_per_interval(d, pv, hs, 10.0, 1.0, ceiling_kwh=9.45)
    assert (c0, n0) == (0.0, 0)
    assert n1 == 1 and abs(c1 - 0.45) < 1e-9, (c1, n1)
    assert abs(sum(free) - 1.4) < 1e-9 and abs(sum(capped) - 0.95) < 1e-9, (sum(free), sum(capped))
    return "9 kWh hour scaled to 9.9 is clipped to 9.45; the 5 kWh hour is untouched"


# ---------------------------------------------------------------------------
# (c) the payback ladder, the nameplate, the verdict check
# ---------------------------------------------------------------------------
@case
def case_the_payback_ladder_is_labelled_an_assumption_and_refuses_to_divide_by_nothing():
    rungs = M.payback_ladder(482.54, 1.0)
    assert [r["assumed_usd_per_w"] for r in rungs] == list(M.RETROFIT_USD_PER_W_LADDER)
    assert rungs[0]["cost_usd"] == 2000 and rungs[0]["simple_payback_years"] == 4.1, rungs[0]
    assert all(r["simple_payback_years"] is None for r in M.payback_ladder(0.0, 1.0))
    assert all(r["simple_payback_years"] is None for r in M.payback_ladder(-5.0, 1.0))
    assert all("assumed" in k for r in rungs for k in r if "usd_per_w" in k)
    return f"{len(rungs)} rungs keyed assumed_usd_per_w; zero or negative value -> null years"


@case
def case_a_missing_or_nonpositive_nameplate_stops_the_run():
    real = M.HH.get
    try:
        for bad in ({"solar.kw_dc": 0.0, "solar.kw_ac": 9.45},
                    {"solar.kw_dc": "ten", "solar.kw_ac": 9.45},
                    {"solar.kw_dc": 10.0, "solar.kw_ac": float("nan")}):
            M.HH.get = lambda key, required=True, bad=bad: bad[key]
            try:
                M.array_nameplate()
            except SystemExit as e:
                assert "household.yaml" in str(e), e
            else:
                raise AssertionError(f"array_nameplate accepted {bad}")

        def missing(key, required=True):
            raise SystemExit(f"missing key '{key}' in private/household.yaml")
        M.HH.get = missing
        try:
            M.array_nameplate()
        except SystemExit as e:
            assert "solar.kw_dc" in str(e), e
        else:
            raise AssertionError("array_nameplate ran without solar.kw_dc")
    finally:
        M.HH.get = real
    return "zero, non-numeric, nan and absent nameplates each raise SystemExit"


def _with_intake(values):
    """Point M.HH.get at a dict for one call; a key the dict lacks fails
    closed the way household.py does."""
    def get(key, required=True):
        if key not in values:
            raise SystemExit(f"missing key '{key}' in private/household.yaml")
        return values[key]
    return get


@case
def case_the_clipping_case_branches_on_the_intakes_inverter_architecture():
    """Intake owns the fact: one inverter per module scales the AC ceiling,
    anything else keeps today's. The rule reads the two counts, never the
    model string, and the published block carries its inputs."""
    real = M.HH.get
    try:
        outcomes = {}
        for count, modules, expect in ((30, 30, True), (1, 30, False), (2, 20, False),
                                       (1, 1, False), (20, 20, True)):
            M.HH.get = _with_intake({"solar.module_count": modules,
                                     "solar.inverter_count": count,
                                     "solar.inverter_model": "Any Model"})
            arch = M.inverter_architecture()
            assert arch["per_module_inverters"] is expect, (count, modules, arch)
            assert arch["module_count"] == modules and arch["inverter_count"] == count
            expected_case = "scaled_ceiling" if expect else "fixed_ceiling"
            assert M.primary_clipping_case(arch) == expected_case, (count, modules)
            outcomes[(count, modules)] = expected_case
        for bad in ({"solar.module_count": 30.5, "solar.inverter_count": 30,
                     "solar.inverter_model": "Any Model"},
                    {"solar.module_count": 30, "solar.inverter_count": 0,
                     "solar.inverter_model": "Any Model"},
                    {"solar.module_count": 30, "solar.inverter_count": True,
                     "solar.inverter_model": "Any Model"},
                    {"solar.module_count": 30, "solar.inverter_count": 30,
                     "solar.inverter_model": "  "},
                    {"solar.module_count": 30, "solar.inverter_count": 30}):
            M.HH.get = _with_intake(bad)
            try:
                M.inverter_architecture()
            except SystemExit as e:
                assert "household.yaml" in str(e), e
            else:
                raise AssertionError(f"inverter_architecture accepted {bad}")
    finally:
        M.HH.get = real
    return f"{outcomes}; a fractional, zero, boolean or absent count and a blank model each stop the run"


@case
def case_a_bill_that_rises_with_added_production_stops_the_run():
    """The engine is monotone, so the delta is at or above zero by
    construction; a negative one is a broken input, refused by name rather
    than published as a negative value."""
    assert M.bill_delta(100.0, 90.0) == 10.0
    assert M.bill_delta(100.0, 100.0) == 0.0
    try:
        M.bill_delta(100.0, 100.5)
    except SystemExit as e:
        assert "$0.50 MORE" in str(e), e
    else:
        raise AssertionError("bill_delta accepted a bill that rose with added production")
    return "a $0.50 rise is refused; a fall and a zero change pass through"


@case
def case_the_verdict_check_reads_the_cap_rule_and_the_committed_bracket():
    if not M.NEM3_JSON.is_file():
        raise SkipCase("needs data/nem3_grandfathering.json")
    vc = M.verdict_check(482.54, 10.05, 1.0)
    assert abs(vc["nem2_growth_cap_kw"] - 1.005) < 1e-9, vc
    assert vc["added_kw_within_cap"] is True
    assert M.verdict_check(482.54, 10.05, 1.2)["added_kw_within_cap"] is False
    assert M.verdict_check(482.54, 6.0, 1.0)["nem2_growth_cap_kw"] == 1.0
    lo = vc["grandfathering_at_risk_usd_yr"]["low"]
    assert abs(vc["grandfathering_over_added_kw_value"]["low"] - round(lo / 482.54, 1)) < 1e-9
    assert M.verdict_check(0.0, 10.05, 1.0)["grandfathering_over_added_kw_value"]["low"] is None
    return (f"cap {vc['nem2_growth_cap_kw']} kW; grandfathering is "
            f"{vc['grandfathering_over_added_kw_value']['low']}-"
            f"{vc['grandfathering_over_added_kw_value']['high']}x the added kW's value")


# ---------------------------------------------------------------------------
# (d) the committed artifact, read without the archive
# ---------------------------------------------------------------------------
@case
def case_the_committed_artifact_is_internally_consistent():
    doc = json.loads(ARTIFACT.read_text())
    per = doc["per_added_kw"]
    assert per["added_kw_dc"] == 1.0
    assert abs(per["import_offset_kwh"] + per["exported_kwh"] - per["added_production_kwh"]) < 0.15
    assert abs(per["import_offset_pct"] + per["exported_pct"] - 100.0) < 0.15
    assert abs(per["bill_before_usd"] - per["bill_after_usd"] - per["bill_delta_usd_yr"]) < 0.011
    assert abs(per["bill_delta_usd_yr"] / per["added_production_kwh"] * 100
               - per["value_per_added_kwh_cents"]) < 0.01
    assert abs(sum(per["monthly_delta_usd"].values()) - per["bill_delta_usd_yr"]) < 0.02
    dec = per["settlement_decomposition"]
    assert abs(dec["total_usd"] - per["bill_delta_usd_yr"]) < 0.011
    assert dec["reconciles_to_engine_delta_within_usd"] <= M.DECOMPOSITION_TOLERANCE_USD
    assert abs(dec["netted_kwh"] + dec["surplus_kwh"] - per["added_production_kwh"]) < 0.15
    assert abs(sum(c["value_usd"] for c in dec["by_period"].values()) - dec["total_usd"]) < 0.05
    w = doc["window"]
    assert w["days_with_increment"] == w["days"] - len(w["excluded_dst_days"])
    prof = doc["production_profile"]
    assert prof["hours_above_ac_nameplate"] == 0
    assert prof["max_hour_kwh"] <= doc["array"]["kw_ac"]
    assert prof["tie_out_threeway_meter_derived"]["max_abs_daily_diff_kwh"] <= M.THREEWAY_TIE_OUT_KWH
    assert prof["reconciliation_enphase_meter_record"]["excluded_dst_days_share_of_annual_pct"] < 2.0
    assert 0.95 < prof["reconciliation_enphase_meter_record"]["derived_over_meter_non_dst_ratio"] < 1.05
    clip = doc["clipping"]
    arr = doc["array"]
    assert clip["primary_case"] in ("scaled_ceiling", "fixed_ceiling"), clip["primary_case"]
    assert per["clipping_case"] == clip["primary_case"]
    # The branch is published with its inputs, and the inputs are the intake's.
    assert clip["rule_inputs"] == {"solar.inverter_count": arr["inverter_count"],
                                   "solar.module_count": arr["module_count"]}
    expect_per_module = arr["inverter_count"] > 1 and arr["inverter_count"] == arr["module_count"]
    assert clip["per_module_inverters"] is expect_per_module
    assert clip["primary_case"] == M.primary_clipping_case(clip)
    assert clip["rule"] and clip["resolution_caveat"]
    for name in ("scaled_ceiling", "fixed_ceiling"):
        assert clip[name]["assumption"], name
    # per_added_kw IS the primary case's figure, not a third computation.
    assert clip[clip["primary_case"]]["bill_delta_usd_yr"] == per["bill_delta_usd_yr"]
    assert clip[clip["primary_case"]]["added_production_kwh"] == per["added_production_kwh"]
    assert clip["scaled_ceiling"]["ceiling_kw_ac"] > arr["kw_ac"]
    assert clip["fixed_ceiling"]["ceiling_kw_ac"] == arr["kw_ac"]
    assert clip["fixed_ceiling"]["bill_delta_usd_yr"] <= clip["scaled_ceiling"]["bill_delta_usd_yr"]
    assert clip["fixed_ceiling"]["clipped_kwh"] >= clip["scaled_ceiling"]["clipped_kwh"]
    pb = doc["payback_sensitivity"]
    assert "ASSUMED" in pb["assumption"] and pb["annual_value_usd"] == per["bill_delta_usd_yr"]
    for r in pb["ladder"]:
        assert r["simple_payback_years"] == round(r["cost_usd"] / per["bill_delta_usd_yr"], 1)
    assert doc["notes"]["confidence"] == "modeled"
    assert doc["verdict_check"]["added_kw_within_cap"] is True
    for key in ("degradation", "export_crediting", "intra_hour_shape", "rates", "grandfathering"):
        assert doc["notes"][key], key
    return (f"${per['bill_delta_usd_yr']}/yr for +{per['added_production_kwh']} kWh "
            f"({per['import_offset_pct']}% offsets an import), decomposition and "
            "monthly deltas reconcile, every assumption stated")


# ---------------------------------------------------------------------------
# (e) the generator itself: fails closed without the archive, and reproduces
#     the committed bytes twice with it
# ---------------------------------------------------------------------------
def _stage_root(root, with_private):
    (root / "analysis").mkdir()
    (root / "data").mkdir()
    for name in MODULES:
        shutil.copy(ROOT / "analysis" / name, root / "analysis" / name)
    for name in COMMITTED_INPUTS:
        src = ROOT / "data" / name
        if src.is_file():
            shutil.copy(src, root / "data" / name)
    work = root / "private" / "verify"
    work.mkdir(parents=True)
    if with_private:
        shutil.copy(HOUSEHOLD, root / "private" / "household.yaml")
        for fname in ("usage.csv", "samA.csv", "samB.csv"):
            shutil.copy(SANDBOX / fname, work / fname)
    for name in MODULES:
        shutil.copy(ROOT / "analysis" / name, work / name)
    return work


def _run(work):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "marginal_capacity_value.py"], cwd=work,
                          capture_output=True, text=True, timeout=600, env=env)


@case
def case_the_generator_fails_closed_without_the_private_archive():
    """A checkout with analysis/ and data/ but no private/ must stop through
    household.py's intake message before reading any interval, and must
    write nothing."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        work = _stage_root(root, with_private=False)
        r = _run(work)
        assert r.returncode != 0, "the generator exited 0 with no private archive"
        err = (r.stderr or "") + (r.stdout or "")
        assert "household.yaml" in err and "intake" in err, err[-500:]
        assert not (root / "data" / "marginal_capacity_value.json").exists()
    return "no private/: SystemExit naming private/household.yaml and the intake interview, nothing written"


@case
def case_regeneration_is_byte_identical_twice_and_matches_the_committed_artifact():
    _require_archive()
    prefix = "mcv-test-"
    base = ROOT / "private"
    root = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    try:
        work = _stage_root(root, with_private=True)
        out = root / "data" / "marginal_capacity_value.json"
        digests = []
        for _ in range(2):
            r = _run(work)
            assert r.returncode == 0, (r.stderr or r.stdout)[-800:]
            digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
        assert digests[0] == digests[1], digests
        committed = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
        assert digests[0] == committed, (
            f"regenerated sha256 {digests[0][:12]} != committed {committed[:12]}; the "
            "committed artifact is stale or the generator is not reproducible")
    finally:
        # Only ever this case's own mkdtemp sandbox under private/, never the
        # fixtures beside it.
        assert root.parent == base and root.name.startswith(prefix), root
        shutil.rmtree(root)
    return f"two runs -> sha256 {digests[0][:12]}, identical to data/marginal_capacity_value.json"


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
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            suite_runner.report_case_failure(fn, exc)
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
