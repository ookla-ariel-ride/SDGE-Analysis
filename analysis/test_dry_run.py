#!/usr/bin/env python3
"""Guard suite for dry_run.py -- the sandbox that lets a generator be asked
"what would you change?" without letting it change anything.

The whole value of dry_run.py is a negative claim ("the repo's data/ cannot be
modified"), and a negative claim is exactly the kind that passes for the wrong
reason. So the cases below are built in matched pairs:

  * the real repo's data/ is hashed before and after a real generator runs
    through the tool, and must be byte-identical -- AND
  * a generator that genuinely would change an artifact must still be REPORTED
    as changing it, on a synthetic repo (always) and on a real generator via the
    baseline hook (wherever that generator runs). Without the second half, a
    tool that silently did nothing would pass the first half perfectly.

  * both repo-root idioms in this codebase (`__file__.parent.parent` and the
    CWD-first `_repo_root()` walk-up) are exercised by generators written for
    this suite, and both must land their writes in the sandbox -- AND
  * the committed `_repo_root()` is executed from a rootless directory to
    confirm the SystemExit fail-closed behaviour dry_run.py's docstring leans
    on, rather than taking the docstring's word for it.

  * a generator that exits 0 having neither written nor deleted anything, and one
    that crashes, must both be reported as FAILURES -- never as "nothing would
    change" -- AND
  * a generator that exits 0 having only DELETED an artifact must be reported as
    a REMOVAL, since a deletion is a real change that leaves the same empty write
    set as a run that never happened.

  * a tracked symlink is seeded by copying its target's content, so no path
    inside the sandbox leads out of it (the checkout has none today, so the
    case builds one) -- AND
  * tearing the sandbox down is part of the result: a sandbox that cannot be
    removed is a FAILURE naming the path, because it holds the copied private/
    archive, while a leftover -baseline copy of committed data/ is only a
    warning and must not stop the sandbox from being disposed of.

Run from the repo root:  ./.venv/bin/python analysis/test_dry_run.py
"""
import ast
import contextlib
import errno
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import time
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
ROOT = ANALYSIS.parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402

