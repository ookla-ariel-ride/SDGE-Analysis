#!/usr/bin/env python3
"""Every suite's runner must survive a case that raises something nobody expected.

ISSUE #209. The hand-rolled `main()` in each suite caught `SkipCase` and
`AssertionError` and nothing else, so any other exception escaped the loop and
ended the RUN: no FAIL line naming the case, no "N/M passed" tally, and every
case after it silently never executed. It had already been fixed locally twice,
for two different exception classes, by two people who each took it for a defect
in one file.

THIS SUITE IS THE THING THAT STOPS THE THIRD INSTANCE. It does not read the
runners and check they look right -- it RUNS each one against a case that raises
`KeyError`, and requires the run to name that case, keep going, and still print
its tally. A suite added later without the guard fails here, and so does one
whose guard is reverted.

WHY IT IS CHEAP DESPITE RUNNING 19 RUNNERS. Each suite's `CASES` list is
swapped for three synthetic cases -- pass, raise, pass -- so none of the real
cases execute. What is being tested is the runner, not the suite.
"""
import contextlib
import io as _io
import pathlib
import sys
import traceback

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))

import suite_runner  # noqa: E402


class SkipCase(Exception):
    pass


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# Every suite in this repo that runs cases through a hand-rolled main(). The
# list is asserted COMPLETE below rather than maintained by hand: a new
# test_*.py with a main() that iterates CASES and is missing here fails.
SUITES = [
    # EVERY hand-rolled runner in analysis/, discovered by the function each
    # module actually CALLS from its __main__ block -- not by the name `main`.
    # Three of them (test_stage_private_data, test_heat_pump_conversion,
    # test_extra_results) call run(), are wired into CI and the coverage gate,
    # and were invisible to a discovery that matched on the name. All three
    # were exposed.
    "test_all_electric_endgame", "test_battery_backup_sims",
    "test_battery_dispatch_policies", "test_battery_plan_matrix",
    "test_battery_sizing_curve", "test_bill_decomposition", "test_carbon_dispatch_tradeoff",
    "test_carbon_fullyear", "test_cca_bundled_counterfactual", "test_cca_rate_extraction",
    "test_charge_discharge_distinct_naming", "test_deep_analyses", "test_dry_run",
    "test_dsgs_vpp_backtest", "test_egress_preflight", "test_extended_findings",
    "test_extra_results", "test_generate_report", "test_gross_import_decomposition",
    "test_heat_pump_conversion", "test_household", "test_irreducible_bill",
    "test_lifetime_payback", "test_llm_providers", "test_nem3_grandfathering",
    "test_package_results", "test_parse_bills", "test_perfect_foresight_dispatch",
    "test_privacy_tiers", "test_private_egress", "test_prose_blocks",
    "test_prose_lint", "test_publish", "test_quiet_night_floor", "test_rates",
    "test_rates_history", "test_report_blocks", "test_report_consistency",
    "test_report_tokens", "test_reprice_by_vintage", "test_scripts_runnable",
    "test_service_headroom", "test_soiling_analysis", "test_stage_private_data",
    "test_stamp_report_version",
    "test_threeway_production_validation", "test_tou_audit", "test_tou_spread",
    "test_tou_structure_stress", "test_uncertainty_propagation",
]

# The entry point, where it is not main().
ENTRY = {
    "test_extra_results": "run",
    "test_heat_pump_conversion": "run",
    "test_stage_private_data": "run",
}

# EXEMPT FROM "KEEPS GOING", AND SHOWN RATHER THAN ASSUMED (AC1). Each names the
# failing case and the class it raised, then deliberately stops -- their handler
# re-raises. NOT exempt from the sweep, and NOT exempt from saying how far they
# got: stopping is a choice, going quiet is not.
ABORTS_ON_FIRST_FAILURE = {
    "test_all_electric_endgame", "test_dry_run", "test_egress_preflight",
    "test_generate_report", "test_gross_import_decomposition", "test_llm_providers",
    "test_prose_blocks", "test_prose_lint", "test_report_blocks", "test_report_tokens",
    "test_reprice_by_vintage", "test_stamp_report_version",
    "test_uncertainty_propagation",
}


# The case lists each runner iterates. All but one hold a single `CASES`.
CASE_LISTS = {"test_parse_bills": ("STANDALONE_CASES", "CORPUS_CASES")}

INJECTED = "injected: an exception class no runner anticipated"


def _synthetic_cases(injected):
    """pass, raise `injected`, pass -- ONE injected class per invocation.

    THIS USED TO INJECT BOTH CLASSES IN ONE RUN, and that hid the thing the file
    exists to check. A KeyError placed before the SystemExit meant every
    abort-on-first-failure runner stopped at the KeyError and never reached the
    SystemExit case, so reverting any of those eleven handlers from the shared
    tuple back to `except Exception` left this suite green. The claim was that
    any reverted handler fails here; it was true only for the runners that keep
    going. Probing one class per invocation is what makes it true for all of
    them.
    """
    def case_synthetic_first():
        return "the case before the injected one"

    def case_synthetic_raises():
        raise injected

    def case_synthetic_last():
        return "the case after the injected one"

    return [case_synthetic_first, case_synthetic_raises, case_synthetic_last]


