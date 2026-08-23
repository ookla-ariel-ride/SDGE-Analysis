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
import contextlib
import csv
import datetime as dt
import errno
import fcntl
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
ELEC = ROOT / "private" / "1-raw-data" / "electric-bills"
GAS = ROOT / "private" / "1-raw-data" / "gas-bills"
PY = sys.executable
# Distinctive prefix for every throwaway repo this suite builds under _build(): a
# stranded one (killed process, exception that escaped the `with`) is greppable in
# $TMPDIR instead of looking like any other program's `tmp*` (issue #187 — three such
# copies, 787 files each, were found with no way to tell whose they were).
SANDBOX_PREFIX = "sdge-parse-bills-"
# A marker file inside every sandbox this suite builds, flock'd exclusively
# for the sandbox's whole lifetime. _sweep_stale_sandboxes() tries a
# NON-BLOCKING lock on each candidate's marker before removing it: the OS
# releases a process's flocks the instant it exits, crash or not, so a
# marker that locks cleanly proves nobody is using that sandbox any more,
# and one that refuses proves a sibling run still is. Without this, two
# overlapping invocations of this suite could have the later one's sweep
# delete the earlier one's still-live sandbox out from under it -- a race
# this sweep would introduce, not fix (issue #187 follow-up).
# A candidate carrying NO marker proves nothing either way: it is equally a
# sibling between TemporaryDirectory() and its own lock, so the sweep reports
# it and leaves it alone rather than creating the marker itself.
SANDBOX_MARKER = ".sandbox.lock"
# The ONLY errnos that mean "a live sibling really does hold this lock". flock
# with LOCK_NB reports contention as EWOULDBLOCK/EAGAIN (Python raises
# BlockingIOError, an OSError subclass, for those) -- and ONLY as those. Every
# other OSError, from the open() or from the flock(), means we could not
# establish liveness at all, which is a different verdict and must not be read
# as "someone else is using it". Tested as a SET because EWOULDBLOCK == EAGAIN
# on Linux and macOS but is not required to be equal everywhere.
_LOCK_CONTENTION_ERRNOS = frozenset((errno.EWOULDBLOCK, errno.EAGAIN))


class MarkerUnreadable(Exception):
    """_lock_marker() could not establish liveness AT ALL: the marker would not
    open (permissions, an I/O error), or flock() failed for a reason other than
    contention. Deliberately NOT the same signal as the None return, which
    means one specific, healthy thing -- a live sibling holds the lock.

    Collapsing the two is the defect this class exists to prevent (issue #187
    AC2). A sweep reading "unreadable" as "in use" skips an abandoned sandbox
    SILENTLY, on this run and every future one, while it holds a full copy of
    the real bill PDFs -- neither removed nor reported, which is the one
    outcome AC2 forbids. Callers must decide: an owner locking its OWN fresh
    marker treats this as a hard error, a sweep reports the candidate and moves
    on. Mirrors dry_run.py's MarkerUnreadable."""


def _discard_marker_temp(tmp_name):
    """Remove a marker that never reached its canonical name (see _lock_marker's
    create=True path). Best effort on purpose: the caller is already raising the
    real failure, and a leftover under this name is invisible to the sweep,
    which matches SANDBOX_MARKER exactly and nothing else.
    Mirrors dry_run.py's _discard_marker_temp."""
    try:
        os.unlink(tmp_name)
    except OSError:
        pass


def _lock_marker(sandbox_dir, create=True):
    """Non-blocking-exclusive-lock the marker inside `sandbox_dir`. Three
    outcomes, and the caller MUST be able to tell them apart:

      * the open file object holding the lock -- we won it;
      * None -- CONTENTION, and only contention: flock refused with
        EWOULDBLOCK/EAGAIN, which means a live sibling holds the lock. This is
        the normal, expected, healthy answer for a sweep, and the one case it
        may act on silently;
      * MarkerUnreadable -- liveness could not be established at all: the
        open() failed (permissions, I/O error, or a marker that vanished under
        create=False), or flock() failed with some other errno.

    Returning None for that third case is the issue #187 AC2 defect: a
    genuinely abandoned sandbox whose marker cannot be opened would be read as
    "a sibling has it" and skipped in silence, forever, while holding a copy of
    private data -- neither removed nor reported.

    `create` decides whether a MISSING marker is brought into existence. True
    (the default) is for a sandbox we own. False is mandatory for
    _sweep_stale_sandboxes(), which inspects directories it does NOT own:
    creating a marker there and then locking the file we just made is a
    trivially-won lock that proves nothing about the owner, and it leaves our
    litter behind. With create=False a missing marker fails the open, which is
    now a MarkerUnreadable rather than a None -- callers that expect a
    markerless candidate test for the file itself, before calling, and the
    raise covers only the narrow race where it disappears in between.

    A marker this call has to CREATE is published atomically, already locked.
    Making the file under its canonical name and locking it a moment later
    leaves a window -- between the open() and the flock() -- in which
    SANDBOX_MARKER exists and is FREE, which is exactly the state the sweep is
    built to read as "provably abandoned, remove it": a sibling sweeping in that
    instant wins the lock and recursively deletes a LIVE sandbox. So the file is
    built under a unique temporary name, flocked THERE, and only then linked
    onto SANDBOX_MARKER. A flock lives on the open file DESCRIPTION, not on the
    name, so it survives intact, and os.link() is both atomic and
    non-clobbering; the temporary name is dropped afterwards either way, and the
    sweep never sees it, matching SANDBOX_MARKER exactly and nothing else. The
    canonical name therefore only ever becomes visible already locked.

    A marker that is ALREADY THERE is opened and locked exactly as before,
    create or not: there is no publication window to close for a file this call
    did not create, and overwriting a live owner's marker would be a far worse
    bug than the one that closes. os.link() refusing to clobber is what makes
    that split safe rather than a check-then-act race -- if a sibling publishes
    between the test and the link, its marker stands and we fall through to
    locking THAT file, which is where genuine contention gets reported. Any
    other failure to establish our own marker is raised, not swallowed: an owner
    that cannot mark itself must fail loudly rather than run on unmarked and
    sweepable.
    Mirrors dry_run.py's Sandbox._lock_marker."""
    path = pathlib.Path(sandbox_dir) / SANDBOX_MARKER
    if create and not path.exists():
        try:
            raw, tmp_name = tempfile.mkstemp(prefix=SANDBOX_MARKER + ".new-",
                                             dir=str(sandbox_dir))
        except OSError as e:
            raise MarkerUnreadable(
                f"could not create a marker to publish as {path}: {e}") from e
        try:
            try:
                fd = os.fdopen(raw, "a+")
            except OSError as e:
                os.close(raw)
                raise MarkerUnreadable(
                    f"could not open the marker built for {path}: {e}") from e
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                fd.close()
                # A private, just-created temporary has nobody to contend with,
                # but the errno rule holds everywhere: only EWOULDBLOCK/EAGAIN
                # returns None, and an owner treats that answer as fatal
                # exactly as it treats a MarkerUnreadable.
                if e.errno in _LOCK_CONTENTION_ERRNOS:
                    return None
                raise MarkerUnreadable(
                    f"could not lock {tmp_name}, this run's own new marker "
                    f"for {path}: {e}") from e
            try:
                os.link(tmp_name, path)
            except OSError as e:
                fd.close()
                if e.errno != errno.EEXIST:
                    raise MarkerUnreadable(
                        f"could not publish {tmp_name} as {path}: {e}") from e
                # A sibling published between the test above and this link.
                # Its marker stands; fall through and lock THAT one.
            else:
                return fd         # published already locked, never unlocked
        finally:
            # On success the canonical link keeps the inode -- and this fd's
            # lock with it -- alive; on every failure path there is nothing to
            # keep. Either way the temporary name is finished.
            _discard_marker_temp(tmp_name)
    try:
        fd = open(path, "a+" if create else "r+")
    except OSError as e:
        raise MarkerUnreadable(f"could not open {path}: {e}") from e
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        fd.close()
        if e.errno in _LOCK_CONTENTION_ERRNOS:
            return None      # a live sibling holds it -- the healthy path
        raise MarkerUnreadable(f"could not lock {path}: {e}") from e
    return fd


# Sandbox path -> its held marker fd, populated only by _locked_sandbox and
# consulted only by _run(). Lets _run() pass that fd through to the
# parse_bills.py subprocess it launches (see _run's docstring) without
# threading a new parameter through this file's ~40 `_run(tmp)` call sites --
# every sandbox NOT built by _locked_sandbox (everything except the
# real-archive CORPUS_CASES path) is simply absent from this dict and _run()
# behaves exactly as before for it.
_ACTIVE_SANDBOX_MARKERS = {}


