#!/usr/bin/env python3
"""Guard suite for battery_plan_matrix.py -- run END TO END on a synthetic house.

battery_plan_matrix.py sat in NEEDS_PRIVATE_ARCHIVE with a claim that it
"genuinely cannot" be covered synthetically, because its fail-closed tie-outs
compare against data/plan_results.csv and data/battery_dispatch_policies.json,
both built from the real year -- so a synthetic run would have to diverge and
trip them. A clean-room review (issue #44 follow-up) disproved this with a
working demonstration: an INDEPENDENTLY computed reference (the exact same
published-rate-table formula the generator itself uses, transcribed here, not
imported from it) can be promoted into a throwaway data/ as the tie-out
target, satisfying both tie-outs for real rather than neutering them, while
still catching a defect planted in the generator's own bill_plan().

Design: builds the already-proven synthetic Green Button fixture
(test_scripts_runnable.SYNTH_HOUSEHOLD / _build_throwaway_root), computes the
three plans' no-battery bills and the EV-TOU-5 battery value with an
independent transcription of bill_plan() (same formula, same constants --
extracted from the generator's own source, not hand-copied, so a future rate
change cannot silently desync this test from the generator), writes those as
the tie-out artifacts the generator reads, then runs the REAL generator and
checks its output matches the independent computation.

SkipCase matches test_parse_bills.py's typed-exception convention (issue #44
AC4); there is no skip path in this file since the fixture is fully synthetic.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402
import test_scripts_runnable as TSR   # the proven synthetic-fixture machinery
import numpy as np


class SkipCase(Exception):
    pass


def _generator_constants():
    """EXTRACT battery_plan_matrix.py's own published-rate-table constants
    (WFNBC_DWR/PCIA/NBC/BSC/UDC/CEA_GEN/PLANS) directly out of its source, by
    executing the exact lines that declare them -- not hand-copied, so a
    future rate-table update cannot silently desync this test's reference
    computation from what the generator actually does."""
    src = (ANALYSIS / "battery_plan_matrix.py").read_text()
    span = src[src.index("WFNBC_DWR = 0.00591"):src.index("\n\ndef repo_root")]
    ns = {}
    exec(span, ns)
    return (ns["WFNBC_DWR"], ns["PCIA"], ns["NBC"], ns["BSC"],
            ns["UDC"], ns["CEA_GEN"], ns["PLANS"])


(_WFNBC_DWR, _PCIA, _NBC, _BSC, _UDC, _CEA_GEN, _PLANS) = _generator_constants()


def _ref_bill(plan, seas, per, imp, exp):
    """Independent transcription of battery_plan_matrix.py's bill_plan(): the
    published-rate-table interval method (analyze_norelief.py's method), on
    the SAME constants extracted above."""
    rate = np.array([_UDC[plan][s][p] + _WFNBC_DWR + _PCIA + _CEA_GEN[s][p]
                     for s, p in zip(seas, per)])
    return float((imp * rate).sum() - (exp * np.maximum(rate - _NBC, 0)).sum()) + _BSC * 365


_LOAD_PROBE = """
import json, sys
sys.path.insert(0, {tmp!r})
import behavior_rebuild as br, rates as R
d = br.load().copy()
d["p"] = [R.period_at(t) for t in d.dt]
print(json.dumps({{"seas": list(d.seas.values), "per": list(d.p.values),
                   "imp": [float(x) for x in d.Consumption.values],
                   "exp": [float(x) for x in d.Generation.values]}}))
"""

_DISPATCH_PROBE = """
import json, sys
sys.path.insert(0, {tmp!r})
import behavior_rebuild as br, rates as R
from battery_dispatch_policies import run_batt, CHARGE_KW
d = br.load().copy()
d["p"] = [R.period_at(t) for t in d.dt]
imp_b, exp_b, served, thru = run_batt(d, d.Consumption.values.astype(float),
                                      d.Generation.values.astype(float), 13.5,
                                      "greedy", charge_kw=CHARGE_KW)
print(json.dumps({{"imp_b": [float(x) for x in imp_b], "exp_b": [float(x) for x in exp_b]}}))
"""

_SHIFT_PROBE = """
import json, sys
sys.path.insert(0, {tmp!r})
import behavior_rebuild as br, rates as R
from battery_dispatch_policies import run_batt, free_fix_shift, CHARGE_KW
d = br.load().copy()
d["p"] = [R.period_at(t) for t in d.dt]
ev, sessions = br.detect_sessions(d)
imp0 = d.Consumption.values.astype(float)
imp_sh, moved, scenario = free_fix_shift(d, imp0)
imp_p, exp_p, _, _ = run_batt(d, imp_sh, d.Generation.values.astype(float), 13.5,
                              "greedy", charge_kw=CHARGE_KW)
print(json.dumps({{"imp_p": [float(x) for x in imp_p],
                   "exp_p": [float(x) for x in exp_p],
                   "moved": float(moved), "n_sessions": len(sessions),
                   "scenario": scenario}}))
"""


def _noev_household_yaml():
    """test_scripts_runnable.SYNTH_HOUSEHOLD with household.has_ev set false and
    the charger block removed -- behavior_rebuild.py refuses a declared charger
    alongside a false flag, so both edits are needed for a household that is
    genuinely EV-free rather than merely quiet.

    Every edit asserts it took. A string-surgery fixture that silently matched
    nothing would leave the EV household in place and make the no-EV cases below
    pass for the wrong reason (see the forcing-fixtures lesson: a helper that
    FORCES a precondition must prove it forced it)."""
    hh = TSR.SYNTH_HOUSEHOLD
    assert "charger:\n  kw: 11.5\n" in hh, "SYNTH_HOUSEHOLD no longer declares a charger"
    assert "household:\n  pto_date: 2019-12-01\n" in hh, \
        "SYNTH_HOUSEHOLD's household block no longer has the shape this edit expects"
    hh = hh.replace("household:\n  pto_date: 2019-12-01\n",
                    "household:\n  pto_date: 2019-12-01\n  has_ev: false\n")
    hh = hh.replace("charger:\n  kw: 11.5\n", "")
    assert "has_ev: false" in hh and "charger:" not in hh, hh
    return hh


def _noev_root(tmp):
    """A throwaway repo root whose household genuinely has no EV, with
    behavior_rebuild.py already run in it (its scenarios.c is the independent
    cross-check target for what the free fix should move)."""
    TSR._build_throwaway_root(tmp, synthetic=True)
    (tmp / "private" / "household.yaml").write_text(_noev_household_yaml())
    r = subprocess.run([sys.executable, "behavior_rebuild.py"], cwd=tmp,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"behavior_rebuild.py failed: {r.stderr[-2000:]}"
    br_json = json.loads((tmp / "behavior_rebuild.json").read_text())
    assert br_json["scenarios"]["a"].get("not_applicable") is True, (
        "the no-EV fixture did not actually produce an EV-free household: "
        f"scenarios.a is {br_json['scenarios']['a']!r}")
    return br_json


def _independent_ref_and_dispatch(tmp):
    """Steps shared by every case below: an INDEPENDENTLY computed no-battery
    reference per plan (promoted into throwaway data/plan_results.csv, the
    generator's own no-battery tie-out target) and the matching EV-TOU-5
    with-battery figure from the same dispatch trace -- everything the
    canonical-crosscheck tie-out target needs, before a case decides WHERE to
    place the battery_dispatch_policies.json artifact (issue #29: current-run
    copy, committed copy, both, or a broken one)."""
    r1 = subprocess.run([sys.executable, "-c", _LOAD_PROBE.format(tmp=str(tmp))],
                        cwd=tmp, capture_output=True, text=True, timeout=300)
    assert r1.returncode == 0, f"load probe failed: {r1.stderr[-2000:]}"
    f = json.loads(r1.stdout)
    seas, per = np.array(f["seas"]), np.array(f["per"])
    imp0, gen0 = np.array(f["imp"]), np.array(f["exp"])
    ref = {p: _ref_bill(p, seas, per, imp0, gen0) for p in _PLANS}

    (tmp / "data" / "plan_results.csv").write_text(
        "provider,plan,total\n" +
        "".join(f"CEA,{p},{v:.6f}\n" for p, v in ref.items()))

    r2 = subprocess.run([sys.executable, "-c", _DISPATCH_PROBE.format(tmp=str(tmp))],
                        cwd=tmp, capture_output=True, text=True, timeout=300)
    assert r2.returncode == 0, f"dispatch probe failed: {r2.stderr[-2000:]}"
    b = json.loads(r2.stdout)
    imp_b, exp_b = np.array(b["imp_b"]), np.array(b["exp_b"])
    with_b5 = _ref_bill("EV-TOU-5", seas, per, imp_b, exp_b)
    exp_battery_value = round(ref["EV-TOU-5"] - with_b5)

    # Step 1b (issue #200): the mid PACKAGE reference — the same integrated
    # shift-then-battery series the generator itself builds (this household's
    # own free fix via battery_dispatch_policies.free_fix_shift, then the
    # 13.5 kWh greedy dispatch), billed with the SAME independent transcription
    # per plan. The generator's new
    # mid-package crosscheck also demands a post_behavior.mid block in the
    # dispatch artifact, so every case's tie-out fixture needs these figures.
    r3 = subprocess.run([sys.executable, "-c", _SHIFT_PROBE.format(tmp=str(tmp))],
                        cwd=tmp, capture_output=True, text=True, timeout=300)
    assert r3.returncode == 0, f"shift probe failed: {r3.stderr[-2000:]}"
    s = json.loads(r3.stdout)
    imp_p, exp_p = np.array(s["imp_p"]), np.array(s["exp_p"])
    pkg_ref = {"moved": s["moved"], "n_sessions": s["n_sessions"],
               "scenario": s["scenario"], "plans": {}}
    for p in _PLANS:
        pb = _ref_bill(p, seas, per, imp_p, exp_p)
        pkg_ref["plans"][p] = {"package_bill": pb,
                               "package_save": round(ref[p] - pb)}
    return ref, with_b5, exp_battery_value, pkg_ref


_OMIT = object()
"""Sentinel for _dispatch_fixture(free_fix_scenario=...): leave the key OUT of
the artifact entirely, which is what a dispatch artifact written before the
field existed looks like -- a different shape from a key holding the wrong
value, and battery_plan_matrix.py has to refuse both."""


def _dispatch_fixture(ref, exp_battery_value, pkg_ref, offset=0,
                      free_fix_scenario=None):
    """The dispatch-artifact tie-out fixture every case promotes: the
    independently computed battery value AND the post_behavior.mid block the
    generator's mid-package crosscheck reads. offset shifts every figure to
    build a deliberately stale/divergent copy.

    post_behavior.free_fix_scenario is what the artifact says about WHICH
    household it belongs to (issue #147): "a" (the EV charge reschedule) on an
    EV household, "c" (the flexible house-load shift) on one with no EV. It
    defaults to the scenario THIS root's own free_fix_shift() selected, so
    every fixture is that household's by construction; a case that wants a
    different household's artifact passes the other letter (or _OMIT for an
    artifact that names none)."""
    ev5 = pkg_ref["plans"]["EV-TOU-5"]
    if free_fix_scenario is None:
        free_fix_scenario = pkg_ref["scenario"]
    post = {"mid": {"combined_save": ev5["package_save"] + offset,
                    "bill": round(ev5["package_bill"]) + offset}}
    if free_fix_scenario is not _OMIT:
        post = dict({"free_fix_scenario": free_fix_scenario}, **post)
    return json.dumps({
        "pw3": {"greedy": {"save": exp_battery_value + offset}},
        "baseline_bill_current_rates": round(ref["EV-TOU-5"]) + offset,
        "post_behavior": post,
    })


def case_battery_plan_matrix_end_to_end_on_a_synthetic_house():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)

        # Step 2: the canonical-crosscheck tie-out target, from the SAME
        # independently-computed dispatch trace and formula -- this proves
        # battery_plan_matrix.py's OWN tie-out logic and bill_plan()
        # arithmetic (which the mutation test below targets), not
        # battery_dispatch_policies.py's canonical bill_nem engine, which is
        # already covered separately in CI_RUNNABLE. Placed ONLY in the
        # committed data/ directory, with no current-run copy in the CWD --
        # issue #29's fallback path (no current-run copy -> the committed
        # copy is used, with a NOTICE) -- since this case never runs
        # battery_dispatch_policies.py itself.
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref))

        r3 = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                            capture_output=True, text=True, timeout=600)
        assert r3.returncode == 0, f"battery_plan_matrix.py failed: {r3.stderr[-2000:]}"
        assert "NOTICE: no current-run battery_dispatch_policies.json" in r3.stdout, r3.stdout
        assert "canonical crosscheck read from the committed" in r3.stdout, r3.stdout
        out = json.loads((tmp / "data" / "battery_plan_matrix.json").read_text())

    for plan in _PLANS:
        got = out["plans"][plan]
        assert abs(got["no_battery"] - round(ref[plan])) <= 1, (plan, got, ref[plan])
    got5 = out["plans"]["EV-TOU-5"]
    assert abs(got5["with_battery"] - round(with_b5)) <= 1, (got5, with_b5)
    assert abs(got5["battery_value"] - exp_battery_value) <= 2, (
        got5, exp_battery_value)
    cx = out["canonical_crosscheck_ev_tou_5"]
    assert cx["battery_value"] == exp_battery_value, cx
    # issue #177: the totals the report RANKS are carried unrounded, as
    # integer cents beside each whole-dollar display cell, and each cents
    # field is the independent reference to the cent. The differenced cells
    # (battery_value, package_save) are the exact integer differences of the
    # cents they are taken from, so a consumer cannot be a cent out from
    # subtracting them itself.
    pkg = out["mid_package_on_plans"]["plans"]
    for plan in _PLANS:
        got = out["plans"][plan]
        assert abs(got["no_battery_cents"] - ref[plan] * 100) <= 1.0, (
            plan, got, ref[plan])
        for column in ("no_battery", "with_battery", "battery_value"):
            assert isinstance(got[f"{column}_cents"], int), (plan, column, got)
            assert abs(got[f"{column}_cents"] / 100 - got[column]) <= 0.5 + 1e-9, (
                plan, column, got)
        assert got["battery_value_cents"] == got["no_battery_cents"] - got["with_battery_cents"], (
            plan, got)
        exp_pkg = pkg_ref["plans"][plan]["package_bill"]
        assert abs(pkg[plan]["package_bill_cents"] - exp_pkg * 100) <= 1.0, (
            plan, pkg[plan], exp_pkg)
        assert pkg[plan]["package_save_cents"] == got["no_battery_cents"] - pkg[plan]["package_bill_cents"], (
            plan, pkg[plan], got)
    assert abs(got5["with_battery_cents"] - with_b5 * 100) <= 1.0, (got5, with_b5)
    assert json.dumps(out), "battery_plan_matrix.json is not JSON-serializable"
    return ("battery_plan_matrix.py runs end to end on a synthetic house with "
            "both fail-closed tie-outs satisfied by an independently computed "
            "reference (not neutered), its no-battery/with-battery/"
            "battery-value figures for all 3 plans match that reference (whole "
            "dollars for display and integer cents for ranking, issue #177), and "
            "it falls back to the committed dispatch artifact (with a "
            "NOTICE) when no current-run copy exists")


def case_disagreeing_current_run_dispatch_artifact_wins_and_is_announced():
    """issue #29: a current-run battery_dispatch_policies.json in the CWD
    that DISAGREES with the committed data/ copy must win (this run's
    figures, not the stale committed ones) and the mismatch must be
    announced loudly, not resolved in silence."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)

        # committed copy: a stale, DIFFERENT run
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref, offset=500))
        # current-run copy: the correct figures for THIS run, matching what
        # the generator will itself compute, so the crosscheck assertion
        # passes using the (correct) current-run copy
        (tmp / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref))

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"battery_plan_matrix.py failed: {r.stderr[-2000:]}"
        assert "STALE COMMITTED ARTIFACT" in r.stdout, r.stdout
        assert "this run's battery_dispatch_policies.json differs" in r.stdout, r.stdout
        out = json.loads((tmp / "data" / "battery_plan_matrix.json").read_text())

    cx = out["canonical_crosscheck_ev_tou_5"]
    assert cx["battery_value"] == exp_battery_value, (
        "canonical crosscheck used the stale committed value instead of "
        "this run's", cx)
    assert cx["no_battery"] == round(ref["EV-TOU-5"]), cx
    return ("battery_plan_matrix.py prefers a disagreeing current-run copy "
            "of battery_dispatch_policies.json over the committed one, and "
            "announces the mismatch loudly rather than resolving it in "
            "silence")


