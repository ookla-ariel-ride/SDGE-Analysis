#!/usr/bin/env python3
"""The census of code that copies raw private data, and the proof that the two
destination guards agree (issue #186).

Three things live here, and they answer different questions.

1. THE CENSUS. MOVERS below is a registry of every code path that copies this
   household's raw private files. Each entry names THE TEST CASE that proves
   its guard, or states in words why it has none -- never a boolean, because a
   boolean rots into a claim nobody re-reads. Discovery runs INDEPENDENTLY of
   the registry (an AST pass for python, a scan for shell), so deleting an
   entry cannot delete its own check: an unregistered mover fails
   case_every_discovered_mover_is_registered, a registered mover that no longer
   exists fails case_every_registered_mover_still_exists, and a named guard
   that does not exist -- or exists but never runs -- fails
   case_every_named_guard_case_exists_and_runs.

2. THE AGREEMENT. private_egress.check_write_set() and stage-private-data.sh
   enforce the same rule in two languages. Both are run over ONE shared table
   of destination fixtures and must return identical verdicts. That is stronger
   than sharing code and needs no refactor of a guard that is now well tested;
   it also catches either one drifting. The table is one list, so a case cannot
   be added to one side only, and the WRITE SET the python side checks is
   PARSED OUT OF THE SHELL SCRIPT rather than retyped -- a new `cp` line there
   changes what this suite checks, or fails case_the_write_set_matches_the_
   shell_script.

3. THE PREDICATE'S OWN CASES: what it accepts and refuses on this checkout.

WITHOUT THE PRIVATE ARCHIVE. Nothing here needs it. The census is static; the
agreement table stages a SYNTHETIC source (empty files with the right names)
into throwaway `git worktree add` worktrees of this checkout. What it does need
is a git checkout with a worktree register, so the handful of cases that build
one raise SkipCase where that is impossible.

Run from the repo root:  ./.venv/bin/python analysis/test_private_egress.py
"""
import ast
import contextlib
import os
import pathlib
import re
import signal
import stat
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
ROOT = ANALYSIS.parent
sys.path.insert(0, str(ANALYSIS))

import private_egress as PE  # noqa: E402

SCRIPT = ROOT / "stage-private-data.sh"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


# ===========================================================================
# 1. DISCOVERY -- independent of the registry below
#
# WHAT COUNTS AS A MOVER, and it is deliberately narrower than "writes a file".
# The ~120 sites that write de-identified aggregates into data/ are the
# product, not egress. The incident class is a FILE-LEVEL COPY OF RAW PRIVATE
# DATA, or a write of private-derived content at a fixed private path -- which
# is a dozen or so sites in this tree, not the ~120 that write anything:
#
#   python   a copy call (shutil.copy/copy2/copyfile/copytree/move, os.link,
#            os.symlink, tarfile extraction, shutil.unpack_archive) any of
#            whose arguments is a path ANCHORED AT THE REPO ROOT that names
#            private/, or a write call (write_text/write_bytes/mkdir) AT such a
#            path. Root-anchoring is what separates the real archive from the
#            many test fixtures built under a temp directory whose own path
#            contains the word "private".
#   python   a tempfile.mkstemp/mkdtemp/NamedTemporaryFile with NO dir= in a
#            SHIPPED module that handles private data. That is not a copy, it
#            is worse: the destination is whatever TMPDIR says, so the file
#            lands somewhere no guard here has an opinion about.
#   python   a subprocess call whose argv reaches a discovered SHELL mover.
#            Running a mover is moving.
#   shell    a copy command (cp/rsync/install/ln/tar/scp/ditto) at the start of
#            a command in a script that mentions private/ at all. There is no
#            AST for shell, so the file-level condition is the conservative
#            shape -- the same choice privacy_tiers.scan_script_reads() makes
#            for the same reason.
#
# The taint pass is SCOPED (a name bound inside one function does not taint the
# same name in another) and binds only what an assignment really rebinds --
# walking the target blindly binds the `self` in `self.x = <private path>` and
# taints every expression in the class. Both were real false-positive sources,
# found by running this against the tree; before they were fixed, adding the
# write rule above returned 21 python sites, most of them tests writing a
# fixture into their own temp directory. With them fixed the same rule adds
# three sites and no false positives -- measured, both times, on this tree.
#
# WHAT IT MISSES, stated rather than discovered later:
#   * a private path that arrives as a bare function PARAMETER. The pass
#     propagates through assignments, for-targets, with-targets and parameter
#     DEFAULTS within a scope, and through a function's return value; it does
#     not do interprocedural analysis, so `def save_cache(cache_dir, ...)`
#     looks like any other function. That is a registry row with
#     kind="declared", so the blind spot is enumerated rather than admitted.
#   * a path assembled at runtime from an environment variable or read out of a
#     config file. The census found none in this tree -- no shell=True, no
#     eval, no env-derived destination anywhere in analysis/ -- which is the
#     property that makes static discovery tractable here at all, and it is a
#     property of today's tree, not a guarantee.
#   * anything in an untracked-and-gitignored file. The scan reads git's list
#     of tracked files plus untracked-not-ignored ones, so a mover added but
#     not yet committed IS seen; one hidden inside private/ is not, and that is
#     deliberate: private/ is not published.
# ===========================================================================
COPY_CALLS = {"copy", "copy2", "copyfile", "copytree", "move", "unpack_archive",
              "extractall", "extract", "link", "symlink"}
WRITE_CALLS = {"write_text", "write_bytes", "mkdir"}
TEMP_CALLS = {"mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"}
RUN_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
ROOT_NAMES = {"ROOT"}          # the repo-root constant every module here uses
ROOT_ATTRS = {"root"}          # ... and dry_run's Sandbox.root
SHELL_COPY = re.compile(r"^\s*(?:[\w.]+=\S*\s+)*(cp|rsync|install|ln|tar|scp|ditto)\s")


