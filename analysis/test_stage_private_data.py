#!/usr/bin/env python3
"""stage-private-data.sh must copy every raw private input a committed
generator actually reads, so a fresh worktree staged only by the script can
run the full pipeline (issue #33) -- and it must REFUSE to write that archive
anywhere that is not a working tree of this checkout, before the first byte
lands (issue #184) -- including when the caller's environment tells git to
report some other repository for that destination, which the cases at the
bottom of this file forge deliberately, and including when the destination
ROOT is a genuine worktree but a symbolic link planted at a gitignored path
BELOW it (private/1-raw-data, private/verify, or any file the copies write)
would carry the archive back out of the tree that just passed every check,
and including a plain directory that merely CLAIMS the checkout -- a `.git`
gitfile naming this repository's common directory answers every probe the
way a real worktree does, so membership is settled against git's own
`worktree list` register instead -- and including a genuine registered
worktree whose own git would let the archive be COMMITTED, either because it
does not ignore these paths (the property the 2026-08-13 destination lacked,
and the one that made the incident dangerous) or because it already tracks
one of them, and including an existing output file that is a HARD link to a
file outside the tree, which `[ -L ]` cannot see and `cp` rewrites in place,
and including one that is a SPECIAL FILE -- a FIFO or a device node is neither
a link nor a multiply-linked regular file, so it passes both of those guards,
and `cp` opening it either hangs the run outright or hands the archive to
whatever process is reading the other end.

A guard that refuses correct input is the worst kind, so the accepting half is
pinned just as hard: a genuinely REGISTERED worktree must still be accepted
even when its path is one the porcelain listing splits across two lines (git
emits a worktree path raw, newlines included), which is why the register is now
read with `--porcelain -z`.

The required-input set is DERIVED by scanning every generator's own source
for its private/1-raw-data path references, in the four shapes this repo's
generators actually use (a pathlib chain, os.path.join, a
ROOT/"private"/"1-raw-data" directory variable referenced later, or a .glob()
call directly on that bare root variable/expression with either a literal
pattern or a same-file string constant) -- not hand-typed here -- so a newly
added private input a future generator reads and stage-private-data.sh does
not stage fails this suite instead of silently breaking a fresh worktree the
way electric-bills/, gas-bills/ and electric_billing_history_2024-2026.csv
did before this issue.

KNOWN GAP, left honest rather than silently claimed solved (adversarial
review, issue #33, round 1): service_headroom.py's only_match(pattern, what) takes
its glob pattern as a FUNCTION PARAMETER, so `RAW_DIR.glob(pattern)` at
service_headroom.py:746 cannot be resolved by scanning that line alone --
doing so would require tracing every call site of an arbitrary function,
which this scanner does not attempt. Both of that function's actual call
sites (service_headroom.py:3941,4785) pass the literal
"Electric_15_Minute_*.csv", already staged and already caught directly at
its OTHER two call shapes elsewhere in the codebase (service_headroom.py's
own RAW_DIR.glob("enphase_sam8760_*.csv") calls, and
irreducible_bill.py's (ROOT/"private"/"1-raw-data").glob(RAW_INTERVAL_GLOB))
-- verified by hand, not by this scanner, and re-verify by hand if
only_match() ever gains a new call site with a new pattern.

Run from the repo root:  ./.venv/bin/python analysis/test_stage_private_data.py
"""
import contextlib
import os
import pathlib
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"
SCRIPT = ROOT / "stage-private-data.sh"

# private/1-raw-data entries a generator's own NORMAL run needs but this
# script deliberately does not stage, each with the reason -- an exemption
# here is never silent. Both are read only under a non-default flag no
# automated pipeline run ever passes (dsgs_vpp_backtest.py --build-calendar,
# nem3_grandfathering.py --build-rates); caiso_raw is handled, conditionally,
# by the script itself, so it is exempt from the "must appear literally" check
# below without needing to be listed twice.
OPTIONAL_NOT_STAGED = {
    "dsgs_events": "dsgs_vpp_backtest.py reads this only under --build-calendar, "
                  "which no automated pipeline run passes",
    "sdge_nbt_export_rates": "nem3_grandfathering.py reads this only under "
                            "--build-rates, which no automated pipeline run passes",
    "caiso_raw": "staged conditionally by this same script when present; "
                "carbon_fullyear.py rebuilds exactly from the committed "
                "data/caiso_hourly_intensity.csv when it is not",
}

_Q = '["\']'   # Python allows either quote style; the codebase happens to use
              # only double quotes today, but the whole point of this scanner
              # is not to assume that stays true (Codex review, issue #33).

_ROOT_VAR = re.compile(
    r'^(\w+)\s*=\s*ROOT\s*/\s*' + _Q + r'private' + _Q + r'\s*/\s*' + _Q + r'1-raw-data' + _Q + r'\s*$', re.M)
_ANON_ROOT = r'\(\s*ROOT\s*/\s*' + _Q + r'private' + _Q + r'\s*/\s*' + _Q + r'1-raw-data' + _Q + r'\s*\)'
_DIRECT = re.compile(
    _Q + r'private' + _Q + r'\s*/\s*' + _Q + r'1-raw-data' + _Q + r'\s*/\s*' + _Q + r'([^"\'/]+)' + _Q)
_OS_JOIN = re.compile(
    r'os\.path\.join\(\s*ROOT\s*,\s*' + _Q + r'private' + _Q + r'\s*,\s*'
    + _Q + r'1-raw-data' + _Q + r'\s*,\s*' + _Q + r'([^"\'/]+)' + _Q)
_STR_CONST = re.compile(r'^(\w+)\s*=\s*' + _Q + r'([^"\']+)' + _Q + r'\s*$', re.M)


def _glob_matches(text, receiver_pattern):
    """.glob(...) calls on `receiver_pattern` (a bare 1-raw-data root, never a
    subdirectory var -- a glob on an already-staged subdirectory like
    ELEC_DIR/GAS_DIR/CAISO_DIR needs no separate entry, since cp -R already
    covers every file under it). The argument is either a literal string or
    a same-file string constant's name; a glob argument that is itself a
    function PARAMETER (service_headroom.py's only_match(pattern, ...)) is
    not resolved here -- see this module's own docstring."""
    consts = dict(_STR_CONST.findall(text))
    out = []
    pat = receiver_pattern + r'\.glob\(\s*(?:' + _Q + r'([^"\']+)' + _Q + r'|(\w+))\s*\)'
    for m in re.finditer(pat, text):
        literal, name = m.group(1), m.group(2)
        if literal is not None:
            out.append(literal)
        elif name in consts:
            out.append(consts[name])
    return out


def _referenced_1raw_data_paths():
    """{leaf_name: {generator filenames that reference it}}, scanning every
    non-test .py file in analysis/. Test files build their own throwaway
    private trees (test_parse_bills.py's own ELEC/GAS fixtures) and are not
    part of what a real worktree's stage-private-data.sh run has to cover."""
    found = {}
    for f in sorted(ANALYSIS.glob("*.py")):
        if f.name.startswith("test_"):
            continue
        text = f.read_text()
        root_vars = set(_ROOT_VAR.findall(text))
        for pat in (_DIRECT, _OS_JOIN):
            for m in pat.finditer(text):
                found.setdefault(m.group(1), set()).add(f.name)
        for var in root_vars:
            for m in re.finditer(
                    re.escape(var) + r'\s*/\s*' + _Q + r'([^"\'/]+)' + _Q, text):
                found.setdefault(m.group(1), set()).add(f.name)
            for pattern in _glob_matches(text, re.escape(var)):
                found.setdefault(pattern, set()).add(f.name)
        for pattern in _glob_matches(text, _ANON_ROOT):
            found.setdefault(pattern, set()).add(f.name)
    return found


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


_CP_ARG = re.compile(r'^\s*cp\s+(?:-R\s+)?"?\$SRC"?/private/1-raw-data/([^\s"\\]+)', re.M)


def _staged_basenames(script_text):
    """Every source basename (or glob pattern) an actual cp/cp -R INVOCATION
    line copies out of private/1-raw-data/ -- e.g. "Electric_15_Minute_*.csv",
    "enphase_sam8760_2025.csv", "electric-bills". Anchored to a leading `cp`
    so the script's own unrelated caiso_raw existence-check `ls` line is
    never mistaken for a real copy."""
    return {pathlib.PurePosixPath(m.group(1)).name for m in _CP_ARG.finditer(script_text)}


def _is_staged(name, script_text):
    """A literal filename/dirname (no '*') is covered by an identical staged
    basename, OR by a staged GLOB that matches it -- a broader staged
    pattern genuinely proves the concrete file gets copied.

    A referenced GLOB pattern (contains '*') is a different, stronger claim
    -- "every file this generator's own glob would ever match gets staged"
    -- and can ONLY be proven by an IDENTICAL staged glob (a copy that uses
    the exact same wildcard the generator reads with, so any future file
    the generator would pick up is staged too). A staged glob matching this
    pattern via fnmatch but not being identical, or a single enumerated
    concrete file happening to fall inside the pattern, does NOT prove
    coverage of the open-ended pattern -- a script that regressed from
    glob-copying enphase_sam8760_*.csv back to two hardcoded year-specific
    cp lines would still (wrongly) look "covered" under a looser check,
    silently reintroducing the class of bug issue #33 was opened to close
    (Codex review, issue #33, pass 2)."""
    import fnmatch
    staged = _staged_basenames(script_text)
    if "*" not in name:
        return name in staged or any("*" in s and fnmatch.fnmatch(name, s) for s in staged)
    return name in staged


@case
def case_every_referenced_private_input_is_staged_or_documented_optional():
    referenced = _referenced_1raw_data_paths()
    assert referenced, "the scanner found nothing -- it likely broke silently"
    script_text = SCRIPT.read_text()
    missing = {name: sorted(users) for name, users in referenced.items()
              if name not in OPTIONAL_NOT_STAGED and not _is_staged(name, script_text)}
    assert not missing, (
        f"stage-private-data.sh does not stage these private inputs a generator "
        f"reads, and they are not documented in OPTIONAL_NOT_STAGED: {missing}")
    return (f"{len(referenced)} referenced private inputs are all either "
           f"staged by the script or documented as intentionally optional")


@case
def case_is_staged_rejects_an_unrelated_pattern_sharing_a_prefix():
    """Adversarial review, issue #33, round 2: an earlier version of
    _is_staged checked only whether a glob pattern's fixed prefix appeared
    anywhere in the script's text, which would have wrongly certified a
    brand-new, genuinely different glob pattern as covered merely because it
    shares a prefix with an already-staged, unrelated filename -- e.g.
    "enphase_sam*.pdf" (a hypothetical future export) against the real,
    already-staged "enphase_sam8760_2025.csv" (a .csv, not a .pdf)."""
    script_text = SCRIPT.read_text()
    assert not _is_staged("enphase_sam*.pdf", script_text), (
        "a pattern for a different file type must not be certified staged "
        "just because it shares a text prefix with an unrelated staged file")
    assert not _is_staged("gas*.xlsx", script_text)
    assert not _is_staged("electric_billing*.pdf", script_text)
    # the real, genuinely-covered pattern must still pass
    assert _is_staged("enphase_sam8760_*.csv", script_text)
    return "an unrelated pattern sharing only a text prefix with a staged file is correctly rejected"


@case
def case_is_staged_does_not_certify_a_glob_covered_by_one_concrete_file():
    """Codex review, issue #33, pass 2: a referenced GLOB pattern is an
    open-ended claim ("every file this generator's own glob would ever
    match"), which a single enumerated concrete file can never prove --
    only an identically-globbing staged copy can. Simulates the exact
    regression named in review: the script reverting from glob-copying
    enphase_sam8760_*.csv back to two hardcoded year-specific cp lines."""
    regressed_script_text = (
        'cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST/private/1-raw-data/"\n'
        'cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST/private/1-raw-data/"\n')
    assert not _is_staged("enphase_sam8760_*.csv", regressed_script_text), (
        "two hardcoded year-specific cp lines must not certify the open-ended "
        "glob pattern the generator actually reads with as covered")
    # the same referenced LITERAL year, by contrast, genuinely is covered
    assert _is_staged("enphase_sam8760_2025.csv", regressed_script_text)
    return "a referenced glob is not falsely certified by enumerated concrete files alone"


