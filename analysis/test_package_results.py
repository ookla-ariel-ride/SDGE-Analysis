#!/usr/bin/env python3
"""Guard suite for package_results.py's upstream-artifact resolution (issue #29).

package_results.py computes nothing new -- every figure it writes is read out
of two upstream artifacts, data/behavior_rebuild.json and
data/battery_dispatch_policies.json (see its module docstring). Both are
written by their own generators into the WORKING DIRECTORY under the
documented private/verify sandbox flow, and data/ holds only the last
PROMOTED run, so package_results.py must prefer a current-run copy in the CWD
over the committed data/ copy -- the exact pattern PR #26 established for
carbon_fullyear.py's _scenario_a_saved and deep_analyses.py's _base_save,
generalized here to a whole-document read (see _resolve_artifact's own
docstring for why: this script cites many fields out of each artifact, not
one scalar).

These cases build tiny, fully synthetic behavior_rebuild.json /
battery_dispatch_policies.json fixtures (just the fields package_results.py
actually reads) and run the REAL script end to end via subprocess against a
throwaway repo-shaped root, covering the three resolution outcomes CLAUDE.md
9 and issue #29's acceptance criteria require: (1) no current-run copy ->
fall back to the committed copy, with a NOTICE; (2) both present and
DISAGREEING -> the current run wins, announced loudly; (3) a malformed
current-run copy -> fail closed, never silently falling back to the
committed copy.

SkipCase matches test_parse_bills.py's typed-exception convention (issue #44
AC4); there is no skip path in this file since the fixture is fully synthetic.
"""
import json
import pathlib
import subprocess
import sys

import suite_runner
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
PACKAGE_RESULTS = ANALYSIS / "package_results.py"


class SkipCase(Exception):
    pass


def _behavior_json(model_bill, a, b, c, d):
    return json.dumps({
        "baseline": {"model_bill": model_bill},
        "scenarios": {
            "a": {"saved": a}, "b": {"saved": b},
            "c": {"saved": c}, "d": {"saved": d}},
    })


def _dispatch_json(greedy_save, evening_save, mid_marginal, mid_combined,
                    mid_bill, high_combined, high_bill):
    return json.dumps({
        "pw3": {"greedy": {"save": greedy_save}, "evening": {"save": evening_save}},
        "post_behavior": {
            "mid": {"battery_marginal": mid_marginal, "combined_save": mid_combined,
                    "bill": mid_bill},
            "high": {"combined_save": high_combined, "bill": high_bill}},
    })


# One canonical fixture pair, reused (with deliberate variants) across cases
# so the expected package_results.json fields are hand-computable from these
# same numbers in every case.
BASE_BEHAVIOR = dict(model_bill=4904, a=1221, b=1009, c=1700, d=2179)
BASE_DISPATCH = dict(greedy_save=2328, evening_save=1720, mid_marginal=2238,
                     mid_combined=3459, mid_bill=1445, high_combined=3675,
                     high_bill=1229)


def _build_root(tmp):
    """A minimal repo-shaped root: package_results.py's _repo_root() only
    requires an 'analysis' and a 'data' subdirectory to exist (their contents
    are never inspected by that function)."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    return tmp


def _run(tmp):
    return subprocess.run([sys.executable, str(PACKAGE_RESULTS)], cwd=tmp,
                          capture_output=True, text=True, timeout=120)


def case_no_current_run_copy_falls_back_to_committed_with_a_notice():
    """Neither behavior_rebuild.json nor battery_dispatch_policies.json has a
    current-run copy in the CWD -- only the committed data/ copies exist, the
    documented "operator already promoted" case. package_results.py must
    still run, reading both committed copies, and must say so out loud."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        (tmp / "data" / "behavior_rebuild.json").write_text(
            _behavior_json(**BASE_BEHAVIOR))
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**BASE_DISPATCH))

        r = _run(tmp)
        assert r.returncode == 0, f"package_results.py failed: {r.stderr[-2000:]}"
        assert "NOTICE: no current-run behavior_rebuild.json" in r.stdout, r.stdout
        assert "NOTICE: no current-run battery_dispatch_policies.json" in r.stdout, r.stdout
        assert "reading the committed" in r.stdout, r.stdout

        out = json.loads((tmp / "data" / "package_results.json").read_text())
        assert out["model_baseline_current_rates"] == BASE_BEHAVIOR["model_bill"], out
        assert out["packages"]["LOW"]["savings_yr"] == BASE_BEHAVIOR["a"], out
        assert out["packages"]["MID"]["battery_alone_yr"] == BASE_DISPATCH["greedy_save"], out
    return ("package_results.py falls back to the committed copy of both "
            "upstream artifacts when no current-run copy exists, and prints "
            "a NOTICE naming each fallback")