@contextlib.contextmanager
def _locked_sandbox(prefix):
    """A TemporaryDirectory that holds its own SANDBOX_MARKER locked for the
    whole block, including through its own removal, so a sibling process's
    _sweep_stale_sandboxes() sees it as still in use rather than deleting it
    out from under this run.

    Managed by hand rather than `with tempfile.TemporaryDirectory()`: that
    context manager's own __exit__ (the rmtree) would run AFTER this
    function's `finally` released the marker, reopening the exact TOCTOU
    window the marker exists to close (a sibling's sweep could lock the
    freed marker and start using this path a moment before removal
    completes)."""
    td_obj = tempfile.TemporaryDirectory(prefix=prefix)
    p = pathlib.Path(td_obj.name)
    # An owner has no use for the contention/unreadable distinction: BOTH mean
    # this run cannot hold its own marker, and both must fail exactly as loudly
    # as before. Only the sweep, which judges directories it does not own,
    # needs to tell them apart.
    try:
        marker_fd = _lock_marker(p)
    except MarkerUnreadable as e:
        td_obj.cleanup()
        raise RuntimeError(
            f"could not lock the sandbox's own marker file: {p} -- a "
            "freshly created, uniquely-named sandbox should never fail "
            f"this; refusing rather than running unmarked and sweepable. "
            f"Cause: {e}")
    if marker_fd is None:
        td_obj.cleanup()
        raise RuntimeError(
            f"could not lock the sandbox's own marker file: {p} -- a "
            "freshly created, uniquely-named sandbox should never fail "
            "this; refusing rather than running unmarked and sweepable.")
    _ACTIVE_SANDBOX_MARKERS[td_obj.name] = marker_fd
    try:
        yield td_obj.name
    finally:
        try:
            del _ACTIVE_SANDBOX_MARKERS[td_obj.name]
            td_obj.cleanup()
        finally:
            marker_fd.close()


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


class _Record(dict):
    """A parsed JSON object that names itself when a case reads a key it does not have.

    Every case below reads the artifact it is asserting about. A key the generator
    stopped writing is a REAL failure of that case, but as a bare KeyError it is the
    wrong kind: `KeyError: 'gas_corpus'` names neither the artifact nor the case, and
    before the runner learned to catch it, it escaped main() and took every case after
    it down with it — a traceback instead of one FAIL line, with the ~37 corpus cases
    that follow never running at all.

    So the sweep is done once, here, instead of by an `assert k in d` at every
    subscript: any missing key at any depth of any artifact loaded through _json()
    reports as an AssertionError naming the file, the key and the keys that ARE there.
    A case added tomorrow inherits it without a line of new wiring.

    `.get()` and `in` are deliberately untouched — __missing__ fires only on `[]` — so
    a case that tests for a genuinely optional key (`boundary_not_derived`,
    `window_coverage`) still gets None or False rather than a failure."""

    _where = "a JSON artifact"

    def __missing__(self, key):
        raise AssertionError(
            f"{self._where} carries no {key!r}; the record has {sorted(self)}. Either "
            f"the generator stopped writing that key or this case is reading the "
            f"wrong record — both are failures of this case, not of the harness.")


def _named(pairs, path):
    """A _Record that knows which file it was read out of."""
    d = _Record(pairs)
    d._where = str(path)
    return d


def _json(path):
    """json.loads(path.read_text()) with every missing key reported as an AssertionError
    naming the file (see _Record). Use this, never json.loads, for artifacts a case
    asserts about."""
    path = pathlib.Path(path)
    return json.loads(path.read_text(),
                      object_pairs_hook=lambda pairs: _named(pairs, path))


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
    # Pass the sandbox's marker fd through to the child, when one exists
    # (issue #187 follow-up): a flock is held by the OPEN FILE DESCRIPTION,
    # not the process, so a child that inherits this fd keeps a real-archive
    # sandbox looking "in use" to any sibling's sweep even if THIS process
    # is SIGKILLed while parse_bills.py is still running -- exactly the
    # crash shape #187 exists to survive. Every sandbox not built via
    # _locked_sandbox is simply absent from the registry, so this is a
    # no-op for the other ~40 callers of _run().
    marker_fd = _ACTIVE_SANDBOX_MARKERS.get(str(tmp))
    pass_fds = (marker_fd.fileno(),) if marker_fd is not None else ()
    return subprocess.run([PY, str(tmp / "analysis" / "parse_bills.py")],
                          cwd=tmp, capture_output=True, text=True,
                          pass_fds=pass_fds)


def _sweep_stale_sandboxes():
    """Remove any SANDBOX_PREFIX directory left in $TMPDIR by a prior run of this suite
    that never reached its own `with tempfile.TemporaryDirectory()` cleanup (killed
    process, an exception that somehow escaped it). Each one can hold a full copy of
    the real bill PDFs, so letting them accumulate silently is the bug (#187) — this
    runs once, at the top of main(), before this run creates its own sandbox. A
    removal failure is reported and the sweep moves on; someone else's leftover being
    unremovable is not a reason to fail THIS run before it has done anything.

    LIVENESS CHECK, before touching anything -- four outcomes, only one of
    which removes anything, and only one of which is silent:
      * marker present and WE CAN LOCK IT -> the owner's flock is gone, so the
        owner is gone: provably abandoned, remove it.
      * marker present but UNREADABLE (it will not open, or flock fails for any
        reason other than contention) -> liveness was never established. Not
        removed -- we cannot prove it is dead -- but reported to stderr naming
        the path and the cause. Silence here was the issue #187 AC2 defect: an
        abandoned copy of the real bill PDFs that is neither removed nor reported is
        indistinguishable from "nothing to do".
      * marker present and flock refuses with EWOULDBLOCK/EAGAIN -> a live
        sibling really does hold it (see _locked_sandbox): leave it alone,
        silently. This is the ONLY silent skip, because it is the only one
        that has actually established liveness.
      * NO marker at all -> unknowable, and never removed. A sibling caught
        between its own TemporaryDirectory(prefix=...) and its own
        _lock_marker() looks exactly like this, as does a pre-marker version of
        this suite; the candidate is reported to stderr and left in place. The
        sweep never creates a marker to lock (_lock_marker(create=False)) --
        locking a file we just made proves nothing about the owner, and it
        would litter a directory we do not own.
    Without this, an overlapping invocation of this suite could delete a
    SIBLING run's still-in-use sandbox, which is a race this sweep would
    introduce, not one it fixes."""
    for stale in pathlib.Path(tempfile.gettempdir()).glob(f"{SANDBOX_PREFIX}*"):
        if not stale.is_dir():
            continue
        if not (stale / SANDBOX_MARKER).exists():
            print(f"[stale sandbox candidate left in place: {stale} -- it "
                  f"carries no {SANDBOX_MARKER}, so it is indistinguishable "
                  "from a live run that has not marked itself yet; if no run "
                  "is in progress this is a prior run's leftover holding a "
                  "copy of the real bill PDFs, and you should delete it by "
                  "hand]", file=sys.stderr)
            continue
        # A marker we cannot even READ is not a live sibling. Skipping it
        # silently -- which is what collapsing every OSError into None used to
        # do -- leaves an abandoned copy of the real bill PDFs neither removed
        # nor reported, on this run and every future one (issue #187 AC2).
        # Report it in the same voice as the markerless case above; do NOT
        # remove it, since an unreadable marker is no proof of death either.
        try:
            marker_fd = _lock_marker(stale, create=False)
        except MarkerUnreadable as e:
            print(f"[stale sandbox candidate left in place: {stale} -- "
                  f"its {SANDBOX_MARKER} could not be read, so this run cannot "
                  "tell a live sibling from a prior run's leftover holding a "
                  f"copy of the real bill PDFs; fix the permissions or delete it by "
                  f"hand once no run is in progress. Cause: {e}]", file=sys.stderr)
            continue
        if marker_fd is None:
            continue  # a live sibling holds the lock -- not our leftover to take
        # Hold the lock THROUGH the removal: releasing it first would let a
        # process that is about to legitimately create a sandbox at this
        # exact path acquire the now-unlocked marker and start using it a
        # moment before we delete it out from under them (TOCTOU).
        try:
            shutil.rmtree(stale)
        except OSError as e:
            print(f"[stale sandbox not removed: {stale} ({e})]", file=sys.stderr)
        finally:
            marker_fd.close()


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
    """Read a committed artifact CSV as a list of dicts (they are CRLF or LF).

    Rows come back as _Record, for the same reason _json() does: a column a generator
    stopped writing is a failure of the case that reads it, and `KeyError: 'period'`
    names neither the CSV nor the case. Same shape, same treatment — 21 call sites
    swept at one."""
    with open(path, newline="") as fh:
        return [_named(dict(r), path) for r in csv.DictReader(fh)]


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


def _fail_copy2_at(fail_calls):
    """Return (patcher, counter) making shutil.copy2 raise on the given 1-based calls."""
    import unittest.mock as mock
    real = shutil.copy2
    state = {"n": 0}

    def flaky(src, dst, *a, **kw):
        state["n"] += 1
        if state["n"] in fail_calls:
            raise OSError(f"simulated shutil.copy2 failure #{state['n']}")
        return real(src, dst, *a, **kw)

    return mock.patch("shutil.copy2", flaky), state


