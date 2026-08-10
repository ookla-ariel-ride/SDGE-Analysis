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
import csv
import datetime as dt
import json
import os
import pathlib
import re
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


def _set_flag(tmp, has_gas):
    """Write the throwaway root's SYNTHETIC private/household.yaml. parse_bills.py
    reads gas applicability from household.has_gas through the analysis/household.py
    loader, which resolves its repo root by walking up from the CWD — the subprocess
    runs with cwd=tmp, so the loader finds this file and the real gitignored
    private/household.yaml is never involved."""
    (tmp / "private").mkdir(exist_ok=True)
    (tmp / "private" / "household.yaml").write_text(
        f"household:\n  has_gas: {'true' if has_gas else 'false'}\n")


def _build(tmp):
    """A minimal repo the parser will accept as its root, with the real corpus.
    The synthetic household.has_gas flag mirrors the corpus actually staged (true
    when the real repo has gas PDFs to copy) so the control case passes on gas and
    no-gas corpora alike; flag-semantics cases overwrite it via _set_flag()."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private" / "1-raw-data" / "electric-bills").mkdir(parents=True)
    for name in ("parse_bills.py", "household.py"):
        shutil.copy2(HERE / name, tmp / "analysis" / name)
    have_gas_corpus = GAS.is_dir() and any(GAS.glob("*.pdf"))
    srcs = [(ELEC, "electric-bills")]
    if have_gas_corpus:
        (tmp / "private" / "1-raw-data" / "gas-bills").mkdir(parents=True)
        srcs.append((GAS, "gas-bills"))
    for src, dst in srcs:
        for f in src.glob("*.pdf"):
            shutil.copy2(f, tmp / "private" / "1-raw-data" / dst / f.name)
    _set_flag(tmp, have_gas_corpus)
    # Pre-existing artifacts, so each case can assert they were not modified.
    for name in ARTIFACTS:
        (tmp / "data" / name).write_text("SENTINEL\n")
    return tmp


def _run(tmp):
    return subprocess.run([PY, str(tmp / "analysis" / "parse_bills.py")],
                          cwd=tmp, capture_output=True, text=True)


# The whole publish set, in one place: every case that asserts "nothing was written"
# has to cover all of it, and bill_corpus_boundary.json belongs to the same atomic
# set as the six CSVs (it states which corpus they contain), so a rollback that left
# it behind would be exactly the drift it exists to prevent.
ARTIFACTS = ("bill_periods_electric.csv", "bill_periods_gas.csv", "bill_tou_detail.csv",
             "bill_gas_detail.csv", "electric_bill_summary.csv", "gas_bill_summary.csv",
             "bill_corpus_boundary.json")


def _artifacts_untouched(tmp):
    return all((tmp / "data" / n).read_text() == "SENTINEL\n" for n in ARTIFACTS)


def _statement_date(path):
    """The statement date a bill PDF's filename carries (the parser's convention)."""
    return re.search(r"(\d{4}-\d{2}-\d{2})\.pdf$", path.name).group(1)


def _rows(path):
    """Read a committed artifact CSV as a list of dicts (they are CRLF or LF)."""
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _patch_summary_lists(tmp, elec_dates, gas_dates):
    """Rewrite the throwaway copy's SUMMARY_STATEMENTS_* lists.

    A fork's situation is "the lists in the script describe a corpus other than the
    one on disk". Editing the lists in the throwaway copy produces exactly that
    state without having to fabricate a second bill corpus, and it is the same
    zero-overlap condition the parser tests."""
    script = tmp / "analysis" / "parse_bills.py"
    src = script.read_text()
    out = src
    for name, dates in (("SUMMARY_STATEMENTS_ELEC", elec_dates),
                        ("SUMMARY_STATEMENTS_GAS", gas_dates)):
        out, n = re.subn(rf"{name} = \[.*?\]",
                         f"{name} = {dates!r}", out, count=1, flags=re.S)
        assert n == 1, f"test needs updating: {name} assignment not found"
    assert out != src, "test needs updating: summary lists unchanged"
    script.write_text(out)


def case_fork_summary_built_from_own_corpus(tmp):
    """A fork whose corpus shares no statement date with the SUMMARY_STATEMENTS_*
    lists must still get a REAL summary. Filtering by another household's dates
    would select nothing and publish a header-only summary, silently discarding the
    billing summary just parsed. The fork's window is every statement it parsed, so
    the summary must cover exactly the periods in the periods artifact."""
    _patch_summary_lists(tmp, ["1900-01-01", "1900-02-01"], ["1900-01-15"])
    r = _run(tmp)
    assert r.returncode == 0, f"fork corpus failed to publish:\n{r.stderr}"
    assert "FULL parsed corpus" in r.stdout and "SUMMARY_STATEMENTS_ELEC" in r.stdout, \
        f"no notice naming the window actually used:\n{r.stdout}"

    periods = _rows(tmp / "data" / "bill_periods_electric.csv")
    summary = _rows(tmp / "data" / "electric_bill_summary.csv")
    assert summary, "fork published an EMPTY electric summary"
    assert len(summary) == len(periods), \
        f"summary covers {len(summary)} periods, corpus has {len(periods)}"
    assert [s["period"] for s in summary] == [p["period"] for p in periods], \
        "summary periods are not the fork's own parsed periods"
    # The count in the notice must be the fork's own statement count, not the list's.
    n_stmts = len({p["statement_date"] for p in periods})
    assert f"{n_stmts} statement(s)" in r.stdout, \
        f"notice does not name the {n_stmts}-statement window used:\n{r.stdout}"

    if not (tmp / "private" / "1-raw-data" / "gas-bills").is_dir():
        return ("fork corpus -> electric summary built from its own statements "
                "(no gas corpus here)")
    gperiods = _rows(tmp / "data" / "bill_periods_gas.csv")
    gsummary = _rows(tmp / "data" / "gas_bill_summary.csv")
    assert gsummary, "fork published an EMPTY gas summary"
    assert len(gsummary) == len(gperiods), \
        f"gas summary covers {len(gsummary)} periods, corpus has {len(gperiods)}"
    assert {g["file_month"] for g in gsummary} == {p["period_end_month"] for p in gperiods}, \
        "gas summary months are not the fork's own parsed months"
    assert "SUMMARY_STATEMENTS_GAS" in r.stdout, f"no gas window notice:\n{r.stdout}"
    return "fork corpus -> both summaries built from its own statements, non-empty"


def case_partial_overlap_corpus_fails(tmp):
    """End-to-end counterpart of the fork case: PARTIAL overlap is corpus loss, not a
    fork, so the run must fail closed and publish nothing — the fork path must never
    become an escape hatch for a thinned corpus."""
    present = _require(
        tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2026-02-02.pdf")
    _patch_summary_lists(tmp, [_statement_date(present), "1900-01-01"], ["1900-01-15"])
    r = _run(tmp)
    assert r.returncode != 0, "partial overlap with the summary list was accepted"
    assert "missing from the corpus" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "partial overlap (end to end) -> exits, artifacts untouched"


def case_healthy_corpus(tmp):
    """Control: the real corpus must parse and write all six artifacts."""
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


def case_common_mode_rate_misread_caught_by_charge_crossfoot(tmp):
    """Issue #27: prove the charge-line cross-foot actually catches the failure
    mode it was built for, not just a plausible-sounding one.

    delivery/summer/on_peak's $0.26438 rate is printed, unchanged, on five 2024
    statements — a real repeated historical vintage. rates_history.py's holdout
    gate corroborates a printed rate by checking whether OTHER statements' printed
    rates agree; test_rates_history.py's
    case_a_common_mode_shift_of_one_repeated_vintage_is_invisible_to_the_holdout
    proves that gate is BLIND to a parser bug that shifts every occurrence of this
    exact vintage by the same amount — every witness would carry the identical
    wrong value and agree with itself.

    Here we inject that exact bug into the extraction (every rate_per_kwh read as
    literal 0.26438 gets bumped +$0.05, simulating a systematic misread of one
    printed digit sequence) and confirm parse_bills.py now refuses — on the FIRST
    occurrence, in the FIRST statement, using nothing from any other statement:
    the cross-foot checks this block's own printed "Charge $a + $b + $c = total"
    line, which the injected bug never touches."""
    victim = _require(
        tmp / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_2024-06-27.pdf")
    src = (tmp / "analysis" / "parse_bills.py").read_text()
    needle = "kwh_j, rate_j = _f(u.group(1 + j)), _f(r_row.group(1 + j))"
    assert needle in src, "test needs updating: extraction line not found"
    patched = src.replace(
        needle,
        needle + "\n                if abs(rate_j - 0.26438) < 1e-9:\n"
                  "                    rate_j += 0.05  # simulated common-mode misread"
                  " (issue #27)",
        1)
    assert patched != src, "test needs updating: patch did not apply"
    (tmp / "analysis" / "parse_bills.py").write_text(patched)
    r = _run(tmp)
    assert r.returncode != 0, \
        f"parser accepted a corpus with a common-mode-shifted printed rate:\n{r.stdout}"
    assert victim.name in r.stderr, \
        f"error does not name the first corrupted statement, {victim.name}:\n{r.stderr}"
    assert "printed charge line" in r.stderr and "disagree" in r.stderr, \
        f"error is not the charge-line cross-foot:\n{r.stderr}"
    assert "0.31438" in r.stderr, f"error does not show the shifted rate:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return ("common-mode +$0.05/kWh shift of a 5-statement-repeated vintage -> "
            "caught on the FIRST occurrence by the charge-line cross-foot: "
            + r.stderr.strip().splitlines()[-1])


def case_charge_line_missing_fails_closed(tmp):
    """Issue #27 review: the cross-foot's OWN precondition -- a printed
    '[N Days ]Charge $a + $b + $c = total' line after every Rate/kWh row -- is
    itself a new fail-closed branch this PR added. Prove it actually fires
    rather than trusting the raise is reachable: break the regex that finds
    the charge line (a bill-layout-changed scenario, the same shape as
    case_tou_headers_stop_matching two cases up) and confirm the parser
    refuses naming the missing line, not a downstream crash."""
    src = (tmp / "analysis" / "parse_bills.py").read_text()
    needle = r"Charge\s*\$("
    assert src.count(needle) == 1, "test needs updating: charge-line anchor not found once"
    broken = src.replace(needle, r"ChargeRENAMED\s*\$(", 1)
    assert broken != src, "test needs updating: patch did not apply"
    (tmp / "analysis" / "parse_bills.py").write_text(broken)
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus with no charge line at all"
    assert "no '[N Days ]Charge $a + $b + $c = total' line" in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "charge line missing after a Rate/kWh row -> exits, artifacts untouched"


def case_charge_line_internal_crossfoot_fails_closed(tmp):
    """Issue #27 review: the charge line's OWN internal identity (its three
    printed dollar amounts must sum to its own printed total) is a second new
    fail-closed branch this PR added, independent of the rate x kWh
    cross-foot the other test exercises. Simulate a misaligned column read
    (the printed $a/$b/$c parsed correctly but summing wrong, e.g. a
    misplaced decimal) and confirm the parser refuses on the internal
    identity, not the rate cross-foot."""
    src = (tmp / "analysis" / "parse_bills.py").read_text()
    needle = "            charges = [_f(c_row.group(i)) for i in (2, 3, 4)]"
    assert needle in src, "test needs updating: charges extraction line not found"
    patched = src.replace(
        needle,
        needle + "\n            charges[0] += 1.0  # simulated column misalignment"
                  " (issue #27 review)",
        1)
    assert patched != src, "test needs updating: patch did not apply"
    (tmp / "analysis" / "parse_bills.py").write_text(patched)
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a corpus whose charge line doesn't sum"
    assert "misaligned or misread" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return ("charge line's own $a+$b+$c disagrees with its printed total -> "
            "exits on the internal cross-foot, artifacts untouched")


def case_missing_household_yaml_fails(tmp):
    """parse_bills now REQUIRES the intake yaml (household.has_gas): without it the
    loader must fail closed pointing at the intake interview, touching nothing."""
    (tmp / "private" / "household.yaml").unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser ran without private/household.yaml"
    assert "household.yaml" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "missing household.yaml -> exits, artifacts untouched"


def case_gas_flag_true_missing_dir_fails(tmp):
    """household.has_gas true with NO gas-bills/ directory is staging loss, never a
    no-gas household: the run must fail closed NAMING THE FLAG and touch nothing.
    (Directory-presence inference is gone — a missing dir proves nothing.)"""
    _set_flag(tmp, True)
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    if gasdir.exists():
        shutil.rmtree(gasdir)
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted a missing gas-bills/ despite has_gas: true"
    assert "household.has_gas is true" in r.stderr and "staging loss" in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "flag true + missing gas-bills/ -> exits naming the flag, artifacts untouched"


def case_gas_flag_true_empty_dir_fails(tmp):
    """household.has_gas true with an EMPTY gas-bills/ is corpus loss: fail closed,
    touch nothing."""
    _set_flag(tmp, True)
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    gasdir.mkdir(parents=True, exist_ok=True)
    for f in gasdir.glob("*.pdf"):
        f.unlink()
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted an empty gas-bills/ despite has_gas: true"
    assert "household.has_gas is true" in r.stderr and "corpus loss" in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "flag true + empty gas-bills/ -> exits (corpus loss), artifacts untouched"


def case_gas_flag_false_retires_gas_artifacts(tmp):
    """household.has_gas false with no gas-bills/ dir: the run must succeed, notice
    loudly, write the electric artifacts, and RETIRE the gas artifacts to header-only
    CSVs in the same publish set — never leave another corpus's stale gas data (the
    sentinels here) in place."""
    _set_flag(tmp, False)
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    if gasdir.exists():
        shutil.rmtree(gasdir)
    r = _run(tmp)
    assert r.returncode == 0, f"has_gas-false run failed:\n{r.stderr}"
    assert "household.has_gas is false" in r.stdout and "header-only" in r.stdout, \
        f"missing the loud retirement notice:\n{r.stdout}"
    for n in ("bill_periods_electric.csv", "bill_tou_detail.csv",
              "electric_bill_summary.csv"):
        assert (tmp / "data" / n).read_text() != "SENTINEL\n", \
            f"electric artifact {n} was not written"
    # The stale (sentinel) gas artifacts must be REPLACED by header-only CSVs with
    # exactly the real artifacts' schemas and line endings.
    assert (tmp / "data" / "bill_periods_gas.csv").read_bytes() == (
        b"statement_date,period,period_end_month,therms,total_gas_service,"
        b"billed_amount,baseline_rate,nonbaseline_rate,baseline_allowance_therms,"
        b"gas_energy_charge_rate,other_fees_rate\n"), \
        "bill_periods_gas.csv is not the expected header-only CSV"
    assert (tmp / "data" / "bill_gas_detail.csv").read_bytes() == (
        b"statement_date,period,charge_type,segment,segment_days,segment_therms,"
        b"baseline_rate,nonbaseline_rate,energy_rate,other_fees_rate\n"), \
        "bill_gas_detail.csv is not the expected header-only CSV"
    assert (tmp / "data" / "gas_bill_summary.csv").read_bytes() == (
        b"file_month,therms,total_gas_service,baseline_rate,nonbaseline_rate\r\n"), \
        "gas_bill_summary.csv is not the expected header-only CSV"
    return "flag false + no gas dir -> electric published, gas retired to header-only"


def case_gas_flag_false_with_dir_present_fails(tmp):
    """household.has_gas false while a gas-bills/ directory EXISTS is a contradiction
    (wrong flag, or a directory that should not be there): fail closed telling the
    user to fix one or the other, touch nothing."""
    _set_flag(tmp, False)
    gasdir = tmp / "private" / "1-raw-data" / "gas-bills"
    gasdir.mkdir(parents=True, exist_ok=True)   # presence alone is the contradiction
    r = _run(tmp)
    assert r.returncode != 0, "parser accepted gas-bills/ present despite has_gas: false"
    assert "household.has_gas is false" in r.stderr and "contradiction" in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "flag false + gas-bills/ present -> exits (contradiction), artifacts untouched"


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


# --- fixed_charge_total reconciliation (issue #7) ---------------------------------
#
# SDG&E replaced the flat "Monthly Service Fee" ($16.00/month) with a per-day "Base
# Services Charge" ($0.79343/day) at the 2025-10-01 billing boundary. Neither shape
# below occurs in the real corpus (no period has EVER printed neither label, and the
# one-way transition means no period has ever printed both), so both cases are
# exercised directly against parse_electric() with a synthetic statement text rather
# than a real (or deleted) PDF — there is no real PDF whose deletion would produce
# either shape.
_SYNTHETIC_BASE = (
    "Billing Period: 1/1/24 - 1/31/24 Total Days: 31\n"
    "Total Usage: 500\n"
    "Non Bypassable Charges Usage: 500\n"
    "Total Electric Service $100.00\n"
)


def case_neither_fixed_charge_label_present_fails():
    """A period naming NEITHER 'Base Services Charge' nor 'Monthly Service Fee' is a
    real gap — a layout change, or a tariff regime this parser has never seen — so
    the fixed-charge floor cannot be computed. The run must refuse rather than emit a
    silent zero or NaN for fixed_charge_total."""
    sys.path.insert(0, str(HERE))
    import unittest.mock as mock
    import parse_bills as pb
    with mock.patch.object(pb, "_text", return_value=_SYNTHETIC_BASE):
        try:
            pb.parse_electric(pathlib.Path("sdge_electric_2024-01-01.pdf"))
        except SystemExit as e:
            assert ("neither a 'Base Services Charge' nor a 'Monthly Service Fee'"
                    in str(e)), f"wrong error: {e}"
        else:
            raise AssertionError(
                "a period with neither fixed-charge label present was accepted")
    return ("neither Base Services Charge nor Monthly Service Fee present -> "
            "parse_electric refuses")


def case_both_fixed_charge_labels_present_prefers_bsc():
    """A period printing BOTH labels never happens in this corpus (the transition is
    a one-way, one-time swap), but a malformed statement could produce it. Decided
    behavior: deterministically prefer Base Services Charge — the tariff currently in
    force — matching the fallback order bill_decomposition.py already uses
    (`lines.get("base_services_charge", lines.get("monthly_service_fee"))`) — rather
    than leaving the case unhandled."""
    sys.path.insert(0, str(HERE))
    import unittest.mock as mock
    import parse_bills as pb
    txt = (_SYNTHETIC_BASE
           + "Base Services Charge $.79343 x 31 days 24.60\n"
           + "Monthly Service Fee 16.00\n")
    with mock.patch.object(pb, "_text", return_value=txt):
        rows, _ = pb.parse_electric(pathlib.Path("sdge_electric_2024-01-01.pdf"))
    assert len(rows) == 1, f"expected exactly one period, got {len(rows)}"
    row = rows[0]
    assert row["base_services_charge"] == 24.60, row
    assert row["monthly_service_fee"] == 16.00, row
    assert row["fixed_charge_total"] == 24.60, \
        f"Base Services Charge did not win the tie-break deterministically: {row}"
    return ("both fixed-charge labels present -> Base Services Charge wins "
            "deterministically")


# --- Gas rate extraction (issue #98) -----------------------------------------------
#
# All three cases below run against the REAL corpus (private/1-raw-data/gas-bills/),
# not synthetic fixtures: the real 25-bill corpus already covers every case named in
# the issue (single-tier, multi-segment Gas Service, multi-segment Gas Energy
# Charge), so there is no need to invent bill text. Expected values are hand-computed
# from the PDF's own printed "Rate/Therm"/day-split lines, quoted in each docstring.

def case_gas_baseline_rate_previously_blank_now_populated(tmp):
    """Issue #98: fixes the pre-existing bug where a single global two-$-value regex
    (r"Rate/Therm\\s+\\$NUM\\s+\\$NUM") left baseline_rate/nonbaseline_rate BLANK on a
    period whose Gas Service never crossed its baseline allowance -- every segment's
    own "Rate/Therm" line then carries only ONE $ value, which the old two-value
    regex never matched anywhere in the statement.

    sdge_gas_2025-07-30.pdf's period is exactly this case (confirmed against the
    committed data/bill_periods_gas.csv before this fix: baseline_rate and
    nonbaseline_rate were both blank for statement_date 2025-07-30). Its Gas Service
    prints two single-tier segments: "4 of 32 Days" at $2.04568/therm, "28 of 32
    Days" at $2.03321/therm (read directly off the PDF text) -- day-weighted blend
    (4*2.04568 + 28*2.03321) / 32 = 2.03477."""
    _require(tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2025-07-30.pdf")
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_gas.csv")
    row = next(p for p in periods if p["statement_date"] == "2025-07-30")
    expect_baseline = (4 * 2.04568 + 28 * 2.03321) / 32
    assert abs(float(row["baseline_rate"]) - expect_baseline) < 0.00001, row
    assert row["nonbaseline_rate"] == "", \
        f"period never crossed its baseline allowance; nonbaseline_rate should " \
        f"stay blank, not a hand-invented number: {row}"
    assert row["baseline_allowance_therms"] == "11.0", row
    return "2025-07-30 (previously blank baseline_rate) -> now day-weighted blended"


def case_gas_service_multi_segment_day_weighted_blend(tmp):
    """Issue #98: fixes the pre-existing bug where the same global regex matched only
    the FIRST Gas Service rate segment when a mid-cycle rate change split a period
    into two TWO-VALUE (baseline/non-baseline) segments -- the second segment's rate
    was silently dropped.

    sdge_gas_2025-01-29.pdf splits Gas Service on day 6 of a 32-day period: "5 of 32
    Days" at baseline $1.56901/nonbaseline $1.87417/therm, "27 of 32 Days" at
    baseline $1.61980/nonbaseline $1.91783/therm (read directly off the PDF text).
    The committed artifact before this fix carried only the FIRST segment's rate
    (1.56901/1.87417) for the whole period."""
    _require(tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2025-01-29.pdf")
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_gas.csv")
    row = next(p for p in periods if p["statement_date"] == "2025-01-29")
    expect_baseline = (5 * 1.56901 + 27 * 1.61980) / 32
    expect_nonbaseline = (5 * 1.87417 + 27 * 1.91783) / 32
    assert abs(float(row["baseline_rate"]) - expect_baseline) < 0.00001, row
    assert abs(float(row["nonbaseline_rate"]) - expect_nonbaseline) < 0.00001, row
    assert row["baseline_rate"] != "1.56901", \
        "regression: baseline_rate is the FIRST segment only, second segment dropped"
    assert row["baseline_allowance_therms"] == "39.0", row
    return ("2025-01-29 (previously first-segment-only) -> now day-weighted across "
            "both Gas Service segments")


def case_gas_energy_charge_multi_segment_and_detail_schema(tmp):
    """Issue #98: the flat, untiered Gas Energy Charge (a SEPARATE line item from the
    two-tier Gas Service rate, priced on every therm regardless of baseline/
    non-baseline) was not extracted into any committed artifact at all before this
    fix. Verifies both the period-level day-weighted blend
    (bill_periods_gas.csv's gas_energy_charge_rate) and the new long-format detail
    (bill_gas_detail.csv), including that each charge type's own segment day counts
    sum to the period's real calendar days.

    sdge_gas_2025-07-30.pdf's own printed text: Gas Energy Charge splits on day 5 of
    its 32-day period -- "4 of 32 Days" at $.33603/therm, "28 of 32 Days" at
    $.40247/therm; Gas Service splits on the SAME day into two single-tier segments
    (4 days at $2.04568/therm, 28 days at $2.03321/therm) -- confirming the two
    charge types split on the identical day here while still being extracted as
    independent segment schedules."""
    _require(tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2025-07-30.pdf")
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_gas.csv")
    row = next(p for p in periods if p["statement_date"] == "2025-07-30")
    expect_energy = (4 * 0.33603 + 28 * 0.40247) / 32
    assert abs(float(row["gas_energy_charge_rate"]) - expect_energy) < 0.00001, row

    detail = _rows(tmp / "data" / "bill_gas_detail.csv")
    ge = sorted((d for d in detail if d["statement_date"] == "2025-07-30"
                 and d["charge_type"] == "gas_energy"), key=lambda d: int(d["segment"]))
    assert len(ge) == 2, f"expected 2 gas_energy segments, got {ge}"
    assert ge[0]["segment_days"] == "4" and ge[0]["energy_rate"] == "0.33603", ge[0]
    assert ge[1]["segment_days"] == "28" and ge[1]["energy_rate"] == "0.40247", ge[1]
    assert ge[0]["baseline_rate"] == "" and ge[0]["nonbaseline_rate"] == "", \
        f"gas_energy rows must leave Gas Service columns empty: {ge[0]}"

    gs = sorted((d for d in detail if d["statement_date"] == "2025-07-30"
                 and d["charge_type"] == "gas_service"), key=lambda d: int(d["segment"]))
    assert len(gs) == 2, f"expected 2 gas_service segments, got {gs}"
    assert gs[0]["segment_days"] == "4" and gs[0]["baseline_rate"] == "2.04568", gs[0]
    assert gs[1]["segment_days"] == "28" and gs[1]["baseline_rate"] == "2.03321", gs[1]
    assert gs[0]["energy_rate"] == "" and gs[1]["nonbaseline_rate"] == "", \
        f"gas_service rows must leave Gas Energy Charge's column empty: {gs}"

    total_gs_days = sum(int(d["segment_days"]) for d in gs)
    total_ge_days = sum(int(d["segment_days"]) for d in ge)
    assert total_gs_days == 32, \
        f"gas_service segment days sum to {total_gs_days}, not the period's 32 days"
    assert total_ge_days == 32, \
        f"gas_energy segment days sum to {total_ge_days}, not the period's 32 days"
    return ("2025-07-30 gas_energy + gas_service segments -> bill_gas_detail.csv "
           "schema and day counts verified against the real PDF text")


def case_other_fees_multi_segment_therm_weighted_blend(tmp):
    """Codex review, issue #98, pass 1: Public Purpose Programs and the State
    Regulatory Fee are a further flat, untiered $/therm charge omitted from the
    first draft of this fix entirely -- baseline_rate + nonbaseline_rate +
    energy_rate alone do not reproduce total_gas_service. Unlike Gas Service/Gas
    Energy Charge, a mid-cycle rate change here splits by THERM COUNT, not days.

    sdge_gas_2025-01-29.pdf's own printed text (97 total therms): 'Public Purpose
    Programs 15 Therms x $.101330 1.52' then 'Public Purpose Programs 82 Therms x
    $.115410 9.46'; 'State Regulatory Fee 15 Therms x $.001000 .02' then '82
    Therms x $.002500 .21' -- the SAME 15/82 therm split for both fees. Combined
    per segment: .101330+.001000=.10233 (15 therms), .115410+.002500=.11791 (82
    therms)."""
    _require(tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2025-01-29.pdf")
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_gas.csv")
    row = next(p for p in periods if p["statement_date"] == "2025-01-29")
    expect_blend = (15 * 0.10233 + 82 * 0.11791) / 97
    assert abs(float(row["other_fees_rate"]) - expect_blend) < 0.00001, row

    detail = _rows(tmp / "data" / "bill_gas_detail.csv")
    of = sorted((d for d in detail if d["statement_date"] == "2025-01-29"
                 and d["charge_type"] == "other_fees"), key=lambda d: int(d["segment"]))
    assert len(of) == 2, f"expected 2 other_fees segments, got {of}"
    assert of[0]["segment_therms"] == "15" and of[0]["other_fees_rate"] == "0.10233", of[0]
    assert of[1]["segment_therms"] == "82" and of[1]["other_fees_rate"] == "0.11791", of[1]
    assert of[0]["segment_days"] == "" and of[0]["baseline_rate"] == "" and \
        of[0]["energy_rate"] == "", \
        f"other_fees rows must leave the day-based charge types' columns empty: {of[0]}"
    total_of_therms = sum(int(d["segment_therms"]) for d in of)
    assert total_of_therms == 97, \
        f"other_fees segment therms sum to {total_of_therms}, not the period's 97 therms"
    return ("2025-01-29 other_fees (Public Purpose Programs + State Regulatory "
           "Fee) segments -> therm-weighted blend and bill_gas_detail.csv schema "
           "verified against the real PDF text")


def case_gas_rate_misread_caught_by_gas_charge_crossfoot(tmp):
    """Issue #98 review: _gas_segments()'s cross-foot (each segment's own
    printed 'Rate/Therm' x 'Therms used' must reproduce its own printed
    '[N of M Days ]Charge ... = total' line) mirrors the electric parser's
    issue #27 cross-foot, but had no negative test proving it actually
    fires -- unlike the electric side's two dedicated cases. $1.55659/therm
    is a real Gas Service baseline rate printed, unchanged, on multiple
    2024 statements (a repeated vintage, same shape as the electric
    common-mode test). Inject a systematic +$0.05 misread of that exact
    value and confirm parse_bills.py refuses on the FIRST occurrence,
    using nothing from any other statement -- the cross-foot checks this
    segment's own printed charge line, which the injected bug never
    touches."""
    victim = _require(
        tmp / "private" / "1-raw-data" / "gas-bills" / "sdge_gas_2024-07-29.pdf")
    src = (tmp / "analysis" / "parse_bills.py").read_text()
    needle = "rate1 = _f(m.group(3))"
    assert src.count(needle) == 1, "test needs updating: extraction line not found once"
    patched = src.replace(
        needle,
        needle + "\n        if abs(rate1 - 1.55659) < 1e-9:\n"
                  "            rate1 += 0.05  # simulated common-mode misread (issue #98)",
        1)
    assert patched != src, "test needs updating: patch did not apply"
    (tmp / "analysis" / "parse_bills.py").write_text(patched)
    r = _run(tmp)
    assert r.returncode != 0, \
        f"parser accepted a corpus with a misread gas rate:\n{r.stdout}"
    assert victim.name in r.stderr, \
        f"error does not name the first corrupted statement, {victim.name}:\n{r.stderr}"
    assert "printed charge says" in r.stderr, \
        f"error is not the gas charge-line cross-foot:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return ("common-mode +$0.05/therm shift of a repeated Gas Service rate -> "
            "caught on the FIRST occurrence by _gas_segments()'s charge-line "
            "cross-foot: " + r.stderr.strip().splitlines()[-1])


def case_fixed_charge_total_reconciles_real_statements(tmp):
    """End-to-end proof against the real corpus: fixed_charge_total must equal the
    correct label's REAL dollar amount on both sides of the 2025-10-01 transition —
    the flat Monthly Service Fee before it (sdge_electric_2025-09-02.pdf: $16.00),
    the per-day Base Services Charge after it (sdge_electric_2025-12-03.pdf: $23.01,
    $.79343 x 29 days) — read directly off those statements, not fixtures."""
    _require(tmp / "private" / "1-raw-data" / "electric-bills"
             / "sdge_electric_2025-09-02.pdf")
    _require(tmp / "private" / "1-raw-data" / "electric-bills"
             / "sdge_electric_2025-12-03.pdf")
    r = _run(tmp)
    assert r.returncode == 0, f"healthy corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_electric.csv")
    pre = next((p for p in periods if p["statement_date"] == "2025-09-02"), None)
    post = next((p for p in periods if p["statement_date"] == "2025-12-03"), None)
    assert pre is not None, "2025-09-02 statement produced no period"
    assert post is not None, "2025-12-03 statement produced no period"
    assert pre["monthly_service_fee"] == "16.0", f"pre-transition row: {pre}"
    assert pre["base_services_charge"] == "", f"pre-transition row: {pre}"
    assert pre["fixed_charge_total"] == "16.0", f"pre-transition row: {pre}"
    assert post["base_services_charge"] == "23.01", f"post-transition row: {post}"
    assert post["monthly_service_fee"] == "", f"post-transition row: {post}"
    assert post["fixed_charge_total"] == "23.01", f"post-transition row: {post}"
    return "fixed_charge_total reconciles real pre- and post-transition statements"


# ---------------------------------------------------------------------------
# The corpus boundary (parse_bills.py, "THE CORPUS BOUNDARY")
#
# The boundary is DERIVED from the billing-history export, never written down as a
# date, so the pair of cases below is the real proof: the SAME corpus, the SAME
# statement, two exports — excluded under one, published under the other. A
# hardcoded cutoff would fail the second case, and a rule that never excludes
# anything would fail the first.
# ---------------------------------------------------------------------------
EXPORT_NAME = "electric_billing_history_2024-2026.csv"


def _corpus_dates(tmp):
    return sorted(_statement_date(p) for p in
                  (tmp / "private" / "1-raw-data" / "electric-bills").glob("*.pdf"))


def _stage_export(tmp, dates):
    """Write a synthetic billing-history export covering exactly `dates`.

    parse_bills.py reads only the statement_date column, but the real export's full
    header is written so the fixture cannot pass by being narrower than reality."""
    path = tmp / "private" / "1-raw-data" / EXPORT_NAME
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["statement_date", "billing_days", "current_charges",
                    "amount_due", "status"])
        for d in dates:
            w.writerow([d, 30, "0.00", "0.00", "Paid"])
    return path


def _stage_window(tmp, start, end):
    """The analysis window parse_bills.py reads for its day-coverage figures. A
    synthetic behavior_rebuild.json, so the coverage arithmetic is exercised against
    a window this test chose and can recompute independently."""
    (tmp / "data" / "behavior_rebuild.json").write_text(json.dumps(
        {"window": {"start": f"{start} 00:00:00", "end": f"{end} 23:45:00"}}))


def _summary_pinned(tmp, stmt):
    """True when `stmt` is one of the throwaway copy's SUMMARY_STATEMENTS_* dates —
    excluding one of those is corpus loss by presence-check 1, a different case."""
    return stmt in (tmp / "analysis" / "parse_bills.py").read_text()


def _period_bounds(period):
    s, e = period.split(" - ")
    return (dt.datetime.strptime(s.strip(), "%m/%d/%y").date(),
            dt.datetime.strptime(e.strip(), "%m/%d/%y").date())


def case_export_missing_a_statement_excludes_and_records_it(tmp):
    """A statement PDF the billing-history export does not cover is outside the
    reconcilable corpus: parse_bills must publish the rest, emit NONE of that
    statement's rows, say so on stdout, and record the exclusion — with its reason,
    its remedy and the resulting day-coverage shortfall — in the committed boundary
    artifact. Silence here is the CLAUDE.md §1 failure this whole rule exists to
    prevent (a hidden 27-day hole once understated the annual baseline ~9%)."""
    dates = _corpus_dates(tmp)
    victim = dates[-1]
    if len(dates) < 3 or _summary_pinned(tmp, victim):
        raise SkipCase("needs a newest statement outside the SUMMARY_STATEMENTS lists")
    _stage_export(tmp, dates[:-1])
    # A 365-day window ending on the excluded statement's own date, so it certainly
    # overlaps both the published corpus and the excluded period and the day-coverage
    # line has real numbers in it. The arithmetic itself is checked in
    # case_export_day_coverage_shortfall_is_a_number.
    w_end = dt.date.fromisoformat(victim)
    _stage_window(tmp, (w_end - dt.timedelta(days=364)).isoformat(), w_end.isoformat())

    r = _run(tmp)
    assert r.returncode == 0, f"excluding a statement failed the run:\n{r.stderr}"

    periods = _rows(tmp / "data" / "bill_periods_electric.csv")
    tou = _rows(tmp / "data" / "bill_tou_detail.csv")
    published = {p["statement_date"] for p in periods}
    assert victim not in published, f"{victim} was published despite no export row"
    assert published == set(dates[:-1]), \
        f"published set is not the export's own coverage: {sorted(published)}"
    assert victim not in {t["statement_date"] for t in tou}, \
        f"{victim} left TOU rows behind in bill_tou_detail.csv"

    b = json.loads((tmp / "data" / "bill_corpus_boundary.json").read_text())
    assert [e["statement_date"] for e in b["excluded_statements"]] == [victim], b
    rec = b["excluded_statements"][0]
    assert b["statements_parsed"] == len(dates), b
    assert b["statements_published"] == len(dates) - 1, b
    assert EXPORT_NAME in rec["reason"], rec
    assert "re-pulled" in rec["exclusion_ends_when"], rec
    assert b["export"]["statements"] == len(dates) - 1, b["export"]

    # The exclusion is announced, not merely recorded.
    assert "EXCLUDED" in r.stdout and victim in r.stdout, \
        f"the excluded statement is not named on stdout:\n{r.stdout}"
    assert "day coverage" in r.stdout, f"no day-coverage figure printed:\n{r.stdout}"
    v_start, v_end = _period_bounds(rec["periods"][0])
    assert rec["period_span"] == [v_start.isoformat(), v_end.isoformat()], rec
    return (f"a statement the export does not cover ({victim}) is excluded from every "
            f"artifact, announced on stdout, and recorded with its reason and remedy")


def case_export_covering_every_statement_publishes_every_statement(tmp):
    """The other half of the proof, and the reason the rule is derived rather than a
    date: the SAME corpus and the SAME statement as the case above, with an export
    that covers it, must publish it and record ZERO exclusions. Re-pulling the export
    is the whole remedy — nothing in the parser has to be edited."""
    dates = _corpus_dates(tmp)
    victim = dates[-1]
    if len(dates) < 3:
        raise SkipCase("needs a multi-statement corpus")
    _stage_export(tmp, dates)                      # the export has caught up
    r = _run(tmp)
    assert r.returncode == 0, f"a fully-covered corpus failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_electric.csv")
    assert {p["statement_date"] for p in periods} == set(dates), \
        "an export covering every statement still narrowed the corpus"
    b = json.loads((tmp / "data" / "bill_corpus_boundary.json").read_text())
    assert b["excluded_statements"] == [], b
    assert b["statements_published"] == b["statements_parsed"] == len(dates), b
    assert "EXCLUDED" not in r.stdout, f"nothing was excluded but stdout says so:\n{r.stdout}"
    return (f"the same corpus with an export that covers {victim} publishes it and "
            f"records no exclusion — the boundary follows the export, not a date")


def case_export_day_coverage_shortfall_is_a_number(tmp):
    """CLAUDE.md §1: coverage is counted in DAYS, never in files. The boundary record
    must state how many of the analysis window's days the published corpus actually
    covers, which days are missing, and how many of them the excluded statement would
    supply — recomputed here from the published artifact and the staged window, not
    read back from the same helper that wrote them."""
    dates = _corpus_dates(tmp)
    victim = dates[-1]
    if len(dates) < 3 or _summary_pinned(tmp, victim):
        raise SkipCase("needs a newest statement outside the SUMMARY_STATEMENTS lists")
    _stage_export(tmp, dates[:-1])
    r = _run(tmp)                       # first pass: learn the excluded period's span
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp / "data" / "bill_corpus_boundary.json").read_text())
    assert [e["statement_date"] for e in b["excluded_statements"]] == [victim], (
        f"the export covers {len(dates) - 1} of {len(dates)} statements but the "
        f"boundary record excludes {[e['statement_date'] for e in b['excluded_statements']]}")
    ex_start, ex_end = (dt.date.fromisoformat(x)
                        for x in b["excluded_statements"][0]["period_span"])

    # A window ending part-way into the excluded statement's period: the shortfall is
    # then a genuine subset of that period, which a coverage figure that just counted
    # the excluded period's own length would get wrong.
    w_end = ex_start + dt.timedelta(days=(ex_end - ex_start).days // 2)
    w_start = w_end - dt.timedelta(days=364)
    _stage_window(tmp, w_start.isoformat(), w_end.isoformat())
    r = _run(tmp)
    assert r.returncode == 0, r.stderr
    b = json.loads((tmp / "data" / "bill_corpus_boundary.json").read_text())
    cov = b["window_coverage"]

    published = _rows(tmp / "data" / "bill_periods_electric.csv")
    bounds = [_period_bounds(p["period"]) for p in published]
    c_start, c_end = min(s for s, _ in bounds), max(e for _, e in bounds)
    window = [w_start + dt.timedelta(days=i) for i in range((w_end - w_start).days + 1)]
    covered = [d for d in window if c_start <= d <= c_end]
    missing = [d for d in window if not (c_start <= d <= c_end)]
    supplied = [d for d in missing if ex_start <= d <= ex_end]

    assert cov["window"] == [w_start.isoformat(), w_end.isoformat()], cov
    assert cov["window_days"] == len(window) == 365, cov
    assert cov["days_covered"] == len(covered), (cov, len(covered))
    assert cov["days_missing"] == len(missing), (cov, len(missing))
    assert cov["days_covered"] + cov["days_missing"] == cov["window_days"], cov
    assert cov["days_the_excluded_statements_would_supply"] == len(supplied), cov
    assert cov["missing_ranges"] == [[missing[0].isoformat(), missing[-1].isoformat()]], cov
    assert f"{cov['days_covered']} of the analysis window's {cov['window_days']} days" \
        in r.stdout, f"the day-coverage figure is not printed:\n{r.stdout}"
    return (f"the shortfall is published as a number: {cov['days_covered']}/"
            f"{cov['window_days']} days covered, {cov['days_missing']} missing, "
            f"{cov['days_the_excluded_statements_would_supply']} of them supplied by "
            f"the excluded statement")


def case_export_row_with_no_pdf_fails_closed(tmp):
    """The dangerous direction, and why it is NOT absorbed by narrowing the corpus:
    an export row is positive evidence that a statement exists. A corpus missing it
    would understate every total with nothing to reveal the hole, so the run must
    stop rather than publish."""
    dates = _corpus_dates(tmp)
    _stage_export(tmp, dates + ["2099-01-01"])
    r = _run(tmp)
    assert r.returncode != 0, "an export row with no PDF was accepted"
    assert "no PDF was parsed" in r.stderr and "2099-01-01" in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "export row with no PDF -> exits, artifacts untouched"


def case_export_hole_in_the_middle_fails_closed(tmp):
    """An export hole INSIDE the corpus cannot be resolved by narrowing it: dropping
    the statement would punch a gap into an otherwise contiguous published series.
    Fail closed with that diagnosis, rather than leaving the operator to read
    continuity check 3's 'a statement is missing from the corpus'."""
    dates = _corpus_dates(tmp)
    if len(dates) < 3:
        raise SkipCase("needs at least three statements to have an interior one")
    victim = dates[len(dates) // 2]
    _stage_export(tmp, [d for d in dates if d != victim])
    r = _run(tmp)
    assert r.returncode != 0, "an interior export hole was accepted"
    assert "hole in the MIDDLE" in r.stderr and victim in r.stderr, \
        f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return f"export hole at {victim} (interior) -> exits, artifacts untouched"


def case_export_sharing_no_statement_fails_closed(tmp):
    """An export documenting a different account would exclude the entire corpus.
    Publishing nothing at all is never the honest reading of "restrict deliberately",
    so this is a fail-closed case too."""
    _stage_export(tmp, ["1990-01-01", "1990-02-01"])
    r = _run(tmp)
    assert r.returncode != 0, "an export covering none of the corpus was accepted"
    assert "covers none of the" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "export sharing no statement with the corpus -> exits, artifacts untouched"


def case_no_export_publishes_the_whole_corpus(tmp):
    """No export staged: the boundary is underivable, so nothing may be excluded on a
    guess. Every parsed statement is published and the run says the boundary was not
    checked — the same "cannot check" vs "nothing to check" distinction
    bill_decomposition.py draws."""
    dates = _corpus_dates(tmp)
    assert not (tmp / "private" / "1-raw-data" / EXPORT_NAME).exists()
    r = _run(tmp)
    assert r.returncode == 0, f"a corpus with no export failed:\n{r.stderr}"
    periods = _rows(tmp / "data" / "bill_periods_electric.csv")
    assert {p["statement_date"] for p in periods} == set(dates), \
        "statements were dropped with no export to justify it"
    assert "not derivable" in r.stdout, f"no notice about the missing export:\n{r.stdout}"
    b = json.loads((tmp / "data" / "bill_corpus_boundary.json").read_text())
    assert b["export"] is None and b["excluded_statements"] == [], b
    assert b["statements_published"] == b["statements_parsed"] == len(dates), b
    return "no export staged -> whole corpus published, boundary recorded as underived"


# Cases needing the gitignored bill PDFs. Only these can be skipped.
CORPUS_CASES = [case_healthy_corpus, case_missing_summary_statement,
                case_mid_corpus_gap, case_mid_corpus_gas_gap,
                case_tou_headers_stop_matching,
                case_common_mode_rate_misread_caught_by_charge_crossfoot,
                case_charge_line_missing_fails_closed,
                case_charge_line_internal_crossfoot_fails_closed,
                case_missing_household_yaml_fails,
                case_gas_flag_true_missing_dir_fails,
                case_gas_flag_true_empty_dir_fails,
                case_gas_flag_false_retires_gas_artifacts,
                case_gas_flag_false_with_dir_present_fails,
                case_fork_summary_built_from_own_corpus,
                case_partial_overlap_corpus_fails,
                case_fixed_charge_total_reconciles_real_statements,
                case_gas_baseline_rate_previously_blank_now_populated,
                case_gas_service_multi_segment_day_weighted_blend,
                case_gas_energy_charge_multi_segment_and_detail_schema,
                case_other_fees_multi_segment_therm_weighted_blend,
                case_gas_rate_misread_caught_by_gas_charge_crossfoot,
                case_export_missing_a_statement_excludes_and_records_it,
                case_export_covering_every_statement_publishes_every_statement,
                case_export_day_coverage_shortfall_is_a_number,
                case_export_row_with_no_pdf_fails_closed,
                case_export_hole_in_the_middle_fails_closed,
                case_export_sharing_no_statement_fails_closed,
                case_no_export_publishes_the_whole_corpus]

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
                    case_partial_overlap_still_fails,
                    case_neither_fixed_charge_label_present_fails,
                    case_both_fixed_charge_labels_present_prefers_bsc]


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