@case
def case_the_scanner_catches_a_planted_missing_input():
    """The derivation above is only useful if it actually fails when a real
    generator starts reading something new -- proven by planting one."""
    with tempfile.TemporaryDirectory() as td:
        planted = pathlib.Path(td) / "_planted_generator.py"
        planted.write_text(
            'NEW_INPUT = ROOT / "private" / "1-raw-data" / "brand_new_export.csv"\n')
        try:
            shutil.copy2(planted, ANALYSIS / "_planted_generator.py")
            referenced = _referenced_1raw_data_paths()
            assert "brand_new_export.csv" in referenced, referenced
            assert "brand_new_export.csv" not in SCRIPT.read_text()
        finally:
            (ANALYSIS / "_planted_generator.py").unlink(missing_ok=True)
    return "a planted new private-input reference is detected as unstaged"


@case
def case_the_scanner_catches_single_quoted_references_too():
    """Codex review, issue #33, pass 1: Python allows either
    quote style, and an earlier version of the scanner only recognized
    double-quoted literals -- a future generator written with single quotes
    (ROOT / 'private' / '1-raw-data' / 'new.csv') would have gone completely
    undetected, and this suite would have reported a clean pass on a real
    staging gap."""
    with tempfile.TemporaryDirectory() as td:
        planted = pathlib.Path(td) / "_planted_single_quoted.py"
        planted.write_text(
            "NEW_INPUT = ROOT / 'private' / '1-raw-data' / 'single_quoted_export.csv'\n")
        try:
            shutil.copy2(planted, ANALYSIS / "_planted_single_quoted.py")
            referenced = _referenced_1raw_data_paths()
            assert "single_quoted_export.csv" in referenced, referenced
        finally:
            (ANALYSIS / "_planted_single_quoted.py").unlink(missing_ok=True)
    return "a single-quoted private-input reference is detected"


def _synthetic_src(td, household_yaml_text, has_gas_bills_dir, with_venv=True):
    """A minimal SRC tree the script can run against: just enough of
    analysis/household.py's own repo-root walk-up (analysis/+data/) for it
    to resolve correctly when imported with PYTHONPATH pointed at this
    tree's own analysis/, plus a stand-in .venv/bin/python satisfying
    stage-private-data.sh's own venv-exists guard, and the handful of
    private/1-raw-data/ files the script's non-gas cp lines need so the
    whole run succeeds end to end, not just the has_gas branch.

    The stand-in points at sys.executable -- the interpreter already
    running this test -- rather than symlinking ROOT/.venv: CI (tests.yml)
    never runs `python -m venv`, it just `pip install`s into whatever
    interpreter actions/setup-python provides, so ROOT/.venv does not exist
    on a runner and a symlink to it would be dangling (issue #102 review).

    `with_venv=False` builds the same tree WITHOUT that stand-in, which is the
    shape of a source whose venv lives somewhere else -- the first of the
    source-side checks, and one of the two the destination must survive
    untouched. The tree is built without it rather than by deleting it
    afterwards: the stand-in is a symlink to the live interpreter, and nothing
    in this suite may unlink or chmod its way toward that."""
    src = pathlib.Path(td) / "src"
    (src / "analysis").mkdir(parents=True)
    (src / "data").mkdir(parents=True)
    (src / "private" / "1-raw-data").mkdir(parents=True)
    shutil.copy2(ANALYSIS / "household.py", src / "analysis" / "household.py")
    if with_venv:
        (src / ".venv" / "bin").mkdir(parents=True)
        (src / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (src / "private" / "household.yaml").write_text(household_yaml_text)
    raw = src / "private" / "1-raw-data"
    (raw / "gas.csv").touch()
    (raw / "electric_billing_history_2024-2026.csv").touch()
    (raw / "electric-bills").mkdir()
    (raw / "Electric_15_Minute_test.csv").touch()
    (raw / "enphase_sam8760_2025.csv").touch()
    (raw / "enphase_sam8760_2026.csv").touch()
    if has_gas_bills_dir:
        (raw / "gas-bills").mkdir()
    return src


# --------------------------------------------------------------------------
# issue #184: the destination guard. Every case below runs the REAL script
# against a REAL directory and reads its exit status directly -- the defect
# being guarded here was born from a pipeline (`git worktree add ... | tail`)
# whose failing exit status was swallowed by the last command in the pipe, so
# nothing in this suite may infer success from output text alone.
# --------------------------------------------------------------------------
def _run_script(src, dst, cwd, script=SCRIPT, env=None, timeout=None):
    argv = ["bash", str(script), str(src), str(dst)]
    if timeout is None:
        return subprocess.run(argv, capture_output=True, text=True,
                              cwd=str(cwd), env=env)
    # A BOUNDED run, for the fixtures that plant something `cp` can block on
    # forever (a FIFO with no reader). Two things subprocess.run's own timeout
    # would not do: it kills only the bash it started, and the process actually
    # blocked is a `cp` GRANDCHILD that would survive, keep the fixture's FIFO
    # open and outlive this suite -- so the whole process GROUP is started and
    # killed together. And a timeout must FAIL the case rather than be reported
    # as a slow pass, which is why it raises: a guard case whose script hangs
    # has found the defect, not an infrastructure problem.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(cwd), env=env, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)   # start_new_session: pid == pgid
        out, err = proc.communicate()
        raise AssertionError(
            f"the script did not finish within {timeout}s -- it blocked while "
            f"opening a destination path instead of refusing it before the "
            f"first write. stderr so far: {err!r}")
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _snapshot(root):
    """Every path under `root`, git's own internals excluded.

    A refused run's "nothing was written" claim is checked against the
    destination's WHOLE contents before and after, not against the absence of
    one expected directory: a clone of this repo already contains a private/
    (the committed placeholder README.md, see CLAUDE.md's repo map), so
    "private/ does not exist" would pass on a directory the script had in
    fact just filled with the raw archive."""
    out = set()
    root = pathlib.Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in dirnames + [f for f in filenames if f != ".git"]:
            out.add(str((pathlib.Path(dirpath) / name).relative_to(root)))
    return out


@contextlib.contextmanager
def _linked_worktree(td, name="dst"):
    """A REAL linked worktree of THIS checkout, as the destination.

    It has to be real: the guard's whole point is that the destination's
    --git-common-dir equals this checkout's, and nothing short of an actual
    worktree (or the checkout itself) has that property. A plain temp
    directory -- what these cases used before issue #184 -- is now correctly
    refused, which is why the two pre-existing end-to-end cases below stage
    in here as well.

    The finally clause hands the directory back to git rather than leaving it
    registered: without it every run of this suite would leave a stale entry
    in the developer's real .git/worktrees. Its argument is always a path
    this contextmanager itself created inside the caller's
    TemporaryDirectory, asserted below, and if the removal fails the
    TemporaryDirectory still clears the files."""
    path = pathlib.Path(td) / name
    assert path.parent == pathlib.Path(td), "worktree must live inside the test's tempdir"
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                      capture_output=True, text=True).returncode != 0:
        raise SkipCase("this checkout is not a git repository, so a linked "
                       "worktree of it cannot be built")
    added = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(path), "HEAD"],
        capture_output=True, text=True)
    assert added.returncode == 0, (
        f"could not create a worktree of this checkout: {added.stderr}")
    try:
        yield path
    finally:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(path)],
                       capture_output=True, text=True)


def _assert_refused(result, dst, before, why):
    """A refusal is three separate claims, all of which have to hold: it
    failed, it SAID what it refused and what it wanted (so the operator can
    tell a typo from a wrong project instead of reaching for a flag that
    disables the check), and it wrote nothing."""
    assert result.returncode != 0, (
        f"{why}: expected a refusal, got exit 0\nstdout: {result.stdout}")
    msg = result.stderr
    assert "REFUSED" in msg, f"{why}: refusal was not announced: {msg}"
    assert str(dst) in msg, f"{why}: the message does not name the destination: {msg}"
    assert "expected" in msg, f"{why}: the message does not say what was expected: {msg}"
    after = _snapshot(dst) if pathlib.Path(dst).is_dir() else None
    assert after == before, (
        f"{why}: a REFUSED run wrote into the destination: "
        f"{sorted((after or set()) ^ (before or set()))}")


@case
def case_refuses_a_destination_that_is_not_a_git_repository_at_all():
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "not-a-repo"
        dst.mkdir()
        (dst / "someone_elses_notes.txt").write_text("unrelated project\n")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, before, "a plain directory")
        assert "not a git repository" in result.stderr, result.stderr
    return "a destination that is not a git repository is refused and left untouched"


@case
def case_refuses_a_different_repository():
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "other-project"
        dst.mkdir()
        subprocess.run(["git", "init", "-q", str(dst)], check=True,
                       capture_output=True, text=True)
        (dst / "README.md").write_text("a different project entirely\n")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, before, "an unrelated git repository")
        assert "DIFFERENT repository" in result.stderr, result.stderr
        # The refusal has to name the INVOKING path too (issue #184, /review
        # finding 3): which copy is running is what fixes the expected common
        # dir, so a run refused because the wrong copy was invoked -- through a
        # symlink, or a stray copy on $PATH -- otherwise reads as an accusation
        # against a destination that is perfectly fine.
        assert str(SCRIPT) in result.stderr, (
            "the message does not say which copy of the script decided the "
            "expected repository: " + result.stderr)
    return "an unrelated git repository is refused and left untouched"


@case
def case_refuses_a_different_clone_of_the_same_remote():
    """The case that decides the shape of the check (issue #184): `git remote
    -v` matches ANY clone sharing an origin, so it would wave this one
    through. The question that decides where a secret lands is not "does this
    share my origin" but "is this the checkout I think it is", and only
    --git-common-dir answers that."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "same-remote-clone"
        cloned = subprocess.run(
            ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)],
            capture_output=True, text=True)
        if cloned.returncode != 0:
            raise SkipCase(f"this checkout cannot be cloned here: {cloned.stderr}")
        common = subprocess.run(
            ["git", "-C", str(dst), "remote", "-v"], capture_output=True, text=True)
        assert str(ROOT) in common.stdout or "origin" in common.stdout, (
            "the fixture must actually be a clone with an origin, or it does "
            "not test what it claims to")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, before, "a different clone of the same repo")
        assert "DIFFERENT repository" in result.stderr, result.stderr
    return "a different clone of the same remote is refused and left untouched"


@case
def case_refuses_a_destination_that_does_not_exist_and_does_not_create_it():
    """The exact shape of the 2026-08-13 incident: `git worktree add` had
    failed (its status hidden by a pipe), so the destination the caller named
    was never created. `mkdir -p whatever I was handed` is what turned that
    into a copy of the archive, so a missing destination is refused, not
    created -- it cannot be a working tree of anything."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "never-created-by-a-failed-worktree-add"
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, None, "a destination that does not exist")
        assert not dst.exists(), (
            "the script created the destination it was told to refuse")
    return "a non-existent destination is refused, and is not created"


@case
def case_refuses_a_destination_that_is_not_a_directory():
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "a-file-not-a-directory"
        dst.write_text("some file that is not where an archive goes\n")
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, None, "a plain file")
        assert "not a directory" in result.stderr, result.stderr
        assert dst.read_text().startswith("some file"), "the file was overwritten"
    return "a destination that is a file, not a directory, is refused"


@case
def case_refuses_a_git_internal_directory_of_this_checkout():
    """A git internal directory shares this checkout's --git-common-dir and
    still has no working tree of its own, so rev-parse --show-toplevel fails
    there; unhandled, `set -e` would end the run with a bare exit 128 and no
    explanation, and a silent refusal is the kind that gets "fixed" by
    disabling the check.

    Unlike every other case here, this one refuses to RUN against a script
    whose guard is missing, and fails instead. The reason is specific to the
    fixture: an unguarded script writes wherever it is pointed before
    anything can check, and here that is git's own metadata -- proven the
    hard way, by reintroducing the defect and finding the staged tree inside
    .git/worktrees/, where it also broke the worktree teardown this suite
    depends on. A guard case still has to fail when the defect returns, so it
    fails on the missing guard rather than on the probe. The probe below is
    what proves the message; the assertion here is what keeps proving it
    cheap."""
    text = SCRIPT.read_text()
    assert "DESTINATION GUARD" in text and "no working tree of its own" in text, (
        "the destination guard, or its no-working-tree refusal, is gone from "
        "the script -- restore it; this case will not point an unguarded "
        "script at a git directory to find out what happens")
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as wt:
            got = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "--path-format=absolute", "--git-dir"],
                capture_output=True, text=True)
            assert got.returncode == 0, got.stderr
            gitdir = pathlib.Path(got.stdout.strip())
            assert gitdir.is_dir() and gitdir != wt, gitdir
            result = _run_script(src, gitdir, cwd=src)
            assert result.returncode != 0, "a git internal directory is not a destination"
            assert "REFUSED" in result.stderr and str(gitdir) in result.stderr, result.stderr
            assert "no working tree" in result.stderr, result.stderr
            assert not (gitdir / "private").exists(), (
                "the script wrote the private archive inside a git directory")
    return "a git internal directory of this checkout is refused, loudly, with nothing written"