def source_files(root=ROOT):
    """Tracked files, plus untracked ones git would not ignore.

    Untracked-not-ignored is included on purpose: a mover added in the working
    tree fails this suite BEFORE it is committed, which is the point of a build
    gate. Ignored files (private/ itself) are excluded -- they are not published
    and cannot be reviewed here.
    """
    out = []
    for args in (["ls-files"], ["ls-files", "--others", "--exclude-standard"]):
        r = subprocess.run(["git", "-C", str(root)] + args,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SkipCase(f"{root} is not a git checkout, so the file list is unavailable")
        out += [ln for ln in r.stdout.splitlines() if ln]
    return sorted(set(out))


def _private_string(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and (node.value == "private" or node.value.startswith("private/")
                 or "/private/" in node.value))


def _root_anchored(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in ROOT_NAMES:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in ROOT_ATTRS:
            return True
    return False


def _archive_expr(node):
    """A path anchored at the REPO ROOT that names private/ -- the real archive,
    as opposed to a fixture built under a directory the code created itself."""
    return any(_private_string(s) for s in ast.walk(node)) and _root_anchored(node)


def _own_nodes(scope):
    """Every descendant of `scope` that is not inside a NESTED scope."""
    out = []

    def walk(n):
        for c in ast.iter_child_nodes(n):
            out.append(c)
            if not isinstance(c, SCOPE_NODES):
                walk(c)
    walk(scope)
    return out


def _bind(target, names):
    """Bind only what the assignment really rebinds: the Name inside
    `self.x = <private path>` is not a rebinding of `self`."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            _bind(el, names)
    elif isinstance(target, ast.Starred):
        _bind(target.value, names)


def _tainted(node, names):
    if _archive_expr(node):
        return True
    return any(isinstance(s, ast.Name) and s.id in names for s in ast.walk(node))


def _scope_taint(scope, inherited, seed=_archive_expr):
    names = set(inherited)
    nodes = _own_nodes(scope)
    for _ in range(6):                       # fixpoint; six rounds is slack
        before = len(names)
        for node in nodes:
            if isinstance(node, ast.Assign) and _taints(node.value, names, seed):
                for t in node.targets:
                    _bind(t, names)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and \
                    node.value is not None and _taints(node.value, names, seed):
                _bind(node.target, names)
            elif isinstance(node, (ast.For, ast.AsyncFor)) and _taints(node.iter, names, seed):
                _bind(node.target, names)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is not None and \
                            _taints(item.context_expr, names, seed):
                        _bind(item.optional_vars, names)
        if len(names) == before:
            break
    return names


def _taints(node, names, seed):
    if seed(node):
        return True
    return any(isinstance(s, ast.Name) and s.id in names for s in ast.walk(node))


def _scopes(tree, seed=_archive_expr, params=True):
    """[(scope, qualified name, tainted names)], module first.

    Two propagations beyond plain assignment, each earning its place on a real
    site in this tree and each measured for what it costs:

    * a function whose RETURN value is a private path taints its own NAME, so
      `path = _dry_run_dir() / f"{stamp}.json"` is seen. This was removed once,
      during development, because it appeared to taint half of dry_run.py --
      the real cause was the target-binding defect in _bind(), and with that
      fixed it costs nothing.
    * `params=True` seeds a function's locals from its parameter DEFAULTS,
      which is how test_stage_private_data.py's `_run_script(..., script=SCRIPT)`
      carries a mover script into a subprocess call.
    """
    module = _scope_taint(tree, set(), seed)
    for _ in range(2):          # a returns-tainted name can taint another return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = _scope_taint(node, module, seed)
                for sub in _own_nodes(node):
                    if isinstance(sub, ast.Return) and sub.value is not None \
                            and _taints(sub.value, local, seed):
                        module.add(node.name)
        module = _scope_taint(tree, module, seed)
    out = [(tree, "<module>", module)]

    def walk(node, prefix, inherited):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, SCOPE_NODES):
                qn = f"{prefix}.{c.name}" if prefix else c.name
                start = set(inherited)
                if params and isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = c.args
                    slots = list(args.args) + list(args.kwonlyargs)
                    defaults = ([None] * (len(args.args) - len(args.defaults))
                                + list(args.defaults) + list(args.kw_defaults))
                    for a, d in zip(slots, defaults):
                        if d is not None and _taints(d, start, seed):
                            start.add(a.arg)
                names = _scope_taint(c, start, seed)
                out.append((c, qn, names))
                walk(c, qn, names)
            else:
                walk(c, prefix, inherited)
    walk(tree, "", module)
    return out


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    return f.id if isinstance(f, ast.Name) else ""


def discover_shell(root=ROOT, files=None):
    """{(relpath, "<script>"): [evidence]} for shell scripts that copy.

    Keyed on the FILE, not on a line: shell has no symbol a reviewer would
    recognise, and every copy in a shell script here is at the top level of the
    same run.
    """
    found = {}
    for rel in (files if files is not None else source_files(root)):
        p = pathlib.Path(root) / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        first = text.splitlines()[0] if text else ""
        if not (rel.endswith(".sh") or (first.startswith("#!") and "sh" in first)):
            continue
        body = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
        if "private" not in body:
            continue
        hits = [f"line {i + 1}: {ln.strip()[:60]}"
                for i, ln in enumerate(body.splitlines()) if SHELL_COPY.search(ln)]
        if hits:
            found[(rel, "<script>")] = hits
    return found


def discover_python(root=ROOT, files=None, shell_scripts=()):
    """{(relpath, qualified symbol): [evidence]} -- see the block comment above."""
    names = {pathlib.Path(f).name for f, _ in shell_scripts}
    found = {}
    for rel in (files if files is not None else source_files(root)):
        if not rel.endswith(".py"):
            continue
        p = pathlib.Path(root) / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            # Fail closed, like privacy_tiers.scan_script_reads(): a file this
            # cannot read is a file whose movers are invisible.
            raise AssertionError(f"{rel} does not parse, so it cannot be scanned for movers")
        is_test = pathlib.Path(rel).name.startswith("test_")
        module_private = any(_private_string(n) for n in ast.walk(tree))

        def script_seed(node, _names=names):
            return any(isinstance(s, ast.Constant) and isinstance(s.value, str)
                       and s.value in _names for s in ast.walk(node))

        script_taint = {qn: nm for _, qn, nm in _scopes(tree, script_seed, params=True)}
        for scope, qn, tainted in _scopes(tree):
            scripts = script_taint.get(qn, set())
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Call):
                    continue
                nm = _call_name(node)
                args = list(node.args) + [k.value for k in node.keywords]
                why = None
                if nm in COPY_CALLS and any(_tainted(a, tainted) for a in args):
                    why = f"{nm}() of a repo-root private path"
                elif (nm in WRITE_CALLS and isinstance(node.func, ast.Attribute)
                      and _tainted(node.func.value, tainted)):
                    why = f"{nm}() AT a repo-root private path"
                elif nm in RUN_CALLS and any(
                        _taints(a, scripts, script_seed) for a in args):
                    why = "invokes a mover script"
                elif (nm in TEMP_CALLS and module_private and not is_test
                      and not any(k.arg == "dir" for k in node.keywords)):
                    why = f"{nm}() with no dir=, in a module that handles private data"
                if why:
                    # "runs a mover script" is keyed at the FILE, not the symbol:
                    # a suite that drives stage-private-data.sh calls it from
                    # every case, and six registry rows saying the same sentence
                    # about the same script make the census harder to read
                    # without making it more complete.
                    sym = "<invokes a mover script>" if nm in RUN_CALLS else qn
                    found.setdefault((rel, sym), []).append(f"line {node.lineno}: {why}")
    return found


def discover(root=ROOT):
    files = source_files(root)
    sh = discover_shell(root, files)
    py = discover_python(root, files, sh)
    out = dict(sh)
    out.update(py)
    return out


# ===========================================================================
# 2. THE REGISTRY
#
# KEYED ON file + SYMBOL, and neither alone.
#   * file alone is too coarse: dry_run.py has three distinct movers with three
#     different destinations, and a registry that said "dry_run.py is fine"
#     would not notice a fourth.
#   * file:LINE is stale within a week -- one module here is 6,000 lines and
#     several are edited most days; a registry whose keys move every time
#     something above them is edited teaches people to regenerate it rather
#     than read it.
#   * destination CLASS is what a reviewer actually reasons about, so it is a
#     FIELD rather than the key: two movers can share a class ("a throwaway
#     TemporaryDirectory") and still need separate guards.
#   A function name is what a reviewer names, survives edits inside the
#   function, and changes exactly when the mover is really moved or removed.
#
# kind="discovered" entries must be re-found by the pass above on every run.
# kind="declared" entries name a site the heuristic CANNOT see, with the reason
# it cannot; their file and symbol are still verified to exist. Every discovered
# mover must be classified, INCLUDING the ones that legitimately need no guard:
# a registry that only admits guarded things gets the awkward ones quietly
# omitted and becomes aspirational.
# ===========================================================================
class Mover:
    def __init__(self, moves, destination, kind="discovered", guard=None, unguarded=None,
                 invisible=None):
        self.moves = moves                # what raw private data it copies
        self.destination = destination    # the destination CLASS, in words
        self.kind = kind
        self.guard = guard                # "analysis/test_x.py::case_y", or None
        self.unguarded = unguarded        # why it has no guard, if it has none
        self.invisible = invisible        # why discovery cannot see it (declared only)


MOVERS = {
    ("stage-private-data.sh", "<script>"): Mover(
        moves="the whole raw archive: household.yaml, the Green Button and SAM "
              "exports, gas.csv, the bill PDF directories, the billing-history "
              "export and (when present) caiso_raw",
        destination="a REGISTERED worktree of this checkout, supplied as argv[2]",
        guard="analysis/test_stage_private_data.py::"
              "case_refuses_a_plain_directory_whose_git_gitfile_forges_membership"),

    ("analysis/check_coverage.sh", "<script>"): Mover(
        moves="nothing private OUTWARD: it copies analysis/*.py and two committed "
              "data/*.csv INTO private/verify, then runs the generators there",
        destination="private/verify inside the checkout the script derives from its "
                    "own location (cd \"$(dirname \"$0\")/..\")",
        unguarded="No test asserts where it writes. It is in the census because it "
                  "is a shell script that copies and touches private/, and the "
                  "honest answer is that its containment rests on `cd $(dirname "
                  "$0)/..` and on every path being $ROOT-relative -- reviewed by "
                  "eye here, not by a mechanism. The exposure is small and in the "
                  "safe direction: the data moving is public (committed scripts and "
                  "committed artifacts) and the destination is inside the private "
                  "tree, so a wrong $ROOT loses a coverage run rather than leaking "
                  "a household."),

    ("analysis/dry_run.py", "Sandbox._copy_private"): Mover(
        moves="the entire private/ tree, copied (never symlinked) into the sandbox",
        destination="a mkdtemp() sandbox under TMPDIR, refused if it is entangled "
                    "with the checkout",
        guard="analysis/test_dry_run.py::"
              "case_a_generator_that_writes_under_private_cannot_reach_the_real_archive"),

    ("analysis/dry_run.py", "Sandbox._copy_cwd_fixtures"): Mover(
        moves="private/verify/usage.csv and the SAM fixtures the generators read "
              "from their working directory",
        destination="the same mkdtemp() sandbox",
        guard="analysis/test_dry_run.py::"
              "case_a_generator_that_overwrites_a_cwd_fixture_cannot_reach_the_real_one"),

    ("analysis/dry_run.py", "Sandbox.build"): Mover(
        moves="nothing itself -- it CREATES the directory the two movers above "
              "fill, with mkdtemp() and no dir=, so TMPDIR chooses the location",
        destination="TMPDIR, checked only for entanglement with the checkout",
        guard="analysis/test_dry_run.py::"
              "case_the_sandbox_is_outside_the_repo_and_holds_no_path_back_into_private"),

    ("analysis/llm_providers.py", "_gitleaks_scan"): Mover(
        moves="the assembled outbound request body -- private-DERIVED text, not a "
              "raw file, written to a real temp file because gitleaks's "
              "path-scoped rules do not run on --pipe",
        destination="mkstemp() with no dir=: a TMPDIR-controlled path, outside "
                    "every worktree and outside every guard in this repo",
        unguarded="test_egress_preflight.py guards WHAT may be sent and refuses any "
                  "path under private/; nothing asserts where this scratch copy "
                  "lands. It is removed in a finally, including on timeout, so the "
                  "window is the scan's duration -- but on a shared TMPDIR the "
                  "content is a household's data on a world-readable path for that "
                  "window. Wiring check_destination() in here is the first thing "
                  "the follow-up change should do; see this module's docstring on "
                  "why the wiring is not part of #186."),

    ("analysis/test_parse_bills.py", "_build"): Mover(
        moves="every electric (and gas) bill PDF in the real corpus",
        destination="a tempfile.TemporaryDirectory() the case creates and deletes",
        unguarded="The destination is not argument-derived and not caller-supplied: "
                  "it is a TemporaryDirectory() created two frames up and removed on "
                  "exit. There is nothing for a destination guard to check that "
                  "TMPDIR does not already decide, which is the same exposure "
                  "llm_providers._gitleaks_scan has and the reason both are listed "
                  "rather than one of them."),

    ("analysis/test_scripts_runnable.py", "_build_throwaway_root"): Mover(
        moves="private/household.yaml, the whole of private/1-raw-data, and the "
              "private/verify CWD fixtures",
        destination="a tempfile.TemporaryDirectory() the case creates and deletes",
        unguarded="Same shape as test_parse_bills._build: a TemporaryDirectory the "
                  "test owns. The copy exists precisely so a generator cannot "
                  "corrupt the real archive (its own comment says so), and that "
                  "direction -- protecting the source -- is guarded by the "
                  "byte-identity assertions in its own suite. Where the copy lands "
                  "is guarded by nothing."),

    ("analysis/llm_providers.py", "_dry_run_dir"): Mover(
        moves="nothing itself -- it CREATES private/llm_dry_run/, the directory "
              "preflight() then writes assembled request bodies into",
        destination="ROOT/private/llm_dry_run, inside this checkout, with "
                    "parents=True and exist_ok=True",
        unguarded="mkdir(parents=True) follows a symlink at any component and "
                  "writes straight through it, and nothing asks whether the "
                  "directory it creates is ignored in this checkout. Both are one "
                  "check_destination() call. The exposure is small today because "
                  "the path is ROOT-anchored and this repo ignores private/, which "
                  "is exactly the assumption 2026-08-13 showed is worth checking "
                  "rather than assuming."),

    ("analysis/llm_providers.py", "preflight"): Mover(
        moves="the assembled request body, written under private/llm_dry_run/ for "
              "inspection before any real API call",
        destination="the directory _dry_run_dir() just created",
        unguarded="Same as _dry_run_dir, one level down: the write itself asks "
                  "nothing about where it lands. Listed separately because it is a "
                  "separate call that a later edit could point elsewhere."),

    ("analysis/generate_report.py", "run"): Mover(
        moves="the generation manifest: which blocks were produced, from which "
              "inputs, with which provider",
        destination="--manifest-path, ARGUMENT-DERIVED, defaulting to "
                    "ROOT/private/report_generation_manifest.json",
        unguarded="The 2026-08-13 shape exactly: a caller hands a path and the code "
                  "writes private-derived content there, with nothing checking what "
                  "it was handed -- including mkdir(parents=True) on its parent. "
                  "This is the first call site check_destination() was written for."),

    ("analysis/test_stage_private_data.py", "<invokes a mover script>"): Mover(
        moves="whatever the real stage-private-data.sh copies -- this runs it, "
              "against the real archive in the archive-present cases",
        destination="linked worktrees of this checkout, created and removed by the "
                    "case, plus deliberately hostile fixtures the script must refuse",
        guard="analysis/test_stage_private_data.py::"
              "case_accepts_a_registered_linked_worktree_and_the_register_names_it"),

    ("analysis/test_private_egress.py", "<invokes a mover script>"): Mover(
        moves="a SYNTHETIC source only -- empty files with the archive's names. "
              "This suite never stages the real archive anywhere",
        destination="throwaway worktrees of this checkout, created and removed per "
                    "case by _worktree()",
        guard="analysis/test_private_egress.py::"
              "case_the_shell_and_the_python_predicate_agree_on_every_destination"),

    # -- declared: a real mover the discovery pass cannot see ---------------
    ("analysis/generate_report.py", "save_cache"): Mover(
        kind="declared",
        moves="LLM response fragments, keyed by prompt hash -- derived from "
              "private inputs, one file per block",
        destination="cache_dir, a PARAMETER, reaching it from --cache-dir "
                    "(default ROOT/private/report_cache) through two call frames",
        invisible="the destination is a bare function PARAMETER with no default: "
                  "`def save_cache(cache_dir, ...)` then `d.mkdir(parents=True)`. "
                  "Nothing in the file says the path is private, and the pass does "
                  "not do interprocedural analysis, so no rule short of tracing "
                  "every call site would see it. Its sibling generate_report.run IS "
                  "discovered, because it rebinds its own manifest_path parameter "
                  "from the module-level private default -- which is the whole "
                  "difference, and a fair measure of how thin this blind spot is.",
        unguarded="The 2026-08-13 shape: a caller hands a directory and the code "
                  "mkdirs and writes private-derived files into it, with nothing "
                  "checking what it was handed."),
}

# The anti-narrowing floor, in the shape _SEAM_VOCABULARY_FLOOR uses in
# test_report_tokens.py: deleting an entry from MOVERS above fails against this,
# so shrinking the census is a deliberate TWO-PLACE edit with a reviewer's name
# on it instead of one line quietly removed from a dict. Adding a mover stays a
# one-place edit -- the direction that should be easy.
#
# WRITTEN OUT, never `frozenset(MOVERS)`. A floor derived from the thing it
# guards agrees with it by construction and can never fail; that version of this
# constant was here first, and it passed while an entry was being deleted.
MOVERS_FLOOR = frozenset({
    ("stage-private-data.sh", "<script>"),
    ("analysis/check_coverage.sh", "<script>"),
    ("analysis/dry_run.py", "Sandbox._copy_private"),
    ("analysis/dry_run.py", "Sandbox._copy_cwd_fixtures"),
    ("analysis/dry_run.py", "Sandbox.build"),
    ("analysis/llm_providers.py", "_gitleaks_scan"),
    ("analysis/llm_providers.py", "_dry_run_dir"),
    ("analysis/llm_providers.py", "preflight"),
    ("analysis/generate_report.py", "save_cache"),
    ("analysis/generate_report.py", "run"),
    ("analysis/test_parse_bills.py", "_build"),
    ("analysis/test_scripts_runnable.py", "_build_throwaway_root"),
    ("analysis/test_stage_private_data.py", "<invokes a mover script>"),
    ("analysis/test_private_egress.py", "<invokes a mover script>"),
})


# ===========================================================================
# 3. THE SHELL'S WRITE SET, parsed from the shell
#
# The python side of the agreement table must ask about the SAME paths
# stage-private-data.sh writes. Retyping them here would drift on the first new
# `cp` line, so they are parsed out of the script and only then compared with
# the declared constants below -- which is what makes adding a case to one side
# impossible without the other.
# ===========================================================================
DECLARED_IGNORE_PATHS = ("private/1-raw-data", "private/verify", "private/household.yaml")
DECLARED_SUBTREES = ("electric-bills", "gas-bills", "caiso_raw")
DECLARED_LEAVES = (
    "private/household.yaml",
    "private/verify/usage.csv",
    "private/verify/samA.csv",
    "private/verify/samB.csv",
    "private/1-raw-data/gas.csv",
    "private/1-raw-data/electric_billing_history_2024-2026.csv",
    "private/1-raw-data/Electric_15_Minute_*.csv",
    "private/1-raw-data/enphase_sam8760_*.csv",
)

_RE_UNCOMMITTABLE = re.compile(r'^_require_uncommittable\s+"([^"]+)"', re.M)
_RE_SUBTREES = re.compile(r'_scanned_subtrees="(?:\$_scanned_subtrees )?([\w-]+)"')
_RE_LEAF_ARRAY = re.compile(r'^_dst_leaves=\((.*?)\)\s*$', re.M | re.S)
# The two glob families are read from the `for _srcfile in ... ; do` loop that
# builds them, not from anywhere the pattern happens to appear: the STAGE_CAISO
# probe elsewhere in the script globs caiso_co2_*.csv without ever writing it,
# and a looser scan silently added a file the copies never touch.
_RE_LEAF_GLOB_LOOP = re.compile(r'for _srcfile in (.*?);\s*do', re.S)
_RE_LEAF_GLOB = re.compile(r'"\$SRC"/private/1-raw-data/([^\s"\\;]+\*[^\s"\\;]*)')


def _shell_write_set(text):
    ignore = _RE_UNCOMMITTABLE.findall(text)
    subtrees = _RE_SUBTREES.findall(text)
    m = _RE_LEAF_ARRAY.search(text)
    leaves = []
    if m:
        for tok in re.findall(r'"([^"]+)"', m.group(1)):
            leaves.append(tok.replace("$DST_REAL/", ""))
    loop = _RE_LEAF_GLOB_LOOP.search(text)
    assert loop, "stage-private-data.sh: the leaf-glob loop could not be found"
    globs = sorted({f"private/1-raw-data/{g}" for g in _RE_LEAF_GLOB.findall(loop.group(1))})
    return ignore, subtrees, leaves + globs


# ===========================================================================
# 4. FIXTURES for the agreement table
# ===========================================================================
def _synthetic_src(td):
    """A SOURCE tree stage-private-data.sh accepts, holding no real data: empty
    files with the names the copies look for, a household.yaml with has_gas
    false, and a .venv/bin/python standing in for the source's interpreter.

    The stand-in points at sys.executable rather than at ROOT/.venv, which CI
    never creates (tests.yml pip-installs into the runner's interpreter).
    """
    src = pathlib.Path(td) / "src"
    (src / "analysis").mkdir(parents=True)
    (src / "data").mkdir(parents=True)
    raw = src / "private" / "1-raw-data"
    raw.mkdir(parents=True)
    (src / "analysis" / "household.py").write_bytes(
        (ANALYSIS / "household.py").read_bytes())
    (src / ".venv" / "bin").mkdir(parents=True)
    (src / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (src / "private" / "household.yaml").write_text(
        "household:\n  has_gas: false\n")
    for name in ("gas.csv", "electric_billing_history_2024-2026.csv",
                 "Electric_15_Minute_test.csv", "enphase_sam8760_2025.csv",
                 "enphase_sam8760_2026.csv"):
        (raw / name).touch()
    (raw / "electric-bills").mkdir()
    return src


@contextlib.contextmanager
def _worktree(td, name="dst"):
    """A REAL registered worktree of this checkout, handed back to git after."""
    path = pathlib.Path(td) / name
    assert path.parent == pathlib.Path(td), "the worktree must live in the case's tempdir"
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                      capture_output=True).returncode != 0:
        raise SkipCase("this checkout is not a git repository")
    added = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(path), "HEAD"],
        capture_output=True, text=True)
    if added.returncode != 0:
        raise SkipCase(f"could not create a worktree of this checkout: {added.stderr[:200]}")
    try:
        yield path
    finally:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(path)],
                       capture_output=True, text=True)


def _run_shell(src, dst, cwd, env=None, timeout=120):
    """Run the REAL script and read its exit status DIRECTLY.

    `cwd` is the case's own tempdir, never this checkout: household.py resolves
    the repo root from the CWD FIRST and only then from its own location, so a
    run standing in this checkout validates the SOURCE's has_gas against THIS
    household's real intake file. (Reproduced while building this suite; noted
    in the issue report, not worked around here beyond standing somewhere
    neutral -- stage-private-data.sh is not this change's to edit.)

    Never piped: a pipeline's status is its LAST command's, which is exactly the
    bug behind issue #186 -- `git worktree add ... | tail -2` failed, `tail`
    returned 0, and the archive was staged against a directory this repo does
    not own. Nothing in this suite may infer a verdict from output text alone.

    Bounded and killed by PROCESS GROUP, because one fixture plants a FIFO: if
    the guard ever regressed, the blocked process would be a `cp` GRANDCHILD
    that subprocess's own timeout would leave running with the FIFO open.
    """
    argv = ["bash", str(SCRIPT), str(src), str(dst)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(cwd), env=env, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        raise AssertionError(
            f"stage-private-data.sh did not finish within {timeout}s -- it blocked "
            f"opening a destination path instead of refusing it. stderr: {err[-400:]!r}")
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


# stage-private-data.sh's REFUSED headline -> private_egress's reason code. The
# mapping is explicit because the two vocabularies were written independently;
# an unmapped headline is a FAILURE, never a silent "they disagree".
SHELL_HEADLINES = {
    "destination is not an existing directory": "no_such_destination",
    "destination is not a git working tree": "not_a_worktree",
    "destination belongs to a DIFFERENT repository": "different_repository",
    "destination has no working tree of its own": "no_worktree_of_its_own",
    "destination is a subdirectory, not a worktree root": "not_worktree_root",
    "this checkout's worktrees could not be listed": "register_unavailable",
    "destination is not a REGISTERED worktree of this checkout": "not_registered",
    "the destination TRACKS a path this script writes": "tracked_path",
    "the destination does not gitignore a path this script writes": "not_ignored",
    "the destination could not say whether it ignores a path this script writes":
        "ignore_unanswerable",
    "the destination could not be asked which paths it tracks": "tracked_unanswerable",
    "a destination path is a symbolic link": "symlink_component",
    "a destination directory does not resolve to itself": "symlink_component",
    "a destination path exists and is not a directory": "not_a_directory",
    "a destination file is not a regular file": "special_file",
    "a destination file is a HARD link": "hard_link",
    "the destination contains symbolic link(s)": "symlink_under",
    "the destination contains special file(s)": "special_under",
    "the destination contains hard-linked file(s)": "hardlink_under",
    "the destination could not be scanned for symbolic links": "scan_unreadable",
    "the destination could not be scanned for special files": "scan_unreadable",
    "the destination could not be scanned for hard links": "scan_unreadable",
    "a destination file's link count could not be read": "scan_unreadable",
}


def _shell_verdict(res):
    """None when the script accepted the destination, else the reason code."""
    if res.returncode == 0:
        return None
    m = re.search(r"stage-private-data\.sh: REFUSED -- (.*?) \(nothing was written\)",
                  res.stderr)
    if not m:
        raise AssertionError(
            f"the script exited {res.returncode} with no parsable REFUSED headline; "
            f"stderr: {res.stderr[-600:]!r}")
    headline = m.group(1)
    assert headline in SHELL_HEADLINES, (
        f"unmapped REFUSED headline {headline!r} -- add it to SHELL_HEADLINES with "
        "the private_egress reason it corresponds to, or the two implementations "
        "are being compared on a refusal nobody has classified")
    return SHELL_HEADLINES[headline]


def _python_verdict(root, src, env=None):
    """private_egress's verdict on the same question, over the write set parsed
    from the shell script itself."""
    ignore, subtrees, leaves = _shell_write_set(SCRIPT.read_text())
    # A managed path that the copies also write as a FILE is a leaf, not a
    # directory -- derived from the two parsed lists rather than re-typed, so a
    # fourth managed path arrives correctly classified.
    dirs = [p for p in ignore if p not in leaves]
    # The source decides which subtrees the script will actually descend, and
    # the python side is told the same thing: has_gas false and no caiso_raw in
    # the synthetic source means neither is copied, so neither is scanned.
    recursive = [f"private/1-raw-data/{s}" for s in subtrees
                 if (pathlib.Path(src) / "private" / "1-raw-data" / s).is_dir()]
    try:
        PE.check_write_set(root, dirs=dirs, leaves=leaves, recursive=recursive,
                           glob_source=src, env=env)
        return None
    except PE.DestinationRefused as e:
        return e.reason


# ===========================================================================
# 5. THE SHARED TABLE. One list, two runners: a case cannot exist for one
# implementation only. Each builder returns the destination to hand both.
# ===========================================================================
class DestCase:
    def __init__(self, name, build, expect, why):
        self.name = name
        self.build = build          # (td, worktree_or_None) -> (dest, env|None)
        self.expect = expect        # None to accept, else a reason code
        self.why = why
        # Every builder takes (td, worktree_or_None); the ones that need a real
        # worktree name that second parameter `wt` and the ones that do not name
        # it `_wt`. Exact equality, not a substring test: a rename that silently
        # flipped a case to "no worktree needed" would hand the builder None and
        # test nothing.
        self.needs_worktree = build.__code__.co_varnames[1] == "wt"


def _plain_dir(td, _wt=None):
    d = pathlib.Path(td) / "plain"
    d.mkdir()
    return d, None


def _missing_dir(td, _wt=None):
    return pathlib.Path(td) / "never-created", None


def _unrelated_repo(td, _wt=None):
    d = pathlib.Path(td) / "other-repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], capture_output=True, check=True)
    return d, None


def _gitfile_forgery(td, _wt=None):
    """A plain directory whose one-line .git gitfile answers --git-common-dir
    and --show-toplevel exactly like a real worktree. Reachable by accident: a
    directory rsynced or restored from a backup of a linked worktree carries
    that file with it."""
    d = pathlib.Path(td) / "forged"
    d.mkdir()
    common = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True).stdout.strip()
    (d / ".git").write_text(f"gitdir: {common}\n")
    return d, None


def _env_forged_plain_dir(td, _wt=None):
    """The environment, not the disk: GIT_COMMON_DIR alone makes any directory
    answer the identity probe with this checkout's own common dir."""
    d = pathlib.Path(td) / "env-forged"
    d.mkdir()
    common = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True).stdout.strip()
    env = dict(os.environ)
    env["GIT_COMMON_DIR"] = common
    env["GIT_DIR"] = common
    env["GIT_WORK_TREE"] = str(d)
    return d, env


def _registered_worktree(td, wt):
    return wt, None


def _worktree_subdirectory(td, wt):
    sub = wt / "analysis"
    return sub, None


def _worktree_not_ignoring_private(td, wt):
    gi = wt / ".gitignore"
    kept = [ln for ln in gi.read_text().splitlines()
            if ln.strip() not in ("private/", "*/private/")]
    gi.write_text("\n".join(kept) + "\n")
    return wt, None


def _worktree_tracking_a_staged_path(td, wt):
    (wt / "private" / "household.yaml").write_text("household: {}\n")
    subprocess.run(["git", "-C", str(wt), "add", "-f", "private/household.yaml"],
                   capture_output=True, check=True)
    return wt, None


def _symlink_at_raw_data(td, wt):
    outside = pathlib.Path(td) / "outside"
    outside.mkdir()
    (wt / "private" / "1-raw-data").symlink_to(outside, target_is_directory=True)
    return wt, None


def _symlink_at_private(td, wt):
    """The link is at private/ itself -- one level ABOVE anything the script
    names, so an lstat of the paths it writes never sees it.

    The only fixture here that has to REMOVE anything (a link cannot be planted
    over an existing directory), so it is fenced: the directory must be the
    committed placeholder inside the throwaway worktree this case just created,
    and only regular files directly inside it are unlinked. CLAUDE.md sec.4 is
    about exactly this shape -- a cleanup step invented inside a fixture that
    turned out to be pointed at something real.
    """
    outside = pathlib.Path(td) / "outside-private"
    (outside / "1-raw-data").mkdir(parents=True)
    (outside / "verify").mkdir()
    real = wt / "private"
    assert pathlib.Path(td) in real.parents, f"refusing to touch {real}: outside the fixture"
    assert real.is_dir() and not real.is_symlink()
    contents = sorted(p.name for p in real.iterdir())
    assert contents == ["README.md"], (
        f"a fresh worktree's private/ should hold only the committed placeholder, "
        f"found {contents} -- refusing to delete anything else")
    (real / "README.md").unlink()
    real.rmdir()
    real.symlink_to(outside, target_is_directory=True)
    return wt, None


def _symlink_deep_inside_a_scanned_subtree(td, wt):
    outside = pathlib.Path(td) / "target.pdf"
    outside.write_text("not a statement\n")
    bills = wt / "private" / "1-raw-data" / "electric-bills"
    bills.mkdir(parents=True)
    (bills / "2026-01.pdf").symlink_to(outside)
    return wt, None


def _fifo_at_a_written_leaf(td, wt):
    verify = wt / "private" / "verify"
    verify.mkdir(parents=True)
    os.mkfifo(verify / "usage.csv")
    return wt, None


def _hard_link_at_a_written_leaf(td, wt):
    outside = pathlib.Path(td) / "elsewhere.yaml"
    outside.write_text("someone else's file\n")
    os.link(outside, wt / "private" / "household.yaml")
    return wt, None


def _regular_file_where_a_directory_belongs(td, wt):
    (wt / "private").mkdir(exist_ok=True)
    (wt / "private" / "1-raw-data").write_text("not a directory\n")
    return wt, None


TABLE = [
    DestCase("a registered worktree of this checkout", _registered_worktree, None,
             "the accepting half: a guard that refuses correct input is the shape "
             "that gets guards disabled"),
    DestCase("a destination that does not exist", _missing_dir, "no_such_destination",
             "the incident's own shape -- a failed `git worktree add` whose failure "
             "was swallowed by a pipe, followed by a write to the path anyway"),
    DestCase("a plain directory in no repository", _plain_dir, "not_a_worktree", ""),
    DestCase("a different repository", _unrelated_repo, "different_repository",
             "what the archive was actually written into on 2026-08-13"),
    DestCase("a plain directory with a forged .git gitfile", _gitfile_forgery,
             "not_registered",
             "answers --git-common-dir and --show-toplevel like a real worktree; "
             "only git's own register tells them apart"),
    DestCase("a plain directory dressed up by the environment", _env_forged_plain_dir,
             "not_a_worktree",
             "GIT_COMMON_DIR/GIT_DIR/GIT_WORK_TREE set: both implementations must "
             "clear them before probing, or the caller supplies the answer"),
    DestCase("a subdirectory of a legitimate worktree", _worktree_subdirectory,
             "not_worktree_root", ""),
    DestCase("a worktree that does not gitignore private/",
             _worktree_not_ignoring_private, "not_ignored",
             "the half of the incident a repository check cannot see: the data sat "
             "one `git add -A` from a commit into an unrelated public repo"),
    DestCase("a worktree that already tracks a staged path",
             _worktree_tracking_a_staged_path, "tracked_path",
             "a tracked file stays in the index whatever .gitignore says"),
    DestCase("a symlink at private/1-raw-data", _symlink_at_raw_data,
             "symlink_component", ""),
    DestCase("a symlink at private/ itself", _symlink_at_private, "ignore_unanswerable",
             "one level above every path either implementation names. Both refuse, "
             "and both refuse for the same measured reason rather than the obvious "
             "one: `git check-ignore private/1-raw-data` in a tree where private/ "
             "is a link exits 128 ('beyond a symbolic link'), and the ignore "
             "question is asked before any path is inspected -- in both "
             "implementations, which is why the phase order in check_write_set() "
             "matches the script's. Fail-closed either way; the case is here so "
             "the shared answer is a recorded decision and not a coincidence"),
    DestCase("a symlink deep inside a scanned subtree",
             _symlink_deep_inside_a_scanned_subtree, "symlink_under", ""),
    DestCase("a FIFO at a file the copies write", _fifo_at_a_written_leaf,
             "special_file",
             "neither a link nor a multiply-linked regular file; opening it blocks "
             "the run or hands the archive to whatever is reading"),
    DestCase("a hard link at a file the copies write", _hard_link_at_a_written_leaf,
             "hard_link",
             "`[ -L ]` cannot see it and cp rewrites the shared inode in place"),
    DestCase("a regular file where a staged directory belongs",
             _regular_file_where_a_directory_belongs, "not_a_directory", ""),
]

# Anti-narrowing for the table, same shape as MOVERS_FLOOR: every refusal
# #184 added stays exercised, and dropping one is a two-place edit.
TABLE_FLOOR = frozenset({
    "no_such_destination", "not_a_worktree", "different_repository", "not_registered",
    "not_worktree_root", "not_ignored", "tracked_path", "symlink_component",
    "symlink_under", "special_file", "hard_link", "not_a_directory",
})


# ===========================================================================
# CASES -- the census
# ===========================================================================
@case
def case_every_discovered_mover_is_registered():
    """The half that makes the set enforced rather than declared: a new copy of
    raw private data fails the build until somebody classifies it."""
    found = discover()
    missing = sorted(k for k in found if k not in MOVERS)
    assert not missing, (
        "these copy raw private data and are in no registry entry -- add each to "
        "MOVERS with the test case that proves its guard, or with the reason it "
        f"needs none: {[f'{f}::{s}' for f, s in missing]}")
    return f"all {len(found)} discovered movers are classified in MOVERS"


@case
def case_every_registered_mover_still_exists():
    """The other direction. A registry entry for code that has been deleted or
    renamed is worse than no entry: it reads as coverage."""
    found = discover()
    stale, absent = [], []
    for (rel, symbol), m in sorted(MOVERS.items()):
        if m.kind == "discovered":
            if (rel, symbol) not in found:
                stale.append(f"{rel}::{symbol}")
            continue
        path = ROOT / rel
        if not path.is_file():
            absent.append(f"{rel} (file is gone)")
        elif symbol != "<script>" and not _defines(path, symbol):
            absent.append(f"{rel}::{symbol} (symbol is gone)")
    assert not stale, (
        "registered as discoverable but the scan no longer finds them -- either the "
        f"mover is gone (delete the entry AND its MOVERS_FLOOR member) or the "
        f"discovery pass has been narrowed: {stale}")
    assert not absent, f"declared movers whose code no longer exists: {absent}"
    return (f"all {len(MOVERS)} registry entries still describe live code "
            f"({sum(1 for m in MOVERS.values() if m.kind == 'declared')} declared)")


def _defines(path, symbol):
    """Is `symbol` (dotted, class-qualified) defined in this file?"""
    tree = ast.parse(path.read_text())
    for _, qn, _ in _scopes(tree):
        if qn == symbol:
            return True
    return False


@case
def case_every_named_guard_case_exists_and_runs():
    """A named guard must be a real case that a real suite really runs.

    Checked structurally, not by trusting the string: the function must be
    defined in the named file AND registered to run there -- either by the
    @case decorator this repo uses or by membership in a CASES list literal. A
    guard that exists but is not wired in proves nothing.
    """
    checked = []
    for key, m in sorted(MOVERS.items()):
        if not m.guard:
            continue
        assert "::" in m.guard, f"{key}: guard {m.guard!r} is not 'file::case_name'"
        rel, name = m.guard.split("::", 1)
        path = ROOT / rel
        assert path.is_file(), f"{key}: guard file {rel} does not exist"
        tree = ast.parse(path.read_text())
        defined = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == name]
        assert defined, f"{key}: {rel} defines no case named {name}"
        decorated = any(isinstance(d, ast.Name) and d.id == "case"
                        for n in defined for d in n.decorator_list)
        in_list = any(isinstance(t, ast.Name) and t.id == "CASES"
                      for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      for t in node.targets
                      if isinstance(node.value, (ast.List, ast.Tuple))
                      and any(isinstance(e, ast.Name) and e.id == name
                              for e in node.value.elts))
        assert decorated or in_list, (
            f"{key}: {rel} defines {name} but never runs it -- a guard that is not "
            "wired into its suite proves nothing")
        checked.append(m.guard)
    assert checked, "no registry entry names a guard at all"
    return f"{len(checked)} named guard cases exist and are wired into their suites"


@case
def case_every_unguarded_mover_states_why():
    """Every discovered mover is classified, including the ones that need no
    guard. A registry that only admits guarded things gets the awkward entries
    quietly omitted and becomes aspirational."""
    bad = []
    for key, m in sorted(MOVERS.items()):
        if m.guard and m.unguarded:
            bad.append(f"{key}: names a guard AND a no-guard reason")
        if not m.guard and len(m.unguarded or "") < 80:
            bad.append(f"{key}: no guard and no real reason given")
        if m.kind == "declared" and len(m.invisible or "") < 20:
            bad.append(f"{key}: declared but does not say why discovery misses it")
        if len(m.moves) < 20 or len(m.destination) < 20:
            bad.append(f"{key}: 'moves'/'destination' must describe, not label")
    assert not bad, bad
    n = sum(1 for m in MOVERS.values() if not m.guard)
    return f"{len(MOVERS)} movers classified; {n} state in words why they have no guard"


@case
def case_the_registry_cannot_be_narrowed_by_one_edit():
    """_SEAM_VOCABULARY_FLOOR's shape: deleting a mover from MOVERS fails here
    until the floor is edited too, so shrinking the census is a deliberate
    two-place edit rather than one line quietly removed."""
    gone = sorted(f"{f}::{s}" for f, s in MOVERS_FLOOR - set(MOVERS))
    assert not gone, (
        f"these movers were dropped from MOVERS: {gone}. If the code really is "
        "gone, remove the MOVERS_FLOOR member in the same commit and say so in "
        "the message.")
    return f"the census floor holds all {len(MOVERS_FLOOR)} entries"


@case
def case_discovery_finds_an_unregistered_mover():
    """Behavioral, on a synthetic tree: the pass must FAIL on the defect it
    names. A discovery function that quietly found nothing would let every case
    above pass forever."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
        (root / "analysis").mkdir()
        (root / "analysis" / "planted.py").write_text(
            "import pathlib, shutil\n"
            "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"
            "def stage(dest):\n"
            "    archive = ROOT / 'private' / '1-raw-data'\n"
            "    shutil.copytree(archive, dest)\n")
        (root / "stage.sh").write_text("#!/bin/bash\ncp -R \"$1\"/private/1-raw-data \"$2\"\n")
        found = discover(root)
        assert ("analysis/planted.py", "stage") in found, (
            f"the AST pass missed a plain copytree of ROOT/private: {sorted(found)}")
        assert ("stage.sh", "<script>") in found, (
            f"the shell scan missed a plain `cp -R` of private/: {sorted(found)}")
        # ... and the same tree with the copy removed must come back clean, or
        # the pass is just matching the word "private".
        (root / "analysis" / "planted.py").write_text(
            "import pathlib\n"
            "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"
            "def stage(dest):\n"
            "    return (ROOT / 'private' / '1-raw-data').exists()\n")
        again = discover(root)
        assert ("analysis/planted.py", "stage") not in again, (
            "a function that only READS a private path was reported as a mover")
    return "a planted copy is discovered in both languages; a mere read is not"


@case
def case_discovery_reads_the_working_tree_not_only_the_index():
    """A mover added but not yet committed must fail the build. Checked by
    asking the file list itself, not by planting a file in this checkout: a
    suite that writes into analysis/ can leave one behind on a crash."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
        (root / "committed.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", str(root), "add", "committed.py"],
                       capture_output=True, check=True)
        (root / "uncommitted.py").write_text("y = 2\n")
        (root / ".gitignore").write_text("ignored.py\n")
        (root / "ignored.py").write_text("z = 3\n")
        files = source_files(root)
    assert "uncommitted.py" in files, "an untracked new file is invisible to discovery"
    assert "committed.py" in files
    assert "ignored.py" not in files, "a gitignored file must not be scanned"
    return "discovery scans tracked + untracked-not-ignored files, never ignored ones"