import dry_run as DR  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _hash_dir(path):
    out = {}
    for f in sorted(pathlib.Path(path).rglob("*")):
        if f.is_file() and not f.is_symlink():
            out[str(f.relative_to(path))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


# The two root idioms, verbatim in shape from the real generators. GEN_PARENT is
# generate_report.py's/privacy_tiers.py's; GEN_WALKUP is the CWD-first walk-up
# carbon_fullyear.py and ~33 siblings share.
GEN_PARENT = """\
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
(ROOT / "data" / "%(out)s").write_text(json.dumps(%(payload)s, indent=1) + "\\n")
"""

GEN_WALKUP = """\
import json, pathlib, sys
def _repo_root():
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found")
ROOT = _repo_root()
(ROOT / "data" / "%(out)s").write_text(json.dumps(%(payload)s, indent=1) + "\\n")
"""


def _synth_repo(tmp, generators, data_files):
    """A minimal repo-shaped, git-tracked checkout: analysis/ + data/ + a commit.

    Real enough for dry_run.py (it seeds from `git ls-files`), small enough that
    a case can state exactly what the generator will and will not write.
    """
    tmp = pathlib.Path(tmp)
    (tmp / "analysis").mkdir(parents=True, exist_ok=True)
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    for name, body in generators.items():
        (tmp / "analysis" / name).write_text(body)
    for name, body in data_files.items():
        (tmp / "data" / name).write_text(body)
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    for cmd in (["init", "-q"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"]):
        r = subprocess.run(["git", "-C", str(tmp), *cmd], capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0, f"git {cmd}: {r.stderr}"
    return tmp


def _cli(*args):
    r = subprocess.run([sys.executable, str(ANALYSIS / "dry_run.py"), *map(str, args)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# AC: a dry run cannot modify the repo's own data/.
# ---------------------------------------------------------------------------
@case
def case_the_real_repo_data_is_byte_identical_after_a_dry_run():
    gen = ROOT / "analysis" / "tou_spread.py"   # committed inputs only, ~0.3s
    if not gen.is_file():
        raise SkipCase("analysis/tou_spread.py is missing from this checkout")
    before = _hash_dir(ROOT / "data")
    assert before, "the real data/ is empty; this case would prove nothing"
    rep = DR.dry_run(gen)
    after = _hash_dir(ROOT / "data")
    assert after == before, (
        "the real data/ changed during a dry run: "
        + str(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))))
    assert rep.failure is None, rep.failure
    assert rep.result.wrote, "the generator wrote nothing -- this case proved nothing"
    assert not str(rep.sandbox_path).startswith(str(ROOT)), rep.sandbox_path
    return ("a real generator run through dry_run.py leaves every one of the "
            f"{len(before)} files under the repo's data/ byte-identical, and its "
            "sandbox sits outside the checkout")


@case
def case_both_repo_root_idioms_land_their_writes_in_the_sandbox():
    payload = '{"marker": 1}'
    gens = {"g_parent.py": GEN_PARENT % {"out": "from_parent.json", "payload": payload},
            "g_walkup.py": GEN_WALKUP % {"out": "from_walkup.json", "payload": payload}}
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, gens, {"seed.json": "{}\n"})
        for name, artifact in (("g_parent.py", "from_parent.json"),
                               ("g_walkup.py", "from_walkup.json")):
            rep = DR.dry_run(repo / "analysis" / name)
            assert rep.failure is None, (name, rep.failure)
            added = [c.path for c in rep.changes if c.kind == "added"]
            assert added == [f"data/{artifact}"], (name, added)
            assert not (repo / "data" / artifact).exists(), (
                f"{name} wrote into the real repo's data/, not the sandbox")
        assert set(_hash_dir(repo / "data")) == {"seed.json"}, "the synthetic data/ grew"
    return ("both root idioms in this codebase -- __file__.parent.parent and the "
            "CWD-first _repo_root() walk-up -- resolve to the sandbox, so neither "
            "generator's write reaches the checkout it was copied from")


@case
def case_the_committed_repo_root_fails_closed_with_no_root_above_it():
    """dry_run.py's safety argument rests on _repo_root() raising SystemExit
    rather than searching wider. Execute the committed function, don't trust it."""
    src = (ROOT / "analysis" / "carbon_fullyear.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_repo_root"), None)
    assert fn is not None, "carbon_fullyear.py no longer defines _repo_root()"
    body = ast.get_source_segment(src, fn)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        probe = td / "probe.py"
        probe.write_text("import pathlib\n" + body + "\nprint(_repo_root())\n")
        r = subprocess.run([sys.executable, str(probe)], cwd=str(td),
                           capture_output=True, text=True)
    assert r.returncode != 0, f"_repo_root() succeeded from a rootless dir: {r.stdout}"
    assert "repo root not found" in (r.stderr + r.stdout), (r.stdout, r.stderr)
    try:
        DR.repo_root_for(pathlib.Path(tempfile.gettempdir()) / "nowhere" / "x.py")
        raise AssertionError("repo_root_for() found a root where there is none")
    except DR.DryRunError as e:
        assert "no repo root" in str(e), e
    return ("the committed _repo_root() raises SystemExit ('repo root not found') "
            "when no ancestor holds both analysis/ and data/, and dry_run.py's own "
            "repo_root_for() refuses the same case -- a malformed sandbox fails "
            "closed instead of finding the real data/")


# ---------------------------------------------------------------------------
# AC: a change that WOULD happen is reported. (The case that matters: a tool
# that reports "no changes" because it never ran is worse than no tool.)
# ---------------------------------------------------------------------------
@case
def case_a_generator_that_would_change_an_artifact_is_reported_as_changing_it():
    gen = GEN_WALKUP % {"out": "out.json",
                        "payload": '{"kept": 1, "moved": 99, "fresh": 3}'}
    committed = json.dumps({"kept": 1, "moved": 2, "stale": 7}, indent=1) + "\n"
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": gen}, {"out.json": committed})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        mods = [c for c in rep.changes if c.kind == "modified"]
        assert [c.path for c in mods] == ["data/out.json"], [c.path for c in rep.changes]
        detail = " ".join(mods[0].detail)
        assert "keys added (1): fresh" in detail, detail
        assert "keys removed (1): stale" in detail, detail
        assert "keys changed (1): moved" in detail, detail
        assert rep.would_change is True
        assert (repo / "data" / "out.json").read_text() == committed, \
            "the dry run rewrote the artifact it was only supposed to describe"
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 1, (code, out)
        assert "WOULD CHANGE" in out and "moved" in out, out
    return ("a generator whose output genuinely differs is reported as modifying "
            "data/out.json with the exact top-level keys added/removed/changed, "
            "--check exits 1, and the committed artifact is left untouched")


@case
def case_a_real_generator_reports_a_real_diff_when_the_baseline_differs():
    """The synthetic case above proves the diff engine fires; this proves it
    fires for a REAL generator's real output, so the tool cannot pass by never
    running the thing it was pointed at."""
    gen = ROOT / "analysis" / "tou_spread.py"
    if not gen.is_file():
        raise SkipCase("analysis/tou_spread.py is missing from this checkout")
    baseline = DR.dry_run(gen)
    if baseline.failure is not None:
        raise SkipCase(f"tou_spread.py does not run in this checkout: {baseline.failure}")
    assert not baseline.changes, (
        "tou_spread.py is not reproducible here, so this case cannot isolate the "
        f"perturbation: {[c.path for c in baseline.changes]}")

    def perturb(sb):
        f = sb.baseline_dir / "tou_spread.json"
        doc = json.loads(f.read_text())
        assert isinstance(doc, dict) and doc, "tou_spread.json is not a JSON object"
        doc["__injected_key__"] = "this key is not in the generator's output"
        f.write_text(json.dumps(doc, indent=1))

    rep = DR.dry_run(gen, on_built=perturb)
    assert rep.failure is None, rep.failure
    mods = [c for c in rep.changes if c.kind == "modified"]
    assert [c.path for c in mods] == ["data/tou_spread.json"], [c.path for c in rep.changes]
    assert "__injected_key__" in " ".join(mods[0].detail), mods[0].detail
    assert rep.would_change is True
    return ("a real generator's real output is diffed, not assumed: perturbing one "
            "key of the baseline turns an otherwise-clean tou_spread.py dry run "
            "into a reported modification naming that key")


# ---------------------------------------------------------------------------
# AC: a run that did not really happen is a FAILURE, never "no changes".
# ---------------------------------------------------------------------------
@case
def case_a_generator_that_writes_nothing_is_a_failure_not_no_changes():
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": "print('I did nothing at all')\n"},
                           {"out.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is not None, "a silent no-op was accepted as a clean run"
        assert "wrote nothing" in rep.failure, rep.failure
        text = DR.render(rep, "g.py")
        assert "nothing would change" not in text.lower(), text
        code, out = _cli(repo / "analysis" / "g.py")
        assert code == 2, (code, out)
        assert "FAILED" in out, out
    return ("a generator that exits 0 having written nothing is reported as a "
            "FAILURE (exit 2) and the words 'nothing would change' never appear")


@case
def case_a_generator_that_only_deletes_an_artifact_is_reported_as_a_removal():
    """Deleting an artifact is a real change -- diff_dirs() classifies it as
    Change("removed", ...) -- but _written_since() cannot see it, because a
    deleted file has no mtime left to be newer than the sentinel. A deletion-only
    run must therefore be reported as a REMOVAL, not rejected by the empty-write
    guard as a silent no-op."""
    body = ("import pathlib\n"
            "def _repo_root():\n"
            "    p = pathlib.Path.cwd()\n"
            "    while not ((p / 'analysis').is_dir() and (p / 'data').is_dir()):\n"
            "        p = p.parent\n"
            "    return p\n"
            "(_repo_root() / 'data' / 'retired.json').unlink()\n")
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body},
                           {"retired.json": '{"gas": 1}\n', "kept.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert rep.result.wrote == [], (
            "the generator wrote something, so this case no longer isolates the "
            f"deletion-only path: {rep.result.wrote}")
        assert [(c.kind, c.path) for c in rep.changes] == [("removed", "data/retired.json")], \
            [(c.kind, c.path) for c in rep.changes]
        assert rep.would_change is True
        assert (repo / "data" / "retired.json").is_file(), \
            "the dry run deleted the real artifact it was only supposed to describe"
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 1, (code, out)
        assert "- data/retired.json" in out, out
        assert "nothing would change" not in out.lower(), out
    return ("a generator that exits 0 having only DELETED an artifact -- an empty "
            "write set -- is reported as `removed data/retired.json` with --check "
            "exiting 1, not rejected as a silent no-op")


@case
def case_a_run_with_neither_a_write_nor_a_removal_is_still_a_failure():
    """The acceptance criterion for the deletion fix above: allowing a removal to
    stand in for a write must not let a generator that never really ran through.
    Same shape as the writes-nothing case, asserted after the diff has been
    computed -- the diff is empty, so the guard must still fire."""
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": "print('I touched nothing')\n"},
                           {"a.json": "{}\n", "b.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is not None, "a silent no-op was accepted as a clean run"
        assert "wrote nothing" in rep.failure, rep.failure
        assert rep.changes == [], (
            "a failed run published a diff: " + str([c.path for c in rep.changes]))
        assert rep.cwd_outputs == [], rep.cwd_outputs
        text = DR.render(rep, "g.py")
        assert "nothing would change" not in text.lower(), text
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 2, (code, out)
    return ("a generator that neither writes nor deletes anything is still a "
            "FAILURE (exit 2 under --check) carrying no diff at all -- the "
            "empty-write guard survives the deletion case being allowed through")


@case
def case_a_generator_that_crashes_is_a_failure_with_its_own_error_surfaced():
    body = "import sys\nsys.stderr.write('boom: missing input X\\n')\nraise SystemExit(3)\n"
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body}, {"out.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is not None and "exited 3" in rep.failure, rep.failure
        assert "boom: missing input X" in rep.failure, rep.failure
        assert rep.changes == [], rep.changes
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 2, (code, out)   # a failure is 2 with or without --check
    return ("a generator exiting non-zero is a FAILURE (exit 2 even under --check) "
            "with its own last error lines quoted, never a silent 'no changes'")


@case
def case_an_empty_or_rootless_sandbox_is_refused_before_anything_runs():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        (td / "analysis").mkdir()
        (td / "analysis" / "g.py").write_text("print('hi')\n")
        try:
            DR.dry_run(td / "analysis" / "g.py")
            raise AssertionError("dry_run accepted a checkout with no data/")
        except DR.DryRunError as e:
            assert "no repo root" in str(e), e
        code, out = _cli(td / "analysis" / "g.py")
        assert code == 2 and "FAILED" in out, (code, out)
    return ("a target with no data/ above it is refused with a repo-root error "
            "(exit 2) instead of walking further up and finding some other "
            "checkout's data/")


# ---------------------------------------------------------------------------
# AC: the sandbox itself is well formed and cannot leak.
# ---------------------------------------------------------------------------
@case
def case_the_sandbox_is_outside_the_repo_and_holds_no_path_back_into_private():
    """The sandbox must contain no filesystem route to the authoritative
    archive -- not the private/ directory itself, and not a symlink hidden
    inside the copy of it."""
    before = DR.stat_manifest(ROOT / "private")
    sb = DR.Sandbox(ROOT).build()
    try:
        p = sb.path
        assert p.is_absolute() and ROOT not in p.parents and p != ROOT, p
        assert (p / "analysis" / "rates.py").is_file(), "analysis/ was not seeded"
        assert list((p / "data").glob("*.json")), "data/ was not seeded"
        n_priv = 0
        if (ROOT / "private").is_dir():
            priv = p / "private"
            assert priv.is_dir(), "private/ was not staged into the sandbox"
            assert not priv.is_symlink(), \
                "private/ is a symlink -- a write in the sandbox reaches the real archive"
            real_priv = (ROOT / "private").resolve()
            escapes = []
            for f in p.rglob("*"):
                if not f.is_symlink():
                    continue
                t = f.resolve()
                if t == real_priv or real_priv in t.parents:
                    escapes.append(str(f.relative_to(p)))
            assert not escapes, f"sandbox symlinks resolve into the real private/: {escapes}"
            n_priv = sum(1 for f in priv.rglob("*") if f.is_file())
            assert n_priv == sum(1 for f in (ROOT / "private").rglob("*") if f.is_file()), \
                "the private/ copy does not hold the same number of files as the archive"
            for name in DR.CWD_FIXTURES:
                src = ROOT / "private" / "verify" / name
                if src.is_file():
                    staged = p / name
                    assert staged.is_file() and not staged.is_symlink(), \
                        f"cwd fixture {name} is not a plain copy in the sandbox"
                    assert staged.stat().st_size == src.stat().st_size, name
        n = sb.n_seeded
    finally:
        sb.dispose()
    assert not p.exists(), "the sandbox was not removed"
    assert (ROOT / "private").is_dir(), "the real private/ was removed"
    assert DR.stat_manifest(ROOT / "private") == before, \
        "building and disposing of a sandbox altered the real private/"
    return (f"the sandbox is built outside the checkout from {n} tracked files with "
            f"both analysis/ and data/ populated, private/ is a plain copy ({n_priv} "
            "files) with no symlink in the whole tree resolving back into the real "
            "archive, and disposal removes the sandbox leaving private/ untouched")


@case
def case_disposal_refuses_any_path_that_is_not_its_own_temp_sandbox():
    sb = DR.Sandbox(ROOT).build()
    real = sb.path
    for bogus in (ROOT, ROOT / "data", pathlib.Path(tempfile.gettempdir()) / "not-ours"):
        sb.path = bogus
        try:
            sb.dispose()
            raise AssertionError(f"dispose() accepted {bogus}")
        except DR.DryRunError as e:
            assert "refusing to dispose" in str(e), e
    sb.path = real
    sb.dispose()
    assert (ROOT / "data").is_dir(), "the repo's data/ was removed"
    return ("dispose() refuses any path that is not a prefix-matched sandbox under "
            "the system temp dir -- the repo root and data/ are both rejected")


def _plant_abandoned_sandbox(tag):
    """Plant a stale sandbox in the ONE state the sweep is allowed to act on:
    prefix-named, under the temp dir, carrying a SANDBOX_MARKER whose lock
    nobody holds -- what a run killed AFTER it marked itself leaves behind.

    A MARKERLESS directory is deliberately not this shape. It is
    indistinguishable from a live sibling caught between mkdtemp() and its own
    _lock_marker() call, so the sweep must leave it alone; that is its own pair
    of cases below.

    Forcing a precondition means proving the forcing took, so this asserts both
    halves: the marker exists, and it is genuinely free. Without the second
    assert a case built on this fixture could pass because the sweep refused a
    LIVE-looking directory, which is not the behaviour it claims to test."""
    stale = pathlib.Path(tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + tag)).resolve()
    (stale / "household.yaml").write_text("stranded private data\n")
    (stale / DR.SANDBOX_MARKER).touch()
    assert (stale / DR.SANDBOX_MARKER).is_file(), \
        f"setup failed: the planted stale sandbox carries no marker: {stale}"
    probe = DR.Sandbox._lock_marker(stale, create=False)
    assert probe is not None, (
        f"setup failed: the planted marker at {stale} could not be locked, so "
        "this fixture would look like a LIVE sibling and any case built on it "
        "would pass for the wrong reason")
    probe.close()
    return stale


@case
def case_stale_sandboxes_from_a_prior_run_are_swept_at_build_time():
    """A hard kill between mkdtemp() and dispose() strands a full copy of
    private/ under a name that already carries SANDBOX_PREFIX (issue #187) --
    three such directories, 787 files each, were found stranded on a real
    machine. Plant one in the provably-abandoned shape (marker present, lock
    free), then prove the NEXT sandbox built removes it via Sandbox.build()'s
    own startup sweep, and leaves the new sandbox alone."""
    stale = _plant_abandoned_sandbox("stale-marker-")
    sb = None
    try:
        assert stale.is_dir(), "setup failed: the stale directory was not created"
        sb = DR.Sandbox(ROOT).build()
        assert not stale.exists(), \
            f"a stale sandbox from a prior run was not swept: {stale}"
        assert sb.path.is_dir(), "the new sandbox was not built"
        assert sb.path != stale, \
            "the sweep must not be confused with the sandbox this build() just made"
    finally:
        if sb is not None:
            sb.dispose()
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    return ("Sandbox.build() sweeps stale SANDBOX_PREFIX-named directories left by "
            "a prior run's hard kill before seeding its own sandbox -- the planted "
            "stale directory is gone afterward, and the new sandbox is untouched")


@case
def case_the_sweep_never_touches_a_directory_outside_its_own_prefix():
    """Safety check: a directory that does not carry SANDBOX_PREFIX -- the
    default `tmp*` name every other program's scratch space also uses -- must
    survive the sweep untouched. This proves the sweep reuses dispose()'s own
    prefix-and-tempdir predicate rather than a second, looser check."""
    unrelated = pathlib.Path(tempfile.mkdtemp(prefix="tmp-unrelated-"))
    (unrelated / "marker.txt").write_text("not ours\n")
    sb = None
    try:
        sb = DR.Sandbox(ROOT).build()
        assert unrelated.is_dir(), \
            "the sweep removed a directory that does not carry SANDBOX_PREFIX"
        assert (unrelated / "marker.txt").is_file(), \
            "the sweep touched the contents of an unrelated directory"
    finally:
        if sb is not None:
            sb.dispose()
        shutil.rmtree(unrelated, ignore_errors=True)
    return ("a directory that does not start with SANDBOX_PREFIX survives "
            "Sandbox.build()'s startup sweep untouched, matching dispose()'s own "
            "safety predicate")


@case
def case_the_sweep_never_removes_a_sandbox_a_live_process_still_holds():
    """A prefix-matching directory alone is not proof of abandonment: two
    overlapping dry_run.py invocations both carry SANDBOX_PREFIX while both
    are legitimately alive. A sweep that removed on name alone could delete a
    SIBLING run's sandbox out from under it -- a race the sweep would
    introduce, not fix (issue #187 follow-up). Simulate a still-running
    sibling by holding its marker locked ourselves, exactly as its own
    process would for its whole lifetime, then prove build()'s sweep leaves
    it alone."""
    live = pathlib.Path(tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + "still-running-"))
    (live / "household.yaml").write_text("a live sibling's private data\n")
    held_fd = DR.Sandbox._lock_marker(live)
    assert held_fd is not None, "setup failed: could not lock the simulated sibling's marker"
    sb = None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            sb = DR.Sandbox(ROOT).build()
        assert live.is_dir(), \
            f"the sweep removed a sandbox whose marker was still locked (a live sibling): {live}"
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
        if sb is not None:
            sb.dispose()
        held_fd.close()
        shutil.rmtree(live, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory whose marker is still locked -- a live sibling "
            "run -- survives the startup sweep untouched, and silently: real lock "
            "contention is the one liveness answer the sweep may act on without a word")


@case
def case_a_live_runs_baseline_and_head_copies_survive_the_sweep():
    """dry_run() builds its two comparison copies of data/ as SIBLINGS of the
    sandbox, named from the sandbox's own name -- so they inherit
    SANDBOX_PREFIX, sit in the very temp dir the sweep scans, and can hold no
    marker of their own, because nothing keeps them open. Judged on name alone
    they look exactly like abandoned sandboxes, and an overlapping invocation's
    sweep would rmtree a LIVE run's baseline out from under the diff being
    computed against it. Simulate that live run by holding its sandbox marker
    locked, as its own process would, and prove both copies survive a real
    build()/sweep."""
    live = pathlib.Path(tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + "diffing-")).resolve()
    copies = [live.parent / (live.name + suffix) for suffix in DR.COMPARISON_SUFFIXES]
    for copy in copies:
        copy.mkdir()
        (copy / "package_results.json").write_text('{"baseline": true}\n')
    held_fd = DR.Sandbox._lock_marker(live)
    assert held_fd is not None, \
        "setup failed: could not lock the simulated live run's marker"
    # Positive control on the fixture itself: survival has to come from the
    # sweep's own suffix rule, not from these copies sitting somewhere the
    # sweep's prefix-and-tempdir predicate could never have reached anyway.
    for copy in copies:
        assert DR.Sandbox(ROOT)._safe_to_dispose(copy), (
            f"setup failed: {copy} is outside the sweep's disposal predicate, so "
            "this case would pass no matter what the sweep did")
    sb = None
    try:
        sb = DR.Sandbox(ROOT).build()
        assert live.is_dir(), f"the sweep removed the live run's sandbox itself: {live}"
        for copy in copies:
            assert copy.is_dir(), (
                "the sweep removed a live run's comparison copy of data/ out from "
                f"under its diff: {copy}")
            assert (copy / "package_results.json").is_file(), \
                f"the sweep emptied a live run's comparison copy: {copy}"
    finally:
        if sb is not None:
            sb.dispose()
        held_fd.close()
        for copy in copies:
            shutil.rmtree(copy, ignore_errors=True)
        shutil.rmtree(live, ignore_errors=True)
    return ("the -baseline and -head copies of a run whose sandbox marker is still "
            "locked survive the startup sweep with their contents, even though their "
            "names carry SANDBOX_PREFIX and they hold no marker of their own")