@case
def case_refuses_a_subdirectory_of_a_legitimate_worktree():
    """A near miss is likelier than a wild one: the destination belongs to
    this checkout, but naming a subdirectory would bury the archive at
    <worktree>/analysis/private/ where nobody looks for it."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as wt:
            dst = wt / "analysis"
            before = _snapshot(dst)
            result = _run_script(src, dst, cwd=src)
            _assert_refused(result, dst, before, "a subdirectory of a real worktree")
            assert "subdirectory" in result.stderr, result.stderr
            assert str(wt) in result.stderr, "the message should name the real root"
    return "a subdirectory of a legitimate worktree is refused and left untouched"


# --------------------------------------------------------------------------
# issue #184, adversarial review round 2: the root guard above validates the
# destination ROOT, and `mkdir -p`/`cp` then follow a symbolic link at any path
# BELOW it. A genuine linked worktree passes every repository check while a
# link planted at private/1-raw-data carries the whole archive somewhere else
# -- reproduced at exit 0, with the script's own success line asserting that
# nothing outside the gitignored tree had been written.
#
# These are the paths where it is reachable: private/1-raw-data and
# private/verify are gitignored, so they do not exist in a fresh checkout,
# nothing pre-empts a link planted there, and `git status` never mentions it.
# private/ is a different shape (the committed private/README.md means a real
# directory is already there), which is why the accepting case below asserts
# that shape still stages.
#
# Every case here checks the EXTERNAL directory's own contents before and
# after, not the exit status and not the destination alone: the defect is
# precisely a run that reports success while writing somewhere else.
# --------------------------------------------------------------------------
def _external_target(td, name="escaped"):
    """A directory outside any worktree, standing in for wherever a planted
    link points, seeded with a file whose survival is checked afterwards."""
    ext = pathlib.Path(td) / name
    ext.mkdir()
    (ext / "someone_elses_file.txt").write_text("not a staging destination\n")
    return ext


def _assert_nothing_escaped(ext, before, why):
    after = _snapshot(ext)
    assert after == before, (
        f"{why}: files were written outside the destination worktree: "
        f"{sorted(after ^ before)}")
    assert (ext / "someone_elses_file.txt").read_text() == "not a staging destination\n", (
        f"{why}: an existing file outside the destination was overwritten")


@case
def case_refuses_a_symlinked_directory_planted_below_a_legitimate_root():
    """The reproduction. Each planted link is a DIRECTORY component of a path
    the script writes through, inside a destination whose root is a real,
    legitimate linked worktree of this checkout -- so the repository guard
    above has nothing to object to and the write paths are what redirect."""
    checked = []
    # electric-bills is here because of how the scans are scoped (issue #184,
    # /review finding 4): `[ -d ]` is TRUE for a symbolic link to a directory,
    # so a per-subtree scan would happily WALK a linked electric-bills instead
    # of refusing it. The subtree's own slot check is what closes that, and this
    # is the case that proves it did not go missing.
    for planted in ("private/1-raw-data", "private/verify",
                    "private/1-raw-data/electric-bills"):
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            ext = _external_target(td)
            with _linked_worktree(td) as dst:
                link = dst / planted
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(ext)
                outside_before = _snapshot(ext)
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src)
                _assert_refused(result, dst, before, f"a symlinked {planted}")
                assert "symbolic link" in result.stderr, result.stderr
                assert str(ext) in result.stderr, (
                    "the refusal must name where the link pointed: "
                    + result.stderr)
                _assert_nothing_escaped(ext, outside_before, f"a symlinked {planted}")
            checked.append(planted)
    return f"a symlink planted at {' or '.join(checked)} is refused with nothing written"


@case
def case_refuses_a_symlinked_file_the_copies_would_write_through():
    """The same defect one level down: `cp` follows a link at the DESTINATION
    FILE too, so a link left at private/household.yaml, at a name inside
    private/1-raw-data, or nested under a directory cp -R descends into, sends
    that file's contents outside the worktree just as effectively. Proven
    before the fix: household.yaml and gas.csv both landed in the external
    directory, and the nested bill PDF's target was in line to be overwritten."""
    planted_at = ("private/household.yaml",
                  "private/verify/usage.csv",
                  "private/1-raw-data/gas.csv",
                  "private/1-raw-data/electric-bills/statement.pdf")
    for planted in planted_at:
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            (src / "private" / "1-raw-data" / "electric-bills" / "statement.pdf") \
                .write_text("the source statement\n")
            ext = _external_target(td)
            (ext / "statement.pdf").write_text("not a staging destination\n")
            with _linked_worktree(td) as dst:
                target = dst / planted
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(ext / pathlib.Path(planted).name)
                outside_before = _snapshot(ext)
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src)
                _assert_refused(result, dst, before, f"a symlinked {planted}")
                assert "symbolic link" in result.stderr, result.stderr
                _assert_nothing_escaped(ext, outside_before, f"a symlinked {planted}")
                assert not (ext / pathlib.Path(planted).name).exists() \
                    or (ext / pathlib.Path(planted).name).read_text() \
                    == "not a staging destination\n", (
                        f"a symlinked {planted} let the copy overwrite its target")
    return (f"a symlink at any of the {len(planted_at)} file destinations "
            f"(including one nested under a cp -R directory) is refused")


@case
def case_accepts_a_worktree_whose_private_directory_already_exists():
    """The shape every real destination has, and the one the strict rule must
    not break: private/ is NOT gitignored away entirely -- the committed
    private/README.md placeholder (CLAUDE.md's repo map) means a checkout
    already carries it as a real directory. It is a real directory, not a
    link, so staging proceeds, creates the two gitignored subdirectories
    beneath it, and leaves the placeholder alone."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            readme = dst / "private" / "README.md"
            assert readme.is_file(), (
                "this checkout no longer commits private/README.md, so this "
                "case is testing a shape that no longer exists")
            was = readme.read_text()
            assert not (dst / "private" / "1-raw-data").exists(), (
                "the fixture must start without the gitignored subdirectories")
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, (
                f"an ordinary worktree with a real private/ must still stage: "
                f"{result.stderr}")
            assert readme.read_text() == was, "the committed placeholder was disturbed"
            staged = sorted(str(p.relative_to(dst)) for p in (dst / "private").rglob("*")
                            if p.is_file())
            for required in ("private/household.yaml",
                            "private/1-raw-data/gas.csv",
                            "private/verify/usage.csv"):
                assert required in staged, f"{required} missing from {staged}"
            for d in ("private", "private/1-raw-data", "private/verify"):
                assert not (dst / d).is_symlink(), d
    return (f"a worktree whose private/ is a real directory with its committed "
            f"README stages fully ({len(staged)} files)")


@case
def case_the_success_message_reports_what_was_checked_after_writing():
    """The line this replaces claimed "nothing outside the gitignored tree was
    written" and checked nothing -- it printed verbatim on the run that wrote
    the archive outside the tree. The claim now follows a re-resolution of
    every directory the copies ran through, and the file count is read off the
    destination rather than inferred from the list of cp lines."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, result.stderr
            real = sum(1 for p in (dst / "private").rglob("*") if p.is_file())
            assert f"({real} files now under it)" in result.stdout, (
                f"the message must count what is actually there ({real}): "
                f"{result.stdout}")
            assert "verified after writing" in result.stdout, result.stdout
    text = SCRIPT.read_text()
    tail = text.split("POST-WRITE VERIFICATION", 1)
    assert len(tail) == 2, "the post-write verification is gone from the script"
    assert "_physical" in tail[1], (
        "the closing message no longer re-resolves the directories it wrote "
        "through, so it is an assertion again rather than a check")
    return "the closing message counts the staged files and follows a real re-check"


@case
def case_accepts_a_freshly_created_worktree_of_this_checkout():
    """The normal case, and the one the guard must not break: a worktree
    created moments ago is a working tree of this checkout, so staging into
    it succeeds and every documented input arrives."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: true\n", has_gas_bills_dir=True)
        with _linked_worktree(td) as dst:
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, (
                f"a fresh worktree of this checkout must be accepted: {result.stderr}")
            staged = sorted(str(p.relative_to(dst)) for p in (dst / "private").rglob("*")
                            if p.is_file())
            for required in ("private/household.yaml",
                            "private/1-raw-data/gas.csv",
                            "private/1-raw-data/Electric_15_Minute_test.csv",
                            "private/verify/usage.csv",
                            "private/verify/samA.csv",
                            "private/verify/samB.csv"):
                assert required in staged, f"{required} missing from {staged}"
    return f"a freshly created worktree is accepted; {len(staged)} files staged"


@case
def case_accepts_the_worktree_the_script_itself_lives_in():
    """The guard's reference is the script's OWN location, so a checkout
    staging into itself is the same shape as the main checkout being the
    destination. The script under test is copied in rather than taken from
    the worktree's checked-out HEAD, so this case tests the working copy of
    the script on this branch, not whatever version HEAD happens to hold."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            its_own_copy = dst / SCRIPT.name
            its_own_copy.write_text(SCRIPT.read_text())
            result = _run_script(src, dst, cwd=src, script=its_own_copy)
            assert result.returncode == 0, (
                f"a checkout must be able to stage into itself: {result.stderr}")
            assert (dst / "private" / "household.yaml").is_file()
    return "the checkout the script itself lives in is accepted as the destination"


@case
def case_the_guard_is_what_refuses_and_it_runs_before_any_write():
    """The property that matters is ordering, not exit status: the check has
    to be reached before the first mkdir/cp, or a refusal still leaves a
    partial archive behind. Proven structurally -- the guard's exit lines all
    precede the first write in the file -- alongside the behavioral proof
    above that refused destinations stay byte-identical."""
    # Line-based and comment-stripped on purpose: matching the raw text would
    # find "mkdir -p" inside the guard's own explanatory comment and place the
    # first write before the guard that follows it.
    code = [(n, line) for n, line in enumerate(SCRIPT.read_text().splitlines())
            if line.strip() and not line.lstrip().startswith("#")]
    writes = [n for n, line in code if re.match(r'^\s*(mkdir|cp)\s', line)]
    guards = [n for n, line in code if "_common_git_dir()" in line]
    refusals = [n for n, line in code if "REFUSED" in line]
    assert writes, "the script no longer writes anything -- this case is stale"
    assert guards, "the destination guard has been removed from the script"
    assert refusals, "the script has no refusal path left"
    first_write, guard, last_refusal = min(writes), min(guards), max(refusals)
    assert guard < first_write, "the destination guard must precede the first write"
    assert last_refusal < first_write, (
        "every refusal must be decided before the first write, or a refused "
        "run can still leave a partial copy of the archive behind")
    # The path guard (issue #184, round 2) and the ignore guard (round 4) are
    # further decisions with the same ordering requirement: which PATHS inside
    # the accepted tree are real, and whether that tree can commit them, both
    # have to be settled before the copies -- not discovered by following a
    # link halfway through them, and not left for a `git status` after the
    # archive has landed. Checked on the CALLS (a trailing quote), never the
    # definitions, so moving a helper cannot satisfy it by accident.
    call_forms = ('_check_dir_slot "', '_reject_links_under "', '_reject_link "',
                  '_reject_multilinked_under "', '_reject_hardlink "',
                  '_reject_special_under "', '_reject_special "',
                  '_require_uncommittable "')
    checks = [n for n, line in code if any(f in line for f in call_forms)]
    copies = [n for n, line in code if re.match(r'^\s*cp\s', line)]
    assert checks, "the destination path guard's checks are gone from the script"
    for form in call_forms:
        assert any(form in line for _, line in code), (
            f"{form.strip()} is no longer called anywhere in the script")
    assert max(checks) < min(copies), (
        "every path check must run before the first cp, or the archive is "
        "already moving when a planted link is found")
    return ("the destination guard, the ignore guard, the path guard and all "
            "of their exits precede the first write")


# --------------------------------------------------------------------------
# issue #184, adversarial review: the guard asks git which repository a
# directory belongs to, and git answers that from the ENVIRONMENT before the
# filesystem. Every case below sets those variables so a naive probe LIES,
# and requires a refusal with NOTHING WRITTEN -- the destination's whole
# contents compared before and after, never the exit status alone. The
# inherited environment is the one thing the pre-existing cases above cannot
# see: subprocess.run passes this process's own clean environment through,
# so a guard that trusts $GIT_DIR passes all of them.
# --------------------------------------------------------------------------