# ===========================================================================
# CASES -- the two implementations agree
# ===========================================================================
@case
def case_the_environment_this_module_clears_matches_the_shell_script():
    """Both implementations must blind themselves to the same variables. Parsed
    from the shell's own loop, because a variable added there and not here
    leaves the python predicate answering a question the caller controls."""
    text = SCRIPT.read_text()
    m = re.search(r"^for _v in (.*?); do$", text, re.M | re.S)
    assert m, "stage-private-data.sh: the sanitizing loop could not be found"
    shell = set(m.group(1).replace("\\\n", " ").split())
    prefix_form = "${!GIT_CONFIG*}"
    assert prefix_form in shell, (
        "the shell no longer clears the GIT_CONFIG* family by prefix")
    shell.discard(prefix_form)
    ours = set(PE.GIT_IDENTITY_VARS) | set(PE.PATH_MEANING_VARS)
    assert shell == ours, (
        f"the two sanitizers disagree -- only the shell clears {sorted(shell - ours)}, "
        f"only private_egress clears {sorted(ours - shell)}")
    env = PE.sanitized_env({"GIT_DIR": "/x", "GIT_CONFIG_KEY_0": "core.worktree",
                            "CDPATH": "/y", "GIT_EXEC_PATH": "/keep", "PATH": "/bin"})
    assert env == {"GIT_EXEC_PATH": "/keep", "PATH": "/bin"}, env
    return (f"both implementations clear the same {len(ours)} variables plus the "
            "GIT_CONFIG* family, and keep GIT_EXEC_PATH")