def case_build_cleanup_survives_mid_copy_failure():
    """The sandbox _build() populates with real bill PDFs must not survive a crash
    partway through copying them in — that stranding is issue #187 itself: a process
    that dies mid-_build leaves a full PII copy sitting under $TMPDIR forever, because
    TemporaryDirectory only cleans up on a normal `with`-block exit. This proves the
    `with` block's cleanup fires on the EXCEPTION path too, not just the happy one, by
    forcing shutil.copy2 to fail after real PDF copying has already begun (call #4:
    the first two calls copy parse_bills.py/household.py, the third copies the first
    real PDF, so failing the fourth guarantees at least one real bill PDF was already
    on disk in the sandbox at the moment it dies)."""
    if not (ELEC.is_dir() and any(ELEC.glob("*.pdf"))):
        raise SkipCase("needs the gitignored bill PDFs; see DATA-SOURCES-CHEATSHEET.md §D")
    patcher, _ = _fail_copy2_at({4})
    sandbox_path = {}
    with patcher:
        try:
            with _locked_sandbox(SANDBOX_PREFIX) as td:
                sandbox_path["p"] = td
                _build(pathlib.Path(td))
        except OSError:
            pass
        else:
            raise AssertionError("simulated mid-copy failure did not propagate")
    assert not os.path.exists(sandbox_path["p"]), \
        f"sandbox holding real bill PDFs survived a mid-_build failure: {sandbox_path['p']}"
    return "exception mid-_build -> sandbox cleaned up, no stranded PDF copy"


def case_the_sweep_never_removes_a_sandbox_a_live_process_still_holds():
    """A prefix-matching directory alone is not proof of abandonment: two
    overlapping invocations of this suite both carry SANDBOX_PREFIX while
    both are legitimately alive. A sweep that removed on name alone could
    delete a SIBLING run's sandbox out from under it -- a race the sweep
    would introduce, not fix. Simulate a still-running sibling by holding its
    marker locked ourselves, exactly as _locked_sandbox would for its whole
    lifetime, then prove _sweep_stale_sandboxes leaves it alone. Needs no
    real bill PDFs -- the sweep and the marker never touch archive content."""
    live = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + "still-running-"))
    (live / "household.yaml").write_text("a live sibling's private data\n")
    held_fd = _lock_marker(live)
    assert held_fd is not None, "setup failed: could not lock the simulated sibling's marker"
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            _sweep_stale_sandboxes()
        assert live.is_dir(), (
            f"the sweep removed a sandbox whose marker was still locked "
            f"(a live sibling): {live}")
        assert (live / "household.yaml").is_file(), \
            "the sweep touched the contents of a still-in-use sibling sandbox"
        # The positive control for the unreadable-marker case below: genuine
        # contention (EWOULDBLOCK/EAGAIN) is the one liveness answer the sweep
        # has actually established, so it stays SILENT. Without this assert,
        # making every skip noisy would pass both cases.
        assert str(live) not in err.getvalue(), (
            "a live sibling holding its own lock is the normal, healthy path "
            "and must be skipped silently, but the sweep reported it: "
            f"{err.getvalue()!r}")
    finally:
        held_fd.close()
        shutil.rmtree(live, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory whose marker is still locked -- a live sibling "
            "run -- survives _sweep_stale_sandboxes untouched, and silently: real lock "
            "contention is the one liveness answer the sweep may act on without a word")


def _plant_abandoned_sandbox(tag):
    """A stale sandbox in the ONE state the sweep may act on: prefix-named,
    carrying a SANDBOX_MARKER whose lock nobody holds -- what a run killed
    AFTER it marked itself leaves behind. A MARKERLESS directory is
    deliberately not this shape (see the case below).

    Forcing a precondition means proving the forcing took, so both halves are
    asserted: the marker is there, and it is genuinely free. Without the second
    assert a case could pass because the sweep refused a LIVE-looking
    directory, which is not the behaviour it claims to test."""
    stale = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + tag))
    (stale / "a-bill.pdf").write_text("stranded statement copy\n")
    (stale / SANDBOX_MARKER).touch()
    assert (stale / SANDBOX_MARKER).is_file(), \
        f"setup failed: the planted stale sandbox carries no marker: {stale}"
    probe = _lock_marker(stale, create=False)
    assert probe is not None, (
        f"setup failed: the planted marker at {stale} could not be locked, so "
        "this fixture would look like a LIVE sibling and the case built on it "
        "would pass for the wrong reason")
    probe.close()
    return stale


def case_an_abandoned_sandbox_carrying_a_free_marker_is_swept():
    """The positive half of the pair: a directory whose marker exists and
    locks cleanly is PROVABLY abandoned -- the OS drops a process's flocks the
    instant it exits -- so the sweep must still remove it, private copies and
    all. Without this, the guard below could be satisfied by a sweep that
    removed nothing at all."""
    stale = _plant_abandoned_sandbox("abandoned-")
    try:
        _sweep_stale_sandboxes()
        assert not stale.exists(), (
            "a provably abandoned sandbox -- marker present, lock free -- was "
            f"not swept: {stale}")
    finally:
        shutil.rmtree(stale, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory whose marker exists and locks cleanly is "
            "proven abandoned and is removed by _sweep_stale_sandboxes")


def case_a_markerless_candidate_survives_the_sweep_and_is_reported():
    """The liveness test may never manufacture its own evidence. A directory
    carrying SANDBOX_PREFIX but NO marker is exactly what a live sibling looks
    like between its TemporaryDirectory(prefix=...) and its own _lock_marker()
    call, and what any pre-marker version of this suite looks like for its
    whole run. A sweep that CREATES the missing marker wins a lock against
    nobody, reads "abandoned", and deletes a live run's copy of the real bill
    PDFs. Prove such a candidate survives, is not marked by the sweep, and is
    reported on stderr instead (issue #187 AC2 accepts removed OR reported;
    reporting is the only honest verdict when the state is unknowable)."""
    live = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + "LIVE-mid-creation-"))
    (live / "a-bill.pdf").write_text("a live run's statement copy\n")
    assert not (live / SANDBOX_MARKER).exists(), \
        "setup failed: the planted directory already carries a marker"
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            _sweep_stale_sandboxes()
        assert live.is_dir(), (
            "the sweep deleted a prefixed directory carrying no marker -- which is "
            f"indistinguishable from a live run mid-creation: {live}")
        assert (live / "a-bill.pdf").is_file(), \
            "the sweep destroyed the contents of a live run's sandbox"
        assert not (live / SANDBOX_MARKER).exists(), (
            "the sweep created a marker inside a directory it does not own; that "
            "self-made, trivially-won lock is what makes a live sibling read as "
            "abandoned")
        assert str(live) in err.getvalue(), (
            "an unknowable candidate must be REPORTED, not silently skipped -- "
            f"stderr never named it: {err.getvalue()!r}")
    finally:
        shutil.rmtree(live, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory with no marker -- a live run between "
            "TemporaryDirectory() and its own lock -- survives the sweep unmarked "
            "and untouched, and is reported to stderr instead of removed")


def _plant_unopenable_marker_sandbox(tag):
    """A stale sandbox whose marker EXISTS but cannot be OPENED (mode 0o000).

    This is the liveness outcome that is neither of the other two: not "we won
    the lock" and not "a live sibling holds it", but "we could not establish
    liveness at all". Before the fix _lock_marker collapsed it into the same
    None a live sibling returns, so the sweep skipped such a candidate in
    SILENCE -- on that run and every future one -- while it held a copy of
    the real bill PDFs. Neither removed nor reported is the one outcome issue #187 AC2
    forbids.

    Not removed by the fixed sweep either, and deliberately so: an unreadable
    marker is no more proof of death than a missing one. The required behaviour
    is a REPORT.

    chmod 0o000 does not stop the file's owner from opening it when the process
    runs as root, and some filesystems ignore the mode outright, so the forcing
    is VERIFIED rather than assumed: if the open still succeeds this raises
    SkipCase instead of letting the case pass vacuously. Returns
    (sandbox, marker); the caller MUST restore the mode in a `finally`, so no
    case leaves an unreadable file behind."""
    stale = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + tag))
    (stale / "a-bill.pdf").write_text("stranded statement copy\n")
    marker = stale / SANDBOX_MARKER
    marker.touch()
    os.chmod(marker, 0o000)
    try:
        open(marker, "r+").close()
    except OSError:
        pass
    else:
        os.chmod(marker, 0o600)
        shutil.rmtree(stale, ignore_errors=True)
        raise SkipCase(
            "chmod 0o000 does not make a file unopenable here (running as "
            "root, or a filesystem that ignores the mode), so this case "
            "cannot force the unreadable-marker state it exists to test")
    return stale, marker