# The variables a caller can forge. The first three were each demonstrated to
# subvert a probe on their own; the rest are the ones the script clears as
# defence in depth, forged here together so shrinking that list shows up as a
# behavioral failure rather than a silently narrower guard.
#
# The four GIT_*_PATHSPECS (issue #194) are the newest, and they forge a
# different thing: not which repository answers but what the PATH handed to it
# means. Each was measured -- LITERAL and NOGLOB empty a glob-backed `git
# ls-files`, and all four make `git check-ignore` exit 128, which turns every
# staging run into a refusal. Forged here alongside the rest, so the legitimate
# destination below has to survive them too.
_FORGEABLE = ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE",
              "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
              "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_CONFIG_GLOBAL",
              "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
              "GIT_LITERAL_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
              "GIT_GLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")


def _forced_config():
    """The NAME=VALUE pairs the script FORCES into its own environment (issue
    #193), parsed from its own loop.

    These are the other half of the sanitizing: the clearing above stops a
    caller replacing the configuration, and this stops the operator's ORDINARY
    global, XDG and system configuration -- a core.excludesFile there makes a
    committable destination answer "ignored" with nothing forged at all. Read
    from the script rather than retyped, so a value changed there is compared
    against what the run really exports.
    """
    text = SCRIPT.read_text()
    m = re.search(r"^for _kv in (.*?); do$", text, re.M | re.S)
    assert m, ("stage-private-data.sh no longer forces its git configuration -- "
               "the ignore verdict is back on the operator's ~/.gitconfig")
    out = {}
    for tok in m.group(1).replace("\\\n", " ").split():
        name, sep, value = tok.partition("=")
        assert sep, f"not a NAME=VALUE pair in the forcing loop: {tok!r}"
        out[name] = value
    return out