@case
def case_an_abandoned_sandboxs_baseline_and_head_copies_are_swept_with_it():
    """The other half of the pair above. A comparison copy carries no marker,
    so the only liveness signal it can ever have is its OWNING sandbox's -- and
    once the sweep has won that owner's lock, the run that made all three is
    proven dead and the copies are its leftovers too. Plant an abandoned
    sandbox with both copies beside it and prove the next build() takes all
    three, not just the sandbox."""
    stale = _plant_abandoned_sandbox("abandoned-")
    copies = [stale.parent / (stale.name + suffix) for suffix in DR.COMPARISON_SUFFIXES]
    for copy in copies:
        copy.mkdir()
        (copy / "package_results.json").write_text('{"baseline": true}\n')
    sb = None
    try:
        assert all(c.is_dir() for c in copies), \
            "setup failed: the comparison copies were not created"
        sb = DR.Sandbox(ROOT).build()
        assert not stale.exists(), \
            f"the abandoned sandbox itself was not swept: {stale}"
        for copy in copies:
            assert not copy.exists(), (
                "an abandoned run's comparison copy of data/ was left behind by the "
                f"sweep that removed its owning sandbox: {copy}")
    finally:
        if sb is not None:
            sb.dispose()
        for copy in copies:
            shutil.rmtree(copy, ignore_errors=True)
        shutil.rmtree(stale, ignore_errors=True)
    return ("the -baseline and -head copies of an abandoned sandbox are removed "
            "together with it, once the sweep has won the owning sandbox's marker "
            "lock and so proved the run that made all three is dead")