def case_an_unreadable_marker_is_reported_not_silently_skipped():
    """A candidate whose marker cannot be opened has told us NOTHING about
    whether its owner is alive, so treating that failure as "a sibling holds
    it" is a guess dressed as evidence -- and a silent one. Plant a sandbox
    holding a copy of the real bill PDFs whose marker is mode 0o000, and prove the sweep
    names it on stderr with the cause, rather than skipping it without a word
    (issue #187 AC2: removed OR reported, never neither).

    It must also SURVIVE: an unreadable marker is not proof of death, so
    deleting it would be the sibling-destroying race the marker exists to
    prevent, with a worse excuse."""
    stale, marker = _plant_unopenable_marker_sandbox("unreadable-marker-")
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            _sweep_stale_sandboxes()
        assert stale.is_dir(), (
            "the sweep deleted a candidate whose marker it could not even read "
            f"-- an unreadable marker is not proof the owner is dead: {stale}")
        assert (stale / "a-bill.pdf").is_file(), \
            "the sweep destroyed the contents of a candidate it could not read"
        assert str(stale) in err.getvalue(), (
            "a candidate whose marker cannot be read was skipped in SILENCE, "
            "which is indistinguishable from 'nothing to do', while it holds a "
            f"copy of the real bill PDFs: stderr was {err.getvalue()!r}")
        assert "could not be read" in err.getvalue(), (
            "the report must name the CAUSE as well as the path, or the reader "
            f"cannot tell it from the markerless case: {err.getvalue()!r}")
    finally:
        os.chmod(marker, 0o600)
        shutil.rmtree(stale, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory whose marker cannot be opened is "
            "reported to stderr by name and cause, and left in place -- never "
            "skipped in silence as though a live sibling held it")


def _canonical_marker_is_free(sandbox_dir, flock):
    """The SWEEP's verdict on `sandbox_dir`, taken right now and with the
    primitive operations rather than through _lock_marker -- because
    _lock_marker is the thing being observed. True means SANDBOX_MARKER both
    EXISTS and has a FREE lock, which is the one state the sweep removes on.

    The real fcntl.flock is passed in: the caller observes _lock_marker by
    replacing fcntl.flock, and a probe measuring through that replacement would
    recurse into the observer. Any lock this wins is released before it returns
    -- a probe may not alter what it measures."""
    marker = pathlib.Path(sandbox_dir) / SANDBOX_MARKER
    if not marker.exists():
        return False
    try:
        fh = open(marker, "r+")
    except OSError:
        return False
    try:
        flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False            # somebody holds it -- a sweep would skip it
    finally:
        fh.close()
    return True


def case_a_new_marker_is_never_visible_to_the_sweep_unlocked():
    """Creating the marker under its canonical name and locking it a moment
    later publishes a LIVE sandbox in the ABANDONED state for the instant in
    between: SANDBOX_MARKER on disk with a free lock is exactly what the sweep
    deletes on (case_an_abandoned_sandbox_carrying_a_free_marker_is_swept).
    A sibling sweeping in that window wins the lock and recursively removes a
    running sandbox and its copy of the real bill PDFs.
    Locking the missing-marker window shut closed the gap BEFORE the file
    exists; this is the gap immediately after it appears.

    _lock_marker(create=True) therefore locks a uniquely-named temporary first
    and renames it onto the canonical name -- a flock lives on the open file
    DESCRIPTION, not on the name, so it survives the rename and the canonical
    marker only ever becomes visible already locked.

    Observed at the one instant that separates the two implementations, the
    flock() call itself, and then confirmed from outside: an independent
    attempt on the published marker must report contention."""
    d = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + "atomic-marker-"))
    real_flock = fcntl.flock
    seen = {"calls": 0, "sweepable": None}

    def observing_flock(fd, op):
        # The old implementation had already created the canonical marker by
        # the time it reached here, and had not yet locked it.
        if seen["calls"] == 0:
            seen["sweepable"] = _canonical_marker_is_free(d, real_flock)
        seen["calls"] += 1
        return real_flock(fd, op)

    held = None
    try:
        fcntl.flock = observing_flock
        try:
            held = _lock_marker(d)
        finally:
            fcntl.flock = real_flock
        # Forcing a precondition means proving the forcing took: an observer
        # that never fired would make every assertion below vacuous.
        assert seen["calls"] >= 1, (
            "setup failed: _lock_marker never called fcntl.flock, so this case "
            "observed nothing")
        assert seen["sweepable"] is False, (
            f"{SANDBOX_MARKER} was on disk with a FREE lock while its owner "
            "was still acquiring it -- a sibling's sweep reads exactly that as "
            "'provably abandoned' and would delete this LIVE sandbox")
        assert held is not None, \
            "the owner did not win the lock on its own fresh marker"
        assert (d / SANDBOX_MARKER).is_file(), (
            f"_lock_marker returned without publishing {SANDBOX_MARKER}, so the "
            "sweep would read this live sandbox as unknowable forever")
        # From the outside, the way a sweep sees it: the published marker is
        # locked, so an independent attempt reports contention rather than
        # winning it.
        second = _lock_marker(d, create=False)
        if second is not None:
            second.close()
            raise AssertionError(
                "a second, independent attempt LOCKED the published marker, so "
                "the marker its owner published is not actually held -- the "
                "sweep would remove this sandbox")
        # A rename leaves nothing behind; a copy would. Nothing but the
        # canonical marker may remain, or the temporary itself becomes litter
        # inside every sandbox.
        leftovers = sorted(p.name for p in d.iterdir())
        assert leftovers == [SANDBOX_MARKER], (
            "the marker was not published by rename -- the sandbox holds more "
            f"than its canonical marker: {leftovers}")
    finally:
        if held is not None:
            held.close()
        shutil.rmtree(d, ignore_errors=True)
    return ("a sandbox's own marker is published atomically: the canonical "
            ".sandbox.lock never exists unlocked, so no sibling's sweep can "
            "read a live sandbox as abandoned and delete it")


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

    # ...and each fuel's notice must send the reader somewhere that answers it. The
    # notice is printed inside the per-fuel loop, so a pointer written for gas is
    # printed to electric readers too — and the boundary record has no electric
    # equivalent of summary_statements_presence_check, on purpose (electric's
    # completeness rests on the billing-history export). A pointer that resolves to
    # nothing is worse than none: the operator opens the file and leaves with less
    # than they arrived with.
    elec_notice, gas_notice = (
        [n for n in out.split("NOTICE:") if f"check 1 for {f}" in n][0]
        for f in ("electric", "gas"))
    assert "summary_statements_presence_check" in gas_notice, (
        f"the gas notice does not name the field that records the skip:\n{gas_notice}")
    assert "summary_statements_presence_check" not in elec_notice, (
        f"the electric notice sends an electric reader to the gas-only presence-check "
        f"record, which has no electric equivalent — a dead end:\n{elec_notice}")
    assert "billing-history" in elec_notice and "excluded_statements" in elec_notice, (
        f"the electric notice names no evidence an electric reader can actually "
        f"open:\n{elec_notice}")
    return ("fork corpus (zero overlap) -> check 1 skipped with loud notice, each "
            "fuel pointed at evidence that exists for it")


def case_boundary_record_requires_the_runs_own_gas_state():
    """_boundary_record() must not be callable without the run's gas state.

    _gas_corpus_scope() fails closed on (gas set, presence unset) because the record
    has to state what THIS run's check did. Defaults of None on `gas` and `presence`
    left a way under that guard: a caller that just omitted them fell into the no-gas
    branch and published "no gas corpus is published in this set (household.has_gas is
    false ...)" — a positive false claim about a run that parsed gas, which is the very
    thing the SystemExit next to it exists to prevent. Only a caller that PASSES
    gas=None is evidence of a no-gas household; an omission is evidence of nothing."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    try:
        pb._boundary_record(None, None, [])
    except TypeError as e:
        assert "gas" in str(e) and "presence" in str(e), (
            f"the call was refused, but not for the missing run state: {e}")
    except Exception as e:                                   # noqa: BLE001
        raise AssertionError(
            f"_boundary_record() accepted a call with no gas state and got as far as "
            f"{type(e).__name__}: {e} — a caller that forgets the argument can still "
            f"publish a record claiming this run parsed no gas corpus")
    else:
        raise AssertionError(
            "_boundary_record() accepted a call with no gas state and returned a "
            "record; an omitted argument must never read as a no-gas household")
    return ("_boundary_record() without the run's gas state -> TypeError, so the "
            "no-gas claim can only come from a caller that meant it")


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


def _stage_raw_export(tmp, text):
    """Write the billing-history export VERBATIM, header and all.

    _stage_export() can only produce a well-formed export. The cases below need the
    shapes a real re-pull actually produces when it goes wrong — a download that
    stopped after the header, a renamed column, a column present but empty — and only
    raw text can express those."""
    path = tmp / "private" / "1-raw-data" / EXPORT_NAME
    path.write_text(text)
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

    b = _json(tmp / "data" / "bill_corpus_boundary.json")
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
    that covers it, must publish it and record ZERO exclusions. For a TRAILING
    exclusion like this one, re-pulling the export is the whole remedy — nothing in
    the parser has to be edited. (A LEADING exclusion needs an export whose range
    reaches back instead; case_export_leading_hole_records_a_remedy_that_is_true_of_it
    and case_artifact_level_guidance_never_prescribes_a_repull_for_a_leading_exclusion
    cover that direction.)"""
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
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert b["excluded_statements"] == [], b
    assert b["statements_published"] == b["statements_parsed"] == len(dates), b
    # The underivable-boundary block belongs ONLY to runs with no export: a run that
    # derived the boundary and excluded nothing must serialise exactly as it always
    # has, or every committed bill_corpus_boundary.json in the wild changes bytes.
    assert "boundary_not_derived" not in b, \
        f"a derived boundary carries the not-derived block: {b}"
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
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
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
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
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