def _common_dir_of(path):
    """The absolute --git-common-dir of `path` -- the identity these cases
    forge, and the value the guard compares."""
    r = subprocess.run(["git", "-C", str(path), "rev-parse",
                        "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SkipCase(f"this checkout has no git identity to forge: {r.stderr.strip()}")
    return r.stdout.strip()


def _probe_lies(dst, env, forged):
    """Whether `git -C dst rev-parse --git-common-dir` under `env` really does
    report `forged` -- the naive probe the guard used to make.

    Checked as a precondition instead of assumed. If some future git stops
    honouring one of these, the case has to announce that it went stale rather
    than keep passing against a probe that no longer lies, which would leave
    the suite reporting a refusal it is no longer testing for."""
    r = subprocess.run(["git", "-C", str(dst), "rev-parse",
                        "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0 and r.stdout.strip() == forged


def _forged_env(dst, forged_common, td):
    """Every variable in _FORGEABLE set at once, all pointing the guard at the
    wrong answer: repository and common dir forged to `forged_common`, working
    tree (directly, and again through an injected core.worktree in both config
    channels) forged to `dst`. The content-selection variables get throwaway
    paths inside `td` rather than this checkout's real objects/index, so a case
    about a guard can never reach into the repository it is guarding."""
    cfg = pathlib.Path(td) / "injected-gitconfig"
    cfg.write_text(f"[core]\n\tworktree = {dst}\n")
    return dict(
        os.environ,
        GIT_DIR=forged_common,
        GIT_COMMON_DIR=forged_common,
        GIT_WORK_TREE=str(dst),
        GIT_CEILING_DIRECTORIES=str(pathlib.Path(dst).parent),
        GIT_DISCOVERY_ACROSS_FILESYSTEM="1",
        GIT_OBJECT_DIRECTORY=str(pathlib.Path(td) / "forged-objects"),
        GIT_ALTERNATE_OBJECT_DIRECTORIES=str(pathlib.Path(td) / "forged-alt-objects"),
        GIT_INDEX_FILE=str(pathlib.Path(td) / "forged-index"),
        GIT_NAMESPACE="forged",
        GIT_CONFIG_GLOBAL=str(cfg),
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="core.worktree",
        GIT_CONFIG_VALUE_0=str(dst),
        GIT_LITERAL_PATHSPECS="1",
        GIT_NOGLOB_PATHSPECS="1",
        GIT_GLOB_PATHSPECS="1",
        GIT_ICASE_PATHSPECS="1",
    )


@case
def case_refuses_a_plain_directory_that_git_dir_dresses_up_as_this_checkout():
    """The reproduced bypass. GIT_DIR names this checkout's repository and
    GIT_WORK_TREE names an unrelated scratch directory, so

        git -C <unrelated> rev-parse --git-common-dir  -> this checkout's .git
        git -C <unrelated> rev-parse --show-toplevel   -> <unrelated>

    -- all three of the guard's comparisons pass on a directory that has
    nothing to do with this repository. Before the environment was cleared
    this run exited 0 and copied the whole private archive there."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "unrelated-scratch-dir"
        dst.mkdir()
        (dst / "someone_elses_notes.txt").write_text("unrelated project\n")
        forged = _common_dir_of(ROOT)
        env = dict(os.environ, GIT_DIR=forged, GIT_WORK_TREE=str(dst))
        if not _probe_lies(dst, env, forged):
            raise SkipCase("this git no longer lets GIT_DIR make an unrelated "
                           "directory report this checkout, so there is no "
                           "forgery left for this case to refuse")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src, env=env)
        _assert_refused(result, dst, before, "a directory dressed up by GIT_DIR")
        assert "not a git repository" in result.stderr, result.stderr
    return ("an unrelated directory that GIT_DIR/GIT_WORK_TREE make look like "
            "this checkout is refused and left untouched")


@case
def case_refuses_a_foreign_repository_that_git_common_dir_dresses_up():
    """GIT_COMMON_DIR needs no GIT_DIR, and is worse than it: it IS the value
    the guard compares, and it answers the probe on BOTH sides at once -- the
    destination's and the script's own -- so the identity comparison passes
    whatever the two directories really are. It therefore defeats precisely
    the two destinations this suite already refuses by name, which is why both
    are re-run here under the forgery rather than one standing in for the
    other: a real unrelated repository, and a different clone of this remote."""
    forged = _common_dir_of(ROOT)
    checked = []
    for label in ("an unrelated repository", "a different clone of this remote"):
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            dst = pathlib.Path(td) / "other-project"
            if label.startswith("an unrelated"):
                subprocess.run(["git", "init", "-q", str(dst)], check=True,
                               capture_output=True, text=True)
            else:
                cloned = subprocess.run(
                    ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)],
                    capture_output=True, text=True)
                if cloned.returncode != 0:
                    raise SkipCase(f"this checkout cannot be cloned here: {cloned.stderr}")
            (dst / "someone_elses_notes.txt").write_text("a different project\n")
            env = dict(os.environ, GIT_COMMON_DIR=forged)   # GIT_DIR is NOT set
            if not _probe_lies(dst, env, forged):
                raise SkipCase("this git no longer lets GIT_COMMON_DIR override "
                               f"the reported identity of {label}")
            before = _snapshot(dst)
            result = _run_script(src, dst, cwd=src, env=env)
            _assert_refused(result, dst, before, f"{label}, forged by GIT_COMMON_DIR")
            assert "DIFFERENT repository" in result.stderr, result.stderr
            checked.append(label)
    return f"GIT_COMMON_DIR cannot make {' or '.join(checked)} pass the guard"


@case
def case_refuses_a_plain_directory_with_every_forgeable_variable_set_at_once():
    """The whole set the script clears, forged together: repository, common
    dir, working tree (directly and through core.worktree injected via both
    config channels), the two discovery modifiers, and the content-selection
    variables. Behavioral first -- the refusal and the untouched destination
    are what matter -- then structural, because a variable dropped from the
    script's list would otherwise only show up the day someone exploits it."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = pathlib.Path(td) / "unrelated-scratch-dir"
        dst.mkdir()
        (dst / "someone_elses_notes.txt").write_text("unrelated project\n")
        env = _forged_env(dst, _common_dir_of(ROOT), td)
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src, env=env)
        _assert_refused(result, dst, before, "a fully forged environment")
        # Read out of the IGNORING lines only. The script also announces the
        # configuration it FORCES (issue #193), and those lines name
        # GIT_CONFIG_GLOBAL and friends too -- searching the whole of stderr
        # would let the forcing announcement stand in for the ignoring one and
        # this assertion would pass on a script that cleared nothing.
        announced = " ".join(
            ln for ln in result.stderr.splitlines()
            if "stage-private-data.sh: ignoring inherited" in ln)
        for name in _FORGEABLE:
            assert name in announced, (
                f"{name} was set but the run never said it ignored it: {result.stderr}")

    text = SCRIPT.read_text()
    cleared = text.split("for _v in", 1)[1].split("; do", 1)[0]
    for name in _FORGEABLE:
        assert name in cleared or (name.startswith("GIT_CONFIG")
                                   and "${!GIT_CONFIG*}" in cleared), (
            f"{name} is no longer cleared by the script")
    # The clearing has to happen before the first git invocation, or the first
    # probe still reads the forged environment. Comment-stripped, since the
    # explanation above the loop names these variables too.
    code = [(n, line) for n, line in enumerate(text.splitlines())
            if line.strip() and not line.lstrip().startswith("#")]
    # Every probe now goes through the _git wrapper (see the case below), so the
    # first probe is the first line that CALLS it -- matched without the leading
    # underscore, which `\bgit` would not see, and excluding the wrapper's own
    # definition and the `command git` inside it.
    git_lines = [n for n, line in code
                 if re.search(r'(?<![\w.-])_git\s+-C\b', line)]
    assert git_lines, ("no _git -C probe found in the script -- this case can no "
                       "longer tell whether the sanitizing happens first")
    first_git = min(git_lines)
    first_unset = min(n for n, line in code if line.strip().startswith("unset "))
    assert first_unset < first_git, (
        "the environment must be cleared before the first git probe, not after")
    # Same requirement for the configuration the script FORCES (issue #193):
    # exported after the clearing, because every forced name is in the
    # GIT_CONFIG* family the clearing drops by prefix, and before the first
    # probe, or the first probe still answers from the operator's ~/.gitconfig.
    first_export = min(n for n, line in code if line.strip().startswith("export "))
    assert first_unset < first_export < first_git, (
        f"the forced configuration must be exported after the clearing "
        f"(line {first_unset}) and before the first git probe (line {first_git}), "
        f"not at line {first_export}")
    return (f"all {len(_FORGEABLE)} forgeable variables are refused together, "
            f"announced, and cleared -- and the forced configuration exported -- "
            f"before the first git probe")


@case
def case_every_git_invocation_carries_the_configuration_override():
    """Issue #193, adversarial review. The environment half of the configuration
    isolation is read only by git 2.31 and newer; an older git ignores those
    variables in silence, and the guard's ignore verdict goes back to whatever
    the operator's ~/.gitconfig says. What closes that on every version is
    `-c core.excludesFile=...` on the command line, and a command-line option is
    per-invocation: a probe that goes round the wrapper is a probe without it.

    So this reads the script rather than running it. Every git invocation in the
    executable text must be a call to the _git wrapper; the wrapper's own
    `command git -c ...` line is the one exception, and it must carry at least
    one override. `git` inside a quoted message is not an invocation and is
    skipped, which is why the quote parity is counted rather than the word.

    The behavioural half -- that the override really does defeat an ambient
    excludes file on a git that ignores the variables -- is
    test_private_egress.case_a_git_that_ignores_the_config_variables_cannot_be_
    told_a_committable_path_is_ignored, which runs this script under a PATH shim.
    """
    text = SCRIPT.read_text()
    m = re.search(r"^_git\(\) \{\n\s*command git ((?:-c \S+ )+)\"\$@\"\n\}$",
                  text, re.M)
    assert m, ("stage-private-data.sh no longer defines a _git wrapper that passes "
               "-c options -- the ignore verdict is back on whatever the running "
               "git's version happens to make of GIT_CONFIG_GLOBAL and friends")
    overrides = [tok for tok in m.group(1).split() if tok != "-c"]
    assert overrides, "the wrapper passes no configuration override at all"

    strays = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if line.lstrip().startswith("#"):
            continue
        for hit in re.finditer(r"(?<![\w./-])git\b", code):
            if code[:hit.start()].count('"') % 2:
                continue                       # inside a quoted message
            if code[:hit.start()].rstrip().endswith("command"):
                continue                       # the wrapper's own line
            strays.append(f"line {n}: {line.strip()[:90]}")
    assert not strays, (
        "these git invocations do not go through the _git wrapper, so they run "
        "without the configuration override:\n  " + "\n  ".join(strays))
    calls = len(re.findall(r"(?<![\w./-])_git\s", text))
    return (f"every git invocation in the script goes through _git, which passes "
            f"{len(overrides)} override(s): {' '.join(overrides)} ({calls} call sites)")


@case
def case_a_forged_environment_does_not_break_a_legitimate_destination():
    """The behavioral difference between CLEARING these variables and
    REJECTING them. A real linked worktree of this checkout is a legitimate
    destination whether or not the caller's environment is polluted -- and it
    routinely is, since every git hook exports GIT_DIR -- so the run has to
    succeed and stage everything. A guard that refused on sight would fail
    here, which is the cost that decided the design."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            env = _forged_env(dst, _common_dir_of(ROOT), td)
            result = _run_script(src, dst, cwd=src, env=env)
            assert result.returncode == 0, (
                f"clearing the environment must not turn a legitimate worktree "
                f"into a refusal: {result.stderr}")
            assert "ignoring inherited git variable" in result.stderr, (
                "the variables were cleared silently -- an operator whose "
                "environment is polluted has to be told it was ignored")
            for required in ("private/household.yaml",
                             "private/1-raw-data/gas.csv",
                             "private/verify/usage.csv"):
                assert (dst / required).is_file(), f"{required} was not staged"
    return "a legitimate worktree is still accepted and fully staged under a forged environment"


@case
def case_the_cleared_environment_reaches_the_work_the_guard_authorized():
    """A guard that sanitizes only its own probes and then hands the untouched
    environment to the work it approved has checked one repository and written
    into another. The script's post-guard work spawns one child --
    household.py under $SRC/.venv/bin/python -- so that child is where the
    authorized environment can be read back.

    The stand-in interpreter is REPLACED (os.replace, atomically, never an
    unlink) with a shell wrapper that records what it inherited and then execs
    the real interpreter, so the staging still completes and the recording
    comes from a genuinely accepted run rather than a synthetic probe.

    TWO different requirements, and the difference is the whole point (issue
    #193). Nothing the CALLER set may survive -- that is the original claim, and
    every forged value must read back <unset>. But the script also FORCES its own
    configuration, and those values must survive, for the same reason the
    clearing has to: a run that isolated its own probes and then handed the
    ambient configuration to the work it authorized would be checking one set of
    ignore rules and copying under another. So a forced name is required to read
    back as the script's value, not as <unset>, and a name that is neither
    forged-and-cleared nor forced would be classified by neither rule -- which
    is asserted rather than left to arithmetic."""
    forced = _forced_config()
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        record = pathlib.Path(td) / "inherited-by-the-child.txt"
        wrapper = pathlib.Path(td) / "python-wrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"REC={shlex.quote(str(record))}\n"
            ': > "$REC"\n'
            + "".join('printf "%s\\n" "{v}=${{{v}-<unset>}}" >> "$REC"\n'.format(v=v)
                      for v in sorted(set(_FORGEABLE) | set(forced)))
            + f'exec {shlex.quote(sys.executable)} "$@"\n')
        wrapper.chmod(0o755)
        os.replace(wrapper, src / ".venv" / "bin" / "python")
        with _linked_worktree(td) as dst:
            env = _forged_env(dst, _common_dir_of(ROOT), td)
            result = _run_script(src, dst, cwd=src, env=env)
            assert result.returncode == 0, result.stderr
            assert record.is_file(), (
                "the wrapper never ran, so this case proved nothing about the "
                "environment the copy step runs in")
            seen = dict(line.split("=", 1) for line in
                        record.read_text().splitlines() if "=" in line)
            assert set(seen) == set(_FORGEABLE) | set(forced), seen
            leaked = {k: v for k, v in seen.items()
                      if k not in forced and v != "<unset>"}
            assert not leaked, (
                f"the guard cleared these for its own probes but handed them to "
                f"the work it authorized: {leaked}")
            wrong = {k: seen[k] for k, v in forced.items() if seen[k] != v}
            assert not wrong, (
                f"the guard forced these for its own probes and then handed the "
                f"ambient configuration to the work it authorized: {wrong} "
                f"(expected {forced})")
    return (f"all {len(set(_FORGEABLE) - set(forced))} forgeable variables are gone "
            f"from the environment the post-guard work inherits, and the "
            f"{len(forced)} values the script forces reach it unchanged")


# --------------------------------------------------------------------------
# issue #184, adversarial review round 3: the guard's identity comparison and
# its --show-toplevel check both read answers the DESTINATION itself supplies,
# so a plain directory holding a one-line `.git` gitfile pointing at this
# checkout's common directory satisfies both without ever having been
# registered -- reproduced at exit 0 with the whole archive written there.
# Unlike the forged environment above, the forgery is on disk, so clearing
# variables does not touch it; membership has to be decided against git's own
# register instead. The cases below check the destination's whole contents
# before and after, never the exit status alone.
# --------------------------------------------------------------------------
def _forged_gitfile_dir(td, common_dir, name="dir-with-a-forged-gitfile"):
    """A plain directory that CLAIMS to be a worktree of `common_dir`.

    This is what a copied, rsynced or restored linked-worktree directory looks
    like once git no longer knows about it: the `.git` gitfile came along for
    the ride and still names this checkout, so `--git-common-dir` matches and
    `--show-toplevel` reports the directory itself. Nothing here is exotic --
    it is one text file."""
    d = pathlib.Path(td) / name
    d.mkdir()
    (d / "someone_elses_notes.txt").write_text("unrelated project\n")
    (d / ".git").write_text(f"gitdir: {common_dir}\n")
    return d


def _claims_to_be_a_worktree(d, common_dir):
    """Whether the fixture really does answer both probes the way a genuine
    worktree does, and really is absent from the register. Asserted as a
    precondition rather than assumed: if a future git stops honouring bare
    gitfiles, this case has to announce that it went stale instead of passing
    against a forgery that no longer forges anything."""
    def rev(*args):
        r = subprocess.run(["git", "-C", str(d), "rev-parse", *args],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    top = rev("--show-toplevel")
    listed = subprocess.run(["git", "-C", str(common_dir), "worktree", "list",
                             "--porcelain"], capture_output=True, text=True)
    return (rev("--path-format=absolute", "--git-common-dir") == common_dir
            and top is not None
            and pathlib.Path(top).resolve() == pathlib.Path(d).resolve()
            and str(pathlib.Path(d).resolve()) not in listed.stdout)


@case
def case_refuses_a_plain_directory_whose_git_gitfile_forges_membership():
    """The reproduction. The directory passes the common-dir comparison and
    the --show-toplevel check -- both asserted here first, so this case fails
    loudly if it ever stops being a forgery -- and is refused anyway, because
    `git worktree list` for this checkout does not name it."""
    common = _common_dir_of(ROOT)
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = _forged_gitfile_dir(td, common)
        if not _claims_to_be_a_worktree(dst, common):
            raise SkipCase("this git no longer lets a bare .git gitfile make a "
                           "plain directory report this checkout's common dir "
                           "and itself as the toplevel")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src)
        _assert_refused(result, dst, before, "a directory with a forged .git gitfile")
        assert "REGISTERED" in result.stderr, result.stderr
        assert not (dst / "private").exists(), (
            "the private archive was written into a directory that only claimed "
            "to be a worktree")
        assert (dst / "someone_elses_notes.txt").read_text() == "unrelated project\n"
    return ("a plain directory whose .git gitfile forges membership of this "
            "checkout is refused with nothing written")


@case
def case_refuses_a_forged_gitfile_under_a_forged_environment_too():
    """The two guards have to compose. The environment hardening cannot see an
    on-disk gitfile and the register check does not read the environment, so a
    caller holding both forgeries at once must still be refused -- and the
    register listing itself must run in the SANITIZED shell, or the hardening
    was reintroduced with a hole in it. Checked behaviorally, then structurally
    on every git invocation in the script rather than only the first."""
    common = _common_dir_of(ROOT)
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst = _forged_gitfile_dir(td, common)
        if not _claims_to_be_a_worktree(dst, common):
            raise SkipCase("this git no longer honours a bare .git gitfile")
        env = _forged_env(dst, common, td)
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src, env=env)
        _assert_refused(result, dst, before, "a forged gitfile plus a forged environment")
        assert "REGISTERED" in result.stderr, result.stderr
        assert "ignoring inherited git variable" in result.stderr, (
            "the environment was cleared silently: " + result.stderr)

    code = [(n, line) for n, line in enumerate(SCRIPT.read_text().splitlines())
            if line.strip() and not line.lstrip().startswith("#")]
    first_unset = min(n for n, line in code if line.strip().startswith("unset "))
    git_calls = [n for n, line in code
                 if re.search(r'(?<![\w.-])_git\s+-C\b', line)]
    assert git_calls, "the script no longer asks git anything"
    assert min(git_calls) > first_unset, (
        "a git invocation runs before the environment is cleared, so it reads "
        "whatever the caller forged")
    return ("a forged gitfile and a forged environment together are still "
            "refused, and every git call runs after the clearing")


@case
def case_accepts_a_registered_linked_worktree_and_the_register_names_it():
    """The positive half of the same check, and the regression it must not
    cause: a worktree created with `git worktree add` IS in the register, so it
    stages fully. The register membership is asserted directly, so an
    acceptance here can never be a guard that stopped checking."""
    common = _common_dir_of(ROOT)
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            listed = subprocess.run(["git", "-C", common, "worktree", "list",
                                     "--porcelain"], capture_output=True, text=True)
            assert str(dst.resolve()) in listed.stdout, (
                f"the fixture is not actually registered, so accepting it "
                f"proves nothing: {listed.stdout}")
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, (
                f"a registered worktree must still stage: {result.stderr}")
            staged = sorted(str(p.relative_to(dst)) for p in (dst / "private").rglob("*")
                            if p.is_file())
            for required in ("private/household.yaml",
                             "private/1-raw-data/gas.csv",
                             "private/verify/usage.csv"):
                assert required in staged, f"{required} missing from {staged}"
    return (f"a worktree named in 'git worktree list' is accepted and stages "
            f"fully ({len(staged)} files)")


def _throwaway_checkout(td, gitignore_text, name="a-main-checkout"):
    """A repository of its OWN, built inside the caller's tempdir: a main
    checkout plus one linked worktree carrying this branch's copy of the
    script.

    The guard fixes identity from the script's own location, so putting the
    copy in the LINKED worktree makes that repository's MAIN CHECKOUT a
    legitimate, registered destination. That is what lets the cases below
    reach the checks that run after the register check without pointing
    anything at this machine's real repository, and without touching this
    repo's own .gitignore.

    `gitignore_text` is the destination's .gitignore, or None for a repository
    that has none at all -- the shape of the 2026-08-13 incident's
    destination. Returns (main_checkout, the_script_copy_to_run)."""
    main = pathlib.Path(td) / name
    made = subprocess.run(["git", "init", "-q", str(main)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        raise SkipCase(f"could not build a repository fixture: {made.stderr}")
    (main / "README.md").write_text("a checkout of its own\n")
    if gitignore_text is not None:
        (main / ".gitignore").write_text(gitignore_text)
    for cmd in (["add", "-A"],
                ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "commit", "-qm", "initial"]):
        r = subprocess.run(["git", "-C", str(main), *cmd],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"could not commit in the fixture: {r.stderr}")
    linked = pathlib.Path(td) / f"{name}-linked-worktree"
    r = subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach",
                        str(linked), "HEAD"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SkipCase(f"could not add a worktree to the fixture: {r.stderr}")
    its_own_copy = linked / SCRIPT.name
    its_own_copy.write_text(SCRIPT.read_text())
    return main, its_own_copy


def _throwaway_worktree(td, wt_name, name="odd-path-repo"):
    """A repository of its own whose LINKED WORKTREE is named `wt_name`, with
    the script copy in its MAIN checkout so that repository fixes the guard's
    identity. Returns (the_linked_worktree, the_script_copy_to_run).

    Built here rather than with _linked_worktree for a specific reason: these
    names are deliberately awkward (a space, a newline), and registering one in
    the developer's REAL .git/worktrees to test a parser is not a trade worth
    making. Everything below lives and dies inside the caller's tempdir.

    The .gitignore covers private/, because since the ignore guard a
    destination that would let the archive be committed is refused."""
    main = pathlib.Path(td) / name
    made = subprocess.run(["git", "init", "-q", str(main)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        raise SkipCase(f"could not build a repository fixture: {made.stderr}")
    (main / "README.md").write_text("a checkout of its own\n")
    (main / ".gitignore").write_text("private/\n")
    for cmd in (["add", "-A"],
                ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "commit", "-qm", "initial"]):
        r = subprocess.run(["git", "-C", str(main), *cmd],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"could not commit in the fixture: {r.stderr}")
    its_own_copy = main / SCRIPT.name
    its_own_copy.write_text(SCRIPT.read_text())
    wt = pathlib.Path(td) / wt_name
    r = subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach",
                        str(wt), "HEAD"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SkipCase(f"this filesystem will not hold a worktree named "
                       f"{wt_name!r}: {r.stderr}")
    return wt, its_own_copy


@case
def case_accepts_a_registered_worktree_whose_path_the_line_parser_cannot_read():
    """Codex review, issue #184: the register parse was line-based, so a
    registered worktree whose path the porcelain listing cannot express on one
    line would be WRONGLY REFUSED -- a guard failing on correct input, which is
    the failure mode that gets guards disabled rather than fixed.

    The mechanism review named -- C-quoted paths -- is NOT what this git does,
    and the premises below say so out loud rather than leaving it to a comment:
    `git worktree list --porcelain` emits the worktree PATH raw, including a
    newline. (What it quotes is the `locked` REASON; git's own manual documents
    the split, and documents `-z` as existing precisely so "a worktree path
    contains a newline character" can be parsed.)

    The defect is therefore real but arrives the other way round: the raw
    newline splits one entry across two lines, and a line parser recovers a
    TRUNCATED PREFIX -- a path the register never contained. Reproduced before
    the fix, on this exact fixture: registered, and refused.

    The space-named worktree is the case that already worked and must keep
    working; it is here so a fix for the newline cannot regress it."""
    checked = []
    for label, wt_name in (("a space", "a worktree with spaces"),
                           ("a newline", "a worktree with a\nnewline")):
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            try:
                dst, script = _throwaway_worktree(td, wt_name)
            except SkipCase:
                if label == "a space":
                    raise
                # A filesystem that will not hold the name cannot test it.
                continue
            resolved = str(dst.resolve())
            listed = subprocess.run(["git", "-C", str(dst.parent / "odd-path-repo"),
                                     "worktree", "list", "--porcelain"],
                                    capture_output=True, text=True)
            # The premises, measured on this git rather than assumed: the
            # fixture really is registered, and its path really is emitted RAW.
            assert f"worktree {resolved}" in listed.stdout, (
                f"the fixture is not registered under its own name, so "
                f"accepting it would prove nothing: {listed.stdout!r}")
            if label == "a newline":
                assert not any(line == f"worktree {resolved}"
                               for line in listed.stdout.splitlines()), (
                    "this git now escapes a newline in the worktree PATH, so a "
                    "line-based parse could read it after all and this case no "
                    "longer pins what it claims: " + repr(listed.stdout))
            result = _run_script(src, dst, cwd=src, script=script, timeout=120)
            assert result.returncode == 0, (
                f"a registered worktree whose path contains {label} must be "
                f"accepted: {result.stderr}")
            for required in ("private/household.yaml",
                             "private/1-raw-data/gas.csv",
                             "private/verify/usage.csv"):
                assert (dst / required).is_file(), (
                    f"{required} was not staged into the {label} worktree")
            checked.append(label)

    # Structural, because the behavioral half above would also pass on a parse
    # that captured the -z output into a variable -- on a path without a
    # newline. bash cannot hold a NUL and drops them SILENTLY (measured on bash
    # 3.2, the macOS system shell: `x=$(printf 'a\0b')` yields `ab`, no
    # warning), so `$(git worktree list --porcelain -z)` glues every record into
    # one string and matches nothing. The listing has to be read through a
    # process substitution, and this is the line that says it is.
    text = SCRIPT.read_text()
    assert 'done < <(_git -C "$SELF_GIT" worktree list --porcelain -z' in text, (
        "the register listing is no longer read with -z through a process "
        "substitution -- a command substitution would silently drop the NUL "
        "separators and match nothing")
    assert 'worktree list --porcelain 2>/dev/null' in text, (
        "the line-based fallback for a git too old to know -z is gone, so such "
        "a git now gets an unexplained refusal on a legitimate destination")
    return (f"a registered worktree whose path contains {' or '.join(checked)} "
            f"is accepted and staged, and the register is read with -z")


def _check_ignore_rc(repo, rel):
    """`git check-ignore`'s three-way status for `rel` inside `repo`:
    0 ignored, 1 not ignored, anything else the question went unanswered."""
    return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--", rel],
                          capture_output=True, text=True).returncode


@case
def case_accepts_a_main_checkout_as_the_destination():
    """A main checkout is itself an entry in `git worktree list`, and staging
    into one is supported (README "Refreshing this analysis"), so the register
    check must not quietly restrict the script to LINKED worktrees.

    Proven on a throwaway repository built here rather than on this machine's
    real checkout, for the reason the rest of this suite stages into temporary
    worktrees: the case has to write the archive into whatever it accepts, and
    the real main checkout is not a place a test may write. The fixture is the
    same shape -- the script copy lives in a linked worktree, so its own
    location fixes the identity, and the destination is that repository's main
    checkout.

    The fixture carries a .gitignore covering private/, because since the
    ignore guard a destination that would let the archive be committed is
    refused. That makes this case the positive half of that guard as well: an
    ordinary checkout that does ignore these paths still stages in full.

    It also exercises the physical-path comparison for free: on macOS the
    temporary directory sits under a symlinked /var, so the register entry and
    the destination only agree once both are resolved."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        main, its_own_copy = _throwaway_checkout(td, "private/\n")
        listed = subprocess.run(["git", "-C", str(main), "worktree", "list",
                                 "--porcelain"], capture_output=True, text=True)
        assert "worktree " in listed.stdout, listed.stdout
        result = _run_script(src, main, cwd=src, script=its_own_copy)
        assert result.returncode == 0, (
            f"a main checkout is a registered worktree and must be accepted: "
            f"{result.stderr}")
        for required in ("private/household.yaml",
                         "private/1-raw-data/gas.csv",
                         "private/verify/usage.csv"):
            assert (main / required).is_file(), f"{required} was not staged"
        status = subprocess.run(["git", "-C", str(main), "status", "--porcelain",
                                 "--untracked-files=all"], capture_output=True, text=True)
        assert "private/" not in status.stdout, (
            f"the accepted destination can still offer the archive to git add: "
            f"{status.stdout}")
    return "the main checkout of a repository is accepted as the destination"


@case
def case_a_separate_git_dir_checkout_is_the_documented_collateral_refusal():
    """The cost of the register check, pinned so it stays a decision.

    A `--separate-git-dir` working tree is legitimate and is refused, because
    git reports the EXTERNAL GITDIR as the main worktree's path and never the
    working tree -- asserted here from both vantages, so this case fails if a
    future git learns to report it and the refusal becomes fixable rather than
    inherent. Until then such a directory's only claim on this checkout is a
    `.git` gitfile, which is exactly the forgery above, and nothing available
    to the guard tells them apart. Refusing both is the fail-closed direction;
    the remedy is in the refusal message."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        wt = pathlib.Path(td) / "sep-worktree"
        gd = pathlib.Path(td) / "external-gitdir"
        made = subprocess.run(["git", "init", "-q", "--separate-git-dir", str(gd), str(wt)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            raise SkipCase(f"this git cannot build a --separate-git-dir fixture: {made.stderr}")
        (wt / "README.md").write_text("a separate-git-dir checkout\n")
        for cmd in (["add", "README.md"],
                    ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                     "commit", "-qm", "initial"]):
            r = subprocess.run(["git", "-C", str(wt), *cmd], capture_output=True, text=True)
            if r.returncode != 0:
                raise SkipCase(f"could not commit in the fixture: {r.stderr}")
        linked = pathlib.Path(td) / "its-linked-worktree"
        r = subprocess.run(["git", "-C", str(wt), "worktree", "add", "--detach",
                            str(linked), "HEAD"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"could not add a worktree to the fixture: {r.stderr}")

        # The premise: git does not name the working tree, from either vantage.
        for vantage in (gd, linked):
            listed = subprocess.run(["git", "-C", str(vantage), "worktree", "list",
                                     "--porcelain"], capture_output=True, text=True)
            assert listed.returncode == 0, listed.stderr
            assert str(wt.resolve()) not in listed.stdout, (
                f"git now names the --separate-git-dir working tree from "
                f"{vantage}, so this refusal is no longer inherent and the "
                f"guard should accept it: {listed.stdout}")

        its_own_copy = linked / SCRIPT.name
        its_own_copy.write_text(SCRIPT.read_text())
        before = _snapshot(wt)
        result = _run_script(src, wt, cwd=src, script=its_own_copy)
        _assert_refused(result, wt, before, "a --separate-git-dir working tree")
        assert "REGISTERED" in result.stderr, result.stderr
        assert "git worktree add" in result.stderr, (
            "the refusal must carry its remedy: " + result.stderr)
    return ("a --separate-git-dir working tree is refused -- documented "
            "collateral of deciding membership by git's register")


# --------------------------------------------------------------------------
# issue #184, adversarial review round 4: every guard above answers "which
# repository is this destination", and none answered "can this archive become
# committable there" -- which is the property the 2026-08-13 incident actually
# lost. The repository that took the archive did not gitignore private/, so the
# data sat one `git add -A` from a commit into an unrelated public repo. Every
# mention of gitignore in the script was prose until now: comments, and a
# closing message asserting containment it never checked.
#
# Both cases build a THROWAWAY repository in a temp directory -- this repo's
# own .gitignore is never touched -- whose main checkout is a genuine,
# registered destination, so the refusal they get can only come from the new
# check and not from one of the guards before it.
# --------------------------------------------------------------------------
@case
def case_refuses_a_destination_that_does_not_ignore_the_paths_it_writes():
    """The reproduction. Before the fix a registered main checkout of a
    repository with no .gitignore took the whole archive at exit 0, and
    `git status --untracked-files=all` then listed all nine staged files as
    ready for the next `git add -A`.

    Checked in both shapes an unprotected destination really takes: no
    .gitignore at all, and one that exists but says nothing about private/."""
    checked = []
    for label, gi in (("no .gitignore at all", None),
                      ("a .gitignore that never mentions private/", "*.log\n")):
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            dst, script = _throwaway_checkout(td, gi)
            # The premises, asserted rather than assumed: this destination
            # passes every guard that runs BEFORE the ignore check, and really
            # does fail to ignore the paths. Without both, a refusal here could
            # be the register check firing and would prove nothing.
            registered = subprocess.run(["git", "-C", str(dst), "worktree", "list",
                                         "--porcelain"], capture_output=True, text=True)
            assert str(dst.resolve()) in registered.stdout, (
                f"the fixture is not a registered worktree, so this case would "
                f"be testing the register guard instead: {registered.stdout}")
            for rel in ("private/1-raw-data", "private/verify", "private/household.yaml"):
                assert _check_ignore_rc(dst, rel) == 1, (
                    f"{rel} is already ignored in this fixture, so it does not "
                    f"test what it claims")
            before = _snapshot(dst)
            result = _run_script(src, dst, cwd=src, script=script)
            _assert_refused(result, dst, before, f"a destination with {label}")
            assert "does not gitignore" in result.stderr, result.stderr
            assert not (dst / "private").exists(), (
                "the archive was written into a repository whose git would "
                "offer it to the next commit")
            status = subprocess.run(["git", "-C", str(dst), "status", "--porcelain",
                                     "--untracked-files=all"],
                                    capture_output=True, text=True)
            assert "private/" not in status.stdout, (
                f"the destination's own git can see private material after a "
                f"refused run: {status.stdout}")
            checked.append(label)
    return (f"a registered worktree that would let the archive be committed "
            f"({' / '.join(checked)}) is refused with nothing written")


@case
def case_refuses_a_destination_that_tracks_a_path_it_writes():
    """The other half of "cannot become committable". A path can be covered by
    .gitignore and still be TRACKED -- `git add -f` is one command away, and a
    repository that ever committed a private/verify/usage.csv still carries it
    -- because a tracked file stays in the index whatever the ignore rules say.
    Staging over one puts the archive straight into the next commit's diff.

    It is also why the script asks `git ls-files` FIRST, which this case pins:
    measured on this very fixture, `git check-ignore` returns 1 (not ignored)
    for the tracked path AND for the directory around it, so asking it first
    would blame a .gitignore that is in fact correct and send the operator to
    edit the wrong file."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst, script = _throwaway_checkout(td, "private/\n")
        committed = dst / "private" / "verify" / "usage.csv"
        committed.parent.mkdir(parents=True)
        committed.write_text("a usage.csv that was force-added long ago\n")
        for cmd in (["add", "-f", "private/verify/usage.csv"],
                    ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                     "commit", "-qm", "force-added"]):
            r = subprocess.run(["git", "-C", str(dst), *cmd], capture_output=True, text=True)
            if r.returncode != 0:
                raise SkipCase(f"could not force-add in the fixture: {r.stderr}")
        # The premise: .gitignore genuinely covers this, so only the tracked
        # check can distinguish it -- and check-ignore alone would misreport it.
        assert _check_ignore_rc(dst, "private/1-raw-data") == 0, (
            "the fixture's .gitignore no longer covers private/")
        assert _check_ignore_rc(dst, "private/verify") == 1, (
            "check-ignore no longer reports a directory holding a tracked file "
            "as unignored, so the ordering this case pins is no longer needed")
        before = _snapshot(dst)
        result = _run_script(src, dst, cwd=src, script=script)
        _assert_refused(result, dst, before, "a destination tracking a written path")
        assert "TRACKS" in result.stderr, result.stderr
        assert "private/verify/usage.csv" in result.stderr, (
            "the refusal must name the tracked path: " + result.stderr)
        assert "does not gitignore" not in result.stderr, (
            "a tracked path must not be reported as an unignored one -- the "
            "remedies are different: " + result.stderr)
        assert committed.read_text() == "a usage.csv that was force-added long ago\n", (
            "the refused run overwrote the destination's own committed file")
        assert not (dst / "private" / "1-raw-data").exists()
    return ("a destination that tracks a path this script writes is refused, "
            "and is reported as tracked rather than as unignored")


# --------------------------------------------------------------------------
# issue #184, adversarial review round 4: _reject_link tests `[ -L ]`, which
# sees SYMBOLIC links only. An existing output file that is a HARD link to a
# file outside the worktree passes every check -- there is no link to follow --
# and `cp` then truncates and rewrites the shared inode, so the outside file's
# contents become this household's private data while the script reports
# containment. Reproduced at exit 0 on three separate paths, each rewriting a
# file in an unrelated directory while the closing line still read "nothing was
# written outside the gitignored tree".
# --------------------------------------------------------------------------
@case
def case_refuses_a_hard_linked_output_file_the_copies_would_write_through():
    """The reproduction, on every shape of output the script writes: the named
    household.yaml, a named private/verify copy, a file inside private/1-raw-data
    whose name a cp line picks, and one nested under a directory `cp -R`
    descends into.

    The external file's CONTENTS are the assertion, not the exit status: the
    defect leaves the destination tree looking exactly right and rewrites
    something else. Each fixture also asserts the planted file is a hard link
    and NOT a symbolic one, and that the refusal is the hard-link refusal --
    otherwise this case could pass on the pre-existing symlink guard and prove
    nothing."""
    planted_at = ("private/household.yaml",
                  "private/verify/usage.csv",
                  "private/1-raw-data/gas.csv",
                  "private/1-raw-data/electric-bills/statement.pdf")
    for planted in planted_at:
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            (src / "private" / "1-raw-data" / "electric-bills" / "statement.pdf") \
                .write_text("the source statement\n")
            ext = _external_target(td)
            outside = ext / "an_unrelated_file.txt"
            outside.write_text("someone else's file\n")
            with _linked_worktree(td) as dst:
                target = dst / planted
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(outside, target)      # a HARD link: no link to follow
                assert not target.is_symlink(), planted
                assert os.stat(target).st_nlink == 2, (
                    f"the fixture did not create a second name for the inode "
                    f"at {planted}")
                outside_before = _snapshot(ext)
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src)
                _assert_refused(result, dst, before, f"a hard-linked {planted}")
                assert "hard" in result.stderr.lower(), (
                    f"the refusal must name what it found at {planted}: "
                    + result.stderr)
                assert "symbolic link" not in result.stderr, (
                    f"a hard link was reported as a symbolic one, so this case "
                    f"is passing on the wrong guard: {result.stderr}")
                _assert_nothing_escaped(ext, outside_before, f"a hard-linked {planted}")
                assert outside.read_text() == "someone else's file\n", (
                    f"a hard-linked {planted} let cp rewrite the file outside "
                    f"the worktree that shares its inode")
    return (f"a hard link at any of the {len(planted_at)} output paths is "
            f"refused, and the file outside sharing its inode is untouched")


# --------------------------------------------------------------------------
# issue #184, Codex review: a SPECIAL FILE at an output path defeats BOTH guards
# above at once. It is not a symbolic link, so `[ -L ]` says no; it is not a
# regular file, so `find -type f -links +1` never looks at it. `cp` then opens
# it -- and on a FIFO that open blocks until some process reads, so the run
# hangs with no message, or, if something IS reading, hands this household's
# archive to a process outside the worktree while every containment check still
# passes.
#
# Reproduced against this script before the fix, on a genuine registered
# worktree: a FIFO at private/verify/usage.csv and one at
# private/1-raw-data/gas.csv each hung the run until it was killed, and each had
# ALREADY written household.yaml and most of the raw archive into the
# destination before blocking -- so the refusal was missing and "nothing was
# written" was false too.
#
# Every case here is BOUNDED (_run_script's timeout) and kills the whole process
# group: a fixture that plants a FIFO must fail the suite if the guard is gone,
# never hang it.
# --------------------------------------------------------------------------
@case
def case_refuses_a_fifo_at_an_output_the_copies_would_write_through():
    """The reproduction, on the same four output shapes the symbolic-link and
    hard-link cases use -- the named household.yaml, a named private/verify
    copy, a file inside private/1-raw-data whose name a cp line picks, and one
    nested under a directory `cp -R` descends into -- so the three guards are
    demonstrably checking the same surface rather than three different ones.

    No reader is attached, so this is the HANG shape: unrefused, `cp` blocks on
    the open forever. The bound is what turns that into a failure with
    evidence. Each case also asserts the refusal is the special-file one and not
    the symbolic-link or hard-link guard firing, or it could pass on a
    pre-existing check and prove nothing."""
    planted_at = ("private/household.yaml",
                  "private/verify/usage.csv",
                  "private/1-raw-data/gas.csv",
                  "private/1-raw-data/electric-bills/statement.pdf")
    for planted in planted_at:
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                 has_gas_bills_dir=False)
            (src / "private" / "1-raw-data" / "electric-bills" / "statement.pdf") \
                .write_text("the source statement\n")
            with _linked_worktree(td) as dst:
                target = dst / planted
                target.parent.mkdir(parents=True, exist_ok=True)
                os.mkfifo(target)
                # The premise: this really is the shape both existing guards
                # miss. Not a link for `[ -L ]` to see, and not a regular file
                # for `find -type f -links +1` to count.
                assert stat.S_ISFIFO(os.stat(target).st_mode), planted
                assert not target.is_symlink(), planted
                assert not target.is_file(), planted
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src, timeout=60)
                _assert_refused(result, dst, before, f"a FIFO at {planted}")
                assert "FIFO" in result.stderr, (
                    f"the refusal must name what it found at {planted}: "
                    + result.stderr)
                assert "symbolic link" not in result.stderr, (
                    f"a FIFO was reported as a symbolic link, so this case is "
                    f"passing on the wrong guard: {result.stderr}")
                assert "hard link" not in result.stderr.lower(), (
                    f"a FIFO was reported as a hard link, so this case is "
                    f"passing on the wrong guard: {result.stderr}")
                assert stat.S_ISFIFO(os.stat(target).st_mode), (
                    f"the refused run replaced the FIFO at {planted}")
    return (f"a FIFO at any of the {len(planted_at)} output paths is refused "
            f"before the copies, with nothing written and no hang")


@case
def case_a_fifo_with_a_reader_attached_never_receives_the_archive():
    """The other half of the finding, and the half an exit status cannot show.

    A FIFO only hangs `cp` while nothing is reading it. Hold the read end open
    and the open succeeds instead: the copy completes, the destination tree
    looks untouched, and this household's intake file has gone to a process
    somewhere else entirely. So the pipe itself is read back, and the assertion
    is that NO BYTES arrived.

    O_NONBLOCK on the read end is what makes that safe to do here -- opening a
    FIFO for reading otherwise blocks until a writer arrives, which is the very
    hang this suite must not have."""
    marker = "# a distinctive marker only this household's file carries\n"
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, marker + "household:\n  has_gas: false\n",
                             has_gas_bills_dir=False)
        with _linked_worktree(td) as dst:
            target = dst / "private" / "household.yaml"
            os.mkfifo(target)
            fd = os.open(str(target), os.O_RDONLY | os.O_NONBLOCK)
            try:
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src, timeout=60)
                try:
                    leaked = os.read(fd, 1 << 20)
                except BlockingIOError:
                    leaked = b""
                assert leaked == b"", (
                    f"the private archive was written INTO the pipe -- a "
                    f"process outside the worktree received "
                    f"{len(leaked)} bytes: {leaked[:120]!r}")
                _assert_refused(result, dst, before, "a FIFO with a reader attached")
                assert "FIFO" in result.stderr, result.stderr
            finally:
                os.close(fd)
    return ("a FIFO whose read end is held open receives nothing: the run is "
            "refused before cp can hand the archive to the process reading it")


