#!/usr/bin/env python3
"""
Negative tests for parse_bills.py — proof that the fail-closed claims are real.

Run:  ./.venv/bin/python analysis/test_parse_bills.py

Each case builds a THROWAWAY repo (its own analysis/, data/, private/1-raw-data/) in a
temp directory, copies the real bill PDFs in, breaks one thing, and asserts that the
parser exits non-zero AND leaves the artifact set untouched. A parser that "succeeds" on
a broken corpus is the failure mode these guard against: it would overwrite committed
evidence with silently truncated data.

Skips (exit 0) when the private PDFs are not on this machine — the corpus is gitignored,
so this file cannot run in CI or on a fresh clone.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
ELEC = ROOT / "private" / "1-raw-data" / "electric-bills"
GAS = ROOT / "private" / "1-raw-data" / "gas-bills"
PY = sys.executable


def _build(tmp):
    """A minimal repo the parser will accept as its root, with the real corpus."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private" / "1-raw-data" / "electric-bills").mkdir(parents=True)
    (tmp / "private" / "1-raw-data" / "gas-bills").mkdir(parents=True)
    shutil.copy2(HERE / "parse_bills.py", tmp / "analysis" / "parse_bills.py")
    for src, dst in ((ELEC, "electric-bills"), (GAS, "gas-bills")):
        for f in src.glob("*.pdf"):
            shutil.copy2(f, tmp / "private" / "1-raw-data" / dst / f.name)
    # Pre-existing artifacts, so each case can assert they were not modified.
    for name in ("bill_periods_electric.csv", "bill_periods_gas.csv", "bill_tou_detail.csv",
                 "electric_bill_summary.csv", "gas_bill_summary.csv"):
        (tmp / "data" / name).write_text("SENTINEL\n")
    return tmp


def _run(tmp):
    return subprocess.run([PY, str(tmp / "analysis" / "parse_bills.py")],
                          cwd=tmp, capture_output=True, text=True)


def _artifacts_untouched(tmp):
    return all((tmp / "data" / n).read_text() == "SENTINEL\n" for n in (
        "bill_periods_electric.csv", "bill_periods_gas.csv", "bill_tou_detail.csv",
        "electric_bill_summary.csv", "gas_bill_summary.csv"))


def case_healthy_corpus(tmp):
    """Control: the real corpus must parse and write all five artifacts."""
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    assert not _artifacts_untouched(tmp), "healthy run wrote nothing"
    return "healthy corpus parses and publishes"


def case_missing_summary_statement(tmp):
    """A statement the committed summary is built from is gone."""
    victim = tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2026-02-02.pdf"
    victim.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus missing a summary statement"
    assert "missing from the corpus" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "missing summary statement -> exits, artifacts untouched"


def case_mid_corpus_gap(tmp):
    """A statement OUTSIDE the summary window is gone: caught by continuity, not by
    the presence check."""
    victim = tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2024-10-29.pdf"
    victim.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus with a mid-window gap"
    assert "gap between consecutive" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "mid-corpus gap -> exits, artifacts untouched"


def case_tou_headers_stop_matching(tmp):
    """Simulate a layout change that makes every TOU season header unrecognisable."""
    src = (tmp / "analysis" / "parse_bills.py").read_text()
    broken = src.replace(r'r"(SUMMER|WINTER) USAGE\s+On-Peak"',
                         r'r"(SUMMER|WINTER) USAGE_RENAMED\s+On-Peak"')
    assert broken != src, "test needs updating: header pattern not found"
    (tmp / "analysis" / "parse_bills.py").write_text(broken)
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus with no TOU detail at all"
    assert ("no TOU detail parsed" in r.stderr or "produced no TOU rows" in r.stderr), \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "TOU headers stop matching -> exits, artifacts untouched"


def case_write_rollback():
    """The publication step itself must be all-or-nothing: if a later file fails to
    swap in, the earlier ones are restored. Exercised directly, because a validation
    failure never reaches the write phase."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        paths = [d / f"a{i}.csv" for i in range(3)]
        for p in paths:
            p.write_text("OLD\n")

        def good(p):
            p.write_text("NEW\n")

        def bad(p):
            raise RuntimeError("simulated writer failure")

        try:
            pb._write_all_atomically([(paths[0], good), (paths[1], good), (paths[2], bad)])
        except RuntimeError:
            pass
        else:
            raise AssertionError("write did not propagate the failure")
        assert all(p.read_text() == "OLD\n" for p in paths), \
            f"partial update left behind: {[p.read_text().strip() for p in paths]}"
        leftovers = [f.name for f in d.iterdir() if f.suffix in (".tmp", ".bak")]
        assert not leftovers, f"temp/backup files left behind: {leftovers}"
    return "write failure -> full rollback, no temp files left"


def main():
    if not ELEC.is_dir() or not GAS.is_dir() or not any(ELEC.glob("*.pdf")):
        print("SKIP: private bill PDFs not present on this machine "
              "(they are gitignored; see DATA-SOURCES-CHEATSHEET.md §D)")
        return 0
    corpus_cases = [case_healthy_corpus, case_missing_summary_statement,
                    case_mid_corpus_gap, case_tou_headers_stop_matching]
    cases = corpus_cases + [case_write_rollback]
    failures = 0
    for case in cases:
        try:
            if case in corpus_cases:
                with tempfile.TemporaryDirectory() as td:
                    print(f"PASS  {case(_build(pathlib.Path(td)))}")
            else:
                print(f"PASS  {case()}")
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