def case_malformed_current_run_dispatch_artifact_fails_closed():
    """A current-run battery_dispatch_policies.json that exists but is not
    valid JSON must ABORT the run, never silently fall back to the committed
    copy -- that is exactly how a stale figure would get published under a
    citation that looks current."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)

        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref))
        (tmp / "battery_dispatch_policies.json").write_text("{not valid json")

        # _build_throwaway_root already staged the REAL repo's committed
        # battery_plan_matrix.json into tmp/data/ -- remove it so a clean
        # absence, not an unchanged pre-existing file, is what proves nothing
        # was written by this (expected-to-fail) run.
        (tmp / "data" / "battery_plan_matrix.json").unlink(missing_ok=True)

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode != 0, "battery_plan_matrix.py did not fail on a malformed artifact"
        assert "cannot parse the dispatch artifact" in r.stderr, r.stderr
        assert "will not fall back past a broken artifact" in r.stderr, r.stderr
        assert not (tmp / "data" / "battery_plan_matrix.json").exists(), (
            "battery_plan_matrix.json was written despite the fail-closed abort")
    return ("battery_plan_matrix.py fails closed on a malformed current-run "
            "copy of battery_dispatch_policies.json instead of silently "
            "falling back to the committed one")


def case_mid_package_on_plans_on_a_synthetic_house():
    """issue #200: the mid package (EV shift scenario a, then the 13.5 kWh
    battery) must be priced on EVERY plan by re-billing the one integrated
    shift-then-dispatch year end-to-end under each plan's own table rates —
    never by summing deltas. Verified against an independent transcription of
    the same pipeline (probes emit the shifted+dispatched series; _ref_bill
    bills it), on the synthetic house, which is guaranteed to have EV
    sessions (asserted, so this case can never silently degenerate into the
    no-EV path)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)
        assert pkg_ref["n_sessions"] > 0, (
            "synthetic fixture detected no EV sessions -- this case would be "
            "vacuous (the package row would just equal the battery row)")
        assert pkg_ref["moved"] > 0, pkg_ref["moved"]

        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref))

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"battery_plan_matrix.py failed: {r.stderr[-2000:]}"
        out = json.loads((tmp / "data" / "battery_plan_matrix.json").read_text())

    mp = out["mid_package_on_plans"]
    assert mp["kwh_moved"] == round(pkg_ref["moved"]), (
        mp["kwh_moved"], pkg_ref["moved"])
    # Positive control for the no-EV case below (issue #147): a household that
    # HAS an EV must still get scenario a, and the arithmetic must still be the
    # EV shift's own kWh, not the house shift's.
    assert pkg_ref["scenario"] == "a", pkg_ref["scenario"]
    assert mp["free_fix_scenario"] == "a", mp
    assert "EV shift scenario a" in mp["method"], mp["method"]
    for plan in _PLANS:
        got, exp = mp["plans"][plan], pkg_ref["plans"][plan]
        assert abs(got["package_bill"] - round(exp["package_bill"])) <= 1, (
            plan, got, exp)
        assert abs(got["package_save"] - exp["package_save"]) <= 2, (
            plan, got, exp)
        # internal consistency: save is the delta against the SAME plan's
        # no-package bill, one pipeline, one rate basis
        assert abs((out["plans"][plan]["no_battery"] - got["package_bill"])
                   - got["package_save"]) <= 2, (plan, out["plans"][plan], got)
    cx = mp["canonical_crosscheck_ev_tou_5"]
    ev5 = pkg_ref["plans"]["EV-TOU-5"]
    assert cx["combined_save"] == ev5["package_save"], cx
    assert cx["bill"] == round(ev5["package_bill"]), cx
    return ("mid_package_on_plans prices the integrated EV-shift+battery "
            "package on all 3 plans by re-billing the one shifted+dispatched "
            "year end-to-end per plan, matching an independent transcription "
            "within $1-2, with kwh_moved recorded and the canonical "
            "post_behavior.mid crosscheck block equal to the tie-out target")