# --------------------------------------------------------------------------
# issue #184, /review: three of the four cases below are the same shape, and
# it is the shape that gets guards DISABLED rather than fixed -- a guard that
# refuses a correct caller, or a message that claims something untrue. The
# fourth (CDPATH) is a guard that validates one directory and writes into
# another, which is the original incident wearing a different hat.
# --------------------------------------------------------------------------
def _repo_with_two_worktrees(td, wt_basename="mystery", name="cdpath-repo"):
    """A repository of its own, the script copy in its MAIN checkout, and TWO
    registered linked worktrees that share a BASENAME under different parents.

    Both are legitimate destinations as far as every guard is concerned, so
    the only thing that decides which one a bare relative `mystery` names is
    how the script resolves that path -- which is exactly what CDPATH moves.
    Returns (the_script_copy_to_run, the_one_the_operator_names,
    the_one_reachable_only_through_CDPATH)."""
    main = pathlib.Path(td) / name
    made = subprocess.run(["git", "init", "-q", str(main)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        raise SkipCase(f"could not build a repository fixture: {made.stderr}")
    (main / "README.md").write_text("a checkout of its own\n")
    (main / ".gitignore").write_text("private/\n")
    for cmd in (["add", "-A"],
                ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "commit", "-qm", "initial"]):
        r = subprocess.run(["git", "-C", str(main), *cmd],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"could not commit in the fixture: {r.stderr}")
    its_own_copy = main / SCRIPT.name
    its_own_copy.write_text(SCRIPT.read_text())
    made_worktrees = []
    for parent in ("named-by-the-operator", "reachable-only-through-cdpath"):
        p = pathlib.Path(td) / parent
        p.mkdir()
        wt = p / wt_basename
        r = subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach",
                            str(wt), "HEAD"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"could not add a worktree to the fixture: {r.stderr}")
        made_worktrees.append(wt)
    return its_own_copy, made_worktrees[0], made_worktrees[1]