@case
def case_the_write_set_matches_the_shell_script():
    """The paths the python side checks are PARSED from the shell script, then
    compared with the constants above. A new `cp` line, a fourth managed path
    or a fourth scanned subtree fails here instead of silently leaving the
    agreement table testing a subset."""
    ignore, subtrees, leaves = _shell_write_set(SCRIPT.read_text())
    assert tuple(ignore) == DECLARED_IGNORE_PATHS, (
        f"stage-private-data.sh now requires {ignore} to be uncommittable; "
        f"DECLARED_IGNORE_PATHS still says {list(DECLARED_IGNORE_PATHS)}")
    assert tuple(subtrees) == DECLARED_SUBTREES, (
        f"the recursively-copied subtrees changed: {subtrees}")
    assert sorted(leaves) == sorted(DECLARED_LEAVES), (
        f"the files the copies write changed:\n  shell: {sorted(leaves)}\n"
        f"  declared: {sorted(DECLARED_LEAVES)}")
    return (f"{len(ignore)} managed paths, {len(subtrees)} scanned subtrees and "
            f"{len(leaves)} written leaves, all read out of stage-private-data.sh")


@case
def case_the_agreement_table_exercises_every_refusal_the_shell_adds():
    """Anti-narrowing for the table: deleting a case fails here."""
    covered = {c.expect for c in TABLE if c.expect}
    missing = sorted(TABLE_FLOOR - covered)
    assert not missing, (
        f"no table case exercises {missing} any more -- restore the case, or drop "
        "the reason from TABLE_FLOOR in the same commit and say why")
    unknown = sorted(covered - set(PE.REASONS))
    assert not unknown, f"table expects reasons private_egress does not define: {unknown}"
    assert any(c.expect is None for c in TABLE), (
        "the table has no accepting case -- a predicate that refuses everything "
        "would pass every refusal case here")
    return f"{len(TABLE)} table cases cover all {len(TABLE_FLOOR)} floor refusals"