@case
def case_a_stale_sandbox_the_sweep_cannot_remove_is_left_as_found():
    """Inspecting a candidate must change nothing about it. Force the removal
    of a provably-abandoned sandbox to fail and prove the failed attempt leaves
    the directory byte-for-byte as it was found -- its contents intact, and the
    marker it arrived with still the only marker there, since the sweep never
    creates one of its own."""
    stale = _plant_abandoned_sandbox("unremovable-")
    marker_before = (stale / DR.SANDBOX_MARKER).read_bytes()
    real_rmtree = DR.shutil.rmtree
    blocked = []

    def rmtree(path, *a, **kw):
        if pathlib.Path(path) == stale:
            blocked.append(str(path))
            raise OSError("simulated: [Errno 13] Permission denied")
        return real_rmtree(path, *a, **kw)

    sb = None
    try:
        DR.shutil.rmtree = rmtree
        try:
            sb = DR.Sandbox(ROOT).build()
        finally:
            DR.shutil.rmtree = real_rmtree
        assert blocked, \
            "the sweep never tried to remove the planted sandbox, so this case proved nothing"
        assert stale.is_dir(), "the forced failure did not take: the sandbox is gone"
        assert (stale / DR.SANDBOX_MARKER).read_bytes() == marker_before, (
            "the failed sweep altered the candidate's own marker file: "
            f"{stale / DR.SANDBOX_MARKER}")
        assert sorted(p.name for p in stale.iterdir()) == \
            sorted([DR.SANDBOX_MARKER, "household.yaml"]), (
            "the failed sweep added or removed entries in a directory it does not "
            f"own: {sorted(p.name for p in stale.iterdir())}")
        assert (stale / "household.yaml").is_file(), \
            "the failed sweep altered the contents of a directory it does not own"
    finally:
        if sb is not None:
            sb.dispose()
        real_rmtree(stale, ignore_errors=True)
    return ("a stale sandbox the sweep cannot remove is left exactly as it was "
            "found -- same entries, same marker, contents untouched")


@case
def case_a_markerless_candidate_survives_the_sweep_and_is_reported():
    """The liveness test may never manufacture its own evidence. A directory
    carrying SANDBOX_PREFIX but NO marker is exactly what a live sibling looks
    like in the window between its mkdtemp() and its own _lock_marker() call --
    and what a pre-marker dry_run.py looks like for its entire run. A sweep
    that CREATES the missing marker wins a lock against nobody, reads
    "abandoned", and recursively deletes a live run's copy of private/;
    reproduced directly before this guard existed. Plant that exact shape and
    prove three things: it survives, the sweep did not mark it, and it is named
    on stderr rather than silently skipped (issue #187 AC2 accepts a stale
    sandbox being removed OR reported, and reporting is the only honest verdict
    available for a candidate whose state is unknowable)."""
    live = pathlib.Path(
        tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + "LIVE-mid-creation-")).resolve()
    (live / "private").mkdir()
    (live / "private" / "household.yaml").write_text("a live run's private data\n")
    assert not (live / DR.SANDBOX_MARKER).exists(), \
        "setup failed: the planted directory already carries a marker"
    sb = None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            sb = DR.Sandbox(ROOT).build()
        assert live.is_dir(), (
            "the sweep deleted a prefixed directory carrying no marker -- which is "
            f"indistinguishable from a live run mid-creation: {live}")
        assert (live / "private" / "household.yaml").is_file(), (
            "the sweep destroyed the contents of a live run's sandbox, including "
            "its copy of private/")
        assert not (live / DR.SANDBOX_MARKER).exists(), (
            "the sweep created a marker inside a directory it does not own; that "
            "self-made, trivially-won lock is exactly what makes a live sibling "
            "read as abandoned")
        assert str(live) in err.getvalue(), (
            "an unknowable candidate must be REPORTED, not silently skipped -- "
            f"stderr never named it: {err.getvalue()!r}")
    finally:
        if sb is not None:
            sb.dispose()
        shutil.rmtree(live, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory with no marker -- a live run between "
            "mkdtemp() and its own lock -- survives the startup sweep unmarked "
            "and untouched, and is reported to stderr instead of removed")


