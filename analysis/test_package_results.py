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

A second group of cases covers the no-EV branch (issue #147): a household whose
intake says household.has_ev is false gets scenarios a and b as explicit
not-applicable STUBS, so the LOW package is read out of scenario c instead, and
the new free_fix_scenario field must name whichever scenario actually fed
savings_yr -- report_tokens._free_fix_saving cross-checks the two artifacts
through that field, so a wrong name silently checks the wrong number.

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


# The explicit not-applicable STUB behavior_rebuild.py publishes for an EV-only
# section when the intake flag household.has_ev is false (issue #147). Copied
# from a genuinely generated no-EV behavior_rebuild.json rather than invented:
# same two keys, same marker value True, same reason wording, and -- the part
# that matters to package_results.py -- NO "saved" key at all.
NA_REASON = ("household.has_ev is false (intake applicability flag, "
             "DATA-SOURCES-CHEATSHEET.md) — the EV-only shift scenario does "
             "not apply to this household; set the flag true and complete the "
             "intake (charger.kw) to compute it")


def _behavior_json_no_ev(model_bill, c, d):
    """behavior_rebuild.json as the generator writes it for a household whose
    intake says household.has_ev is false: scenarios a and b are explicit
    not-applicable stubs carrying no figure, while c and d (pure house-load
    shifts, which exist for every household) hold real savings."""
    stub = {"not_applicable": True, "reason": NA_REASON}
    return json.dumps({
        "baseline": {"model_bill": model_bill},
        "scenarios": {"a": dict(stub), "b": dict(stub),
                      "c": {"saved": c}, "d": {"saved": d}},
    })


def _behavior_json_scenario_a(model_bill, a_node, b, c, d):
    """Like _behavior_json but with scenarios.a set to an ARBITRARY node, so a
    case can hand the script a malformed a (not a stub) and check that it still
    fails loudly."""
    return json.dumps({
        "baseline": {"model_bill": model_bill},
        "scenarios": {"a": a_node, "b": {"saved": b},
                      "c": {"saved": c}, "d": {"saved": d}},
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


BASE_NOEV_BEHAVIOR = dict(model_bill=3173.79, c=428.83, d=857.66)
"""The baseline/scenario figures a genuinely generated no-EV run produced.
Deliberately un-rounded: round(428.83) = 429, so a case that asserts
savings_yr == 429 fails against truncation as well as against the wrong
scenario."""


def _run(tmp):
    return subprocess.run([sys.executable, str(PACKAGE_RESULTS)], cwd=tmp,
                          capture_output=True, text=True, timeout=120)


def _committed_only_root(td, behavior_text, dispatch_text=None):
    """A repo-shaped root holding ONLY the committed copy of both upstream
    artifacts -- the "operator already promoted" resolution path, which the
    cases below use so artifact RESOLUTION (covered by its own cases above)
    stays out of the way of what they are actually about."""
    tmp = _build_root(pathlib.Path(td))
    (tmp / "data" / "behavior_rebuild.json").write_text(behavior_text)
    (tmp / "data" / "battery_dispatch_policies.json").write_text(
        dispatch_text if dispatch_text is not None else _dispatch_json(**BASE_DISPATCH))
    return tmp


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


def case_no_ev_household_low_package_is_scenario_c():
    """Issue #147: on a household whose intake says household.has_ev is false,
    behavior_rebuild.py publishes scenarios a and b as explicit not-applicable
    stubs with no figure at all. The LOW package must NOT vanish and must not
    be read out of a stub -- it becomes scenario c, the pure 25% flexible
    house-load shift, which is a real free fix for every household. Its range
    becomes [c, d] and its note names the flag that decided it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _committed_only_root(td, _behavior_json_no_ev(**BASE_NOEV_BEHAVIOR))

        r = _run(tmp)
        assert r.returncode == 0, (
            "package_results.py failed on a valid no-EV artifact -- a "
            f"not-applicable stub is a VALID artifact, not a broken one: {r.stderr[-2000:]}")

        out = json.loads((tmp / "data" / "package_results.json").read_text())
        low = out["packages"]["LOW"]
        c, d = (round(BASE_NOEV_BEHAVIOR["c"]), round(BASE_NOEV_BEHAVIOR["d"]))
        assert low["free_fix_scenario"] == "c", low
        assert low["savings_yr"] == c, (low["savings_yr"], c)
        assert low["savings_range"] == [c, d], low["savings_range"]
        assert low["cost"] == 0, low["cost"]          # the free fix is still free
        assert "household.has_ev" in low["note"], low["note"]
        base = round(BASE_NOEV_BEHAVIOR["model_bill"])
        assert low["projected_bill_current_rates_yr"] == base - c, low
        # and nothing anywhere may quote a figure off the stubs
        assert "not_applicable" not in json.dumps(out), out
    return ("a no-EV household's LOW package is scenario c (savings_yr, "
            "savings_range and projected bill all derived from c/d), is still "
            "free, and names household.has_ev in its note")


def case_ev_household_low_package_is_still_scenario_a():
    """Positive control for the case above. An ordinary EV household still
    gets the EV-only rung: LOW = scenario a, range [b, c], free_fix_scenario
    "a". Without this, the no-EV case would pass just as happily against a
    script that had been broken into always choosing c."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _committed_only_root(td, _behavior_json(**BASE_BEHAVIOR))

        r = _run(tmp)
        assert r.returncode == 0, f"package_results.py failed: {r.stderr[-2000:]}"

        out = json.loads((tmp / "data" / "package_results.json").read_text())
        low = out["packages"]["LOW"]
        assert low["free_fix_scenario"] == "a", low
        assert low["savings_yr"] == BASE_BEHAVIOR["a"], low
        assert low["savings_range"] == [BASE_BEHAVIOR["b"], BASE_BEHAVIOR["c"]], low
        assert low["cost"] == 0, low["cost"]
        assert "EV-only" in low["note"], low["note"]
        assert "household.has_ev" not in low["note"], low["note"]
    return ("an EV household's LOW package is still scenario a with range "
            "[b, c] and free_fix_scenario \"a\" -- the no-EV branch does not "
            "capture the ordinary household")


def case_low_savings_is_always_the_named_scenarios_rounded_saving():
    """The load-bearing invariant behind the new free_fix_scenario field:

        savings_yr == round(scenarios[free_fix_scenario].saved)

    report_tokens._free_fix_saving cross-checks package_results.json against
    behavior_rebuild.json by reading WHICH scenario fed savings_yr out of this
    field. If the field ever names a scenario that did not feed savings_yr, that
    consumer silently checks the wrong number -- it does not fail, it just stops
    guarding anything. Assert it generically (look the value up by the name the
    artifact itself gives) for BOTH households, off fractional savings so the
    rounding is real work rather than an identity."""
    ev = dict(model_bill=4903.61, a=1220.85, b=1008.72, c=1699.50, d=2178.83)
    fixtures = [
        ("EV", _behavior_json(**ev), "a"),
        ("no-EV", _behavior_json_no_ev(**BASE_NOEV_BEHAVIOR), "c"),
    ]
    for label, behavior_text, expected_key in fixtures:
        with tempfile.TemporaryDirectory() as td:
            tmp = _committed_only_root(td, behavior_text)
            r = _run(tmp)
            assert r.returncode == 0, f"{label}: {r.stderr[-2000:]}"

            low = json.loads((tmp / "data" / "package_results.json").read_text()
                             )["packages"]["LOW"]
            named = low["free_fix_scenario"]
            assert named == expected_key, (label, named, expected_key)
            sc = json.loads(behavior_text)["scenarios"][named]
            assert "saved" in sc, (
                f"{label}: free_fix_scenario names {named!r}, which the behavior "
                f"artifact publishes with no saved figure at all: {sc}")
            assert low["savings_yr"] == round(sc["saved"]), (
                f"{label}: savings_yr {low['savings_yr']} is not "
                f"round(scenarios[{named!r}].saved) = {round(sc['saved'])} -- "
                "free_fix_scenario names a scenario that did not feed it, so "
                "report_tokens._free_fix_saving would cross-check the wrong number")
    return ("savings_yr is round(scenarios[free_fix_scenario].saved) on both an "
            "EV and a no-EV household, so the report-side cross-check reads the "
            "scenario that actually fed the figure")


def case_malformed_scenario_a_still_fails_loudly():
    """A not-applicable STUB is a valid artifact; a MALFORMED scenarios.a is
    not, and must keep failing. The distinction is the explicit marker, not
    "a that cannot be read": if the branch ever widened to any unreadable a,
    a genuinely broken behavior artifact would be published as a no-EV
    household and the EV rung would silently disappear from the report.

    Three shapes that are NOT the marker, all of which must abort before
    package_results.json is written."""
    variants = [
        ("a dict with no saved figure and no marker", {"label": "a: EV only"}),
        ("a that is not a dict at all", 1221),
        ("an explicit not_applicable FALSE with no figure", {"not_applicable": False}),
    ]
    for label, a_node in variants:
        with tempfile.TemporaryDirectory() as td:
            tmp = _committed_only_root(td, _behavior_json_scenario_a(
                model_bill=BASE_BEHAVIOR["model_bill"], a_node=a_node,
                b=BASE_BEHAVIOR["b"], c=BASE_BEHAVIOR["c"], d=BASE_BEHAVIOR["d"]))

            r = _run(tmp)
            assert r.returncode != 0, (
                f"{label}: package_results.py accepted a malformed scenarios.a "
                "-- a broken artifact was treated as a no-EV household")
            assert not (tmp / "data" / "package_results.json").exists(), (
                f"{label}: package_results.json was written from a malformed "
                "behavior artifact")
    return ("a malformed scenarios.a (no saved figure, not a dict, or "
            "not_applicable false) still aborts the run -- only the explicit "
            "not_applicable:true marker is read as a no-EV household")


CASES = [
    case_no_current_run_copy_falls_back_to_committed_with_a_notice,
    case_disagreeing_current_run_copy_wins_and_is_announced_loudly,
    case_malformed_current_run_copy_fails_closed,
    case_mixed_source_cohort_fails_closed,
    case_neither_copy_exists_fails_closed_with_a_clear_message,
    case_no_ev_household_low_package_is_scenario_c,
    case_ev_household_low_package_is_still_scenario_a,
    case_low_savings_is_always_the_named_scenarios_rounded_saving,
    case_malformed_scenario_a_still_fails_loudly,
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