@case
def case_the_shell_and_the_python_predicate_agree_on_every_destination():
    """The whole point: one table, two implementations, identical verdicts.

    Each case gets its own throwaway worktree, because the ACCEPTING case
    really does run the copies -- against a synthetic source holding no real
    data. Reading exit status directly, never through a pipe.
    """
    if not SCRIPT.is_file():
        raise SkipCase("stage-private-data.sh is not in this checkout")
    rows, bad = [], []
    for tc in TABLE:
        with tempfile.TemporaryDirectory() as td:
            src = _synthetic_src(td)
            ctx = _worktree(td) if tc.needs_worktree else contextlib.nullcontext(None)
            with ctx as wt:
                dest, env = tc.build(pathlib.Path(td), wt)
                shell = _shell_verdict(_run_shell(src, dest, cwd=td, env=env))
                saved = dict(os.environ)
                try:
                    if env is not None:
                        # The python side must sanitize the SAME environment,
                        # so it is really set in this process rather than
                        # handed over as a parameter it could ignore.
                        os.environ.update({k: v for k, v in env.items()
                                           if k not in saved or saved[k] != v})
                    py = _python_verdict(dest, src)
                finally:
                    os.environ.clear()
                    os.environ.update(saved)
        rows.append(f"  {tc.name:<52} shell={shell or 'ACCEPT':<22} "
                    f"python={py or 'ACCEPT'}")
        if shell != py:
            bad.append(f"{tc.name}: shell={shell!r} python={py!r}")
        elif shell != tc.expect:
            bad.append(f"{tc.name}: both said {shell!r}, the table expects {tc.expect!r}")
    print("\n".join(rows))
    assert not bad, "the two implementations disagree: " + "; ".join(bad)
    return f"shell and private_egress return identical verdicts on all {len(TABLE)} cases"