def _stage_leading_hole(tmp):
    """Stage an export whose earliest row is the corpus's SECOND statement — the shape
    a rolling-window re-pull produces when it has aged past the oldest statement on
    disk. Returns (victim, dates). SkipCase on a corpus that cannot express it."""
    dates = _corpus_dates(tmp)
    victim = dates[0]
    if len(dates) < 3 or _summary_pinned(tmp, victim):
        raise SkipCase("needs an oldest statement outside the SUMMARY_STATEMENTS lists")
    _stage_export(tmp, dates[1:])
    return victim, dates


def case_export_leading_hole_records_a_remedy_that_is_true_of_it(tmp):
    """A statement OLDER than the export's earliest row is absorbed like a trailing
    one — but its remedy is the opposite, and the record has to say the true one
    (issue #154).

    SDG&E's billing-history export is a rolling window. When it rolls forward past
    the oldest statement on disk, "re-pull the export" — the remedy that ends a
    TRAILING exclusion — is precisely the action that cannot work: a fresh pull
    starts no earlier, and can drop more statements off the front. The days come off
    the FRONT of the analysis window, which is the CLAUDE.md §1 shape (coverage quietly
    short), so the day loss must be a number too."""
    victim, dates = _stage_leading_hole(tmp)
    # A window opening on the excluded statement's own period start, so its day loss
    # lands at the FRONT of the window and cannot be confused with a trailing one.
    r = _run(tmp)
    assert r.returncode == 0, f"a leading exclusion failed the run:\n{r.stderr}"
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert [e["statement_date"] for e in b["excluded_statements"]] == [victim], b
    v_start, v_end = (dt.date.fromisoformat(x)
                      for x in b["excluded_statements"][0]["period_span"])
    _stage_window(tmp, v_start.isoformat(),
                  (v_start + dt.timedelta(days=364)).isoformat())
    r = _run(tmp)
    assert r.returncode == 0, f"a leading exclusion failed the run:\n{r.stderr}"

    published = {p["statement_date"]
                 for p in _rows(tmp / "data" / "bill_periods_electric.csv")}
    assert victim not in published and published == set(dates[1:]), sorted(published)
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    rec = b["excluded_statements"][0]
    assert rec["statement_date"] == victim, b

    # The remedy must be the one that works in THIS direction, and must not be the
    # one that does not: a re-pull starts no earlier than the export already staged.
    ends = rec["exclusion_ends_when"]
    assert "reaches BACK" in ends and "rolling window" in ends, (
        f"a leading exclusion carries a remedy written for a trailing one: {ends}")
    assert "re-pulled so that it covers this statement" not in ends, (
        f"the leading exclusion still tells the operator to re-pull the export, "
        f"which is the one action that cannot recover it: {ends}")
    assert dates[1] in ends, (
        f"the remedy does not say where the export actually starts: {ends}")
    assert "older than the export's earliest row" in rec["reason"], rec["reason"]

    # ...and the day loss off the front is a number, in the artifact and on stdout.
    cov = b["window_coverage"]
    assert cov["missing_ranges"][0][0] == v_start.isoformat(), (
        f"the days lost off the FRONT of the window are not in missing_ranges: {cov}")
    assert cov["days_the_excluded_statements_would_supply"] == cov["days_missing"] > 0, cov
    assert "reaches BACK" in r.stdout, \
        f"the leading exclusion's remedy is not announced:\n{r.stdout}"
    return (f"a statement older than the export's earliest row ({victim}) is excluded "
            f"with the remedy that is true of it (widen the export, not re-pull it) "
            f"and {cov['days_missing']} day(s) lost off the front of the window")


# A re-pull mentioned as the thing that BRINGS THE STATEMENT BACK. The pair is what
# makes the claim: "a re-pull starts no earlier" names a re-pull and prescribes
# nothing, and "the next run publishes it" prescribes something without naming a
# re-pull. Neither is a violation; the two in one sentence, unqualified, is.
_REPULL_NAMED = re.compile(r"re-?pull", re.I)
_REPULL_RESTORES = re.compile(
    r"publish|restore|comes? back|covers this statement|back in(?:to)? the corpus",
    re.I)
# ...unless the sentence scopes or negates the claim, which is how a correct
# TRAILING-only or explicitly two-branched wording reads. Keeping this escape hatch
# is deliberate: the guarded property is "no UNCONDITIONAL re-pull remedy at
# artifact level", not "the word never appears".
_REPULL_QUALIFIED = re.compile(
    r"trailing|newer than|not the remedy|does not|do not|cannot|can't|never|"
    r"no earlier|is not", re.I)