def _stub(name):
    """A no-op standing in for a real case, under the real case's name.

    Several runners assert BEFORE the loop that every `case_*` defined in the
    module appears in CASES. Replacing the list wholesale trips that assertion,
    which cost me two false positives -- test_service_headroom and
    test_privacy_tiers both looked killed when the instrument was what broke.
    Keeping the names and emptying the bodies satisfies the registry check while
    still running none of the real work.
    """
    def f():
        return "stubbed by test_suite_runner"
    f.__name__ = name
    return f


def _run_one(modname, injected):
    """Import the suite, splice ONE injected failure in front of stubbed cases,
    call whatever function that module runs from __main__."""
    mod = __import__(modname)
    names = CASE_LISTS.get(modname, ("CASES",))
    saved = {n: list(getattr(mod, n)) for n in names}
    entry = getattr(mod, ENTRY.get(modname, "main"))
    try:
        for i, n in enumerate(names):
            stubs = [_stub(c.__name__) for c in saved[n]]
            setattr(mod, n, _synthetic_cases(injected) + stubs if i == 0 else stubs)
        out = _io.StringIO()
        status = 1
        try:
            with contextlib.redirect_stdout(out):
                status = entry()
        except BaseException as exc:                        # noqa: BLE001
            # An abort-family runner re-raises on purpose -- some as
            # SystemExit(1), one as a bare `raise` carrying the original class.
            # Either is fine HERE; what it may not do is go quiet, which the
            # caller checks.
            if modname in ABORTS_ON_FIRST_FAILURE:
                status = exc.code if isinstance(getattr(exc, "code", None), int) else 1
            elif isinstance(exc, SystemExit):
                raise AssertionError(
                    f"{modname} let a SystemExit escape the case loop. It "
                    f"inherits from BaseException, so `except Exception` walks "
                    f"straight past it -- and household.py raises one whenever "
                    f"the private archive is absent, which is how CI runs. "
                    f"Output:\n{out.getvalue()}") from None
            else:
                raise AssertionError(
                    f"{modname} let a {type(exc).__name__} escape the case "
                    f"loop, so the run ends with no tally and every case after "
                    f"the failing one never executes -- issue #209's whole "
                    f"shape. Output:\n{out.getvalue()}\n"
                    f"{traceback.format_exc()}") from None
        return status, out.getvalue()
    finally:
        for n, v in saved.items():
            setattr(mod, n, v)


@case
def case_every_runner_survives_an_unexpected_exception():
    """Every runner, every class, one invocation each: the failing case must be
    named, the run must still say how far it got, and a runner that keeps going
    must actually keep going."""
    bad = []
    injections = (
        ("KeyError", KeyError(INJECTED)),
        # The class `except Exception` does not catch, and the one household.py
        # raises when the private archive is absent -- i.e. how CI runs.
        ("SystemExit", SystemExit("injected: a fail-closed exit")),
    )
    for modname in SUITES:
        for label, exc in injections:
            try:
                status, out = _run_one(modname, exc)
            except AssertionError as e:
                bad.append(f"[{label}] " + str(e).split("\n")[0])
                continue
            if "case_synthetic_raises" not in out:
                bad.append(f"[{label}] {modname}: the injected case is not named "
                           "in any FAIL line")
            elif label not in out:
                bad.append(f"[{label}] {modname}: the FAIL line does not say what "
                           "was raised")
            if modname in ABORTS_ON_FIRST_FAILURE:
                if "ran before this failure stopped the run" not in out:
                    bad.append(f"[{label}] {modname}: stops at the first failure "
                               "and does not say how many cases ran, so the "
                               "truncation is as invisible as the defect this "
                               "issue is about")
            else:
                if "the case after the injected one" not in out:
                    bad.append(f"[{label}] {modname}: the case AFTER the injected "
                               "one did not run")
                if "passed" not in out:
                    bad.append(f"[{label}] {modname}: no tally, so a truncated run "
                               "is invisible")
            # TWO CONVENTIONS, and they collide numerically: most runners
            # return `1 if failed else 0`, while test_heat_pump_conversion
            # returns `failed == 0` and its __main__ inverts it. False == 0 in
            # python, so a boolean failure is indistinguishable from an integer
            # success by value -- only by type. Both are correct; asserting one
            # of them would have been a false alarm about working code.
            signalled = (status is False) if isinstance(status, bool) else (status == 1)
            if not signalled:
                bad.append(f"[{label}] {modname}: returned {status!r}, which is "
                           "not how this runner signals failure, so a failing "
                           "run would exit 0")
    assert not bad, (
        f"{len(bad)} runner/class combination(s) still let one case take down "
        "the run:\n  " + "\n  ".join(bad))
    return (f"all {len(SUITES)} runners name an injected KeyError AND an "
            f"injected SystemExit, keep going or say where they stopped, and "
            f"return 1 ({len(SUITES) * 2} probes)")