def _plant_unopenable_marker_sandbox(tag):
    """A stale sandbox whose marker EXISTS but cannot be OPENED (mode 0o000).

    This is the liveness outcome that is neither of the other two: not "we won
    the lock" and not "a live sibling holds it", but "we could not establish
    liveness at all". Before the fix _lock_marker collapsed it into the same
    None a live sibling returns, so the sweep skipped such a candidate in
    SILENCE -- on that run and every future one -- while it held a copy of
    private/. Neither removed nor reported is the one outcome issue #187 AC2
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
    stale = pathlib.Path(tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + tag)).resolve()
    (stale / "private").mkdir()
    (stale / "private" / "household.yaml").write_text("stranded private data\n")
    marker = stale / DR.SANDBOX_MARKER
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


@case
def case_an_unreadable_marker_is_reported_not_silently_skipped():
    """A candidate whose marker cannot be opened has told us NOTHING about
    whether its owner is alive, so treating that failure as "a sibling holds
    it" is a guess dressed as evidence -- and a silent one. Plant a sandbox
    holding a copy of private/ whose marker is mode 0o000, then run a real
    Sandbox.build() and prove its startup sweep names the candidate on stderr
    with the cause, rather than skipping it without a word (issue #187 AC2:
    removed OR reported, never neither).

    It must also SURVIVE: an unreadable marker is not proof of death, so
    deleting it would be the sibling-destroying race the marker exists to
    prevent, with a worse excuse."""
    stale, marker = _plant_unopenable_marker_sandbox("unreadable-marker-")
    sb = None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            sb = DR.Sandbox(ROOT).build()
        assert stale.is_dir(), (
            "the sweep deleted a candidate whose marker it could not even read "
            f"-- an unreadable marker is not proof the owner is dead: {stale}")
        assert (stale / "private" / "household.yaml").is_file(), (
            "the sweep destroyed the contents of a candidate it could not "
            "read, including its copy of private/")
        assert str(stale) in err.getvalue(), (
            "a candidate whose marker cannot be read was skipped in SILENCE, "
            "which is indistinguishable from 'nothing to do', while it holds a "
            f"copy of private/: stderr was {err.getvalue()!r}")
        assert "could not be read" in err.getvalue(), (
            "the report must name the CAUSE as well as the path, or the reader "
            f"cannot tell it from the markerless case: {err.getvalue()!r}")
    finally:
        if sb is not None:
            sb.dispose()
        os.chmod(marker, 0o600)
        shutil.rmtree(stale, ignore_errors=True)
    return ("a SANDBOX_PREFIX directory whose marker cannot be opened is "
            "reported to stderr by name and cause, and left in place -- never "
            "skipped in silence as though a live sibling held it")


@case
def case_lock_marker_separates_contention_from_an_unreadable_marker():
    """The unit-level statement the two sweep cases rest on, so a regression
    lands here first with a message that names the mechanism rather than
    showing up only as a silent sweep.

    Genuine contention -> None (EWOULDBLOCK/EAGAIN, the one healthy answer).
    A marker that cannot be opened -> MarkerUnreadable. Collapsing the second
    into the first is issue #187 AC2's defect, and it is invisible at the sweep
    unless something checks the distinction directly."""
    live = pathlib.Path(
        tempfile.mkdtemp(prefix=DR.SANDBOX_PREFIX + "contention-")).resolve()
    held_fd = DR.Sandbox._lock_marker(live)
    assert held_fd is not None, \
        "setup failed: could not lock the simulated sibling's own marker"
    stale, marker = _plant_unopenable_marker_sandbox("unreadable-unit-")
    try:
        assert DR.Sandbox._lock_marker(live, create=False) is None, (
            "real lock contention must be reported as None -- the sweep reads "
            "that, and only that, as 'a live sibling holds it'")
        try:
            DR.Sandbox._lock_marker(stale, create=False)
        except DR.MarkerUnreadable:
            pass
        else:
            raise AssertionError(
                "an unopenable marker returned a lock verdict instead of "
                "raising MarkerUnreadable -- the sweep will read it as a live "
                "sibling and skip an abandoned copy of private/ in silence")
        assert errno.EWOULDBLOCK in DR._LOCK_CONTENTION_ERRNOS \
            and errno.EAGAIN in DR._LOCK_CONTENTION_ERRNOS, (
            "both contention errnos must be accepted: they are equal on Linux "
            "and macOS but nothing requires that everywhere")
    finally:
        held_fd.close()
        os.chmod(marker, 0o600)
        shutil.rmtree(stale, ignore_errors=True)
        shutil.rmtree(live, ignore_errors=True)
    return ("_lock_marker returns None for real flock contention and raises "
            "MarkerUnreadable for a marker it cannot open -- the distinction "
            "the sweep needs to avoid a silent skip")


@case
def case_a_kept_sandbox_survives_the_next_runs_startup_sweep():
    """--keep-sandbox promises to leave the sandbox on disk (issue #187
    follow-up). Its flock releases the instant THIS process exits, same as
    any other run's, so a kept sandbox left under SANDBOX_PREFIX would look
    exactly like an abandoned one to the very next invocation's startup
    sweep -- breaking the CLI's promise the first time anyone actually
    relies on it. Sandbox.keep() renames it OUT of SANDBOX_PREFIX instead of
    just leaving the lock unheld; prove a second, real build()/sweep cannot
    find or remove it, and that rep.sandbox_path points at the surviving
    (renamed) directory, not the pre-rename one that no longer exists."""
    gen = ROOT / "analysis" / "tou_spread.py"
    if not gen.is_file():
        raise SkipCase("analysis/tou_spread.py is missing from this checkout")
    rep = DR.dry_run(gen, keep_sandbox=True)
    kept = pathlib.Path(rep.sandbox_path)
    sb = None
    try:
        assert kept.is_dir(), f"the kept sandbox does not exist at the reported path: {kept}"
        assert not kept.name.startswith(DR.SANDBOX_PREFIX), (
            f"a kept sandbox still carries SANDBOX_PREFIX and is therefore "
            f"sweepable like an abandoned one: {kept}")
        # A second, real build() -- the exact thing that would run the very
        # next time anyone invokes dry_run.py -- must not touch it.
        sb = DR.Sandbox(ROOT).build()
        assert kept.is_dir(), \
            f"a kept sandbox was removed by the next run's startup sweep: {kept}"
        # The comparison copies must travel WITH it, for two reasons that both
        # bite: a kept tree is only diffable against the baseline it was
        # actually compared to, and a copy left behind under the pre-rename
        # name is litter that nothing can ever collect -- _sweep_stale reaches
        # a comparison copy only through an owning sandbox it has just removed,
        # and that owner no longer exists under that name.
        survivors = [kept.parent / (kept.name + suffix)
                     for suffix in DR.COMPARISON_SUFFIXES]
        assert any(c.is_dir() for c in survivors), (
            "no comparison copy travelled with the kept sandbox: expected one "
            f"of {[c.name for c in survivors]} to exist next to {kept}")
        for suffix in DR.COMPARISON_SUFFIXES:
            stray = kept.parent / (kept.name[len("kept-"):] + suffix)
            assert not stray.exists(), (
                "--keep-sandbox orphaned a comparison copy under the "
                f"pre-rename name: {stray} -- nothing will ever collect it, "
                "since the sweep only reaches one through its owning sandbox")
    finally:
        if sb is not None:
            sb.dispose()
        shutil.rmtree(kept, ignore_errors=True)
        # This case's own litter: the renamed copies deliberately outlive the
        # sweep, so this case collects the ones it created rather than leave a
        # copy of data/ in the temp dir per run. Committed artifacts, no PII.
        for suffix in DR.COMPARISON_SUFFIXES:
            shutil.rmtree(kept.parent / (kept.name + suffix), ignore_errors=True)
    return ("--keep-sandbox renames the sandbox out of SANDBOX_PREFIX, so the very "
            "next invocation's startup sweep cannot find or remove it")


@case
def case_a_generators_inherited_marker_lock_survives_the_parents_own_fd_closing():
    """run_generator() passes the sandbox's marker fd through to its child via
    pass_fds (issue #187 follow-up): a flock is held by the OPEN FILE
    DESCRIPTION, not the process, so a child that inherits this fd keeps the
    lock held even after the PARENT's own fd closes -- exactly what a
    SIGKILL does to a parent while its generator subprocess keeps running.
    Without pass_fds, close_fds=True (Python 3's default) would give the
    child a fresh fd table and the lock would look released the instant the
    parent's own fd closed, orphaned child or not.

    Simulated without an actual kill, since the observable effect (does the
    flock survive) does not depend on WHY the parent's fd closed: launch a
    real generator subprocess with the sandbox's marker inherited via
    pass_fds, close OUR OWN copy of that fd while the child is still
    running, and prove a sibling's lock attempt on the same marker still
    fails -- then prove it succeeds once the child exits, so this is not a
    permanently stuck lock."""
    sb = DR.Sandbox(ROOT).build()
    proc = None
    try:
        started = sb.path / "child_started"
        finish = sb.path / "finish_now"
        script = sb.path / "sleeper.py"
        script.write_text(
            "import pathlib, time\n"
            f"pathlib.Path({str(started)!r}).write_text('1')\n"
            f"finish = pathlib.Path({str(finish)!r})\n"
            "deadline = time.time() + 10\n"
            "while not finish.exists() and time.time() < deadline:\n"
            "    time.sleep(0.02)\n"
        )
        marker_fd = sb._marker_fd
        assert marker_fd is not None, "setup failed: the sandbox has no marker fd"
        proc = subprocess.Popen([sys.executable, str(script)], cwd=str(sb.path),
                                pass_fds=(marker_fd.fileno(),))

        deadline = time.time() + 10
        while not started.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert started.exists(), "the child generator never signalled that it started"

        # Simulate the parent being SIGKILLed: close OUR OWN copy of the
        # marker fd. If pass_fds did its job, the child's inherited copy
        # keeps the underlying flock held regardless.
        marker_fd.close()
        sb._marker_fd = None

        probe_fd = DR.Sandbox._lock_marker(sb.path)
        assert probe_fd is None, (
            "a sibling's lock attempt succeeded while the generator child was "
            "still alive -- the marker fd was not actually inherited")

        finish.write_text("1")
        proc.wait(timeout=10)
        proc = None

        # Now that the child (the last holder) has exited, the lock must be
        # free -- proving this was never a permanently-stuck lock.
        probe_fd2 = DR.Sandbox._lock_marker(sb.path)
        assert probe_fd2 is not None, \
            "the marker stayed locked even after the generator child exited"
        probe_fd2.close()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        sb.dispose()
    return ("a generator subprocess inherits the sandbox's marker fd, so the flock "
            "survives the parent's own fd closing -- e.g. a SIGKILL -- for as long "
            "as the generator keeps running, and releases once it exits")


@case
def case_a_generator_that_writes_under_private_cannot_reach_the_real_archive():
    """The archive is the one input in this repo that cannot be regenerated, so
    the sandbox must not hand a generator a writable path to it. This case runs
    a deliberately destructive generator -- it truncates a file, appends to a
    second, deletes a third and creates a fourth, all under private/ -- against
    a SYNTHETIC archive, and requires the synthetic archive to come out
    byte-for-byte identical. A symlinked private/ fails every one of those
    assertions."""
    body = ("import pathlib\n"
            "def _repo_root():\n"
            "    p = pathlib.Path.cwd()\n"
            "    while not ((p / 'analysis').is_dir() and (p / 'data').is_dir()):\n"
            "        p = p.parent\n"
            "    return p\n"
            "R = _repo_root()\n"
            "(R / 'data' / 'out.json').write_text('{}\\n')\n"
            "(R / 'private' / 'bill.pdf').write_bytes(b'')\n"
            "with open(R / 'private' / '1-raw-data' / 'usage.csv', 'a') as fh:\n"
            "    fh.write('appended garbage\\n')\n"
            "(R / 'private' / 'household.yaml').unlink()\n"
            "(R / 'private' / 'scribble.txt').write_text('I should not be here\\n')\n")
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body}, {"out.json": "{}\n"})
        priv = repo / "private"
        (priv / "1-raw-data").mkdir(parents=True)
        (priv / "bill.pdf").write_bytes(b"%PDF-1.4 pretend statement\n" * 64)
        (priv / "1-raw-data" / "usage.csv").write_text("ts,kwh\n2026-01-01T00:00,0.4\n")
        (priv / "household.yaml").write_text("address: irreplaceable\n")
        before = _hash_dir(priv)
        assert len(before) == 3, before

        rep = DR.dry_run(repo / "analysis" / "g.py")

        after = _hash_dir(priv)
        assert after == before, (
            "the real private/ archive was modified by a dry run: "
            + str(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))))
        assert not (priv / "scribble.txt").exists(), \
            "the generator created a file inside the real private/"
        assert rep.failure is None, rep.failure
        # Contained, and visible: the writes landed on the sandbox's own copy and
        # are reported as writes rather than disappearing behind a symlink.
        wrote = set(rep.result.wrote)
        assert "private/scribble.txt" in wrote and "private/bill.pdf" in wrote, sorted(wrote)
        assert [c.path for c in rep.changes] == [], [c.path for c in rep.changes]
    return ("a generator that truncates, appends to, deletes and creates files "
            "under private/ leaves the archive byte-for-byte identical -- the "
            "writes land on the sandbox's disposable copy and are reported there")


@case
def case_a_generator_that_overwrites_a_cwd_fixture_cannot_reach_the_real_one():
    """usage.csv/samA.csv/samB.csv are staged into the sandbox's CWD from
    private/verify/. usage.csv is the raw Green Button export; a generator that
    opens it for writing must truncate a copy, not the export."""
    body = ("import pathlib\n"
            "open('usage.csv', 'w').write('destroyed\\n')\n"
            "def _repo_root():\n"
            "    p = pathlib.Path.cwd()\n"
            "    while not ((p / 'analysis').is_dir() and (p / 'data').is_dir()):\n"
            "        p = p.parent\n"
            "    return p\n"
            "(_repo_root() / 'data' / 'out.json').write_text('{}\\n')\n")
    original = "ts,kwh\n" + "".join(f"2026-01-01T{h:02d}:00,0.4\n" for h in range(24))
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body}, {"out.json": "{}\n"})
        verify = repo / "private" / "verify"
        verify.mkdir(parents=True)
        (verify / "usage.csv").write_text(original)
        before = _hash_dir(repo / "private")

        rep = DR.dry_run(repo / "analysis" / "g.py")

        assert (verify / "usage.csv").read_text() == original, \
            "the generator truncated the real usage.csv fixture through the sandbox"
        assert _hash_dir(repo / "private") == before, "private/verify/ changed"
        assert rep.failure is None, rep.failure
        assert any("cwd fixtures copied" in n for n in rep.notes), rep.notes
        assert "usage.csv" in rep.result.wrote, sorted(rep.result.wrote)
    return ("a generator that opens usage.csv for writing in its CWD truncates the "
            "sandbox's own copy: the fixture under private/verify/ is unchanged and "
            "the overwrite is reported as a sandbox write")


@case
def case_a_generator_that_escapes_the_sandbox_is_caught_by_the_data_hash_guard():
    """The sandbox stops the two root idioms, but a generator holding an absolute
    path is not bound by either. dry_run.py hashes the checkout's data/ before
    and after every run for exactly that reason; make the guard fire, against a
    SYNTHETIC checkout, rather than assume it would."""
    with tempfile.TemporaryDirectory() as td:
        escape = pathlib.Path(td) / "data" / "escaped.json"
        body = (f"import pathlib\np = pathlib.Path({str(escape)!r})\n"
                "p.write_text('{\"escaped\": true}\\n')\n")
        repo = _synth_repo(td, {"g.py": body}, {"out.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is not None, "an absolute-path write into the checkout went unreported"
        assert "THE REAL data/ CHANGED" in rep.failure, rep.failure
        assert "escaped.json" in rep.failure, rep.failure
        assert rep.changes == [], "an escaping run must not also publish a diff"
        code, out = _cli(repo / "analysis" / "g.py")
        assert code == 2 and "FAILED" in out, (code, out)
    return ("a generator that reaches its checkout's data/ through an absolute "
            "path -- past both root idioms -- is caught by the before/after hash "
            "of the real data/ and reported as a FAILURE naming the file")


@case
def case_a_tracked_symlink_pointing_outside_the_repo_is_dereferenced():
    """A tracked symlink recreated verbatim inside the sandbox is a writable path
    to whatever it names -- and if that target is absolute, it names a file
    OUTSIDE the sandbox. Neither guard would see the write: hash_tree() skips
    symlinks and stat_manifest() records the link rather than following it. There
    are no tracked symlinks in this checkout today, so build one synthetically
    and aim a write-happy generator at it."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        outside = td / "outside"
        outside.mkdir()
        secret = outside / "irreplaceable.txt"
        original = "the real file, outside the sandbox\n"
        secret.write_text(original)

        repo = td / "repo"
        (repo / "analysis").mkdir(parents=True)
        os.symlink(str(secret), repo / "analysis" / "escape.txt")   # ABSOLUTE target
        body = ("import pathlib\n"
                "def _repo_root():\n"
                "    p = pathlib.Path.cwd()\n"
                "    while not ((p / 'analysis').is_dir() and (p / 'data').is_dir()):\n"
                "        p = p.parent\n"
                "    return p\n"
                "R = _repo_root()\n"
                "(R / 'analysis' / 'escape.txt').write_text('CLOBBERED\\n')\n"
                "(R / 'data' / 'out.json').write_text('{}\\n')\n")
        _synth_repo(repo, {"g.py": body}, {"out.json": "{}\n"})
        mode = subprocess.run(["git", "-C", str(repo), "ls-files", "-s",
                               "analysis/escape.txt"],
                              capture_output=True, text=True).stdout
        assert mode.startswith("120000"), (
            f"the symlink is not tracked as a symlink, so this case proves nothing: {mode!r}")

        seen = {}

        def inspect(sb):
            seen["links"] = sorted(str(f.relative_to(sb.path))
                                   for f in sb.path.rglob("*") if f.is_symlink())
            staged = sb.path / "analysis" / "escape.txt"
            seen["is_symlink"] = staged.is_symlink()
            seen["text"] = staged.read_text()

        rep = DR.dry_run(repo / "analysis" / "g.py", on_built=inspect)

        assert seen["links"] == [], f"the sandbox holds symlinks: {seen['links']}"
        assert seen["is_symlink"] is False, \
            "analysis/escape.txt was seeded as a symlink -- a write path out of the sandbox"
        assert seen["text"] == original, seen["text"]
        assert secret.read_text() == original, \
            "the generator wrote through a seeded symlink and clobbered the file outside"
        assert rep.failure is None, rep.failure
        assert "analysis/escape.txt" in rep.result.wrote, sorted(rep.result.wrote)
    return ("a tracked symlink whose target is an absolute path outside the repo "
            "is seeded by copying its CONTENT: the sandbox contains no symlink at "
            "all, and a generator that writes through that path truncates the copy "
            "while the file outside stays byte-identical")


@case
def case_a_tracked_symlink_that_cannot_be_dereferenced_fails_closed():
    """Dereferencing has two cases a copy cannot serve -- a dangling link and a
    link to a directory. Both must raise DryRunError rather than being skipped
    silently or quietly recreated as a link."""
    gen = GEN_WALKUP % {"out": "out.json", "payload": '{"a": 1}'}
    for label, target in (("dangling", "no-such-file.txt"), ("directory", "adir")):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            repo = td / "repo"
            (repo / "analysis").mkdir(parents=True)
            if label == "directory":
                (repo / "adir").mkdir()
                (repo / "adir" / "keep.txt").write_text("x\n")
            os.symlink(str(repo / target), repo / "analysis" / "link.txt")
            _synth_repo(repo, {"g.py": gen}, {"out.json": '{\n "a": 1\n}\n'})
            try:
                DR.dry_run(repo / "analysis" / "g.py")
                raise AssertionError(
                    f"a {label} tracked symlink was seeded without complaint")
            except DR.DryRunError as e:
                assert "analysis/link.txt" in str(e), (label, str(e))
                assert label in str(e), (label, str(e))
            code, out = _cli(repo / "analysis" / "g.py")
            assert code == 2 and "FAILED" in out, (label, code, out)
    return ("a tracked symlink that is dangling, and one that points at a "
            "directory, are both refused with a DryRunError naming the path and "
            "the reason (exit 2) -- neither is skipped nor recreated as a link")


@case
def case_a_sandbox_that_cannot_be_removed_is_reported_as_a_failure():
    """The sandbox holds the whole copied private/ archive, so a disposal that
    fails and is merely printed leaves raw PII in the temp dir behind an exit 0.
    The wrapper below disposes for real and THEN raises, so the failure is
    simulated without stranding anything."""
    gen = GEN_WALKUP % {"out": "out.json", "payload": '{"a": 1}'}
    real_dispose = DR.Sandbox.dispose

    def boom(self):
        real_dispose(self)          # actually remove it; leave no litter behind
        raise DR.DryRunError("simulated: [Errno 16] Device or resource busy")

    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": gen}, {"out.json": '{\n "a": 1\n}\n'})
        DR.Sandbox.dispose = boom
        try:
            rep = DR.dry_run(repo / "analysis" / "g.py")
            # Swallow main()'s rendered report. It legitimately prints a line
            # beginning "FAILED:", and letting that reach this suite's own stdout
            # would make `grep '^FAIL'` over the run misread a passing suite.
            with contextlib.redirect_stdout(io.StringIO()) as rendered:
                code = DR.main([str(repo / "analysis" / "g.py"), "--check"])
            assert "FAILED:" in rendered.getvalue(), rendered.getvalue()
        finally:
            DR.Sandbox.dispose = real_dispose
        assert rep.failure is not None, \
            "a failed disposal was reported as a clean dry run"
        assert str(rep.sandbox_path) in rep.failure, (rep.sandbox_path, rep.failure)
        assert "private/" in rep.failure, rep.failure
        assert "Device or resource busy" in rep.failure, rep.failure
        text = DR.render(rep, "g.py")
        assert "FAILED:" in text and "nothing would change" not in text.lower(), text
        assert code == 2, code
        assert not pathlib.Path(rep.sandbox_path).exists(), "the test left a sandbox behind"
    return ("a sandbox that cannot be removed is a FAILURE on the report -- the CLI "
            "exits 2 and the message names the path still on disk and says it holds "
            "a full copy of private/ -- never a clean 'nothing would change'")


@case
def case_a_failed_baseline_cleanup_still_disposes_of_the_sandbox():
    """The two cleanups must be independent. Sharing one try means a baseline
    rmtree that raises skips dispose() and strands the copied archive."""
    gen = GEN_WALKUP % {"out": "out.json", "payload": '{"a": 1}'}
    real_rmtree = DR.shutil.rmtree
    stranded = []

    def rmtree(path, *a, **kw):
        if str(path).endswith("-baseline"):
            stranded.append(str(path))
            raise OSError("simulated: [Errno 39] Directory not empty")
        return real_rmtree(path, *a, **kw)

    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": gen}, {"out.json": '{\n "a": 1\n}\n'})
        DR.shutil.rmtree = rmtree
        try:
            rep = DR.dry_run(repo / "analysis" / "g.py")
        finally:
            DR.shutil.rmtree = real_rmtree
            # This test's own litter: the baseline copy dry_run() was prevented
            # from removing. It holds copies of committed data/ only.
            for p in stranded:
                q = pathlib.Path(p)
                if q.is_dir() and q.name.startswith(DR.SANDBOX_PREFIX) \
                        and pathlib.Path(tempfile.gettempdir()).resolve() in q.parents:
                    real_rmtree(q, ignore_errors=True)
        assert stranded, "the baseline cleanup never ran, so this case proved nothing"
        assert not pathlib.Path(rep.sandbox_path).exists(), (
            "a failed baseline cleanup skipped dispose() -- the sandbox, holding the "
            f"copied private/ archive, is still on disk at {rep.sandbox_path}")
        assert rep.failure is None, (
            "a leftover baseline copy (committed data/ only, no PII) must stay a "
            f"warning, not a dry-run failure: {rep.failure}")
    return ("when removing the -baseline copy raises, the sandbox is still disposed "
            "of -- the leftover baseline holds committed data/ only and stays a "
            "stderr warning, while the sandbox's copy of private/ is removed")


@case
def case_a_checkout_with_no_private_directory_still_runs():
    """A fork owner may have no private archive at all; the tool must say so
    plainly rather than crash somewhere obscure."""
    gen = GEN_WALKUP % {"out": "out.json", "payload": '{"a": 1}'}
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": gen}, {"out.json": '{\n "a": 1\n}\n'})
        assert not (repo / "private").exists()
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        joined = " ".join(rep.notes)
        assert "private/ does not exist" in joined, rep.notes
        assert "not as 'no changes'" in joined, rep.notes
        assert not rep.changes, [c.path for c in rep.changes]
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 0, (code, out)
    return ("a checkout with no private/ builds a sandbox anyway, says so in a note "
            "that names what will fail and why it will be a failure rather than a "
            "clean result, and still reports a correct empty diff")


# ---------------------------------------------------------------------------
# AC: the diff report itself is right.
# ---------------------------------------------------------------------------
@case
def case_the_diff_reports_added_removed_and_modified_with_json_and_csv_detail():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        base, cand = td / "base", td / "cand"
        base.mkdir(), cand.mkdir()
        (base / "gone.json").write_text('{"x": 1}')
        (cand / "new.csv").write_text("a,b\n1,2\n")
        (base / "j.json").write_text(json.dumps({"same": 1, "moved": 2, "stale": 3}))
        (cand / "j.json").write_text(json.dumps({"same": 1, "moved": 22, "fresh": 4}))
        (base / "c.csv").write_text("d,v\n2026-01-01,1\n2026-02-01,2\n")
        (cand / "c.csv").write_text("d,v\n2026-01-01,1\n2026-03-01,9\n2026-04-01,8\n")
        changes = {c.path: c for c in DR.diff_dirs(base, cand)}
        assert set(changes) == {"data/gone.json", "data/new.csv", "data/j.json",
                                "data/c.csv"}, sorted(changes)
        assert changes["data/gone.json"].kind == "removed"
        assert changes["data/new.csv"].kind == "added"
        jd = " ".join(changes["data/j.json"].detail)
        assert "keys added (1): fresh" in jd and "keys removed (1): stale" in jd \
            and "keys changed (1): moved" in jd, jd
        cd = " ".join(changes["data/c.csv"].detail)
        assert "rows 2 -> 3" in cd, cd
        assert "rows added (2)" in cd and "2026-03-01" in cd, cd
        assert "rows removed (1)" in cd and "2026-02-01" in cd, cd
    return ("diff_dirs() classifies added/removed/modified and describes a JSON "
            "artifact by its changed top-level keys and a CSV artifact by row "
            "counts plus the actual added and removed rows")


@case
def case_check_exits_zero_when_a_generator_reproduces_its_artifact():
    gen = GEN_PARENT % {"out": "out.json", "payload": '{"a": 1}'}
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": gen}, {"out.json": '{\n "a": 1\n}\n'})
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 0, (code, out)
        assert "nothing would change" in out.lower(), out
    return ("--check exits 0 when the generator reproduces its committed artifact "
            "byte for byte, so the section 9 gate can call it directly")


@case
def case_a_new_cwd_artifact_is_reported_as_an_addition_not_a_clean_run():
    """Several generators write their artifact into the CWD while the repo
    commits it under data/. One that writes a BRAND-NEW cwd artifact -- nothing
    under data/ to compare it with -- must be reported as an addition; dropping
    it for lack of a counterpart would print 'nothing would change' over a new
    artifact appearing."""
    body = ("import json, pathlib\n"
            "pathlib.Path('fresh_cwd_artifact.json').write_text("
            "json.dumps({'brand': 'new'}, indent=1) + '\\n')\n")
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body}, {"out.json": "{}\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert rep.changes == [], [c.path for c in rep.changes]   # data/ itself is untouched
        added = [(n, c) for n, c in rep.cwd_outputs if c is not True and c.kind == "added"]
        assert [n for n, _ in added] == ["fresh_cwd_artifact.json"], rep.cwd_outputs
        assert "new file" in " ".join(added[0][1].detail), added[0][1].detail
        assert rep.would_change is True
        text = DR.render(rep, "g.py")
        assert "nothing would change" not in text.lower(), text
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 1, (code, out)
        assert "+ fresh_cwd_artifact.json" in out, out
        assert "1 artifact(s) would change" in out, out
    return ("a generator writing a NEW artifact into its CWD, with no data/ "
            "counterpart to compare against, is reported as an addition naming the "
            "file -- --check exits 1 and the verdict never says 'nothing would "
            "change'")


@case
def case_the_seeded_root_files_are_not_reported_as_cwd_additions():
    """The counterpart-less half of _cwd_output_diffs() walks the sandbox ROOT,
    which holds every tracked root-level file (README.md, index.html, CLAUDE.md,
    ...) plus the seeded cwd fixtures (usage.csv, samA.csv, samB.csv). Almost
    none of them have a data/ counterpart, so a fix that reported every
    counterpart-less root file would flood a clean run with false additions.
    Assert on a REAL generator that a clean run stays clean."""
    gen = ROOT / "analysis" / "package_results.py"
    if not gen.is_file():
        raise SkipCase("analysis/package_results.py is missing from this checkout")
    trap = {}

    def look(sb):
        trap["counterpartless"] = sorted(
            f.name for f in sb.path.iterdir()
            if f.is_file() and not f.is_symlink()
            and not (sb.baseline_dir / f.name).is_file())

    rep = DR.dry_run(gen, on_built=look)
    if rep.failure is not None:
        raise SkipCase(f"package_results.py does not dry-run in this checkout: {rep.failure}")
    # The trap is real, not hypothetical: the sandbox root holds these files and
    # data/ has no counterpart for any of them, so a walk that reported every
    # counterpart-less root file would report all of them as new artifacts.
    assert len(trap["counterpartless"]) >= 5, trap["counterpartless"]
    assert "README.md" in trap["counterpartless"], trap["counterpartless"]
    assert rep.result.wrote, "the generator wrote nothing -- this case proved nothing"
    assert [c.path for c in rep.changes] == [], [c.path for c in rep.changes]
    noise = [n for n, c in rep.cwd_outputs if c is not True]
    assert noise == [], f"a clean run reported cwd artifacts as changing: {noise}"
    code, out = _cli(gen, "--check")
    assert code == 0, (code, out)
    assert "WOULD CHANGE data/  (nothing)" in out, out
    assert "nothing would change" in out.lower(), out
    return ("a real, reproducible generator (package_results.py) dry-runs to an "
            "empty change list under --check: none of the tracked root files or "
            "seeded cwd fixtures are mistaken for new cwd artifacts")


@case
def case_an_untracked_generator_is_seeded_and_flagged():
    """dry_run.py itself is untracked until it lands; a developer must be able to
    dry-run a brand-new generator without committing it first."""
    gen = GEN_WALKUP % {"out": "out.json", "payload": '{"a": 2}'}
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {}, {"out.json": '{\n "a": 1\n}\n'})
        (repo / "analysis" / "brand_new.py").write_text(gen)   # never committed
        rep = DR.dry_run(repo / "analysis" / "brand_new.py")
        assert rep.failure is None, rep.failure
        assert any("untracked" in n for n in rep.notes), rep.notes
        assert [c.path for c in rep.changes] == ["data/out.json"], rep.changes
    return ("an untracked generator is copied into the sandbox from the working "
            "tree and the report says so, so a new script can be dry-run before "
            "it is ever committed")


# ---------------------------------------------------------------------------
# AC: --baseline worktree means data/ AS IT STANDS ON DISK -- untracked files
# included. (issue #152)
# ---------------------------------------------------------------------------
# A generator that reproduces `loose.csv` byte for byte, and rewrites the tracked
# artifact too so the run always leaves a write trace. Uses the walk-up root idiom,
# so a sandbox escape would show up as a real-data hash failure rather than here.
GEN_TOUCHES_BOTH = """\
import pathlib
def _repo_root():
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found")
ROOT = _repo_root()
(ROOT / "data" / "out.json").write_text('{"a": 1}\\n')
(ROOT / "data" / "loose.csv").write_text(%(loose)r)
"""

LOOSE = "d,v\n2026-01-01,1\n"


def _repo_with_an_untracked_data_file(td, gen_body, extra_data=()):
    """A synthetic checkout whose data/ holds one TRACKED artifact and one
    untracked, non-ignored file -- the shape the real checkout is in whenever a
    stray artifact has not been committed yet."""
    repo = _synth_repo(td, {"g.py": gen_body}, {"out.json": '{"a": 1}\n'})
    (repo / "data" / "loose.csv").write_text(LOOSE)      # never committed
    for name, body in dict(extra_data).items():
        (repo / "data" / name).write_text(body)
    porcelain = subprocess.run(["git", "-C", str(repo), "status", "--porcelain",
                                "--", "data"], capture_output=True, text=True).stdout
    assert "?? data/loose.csv" in porcelain, porcelain   # the fixture is real
    return repo


@case
def case_an_untracked_data_file_is_part_of_the_worktree_baseline():
    """`--baseline worktree` promises "data/ as it is on disk". The sandbox is
    seeded from `git ls-files`, which sees tracked files only, so an untracked
    artifact used to be absent from the baseline entirely -- and a generator that
    reproduced it BYTE FOR BYTE was reported as an addition, with --check exiting 1
    over a run that would change nothing.

    Three claims, because the obvious fix breaks the other two: an exact
    reproduction is no change, a genuine rewrite is still a modification, and a
    generator that never touches the file produces no spurious `removed`."""
    reproduce = GEN_TOUCHES_BOTH % {"loose": LOOSE}
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_an_untracked_data_file(td, reproduce)
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert [c.path for c in rep.changes] == [], [
            (c.kind, c.path) for c in rep.changes]
        code, out = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 0, (code, out)
        assert "nothing would change" in out.lower(), out

    # Positive control on the same instrument: change one byte of what the
    # generator writes and the very same file must be reported as MODIFIED. Without
    # this, a baseline that simply hid untracked files would pass the assertions above.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_an_untracked_data_file(
            td, GEN_TOUCHES_BOTH % {"loose": "d,v\n2026-01-01,999\n"})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert [(c.kind, c.path) for c in rep.changes] == [
            ("modified", "data/loose.csv")], [(c.kind, c.path) for c in rep.changes]
        code, _ = _cli(repo / "analysis" / "g.py", "--check")
        assert code == 1, code

    # And a generator that never opens the untracked file must not report it as
    # removed -- the sign-flipped version of the same bug, which is what happens if
    # the baseline is filled from the real data/ instead of from the sandbox.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_an_untracked_data_file(
            td, GEN_PARENT % {"out": "out.json", "payload": '{"a": 9}'})
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert [(c.kind, c.path) for c in rep.changes] == [
            ("modified", "data/out.json")], [(c.kind, c.path) for c in rep.changes]
    return ("an untracked, non-ignored file under data/ is seeded into the sandbox "
            "and so into the worktree baseline: reproducing it is no change "
            "(--check exits 0), rewriting it is still a modification, and leaving "
            "it alone produces no spurious removal")


