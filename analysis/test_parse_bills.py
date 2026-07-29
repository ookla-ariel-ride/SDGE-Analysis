#!/usr/bin/env python3
"""
Negative tests for parse_bills.py — proof that the fail-closed claims are real.

Run:  ./.venv/bin/python analysis/test_parse_bills.py

Each case builds a THROWAWAY repo (its own analysis/, data/, private/1-raw-data/) in a
temp directory, copies the real bill PDFs in, breaks one thing, and asserts that the
parser exits non-zero AND leaves the artifact set untouched. A parser that "succeeds" on
a broken corpus is the failure mode these guard against: it would overwrite committed
evidence with silently truncated data.

Only the corpus-dependent cases need the gitignored PDFs; they report as SKIP when the
corpus is absent. Everything covering publication, rollback and concurrency runs anywhere
(temp files, or the committed data/ artifacts), so a broken lock or a lost rollback cannot
pass in a clean checkout or in CI.
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


class SkipCase(Exception):
    """Raised by a case whose preconditions this corpus cannot meet (e.g. a fork whose
    corpus lacks a statement the case wants to delete). The runner counts it as
    neither pass nor fail."""


def _require(path):
    """The corpus negative-tests delete specific statements from THIS repo's corpus.
    On a fork's corpus those filenames don't exist — skip the case instead of
    crashing with FileNotFoundError."""
    if not path.exists():
        raise SkipCase(f"{path.name} is not in this corpus")
    return path


def _build(tmp):
    """A minimal repo the parser will accept as its root, with the real corpus.
    gas-bills/ is only staged when the real corpus has one — parse_bills.py treats a
    MISSING gas dir as a no-gas household and an EMPTY one as corpus loss."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private" / "1-raw-data" / "electric-bills").mkdir(parents=True)
    shutil.copy2(HERE / "parse_bills.py", tmp / "analysis" / "parse_bills.py")
    srcs = [(ELEC, "electric-bills")]
    if GAS.is_dir() and any(GAS.glob("*.pdf")):
        (tmp / "private" / "1-raw-data" / "gas-bills").mkdir(parents=True)
        srcs.append((GAS, "gas-bills"))
    for src, dst in srcs:
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
    victim = _require(
        tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2026-02-02.pdf")
    victim.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus missing a summary statement"
    assert "missing from the corpus" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "missing summary statement -> exits, artifacts untouched"


def case_mid_corpus_gap(tmp):
    """A statement OUTSIDE the summary window is gone: caught by continuity, not by
    the presence check."""
    victim = _require(
        tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2024-10-29.pdf")
    victim.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus with a mid-window gap"
    assert "gap between consecutive" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "mid-corpus gap -> exits, artifacts untouched"


def case_mid_corpus_gas_gap(tmp):
    """A GAS statement outside the summary window is gone. The presence check only
    covers summary statements, and gas bills on its own cycle, so only a gas-specific
    continuity check catches this."""
    victim = _require(
        tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2024-10-29.pdf")
    victim.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus with a mid-window gas gap"
    assert "gas billing periods" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "mid-corpus gas gap -> exits, artifacts untouched"


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


def case_no_gas_dir_publishes_electric_only(tmp):
    """Gas is presence-based (intake: required_if has_gas): with NO gas-bills/ dir the
    run must succeed, notice loudly, write only the electric artifacts, and leave the
    committed gas artifacts untouched."""
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    if gasdir.exists():
        shutil.rmtree(gasdir)
    r = _run(tmp)
    assert r.returncode == 0, f"no-gas-dir run failed:\n{r.stderr}"
    assert "does not exist" in r.stdout and "no gas service" in r.stdout, \
        f"missing the loud no-gas notice:\n{r.stdout}"
    for n in ("bill_periods_electric.csv", "bill_tou_detail.csv",
              "electric_bill_summary.csv"):
        assert (tmp / "data" / n).read_text() != "SENTINEL\n", \
            f"electric artifact {n} was not written"
    for n in ("bill_periods_gas.csv", "gas_bill_summary.csv"):
        assert (tmp / "data" / n).read_text() == "SENTINEL\n", \
            f"gas artifact {n} was modified on a no-gas run"
    return "missing gas-bills/ -> electric-only publish, gas artifacts untouched"


def case_empty_gas_dir_fails(tmp):
    """An EXISTING-but-empty gas-bills/ is corpus loss, not a no-gas house: the run
    must fail closed and touch nothing."""
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    gasdir.mkdir(parents=True, exist_ok=True)
    for f in gasdir.glob("*.pdf"):
        f.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted an existing-but-empty gas-bills/"
    assert "corpus loss" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "empty gas-bills/ -> exits, artifacts untouched"


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


def _fail_replace_at(fail_calls):
    """Return (patcher, counter) making os.replace raise on the given 1-based calls."""
    import unittest.mock as mock
    real = os.replace
    state = {"n": 0}

    def flaky(src, dst):
        state["n"] += 1
        if state["n"] in fail_calls:
            raise OSError(f"simulated os.replace failure #{state['n']}")
        return real(src, dst)

    return mock.patch("os.replace", flaky), state


def case_rollback_after_partial_swap():
    """Failure DURING the swap phase, after files are already published: every
    already-swapped file must be restored and no temp/backup files left behind."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        paths = [d / f"a{i}.csv" for i in range(4)]
        for p in paths:
            p.write_text("OLD\n")
        writes = [(p, lambda q: q.write_text("NEW\n")) for p in paths]
        patcher, _ = _fail_replace_at({3})          # 3rd swap fails
        with patcher:
            try:
                pb._write_all_atomically(writes)
            except OSError:
                pass
            else:
                raise AssertionError("swap failure did not propagate")
        stale = [p.name for p in paths if p.read_text() != "OLD\n"]
        assert not stale, f"files left published after rollback: {stale}"
        leftovers = [f.name for f in d.iterdir() if f.suffix in (".tmp", ".bak")]
        assert not leftovers, f"temp/backup files left behind: {leftovers}"
    return "failure mid-swap -> all files restored, nothing left behind"


def case_restore_failure_preserves_backups():
    """Failure during the swap AND during the restore: the surviving .bak files are the
    only copy of the previous evidence, so they must NOT be deleted, and the operator
    must be told which artifacts are stale."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        paths = [d / f"a{i}.csv" for i in range(4)]
        for p in paths:
            p.write_text("OLD\n")
        writes = [(p, lambda q: q.write_text("NEW\n")) for p in paths]
        # 3rd call = the failing swap; 4th/5th = the restore attempts, also failing.
        patcher, _ = _fail_replace_at({3, 4, 5})
        with patcher:
            try:
                pb._write_all_atomically(writes)
            except SystemExit as e:
                msg = str(e)
            else:
                raise AssertionError("restore failure did not raise SystemExit")
        assert "LEFT IN PLACE" in msg and "STALE" in msg, f"unhelpful message: {msg}"
        baks = sorted(f.name for f in d.iterdir() if f.suffix == ".bak")
        assert baks, "backups were deleted despite an incomplete rollback"
        # Every stale artifact must still have its previous contents recoverable.
        for p in paths:
            if p.read_text() != "OLD\n":
                bak = p.with_name(p.name + ".bak")
                assert bak.exists() and bak.read_text() == "OLD\n", \
                    f"{p.name} is stale and its backup is missing"
    return "restore failure -> backups preserved, manual recovery reported"


def case_retry_after_failed_rollback_refuses():
    """After a failed rollback the .bak files are the only copy of the previous
    artifacts. A second run must REFUSE rather than back the stale artifact up over its
    own recovery copy."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        paths = [d / f"a{i}.csv" for i in range(4)]
        for p in paths:
            p.write_text("OLD\n")
        writes = [(p, lambda q: q.write_text("NEW\n")) for p in paths]

        patcher, _ = _fail_replace_at({3, 4, 5})     # swap fails, restores fail too
        with patcher:
            try:
                pb._write_all_atomically(writes)
            except SystemExit:
                pass
            else:
                raise AssertionError("restore failure did not raise")
        baks = [f for f in d.iterdir() if f.suffix == ".bak"]
        assert baks, "precondition failed: no backups left to protect"
        before = {b.name: b.read_text() for b in baks}

        try:                                          # the retry
            pb._write_all_atomically(writes)
        except SystemExit as e:
            msg = str(e)
        else:
            raise AssertionError("retry proceeded despite leftover recovery backups")
        assert "refusing to publish" in msg, f"unhelpful message: {msg}"
        after = {b.name: b.read_text() for b in d.iterdir() if b.suffix == ".bak"}
        assert after == before, f"retry damaged the recovery backups: {before} -> {after}"
        assert all(v == "OLD\n" for v in after.values()), \
            "recovery backups no longer hold the previous contents"
    return "retry after failed rollback -> refuses, backups intact"


def case_lock_blocks_second_publisher():
    """A publication while another holds the lock must refuse, not proceed."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        p = d / "a.csv"
        p.write_text("OLD\n")
        with pb._publication_lock(d):                 # someone else is publishing
            try:
                pb._write_all_atomically([(p, lambda q: q.write_text("NEW\n"))])
            except SystemExit as e:
                msg = str(e)
            else:
                raise AssertionError("second publisher ran while the lock was held")
        assert "another parse_bills run" in msg, f"unhelpful message: {msg}"
        assert p.read_text() == "OLD\n", "blocked publisher still modified the artifact"
    return "lock held -> second publisher refuses, artifact untouched"


_CONCURRENT_CHILD = '''
import pathlib, sys, time
sys.path.insert(0, {here!r})
import parse_bills as pb
d = pathlib.Path({dir!r})
paths = [d / f"a{{i}}.csv" for i in range(4)]
tag = sys.argv[1]

def slow(dst, tag=tag):
    time.sleep(0.05)          # widen the window two runs could overlap in
    dst.write_text(tag + "\\n")

try:
    pb._write_all_atomically([(p, slow) for p in paths])
    print("PUBLISHED")
except SystemExit as e:
    print("REFUSED")
'''


def case_concurrent_publishers_serialize():
    """Two processes publishing at once: exactly one wins, the artifact set ends
    internally consistent, and no staging or backup files are left behind."""
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        paths = [d / f"a{i}.csv" for i in range(4)]
        for p in paths:
            p.write_text("OLD\n")
        child = d / "child.py"
        child.write_text(_CONCURRENT_CHILD.format(here=str(HERE), dir=str(d)))
        procs = [subprocess.Popen([PY, str(child), tag],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for tag in ("RUN-A", "RUN-B")]
        outs = [p.communicate()[0].strip() for p in procs]
        published = [o for o in outs if o == "PUBLISHED"]
        assert len(published) >= 1, f"neither run published: {outs}"
        contents = {p.read_text().strip() for p in paths}
        assert len(contents) == 1, \
            f"artifact set is internally inconsistent across runs: {contents}"
        leftovers = sorted(f.name for f in d.iterdir()
                           if f.suffix in (".tmp", ".bak") or ".tmp" in f.name)
        assert not leftovers, f"staging/backup files left behind: {leftovers}"
    return f"concurrent publishers -> serialized, set consistent ({outs})"


def _load_artifacts():
    import pandas as pd
    root = ROOT / "data"
    return (pd.read_csv(root / "bill_periods_electric.csv"),
            pd.read_csv(root / "bill_periods_gas.csv"),
            pd.read_csv(root / "bill_tou_detail.csv"))


def case_overlapping_electric_periods():
    """Overlapping periods are distinct STRINGS, so the duplicate check cannot see them;
    only a continuity check that requires exactly one day between periods catches the
    double-counting."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    elec, gas, tou = _load_artifacts()
    victim = elec.index[5]
    start, end = elec.loc[victim, "period"].split(" - ")
    import datetime as dt
    shifted = (dt.datetime.strptime(start, "%m/%d/%y") - dt.timedelta(days=3))
    elec.loc[victim, "period"] = f"{shifted.strftime('%-m/%-d/%y')} - {end}"
    try:
        pb._validate(elec, gas, tou)
    except SystemExit as e:
        assert "overlapping electric" in str(e), f"wrong error: {e}"
    else:
        raise AssertionError("overlapping electric periods were accepted")
    return "overlapping electric periods -> rejected"


def case_overlapping_gas_periods():
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    elec, gas, tou = _load_artifacts()
    victim = gas.index[5]
    start, end = gas.loc[victim, "period"].split(" - ")
    import datetime as dt
    shifted = (dt.datetime.strptime(start, "%b %d, %Y") - dt.timedelta(days=3))
    gas.loc[victim, "period"] = f"{shifted.strftime('%b %-d, %Y')} - {end}"
    try:
        pb._validate(elec, gas, tou)
    except SystemExit as e:
        assert "overlapping gas" in str(e), f"wrong error: {e}"
    else:
        raise AssertionError("overlapping gas periods were accepted")
    return "overlapping gas periods -> rejected"


def case_fork_corpus_skips_presence_check():
    """A corpus sharing NONE of the SUMMARY_STATEMENTS_* dates is a fork: check 1 is
    skipped with a printed notice instead of demanding statements the fork can never
    have. Every other check still runs."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    elec, gas, tou = _load_artifacts()
    elec, gas = elec.copy(), gas.copy()
    elec["statement_date"] = "1900-01-01"      # zero overlap with either list
    gas["statement_date"] = "1900-01-01"
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        pb._validate(elec, gas, tou)           # must NOT raise
    out = buf.getvalue()
    assert "FORK" in out and "SUMMARY_STATEMENTS_ELEC" in out, \
        f"fork skip ran silently or without the replace-me instruction:\n{out}"
    assert "SUMMARY_STATEMENTS_GAS" in out, f"gas list skip not noticed:\n{out}"
    return "fork corpus (zero overlap) -> check 1 skipped with loud notice"


def case_partial_overlap_still_fails():
    """PARTIAL overlap with the summary lists is corpus loss, never a fork: removing
    one documented statement from an otherwise-matching corpus must still fail closed."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    elec, gas, tou = _load_artifacts()
    elec = elec.copy()
    victim = pb.SUMMARY_STATEMENTS_ELEC[0]
    if victim not in set(elec.statement_date):
        raise SkipCase("committed artifacts do not cover SUMMARY_STATEMENTS_ELEC")
    elec.loc[elec.statement_date == victim, "statement_date"] = "1900-01-01"
    try:
        pb._validate(elec, gas, tou)
    except SystemExit as e:
        assert "missing from the corpus" in str(e), f"wrong error: {e}"
    else:
        raise AssertionError("partial overlap with the summary list was accepted")
    return "partial overlap with the summary list -> still fails closed"


# Cases needing the gitignored bill PDFs. Only these can be skipped.
CORPUS_CASES = [case_healthy_corpus, case_missing_summary_statement,
                case_mid_corpus_gap, case_mid_corpus_gas_gap,
                case_tou_headers_stop_matching,
                case_no_gas_dir_publishes_electric_only,
                case_empty_gas_dir_fails]

# Cases that run anywhere: they use temp files, or the COMMITTED data/ artifacts. The
# publication, rollback and concurrency guards live here, so they must run in a clean
# checkout and in CI — skipping the whole suite when the private corpus is absent would
# let a broken lock or a lost rollback pass the documented command with exit code 0.
STANDALONE_CASES = [case_write_rollback, case_rollback_after_partial_swap,
                    case_restore_failure_preserves_backups,
                    case_retry_after_failed_rollback_refuses,
                    case_lock_blocks_second_publisher,
                    case_concurrent_publishers_serialize,
                    case_overlapping_electric_periods,
                    case_overlapping_gas_periods,
                    case_fork_corpus_skips_presence_check,
                    case_partial_overlap_still_fails]


def main():
    # The electric corpus is the hard requirement; gas-dependent cases skip themselves
    # (via SkipCase) when this corpus has no gas statements — a no-gas fork is valid.
    have_corpus = ELEC.is_dir() and any(ELEC.glob("*.pdf"))
    failures = skipped = ran = 0

    for case in STANDALONE_CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1

    for case in CORPUS_CASES:
        if not have_corpus:
            print(f"SKIP  {case.__name__} (needs the gitignored bill PDFs; "
                  f"see DATA-SOURCES-CHEATSHEET.md §D)")
            skipped += 1
            continue
        try:
            with tempfile.TemporaryDirectory() as td:
                print(f"PASS  {case(_build(pathlib.Path(td)))}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1

    total = len(STANDALONE_CASES) + len(CORPUS_CASES)
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{total} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