def case_mid_package_crosscheck_fails_closed_on_divergent_dispatch_artifact():
    """Mutation-grade negative for the NEW crosscheck: a dispatch artifact
    whose post_behavior.mid.combined_save disagrees with the table-rate
    package save beyond the $100 tolerance must ABORT the run (naming the
    mid-package crosscheck) and write nothing -- proving the guard actually
    fires on the defect it claims to catch. The battery-value crosscheck's
    target is kept CORRECT so this failure can only come from the new
    mid-package assertion."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)

        fx = json.loads(_dispatch_fixture(ref, exp_battery_value, pkg_ref))
        fx["post_behavior"]["mid"]["combined_save"] += 500   # beyond the $100 tolerance
        (tmp / "data" / "battery_dispatch_policies.json").write_text(json.dumps(fx))

        (tmp / "data" / "battery_plan_matrix.json").unlink(missing_ok=True)

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode != 0, (
            "battery_plan_matrix.py did not fail on a divergent "
            "post_behavior.mid crosscheck target")
        assert "mid package save diverged from the canonical dispatch" in r.stderr, r.stderr
        assert not (tmp / "data" / "battery_plan_matrix.json").exists(), (
            "battery_plan_matrix.json was written despite the failed "
            "mid-package crosscheck")
    return ("the mid-package canonical crosscheck fails closed: a dispatch "
            "artifact whose post_behavior.mid.combined_save is $500 off "
            "aborts the run naming the mid-package crosscheck, and no "
            "artifact is written")


def case_mid_package_uses_the_house_shift_when_the_household_has_no_ev():
    """issue #147: on a household whose intake says household.has_ev is false,
    the mid-package row must be behavior scenario c (the flexible house-load
    shift) THEN the battery -- the same pipeline package_results.py's MID uses
    for that household.

    The generator used to call behavior_rebuild.shift_ev() unconditionally.
    That moves nothing here, so kwh_moved was 0 and the "package" row was the
    battery-only row wearing a package label, while packages.LOW/MID for the
    same household were scenario c: two pipelines under one name, which
    CLAUDE.md section 9 forbids.

    Two independent things are checked, deliberately: the LABEL
    (free_fix_scenario == "c" and a method sentence naming scenario c) and the
    ARITHMETIC (kwh_moved equals behavior_rebuild.json's OWN
    scenarios.c.house_kwh_moved, computed by a different script from a
    different entry point, and the per-plan bills equal an independent
    transcription of the shifted-then-dispatched year). A label without the
    arithmetic would pass on a generator that stamps "c" on an unshifted
    year."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        br_json = _noev_root(tmp)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)
        assert pkg_ref["scenario"] == "c", (
            "free_fix_shift did not select scenario c on the no-EV fixture", pkg_ref)
        assert pkg_ref["moved"] > 0, (
            "the no-EV free fix moved nothing, so this case could not tell a "
            "working shift from the unconditional-shift_ev defect", pkg_ref)

        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref))

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"battery_plan_matrix.py failed: {r.stderr[-2000:]}"
        out = json.loads((tmp / "data" / "battery_plan_matrix.json").read_text())

    scen_c = br_json["scenarios"]["c"]
    mp = out["mid_package_on_plans"]
    assert mp["free_fix_scenario"] == "c", mp
    assert "scenario c" in mp["method"], mp["method"]
    assert "EV shift scenario a" not in mp["method"], mp["method"]
    # the arithmetic: what the generator moved is scenario c's own house shift
    assert mp["kwh_moved"] == round(scen_c["house_kwh_moved"]), (
        "mid-package kwh_moved does not equal behavior_rebuild scenarios.c's "
        "own house_kwh_moved", mp["kwh_moved"], scen_c["house_kwh_moved"])
    assert mp["kwh_moved"] > 0, (
        "mid-package kwh_moved is zero on a no-EV household -- the free fix "
        "did not run", mp)
    for plan in _PLANS:
        got, exp = mp["plans"][plan], pkg_ref["plans"][plan]
        assert abs(got["package_bill"] - round(exp["package_bill"])) <= 1, (
            plan, got, exp)
        assert abs(got["package_save"] - exp["package_save"]) <= 2, (
            plan, got, exp)
        # and the package must be worth strictly MORE than the battery alone:
        # a degenerate (unshifted) package row would equal the battery row
        assert got["package_save"] > out["plans"][plan]["battery_value"], (
            f"{plan}: the package saves no more than the battery alone, so the "
            "free fix contributed nothing", got, out["plans"][plan])
    return ("on a household with household.has_ev false, "
            "mid_package_on_plans runs behavior scenario c (the house-load "
            f"shift, {mp['kwh_moved']} kWh — behavior_rebuild's own "
            "scenarios.c.house_kwh_moved) before the battery, records "
            "free_fix_scenario \"c\", names scenario c in its method, and "
            "beats the battery-alone row on every plan")