def _artifact_level_strings(node, path="$"):
    """Every string in the boundary record OUTSIDE excluded_statements, tagged with
    the JSON path it came from.

    Those per-statement records are the ONE place the direction-dependent remedy
    belongs, because only there is the direction known. Every other string in the
    file is a general statement about the boundary and is read as applying to both
    directions — which is exactly why an unconditional remedy at that level is a
    defect even on a corpus whose only exclusion is trailing."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "excluded_statements":
                continue
            yield from _artifact_level_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _artifact_level_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _sentences(text):
    return [s for s in re.split(r"(?<=[.;])\s+", text) if s.strip()]


def case_artifact_level_guidance_never_prescribes_a_repull_for_a_leading_exclusion(tmp):
    """The per-statement remedy being direction-aware is not enough: the record's
    general prose must not contradict it.

    data/bill_corpus_boundary.json states its rule once, at the top, and that is what
    a consumer reads first. When it says a re-pull publishes the excluded statement
    again, and the leading exclusion recorded below it says a re-pull is precisely the
    action that cannot work, the file carries two mutually exclusive remedies and the
    authoritative-looking one is the false one — an operator following it re-pulls a
    rolling window repeatedly, losing more statements off the front each time and
    leaving the corpus truncated.

    So: with a LEADING exclusion recorded, no string in the record outside
    excluded_statements may name a re-pull as the thing that restores the statement,
    unless the sentence scopes or negates the claim. The rule must also either state
    the leading branch itself or point at the field that does, so the first thing read
    is never a dead end."""
    victim, dates = _stage_leading_hole(tmp)
    r = _run(tmp)
    assert r.returncode == 0, f"a leading exclusion failed the run:\n{r.stderr}"
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert [e["statement_date"] for e in b["excluded_statements"]] == [victim], b

    # The per-statement record is the one place the remedy lives, and on this run it
    # says a re-pull is NOT it. Everything below is about what the REST of the file
    # says while that record is sitting in it.
    ends = b["excluded_statements"][0]["exclusion_ends_when"]
    assert "reaches BACK" in ends and "NOT the remedy" in ends, ends

    offenders = []
    for where, text in _artifact_level_strings(b):
        for sentence in _sentences(text):
            if (_REPULL_NAMED.search(sentence)
                    and _REPULL_RESTORES.search(sentence)
                    and not _REPULL_QUALIFIED.search(sentence)):
                offenders.append(f"{where}: {sentence.strip()}")
    assert not offenders, (
        f"{len(offenders)} artifact-level string(s) prescribe a re-pull as the remedy "
        f"while the record's only exclusion ({victim}) is a LEADING one, which a "
        f"re-pull cannot recover — the file contradicts its own per-statement remedy "
        f"and the general claim is the one a consumer reads first: "
        + " | ".join(offenders))

    rule = b["rule"]
    assert "exclusion_ends_when" in rule or "reaches back" in rule.lower(), (
        f"the top-level rule neither states the leading branch nor names the "
        f"per-statement field that does, so a consumer who reads it first is left "
        f"with no remedy at all for the exclusion recorded below it: {rule}")
    return (f"with a leading exclusion ({victim}) recorded, no artifact-level string "
            f"prescribes a re-pull as its remedy "
            f"({len(list(_artifact_level_strings(b)))} strings checked) and the rule "
            f"points at the per-statement field that carries the true one")


def case_gas_days_inside_an_excluded_electric_period_are_recorded(tmp):
    """The boundary restricts ELECTRIC only, so the gas artifacts can publish billed
    days the electric ones exclude. That asymmetry must be in the record, counted in
    days, never left for a reader to discover by diffing the two artifacts (#159).

    The export is an electric billing-history export and the gas statements carry
    their own dates, so restricting gas by it would delete a corpus it was never able
    to corroborate. What the record owes instead: that it does not govern gas, what
    does, and how many published gas days fall inside an excluded electric period."""
    victim, _dates = _stage_leading_hole(tmp)
    r = _run(tmp)
    assert r.returncode == 0, f"the run failed:\n{r.stderr}"
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert "gas_corpus" in b, (
        "the boundary record says nothing about the gas corpus, so a gas artifact "
        "wider than the electric one is left for a reader to discover by diffing "
        f"the two: {sorted(b)}")
    gasc = b["gas_corpus"]
    assert gasc["restricted_by_this_boundary"] is False, gasc
    assert "ELECTRIC billing-history export" in gasc["what_governs_it_instead"], gasc
    assert "SUMMARY_STATEMENTS_GAS" in gasc["what_governs_it_instead"], gasc

    gas_rows = _rows(tmp / "data" / "bill_periods_gas.csv")
    if not gas_rows:
        raise SkipCase("this corpus publishes no gas statements")
    # Recomputed here from the published gas artifact and the excluded electric
    # period, not read back from the helper that wrote the block.
    ex = [(dt.date.fromisoformat(e["period_span"][0]),
           dt.date.fromisoformat(e["period_span"][1])) for e in b["excluded_statements"]]
    days, twins = set(), set()
    for row in gas_rows:
        s, e = (dt.datetime.strptime(x.strip(), "%b %d, %Y").date()
                for x in row["period"].split(" - "))
        for a, z in ex:
            lo, hi = max(a, s), min(z, e)
            if lo <= hi:
                days |= {lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)}
                twins.add(row["statement_date"])
    if not days:
        raise SkipCase("this corpus's gas periods do not reach the excluded electric one")

    assert gasc["days_published_inside_an_excluded_electric_period"] == len(days), (
        f"the gas corpus publishes {len(days)} billed day(s) inside the excluded "
        f"electric period but the record says "
        f"{gasc['days_published_inside_an_excluded_electric_period']}: {gasc}")
    assert {o["gas_statement_date"]
            for o in gasc["periods_inside_an_excluded_electric_period"]} == twins, gasc
    assert {o["excluded_electric_statement_date"]
            for o in gasc["periods_inside_an_excluded_electric_period"]} == {victim}, gasc
    assert gasc["statements_published"] == len({g["statement_date"] for g in gas_rows}), gasc
    # ...and it is announced, so the run is not silent about it either.
    assert "GAS IS WIDER" in r.stdout and str(len(days)) in r.stdout, \
        f"the gas/electric asymmetry is not announced:\n{r.stdout}"
    return (f"the gas artifacts publish {len(days)} billed day(s) inside excluded "
            f"electric statement {victim}, recorded and announced instead of silent")


# A sentence in the record that names the SUMMARY_STATEMENTS_GAS presence check is a
# claim about a check that CAN decline to run. On a corpus where it did not run, such
# a sentence is false unless it carries one of these disqualifiers.
_PRESENCE_NAMED = re.compile(r"SUMMARY_STATEMENTS_GAS presence check", re.I)
_PRESENCE_QUALIFIED = re.compile(
    r"did not run|never ran|was not applied|only on a corpus|\bALONE\b|unverified|"
    r"\bSKIPPED\b")


def _gas_statement_dates(tmp):
    return sorted({r["statement_date"]
                   for r in _rows(tmp / "data" / "bill_periods_gas.csv")})


def case_gas_corpus_records_the_presence_check_that_actually_ran(tmp):
    """On a corpus the pinned list documents, the record must say the presence check
    RAN — and say it with the run's own numbers, not as a standing claim.

    The gas artifacts have no export to corroborate them, so the SUMMARY_STATEMENTS_GAS
    presence check is the only thing that can establish the gas corpus is COMPLETE
    rather than merely self-consistent. A reader of the artifact therefore has to be
    able to tell whether it ran. Here it did, so the block must record applied=true
    with the listed/present counts that the published gas artifact and the list
    actually produce."""
    sys.path.insert(0, str(HERE))
    import parse_bills as pb
    r = _run(tmp)
    assert r.returncode == 0, f"the control corpus failed:\n{r.stderr}"
    # Read AFTER the run: before it, data/ holds this harness's sentinels.
    have = set(_gas_statement_dates(tmp))
    if not have:
        raise SkipCase("this corpus publishes no gas statements")
    listed = set(pb.SUMMARY_STATEMENTS_GAS)
    if not (have & listed):
        raise SkipCase("this corpus shares no date with SUMMARY_STATEMENTS_GAS")

    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert "gas_corpus" in b, (
        "the boundary record says nothing about the gas corpus at all, so nothing in "
        "it states whether the presence check — the gas corpus's only completeness "
        f"guard — ran on this run: {sorted(b)}")
    chk = b["gas_corpus"].get("summary_statements_presence_check")
    assert chk is not None, (
        "the gas corpus's only completeness guard is the SUMMARY_STATEMENTS_GAS "
        "presence check, and the record does not say whether it ran: "
        f"{sorted(b['gas_corpus'])}")
    assert chk["applied"] is True, chk
    assert chk["list"] == "SUMMARY_STATEMENTS_GAS", chk
    # Recomputed from the published gas artifact and the list itself, not read back
    # from the code that wrote the block.
    assert chk["statements_listed"] == len(listed), chk
    assert chk["statements_listed_and_present"] == len(have & listed), (
        f"the record says {chk['statements_listed_and_present']} of the listed gas "
        f"statements are present; the published artifact and the list share "
        f"{len(have & listed)}: {chk}")
    assert "ran on this corpus" in chk["what_it_means"], chk
    # The check covers the statements the LIST NAMES, not the whole published corpus,
    # and the count is the set difference — a published statement the list does not
    # name is unchecked whether it falls past the list's end or sits inside its span.
    # The field is named for that difference, not for a date window: the two coincide
    # on this corpus and come apart on a fork's, where the old name would have been a
    # false explanation of a correct number.
    assert "statements_published_outside_that_window" not in chk, (
        f"the record still counts unchecked gas statements under a name that says "
        f"'outside that window' while computing a set difference against the list; on "
        f"a corpus with a statement inside the list's span but absent from it the "
        f"name and the number disagree: {chk}")
    assert chk["statements_published_the_list_does_not_name"] == len(have - listed), (
        f"the record says {chk['statements_published_the_list_does_not_name']} "
        f"published gas statement(s) go unnamed by the list; the published artifact "
        f"and the list put {len(have - listed)} there: {chk}")
    assert "and no others" in chk["what_it_means"], (
        f"the record does not scope the check to the statements the list names, so "
        f"'complete' reads as complete over the whole published gas corpus: {chk}")
    assert "inside its span" in chk["what_it_means"], (
        f"the record explains the unchecked statements as falling past the list's "
        f"end only, which is one of the two ways a published statement goes unnamed "
        f"and is false of the other: {chk}")
    # ...and the prose that names the check as governing gas must say the same thing,
    # so the two cannot disagree.
    governs = b["gas_corpus"]["what_governs_it_instead"]
    assert _PRESENCE_NAMED.search(governs) and "ran on this corpus" in governs, governs
    return (f"presence check applied on this corpus -> recorded as applied with "
            f"{chk['statements_listed_and_present']}/{chk['statements_listed']} "
            f"listed gas statements present")


def case_fork_gas_corpus_completeness_is_recorded_as_unverified(tmp):
    """A fork's gas corpus is NOT covered by the presence check, and the record must
    say so instead of claiming it.

    A fork's statement dates share nothing with SUMMARY_STATEMENTS_GAS, so _validate()
    treats the list as another household's and skips reproduction-gate check 1. Gas has
    no billing-history export behind it either, so on that run NOTHING establishes the
    gas corpus is complete — every remaining check (duplicates, tiling, cross-foot)
    passes on a corpus with statements missing off either END, because what is left
    still tiles. This case builds exactly that: a fork corpus with its OLDEST gas
    statement deleted. It publishes, which is the deliberate choice (refusing would
    refuse every fork on its first run, before it could pin its own list), so what the
    record owes is the truth about the run: completeness unverified, why, and what
    makes the check apply.

    The failure this guards is a record that says the check governs the gas corpus on
    a run where it never executed — a completeness guarantee for a corpus that is
    demonstrably short by one statement."""
    gas_dir = tmp / "private" / "1-raw-data" / "gas-bills"
    if not gas_dir.is_dir():
        raise SkipCase("this corpus has no gas statements")
    pdfs = sorted(gas_dir.glob("sdge_gas_*.pdf"), key=lambda p: _statement_date(p))
    if len(pdfs) < 3:
        raise SkipCase("needs at least three gas statements to truncate one off the end")
    dropped = _statement_date(pdfs[0])
    pdfs[0].unlink()                                    # truncate off the FRONT
    _patch_summary_lists(tmp, ["1900-01-01", "1900-02-01"], ["1900-01-15"])

    r = _run(tmp)
    assert r.returncode == 0, (
        f"a fork's gas corpus failed to publish — the fork path must stay open:\n"
        f"{r.stderr}")
    assert "skipping reproduction-gate check 1 for gas" in r.stdout, \
        f"the gas presence check was not skipped, so this is not the fork state:\n{r.stdout}"
    published = _gas_statement_dates(tmp)
    assert dropped not in published, "the deleted gas statement was published anyway"
    assert published, "the fork published no gas statements at all"

    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert "gas_corpus" in b, (
        f"the boundary record says nothing about the gas corpus at all, so a fork "
        f"cannot tell this {len(published)}-statement gas corpus from a complete "
        f"one: {sorted(b)}")
    gasc = b["gas_corpus"]
    chk = gasc.get("summary_statements_presence_check")
    assert chk is not None, (
        f"the presence check did not run and the record does not say so — a reader "
        f"cannot tell this {len(published)}-statement gas corpus from a complete "
        f"one: {sorted(gasc)}")
    assert chk["applied"] is False, chk
    assert chk["statements_listed_and_present"] == 0, chk
    assert "UNVERIFIED" in chk["what_it_means"], (
        f"the record does not say the gas corpus's completeness is unverified: {chk}")
    assert "SUMMARY_STATEMENTS_GAS" in chk.get("check_is_applied_when", ""), (
        f"the record states no remedy, so the fork has nothing to act on: {chk}")

    # The sweep: no string anywhere in the record may name the presence check without
    # disqualifying it on this run. This is the claim that was there before — "governed
    # by ... the SUMMARY_STATEMENTS_GAS presence check" — stated unconditionally.
    offenders = []
    for where, text in _artifact_level_strings(b):
        for sentence in _sentences(text):
            if (_PRESENCE_NAMED.search(sentence)
                    and not _PRESENCE_QUALIFIED.search(sentence)):
                offenders.append(f"{where}: {sentence.strip()}")
    assert not offenders, (
        f"{len(offenders)} string(s) in the record name the SUMMARY_STATEMENTS_GAS "
        f"presence check without saying it did not run on this corpus, while the run "
        f"itself skipped it and published a gas corpus short by {dropped}: "
        + " | ".join(offenders))
    return (f"fork gas corpus truncated to {len(published)} statement(s) publishes, "
            f"and the record states its completeness UNVERIFIED with the remedy, "
            f"instead of claiming a check that never ran")


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
    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert b["export"] is None and b["excluded_statements"] == [], b
    assert b["statements_published"] == b["statements_parsed"] == len(dates), b
    return "no export staged -> whole corpus published, boundary recorded as underived"


def case_no_export_records_the_underivable_boundary_in_the_artifact(tmp):
    """A run that could not derive the boundary has to say so in the COMMITTED
    artifact, not only on a console nobody keeps.

    `export: null` beside `excluded_statements: []` reads as "nothing was excluded,
    all fine" — the exact misreading that matters, because on that run nothing was
    CHECKED either. So the record must state where the export was looked for, that no
    boundary was derived, what that costs the published corpus, and what ends it."""
    dates = _corpus_dates(tmp)
    assert not (tmp / "private" / "1-raw-data" / EXPORT_NAME).exists()
    r = _run(tmp)
    assert r.returncode == 0, f"a corpus with no export failed:\n{r.stderr}"

    b = _json(tmp / "data" / "bill_corpus_boundary.json")
    assert b["export"] is None, b
    nd = b.get("boundary_not_derived")
    assert nd is not None, (
        "the boundary was not derivable, but the committed artifact records only "
        f"export=null with excluded_statements={b['excluded_statements']} — a reader "
        f"cannot tell 'nothing excluded' from 'nothing checked': {b}")
    assert EXPORT_NAME in nd["export_looked_for"], nd
    assert "no billing-history export is staged" in nd["reason"], nd
    assert "NOT corroborated" in nd["consequence"], nd
    assert f"{len(dates)} parsed statement(s) was published" in nd["consequence"], nd
    assert "re-run" in nd["boundary_is_derived_when"], nd

    # ...and announced, so the run itself is not silent about it either.
    assert "NOT DERIVED" in r.stdout, \
        f"the underivable boundary is not announced:\n{r.stdout}"
    assert nd["consequence"] in r.stdout, \
        f"the consequence recorded in the artifact is not printed:\n{r.stdout}"
    return ("no export staged -> the artifact records boundary_not_derived with its "
            "reason, its consequence and what ends it, and the run announces it")


def case_export_present_but_header_only_fails_closed(tmp):
    """A download that stopped after the header line. The file EXISTS, so "no export
    is staged" is the wrong reading of it: taking a broken export as "the boundary is
    not derivable" would publish every parsed statement as though SDG&E's own books
    had corroborated it — broadening the corpus to exactly the statements that cannot
    be reconciled. Present-but-unusable is an error, and errors publish nothing."""
    _stage_raw_export(
        tmp, "statement_date,billing_days,current_charges,amount_due,status\n")
    r = _run(tmp)
    assert r.returncode != 0, "a header-only export was accepted as 'no export staged'"
    assert "carries no statement_date value" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert "0 data row(s)" in r.stderr, \
        f"the refusal does not say what was found:\n{r.stderr}"
    assert "statement_date, billing_days" in r.stderr, \
        f"the refusal does not name the columns it read:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "header-only export -> exits, artifacts untouched"


def case_export_without_a_statement_date_column_fails_closed(tmp):
    """SDG&E layout drift, or a re-pull that renamed the column: the rows are all
    there, the dates are all there, and not one of them is reachable. Silently
    equivalent to a header-only file, and equally not a missing export."""
    dates = _corpus_dates(tmp)
    rows = "\n".join(f"{d},30,0.00,0.00,Paid" for d in dates)
    _stage_raw_export(
        tmp, "bill_date,billing_days,current_charges,amount_due,status\n" + rows + "\n")
    r = _run(tmp)
    assert r.returncode != 0, "an export with no statement_date column was accepted"
    assert "carries no statement_date value" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert f"{len(dates)} data row(s)" in r.stderr, \
        f"the refusal does not say how many rows it read:\n{r.stderr}"
    assert "bill_date" in r.stderr, \
        f"the refusal does not name the column it actually found:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "export with the statement_date column renamed -> exits, artifacts untouched"


def case_export_with_only_blank_statement_dates_fails_closed(tmp):
    """The column is there and every value in it is empty — a partial export, or one
    written from a query that returned no dates. The blanks are already discarded as
    unusable, which is precisely why the empty result must not then be read as "no
    export": whitespace-only values collapse the same way."""
    dates = _corpus_dates(tmp)
    rows = "\n".join(f"{'   ' if i % 2 else ''},30,0.00,0.00,Paid"
                     for i, _ in enumerate(dates))
    _stage_raw_export(
        tmp,
        "statement_date,billing_days,current_charges,amount_due,status\n" + rows + "\n")
    r = _run(tmp)
    assert r.returncode != 0, "an export whose statement_date values are all blank was accepted"
    assert "carries no statement_date value" in r.stderr, f"unexpected error:\n{r.stderr}"
    assert f"{len(dates)} data row(s)" in r.stderr, \
        f"the refusal does not say how many rows it read:\n{r.stderr}"
    assert _artifacts_untouched(tmp), "artifacts were modified despite the failure"
    return "export with every statement_date blank -> exits, artifacts untouched"


def _ranges(days):
    """[date, ...] -> [[first, last], ...] over each contiguous run (ISO strings)."""
    out = []
    for d in sorted(days):
        if out and d - dt.date.fromisoformat(out[-1][1]) == dt.timedelta(days=1):
            out[-1][1] = d.isoformat()
        else:
            out.append([d.isoformat(), d.isoformat()])
    return out


def _assert_boundary_window_current(data_dir):
    """Recompute bill_corpus_boundary.json's window_coverage block from the OTHER
    committed artifacts and assert the committed block matches.

    Every input is committed and de-identified — behavior_rebuild.json's window,
    bill_periods_electric.csv's published periods, and the excluded statements'
    own spans — so this runs in CI, where parse_bills.py itself cannot (it needs
    the gitignored bill PDFs). AssertionError, with the diagnosis, on a mismatch."""
    b = _json(data_dir / "bill_corpus_boundary.json")
    stale = (f"Re-run analysis/parse_bills.py (it needs the private bill corpus) and "
             f"commit its seven artifacts: data/bill_corpus_boundary.json publishes "
             f"day-coverage figures computed against a window that no longer exists.")
    # Named, not subscripted. This is the LAST standalone case and standalone runs
    # first, so a bare KeyError here aborted main() before a single corpus case ran —
    # CI printing a traceback in place of the staleness diagnosis two lines below.
    assert "window_coverage" in b, (
        f"data/bill_corpus_boundary.json records no window coverage at all, so the "
        f"day figures the report quotes have no committed source: {sorted(b)}. {stale}")
    cov = b["window_coverage"]
    w = _json(data_dir / "behavior_rebuild.json")["window"]
    w_start = dt.date.fromisoformat(str(w["start"])[:10])
    w_end = dt.date.fromisoformat(str(w["end"])[:10])
    assert "window" in cov, (
        f"window_coverage names no window, so there is nothing to compare "
        f"data/behavior_rebuild.json's [{w_start.isoformat()}, {w_end.isoformat()}] "
        f"against and the coverage figures below stand on nothing: {sorted(cov)}. "
        f"{stale}")
    assert cov["window"] == [w_start.isoformat(), w_end.isoformat()], (
        f"data/bill_corpus_boundary.json reports coverage against the window "
        f"{cov['window']}, but data/behavior_rebuild.json's window is now "
        f"[{w_start.isoformat()}, {w_end.isoformat()}]. {stale}")

    bounds = [_period_bounds(r["period"])
              for r in _rows(data_dir / "bill_periods_electric.csv")]
    c_start, c_end = min(s for s, _ in bounds), max(e for _, e in bounds)
    assert b["published_period_span"] == [c_start.isoformat(), c_end.isoformat()], (
        f"the boundary record says the published corpus spans "
        f"{b['published_period_span']}, but data/bill_periods_electric.csv spans "
        f"[{c_start.isoformat()}, {c_end.isoformat()}]. {stale}")
    window = [w_start + dt.timedelta(days=i) for i in range((w_end - w_start).days + 1)]
    covered = [d for d in window if c_start <= d <= c_end]
    missing = [d for d in window if not (c_start <= d <= c_end)]
    supplied = set()
    for rec in b["excluded_statements"]:
        a, z = (dt.date.fromisoformat(x) for x in rec["period_span"])
        supplied |= {d for d in missing if a <= d <= z}
    for key, want in (("window_days", len(window)),
                      ("days_covered", len(covered)),
                      ("days_missing", len(missing)),
                      ("missing_ranges", _ranges(missing)),
                      ("days_the_excluded_statements_would_supply", len(supplied))):
        assert cov[key] == want, (
            f"window_coverage.{key} is {cov[key]!r}, recomputed from the committed "
            f"artifacts it is {want!r}. {stale}")


def case_committed_boundary_is_current_with_the_committed_window():
    """The committed boundary artifact must still be true of the committed window.

    parse_bills.py reads the analysis window from data/behavior_rebuild.json, which
    another generator owns. Regenerate that against a fresh Green Button export and
    its window moves; nothing re-derives the boundary, so data/bill_corpus_boundary
    .json goes on publishing day-coverage figures (the 338-of-365 the report quotes)
    against a window that no longer exists. Every check that could catch it needs the
    private bill corpus and skips in CI, which is exactly why this one is computed
    from committed, de-identified artifacts only and runs anywhere (issue #155).

    It then proves itself on a tampered copy: a check that cannot fail on the drift
    it names is not a gate."""
    data = ROOT / "data"
    names = ("bill_corpus_boundary.json", "behavior_rebuild.json",
             "bill_periods_electric.csv")
    for n in names:
        if not (data / n).exists():
            raise SkipCase(f"data/{n} is not committed in this checkout")
    b = _json(data / "bill_corpus_boundary.json")
    if b.get("window_coverage") is None:
        raise SkipCase("the committed boundary records no window coverage")
    if not _rows(data / "bill_periods_electric.csv"):
        raise SkipCase("no published electric periods to recompute coverage from")

    _assert_boundary_window_current(data)

    with tempfile.TemporaryDirectory() as td:
        moved = pathlib.Path(td) / "data"
        moved.mkdir()
        for n in names:
            shutil.copy2(data / n, moved / n)
        j = _json(moved / "behavior_rebuild.json")
        for end in ("start", "end"):
            v = str(j["window"][end])
            j["window"][end] = (dt.date.fromisoformat(v[:10])
                                + dt.timedelta(days=30)).isoformat() + v[10:]
        (moved / "behavior_rebuild.json").write_text(json.dumps(j))
        try:
            _assert_boundary_window_current(moved)
        except AssertionError:
            pass
        else:
            raise AssertionError(
                "behavior_rebuild.json's window moved 30 days and the committed "
                "boundary's day figures still passed — the check cannot detect the "
                "staleness it exists for")
    cov = b["window_coverage"]
    return (f"the committed boundary's {cov['days_covered']}/{cov['window_days']}-day "
            f"coverage recomputes from the committed window and periods, and the "
            f"check fails when that window moves")


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
                case_export_leading_hole_records_a_remedy_that_is_true_of_it,
                case_artifact_level_guidance_never_prescribes_a_repull_for_a_leading_exclusion,
                case_gas_days_inside_an_excluded_electric_period_are_recorded,
                case_gas_corpus_records_the_presence_check_that_actually_ran,
                case_fork_gas_corpus_completeness_is_recorded_as_unverified,
                case_export_sharing_no_statement_fails_closed,
                case_no_export_publishes_the_whole_corpus,
                case_no_export_records_the_underivable_boundary_in_the_artifact,
                case_export_present_but_header_only_fails_closed,
                case_export_without_a_statement_date_column_fails_closed,
                case_export_with_only_blank_statement_dates_fails_closed]

