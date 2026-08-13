#!/usr/bin/env python3
"""stage-private-data.sh must copy every raw private input a committed
generator actually reads, so a fresh worktree staged only by the script can
run the full pipeline (issue #33) -- and it must REFUSE to write that archive
anywhere that is not a working tree of this checkout, before the first byte
lands (issue #184) -- including when the caller's environment tells git to
report some other repository for that destination, which the cases at the
bottom of this file forge deliberately.

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


def _synthetic_src(td, household_yaml_text, has_gas_bills_dir):
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
    on a runner and a symlink to it would be dangling (issue #102 review)."""
    src = pathlib.Path(td) / "src"
    (src / "analysis").mkdir(parents=True)
    (src / "data").mkdir(parents=True)
    (src / "private" / "1-raw-data").mkdir(parents=True)
    shutil.copy2(ANALYSIS / "household.py", src / "analysis" / "household.py")
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
def _run_script(src, dst, cwd, script=SCRIPT, env=None):
    return subprocess.run(["bash", str(script), str(src), str(dst)],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


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
    return "the destination guard and all of its exits precede the first write"


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
_FORGEABLE = ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE",
              "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
              "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_CONFIG_GLOBAL",
              "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")


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
        for name in _FORGEABLE:
            assert name in result.stderr, (
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
    first_git = min(n for n, line in code if re.search(r'\bgit\s+-C\b', line))
    first_unset = min(n for n, line in code if line.strip().startswith("unset "))
    assert first_unset < first_git, (
        "the environment must be cleared before the first git probe, not after")
    return (f"all {len(_FORGEABLE)} forgeable variables are refused together, "
            f"announced, and cleared before the first git probe")


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
    comes from a genuinely accepted run rather than a synthetic probe."""
    with tempfile.TemporaryDirectory() as td:
        src = _synthetic_src(td, "household:\n  has_gas: false\n", has_gas_bills_dir=False)
        record = pathlib.Path(td) / "inherited-by-the-child.txt"
        wrapper = pathlib.Path(td) / "python-wrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"REC={shlex.quote(str(record))}\n"
            ': > "$REC"\n'
            + "".join('printf "%s\\n" "{v}=${{{v}-<unset>}}" >> "$REC"\n'.format(v=v)
                      for v in _FORGEABLE)
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
            still_set = {k: v for k, v in seen.items() if v != "<unset>"}
            assert not still_set, (
                f"the guard cleared these for its own probes but handed them to "
                f"the work it authorized: {still_set}")
            assert set(seen) == set(_FORGEABLE), seen
    return (f"all {len(_FORGEABLE)} variables are gone from the environment the "
            f"post-guard work inherits, not just from the probes")


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