@case
def case_seeding_an_untracked_data_file_is_not_itself_a_write():
    """The empty-write guard reads MTIMES, not content, so anything seeded into the
    sandbox has to be backdated with everything else. If an untracked file were
    copied in after the backdate stamp, every run in a checkout carrying one would
    look like it had written something -- and "exited 0 and did nothing" would stop
    being a failure, which is the guarantee this whole tool rests on."""
    body = "import sys\nsys.exit(0)\n"          # exits 0, writes nothing, deletes nothing
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_an_untracked_data_file(td, body)
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is not None, "a do-nothing run was not reported as a failure"
        assert "wrote nothing and deleted nothing" in rep.failure, rep.failure
        assert rep.result.wrote == [], rep.result.wrote
        code, out = _cli(repo / "analysis" / "g.py")
        assert code == 2, (code, out)
    return ("a run that writes and deletes nothing is still a FAILURE in a checkout "
            "carrying an untracked data/ file: the seeded copy is backdated, so it "
            "is not mistaken for a write")


@case
def case_ignored_data_files_stay_out_of_both_sides():
    """.gitignore'd scratch under data/ (data/.parse_bills.lock is the real one) is
    not a repo change and must not become one from either direction: it is not
    seeded into the baseline, and a generator that writes it is reported as ignored
    scratch rather than as an artifact appearing."""
    body = (GEN_PARENT % {"out": "out.json", "payload": '{"a": 9}'}
            + '(ROOT / "data" / "scratch.tmp").write_text("written by the run\\n")\n')
    with tempfile.TemporaryDirectory() as td:
        repo = _synth_repo(td, {"g.py": body},
                           {"out.json": '{\n "a": 1\n}\n', ".gitignore": "*.tmp\n"})
        (repo / "data" / "scratch.tmp").write_text("on disk, ignored\n")
        rep = DR.dry_run(repo / "analysis" / "g.py")
        assert rep.failure is None, rep.failure
        assert [c.path for c in rep.changes] == ["data/out.json"], [
            (c.kind, c.path) for c in rep.changes]
        assert rep.ignored == ["data/scratch.tmp"], rep.ignored
    return ("an ignored file under data/ is kept out of the baseline and, when the "
            "run writes one, reported as ignored scratch -- never as a data/ change")


@case
def case_the_head_baseline_still_answers_against_head_alone():
    """`--baseline head` is defined against HEAD, where an untracked working-tree
    file legitimately has no counterpart. Seeding untracked files for the worktree
    baseline must not leak into it: a generator that never touches such a file must
    report only what it did change."""
    with tempfile.TemporaryDirectory() as td:
        repo = _repo_with_an_untracked_data_file(
            td, GEN_PARENT % {"out": "out.json", "payload": '{"a": 9}'})
        rep = DR.dry_run(repo / "analysis" / "g.py", baseline="head")
        assert rep.failure is None, rep.failure
        assert [(c.kind, c.path) for c in rep.changes] == [
            ("modified", "data/out.json")], [(c.kind, c.path) for c in rep.changes]
        assert rep.dirty_baseline == [], rep.dirty_baseline
    return ("--baseline head still compares against HEAD alone: an untracked "
            "working-tree file under data/ appears on neither side of it")


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