@case
def case_cdpath_cannot_redirect_a_bare_relative_destination():
    """issue #184, /review finding 1. Every guard in this script decides its
    verdict about a directory `cd` picked, and `cd` honours CDPATH: POSIX
    exempts only an operand starting with `/`, `./` or `..`, so `.` is safe
    and a BARE RELATIVE name like `mystery` is not. With CDPATH set, the run
    validated and staged into a DIFFERENT registered worktree while the
    `./mystery` the operator named stayed empty -- the archive somewhere other
    than the path on the command line, silently, and with every containment
    check passing because they all ran against the redirected directory.

    Both fixtures are registered worktrees of the same throwaway repository, so
    neither is refusable on any other ground; the only difference between a
    correct run and the defect is WHICH of the two ends up holding the archive.
    The CDPATH redirection is asserted as a live premise first, so this case
    announces itself stale rather than passing quietly if some future bash
    stops searching CDPATH here."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        script, named, elsewhere = _repo_with_two_worktrees(td)
        assert named.name == elsewhere.name and named.parent != elsewhere.parent
        env = dict(os.environ, CDPATH=str(elsewhere.parent))
        # The premise: an unfixed `cd -- mystery` really does land elsewhere.
        probe = subprocess.run(
            ["bash", "-c", 'cd -- "$1" >/dev/null 2>&1 && pwd -P', "_", named.name],
            capture_output=True, text=True, cwd=str(named.parent), env=env)
        if probe.stdout.strip() != str(elsewhere.resolve()):
            raise SkipCase("this bash no longer resolves a bare relative "
                           "directory through CDPATH, so there is no "
                           "redirection left for this case to close")
        elsewhere_before = _snapshot(elsewhere)
        result = _run_script(src, named.name, cwd=named.parent, script=script,
                             env=env, timeout=120)
        assert result.returncode == 0, (
            f"the worktree the operator actually named must still be accepted: "
            f"{result.stderr}")
        for required in ("private/household.yaml",
                         "private/1-raw-data/gas.csv",
                         "private/verify/usage.csv"):
            assert (named / required).is_file(), (
                f"{required} was not staged into the destination named on the "
                f"command line ({named})")
        after = _snapshot(elsewhere)
        assert after == elsewhere_before, (
            f"the archive was staged into a worktree the operator never named, "
            f"reached only through CDPATH: {sorted(after ^ elsewhere_before)}")
        assert "CDPATH" in result.stderr, (
            "an inherited CDPATH was cleared silently -- the sanitizing block "
            "announces everything it ignores: " + result.stderr)
    return ("CDPATH cannot redirect a bare relative destination: the archive "
            "lands in the worktree named on the command line and nowhere else")


@case
def case_a_failing_source_check_leaves_the_destination_untouched():
    """issue #184, /review finding 2. The venv check and the two has_gas
    checks read only $SRC, and they used to run AFTER six cp invocations --
    so a source whose venv lives elsewhere, or a has_gas:true household whose
    gas-bills/ had not been pulled, exited 1 with a message about the SOURCE
    having already written household.yaml and the whole bill directory into
    the destination, with nothing saying the destination was now half-staged.

    The invariant is "either the script writes nothing, or it completes", not
    "the guard block writes nothing", so the assertion here is the
    destination's WHOLE CONTENTS before and after -- an exit code cannot tell
    a refusal from a half-finished stage."""
    checked = []
    for label, yaml_text, gas_dir, venv, needle in (
            ("a source whose venv lives somewhere else",
             "household:\n  has_gas: false\n", False, False, ".venv/bin/python"),
            ("a has_gas:true source whose gas-bills/ has not been pulled",
             "household:\n  has_gas: true\n", False, True, "gas-bills"),
            ("a has_gas:false source carrying a stale gas-bills/",
             "household:\n  has_gas: false\n", True, True, "gas-bills")):
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td, yaml_text, has_gas_bills_dir=gas_dir,
                                 with_venv=venv)
            with _linked_worktree(td) as dst:
                before = _snapshot(dst)
                result = _run_script(src, dst, cwd=src, timeout=120)
                assert result.returncode != 0, (
                    f"{label}: expected a refusal, got exit 0\n{result.stdout}")
                after = _snapshot(dst)
                assert after == before, (
                    f"{label}: the run failed on a SOURCE check but had already "
                    f"written into the destination: {sorted(after ^ before)}")
                assert needle in result.stderr, (
                    f"{label}: the message does not say what was wrong with the "
                    f"source: {result.stderr}")
                assert "nothing was written" in result.stderr, (
                    f"{label}: the refusal does not make the claim it is now "
                    f"entitled to make: {result.stderr}")
            checked.append(label)
    return (f"a source-side refusal ({len(checked)} shapes) leaves the "
            f"destination byte-for-byte as it found it")


@case
def case_invoked_through_a_symlink_from_inside_another_repository():
    """issue #184, /review finding 3. SELF_DIR came from ${BASH_SOURCE[0]}
    unresolved, so a `~/bin/stage-private-data.sh -> <checkout>/...` symlink
    made SELF_DIR `~/bin`. If that directory sits inside a versioned dotfiles
    repository -- the common case -- SELF_GIT became the DOTFILES repo, and
    EVERY legitimate destination was refused as "belongs to a DIFFERENT
    repository", naming the destination when the invocation was at fault.

    The fixture is that exact shape: a repository of its own whose main
    checkout is the destination, its script copy in a linked worktree, and the
    invocation through a symlink inside a second, unrelated repository. The
    two repositories are asserted to be genuinely different first, or an
    acceptance here would prove nothing."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        dst, its_own_copy = _throwaway_checkout(td, "private/\n")
        dotfiles = pathlib.Path(td) / "dotfiles"
        (dotfiles / "bin").mkdir(parents=True)
        made = subprocess.run(["git", "init", "-q", str(dotfiles)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            raise SkipCase(f"could not build a dotfiles fixture: {made.stderr}")
        (dotfiles / "README.md").write_text("someone's versioned dotfiles\n")
        for cmd in (["add", "-A"],
                    ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
                     "commit", "-qm", "initial"]):
            r = subprocess.run(["git", "-C", str(dotfiles), *cmd],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise SkipCase(f"could not commit in the dotfiles fixture: {r.stderr}")
        entry = dotfiles / "bin" / SCRIPT.name
        entry.symlink_to(its_own_copy)
        # The premises: the invoking directory really is inside a DIFFERENT
        # repository from the one the real script lives in, and the
        # destination really belongs to the latter.
        assert _common_dir_of(entry.parent) != _common_dir_of(its_own_copy.parent), (
            "the fixture's two repositories are the same one, so this case "
            "would pass whether or not the symlink is resolved")
        assert _common_dir_of(dst) == _common_dir_of(its_own_copy.parent)
        result = _run_script(src, dst, cwd=src, script=entry, timeout=120)
        assert result.returncode == 0, (
            f"a legitimate destination was refused because the script was "
            f"invoked through a symlink from another repository: {result.stderr}")
        for required in ("private/household.yaml",
                         "private/1-raw-data/gas.csv",
                         "private/verify/usage.csv"):
            assert (dst / required).is_file(), f"{required} was not staged"
    return ("a script reached through a symlink inside an unrelated repository "
            "still accepts its own checkout's worktree")


@case
def case_the_recursive_scans_cover_only_what_the_copies_write():
    """issue #184, /review finding 4. The three recursive scans walked ALL of
    private/1-raw-data, while `cp -R` writes only electric-bills/, gas-bills/
    and caiso_raw/ plus the named and glob-named files. dsgs_events/ and
    sdge_nbt_export_rates/ are documented as never staged -- so a destination
    whose dsgs_events/ came back from a `cp -al` or `rsync --link-dest` backup
    was refused for files `cp` would never open, and the refusal's remedy
    ("delete them and re-run; the copies below stage these files for you") was
    FALSE: this script does not stage them back.

    The case has to DISCRIMINATE, not merely pass: the same planted shape --
    a hard link and a FIFO -- goes under a not-staged subtree and under a
    staged one, and the run must be accepted in the first and refused in the
    second. A guard scoped to nothing at all would fail the second half."""
    checked = []
    for sub, is_written in (("dsgs_events", False),
                            ("sdge_nbt_export_rates", False),
                            ("electric-bills", True)):
        for kind in ("a hard link", "a FIFO"):
            with tempfile.TemporaryDirectory() as td:
                src = _synthetic_src(td, "household:\n  has_gas: false\n",
                                     has_gas_bills_dir=False)
                ext = _external_target(td)
                outside = ext / "an_unrelated_file.txt"
                outside.write_text("someone else's file\n")
                with _linked_worktree(td) as dst:
                    planted = dst / "private" / "1-raw-data" / sub / "restored.csv"
                    planted.parent.mkdir(parents=True)
                    if kind == "a hard link":
                        os.link(outside, planted)
                        assert os.stat(planted).st_nlink == 2
                    else:
                        os.mkfifo(planted)
                        assert stat.S_ISFIFO(os.stat(planted).st_mode)
                    before = _snapshot(dst)
                    result = _run_script(src, dst, cwd=src, timeout=60)
                    if is_written:
                        _assert_refused(result, dst, before,
                                        f"{kind} under {sub}/")
                    else:
                        assert result.returncode == 0, (
                            f"{kind} under {sub}/ -- a subtree this script "
                            f"never writes -- was refused, and the refusal's "
                            f"remedy is wrong because the script does not "
                            f"stage it back: {result.stderr}")
                        for required in ("private/household.yaml",
                                         "private/1-raw-data/gas.csv",
                                         "private/verify/usage.csv"):
                            assert (dst / required).is_file(), required
                        if kind == "a hard link":
                            assert outside.read_text() == "someone else's file\n", (
                                "the accepted run rewrote the file outside the "
                                "worktree that shares the planted inode")
                        else:
                            assert stat.S_ISFIFO(os.stat(planted).st_mode), (
                                "the accepted run wrote to the FIFO it was "
                                "supposed to be ignoring")
                checked.append(f"{kind} under {sub}/")
    return (f"the scans discriminate: {len(checked)} planted shapes, refused "
            f"under the subtrees the copies write and ignored under the two "
            f"documented never-staged ones")


@case
def case_has_gas_is_read_with_real_yaml_semantics_not_text_scanning():
    """Codex review, issue #33, pass 3: an earlier version grepped the
    household.yaml text for the first line containing "has_gas:" and
    string-compared it to the literal "true" -- which an unrelated EARLIER
    comment mentioning "has_gas:" would have matched instead of the real
    key, and which a valid PyYAML boolean spelling household.py itself
    accepts (True, not just lowercase true) would have rejected as
    unreadable. Both scenarios are real household.yaml configurations the
    actual pipeline runs on without complaint."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(
            td,
            "# note: has_gas: false is the default before intake (comment only)\n"
            "household:\n"
            "  has_gas: True\n",
            has_gas_bills_dir=True)
        # A real linked worktree, not a bare temp directory: since issue #184
        # the script refuses any destination that is not a working tree of
        # this checkout, and it refuses it before the first write.
        # cwd=src: household.py's _repo_root() checks Path.cwd() BEFORE
        # __file__, so without this the script's `import household` call
        # would silently resolve to whatever real repo this test process
        # happens to run from (finding a real, unrelated household.yaml
        # there) instead of the synthetic one built above -- masking this
        # case on any machine that already has private/household.yaml
        # staged at the real repo root (issue #102 review).
        with _linked_worktree(td) as dst:
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, (
                f"a real, pipeline-accepted household.yaml (capitalized True, "
                f"preceded by an unrelated comment mentioning has_gas:) must not "
                f"fail staging: {result.stderr}")
            assert (dst / "private" / "1-raw-data" / "gas-bills").is_dir()
    return "has_gas is read via real YAML semantics, not a text scan"


@case
def case_real_archive_stage_script_produces_every_required_path():
    """End-to-end proof of AC-1: run the actual script against this
    machine's real private archive into a scratch directory, and check every
    non-optional referenced input, plus the private/verify sandbox copies,
    actually exist afterward -- not just that the script's own source text
    mentions them."""
    src = ROOT
    if not (src / "private" / "household.yaml").is_file():
        raise SkipCase("needs this machine's real private/ archive, which "
                       "this checkout does not have")
    referenced = _referenced_1raw_data_paths()
    with tempfile.TemporaryDirectory() as td:
        # A real linked worktree, not a bare temp directory: since issue #184
        # the script stages only into a working tree of this checkout.
        # cwd=src: same reason as the synthetic-fixture case above -- without
        # it, household.py's _repo_root() (Path.cwd() checked before
        # __file__) resolves against whatever repo this test process happens
        # to be invoked from rather than `src`. Harmless when invoked from
        # this same checkout's root, but this repo routinely runs from
        # sibling git worktrees (limits-wt/sdge-issue-*) with their own
        # unrelated household.yaml, where the mismatch produces a confusing,
        # unrelated-looking failure instead of exercising this checkout's
        # own archive (issue #102 review, round 2).
        with _linked_worktree(td) as dst:
            result = _run_script(src, dst, cwd=src)
            assert result.returncode == 0, (
                f"stage-private-data.sh exited {result.returncode}: {result.stderr}")
            # gas-bills only exists in the source archive for a has_gas:true
            # household (parse_bills.py's own invariant); the script's own
            # conditional mirrors that, so this check must too, or it would
            # falsely fail on a genuinely supported has_gas:false checkout
            # (Codex review, issue #33, pass 1).
            source_has_gas_bills = (src / "private" / "1-raw-data" / "gas-bills").is_dir()
            missing = []
            for name in referenced:
                if name in OPTIONAL_NOT_STAGED:
                    continue
                if name == "gas-bills" and not source_has_gas_bills:
                    continue
                if not list((dst / "private" / "1-raw-data").glob(name)) \
                        and not (dst / "private" / "1-raw-data" / name).exists():
                    missing.append(name)
            assert not missing, f"staged directory is missing: {missing}"
            for verify_file in ("usage.csv", "samA.csv", "samB.csv"):
                assert (dst / "private" / "verify" / verify_file).is_file(), verify_file
    return (f"a real run of stage-private-data.sh produced all "
           f"{len(referenced) - len(OPTIONAL_NOT_STAGED)} required private "
           f"inputs plus the three private/verify sandbox copies")


def run():
    passed = failed = skipped = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS  {fn.__name__}: {msg}")
            passed += 1
        except SkipCase as e:
            print(f"SKIP  {fn.__name__}: {e}")
            skipped += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(CASES)} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