def case_dispatch_artifact_from_the_other_household_is_refused():
    """issue #147: the dispatch artifact battery_plan_matrix.py crosschecks
    against must belong to THIS household.

    _resolve_dispatch_artifact() falls back to the committed
    data/battery_dispatch_policies.json when no current-run copy exists, and
    nothing checked that the resolved copy came from a household with the same
    EV applicability. A household with no EV then published
    canonical_crosscheck_ev_tou_5 out of the committed EV household's baseline
    and battery value, beside its own scenario-c mid-package row -- one
    artifact composed from two households.

    The two $100 crosscheck assertions are NOT this guard. They compare
    MAGNITUDES, so they only fire when the two households' numbers happen to
    be far apart; the fixtures here are deliberately built so both tolerances
    are SATISFIED, and the run must still refuse. Without that, this case
    would pass against a generator with no applicability check at all.

    Both directions, and both resolution paths -- guarding only the committed
    fallback would leave the identical defect for a stale current-run copy
    another household's run left in this working directory, and that copy WINS
    the resolution.
    """
    # (label, build the root, the letter THIS household's own run selects,
    #  the letter the other household's artifact carries)
    households = [("no-EV household handed the EV household's dispatch artifact",
                   False, "c", "a"),
                  ("EV household handed the no-EV household's dispatch artifact",
                   True, "a", "c")]
    for label, has_ev, mine, theirs in households:
        for where in ("committed", "current-run"):
            with tempfile.TemporaryDirectory() as td:
                tmp = pathlib.Path(td)
                if has_ev:
                    TSR._build_throwaway_root(tmp, synthetic=True)
                else:
                    _noev_root(tmp)
                ref, with_b5, exp_battery_value, pkg_ref = \
                    _independent_ref_and_dispatch(tmp)
                assert pkg_ref["scenario"] == mine, (label, pkg_ref["scenario"])

                # every FIGURE is this run's own, so both $100 tolerances pass;
                # only the free_fix_scenario says another household
                fixture = _dispatch_fixture(ref, exp_battery_value, pkg_ref,
                                            free_fix_scenario=theirs)
                (tmp / "data" / "battery_dispatch_policies.json").write_text(fixture)
                if where == "current-run":
                    (tmp / "battery_dispatch_policies.json").write_text(fixture)
                (tmp / "data" / "battery_plan_matrix.json").unlink(missing_ok=True)

                r = subprocess.run([sys.executable, "battery_plan_matrix.py"],
                                   cwd=tmp, capture_output=True, text=True,
                                   timeout=600)
                ctx = f"{where}/{label}"
                assert r.returncode != 0, (
                    f"{ctx}: battery_plan_matrix.py crosschecked this "
                    f"household against another household's dispatch "
                    f"artifact:\n{r.stdout[-2000:]}")
                assert "EV APPLICABILITY MISMATCH" in r.stderr, (ctx, r.stderr)
                # the message must name the intake FLAG, the artifact, what it
                # says, and the remedy
                assert "household.has_ev" in r.stderr, (ctx, r.stderr)
                assert "battery_dispatch_policies.json" in r.stderr, (ctx, r.stderr)
                assert f"free_fix_scenario {theirs!r}" in r.stderr, (ctx, r.stderr)
                assert "battery_dispatch_policies.py" in r.stderr, (ctx, r.stderr)
                # the two magnitude tolerances must NOT be what fired
                assert "diverged from the canonical" not in r.stderr, (
                    f"{ctx}: the $100 crosscheck fired instead of the "
                    "applicability guard, so this case proves nothing about "
                    f"applicability: {r.stderr}")
                assert not (tmp / "data" / "battery_plan_matrix.json").exists(), (
                    f"{ctx}: battery_plan_matrix.json was written despite the "
                    "applicability abort")

    # An artifact that names NO free fix cannot be checked against this
    # household at all, so it is refused too rather than trusted.
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value, pkg_ref = _independent_ref_and_dispatch(tmp)
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_fixture(ref, exp_battery_value, pkg_ref,
                              free_fix_scenario=_OMIT))
        (tmp / "data" / "battery_plan_matrix.json").unlink(missing_ok=True)

        r = subprocess.run([sys.executable, "battery_plan_matrix.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode != 0, (
            "battery_plan_matrix.py accepted a dispatch artifact that names no "
            f"free fix at all:\n{r.stdout[-2000:]}")
        assert "no usable post_behavior.free_fix_scenario" in r.stderr, r.stderr
        assert "household.has_ev" in r.stderr, r.stderr
        assert not (tmp / "data" / "battery_plan_matrix.json").exists(), (
            "battery_plan_matrix.json was written despite the abort")

    # POSITIVE CONTROL: a MATCHING household still runs. Without it a generator
    # that refused every artifact would pass everything above. (The EV/committed
    # and no-EV/committed combinations are the two end-to-end cases above; this
    # covers the current-run path for both households.)
    for has_ev, mine in ((True, "a"), (False, "c")):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            if has_ev:
                TSR._build_throwaway_root(tmp, synthetic=True)
            else:
                _noev_root(tmp)
            ref, with_b5, exp_battery_value, pkg_ref = \
                _independent_ref_and_dispatch(tmp)
            fixture = _dispatch_fixture(ref, exp_battery_value, pkg_ref)
            (tmp / "data" / "battery_dispatch_policies.json").write_text(fixture)
            (tmp / "battery_dispatch_policies.json").write_text(fixture)

            r = subprocess.run([sys.executable, "battery_plan_matrix.py"],
                               cwd=tmp, capture_output=True, text=True, timeout=600)
            assert r.returncode == 0, (
                f"has_ev={has_ev}: battery_plan_matrix.py refused a dispatch "
                f"artifact from its OWN household: {r.stderr[-2000:]}")
            out = json.loads((tmp / "data" / "battery_plan_matrix.json").read_text())
            assert out["mid_package_on_plans"]["free_fix_scenario"] == mine, out
    return ("battery_plan_matrix.py refuses a dispatch artifact whose "
            "post_behavior.free_fix_scenario disagrees with this run's "
            "household.has_ev -- both directions, on the current-run copy as "
            "well as the committed fallback, with every figure inside the $100 "
            "crosscheck tolerances so only the applicability guard can fire -- "
            "refuses one that names no free fix at all, writes nothing in "
            "either case, and still runs for a household whose artifact "
            "matches it")


CASES = [
    case_battery_plan_matrix_end_to_end_on_a_synthetic_house,
    case_disagreeing_current_run_dispatch_artifact_wins_and_is_announced,
    case_malformed_current_run_dispatch_artifact_fails_closed,
    case_mid_package_on_plans_on_a_synthetic_house,
    case_mid_package_crosscheck_fails_closed_on_divergent_dispatch_artifact,
    case_mid_package_uses_the_house_shift_when_the_household_has_no_ev,
    case_dispatch_artifact_from_the_other_household_is_refused,
]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