def case_disagreeing_current_run_copy_wins_and_is_announced_loudly():
    """A current-run copy exists in the CWD and DISAGREES with the committed
    data/ copy (the reproduction-with-new-inputs case issue #29 is about).
    The current run must win -- its figures, not the stale committed ones --
    and the mismatch must be announced loudly, not silently resolved."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        # committed copies: the OLD, stale run
        (tmp / "data" / "behavior_rebuild.json").write_text(
            _behavior_json(**BASE_BEHAVIOR))
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**BASE_DISPATCH))
        # current-run copies: a NEW run with different household inputs
        new_behavior = dict(BASE_BEHAVIOR, model_bill=5200, a=1500)
        new_dispatch = dict(BASE_DISPATCH, greedy_save=2600, mid_combined=3800)
        (tmp / "behavior_rebuild.json").write_text(_behavior_json(**new_behavior))
        (tmp / "battery_dispatch_policies.json").write_text(_dispatch_json(**new_dispatch))

        r = _run(tmp)
        assert r.returncode == 0, f"package_results.py failed: {r.stderr[-2000:]}"
        assert r.stdout.count("STALE COMMITTED ARTIFACT") == 2, (
            "expected one loud disagreement notice per artifact", r.stdout)
        assert "this run's behavior_rebuild.json differs" in r.stdout, r.stdout
        assert "this run's battery_dispatch_policies.json differs" in r.stdout, r.stdout

        out = json.loads((tmp / "data" / "package_results.json").read_text())
        # the CURRENT RUN's figures win, not the stale committed ones
        assert out["model_baseline_current_rates"] == new_behavior["model_bill"], out
        assert out["packages"]["LOW"]["savings_yr"] == new_behavior["a"], out
        assert out["packages"]["MID"]["battery_alone_yr"] == new_dispatch["greedy_save"], out
    return ("package_results.py prefers a disagreeing current-run copy over "
            "the committed one for both artifacts, and announces each "
            "mismatch loudly rather than resolving it in silence")


def case_malformed_current_run_copy_fails_closed():
    """A current-run copy that exists but is not valid JSON must ABORT the
    run -- never silently fall back to the committed copy, which is exactly
    how a stale figure would get published under a citation that looks
    current."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        (tmp / "data" / "behavior_rebuild.json").write_text(
            _behavior_json(**BASE_BEHAVIOR))
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**BASE_DISPATCH))
        # a present-but-broken current-run copy, paired with a VALID current-run
        # copy of the other artifact so the cohort check (issue #29 review) does
        # not itself abort first -- this case targets the malformed-JSON path.
        (tmp / "behavior_rebuild.json").write_text("{not valid json")
        (tmp / "battery_dispatch_policies.json").write_text(_dispatch_json(**BASE_DISPATCH))

        r = _run(tmp)
        assert r.returncode != 0, "package_results.py did not fail on a malformed artifact"
        assert "cannot parse artifact" in r.stderr, r.stderr
        assert "will not fall back past a broken artifact" in r.stderr, r.stderr
        assert not (tmp / "data" / "package_results.json").exists(), (
            "package_results.json was written despite the fail-closed abort")
    return ("package_results.py fails closed on a malformed current-run "
            "copy instead of silently falling back to the committed one")


def case_mixed_source_cohort_fails_closed():
    """A current-run copy of ONE upstream artifact exists but not the other --
    composing it with the OTHER's committed (possibly stale, possibly from an
    unrelated earlier session) copy would silently blend two different runs
    into one package figure while still exiting 0. Must fail closed naming
    which artifact is missing, for BOTH directions (behavior-only,
    dispatch-only)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        (tmp / "data" / "behavior_rebuild.json").write_text(
            _behavior_json(**BASE_BEHAVIOR))
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**BASE_DISPATCH))
        # only a current-run behavior_rebuild.json -- no current-run dispatch copy
        (tmp / "behavior_rebuild.json").write_text(
            _behavior_json(**dict(BASE_BEHAVIOR, model_bill=5200)))

        r = _run(tmp)
        assert r.returncode != 0, "package_results.py accepted a mixed-source cohort"
        assert "mixed-source upstream artifacts" in r.stderr, r.stderr
        assert "battery_dispatch_policies.json does not" in r.stderr, r.stderr
        assert not (tmp / "data" / "package_results.json").exists(), (
            "package_results.json was written despite the mixed-source abort")

    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        (tmp / "data" / "behavior_rebuild.json").write_text(
            _behavior_json(**BASE_BEHAVIOR))
        (tmp / "data" / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**BASE_DISPATCH))
        # only a current-run dispatch copy -- no current-run behavior copy
        (tmp / "battery_dispatch_policies.json").write_text(
            _dispatch_json(**dict(BASE_DISPATCH, greedy_save=2600)))

        r = _run(tmp)
        assert r.returncode != 0, "package_results.py accepted a mixed-source cohort"
        assert "mixed-source upstream artifacts" in r.stderr, r.stderr
        assert "behavior_rebuild.json does not" in r.stderr, r.stderr
        assert not (tmp / "data" / "package_results.json").exists(), (
            "package_results.json was written despite the mixed-source abort")
    return ("package_results.py refuses a mixed-source cohort (a current-run "
            "copy of only ONE upstream artifact) in both directions, naming "
            "the missing artifact, rather than silently blending a current "
            "run with the other artifact's committed copy")


def case_neither_copy_exists_fails_closed_with_a_clear_message():
    """Neither a current-run nor a committed copy exists at all -- the
    ordering contract was simply not followed (upstream generator never
    ran). This must name which artifact is missing and how to fix it, not
    crash with a bare traceback."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_root(pathlib.Path(td))
        r = _run(tmp)
        assert r.returncode != 0, "package_results.py did not fail with no artifacts at all"
        assert "no behavior_rebuild.json artifact" in r.stderr, r.stderr
        assert "behavior_rebuild.py" in r.stderr, r.stderr
    return ("package_results.py fails closed with a clear message naming "
            "the missing artifact and its generator when neither copy exists")


CASES = [
    case_no_current_run_copy_falls_back_to_committed_with_a_notice,
    case_disagreeing_current_run_copy_wins_and_is_announced_loudly,
    case_malformed_current_run_copy_fails_closed,
    case_mixed_source_cohort_fails_closed,
    case_neither_copy_exists_fails_closed_with_a_clear_message,
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
