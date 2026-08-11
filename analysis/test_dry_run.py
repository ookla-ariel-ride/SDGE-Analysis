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

  * a generator that exits 0 having written nothing, and one that crashes, must
    both be reported as FAILURES -- never as "nothing would change".

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
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
ROOT = ANALYSIS.parent
sys.path.insert(0, str(ANALYSIS))

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
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