# Cases that run anywhere: they use temp files, or the COMMITTED data/ artifacts. The
# publication, rollback and concurrency guards live here, so they must run in a clean
# checkout and in CI — skipping the whole suite when the private corpus is absent would
# let a broken lock or a lost rollback pass the documented command with exit code 0.
STANDALONE_CASES = [case_write_rollback, case_rollback_after_partial_swap,
                    case_build_cleanup_survives_mid_copy_failure,
                    case_the_sweep_never_removes_a_sandbox_a_live_process_still_holds,
                    case_an_abandoned_sandbox_carrying_a_free_marker_is_swept,
                    case_a_markerless_candidate_survives_the_sweep_and_is_reported,
                    case_an_unreadable_marker_is_reported_not_silently_skipped,
                    case_a_new_marker_is_never_visible_to_the_sweep_unlocked,
                    case_restore_failure_preserves_backups,
                    case_retry_after_failed_rollback_refuses,
                    case_lock_blocks_second_publisher,
                    case_concurrent_publishers_serialize,
                    case_overlapping_electric_periods,
                    case_overlapping_gas_periods,
                    case_fork_corpus_skips_presence_check,
                    case_boundary_record_requires_the_runs_own_gas_state,
                    case_partial_overlap_still_fails,
                    case_neither_fixed_charge_label_present_fails,
                    case_both_fixed_charge_labels_present_prefers_bsc,
                    case_committed_boundary_is_current_with_the_committed_window]