# ===========================================================================
# CASES -- the predicate on this checkout
# ===========================================================================
@case
def case_this_checkout_and_its_private_tree_are_accepted():
    """The accepting half, on the paths the argument-derived writers really
    use: generate_report.py's cache and manifest defaults."""
    for rel in ("private/report_cache", "private/report_generation_manifest.json",
                "private/llm_dry_run/20260816T000000-deadbeef.json"):
        d = PE.check_destination(ROOT / rel)
        assert d.relpath == rel, d
    return "the repo root's own private/ destinations are accepted"


@case
def case_a_committable_destination_is_refused():
    """The rule that is not about worktrees at all: inside the right checkout,
    at a path that checkout would happily commit."""
    assert PE.refusal(ROOT / "data" / "leak.json") == "not_ignored"
    assert PE.refusal(ROOT / "index.html") == "tracked_path"
    assert PE.refusal(ROOT / "private" / "README.md") == "tracked_path", (
        "the committed private/ placeholder is TRACKED -- check-ignore alone "
        "reports it 'not ignored', which would send the operator to edit a "
        ".gitignore that is already correct")
    return "a path this checkout could commit is refused, tracked before unignored"


@case
def case_a_path_outside_every_registered_worktree_is_refused():
    with tempfile.TemporaryDirectory() as td:
        assert PE.refusal(pathlib.Path(td) / "anywhere") == "not_a_worktree"
    assert PE.refusal("/") in ("not_a_worktree", "different_repository")
    return "a temp directory and / are both refused"


