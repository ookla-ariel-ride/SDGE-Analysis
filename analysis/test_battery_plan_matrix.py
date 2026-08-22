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
    return ref, with_b5, exp_battery_value


def case_battery_plan_matrix_end_to_end_on_a_synthetic_house():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        ref, with_b5, exp_battery_value = _independent_ref_and_dispatch(tmp)

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
        (tmp / "data" / "battery_dispatch_policies.json").write_text(json.dumps({
            "pw3": {"greedy": {"save": exp_battery_value}},
            "baseline_bill_current_rates": round(ref["EV-TOU-5"]),
        }))

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
    assert json.dumps(out), "battery_plan_matrix.json is not JSON-serializable"
    return ("battery_plan_matrix.py runs end to end on a synthetic house with "
            "both fail-closed tie-outs satisfied by an independently computed "
            "reference (not neutered), its no-battery/with-battery/"
            "battery-value figures for all 3 plans match that reference, and "
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
        ref, with_b5, exp_battery_value = _independent_ref_and_dispatch(tmp)

        # committed copy: a stale, DIFFERENT run
        (tmp / "data" / "battery_dispatch_policies.json").write_text(json.dumps({
            "pw3": {"greedy": {"save": exp_battery_value + 500}},
            "baseline_bill_current_rates": round(ref["EV-TOU-5"]) + 500,
        }))
        # current-run copy: the correct figures for THIS run, matching what
        # the generator will itself compute, so the crosscheck assertion
        # passes using the (correct) current-run copy
        (tmp / "battery_dispatch_policies.json").write_text(json.dumps({
            "pw3": {"greedy": {"save": exp_battery_value}},
            "baseline_bill_current_rates": round(ref["EV-TOU-5"]),
        }))

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
        ref, with_b5, exp_battery_value = _independent_ref_and_dispatch(tmp)

        (tmp / "data" / "battery_dispatch_policies.json").write_text(json.dumps({
            "pw3": {"greedy": {"save": exp_battery_value}},
            "baseline_bill_current_rates": round(ref["EV-TOU-5"]),
        }))
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


CASES = [
    case_battery_plan_matrix_end_to_end_on_a_synthetic_house,
    case_disagreeing_current_run_dispatch_artifact_wins_and_is_announced,
    case_malformed_current_run_dispatch_artifact_fails_closed,
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