@case
def case_a_clean_run_still_passes_and_a_skip_still_skips():
    """The other direction, or the case above is satisfied by a runner that calls
    everything a failure."""
    mod = __import__("test_household")
    saved = list(mod.CASES)
    try:
        mod.CASES = [lambda: "a synthetic case that passes"]
        mod.CASES[0].__name__ = "case_synthetic_ok"
        out = _io.StringIO()
        with contextlib.redirect_stdout(out):
            status = mod.main()
        assert status == 0, f"a clean run returned {status}, not 0"
        assert "1/1 passed" in out.getvalue(), (
            f"a clean run did not tally correctly: {out.getvalue()!r}")

        def case_synthetic_skips():
            raise mod.SkipCase("nothing to measure here") \
                if hasattr(mod, "SkipCase") else AssertionError("no SkipCase")
        # test_household has no SkipCase; the skip half is asserted on a suite
        # that does, so this stays a real check rather than a vacuous one.
        skipper = __import__("test_private_egress")
        s_saved = list(skipper.CASES)
        try:
            def case_synthetic_skip():
                raise skipper.SkipCase("a synthetic skip")
            skipper.CASES = [case_synthetic_skip]
            out2 = _io.StringIO()
            with contextlib.redirect_stdout(out2):
                s2 = skipper.main()
            assert s2 == 0, f"a run whose only case skipped returned {s2}, not 0"
            assert "SKIP" in out2.getvalue(), (
                f"a skipping case was not reported as a skip: {out2.getvalue()!r}")
        finally:
            skipper.CASES = s_saved
    finally:
        mod.CASES = saved
    return ("a clean run still returns 0 and tallies, and a SkipCase still "
            "reports SKIP rather than a failure")


@case
def case_the_suite_list_is_complete():
    """A runner this file does not know about is a runner nobody guards.

    Every analysis/test_*.py defining a main() that loops over a module-level
    case list must be in SUITES, so adding a suite without the handler cannot
    quietly opt out of the case above.
    """
    import ast
    missing = []
    for p in sorted(ANALYSIS.glob("test_*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue
        tree = ast.parse(p.read_text())
        fn = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        if fn is None:
            continue
        loops = [ast.unparse(n.iter) for n in ast.walk(fn) if isinstance(n, ast.For)]
        if not any(l.endswith("CASES") for l in loops):
            continue
        if p.stem not in SUITES and p.stem not in ABORTS_ON_FIRST_FAILURE:
            missing.append(p.stem)
    assert not missing, (
        f"{missing} run cases through a hand-rolled main() and are not in "
        "SUITES, so nothing checks that one bad case cannot end their run")
    return (f"every hand-rolled runner in analysis/ is covered "
            f"({len(SUITES)} swept, {len(ABORTS_ON_FIRST_FAILURE)} exempt "
            f"for aborting on first failure, shown not assumed)")


@case
def case_the_shared_reporter_says_what_was_raised():
    """The helper itself: an AssertionError prints exactly as it always did, and
    anything else gains its class name and a traceback."""
    def case_probe():
        pass

    out = _io.StringIO()
    with contextlib.redirect_stdout(out):
        suite_runner.report_case_failure(case_probe, AssertionError("a plain message"))
    assert out.getvalue() == "FAIL  case_probe: a plain message\n", (
        f"an AssertionError no longer prints the way every suite's output "
        f"already looks: {out.getvalue()!r}")

    out = _io.StringIO()
    try:
        raise KeyError("something structural")
    except KeyError as e:
        with contextlib.redirect_stdout(out):
            suite_runner.report_case_failure(case_probe, e)
    text = out.getvalue()
    assert text.startswith("FAIL  case_probe: KeyError: "), (
        f"the class of the exception is not on the FAIL line: {text!r}")
    assert "Traceback (most recent call last)" in text, (
        "no traceback was printed for a non-assertion, so the frame is lost for "
        "good in a runner that keeps going")
    assert "SystemExit" in [c.__name__ for c in suite_runner.CASE_FAILURES], (
        "SystemExit is no longer caught explicitly; it inherits from "
        "BaseException, so `except Exception` walks straight past it and a "
        "fail-closed SystemExit ends the run again (issue #221 shipped that)")
    return ("the shared reporter keeps assertion output identical and adds the "
            "class name and traceback for everything else")


def main():
    ran = failures = 0
    for c in CASES:
        try:
            print(f"PASS  {c()}")
            ran += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(c, e)
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