@case
def case_a_dotdot_component_is_refused_rather_than_resolved():
    """`<worktree>/private/../elsewhere` cannot be normalized without following
    symlinks, and a caller-supplied '..' is exactly the argument shape this
    predicate exists for."""
    assert PE.refusal(ROOT / "private" / ".." / "data") == "unnormalized_path"
    return "'..' is refused, never lexically normalized"


@case
def case_the_register_is_read_from_this_checkout_not_from_the_destination():
    """A forged .git gitfile answers every probe the destination is asked. The
    predicate must never ask it -- the register comes from this module's own
    common dir."""
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / "forged"
        d.mkdir()
        common = PE.self_common_git_dir()
        if not common:
            raise SkipCase("this checkout has no git common dir")
        (d / ".git").write_text(f"gitdir: {common}\n")
        assert PE.common_git_dir(str(d)) == common, (
            "the fixture is not a faithful forgery -- it must answer "
            "--git-common-dir exactly like a real worktree")
        assert PE.refusal(d / "private" / "x") == "not_registered"
    return "a directory that CLAIMS this checkout is refused; only the register admits one"


@case
def case_an_unreadable_register_refuses_rather_than_admits():
    """Fail closed. git always reports at least the main worktree, so an empty
    listing means the question went unanswered -- which must not read as 'no
    restrictions'."""
    assert PE.refusal(ROOT / "private" / "x", worktrees=[]) == "register_unavailable"
    return "an empty worktree register refuses every destination"


