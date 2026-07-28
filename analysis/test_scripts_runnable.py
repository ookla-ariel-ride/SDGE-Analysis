#!/usr/bin/env python3
"""Catch analysis scripts that have quietly stopped being runnable.

This repo has been bitten by that three times. analyze.py and analyze_norelief.py
carried absolute paths into a retired Cowork sandbox and had never run here, which
left plan_results.csv, hourly_profile.csv and monthly.csv with no working
generator. carbon_timing.py read a directory that was never committed. In every
case the script sat in analysis/ looking maintained, and nothing complained until
someone tried to reproduce an artifact.

The failure mode is silence, so these cases are deliberately cheap and structural:
they parse, they do not import or execute anything, and they need no private data.
A script is either expected to run or explicitly listed as retired. There is no
third state.

Run from the repo root:  ./.venv/bin/python analysis/test_scripts_runnable.py
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"

# Scripts that are kept for provenance and are not expected to run. Each needs a
# RETIRED marker in its own docstring saying why, so the reason travels with the
# file rather than living only here.
RETIRED = {"carbon_timing.py"}

# Paths a script may legitimately reference: the sandbox convention, and anything
# resolved from the repo root at runtime.
ABS_PATH = re.compile(r"""["'](/(?!tmp/)[A-Za-z0-9_.\-]+/[^"']*)["']""")


def _scripts():
    return sorted(f for f in ANALYSIS.glob("*.py") if not f.name.startswith("test_"))


def case_every_script_parses():
    bad = []
    for f in _scripts():
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            bad.append(f"{f.name}: {e}")
    assert not bad, bad
    return f"all {len(_scripts())} analysis scripts parse"


def case_no_absolute_paths_outside_the_repo():
    """An absolute path baked into a script is how two generators died."""
    offenders = []
    for f in _scripts():
        for m in ABS_PATH.finditer(f.read_text()):
            path = m.group(1)
            if path.startswith(str(ROOT)):
                continue
            if path.startswith("/") and not pathlib.Path(path).exists():
                offenders.append(f"{f.name}: {path}")
    assert not offenders, f"absolute paths that do not exist: {offenders}"
    return "no analysis script hardcodes an absolute path that is not present"


def case_retired_scripts_say_so():
    for name in sorted(RETIRED):
        f = ANALYSIS / name
        assert f.exists(), f"{name} is listed as retired but does not exist"
        head = ast.get_docstring(ast.parse(f.read_text())) or ""
        assert "RETIRED" in head, f"{name} is retired but its docstring does not say so"
    return f"all {len(RETIRED)} retired script(s) carry a RETIRED marker in the docstring"


def case_retired_scripts_fail_loudly():
    """A retired script must explain itself, not die on a stray missing file."""
    for name in sorted(RETIRED):
        src = (ANALYSIS / name).read_text()
        assert "RETIRED" in src and "SystemExit" in src, (
            f"{name} should raise SystemExit with an explanation when run")
    return "retired scripts raise an explanatory SystemExit rather than crashing"


def case_no_script_is_silently_unlisted():
    """Every script is either live or retired; the set is closed."""
    live = {f.name for f in _scripts()} - RETIRED
    assert live, "no live scripts found — the glob is probably wrong"
    stale = RETIRED - {f.name for f in ANALYSIS.glob("*.py")}
    assert not stale, f"RETIRED lists files that no longer exist: {stale}"
    return f"{len(live)} live and {len(RETIRED)} retired scripts, none unaccounted for"


CASES = [
    case_every_script_parses,
    case_no_absolute_paths_outside_the_repo,
    case_retired_scripts_say_so,
    case_retired_scripts_fail_loudly,
    case_no_script_is_silently_unlisted,
]


def main():
    ran = failures = 0
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
