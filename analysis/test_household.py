#!/usr/bin/env python3
"""Unit guards for household.py's fail-closed behavior.

household.py is the intake gate: every per-house constant flows through it, and
its whole contract is refusing to run without the interview answers rather than
inventing defaults. That refusal path never executes on a machine with a filled
private/household.yaml, so it sat untested on exactly the machines where the
data exists. These cases point the loader at synthetic files and exercise every
branch. No private data; runs in CI.

Run from the repo root:  ./.venv/bin/python analysis/test_household.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402
import household as hh


def _with(path_content):
    """Point the module at a synthetic household.yaml (or its absence)."""
    td = tempfile.TemporaryDirectory()
    p = pathlib.Path(td.name) / "household.yaml"
    if path_content is not None:
        p.write_text(path_content)
    hh.PATH = p
    hh._cache = None          # the loader caches; every case starts cold
    return td


def case_missing_file_fails_closed():
    with _with(None):
        try:
            hh.get("charger.kw")
            raise AssertionError("missing household.yaml did not fail closed")
        except SystemExit as e:
            assert "intake interview" in str(e), e
    return "a missing household.yaml raises SystemExit pointing at the interview"


def case_missing_required_key_fails_closed():
    with _with("charger:\n  kw: 11.5\n"):
        assert hh.get("charger.kw") == 11.5
        try:
            hh.get("solar.install_invoice_usd")
            raise AssertionError("missing required key did not fail closed")
        except SystemExit as e:
            assert "solar.install_invoice_usd" in str(e), e
    return "a missing required key raises SystemExit naming the dotted path"


def case_optional_key_returns_none_not_default():
    with _with("charger:\n  kw: 11.5\n"):
        assert hh.get("misc.itc_claimed", required=False) is None
        assert hh.get("charger.kw.deeper", required=False) is None
    return "required=False returns None for absent keys, never a silent default"


def case_empty_file_is_an_empty_mapping():
    with _with(""):
        assert hh.get("anything", required=False) is None
        try:
            hh.get("anything")
            raise AssertionError("empty file satisfied a required key")
        except SystemExit:
            pass
    return "an empty household.yaml loads as an empty mapping and still fails closed"


def case_nested_paths_resolve():
    with _with("a:\n  b:\n    c: 7\n"):
        assert hh.get("a.b.c") == 7
        assert hh.get("a.b") == {"c": 7}
    return "dotted paths resolve through nested mappings"


CASES = [
    case_missing_file_fails_closed,
    case_missing_required_key_fails_closed,
    case_optional_key_returns_none_not_default,
    case_empty_file_is_an_empty_mapping,
    case_nested_paths_resolve,
]


def main():
    real_path, real_cache = hh.PATH, hh._cache
    ran = failures = 0
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
        finally:
            hh.PATH, hh._cache = real_path, real_cache
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