@case
def case_a_symlinked_component_below_the_worktree_root_is_refused():
    """Symlinks ABOVE the root are resolved and fine (on macOS /tmp is one);
    below it they are a route back out of the tree that just passed every
    check."""
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            outside = pathlib.Path(td) / "outside"
            outside.mkdir()
            (wt / "private" / "cache").symlink_to(outside, target_is_directory=True)
            assert PE.refusal(wt / "private" / "cache") == "symlink_component"
            assert PE.refusal(wt / "private" / "cache" / "deep" / "file.json") \
                == "symlink_component", "a link above the leaf must still be seen"
            # and the same worktree, without the link, is accepted
            assert PE.refusal(wt / "private" / "real_cache") is None
    return "a symlinked component at or below the worktree root is refused, at any depth"


@case
def case_a_special_file_and_a_hard_link_are_refused_at_a_leaf():
    """Neither is a symlink, so the link check cannot see either: a FIFO blocks
    the open (or hands the bytes to a reader), a second name on the inode makes
    a file elsewhere a copy of the data."""
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            priv = wt / "private"
            os.mkfifo(priv / "fifo.json")
            elsewhere = pathlib.Path(td) / "elsewhere.json"
            elsewhere.write_text("{}\n")
            os.link(elsewhere, priv / "hard.json")
            (priv / "plain.json").write_text("{}\n")
            for name, expect in (("fifo.json", "special_file"),
                                 ("hard.json", "hard_link"),
                                 ("plain.json", None)):
                got = None
                try:
                    p = priv / name
                    PE.check_destination(p)
                    PE._check_leaf(str(p), "file")
                except PE.DestinationRefused as e:
                    got = e.reason
                assert got == expect, f"{name}: expected {expect}, got {got}"
            assert stat.S_ISFIFO(os.lstat(priv / "fifo.json").st_mode)
    return "a FIFO and a hard-linked file are both refused where a plain file is not"


@case
def case_a_refusal_names_a_reason_the_module_defines():
    """Every raise carries a code from REASONS, so the agreement table can
    compare verdicts instead of prose. Checked by AST over the module, not by
    hoping every branch is exercised."""
    tree = ast.parse((ANALYSIS / "private_egress.py").read_text())
    # The REASONS dict's own keys do not count as uses of themselves.
    vocabulary = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", "") == "REASONS" for t in n.targets))
    in_dict = {id(n) for n in ast.walk(vocabulary)}

    raise_sites = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                and getattr(node.exc.func, "id", "") == "DestinationRefused":
            raise_sites += 1
            first = node.exc.args[0] if node.exc.args else None
            if isinstance(first, ast.Constant):
                assert first.value in PE.REASONS, (
                    f"line {node.lineno}: raises {first.value!r}, which REASONS does "
                    "not define")
            else:
                # The one legitimate indirection: _diagnose_outside() returns the
                # reason it picked. Its literals are covered by the scan below.
                assert isinstance(first, ast.Name), (
                    f"line {node.lineno}: the reason must be a literal or a name "
                    "bound to one, never a computed string -- the agreement table "
                    "compares codes, not prose")

    used = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value in PE.REASONS and id(n) not in in_dict}
    unused = sorted(set(PE.REASONS) - used)
    assert not unused, (
        f"REASONS defines codes the module never produces: {unused} -- a vocabulary "
        "entry that cannot happen is a claim about behaviour that does not exist")
    return (f"{raise_sites} raise sites, and all {len(PE.REASONS)} reasons are "
            "actually produced somewhere in the module")


def main():
    ran = skipped = failures = 0
    for c in CASES:
        try:
            msg = c()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {c.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {c.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