def _report_failure(case, exc):
    """One FAIL line for a case that did not pass, whatever it raised.

    THE HOLE THIS CLOSES. These loops used to catch SkipCase and AssertionError and
    nothing else, so any other exception escaped main() and ended the RUN: no FAIL
    line, no case name, just a traceback — and every case after it never executed. It
    cost the most where it hurt most: the last standalone case reads the committed
    boundary artifact, standalone runs first, and one KeyError there aborted the suite
    before a single corpus case started, with CI reporting a traceback instead of the
    staleness diagnosis the case was written to print. (Same hole, different exception
    and different file, as the one closed in test_report_tokens.main().)

    SystemExit needs its own clause because it inherits from BaseException, not
    Exception: parse_bills fails closed with SystemExit for every refusal, and a case
    that provokes one outside its own try would otherwise walk straight past
    `except Exception`. Nothing here re-raises, so the runner's own exit — main()
    RETURNS 1 and sys.exit() below turns that into the process's status — is never
    routed through this function and never re-reported as a case failure."""
    kind = "" if isinstance(exc, AssertionError) else f"{type(exc).__name__}: "
    print(f"FAIL  {case.__name__}: {kind}{exc}")
    if not isinstance(exc, AssertionError):
        # An assertion carries its own diagnosis; anything else does not, and this
        # runner keeps going, so without the traceback the frame is lost for good.
        # On stdout, not stderr, so it lands under its own FAIL line instead of
        # being interleaved somewhere else in the log by two separate buffers.
        traceback.print_exc(file=sys.stdout)


def main():
    # The electric corpus is the hard requirement; gas-dependent cases skip themselves
    # (via SkipCase) when this corpus has no gas statements — a no-gas fork is valid.
    have_corpus = ELEC.is_dir() and any(ELEC.glob("*.pdf"))
    _sweep_stale_sandboxes()
    failures = skipped = ran = 0

    for case in STANDALONE_CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except (AssertionError, SystemExit, Exception) as e:  # noqa: BLE001
            _report_failure(case, e)
            failures += 1

    for case in CORPUS_CASES:
        if not have_corpus:
            print(f"SKIP  {case.__name__} (needs the gitignored bill PDFs; "
                  f"see DATA-SOURCES-CHEATSHEET.md §D)")
            skipped += 1
            continue
        try:
            with _locked_sandbox(SANDBOX_PREFIX) as td:
                print(f"PASS  {case(_build(pathlib.Path(td)))}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except (AssertionError, SystemExit, Exception) as e:  # noqa: BLE001
            _report_failure(case, e)
            failures += 1

    total = len(STANDALONE_CASES) + len(CORPUS_CASES)
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{total} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
