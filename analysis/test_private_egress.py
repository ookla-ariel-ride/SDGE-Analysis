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
import inspect
import os
import pathlib
import re
import shutil
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
#   python   a copy call (shutil.copy/copy2/copyfile/copytree/copyfileobj/move,
#            os.link, os.symlink, tarfile extraction, shutil.unpack_archive) any
#            of whose arguments is a path ANCHORED AT THE REPO ROOT that names
#            private/, or a write call (write_text/write_bytes/mkdir) AT such a
#            path. Root-anchoring is what separates the real archive from the
#            many test fixtures built under a temp directory whose own path
#            contains the word "private".
#   python   an ORDINARY WRITE of a file at such a path, which is the way a
#            mover would most likely be written by somebody not thinking about
#            this registry at all: open(p, "wb") or Path.open("w") or
#            os.open(p, O_WRONLY) in any write-capable mode, os.write() to a
#            descriptor opened that way, and the rename/replace family
#            (os.replace, os.rename, os.renames, Path.replace, Path.rename,
#            Path.symlink_to, Path.hardlink_to) -- which is how an atomic write
#            lands, and how a `tmp` file written anywhere becomes the archive.
#            A read-mode open is NOT a mover: reading the archive is what the
#            whole pipeline does. The mode decides, and it is read out of the
#            slot that call shape really puts it in, never out of any string
#            that happens to be an argument. An open whose mode is a variable
#            counts as a write -- fail closed, like the SyntaxError branch.
#            For the rename family and for Path.open the RECEIVER counts as
#            well as the arguments: `tmp.replace(ROOT/"private"/x)` taints
#            through the argument, `(ROOT/"private"/x).replace(elsewhere)`
#            through the receiver, and both move the archive.
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
# The ordinary-write rules above were added the same way and measured the same
# way: on this tree they add ZERO sites -- no new movers and no false positives
# -- so what they buy is entirely prospective. That is the point. The census
# existed for two review rounds while recognising only a hard-coded set of copy
# calls, so a mover written the ordinary way (open(p, "wb"), copyfileobj, an
# atomic replace) would never have appeared in it and the census would have
# stayed green with an unguarded egress path shipping. A census with a blind
# spot is worse than no census, because it is what a reviewer trusts instead of
# looking. Every pattern claimed here has a planted positive control in
# case_discovery_finds_every_supported_write_pattern, which fails if the rule is
# removed -- "supported" is proved, not asserted.
#
# WHAT IT MISSES, stated rather than discovered later. Two of these were
# MEASURED and deliberately left open, with the numbers, because the rule that
# would close them costs more than it buys on this tree:
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
#   * CONTENT taint: private bytes read into a variable and written at a
#     destination that is not itself a private path --
#     `dest.write_bytes((ROOT/"private"/x).read_bytes())`. Every rule here
#     taints PATHS, never payloads. MEASURED: extending write_text/write_bytes
#     to fire on a tainted ARGUMENT rather than a tainted receiver finds exactly
#     one site in this tree, parse_bills.main writing
#     data/bill_corpus_boundary.json -- a de-identified artifact, which is the
#     product and not egress. One false positive, no true positives, so the
#     rule is not adopted. The same measurement on the serialiser family
#     (to_csv/to_json/json.dump with a tainted argument) finds four sites, all
#     four of them generators writing committed artifacts into data/. A rule
#     whose whole yield on the real tree is the thing the census explicitly
#     excludes would teach people to suppress it.
#   * a serialiser handed the private path directly: `df.to_csv(ROOT/"private"/
#     x)`, `np.save`, `fig.savefig`. Not in any call set above, and not addable
#     without the false positives just measured. Narrower than it looks:
#     `json.dump(obj, fh)` IS discovered, because the handle it needs comes from
#     a write-capable open of a private path and that open is a rule above. A
#     serialiser that opens the file itself is what stays invisible.
#   * a file written by neither python nor a shell script: a Makefile, a
#     CI workflow's `cp` step, an editor task, a git hook that is not a shell
#     script. discover_shell() reads .sh files and shebang-sh files only.
#   * a mover reached through a name this cannot resolve: getattr(shutil, name),
#     a dispatch table of functions, a subprocess argv built from a glob rather
#     than from the script's literal name.
#     ... and to the same rule in SHELL. SHELL_COPY now reads a command that is
#     not the first on its line (after `&&`, `;`, `|`, `then`, `do`, a subshell),
#     but it still matches a LITERAL command NAME in a position a command can
#     start. MEASURED against the regex, these four stay invisible:
#         CP=cp; $CP -R "$SRC/private" "$d"          the name is a variable
#         find "$SRC/private" -exec cp {} "$d" \;    the copy is an argument
#         ls "$SRC/private" | xargs cp -t "$d"       ... to another command
#         eval "cp -R $SRC/private $d"               the command is a string
#     A line CONTINUATION is not among them: the continued line begins with the
#     command, so `mkdir -p "$d" && \` + `cp -R ...` is found on the second line.
#     There is no AST for shell; resolving any of the four would be a shell
#     parser. The scan also keys on the FILE rather than the symbol, so a copy
#     inside a shell function is found as a hit on the script, which is all this
#     half of the census ever claims.
# ===========================================================================
COPY_CALLS = {"copy", "copy2", "copyfile", "copytree", "copyfileobj", "move",
              "unpack_archive", "extractall", "extract", "link", "symlink"}
WRITE_CALLS = {"write_text", "write_bytes", "mkdir"}
# The rename/replace family, matched on the RECEIVER as well as the arguments:
# os.replace(tmp, private) taints through an argument, tmp.replace(private)
# through both, (ROOT/"private"/x).rename(elsewhere) through the receiver.
MOVE_METHODS = {"replace", "rename", "renames", "symlink_to", "hardlink_to",
                "link_to"}
# Write-capable open modes. Checked against the mode STRING, whose meaning is
# the same for open() and Path.open(); os.open() is checked against its flags.
WRITE_MODE = re.compile(r"[wax+]")
OS_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC", "O_EXCL"}
TEMP_CALLS = {"mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"}
RUN_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
ROOT_NAMES = {"ROOT"}          # the repo-root constant every module here uses
ROOT_ATTRS = {"root"}          # ... and dry_run's Sandbox.root

# WHERE A COMMAND CAN START on a line of shell: at the beginning, or after a
# separator (`;` `&&` `||` `|` `&`), a subshell/group opener, or one of the
# keywords that introduce a command body (`then` `else` `do`). Anchoring at the
# start of the LINE only -- which is what this was -- made
#
#     mkdir -p "$d" && cp -R "$SRC/private" "$d"
#     if [ -d "$SRC/private" ]; then cp -R "$SRC/private" "$d"; fi
#     cd "$d" && rsync -a "$SRC/private/" .
#
# invisible to the census, so case_every_discovered_mover_is_registered stayed
# green with an unregistered mover shipping. These are ordinary shell, not exotic
# ones. No such line exists in this tree today, which is the same footing the
# python ordinary-write rules stand on: the value is prospective, and a census
# with a blind spot is worse than no census because it is what a reviewer trusts
# instead of looking. What is still missed is enumerated in WHAT IT MISSES above.
SHELL_CMD_START = r"(?:^|[;&|(){}]|\b(?:then|else|do)\s)"
SHELL_COPY = re.compile(
    SHELL_CMD_START + r"\s*(?:[\w.]+=\S*\s+)*(cp|rsync|install|ln|tar|scp|ditto)\s")


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
                    # POSITIONAL-ONLY PARAMETERS COUNT, and leaving them out did
                    # not merely miss them -- it MISALIGNED the whole pairing.
                    # ast puts one `defaults` list behind posonlyargs + args
                    # together, so for `def f(script=SCRIPT, /, *, dst=None)` the
                    # padding `len(args.args) - len(args.defaults)` went negative,
                    # collapsed to [], and the zip paired `dst` with SCRIPT's
                    # default: a false negative on the parameter that really
                    # carries the mover and a false positive on one that does not.
                    # Pad against the combined positional length, which is what
                    # `defaults` is really indexed from.
                    positional = list(args.posonlyargs) + list(args.args)
                    slots = positional + list(args.kwonlyargs)
                    defaults = ([None] * (len(positional) - len(args.defaults))
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


def _is_os(node):
    return isinstance(node, ast.Name) and node.id == "os"


def _mode_writes(node):
    """Can a file opened with this mode/flags argument be written through?

    None is a READ: both open() and Path.open() default to "r", and reading the
    archive is what this pipeline does all day. A literal is read literally. An
    os.open() flags expression is read for the O_* names it mentions. Anything
    else -- a mode held in a variable, an f-string, a conditional -- counts as a
    write: a rule that cannot see the mode must not answer "read", for the same
    reason discover_python() refuses to skip a file it cannot parse.
    """
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(WRITE_MODE.search(node.value))
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in OS_WRITE_FLAGS:
            return True
        if isinstance(sub, ast.Name) and sub.id in OS_WRITE_FLAGS:
            return True
    if any(isinstance(sub, ast.Attribute) and sub.attr == "O_RDONLY"
           for sub in ast.walk(node)):
        return False
    return True


def _write_open(node, tainted):
    """A write-capable open OF a repo-root private path -> the evidence line.

    The mode is taken from the slot the CALL SHAPE puts it in, never from "any
    string argument": open(file, mode) and os.open(path, flags) carry it
    second, Path.open(mode) carries it first and the path as the receiver. A
    looser reading would call open(p, "r", encoding="utf-8") a write on the
    strength of some unrelated argument.
    """
    f = node.func
    kw = {k.arg: k.value for k in node.keywords}
    pos = list(node.args)
    if isinstance(f, ast.Name) and f.id == "open":
        path = pos[0] if pos else kw.get("file")
        mode, label = (pos[1] if len(pos) > 1 else kw.get("mode")), "open()"
    elif isinstance(f, ast.Attribute) and _is_os(f.value) and f.attr in ("open", "fdopen"):
        path = pos[0] if pos else None
        slot = "flags" if f.attr == "open" else "mode"
        mode, label = (pos[1] if len(pos) > 1 else kw.get(slot)), f"os.{f.attr}()"
    elif isinstance(f, ast.Attribute) and f.attr == "open":
        path = f.value
        mode, label = (pos[0] if pos else kw.get("mode")), "Path.open()"
    else:
        return None
    if path is None or not _tainted(path, tainted) or not _mode_writes(mode):
        return None
    return f"{label} in a write mode AT a repo-root private path"


def _move_call(node, tainted):
    """A rename/replace/link of a repo-root private path -> the evidence line.

    Read by ARITY as well as by name, because `str.replace` shares a name with
    `Path.replace` and this pass has no types. os.replace(src, dst) and
    os.rename(src, dst) take two paths; every method form here --
    Path.replace/rename/symlink_to/hardlink_to -- takes exactly one positional
    path, and a two-positional `s.replace(old, new)` on a string built from a
    private path is a string edit, not a move. Widening the CALL SET must not
    widen the taint rule, and this is where it would have.
    """
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr not in MOVE_METHODS:
        return None
    if _is_os(f.value):
        if f.attr not in ("replace", "rename", "renames"):
            return None
    elif len(node.args) != 1:
        return None
    args = list(node.args) + [k.value for k in node.keywords]
    if not (_tainted(f.value, tainted) or any(_tainted(a, tainted) for a in args)):
        return None
    where = "os." if _is_os(f.value) else "Path."
    return f"{where}{f.attr}() of a repo-root private path"


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
                recv = node.func.value if isinstance(node.func, ast.Attribute) else None
                opened = _write_open(node, tainted)
                moved = _move_call(node, tainted)
                why = None
                if nm in COPY_CALLS and any(_tainted(a, tainted) for a in args):
                    why = f"{nm}() of a repo-root private path"
                elif (nm in WRITE_CALLS and isinstance(node.func, ast.Attribute)
                      and _tainted(node.func.value, tainted)):
                    why = f"{nm}() AT a repo-root private path"
                elif opened:
                    why = opened
                elif moved:
                    why = moved
                elif (nm == "write" and _is_os(recv) and args
                      and _tainted(args[0], tainted)):
                    why = "os.write() to a descriptor opened at a repo-root private path"
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


def _register_admin_dir():
    """`<common dir>/worktrees` -- the directory holding one ADMIN ENTRY per
    linked worktree of this checkout. None when there is no common dir."""
    common = PE.self_common_git_dir()
    return pathlib.Path(common) / "worktrees" if common else None


def _register_entry_names(admin):
    if admin is None:
        return set()
    try:
        return {p.name for p in admin.iterdir() if p.is_dir()}
    except OSError:
        return set()


@contextlib.contextmanager
def _register_entries_confined_to(td):
    """Hand back ONLY the register entries this block creates for worktrees
    inside `td`, and leave every other entry exactly as it was found.

    `git worktree prune` was here, and prune is not scoped: it removes EVERY
    entry whose directory is missing, not only the ones a fixture left behind.
    A developer with a worktree on an unmounted volume, a network share, or a
    directory mid-rebuild loses that registration by running this suite -- or
    check_coverage.sh, which runs it -- and has to `git worktree repair` or add
    it again. "Prune removes what these fixtures left" is true; "and all it
    removes" does not follow from it, and that was the argument the comment made.

    Git has no per-entry prune, so the scope is built here, and an entry is
    removed only when BOTH hold: it appeared while this block was running, and
    the `gitdir` file git wrote in it names a directory inside `td` -- the
    case's own TemporaryDirectory. An entry that fails either test, including
    every entry that predates the block, is left alone. `finally`, so a case
    that raises mid-way still hands its own entries back.
    """
    admin = _register_admin_dir()
    before = _register_entry_names(admin)
    fence = os.path.realpath(str(td))
    try:
        yield
    finally:
        for name in sorted(_register_entry_names(admin) - before):
            entry = admin / name
            try:
                # git writes '<worktree>/.git' here; its dirname is the worktree.
                owner = os.path.realpath(
                    os.path.dirname((entry / "gitdir").read_text().strip()))
            except OSError:
                continue        # not ours to read, so not ours to remove
            if owner == fence or owner.startswith(fence + os.sep):
                shutil.rmtree(entry, ignore_errors=True)


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
    from the shell script itself.

    env=None -- the ordinary case, where a forged environment is really SET in
    this process rather than handed over as a parameter the module could ignore
    -- goes through the public check_write_set(). A forged environment passed as
    a PARAMETER goes through the private _check_write_set(), because `env=` can
    manufacture the "ignored" verdict and therefore sits on no public signature
    (PUBLIC_PARAMS). The agreement case asks both ways and requires the same
    answer: a parameter that produced a different verdict from the real
    environment would make every env fixture in this table a test of the wrong
    thing.
    """
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
        if env is None:
            PE.check_write_set(root, dirs=dirs, leaves=leaves,
                               recursive=recursive, glob_source=src)
        else:
            PE._check_write_set(root, dirs=dirs, leaves=leaves,
                                recursive=recursive, glob_source=src, env=env)
        return None
    except PE.DestinationRefused as e:
        return e.reason


# ===========================================================================
# 5. THE SHARED TABLE. One list, two runners: a case cannot exist for one
# implementation only. Each builder returns the destination to hand both.
# ===========================================================================
class DestCase:
    """One destination fixture, asked of the shell AND of BOTH python entry
    points.

    The first version of this table ran the shell against check_write_set()
    only. Both defects found in review afterwards were in check_destination(),
    the OTHER public entry point -- the one the argument-derived writers call --
    and the table could not have caught either, because it never called it. So
    every row now carries a `probe`: the path (relative to the destination) and
    the destination KIND the single-path API is asked about, and its own
    expected verdict.

    The two python answers are allowed to differ, and three rows do differ, but
    never silently: a row whose `single` verdict is not its `expect` must say
    why in `asymmetry`, and a row that cannot be asked of the single-path API at
    all must say why in `single_na`. Enforced by
    case_every_table_case_is_asked_of_both_public_apis.
    """

    def __init__(self, name, build, expect, why, probe=None, single=None,
                 asymmetry=None, single_na=None):
        self.name = name
        self.build = build          # (td, worktree_or_None) -> (dest, env|None)
        self.expect = expect        # None to accept, else a reason code
        self.why = why
        self.probe = probe          # (relpath_under_dest, kind) or None
        self.single = single        # check_destination's verdict on that probe
        self.asymmetry = asymmetry  # required when single != expect
        self.single_na = single_na  # required when probe is None
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


def _directory_where_a_file_belongs(td, wt):
    """The reverse of the case above: a DIRECTORY sitting where one of the
    copied files goes. `cp` neither truncates it nor writes through it -- it
    writes INSIDE it, under a name the operator never asked for, and the file
    the run was supposed to produce is not there."""
    (wt / "private" / "household.yaml").mkdir(parents=True)
    return wt, None


TABLE = [
    DestCase("a registered worktree of this checkout", _registered_worktree, None,
             "the accepting half: a guard that refuses correct input is the shape "
             "that gets guards disabled",
             probe=("private/verify/usage.csv", "file"), single=None),
    DestCase("the same root, asked as an ordinary destination", _registered_worktree,
             None,
             "FINDING 1's fixture, and the row the old table had no column for. "
             "The shell's only question is 'stage the archive into this root', "
             "which this root answers yes to; an argument-derived writer asking "
             "'may I write files AT this path' must be told no, because the root "
             "is the one path in the tree that its own git does not ignore",
             probe=("", "dir"), single="worktree_root_itself",
             asymmetry="the same fixture, two different questions. kind='root' is "
                       "'stage into this worktree' and the ignore question is not "
                       "asked of a root; every other kind is 'write files at this "
                       "path', and at the root that is a path one `git add -A` "
                       "from a commit. Before the fix the ordinary question "
                       "returned ACCEPTED here"),
    DestCase("a destination that does not exist", _missing_dir, "no_such_destination",
             "the incident's own shape -- a failed `git worktree add` whose failure "
             "was swallowed by a pipe, followed by a write to the path anyway",
             probe=("", "dir"), single="not_a_worktree",
             asymmetry="a path that does not exist yet is ordinary for the "
                       "single-path API -- writers create their own directories -- "
                       "so it reports why the nearest EXISTING ancestor is "
                       "ineligible instead. Both refuse; only the root question "
                       "can say 'this is not an existing directory', because only "
                       "it requires the destination to already be a worktree"),
    DestCase("a plain directory in no repository", _plain_dir, "not_a_worktree", "",
             probe=("", "dir"), single="not_a_worktree"),
    DestCase("a different repository", _unrelated_repo, "different_repository",
             "what the archive was actually written into on 2026-08-13",
             probe=("", "dir"), single="different_repository"),
    DestCase("a plain directory with a forged .git gitfile", _gitfile_forgery,
             "not_registered",
             "answers --git-common-dir and --show-toplevel like a real worktree; "
             "only git's own register tells them apart",
             probe=("", "dir"), single="not_registered"),
    DestCase("a plain directory dressed up by the environment", _env_forged_plain_dir,
             "not_a_worktree",
             "GIT_COMMON_DIR/GIT_DIR/GIT_WORK_TREE set: both implementations must "
             "clear them before probing, or the caller supplies the answer",
             probe=("", "dir"), single="not_a_worktree"),
    DestCase("a subdirectory of a legitimate worktree", _worktree_subdirectory,
             "not_worktree_root", "",
             probe=("", "dir"), single="tracked_path",
             asymmetry="a subdirectory is the single-path API's ORDINARY input, so "
                       "it does not refuse for being one -- it asks the question "
                       "that actually matters about analysis/, which is that this "
                       "checkout tracks it. Both refuse; the codes differ because "
                       "the remedies do (the shell wants the worktree root, the "
                       "writer wants an ignored path)"),
    DestCase("a worktree that does not gitignore private/",
             _worktree_not_ignoring_private, "not_ignored",
             "the half of the incident a repository check cannot see: the data sat "
             "one `git add -A` from a commit into an unrelated public repo",
             probe=("private/verify/usage.csv", "file"), single="not_ignored"),
    DestCase("a worktree that already tracks a staged path",
             _worktree_tracking_a_staged_path, "tracked_path",
             "a tracked file stays in the index whatever .gitignore says",
             probe=("private/household.yaml", "file"), single="tracked_path"),
    DestCase("a symlink at private/1-raw-data", _symlink_at_raw_data,
             "symlink_component", "",
             probe=("private/1-raw-data/gas.csv", "file"), single="symlink_component"),
    DestCase("a symlink at private/ itself", _symlink_at_private, "ignore_unanswerable",
             "one level above every path either implementation names. Both refuse, "
             "and both refuse for the same measured reason rather than the obvious "
             "one: `git check-ignore private/1-raw-data` in a tree where private/ "
             "is a link exits 128 ('beyond a symbolic link'), and the ignore "
             "question is asked before any path is inspected -- in both "
             "implementations, which is why the phase order in check_write_set() "
             "matches the script's. Fail-closed either way; the case is here so "
             "the shared answer is a recorded decision and not a coincidence",
             probe=("private/verify/usage.csv", "file"), single="symlink_component",
             asymmetry="PHASE ORDER, and it is the whole of the difference. The "
                       "whole-set API settles 'could this tree commit any of what "
                       "I am about to write' for the DECLARED SET before inspecting "
                       "any path, and git answers that question 128 here; the "
                       "single-path API has one path, so it walks that path's "
                       "components first and sees the link itself. Both refuse, "
                       "neither writes"),
    DestCase("a symlink deep inside a scanned subtree",
             _symlink_deep_inside_a_scanned_subtree, "symlink_under", "",
             probe=("private/1-raw-data/electric-bills", "tree"), single="symlink_under"),
    DestCase("a FIFO at a file the copies write", _fifo_at_a_written_leaf,
             "special_file",
             "neither a link nor a multiply-linked regular file; opening it blocks "
             "the run or hands the archive to whatever is reading",
             probe=("private/verify/usage.csv", "file"), single="special_file"),
    DestCase("a hard link at a file the copies write", _hard_link_at_a_written_leaf,
             "hard_link",
             "`[ -L ]` cannot see it and cp rewrites the shared inode in place",
             probe=("private/household.yaml", "file"), single="hard_link"),
    DestCase("a regular file where a staged directory belongs",
             _regular_file_where_a_directory_belongs, "not_a_directory", "",
             probe=("private/1-raw-data", "dir"), single="not_a_directory"),
    DestCase("a directory where a staged file belongs", _directory_where_a_file_belongs,
             "special_file",
             "the reverse mismatch: `cp` writes INSIDE it rather than over it, so "
             "the run reports success and the file it was supposed to write is not "
             "there",
             probe=("private/household.yaml", "file"), single="special_file"),
]

# Anti-narrowing for the table, same shape as MOVERS_FLOOR: every refusal
# #184 added stays exercised, and dropping one is a two-place edit.
TABLE_FLOOR = frozenset({
    "no_such_destination", "not_a_worktree", "different_repository", "not_registered",
    "not_worktree_root", "not_ignored", "tracked_path", "symlink_component",
    "symlink_under", "special_file", "hard_link", "not_a_directory",
})

# The same floor for the OTHER public entry point. It is a separate constant
# because it is a separate claim: TABLE_FLOOR says the shell's refusals stay
# exercised, this says the single-path API reaches them too. The four leaf and
# tree refusals in it were reachable only through check_write_set() until the
# fix, which is exactly how the FIFO and hard-link holes survived a table that
# looked complete.
SINGLE_FLOOR = frozenset({
    "worktree_root_itself", "special_file", "hard_link", "not_a_directory",
    "symlink_under", "symlink_component", "tracked_path", "not_ignored",
    "not_a_worktree", "different_repository", "not_registered",
})

# ---------------------------------------------------------------------------
# WHICH REFUSAL IS REACHABLE THROUGH WHICH API
#
# check_write_set() raises nothing of its own any more: it asks
# check_destination() about the root with kind="root" and about every path below
# it with the kind that path will be written as, and asks _require_uncommittable
# for the declared set -- which check_destination also asks. That is asserted
# structurally by case_check_write_set_owns_no_refusal_of_its_own, and it is
# what makes "reachable through one API and not the other" a question about the
# KIND argument rather than about the two functions.
#
# So this maps each reason to the kinds that can produce it, with the reason for
# every restriction. Everything unrestricted is reachable through both public
# entry points; everything restricted is restricted by the destination kind the
# caller declared, which is the point of making the kind mandatory.
# ---------------------------------------------------------------------------
ALL_KINDS = frozenset(PE.KINDS)
API_REACH = {
    "self_unlocatable":     (ALL_KINDS, ""),
    "register_unavailable": (ALL_KINDS, ""),
    "unnormalized_path":    (ALL_KINDS, ""),
    "no_such_destination":  (ALL_KINDS,
                             "kind='root' produces it for a destination that is not "
                             "an existing directory; the other kinds produce it only "
                             "for a path whose nearest existing ancestor is not a "
                             "directory, because a path that does not exist YET is "
                             "what a writer is about to create"),
    "not_a_worktree":       (ALL_KINDS, ""),
    "different_repository": (ALL_KINDS, ""),
    "no_worktree_of_its_own": (ALL_KINDS, ""),
    "not_registered":       (ALL_KINDS, ""),
    "not_worktree_root":    (frozenset({"root"}),
                             "only the root question can be answered 'this is a "
                             "subdirectory'; for every other kind a subdirectory is "
                             "the ordinary input"),
    "worktree_root_itself": (ALL_KINDS - {"root"},
                             "the mirror image: only an ordinary write can be "
                             "refused for landing ON a root. kind='root' is asking "
                             "to stage INTO one"),
    "symlink_component":    (ALL_KINDS - {"root"},
                             "the walk runs from the worktree root DOWN, and a "
                             "kind='root' destination has nothing below it to walk. "
                             "A link that NAMES a registered worktree is a "
                             "legitimate way to say where it is -- the shell "
                             "resolves DST to DST_REAL for the same reason"),
    "not_a_directory":      (ALL_KINDS - {"root"},
                             "a claim about a DIRECTORY SLOT, and there are two: the "
                             "leaf when the caller says it will write a directory "
                             "there ('dir'/'tree'), and every existing component above "
                             "the last, which the caller's own mkdir must walk through "
                             "whatever the leaf is -- so a 'file' destination reaches "
                             "it too. kind='root' has no components below it to walk, "
                             "and has already been required to be a directory"),
    "special_file":         (frozenset({"file"}),
                             "a FIFO or a directory found where a regular file goes; "
                             "asked of a directory slot the same path is "
                             "not_a_directory"),
    "hard_link":            (frozenset({"file"}), "only a regular file has a link count "
                                                  "a copy would write through"),
    "symlink_under":        (frozenset({"tree"}), "only a recursive copy descends"),
    "special_under":        (frozenset({"tree"}), "only a recursive copy descends"),
    "hardlink_under":       (frozenset({"tree"}), "only a recursive copy descends"),
    "scan_unreadable":      (frozenset({"tree"}), "only a recursive copy descends"),
    "tracked_path":         (ALL_KINDS - {"root"},
                             "no checkout ignores its own root, so the question is "
                             "not asked of one"),
    "not_ignored":          (ALL_KINDS - {"root"},
                             "as tracked_path: the ignore question is asked of a "
                             "path INSIDE a worktree, never of the worktree"),
    "ignore_unanswerable":  (ALL_KINDS - {"root"},
                             "as tracked_path; check_write_set() also reaches it for "
                             "the declared set, which is how a link at private/ is "
                             "refused there while the single-path walk sees the link"),
    "tracked_unanswerable": (ALL_KINDS - {"root"},
                             "as tracked_path: 'git ls-files' is asked about a "
                             "relative path, and a root has none"),
}

# The refusals no destination KIND can produce, because they are not about a
# destination path at all: they are about the whole-set API's own input, the leaf
# PATTERNS and the tree they are expanded against. check_destination() takes no
# pattern and no source, so a (kind, reason) row for either would be a fiction.
#
# This is a separate table rather than an API_REACH entry with an invented kind
# set, because the invariant below stays total either way -- every reason is
# classified in exactly one of the two -- while the classification stays true.
# Each entry says why no kind reaches it AND names the case that produces it, so
# "unreachable" cannot be used to park a refusal nothing exercises.
WRITE_SET_ONLY_REASONS = {
    "glob_source_unlistable":
        "raised while EXPANDING a leaf pattern, before any destination path "
        "exists to ask a kind question about. check_destination() has no "
        "glob_source, so no kind can produce it -- proved by "
        "case_a_leaf_pattern_whose_source_cannot_be_listed_is_refused",
    "pattern_names_a_directory":
        "the same seam: a pattern where a directory is named picks out no "
        "definite path, so there is nothing to ask a kind about. Proved by "
        "case_a_pattern_where_a_directory_is_named_is_refused",
}

# (reason) codes this run really produced through check_write_set()'s expansion,
# filled by the cases below. The reach case asserts every WRITE_SET_ONLY_REASONS
# entry is in here, so a reason declared unreachable-by-kind must still be
# reachable by SOMETHING.
WRITE_SET_OBSERVED = set()

# (kind, reason) pairs this run actually observed through check_destination.
# Filled by the cases below and checked against API_REACH, so the map above is
# measured where the suite reaches and declared only where it does not.
OBSERVED = set()


def _single_verdict(path, kind):
    """check_destination()'s verdict on one path, as a reason code or None."""
    try:
        PE.check_destination(path, kind=kind)
        return None
    except PE.DestinationRefused as e:
        OBSERVED.add((kind, e.reason))
        return e.reason


def _private_verdict(path, *, kind, require_ignored=True, worktrees=None, env=None):
    """The same verdict through the PRIVATE _check_destination().

    Every parameter that can weaken a check lives there and on no public
    signature (PUBLIC_PARAMS below), so a case that needs one -- an empty
    register, a forged environment, the committability exemption -- has to be an
    in-module caller rather than an ordinary one, exactly like check_write_set().
    Kept as one helper so the cases below cannot each invent their own way past
    the public door.
    """
    try:
        PE._check_destination(path, kind=kind, require_ignored=require_ignored,
                              worktrees=worktrees, env=env)
        return None
    except PE.DestinationRefused as e:
        OBSERVED.add((kind, e.reason))
        return e.reason


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


# One planted control per write pattern the discovery pass claims to support,
# and one per shape it must keep OUT. Each positive is a mover written the way
# somebody who never read this registry would write it; each negative is a
# shape the widened call set could have swallowed. Written as data so that
# "supported" is a row here rather than a sentence in a comment: deleting a
# rule from discover_python() fails on its own row, naming the pattern.
#
# Every body is planted at ROOT/private/..., because ROOT-ANCHORING IS THE
# TAINT RULE and widening the call set must not widen it. The third negative is
# the same builtin open() as the first positive, at a path that merely contains
# the word "private" -- the fixture shape this whole tree is full of.
PLANTED_WRITE_PATTERNS = (
    ("builtin_open_wb", "open() in a write mode",
     '    with open(ROOT / "private" / "cache.json", "wb") as fh:\n'
     "        fh.write(payload)\n"),
    ("path_open_w", "Path.open() in a write mode",
     '    fh = (ROOT / "private" / "cache.json").open("w")\n'
     "    fh.close()\n"),
    ("open_with_the_mode_in_a_variable", "open() in a write mode",
     '    fh = open(ROOT / "private" / "cache.json", mode)\n'
     "    fh.close()\n"),
    ("os_open_then_os_write", "os.write()",
     '    fd = os.open(str(ROOT / "private" / "cache.json"),\n'
     "                 os.O_WRONLY | os.O_CREAT)\n"
     "    os.write(fd, payload)\n"
     "    os.close(fd)\n"),
    # The descriptor, not the open: this one is opened READ-ONLY, so the
    # os.open() rule does not fire and only the os.write() rule can see it.
    # Without it, removing the os.write() rule would still leave the row above
    # discovered by its own os.open(), and the control would prove nothing.
    ("os_write_to_a_read_opened_descriptor", "os.write()",
     '    fd = os.open(str(ROOT / "private" / "cache.json"), os.O_RDONLY)\n'
     "    os.write(fd, payload)\n"
     "    os.close(fd)\n"),
    ("copyfileobj_from_the_archive", "copyfileobj()",
     '    with open(ROOT / "private" / "1-raw-data" / "usage.csv", "rb") as src, \\\n'
     '            open(dest, "wb") as dst:\n'
     "        shutil.copyfileobj(src, dst)\n"),
    ("atomic_replace_onto_the_archive", "os.replace()",
     '    tmp = pathlib.Path(str(dest) + ".tmp")\n'
     "    tmp.write_bytes(payload)\n"
     '    os.replace(tmp, ROOT / "private" / "cache.json")\n'),
    ("path_replace_of_the_archive", "Path.replace()",
     '    (ROOT / "private" / "cache.json").replace(dest)\n'),
    ("os_rename_of_the_archive", "os.rename()",
     '    os.rename(ROOT / "private" / "cache.json", dest)\n'),
    ("symlink_to_the_archive", "Path.symlink_to()",
     '    dest.symlink_to(ROOT / "private" / "1-raw-data")\n'),
)

PLANTED_NON_MOVERS = (
    ("reads_the_archive_in_binary",
     '    with open(ROOT / "private" / "1-raw-data" / "usage.csv", "rb") as fh:\n'
     "        return fh.read()\n"),
    ("reads_the_archive_with_path_open",
     '    return (ROOT / "private" / "cache.json").open().read()\n'),
    ("writes_a_fixture_under_a_temp_path_containing_private",
     '    p = os.path.join(tmpdir, "private", "usage.csv")\n'
     '    with open(p, "wb") as fh:\n'
     "        fh.write(payload)\n"),
    ("edits_a_string_built_from_an_archive_path",
     '    return str(ROOT / "private" / "cache.json").replace("private", "public")\n'),
)


@case
def case_discovery_finds_every_supported_write_pattern():
    """A planted positive control for EVERY pattern this pass claims to read,
    and a negative for every shape it must not read.

    The census recognised a hard-coded set of copy calls for two review rounds.
    A mover written the ordinary way -- open(p, "wb"), copyfileobj, an atomic
    os.replace -- would never have appeared in MOVERS, and every case above
    would have gone on passing while an unguarded egress path shipped. The
    single planted control that existed exercised shutil.copytree only, so the
    hole was invisible to the suite that was supposed to prove there was none.

    Each row is planted in its own function so a failure names the pattern that
    broke rather than "discovery found fewer things".
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
        (root / "analysis").mkdir()
        body = ["import os\nimport pathlib\nimport shutil\n",
                "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"]
        for name, _, code in PLANTED_WRITE_PATTERNS:
            body.append(f"\n\ndef {name}(dest, payload, mode, tmpdir):\n{code}")
        for name, code in PLANTED_NON_MOVERS:
            body.append(f"\n\ndef {name}(dest, payload, mode, tmpdir):\n{code}")
        (root / "analysis" / "planted.py").write_text("".join(body))
        found = discover(root)

    missed, mislabelled, spurious = [], [], []
    for name, evidence, _ in PLANTED_WRITE_PATTERNS:
        hits = found.get(("analysis/planted.py", name))
        if not hits:
            missed.append(name)
        elif not any(evidence in h for h in hits):
            mislabelled.append(f"{name}: expected {evidence!r}, got {hits}")
    for name, _ in PLANTED_NON_MOVERS:
        if ("analysis/planted.py", name) in found:
            spurious.append(f"{name}: {found[('analysis/planted.py', name)]}")
    assert not missed, (
        "discovery does not see these ordinary ways to write a file, so a mover "
        f"written that way would never reach MOVERS: {missed}")
    assert not mislabelled, (
        "discovered, but by a rule that does not describe what it found -- the "
        f"evidence line is what a reviewer reads: {mislabelled}")
    assert not spurious, (
        "discovery reported a non-mover. Widening the CALL set must not widen "
        "the TAINT rule: a read is not a move, a two-argument str.replace is not "
        f"a rename, and a fixture under a temp path is not the archive: {spurious}")
    return (f"all {len(PLANTED_WRITE_PATTERNS)} supported write patterns are "
            f"discovered on planted controls, and all {len(PLANTED_NON_MOVERS)} "
            "near-miss shapes are not")


# The shell half of the same idea, and the same reason for it: SHELL_COPY was
# anchored at the start of a LINE, so a copy that is not the first command on
# its own line was invisible. Each positive is an ordinary way to write the
# stage-private-data.sh copy that would have shipped unregistered; each negative
# is a shape the widened anchor could have swallowed.
PLANTED_SHELL_COPIES = (
    ("a copy after &&", 'mkdir -p "$d" && cp -R "$SRC/private" "$d"'),
    ("a copy after `then` on a one-line if",
     'if [ -d "$SRC/private" ]; then cp -R "$SRC/private" "$d"; fi'),
    ("an rsync after cd &&", 'cd "$d" && rsync -a "$SRC/private/" .'),
    ("a copy after a semicolon", 'echo staging; cp "$SRC/private/household.yaml" "$d"'),
    ("a copy after `do` in a loop body",
     'for f in "$SRC"/private/*; do cp "$f" "$d"; done'),
    ("a copy after a pipeline", 'printf x | cat; scp "$SRC/private/gas.csv" "$h:/t"'),
    ("a copy inside a subshell", '(cd "$SRC/private" && tar cf - .) > "$d/a.tar"'),
    ("an env-prefixed copy after a separator",
     'true && LC_ALL=C cp "$SRC/private/gas.csv" "$d"'),
    ("a copy after ||", 'test -e "$d/private" || cp -R "$SRC/private" "$d"'),
)

PLANTED_SHELL_NON_COPIES = (
    ("a command name that is only the tail of a word",
     'echo "$SRC/private" | grep -c scp foo'),
    ("a copy named only in prose", 'echo "this does not cp anything from private/"'),
    ("a flag whose value contains a command name",
     'helper --install-prefix=/opt "$SRC/private"'),
    ("a command whose name merely starts with one",
     'cpio -o < "$SRC/private/list" > "$d/a.cpio"'),
)


@case
def case_discovery_finds_a_shell_copy_that_is_not_first_on_its_line():
    """FINDING: SHELL_COPY matched only at the START of a line.

        mkdir -p "$d" && cp -R "$SRC/private" "$d"

    is an ordinary way to write the copy this whole census exists for, and the
    scan could not see it -- so case_every_discovered_mover_is_registered stayed
    green while an unregistered mover shipped. Prospective, like the python
    ordinary-write rules: no line in this tree has this shape today.

    One planted script per form, because discover_shell() keys on the FILE: all
    nine in one script would pass on any single match and prove nothing about
    the other eight.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
        planted = {}
        for i, (label, line) in enumerate(PLANTED_SHELL_COPIES + PLANTED_SHELL_NON_COPIES):
            rel = f"planted-{i:02d}.sh"
            (root / rel).write_text(f'#!/bin/bash\nSRC="$1"; d="$2"\n{line}\n')
            planted[label] = rel
        found = discover_shell(root, files=sorted(planted.values()))

    missed = [f"{label}: {planted[label]}" for label, _ in PLANTED_SHELL_COPIES
              if (planted[label], "<script>") not in found]
    spurious = [f"{label}: {found[(planted[label], '<script>')]}"
                for label, _ in PLANTED_SHELL_NON_COPIES
                if (planted[label], "<script>") in found]
    assert not missed, (
        "the shell scan cannot see a copy that is not the first command on its "
        f"line, so a mover written that way would never reach MOVERS: {missed}")
    assert not spurious, (
        "widening WHERE a command may start must not widen WHAT counts as one: "
        f"{spurious}")
    return (f"all {len(PLANTED_SHELL_COPIES)} compound-command copy forms are "
            f"discovered, and all {len(PLANTED_SHELL_NON_COPIES)} near-miss "
            "shapes are not")


@case
def case_the_taint_pass_reads_a_positional_only_parameter():
    """FINDING: `slots` left out `posonlyargs`, and ast indexes `args.defaults`
    from `posonlyargs + args` together.

    For `def f(script=SCRIPT, /, *, dst=None)` the padding
    `len(args.args) - len(args.defaults)` was -1, so the padding collapsed to []
    and the zip paired `dst` with SCRIPT's default: SCRIPT's taint landed on the
    wrong name. Both halves are asserted, because the misalignment is worse than
    the omission -- a false negative on the parameter that carries the mover AND
    a false positive on one that does not.
    """
    src = ('SCRIPT = "stage-private-data.sh"\n'
           'def f(script=SCRIPT, /, *, dst=None):\n'
           '    return script, dst\n')

    def seed(node):
        return any(isinstance(s, ast.Constant) and s.value == "stage-private-data.sh"
                   for s in ast.walk(node))

    names = {qn: nm for _, qn, nm in _scopes(ast.parse(src), seed, params=True)}
    assert "script" in names["f"], (
        "the positional-only parameter carrying the mover's default is not "
        f"tainted: {sorted(names['f'])}")
    assert "dst" not in names["f"], (
        "a keyword-only parameter with an inert default was tainted -- the "
        f"defaults are being paired with the wrong slots: {sorted(names['f'])}")

    # ... and behaviorally, through the discovery pass: a mover whose archive
    # path arrives as a positional-only DEFAULT and is used nowhere else.
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)
        (root / "analysis").mkdir()
        (root / "analysis" / "planted.py").write_text(
            "import pathlib, shutil\n"
            "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"
            "def stage(archive=ROOT / 'private' / '1-raw-data', /, *, quiet=False):\n"
            "    shutil.copytree(archive, '/tmp/somewhere')\n")
        found = discover(root)
    assert ("analysis/planted.py", "stage") in found, (
        "a copytree of a repo-root private path reached through a positional-only "
        f"parameter default is invisible to discovery: {sorted(found)}")
    return ("a positional-only parameter's default taints the parameter it "
            "belongs to, and not its neighbour")


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
def case_every_table_case_is_asked_of_both_public_apis():
    """The hole the first version of this table had: every case ran through
    check_write_set() and none through check_destination(), so both defects
    review found sat in the API the table never called.

    Structural, and it runs before the table itself: a row may only skip the
    single-path API with a written reason, and a row whose two python verdicts
    differ must say why. Neither can be omitted silently.
    """
    bad = []
    for c in TABLE:
        if c.probe is None:
            if len(c.single_na or "") < 40:
                bad.append(f"{c.name}: no single-path probe and no reason given")
            continue
        if c.single_na:
            bad.append(f"{c.name}: has a probe AND a reason for having none")
        rel, kind = c.probe
        if kind not in PE.KINDS:
            bad.append(f"{c.name}: probe kind {kind!r} is not one of {PE.KINDS}")
        if c.single is not None and c.single not in PE.REASONS:
            bad.append(f"{c.name}: expects {c.single!r}, which REASONS does not define")
        if c.single != c.expect and len(c.asymmetry or "") < 40:
            bad.append(f"{c.name}: shell says {c.expect!r} and check_destination "
                       f"says {c.single!r} with no asymmetry stated")
        if c.single == c.expect and c.asymmetry:
            bad.append(f"{c.name}: states an asymmetry but both APIs agree")
    assert not bad, bad
    covered = {c.single for c in TABLE if c.single}
    missing = sorted(SINGLE_FLOOR - covered)
    assert not missing, (
        f"no table case reaches {missing} through check_destination() any more -- "
        "restore the case, or drop the reason from SINGLE_FLOOR in the same commit")
    assert any(c.single is None and c.probe for c in TABLE), (
        "no row expects the single-path API to ACCEPT anything")
    differ = [c.name for c in TABLE if c.single != c.expect]
    return (f"all {len(TABLE)} rows are asked of both public APIs, "
            f"{len(differ)} with a stated asymmetry, covering all "
            f"{len(SINGLE_FLOOR)} single-path floor refusals")


@case
def case_the_shell_and_the_python_predicate_agree_on_every_destination():
    """The whole point: one table, THREE runners, and no case that passes for
    one API while failing for another.

    stage-private-data.sh, check_write_set() and check_destination() are each
    asked about the same fixture. The first two must return identical verdicts;
    the third answers about the row's own probe path, and must return the
    verdict the row declares -- which for three rows is a different code, for
    the reason written into the row.

    Each case gets its own throwaway worktree, because the ACCEPTING cases
    really do run the copies -- against a synthetic source holding no real
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
                    single = kind = None
                    if tc.probe is not None:
                        rel, kind = tc.probe
                        probe = pathlib.Path(dest) / rel if rel else pathlib.Path(dest)
                        single = _single_verdict(probe, kind)
                finally:
                    os.environ.clear()
                    os.environ.update(saved)
                # ... and the same forged environment handed over as a
                # PARAMETER -- through the private door that carries it, since
                # no public one does -- must reach the same verdict as really
                # having it in the process. The two are the same sanitizer or
                # every env fixture in this table proves nothing about the
                # parameter, which is the form an in-process caller uses.
                if env is not None:
                    by_param = _python_verdict(dest, src, env=env)
                    if by_param != py:
                        bad.append(f"{tc.name}: the forged environment gives "
                                   f"{py!r} when set in the process and "
                                   f"{by_param!r} when passed as env=")
        shown = (f"{kind}:{single or 'ACCEPT'}" if tc.probe is not None
                 else f"n/a -- {tc.single_na[:40]}")
        # The marker reports what the TABLE declares, not what this run
        # produced: a difference the table did not declare must show up as a
        # failure below, never as a printed "by design".
        differs = tc.probe is not None and tc.single != tc.expect
        rows.append(f"  {tc.name:<48} {shell or 'ACCEPT':<21} {py or 'ACCEPT':<21} "
                    f"{shown}" + ("   <- differs, by design" if differs else ""))
        if shell != py:
            bad.append(f"{tc.name}: shell={shell!r} check_write_set={py!r}")
        elif shell != tc.expect:
            bad.append(f"{tc.name}: both said {shell!r}, the table expects {tc.expect!r}")
        if tc.probe is not None and single != tc.single:
            bad.append(f"{tc.name}: check_destination(kind={kind!r}) said {single!r}, "
                       f"the table expects {tc.single!r}")
    print(f"  {'fixture':<48} {'stage-private-data.sh':<21} "
          f"{'check_write_set()':<21} check_destination(kind=..)")
    print("\n".join(rows))
    assert not bad, "the implementations disagree: " + "; ".join(bad)
    return (f"the shell and check_write_set() agree on all {len(TABLE)} cases, and "
            f"check_destination() returns the declared verdict on every one")


# ===========================================================================
# CASES -- the predicate on this checkout
# ===========================================================================
@case
def case_this_checkout_and_its_private_tree_are_accepted():
    """The accepting half, on the paths the argument-derived writers really
    use: generate_report.py's cache and manifest defaults, each asked with the
    kind that writer actually writes."""
    for rel, kind in (("private/report_cache", "dir"),
                      ("private/report_generation_manifest.json", "file"),
                      ("private/llm_dry_run/20260816T000000-deadbeef.json", "file")):
        d = PE.check_destination(ROOT / rel, kind=kind)
        assert d.relpath == rel, d
    return "the repo root's own private/ destinations are accepted"


@case
def case_a_committable_destination_is_refused():
    """The rule that is not about worktrees at all: inside the right checkout,
    at a path that checkout would happily commit."""
    assert PE.refusal(ROOT / "data" / "leak.json", kind="file") == "not_ignored"
    assert PE.refusal(ROOT / "index.html", kind="file") == "tracked_path"
    assert PE.refusal(ROOT / "private" / "README.md", kind="file") == "tracked_path", (
        "the committed private/ placeholder is TRACKED -- check-ignore alone "
        "reports it 'not ignored', which would send the operator to edit a "
        ".gitignore that is already correct")
    return "a path this checkout could commit is refused, tracked before unignored"


@case
def case_a_path_outside_every_registered_worktree_is_refused():
    with tempfile.TemporaryDirectory() as td:
        assert PE.refusal(pathlib.Path(td) / "anywhere", kind="dir") == "not_a_worktree"
    assert PE.refusal("/", kind="dir") in ("not_a_worktree", "different_repository")
    return "a temp directory and / are both refused"


@case
def case_a_dotdot_component_is_refused_rather_than_resolved():
    """`<worktree>/private/../elsewhere` cannot be normalized without following
    symlinks, and a caller-supplied '..' is exactly the argument shape this
    predicate exists for."""
    assert PE.refusal(ROOT / "private" / ".." / "data", kind="dir") == "unnormalized_path"
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
        assert PE.refusal(d / "private" / "x", kind="file") == "not_registered"
    return "a directory that CLAIMS this checkout is refused; only the register admits one"


def _abandon_worktree(td, name, mode):
    """A REGISTERED worktree of this checkout whose directory is no longer one.

    The register entry is real -- `git worktree add` wrote it -- and then the
    directory is removed WITHOUT `git worktree remove`, which is how a worktree
    is abandoned in practice: `rm -rf`, a wiped scratch directory, a reused
    path. `mode` says what is at the path afterwards:

      "repo"  an unrelated repository, created there by another project, which
              gitignores private/ as many do. The dangerous one.
      "plain" a plain directory in no repository.
      "gone"  nothing.

    THE ONLY REMOVAL IS THE ABANDONMENT ITSELF: `rm -rf` of the worktree
    directory this function created one line earlier, inside the case's own
    TemporaryDirectory. It is the fixture, not a cleanup step -- CLAUDE.md sec.4
    is about a cleanup invented inside a fixture that turned out to be pointed at
    something real, so the path is fenced to the case's tempdir by the assertion
    above and nothing else here deletes anything.

    What is left behind is a REGISTER ENTRY, and it cannot be handed back the
    ordinary way: measured on git 2.50.1, `git worktree remove --force` refuses a
    hijacked one ("is not a .git file") and `git worktree prune` keeps it while
    the directory exists -- which is the whole reason the entry survives to be
    believed. So the case wraps itself in _register_entries_confined_to(), which
    removes the admin directory of each entry that appeared during the case AND
    names a worktree inside the case's own tempdir. Not `git worktree prune`:
    prune would also remove the developer's unrelated entries whose directories
    happen to be missing -- an unmounted volume, a network share -- and those are
    not this suite's to touch.
    """
    path = pathlib.Path(td) / name
    assert path.parent == pathlib.Path(td), "the worktree must live in the case's tempdir"
    added = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(path), "HEAD"],
        capture_output=True, text=True)
    if added.returncode != 0:
        raise SkipCase(f"could not create a worktree of this checkout: {added.stderr[:200]}")
    # os.rmdir/unlink on what git just created, one level at a time, is what
    # `rm -rf` does; the abandonment being reproduced IS the removal, and it is
    # fenced to the path this function created inside the case's tempdir.
    subprocess.run(["rm", "-rf", str(path)], check=True)
    assert not path.exists()
    if mode == "gone":
        return path
    (path / "private").mkdir(parents=True)
    if mode == "repo":
        subprocess.run(["git", "init", "-q", str(path)], capture_output=True, check=True)
        (path / ".gitignore").write_text("private/\n")
        subprocess.run(["git", "-C", str(path), "add", ".gitignore"],
                       capture_output=True, check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "ignore"],
                       capture_output=True, check=True)
    return path


@case
def case_a_stale_register_entry_cannot_admit_an_unrelated_repository():
    """FINDING: `git worktree list` is a RECORD of a directory, not a question
    asked of it, and this module believed the record.

        git worktree add --detach <path> HEAD    # a real entry, from this checkout
        rm -rf <path>                            # abandoned, never `worktree remove`
        mkdir <path>; git -C <path> init         # another project takes the path
        printf 'private/\\n' > <path>/.gitignore  # ... and ignores private/, as many do

        check_destination(<path>, kind='root')                     -> ACCEPTED
        check_destination(<path>/private/household.yaml, 'file')   -> ACCEPTED
        check_write_set(<path>, dirs=('private',),
                        leaves=('private/household.yaml',))        -> ACCEPTED

    Every check below the register was then asked of the FOREIGN repository's
    own git, which answered "ignored" perfectly truthfully about a repository
    this checkout does not own. That is the 2026-08-13 incident with the one
    detail that made a human notice removed: pvoutput did NOT ignore private/,
    so `git status` there showed `?? private/`. A receiving repo that DOES
    ignore it takes the same archive silently. `~/limits-wt/` on this machine is
    shared across projects with colliding issue numbers, so abandoned worktrees
    at reusable paths are the live configuration, not a contrived one.

    THE THREE STALE SHAPES ARE NOT THE SAME FACT and are asserted apart,
    because their remedies differ and a guard that reports one for another sends
    the reader to the wrong command:

      gone   -- dropped by _resolve_register() before any match, so the
                destination is refused for what it is (no directory there);
                `git worktree prune` clears the entry.
      plain  -- the directory exists, so it still matches; refused not_a_worktree,
                and prune clears the entry because the .git it recorded is gone.
      repo   -- the directory exists AND answers a common dir, just not ours;
                refused different_repository. Neither prune nor
                `git worktree remove --force` will clear it.

    And the shell is asked the same question about the same fixture. It was
    never vulnerable -- stage-private-data.sh asks `_common_git_dir "$DST_REAL"`
    of the destination itself before it consults the register -- so before this
    fix the two implementations DISAGREED on the hijacked fixture (shell
    REFUSED, python ACCEPTED) and now agree.
    """
    if not SCRIPT.is_file():
        raise SkipCase("stage-private-data.sh is not in this checkout")
    # Per SHAPE and per question, never one expected code for all three: the
    # vanished directory is refused before the register is consulted at all, so
    # its two path-shaped questions answer about the nearest existing ancestor
    # -- which is the tempdir, in no repository. Writing that down is the point
    # of the case; collapsing it would hide which fact did the refusing.
    expect = {
        "repo":  {"root": "different_repository", "file": "different_repository",
                  "set": "different_repository"},
        "plain": {"root": "not_a_worktree", "file": "not_a_worktree",
                  "set": "not_a_worktree"},
        "gone":  {"root": "no_such_destination", "file": "not_a_worktree",
                  "set": "no_such_destination"},
    }
    bad = []
    with tempfile.TemporaryDirectory() as td:
        # Every entry these fixtures leave behind is handed back on the way out,
        # and NOTHING ELSE IS: see _register_entries_confined_to(). The `git
        # worktree prune` that used to stand here removed every entry in the
        # developer's register whose directory happened to be missing.
        with _register_entries_confined_to(td):
            src = _synthetic_src(td)
            for mode, want in sorted(expect.items()):
                path = _abandon_worktree(td, f"abandoned-{mode}", mode)
                register = subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
                    capture_output=True, text=True, check=True).stdout
                listed = f"worktree {os.path.realpath(path)}" in register.splitlines()
                if mode != "gone" and not listed:
                    bad.append(f"{mode}: git no longer names the abandoned path, so "
                               "the fixture cannot prove a stale entry is refused")
                got = {
                    "root": _single_verdict(path, "root"),
                    "file": _single_verdict(path / "private" / "household.yaml", "file"),
                    "set": _python_verdict(path, src),
                }
                for label in sorted(got):
                    if got[label] != want[label]:
                        bad.append(f"{mode}/{label}: {got[label] or 'ACCEPTED'}, "
                                   f"expected {want[label]}")
                if mode == "repo":
                    shell = _shell_verdict(_run_shell(src, path, cwd=td))
                    if shell != want["set"]:
                        bad.append(f"{mode}: stage-private-data.sh said {shell!r} and "
                                   f"the python side {want['set']!r} -- the two "
                                   "implementations disagree on a stale entry")

            # The fourth shape is a RACE and has no fixture: the entry resolves
            # in _resolve_register() and its directory is gone by the time the
            # confirmation asks. It is asked of the helper directly because
            # nothing can produce it through a public door, and it is asked at
            # all because the ORDER of the two branches decides the message --
            # common_git_dir() answers None for a vanished directory exactly as
            # it does for a plain one, so a confirmation that tested "no common
            # dir" first would tell the reader somebody had put a directory
            # there. Gone is reported as gone.
            gone = pathlib.Path(td) / "never-existed"
            try:
                PE._confirm_register_entry(str(gone), PE.self_common_git_dir())
                bad.append("_confirm_register_entry accepted a vanished entry")
            except PE.DestinationRefused as e:
                if e.reason != "no_such_destination" or "PRUNABLE" not in e.detail:
                    bad.append(f"a vanished register entry is reported as "
                               f"{e.reason} ({e.detail[:60]!r}), not as prunable")
    assert not bad, bad
    return ("a register entry whose directory is now an unrelated repository, a "
            "plain directory, or gone is refused with its own reason through both "
            "public APIs; the shell agrees on the hijacked one")


@case
def case_the_suite_removes_only_the_register_entries_it_created():
    """FINDING: this suite ran `git worktree prune` against the developer's REAL
    checkout, and prune is not scoped.

    The case above leaves register entries that cannot be handed back the
    ordinary way, so its finally pruned. Prune removes EVERY entry whose
    directory is missing, not only the ones a fixture left: a developer with a
    worktree on an unmounted volume, a network share, or a directory being
    rebuilt loses that registration by running this suite -- or
    analysis/check_coverage.sh, which now runs it -- and has to `git worktree
    repair` or add it again.

    The fixture is a BYSTANDER: a registered worktree created here, its
    directory then removed, exactly the state prune exists to clear. It must
    survive a confined block that creates and abandons its own worktree, and the
    block's own entries must be gone. Both halves, because a cleanup that
    removes nothing is not a fix.

    Every removal here is fenced to something this case created: the worktree
    directory lives in this case's own TemporaryDirectory (asserted), and the
    one admin entry removed at the end is the one that appeared when this case
    ran `git worktree add`.
    """
    admin = _register_admin_dir()
    if admin is None:
        raise SkipCase("this checkout has no git common dir, so it has no register")
    with tempfile.TemporaryDirectory() as bystander_td:
        before = _register_entry_names(admin)
        path = pathlib.Path(bystander_td) / "bystander"
        assert path.parent == pathlib.Path(bystander_td), "the fixture must be fenced"
        added = subprocess.run(
            ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(path), "HEAD"],
            capture_output=True, text=True)
        if added.returncode != 0:
            raise SkipCase(f"could not create a worktree of this checkout: "
                           f"{added.stderr[:200]}")
        appeared = _register_entry_names(admin) - before
        assert len(appeared) == 1, (
            f"`git worktree add` wrote {sorted(appeared)} register entries; this "
            "case can only clean up after itself if it knows which one is its own")
        bystander = appeared.pop()
        try:
            # The abandonment being reproduced IS this removal, of the directory
            # created three lines up inside this case's own tempdir.
            subprocess.run(["rm", "-rf", str(path)], check=True)
            assert not path.exists()
            with tempfile.TemporaryDirectory() as td:
                with _register_entries_confined_to(td):
                    own = pathlib.Path(td) / "own"
                    assert own.parent == pathlib.Path(td), "the fixture must be fenced"
                    subprocess.run(
                        ["git", "-C", str(ROOT), "worktree", "add", "--detach",
                         str(own), "HEAD"], capture_output=True, check=True)
                    made = _register_entry_names(admin) - before - {bystander}
                    assert made, "the block created no register entry to hand back"
                    subprocess.run(["rm", "-rf", str(own)], check=True)
                left = _register_entry_names(admin)
            still_there = sorted(made & left)
            assert not still_there, (
                f"the block did not hand back the entries it created: {still_there}")
            assert bystander in left, (
                "a register entry this suite did not create, whose directory is "
                "missing, was removed by running it. That is `git worktree prune`'s "
                "reach, not this suite's: the developer's worktree on an unmounted "
                "volume is the same state, and it now needs `git worktree repair`")
        finally:
            shutil.rmtree(admin / bystander, ignore_errors=True)
    return ("a confined block hands back its own register entries and leaves a "
            "missing-directory entry it did not create registered")


@case
def case_a_real_registered_worktree_is_still_accepted_through_a_symlinked_parent():
    """The accepting half of the case above, and the one the fix could plausibly
    have broken: the register entry is re-asked which repository it is in, and a
    legitimate worktree must still answer this checkout.

    Reached through a SYMLINKED PARENT as well, because symlinks above the root
    are resolved on purpose (on macOS /tmp and /var are links, so every fixture
    here already arrives through one) while links at or below the root are
    refused. A confirmation asked of the literal path instead of the resolved
    root would refuse the legitimate destination and leave the hijack refused
    for the wrong reason.
    """
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            via = pathlib.Path(td) / "reached-through-a-link"
            via.symlink_to(pathlib.Path(td), target_is_directory=True)
            assert os.path.realpath(via / wt.name) == os.path.realpath(wt)
            for path, kind in ((wt, "root"),
                               (wt / "private" / "cache", "dir"),
                               (wt / "private" / "cache" / "x.json", "file"),
                               (via / wt.name, "root"),
                               (via / wt.name / "private" / "cache", "dir"),
                               (via / wt.name / "private" / "cache" / "x.json", "file")):
                got = _single_verdict(path, kind)
                assert got is None, (
                    f"a legitimate registered worktree was refused ({got}) for "
                    f"{path} as {kind}")
    return ("a registered worktree of this checkout is still accepted, literally "
            "and through a symlinked parent, for every kind")


@case
def case_an_unreadable_register_refuses_rather_than_admits():
    """Fail closed. git always reports at least the main worktree, so an empty
    listing means the question went unanswered -- which must not read as 'no
    restrictions'.

    Asked through the private door: `worktrees=` REPLACES the register, so it is
    not on a public signature (PUBLIC_PARAMS). An empty list is the one value of
    it that cannot weaken anything, and it is still reached the same way -- a
    test that kept a public route open for its own convenience would be arguing
    against the thing it tests.
    """
    assert _private_verdict(ROOT / "private" / "x", kind="file",
                            worktrees=[]) == "register_unavailable"
    # A register naming only directories that do not exist is the same fact: the
    # entries resolve to nothing and are dropped, exactly as a stale entry from
    # git's own register is, so it fails closed rather than falling through to a
    # membership question it cannot answer.
    assert _private_verdict(ROOT / "private" / "x", kind="file",
                            worktrees=["/no/such/worktree"]) == "register_unavailable"
    return "an empty or wholly stale worktree register refuses every destination"


@case
def case_a_supplied_register_is_normalized_the_way_the_read_one_is():
    """The asymmetry that made `worktrees=` behave differently depending on how
    the caller SPELLED a path.

    _locate_worktree() matches by resolving each literal ancestor of the
    destination and comparing against the register. registered_worktrees()
    resolves what it reads from git; a SUPPLIED register used to be compared
    raw. So one directory, three spellings, three answers:

        worktrees=[realpath(repo)]   -> ACCEPTED
        worktrees=[repo]             -> different_repository   (macOS /var, ...)
        worktrees=[symlink_to(repo)] -> different_repository

    It only ever worked because check_write_set() happens to pass
    Destination.worktree, which is already resolved. That is a property of one
    call site, not of the parameter, and the next internal caller -- the only
    kind there can be now -- would have been told "different repository" about a
    directory that is the very worktree it just verified.

    Both registers now go through _resolve_register(), so this case asserts the
    three spellings AGREE rather than asserting any one verdict. Needs no
    private archive: the fixture is a throwaway repository.
    """
    with tempfile.TemporaryDirectory() as td:
        repo = pathlib.Path(td) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        (repo / ".gitignore").write_text("scratch/\n")
        link = pathlib.Path(td) / "reached-through-a-link"
        link.symlink_to(repo, target_is_directory=True)
        target = repo / "scratch" / "x"

        spellings = {
            "realpath": os.path.realpath(repo),
            "literal": str(repo),
            "symlink": str(link),
        }
        got = {name: _private_verdict(target, kind="dir", worktrees=[p])
               for name, p in spellings.items()}
        assert len(set(got.values())) == 1, (
            f"the same registered directory gets different verdicts depending on "
            f"how it is spelled in the register: {got}")
        assert got["realpath"] is None, (
            f"the fixture stopped being an accepting one ({got['realpath']}) -- it "
            "has to accept, or agreeing verdicts prove nothing")

    # And the two registers share ONE normalizer, so they cannot drift back
    # apart: the function git's own listing is resolved by is the function a
    # supplied list is resolved by.
    tree = ast.parse((ANALYSIS / "private_egress.py").read_text())
    users = sorted({fn.name for fn in ast.walk(tree)
                    if isinstance(fn, ast.FunctionDef)
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Call)
                    and getattr(n.func, "id", "") == "_resolve_register"})
    assert users == ["_check_destination", "registered_worktrees"], (
        f"_resolve_register() is called from {users}; the read register and the "
        "supplied one must both go through it, or one side of _locate_worktree's "
        "comparison is resolved and the other is not")
    return ("a supplied register is resolved exactly as the one read from git: "
            f"{len(spellings)} spellings of one worktree, one verdict")


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
            assert _single_verdict(wt / "private" / "cache", "dir") == "symlink_component"
            assert _single_verdict(wt / "private" / "cache" / "deep" / "file.json",
                                   "file") == "symlink_component", \
                "a link above the leaf must still be seen"
            # and the same worktree, without the link, is accepted
            assert _single_verdict(wt / "private" / "real_cache", "dir") is None
    return "a symlinked component at or below the worktree root is refused, at any depth"


@case
def case_a_special_file_and_a_hard_link_are_refused_at_a_leaf():
    """THROUGH THE PUBLIC SINGLE-PATH API, which is the whole of the fix here:
    these checks used to be reachable only via check_write_set(), so
    check_destination() -- the entry point the argument-derived writers call --
    accepted a FIFO and accepted a hard link to a file outside the worktree, and
    the caller's ordinary overwrite then rewrote the outside inode.

    Neither is a symlink, so the component walk cannot see either: a FIFO blocks
    the open (or hands the bytes to a reader), a second name on the inode makes
    a file elsewhere a copy of the data.
    """
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            priv = wt / "private"
            os.mkfifo(priv / "fifo.json")
            elsewhere = pathlib.Path(td) / "elsewhere.json"
            elsewhere.write_text("{}\n")
            os.link(elsewhere, priv / "hard.json")
            (priv / "plain.json").write_text("{}\n")
            for name, kind, expect in (("fifo.json", "file", "special_file"),
                                       ("hard.json", "file", "hard_link"),
                                       ("plain.json", "file", None)):
                got = _single_verdict(priv / name, kind)
                assert got == expect, f"{name}: expected {expect}, got {got}"
            assert stat.S_ISFIFO(os.lstat(priv / "fifo.json").st_mode)
            assert os.lstat(elsewhere).st_nlink == 2, (
                "the fixture must really be a second name for an inode outside "
                "the worktree, or the case proves nothing")
    return ("a FIFO and a hard-linked file are refused by check_destination() "
            "itself, where a plain file is not")


@case
def case_a_directory_and_a_file_are_refused_in_each_other_s_slot():
    """The other half of what the destination KIND buys: the same path is
    accepted or refused according to what the caller says it will write there.
    A directory where a file goes is not overwritten, it is written INSIDE --
    the run reports success and the file is not there; a file where a directory
    goes fails the caller's own mkdir, or worse, gets truncated."""
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            priv = wt / "private"
            (priv / "adir").mkdir()
            (priv / "afile.json").write_text("{}\n")
            for rel, kind, expect in (("adir", "dir", None),
                                      ("adir", "file", "special_file"),
                                      ("afile.json", "file", None),
                                      ("afile.json", "dir", "not_a_directory"),
                                      ("afile.json", "tree", "not_a_directory")):
                got = _single_verdict(priv / rel, kind)
                assert got == expect, f"{rel} as {kind}: expected {expect}, got {got}"
    return "a directory and a regular file are each refused in the other's slot"


@case
def case_a_non_directory_intermediate_component_is_refused():
    """FINDING: the component walk required every existing component not to be a
    LINK, and required nothing else of it. So a regular file one level up made
    the whole path below it acceptable:

        printf 'blocker\\n' > <worktree>/private/blocker
        check_destination(<worktree>/private/blocker/leaf.json, kind='file')
            -> ACCEPTED

    and the caller that believed it then could not create the path at all
    (FileExistsError; ENOTDIR one level deeper). No data escapes -- which is why
    this is the lower-severity half -- but the module answers one question, "may
    private-derived files be written here", and the answer was wrong. A FIFO or
    a device node in an intermediate component is the same shape and gets the
    same answer, because for a component above the last the only question is
    whether it is a directory.

    NO NEW REFUSAL CODE. `not_a_directory` is already defined as "a path that
    must be a directory exists and is not one", and _check_leaf() already
    answers it for a FIFO or a regular file in a DIRECTORY slot; an intermediate
    component is a directory slot whatever the leaf's kind is, so the same fact
    gets the same code and one remedy stays one vocabulary entry. It is also the
    code stage-private-data.sh's "a destination path exists and is not a
    directory" already maps to, so the agreement table needs no new mapping.
    What did change is API_REACH: the leaf-slot form is reachable only for
    kind='dir'/'tree', while the component form is reachable for kind='file'
    too, and that widening is asserted by the reach case rather than declared.
    """
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            priv = wt / "private"
            (priv / "blocker").write_text("not a directory\n")
            os.mkfifo(priv / "pipe")
            (priv / "realdir").mkdir()
            bad = []
            for rel, kind, expect in (
                    ("blocker/leaf.json", "file", "not_a_directory"),
                    ("blocker/sub/deep.json", "file", "not_a_directory"),
                    ("blocker/sub", "dir", "not_a_directory"),
                    ("blocker/sub", "tree", "not_a_directory"),
                    ("pipe/leaf.json", "file", "not_a_directory"),
                    # ... and the accepting half in the same position: a real
                    # directory there, and a path whose intermediate components
                    # do not exist yet, are both ordinary destinations.
                    ("realdir/leaf.json", "file", None),
                    ("not-created-yet/deep/leaf.json", "file", None)):
                got = _single_verdict(priv / rel, kind)
                if got != expect:
                    bad.append(f"private/{rel} as {kind}: {got or 'ACCEPTED'}, "
                               f"expected {expect or 'ACCEPTED'}")
            # The whole-set API asks the same question of the same path.
            for leaf, expect in (("private/blocker/leaf.json", "not_a_directory"),
                                 ("private/pipe/leaf.json", "not_a_directory"),
                                 ("private/realdir/leaf.json", None)):
                try:
                    PE.check_write_set(wt, leaves=(leaf,))
                    got = None
                except PE.DestinationRefused as e:
                    got = e.reason
                if got != expect:
                    bad.append(f"check_write_set(leaves=({leaf!r},)): "
                               f"{got or 'ACCEPTED'}, expected {expect or 'ACCEPTED'}")
            assert not bad, bad

            # The fixture is what it claims, and the ACCEPT really was wrong:
            # the caller this module answers for cannot create that path.
            assert stat.S_ISFIFO(os.lstat(priv / "pipe").st_mode)
            try:
                (priv / "blocker" / "leaf.json").parent.mkdir(parents=True)
                raise AssertionError(
                    "the blocker fixture is not a blocker -- the caller created "
                    "the directory, so accepting the path was not wrong")
            except OSError as e:
                assert isinstance(e, (FileExistsError, NotADirectoryError)), e
    OBSERVED.add(("file", "not_a_directory"))
    return ("a regular file or a FIFO in an intermediate component is refused "
            "not_a_directory for every leaf kind, through both public APIs")


@case
def case_the_worktree_root_itself_is_not_an_ordinary_destination():
    """FINDING 1, direct: check_destination(<a registered worktree root>) used
    to be ACCEPTED, because an empty relpath fell straight through the
    ignore-requirement guard. A writer handed the checkout root -- an
    argument-derived cache or manifest directory pointed one level too high --
    would have put private-derived files where every one of them is
    committable.

    kind='root' still accepts it, because that is a different question: stage an
    archive INTO this worktree, then declare the paths to check_write_set()."""
    assert PE.refusal(ROOT, kind="dir") == "worktree_root_itself"
    assert PE.refusal(ROOT, kind="tree") == "worktree_root_itself"
    assert PE.refusal(ROOT, kind="file") == "worktree_root_itself"
    assert PE.refusal(ROOT, kind="root") is None, (
        "the root question must still accept a registered worktree root, or "
        "check_write_set() refuses every legitimate staging destination")
    OBSERVED.update({("dir", "worktree_root_itself"), ("tree", "worktree_root_itself"),
                     ("file", "worktree_root_itself")})
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            # ... and on a worktree that is not the one this module lives in,
            # so the refusal is about being a root and not about being THIS root.
            assert PE.refusal(wt, kind="dir") == "worktree_root_itself"
            assert PE.refusal(wt / "private" / "cache", kind="dir") is None
    return "a worktree root is refused for every ordinary kind and accepted only as a root"


@case
def case_the_public_api_cannot_be_asked_without_a_destination_kind():
    """Why a required argument rather than a check_leaf=True flag: a flag has to
    have a default, and a default that is safe for one caller is the weaker
    check for the next one who does not know it is there. Omitting the kind is a
    TypeError before any work happens; naming a kind that does not exist is a
    ValueError, not a refusal -- a caller who cannot say what it is writing has
    a bug in itself, and must not be able to spell that bug as 'accepted'."""
    for call in (lambda: PE.check_destination(ROOT / "private" / "x"),
                 lambda: PE.refusal(ROOT / "private" / "x")):
        try:
            call()
        except TypeError as e:
            assert "kind" in str(e), e
        else:
            raise AssertionError("the kind is not required -- a caller can still get "
                                 "the weaker check by saying nothing")
    for bad in ("leaf", "", None, "DIR"):
        try:
            PE.check_destination(ROOT / "private" / "x", kind=bad)
        except ValueError as e:
            assert "kind" in str(e), e
        except PE.DestinationRefused as e:
            raise AssertionError(f"kind={bad!r} produced a refusal ({e.reason}) rather "
                                 "than a programming error")
        else:
            raise AssertionError(f"kind={bad!r} was accepted")
    return f"the kind is required, and only {list(PE.KINDS)} are accepted"


# The module's PUBLIC entry points and every parameter each one takes -- as an
# ALLOWLIST, with what each parameter DESCRIBES written next to it.
#
# The generalisation of the defect below. Three separate keywords have now been
# found on a public signature that did not describe the destination but weakened
# the answer about it (require_ignored=, worktrees=, env=), and each was found by
# somebody reading the signature. A set of names alone cannot tell the next one
# apart from a legitimate addition; a set of names with a stated ROLE can, because
# adding a fourth weakener here means writing down a description of it that is
# false, under review, next to three that are true.
#
# What this table CAN check mechanically is the exact parameter set -- a keyword
# added to a public door and not classified here fails at once -- plus, through
# PRIVATE_DOORS below, that every known weakener is on the private side and only
# there. What it cannot check is whether a NEW parameter weakens a check; no test
# can. It gets the reader to the one place where a human decides, which is what
# the earlier version of this table was for and what it did not quite say.
PUBLIC_PARAMS = {
    "check_destination": {
        "path": "WHICH path the caller will write to",
        "kind": "WHAT it will write there -- and its four values are all equally "
                "strong, so there is no weak one to fall into",
    },
    "refusal": {
        "path": "as check_destination",
        "kind": "as check_destination",
        "kw":   "forwarded verbatim to check_destination(), so it is bounded by "
                "that signature and can carry nothing this table does not list. "
                "Asserted below rather than assumed",
    },
    "check_write_set": {
        "root":        "WHICH worktree root the set is staged into",
        "dirs":        "WHICH paths, as directories written into non-recursively",
        "recursive":   "WHICH paths, as directories a recursive copy descends",
        "leaves":      "WHICH paths, as regular files -- a glob among them names "
                       "fewer paths, never a laxer check on one, and never no "
                       "check at all: a pattern that matches nothing is checked "
                       "as the literal it is",
        "glob_source": "WHICH tree a leaf pattern is expanded against. A wrong "
                       "one names FEWER leaves than the copy will write, and "
                       "that is all it can do -- it can no longer take a "
                       "declared pattern out of the check entirely, because a "
                       "source that cannot be listed is refused and a pattern "
                       "that matches nothing is still evaluated. It describes "
                       "the destination set; it cannot waive the question asked "
                       "of any part of it",
    },
}

# The parameters that WEAKEN a check, what each can manufacture, and the private
# door that carries it. Each was reachable from a public signature at some point
# in this module's history, and each is now spellable only from inside it.
WEAKENERS = {
    "require_ignored": "skips the ignore/tracked question entirely, so the "
                       "TRACKED index.html is accepted",
    "worktrees":       "replaces the register, so a gitignored path in an "
                       "UNRELATED repository is accepted -- the 2026-08-13 "
                       "shape, through a keyword",
    "env":             "does not clear HOME, so a .gitconfig with "
                       "core.excludesFile manufactures the 'ignored' verdict "
                       "and data/leak.json is accepted",
}

# public entry point -> (the private door it delegates to, the weakeners that
# door carries). Both directions are asserted: the private door must carry
# exactly these beyond its public face, so moving one back out to the public
# signature fails HERE as well as in PUBLIC_PARAMS -- two locks, because a
# single one can be opened by the same edit that opens the door.
PRIVATE_DOORS = {
    "check_destination": ("_check_destination", {"require_ignored", "worktrees", "env"}),
    "check_write_set":   ("_check_write_set", {"env"}),
}


@case
def case_no_public_entry_point_can_weaken_a_check():
    """FINDINGS 2-4: three checks had a public off switch, found one at a time.

        check_destination(ROOT/"index.html", kind="file")
            -> REFUSED [tracked_path]
        check_destination(ROOT/"index.html", kind="file", require_ignored=False)
            -> ACCEPTED                            on the repo's own TRACKED file

        check_destination(<other repo>/private/x, kind="dir")
            -> REFUSED [different_repository]
        check_destination(<other repo>/private/x, kind="dir",
                          worktrees=[realpath(<other repo>)])
            -> ACCEPTED               a gitignored path in an UNRELATED checkout,
                                      which is the 2026-08-13 incident exactly

        check_destination(ROOT/"data/leak.json", kind="file")
            -> REFUSED [not_ignored]
        check_destination(ROOT/"data/leak.json", kind="file", env=<HOME with a
                          .gitconfig whose core.excludesFile names leak.json>)
            -> ACCEPTED                       and check_write_set(env=...) too

    Each passed every OTHER check -- symlink walk, register, leaf, kind -- so in
    all three the refusal that did not happen is the only thing missing.

    Each capability is legitimate for exactly one internal caller and still
    exists: check_write_set() asks the committability question itself and then
    hands the paths below the root through with the exemption, in one
    already-verified worktree, in one environment. All three moved to the
    PRIVATE _check_destination()/_check_write_set(), which the public doors are
    one-line delegations to. So they were made unreachable, not deleted --
    asserted both ways below, because a "fix" that deleted them would make
    check_write_set() re-ask the ignore question underneath an answered path and
    change the verdicts the agreement table compares.

    Why signatures and not a convention: this module's whole argument is that a
    check which can be waived will be waived. That is why `kind` is required
    rather than defaulted, and the `recursive` hole was require_ignored being
    passed without the phase meant to justify it. `env=` was the sharpest of the
    three -- a TEST-ONLY parameter sitting on the door an argument-derived
    writer calls.

    ON env= AND HOME, so a later reader does not re-open it and reach for the
    sanitizer list instead: HOME is deliberately NOT added to GIT_IDENTITY_VARS.
    It supplies a legitimate INPUT to the real repository's answer rather than
    replacing WHICH repository answers, which is the property that whole list is
    for; clearing it would make this module disagree with the operator's own
    shell in a way no fixture here would catch, since this checkout has
    core.excludesFile unset; and the forgery needs an in-process caller who
    could equally call shutil.copy. The equality with
    stage-private-data.sh's sanitizer therefore stays as it is, and the lever
    taken instead is the REACH of the parameter -- which is this case.
    """
    tracked = ROOT / "index.html"
    assert tracked.is_file(), f"{tracked} is missing -- pick another tracked file"
    assert _single_verdict(tracked, "file") == "tracked_path", (
        "the reproduction's premise is gone: this tracked path is no longer "
        "refused even with the check ON")

    with tempfile.TemporaryDirectory() as td:
        # One reproduction per weakener, each a value that REALLY manufactures
        # an acceptance -- proved in step 1a against the private door, because a
        # "reproduction" whose value happened to be inert would prove only that
        # the keyword is now spelled somewhere else.
        other = pathlib.Path(td) / "unrelated-project"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True)
        (other / ".gitignore").write_text("private/\n")
        home = pathlib.Path(td) / "home"
        home.mkdir()
        (home / "excludes").write_text("leak.json\n")
        (home / ".gitconfig").write_text(
            f"[core]\n\texcludesFile = {home / 'excludes'}\n")
        forged = dict(os.environ, HOME=str(home))

        fixtures = {
            "require_ignored": (tracked, "file", False,
                                "the repo's own TRACKED report file"),
            "worktrees":       (other / "private" / "x", "dir",
                                [os.path.realpath(other)],
                                "a gitignored path in an UNRELATED repository"),
            "env":             (ROOT / "data" / "leak.json", "file", forged,
                                "a committable path made to look ignored via HOME"),
        }
        assert set(fixtures) == set(WEAKENERS), (
            f"no reproduction for {sorted(set(WEAKENERS) ^ set(fixtures))} -- a "
            "weakener classified but never typed at a public door is a claim, "
            "not a test")

        # 1a. Every value is potent, and the capability still exists where it is
        #     earned: refused with the strong answer, ACCEPTED with the weak one.
        #     This is also the "moved, not deleted" half -- a fix that deleted
        #     the exemption would make the whole-set checker re-ask the ignore
        #     question underneath an answered path and change the verdicts the
        #     agreement table compares.
        for keyword, (path, kind, value, what) in sorted(fixtures.items()):
            strong = _private_verdict(path, kind=kind)
            assert strong is not None, (
                f"the {keyword}= reproduction lost its premise: {what} is no longer "
                "refused with the check ON, so accepting it proves nothing")
            weak = _private_verdict(path, kind=kind, **{keyword: value})
            assert weak is None, (
                f"{keyword}={value!r} did not manufacture an acceptance ({weak}) on "
                f"{what}; without that this case tests a keyword that never worked")
        # ... including through the whole-set door env= used to sit on.
        PE._check_write_set(ROOT, dirs=(), recursive=(),
                            leaves=("data/leak.json",), glob_source=None, env=forged)

        # 1b. Behavioral: not spellable through ANY public door. TypeError, not a
        #     refusal and not a Destination -- it is a bug in the caller, not a
        #     verdict about the path.
        for keyword, (path, kind, value, what) in sorted(fixtures.items()):
            for describe, call in (
                    ("check_destination",
                     lambda p=path, k=kind, w=keyword, v=value:
                     PE.check_destination(p, kind=k, **{w: v})),
                    ("refusal",
                     lambda p=path, k=kind, w=keyword, v=value:
                     PE.refusal(p, kind=k, **{w: v})),
                    ("check_write_set",
                     lambda w=keyword, v=value:
                     PE.check_write_set(ROOT, leaves=("data/leak.json",), **{w: v}))):
                try:
                    got = call()
                except TypeError as e:
                    assert keyword in str(e), (
                        f"{describe}({keyword}=...) raised a TypeError that does "
                        f"not name the keyword: {e}")
                except PE.DestinationRefused as e:
                    raise AssertionError(
                        f"{describe}({keyword}=...) produced a refusal ({e.reason}) "
                        "rather than rejecting the keyword -- a caller that types "
                        "it must learn it does not exist, not that this one path "
                        "happened to fail")
                else:
                    raise AssertionError(
                        f"{describe}({keyword}=...) returned {got!r} on {what}: an "
                        "ordinary caller can still weaken this check -- "
                        f"{WEAKENERS[keyword]}")

    # 2. Structural: every public signature is exactly the allowlist, each entry
    #    says what it DESCRIBES, and none can smuggle a keyword through.
    for name, declared in PUBLIC_PARAMS.items():
        params = inspect.signature(getattr(PE, name)).parameters
        assert set(params) == set(declared), (
            f"{name}() takes {sorted(set(params) ^ set(declared))} that PUBLIC_PARAMS "
            "does not classify. Add it here with what it DESCRIBES about the "
            "destination -- and if it turns a check OFF instead, it belongs on the "
            "private door, where a caller outside the module cannot reach it")
        thin = sorted(k for k, why in declared.items() if len(why) < 20)
        assert not thin, (
            f"{name}() has parameter(s) {thin} listed with no stated role. This table "
            "is an allowlist of parameters that only DESCRIBE the destination; an "
            "entry with nothing written next to it classifies nothing")
        assert not set(declared) & set(WEAKENERS), (
            f"{name}() declares {sorted(set(declared) & set(WEAKENERS))}, which "
            "WEAKENERS says weakens a check. A public door may not carry it")
        var_kw = [p for p in params.values() if p.kind is inspect.Parameter.VAR_KEYWORD]
        if var_kw:
            # refusal(**kw) is bounded by check_destination's own signature, which
            # the loop above pins. Asserted, not assumed: a **kw forwarded to
            # something laxer would put the keyword straight back.
            assert name == "refusal", (
                f"{name}() takes **{var_kw[0].name}, so any keyword reaches whatever "
                "it forwards to")

    # 3. The capabilities still exist where they are earned, on the private side
    #    and only there. The second lock: a weakener moved back onto a public
    #    signature fails here as well as in PUBLIC_PARAMS.
    for public, (private, carried) in PRIVATE_DOORS.items():
        pub = set(inspect.signature(getattr(PE, public)).parameters)
        priv = set(inspect.signature(getattr(PE, private)).parameters)
        assert priv - pub == carried, (
            f"{private}() carries {sorted(priv - pub)} beyond {public}(); "
            f"PRIVATE_DOORS declares {sorted(carried)}. A weakener that moved out "
            "to the public signature shows up here")
        assert not carried - set(WEAKENERS), (
            f"{private}() is declared to carry {sorted(carried - set(WEAKENERS))}, "
            "which WEAKENERS does not describe")

    # 4. Each public door is a one-line delegation to its private one, passing
    #    the STRONG value of every weakener as a literal.
    tree = ast.parse((ANALYSIS / "private_egress.py").read_text())
    tops = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    strong_literals = {"require_ignored": True, "worktrees": None, "env": None}
    for public, (private, carried) in PRIVATE_DOORS.items():
        body = [n for n in tops[public].body
                if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.Return), (
            f"{public}() is no longer a single delegation -- two bodies drift, and "
            "the public one is the one nobody exercises with the keyword")
        call = body[0].value
        assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == private, (
            f"{public}() must delegate to {private}()")
        for keyword in sorted(carried):
            passed = [k.value for k in call.keywords if k.arg == keyword]
            assert len(passed) == 1 and isinstance(passed[0], ast.Constant) \
                and passed[0].value is strong_literals[keyword], (
                f"{public}() must pass {keyword}={strong_literals[keyword]!r} as a "
                "literal: anything computed can be computed to the weak value")

    # ... and nothing else in the module reaches a private door.
    for private, entitled in (("_check_destination",
                               ["_check_write_set", "check_destination"]),
                              ("_check_write_set", ["check_write_set"])):
        callers = sorted({name for name, fn in tops.items()
                          for n in ast.walk(fn)
                          if isinstance(n, ast.Call)
                          and getattr(n.func, "id", "") == private})
        assert callers == entitled, (
            f"{private}() is called from {callers}, expected {entitled}; only the "
            "public delegation and the whole-set checker -- which asks the "
            "committability question itself -- are entitled to these")
    return (f"none of the {len(WEAKENERS)} weakening keywords is spellable at any of "
            f"the {len(PUBLIC_PARAMS)} public entry points, and each still works "
            "from the private door that earns it")


@case
def case_check_write_set_owns_no_refusal_of_its_own():
    """The structural half of the asymmetry question: every refusal reachable
    through check_write_set() must be reachable through check_destination()
    too, or the agreement table can be complete for one API and still miss a
    hole in the other -- which is exactly what happened.

    Proved by reading the module: the whole-set checker raises nothing itself.
    It asks the single-path checker about the root and about every path below
    it, and _require_uncommittable() -- which that checker also asks -- about
    the declared set.

    Read off _check_write_set(), which is where the body lives now that `env=`
    has been taken off the public signature. Same body: check_write_set() is a
    one-line delegation to it, asserted by
    case_no_public_entry_point_can_weaken_a_check.
    """
    tree = ast.parse((ANALYSIS / "private_egress.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_check_write_set")
    raises = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
              and getattr(n.exc.func, "id", "") == "DestinationRefused"]
    assert not raises, (
        f"_check_write_set() raises a refusal of its own at line(s) {raises} -- move "
        "the check into the single-path checker behind a kind, or that API silently "
        "has one fewer check than the whole-set one")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_check_destination" in called, (
        "_check_write_set() no longer routes through the single-path checker, so "
        "the two can drift again")
    assert "_check_leaf" not in called and "_scan_tree" not in called, (
        "_check_write_set() reaches past the single-path checker to a private check "
        "-- that is the shape that left the single-path API weaker than this one")
    # The ROOT call takes no exemption. It is the one call here that must not:
    # a root's own committability is asked by nobody else, and this is the
    # question stage-private-data.sh is being compared against.
    root_calls = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "_check_destination"
                  and any(k.arg == "kind" and isinstance(k.value, ast.Constant)
                          and k.value.value == "root" for k in n.keywords)]
    assert len(root_calls) == 1, (
        f"expected exactly one kind='root' call, found {len(root_calls)}")
    for keyword, want in (("require_ignored", True), ("worktrees", None)):
        passed = [k.value for k in root_calls[0].keywords if k.arg == keyword]
        assert len(passed) == 1 and isinstance(passed[0], ast.Constant) \
            and passed[0].value is want, (
            f"the kind='root' call must pass {keyword}={want!r} as a literal -- the "
            "root is the one path here that has earned no exemption")

    # The expansion helpers sit on the whole-set side of that line and DO raise,
    # so the same rule is applied to them by name: what they may refuse is the
    # input they own -- a pattern, and the tree it is expanded against -- and
    # nothing about a destination path. A destination check appearing here would
    # be the old shape again, one API silently stronger than the other.
    expanders = {"_expand", "_expand_leaves", "_require_listable_source",
                 "_require_literal_directories"}
    tops = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    raised = set()
    for name in sorted(expanders):
        assert name in tops, f"{name}() is gone; the expansion seam has moved"
        for n in ast.walk(tops[name]):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) \
                    and getattr(n.exc.func, "id", "") == "DestinationRefused":
                first = n.exc.args[0] if n.exc.args else None
                assert isinstance(first, ast.Constant), (
                    f"{name}() raises a computed reason at line {n.lineno}")
                raised.add(first.value)
    assert raised == set(WRITE_SET_ONLY_REASONS), (
        f"the leaf-expansion helpers raise {sorted(raised)}; "
        f"WRITE_SET_ONLY_REASONS declares {sorted(WRITE_SET_ONLY_REASONS)}. They "
        "may refuse their own input -- an unlistable source, an unexpandable "
        "pattern -- and nothing about a destination path, which belongs behind a "
        "kind where both public APIs reach it")
    return ("_check_write_set() raises nothing the single-path checker cannot "
            f"raise, and its {len(expanders)} expansion helpers raise only the "
            f"{len(raised)} refusals about their own input")


# Every parameter of check_write_set() that NAMES PATHS IT WILL WRITE, and the
# two others it takes. Split out so a fourth path argument added later cannot
# quietly skip the committability phase: the case below fails until it is
# classified here and exercised in the loop.
WRITE_SET_PATH_ARGS = ("dirs", "recursive", "leaves")
WRITE_SET_OTHER_ARGS = ("root", "glob_source", "env")


@case
def case_every_declared_path_is_asked_the_committability_question():
    """FINDING 1: `recursive=` skipped the check the module exists to run.

    check_write_set() hands every path below the root to the checker with
    require_ignored=False, on the strength of an earlier phase having asked
    the ignore/tracked question for the declared set. That phase looped over
    `dirs` and over the leaves no declared path covered -- never over
    `recursive`. So:

        check_write_set(ROOT, recursive=("data",))  ->  ACCEPTED
        check_write_set(ROOT, dirs=("data",))       ->  REFUSED [tracked_path]

    on the same tracked, committable directory. A caller staging a subtree with
    `recursive` -- without redundantly naming its parent in `dirs` -- could
    recursively copy the archive into a path one `git add -A` from a commit,
    through this module's own public API, which is the exact outcome it exists
    to prevent.

    Asked here of EVERY path-bearing argument rather than of the one named in
    the finding, and of the two refusals a committable destination can produce
    (tracked, and untracked-but-not-ignored), because the defect is not
    "`recursive` was forgotten" but "a per-argument loop can forget an
    argument". Needs no private archive and no worktree: it asks about this
    checkout's own tracked paths.
    """
    # The UNION of the public door and the private one it delegates to: a path
    # argument added to either has to be classified, or a fourth `recursive`
    # could arrive on the side this case does not read.
    params = (set(inspect.signature(PE.check_write_set).parameters)
              | set(inspect.signature(PE._check_write_set).parameters))
    unclassified = sorted(params - set(WRITE_SET_PATH_ARGS) - set(WRITE_SET_OTHER_ARGS))
    assert not unclassified, (
        f"check_write_set() has argument(s) {unclassified} that this case does not "
        "classify. If they name paths it will write, add them to "
        "WRITE_SET_PATH_ARGS -- the loop below then proves they are asked the "
        "committability question, which is what `recursive` was not")

    def verdict(**kw):
        try:
            PE.check_write_set(ROOT, **kw)
            return None
        except PE.DestinationRefused as e:
            return e.reason

    # "data" is tracked; "data/no-such-file.json" is untracked and NOT ignored.
    # Both are committable, and each is refused for its own reason.
    bad = []
    for arg in WRITE_SET_PATH_ARGS:
        for value, expect in (("data", "tracked_path"),
                              ("data/no-such-file.json", "not_ignored")):
            got = verdict(**{arg: (value,)})
            if got != expect:
                bad.append(f"check_write_set(ROOT, {arg}=({value!r},)) -> "
                           f"{got or 'ACCEPTED'}, expected {expect}")
    # ... and the transitive form: a leaf is exempt from the phase when a
    # declared directory covers it, so an unchecked `recursive` entry exempted
    # its own contents too. Both were accepted before the fix.
    got = verdict(recursive=("data",), leaves=("data/no-such-file.json",))
    if got != "tracked_path":
        bad.append(f"check_write_set(ROOT, recursive=('data',), leaves=(...)) -> "
                   f"{got or 'ACCEPTED'}, expected tracked_path")
    assert not bad, bad

    # The accepting half, on paths nothing has created: a guard that refuses
    # correct input is the shape that gets guards disabled. Archive-independent
    # by construction -- these three do not exist in any checkout.
    d = PE.check_write_set(ROOT, dirs=("private/nonexistent-dir",),
                           recursive=("private/nonexistent-tree",),
                           leaves=("private/nonexistent-leaf.json",))
    assert d.worktree == str(pathlib.Path(ROOT).resolve()), d
    return (f"all {len(WRITE_SET_PATH_ARGS)} path arguments are asked the "
            "committability question, in both refusal shapes")


def _write_set_verdict(**kw):
    """check_write_set()'s verdict on THIS checkout, as a reason code or None.

    Asked of ROOT because every case below is about the leaf PATTERNS and the
    tree they are expanded against, not about the destination root: this
    checkout is a registered worktree of itself, so the root question is settled
    and what is left is the question under test. Needs no private archive and no
    throwaway worktree.
    """
    try:
        PE.check_write_set(ROOT, **kw)
        return None
    except PE.DestinationRefused as e:
        WRITE_SET_OBSERVED.add(e.reason)
        return e.reason


@case
def case_a_leaf_pattern_whose_source_cannot_be_listed_is_refused():
    """A `glob_source` that cannot be listed used to expand to nothing, and an
    empty expansion took its declared destinations out of the write set with it:

        check_write_set(ROOT, leaves=("data/*.json",),
                        glob_source="<a path that is not there>")
            -> ACCEPTED, on this checkout's own TRACKED data/ directory

    Neither the committability question nor the leaf check ran for that declared
    pattern, and the function returned a Destination. `glob_source` is a public
    parameter and points at a tree nothing else here looks at, so a stale path, a
    typo or an unmounted volume was an off switch for every pattern in the set --
    while the copy the caller then runs reads its REAL source and writes names
    this module never saw. Understating the input is what it does when it is
    merely WRONG; producing acceptance without having evaluated anything is a
    waiver, whatever the argument was for.

    Both shapes of unlistable are asked, because they arrive by different
    accidents: a source that is not there at all, and one whose pattern
    directory is there and unreadable.
    """
    with tempfile.TemporaryDirectory() as td:
        gone = pathlib.Path(td) / "not-mounted"
        got = _write_set_verdict(leaves=("data/*.json",), glob_source=gone)
        assert got == "glob_source_unlistable", (
            f"an absent glob_source gave {got or 'ACCEPTED'} on tracked data/")

        # ... and the source tree itself present, with the pattern's own
        # directory unreadable: the branch a bare existence check would miss.
        locked = pathlib.Path(td) / "locked"
        (locked / "data").mkdir(parents=True)
        os.chmod(locked / "data", 0o000)
        try:
            unreadable = True
            try:
                os.listdir(locked / "data")
                unreadable = False      # running as root, or an OS that ignores it
            except OSError:
                pass
            if unreadable:
                got = _write_set_verdict(leaves=("data/*.json",), glob_source=locked)
                assert got == "glob_source_unlistable", (
                    f"an unreadable pattern directory gave {got or 'ACCEPTED'}")
        finally:
            os.chmod(locked / "data", 0o700)

        # The accepting half, on the same fixture: a source that IS listable and
        # holds the file the pattern names expands to that file, and this
        # checkout ignores where it would land.
        real = pathlib.Path(td) / "real-source"
        (real / "private").mkdir(parents=True)
        (real / "private" / "cache-a.json").touch()
        assert _write_set_verdict(leaves=("private/cache-*.json",),
                                  glob_source=real) is None
    return ("an absent or unreadable glob_source is refused rather than expanded "
            "to nothing, and a listable one still expands")


@case
def case_a_leaf_pattern_that_matches_nothing_is_checked_as_a_literal():
    """The decision the finding asked for, and the reason for it.

    `cp <src>/enphase_sam8760_*.csv <dst>/` copies nothing when the household has
    no SAM export, so a pattern that matches nothing is not an error and refusing
    it would refuse a correct caller -- which is how guards get switched off. But
    the guard may not report success on a destination it never evaluated, so the
    pattern is checked AS THE LITERAL IT IS: the component walk runs down to the
    pattern's own directory, and git answers the committability question for the
    pattern itself (`git ls-files` reads it as a pathspec; `git check-ignore`
    answers for the whole excluded directory).

    Which is what these two reproductions turn on. Before the fix both ACCEPTED
    this checkout's own committable data/ directory, having checked nothing:

        leaves=("data/*.json",)        glob_source=<a tree with no data/>
        leaves=("data/*.json",)        glob_source=<a tree whose data/ is empty>

    They are separate rows because they take different branches -- one cannot
    find the directory, one finds it and finds nothing in it -- and either one
    alone would leave the other silently returning [].
    """
    with tempfile.TemporaryDirectory() as td:
        no_dir = pathlib.Path(td) / "wrong-source"
        no_dir.mkdir()
        empty = pathlib.Path(td) / "empty-data"
        (empty / "data").mkdir(parents=True)

        bad = []
        for what, source in (("a source with no such directory", no_dir),
                             ("a source whose directory is empty", empty)):
            # tracked, and untracked-but-not-ignored: both committable, each
            # refused for its own reason, and neither reachable at all while the
            # expansion returned [].
            for pattern, expect in (("data/*.json", "tracked_path"),
                                    ("data/no-such-*.json", "not_ignored")):
                got = _write_set_verdict(leaves=(pattern,), glob_source=source)
                if got != expect:
                    bad.append(f"leaves=({pattern!r},) against {what}: "
                               f"{got or 'ACCEPTED'}, expected {expect}")
        assert not bad, bad

        # The accepting half, and it is the reason this is not a refusal: a
        # pattern that matches nothing, at a path this checkout really does
        # ignore, must still be accepted -- with the source given and without.
        for source in (no_dir, None):
            got = _write_set_verdict(leaves=("private/nonexistent-*.json",),
                                     glob_source=source)
            assert got is None, (
                f"a zero-match pattern at an ignored path was refused ({got}); a "
                "household with no SAM export is a correct caller")
    return ("a pattern that matches nothing is checked as a literal -- the "
            "committable destination is refused, the ignored one accepted")


@case
def case_a_pattern_where_a_directory_is_named_is_refused():
    """The same shape in the arguments BESIDE the one the finding named, which
    is where it has been every round: a pattern that names a DIRECTORY.

        check_write_set(ROOT, recursive=("private/1-raw-data/*",))  -> ACCEPTED

    on this checkout, having scanned none of the directories the copy really
    descends. `dirs` and `recursive` are not expanded at all, so a pattern in one
    was read as a literal path -- and a path that does not exist is what every
    check treats as absent: the walk stops at the glob, the leaf check returns
    because there is nothing there, and _scan_tree() returns because it is not a
    directory. The tree scan, which is the only reason `recursive` exists as a
    separate argument, ran over nothing and the call returned a Destination.

    The leaf half of the same shape is `private/*/cache.json`: dirname is
    `private/*`, which no listdir can answer, so it fell into the empty
    expansion. Both are refused rather than answered by a literal, and that
    asymmetry with a zero-match LEAF is the point -- a leaf's literal carries the
    facts a leaf needs (its component chain, its committability, which hold for
    every name the pattern could yield), while a directory's check is a scan of
    what is inside it and no literal stands in for that.
    """
    bad = []
    for kw in ({"leaves": ("private/*/cache.json",)},
               {"leaves": ("private/*/*.json",)},
               {"recursive": ("private/1-raw-data/*",)},
               {"recursive": ("private/*-bills",)},
               {"dirs": ("private/*-cache",)}):
        got = _write_set_verdict(**kw)
        if got != "pattern_names_a_directory":
            bad.append(f"check_write_set(ROOT, **{kw}) -> {got or 'ACCEPTED'}")
    assert not bad, bad
    # A pattern in a leaf's LAST component is the supported form and still works,
    # and a literal directory is still accepted -- the refusal is about patterns,
    # not about these arguments.
    assert _write_set_verdict(leaves=("private/nonexistent-*.json",)) is None
    assert _write_set_verdict(dirs=("private/nonexistent-dir",),
                              recursive=("private/nonexistent-tree",)) is None
    return ("a pattern is refused wherever it names a directory -- in dirs, in "
            "recursive, and in a leaf's directory component")


@case
def case_a_bracket_pattern_is_expanded_like_the_shell_expands_it():
    """`[` is a glob metacharacter to `cp` and to fnmatch, and was not one here.

    A leaf like `private/cache-[0-9].json` was returned literally, so the set the
    guard checked was one name the copy will never write while the names it will
    write went unchecked. That is the understating half of the same defect, and
    it is fixed in the same place: the metacharacter set is fnmatch's own.

    The expansion is read as the (declaration, names) pairs it really is, which
    is what _require_every_declaration_planned() accounts against: a name that
    reached the plan attributed to no declaration would be a destination checked
    on nobody's behalf, and a declaration with no names is the invariant's own
    case above.
    """
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "src"
        (src / "private").mkdir(parents=True)
        (src / "private" / "cache-7.json").touch()
        expanded = PE._expand_leaves(("private/cache-[0-9].json",), ROOT, src)
        assert expanded == [("private/cache-[0-9].json", ["private/cache-7.json"])], (
            expanded)
    return ("a set expression expands to the names it really matches, attributed "
            "to the declaration that named them")


# Every spelling of git pathspec MAGIC that a destination-relative path can carry:
# the two long forms, the short forms for top and exclude, and the `::` separator
# that ends a zero-magic short form. Each is a real, creatable directory name
# inside a worktree, so each is a path a writer can be pointed at.
PATHSPEC_MAGIC = (":(top)private/foo", ":(exclude)data/x", ":(glob)private/x",
                  ":(literal)private/foo", ":!data/x", ":^data/x", "::private/foo")

# Real paths of THIS checkout, in every shape _require_uncommittable is asked
# about: tracked file, tracked directory, ignored directory, ignored leaf, a path
# nothing has created, and the two glob shapes. The `./` transform must give the
# identical git answer for all of them, or it has bought a refusal it cannot pay
# for. `data/*.json` is in here because it is the pattern-literal check: it must
# keep matching the tracked files it matches as a GLOB.
PATHSPEC_EQUIVALENT = ("data", "data/package_results.json", "analysis/rates.py",
                       "index.html", ".gitignore", "private", "private/1-raw-data",
                       "private/household.yaml", "private/nonexistent",
                       "no-such-top-level", "data/*.json", "data/no-such-*.json",
                       "private/*.json", "private/cache-[0-9].json")


@case
def case_a_destination_that_looks_like_pathspec_magic_is_answered_about_itself():
    """FINDING: git read a leading ':' in the destination as PATHSPEC MAGIC, so
    both committability questions were answered about a DIFFERENT path.

        check_destination(ROOT / ':(top)private/foo', kind='file')  ->  ACCEPTED

    `<root>/:(top)private/foo` is a perfectly creatable path, and the ignore rule
    is `private/`, not a directory called `:(top)private` -- so it is one
    `git add -A` from a commit. The guard accepted it because git was asked about
    `private/foo`. `::private/foo` did the same. `:(exclude)...` is the worse
    shape: it turns `git ls-files` into a FILTER, which answers about everything
    EXCEPT the declared path, so the tracked half passed having evaluated
    nothing -- the same class of hole as the two already closed here.

    These paths arrive through --cache-dir and dest_dir= arguments, where a
    leading colon is a typo or a hostile argument and never a real destination.

    THE FIX IS A TRANSFORM, NOT A REFUSAL, and which one was decided by measuring
    git rather than by preference. `git check-ignore` rejects pathspec magic
    outright (`:(literal)x` exits 128), and the global escapes do not help: under
    `--literal-pathspecs` or GIT_LITERAL_PATHSPECS=1 it exits 128 for even a plain
    path, and `--stdin` parses magic identically. `git ls-files` does accept
    `:(literal)`, but forcing it there would silently weaken the zero-match
    pattern check `_expand()` relies on -- `data/*.json` lists 34 tracked files as
    a glob and 0 as a literal. `./` + path is the one spelling that works for
    both: no magic is parsed (it starts with '.') and both commands still resolve
    it as the path it names, so literals stay literal and globs keep globbing.
    Both halves are asserted below rather than described.
    """
    def git_out(cmd, spec):
        r = PE._git(cmd + [spec], ROOT)
        return r.returncode, r.stdout

    # 1. The transform preserves every real verdict, for both commands. This is
    #    what a refusal-based fix could not have done, and it is checked first:
    #    a guard that starts refusing correct input is how guards get removed.
    bad, globbed = [], 0
    for rel in PATHSPEC_EQUIVALENT:
        spec = PE._pathspec(rel)
        assert not spec.startswith(":"), f"_pathspec({rel!r}) still carries magic"
        for cmd in (["ls-files", "--"], ["check-ignore", "-q", "--"]):
            if git_out(cmd, rel) != git_out(cmd, spec):
                bad.append(f"{cmd[0]} disagrees for {rel!r} vs {spec!r}")
        if "*" in rel or "[" in rel:
            globbed += len(git_out(["ls-files", "--"], spec)[1].split())
    assert not bad, bad
    assert globbed > 0, (
        "no leaf pattern matched a tracked file through the transform -- glob "
        "semantics have been lost, and the pattern-literal check with them")

    # 2. The hazard itself, measured: asked bare, git answers about another path.
    #    check-ignore says 'ignored' for a path nothing ignores, and ls-files
    #    with an exclude pathspec answers about everything except the one asked.
    assert git_out(["check-ignore", "-q", "--"], ":(top)private/foo")[0] == 0
    assert git_out(["check-ignore", "-q", "--"], PE._pathspec(":(top)private/foo"))[0] == 1
    assert len(git_out(["ls-files", "--"], ":(exclude)data/x")[1].split()) > 100
    assert git_out(["ls-files", "--"], PE._pathspec(":(exclude)data/x"))[1] == ""

    # 3. The verdicts, through both public doors. Every one of these was either
    #    ACCEPTED or refused for an accidental reason before the fix.
    for spell in PATHSPEC_MAGIC:
        got = _single_verdict(ROOT / spell, "file")
        if got != "not_ignored":
            bad.append(f"check_destination(ROOT / {spell!r}) -> {got or 'ACCEPTED'}")
        for arg in WRITE_SET_PATH_ARGS:
            got = _write_set_verdict(**{arg: (spell,)})
            if got != "not_ignored":
                bad.append(f"check_write_set({arg}=({spell!r},)) -> {got or 'ACCEPTED'}")
    assert not bad, bad

    # 4. The accepting half: a path whose ':' component is real and whose parent
    #    IS ignored stays accepted, because git is now answering about it. This
    #    checkout ignores `*/private/`, so `<root>/:/private/foo` really is
    #    uncommittable -- it was accepted before too, but for the wrong reason.
    assert _single_verdict(ROOT / ":" / "private" / "foo", "file") is None
    return (f"all {len(PATHSPEC_MAGIC)} pathspec-magic spellings are answered "
            f"about the path on disk, and the transform is verdict-identical on "
            f"{len(PATHSPEC_EQUIVALENT)} real paths for both git commands")


@case
def case_a_one_shot_declaration_is_checked_in_full():
    """FINDING: `dirs` and `recursive` were converted to tuples MORE THAN ONCE,
    so a generator was exhausted by the first pass and every later phase iterated
    nothing.

        check_write_set(ROOT, dirs=(x for x in ['data']))  ->  ACCEPTED
        check_write_set(ROOT, dirs=('data',))              ->  REFUSED [tracked_path]

    on the same tracked, committable directory. The call returned a Destination
    having scanned nothing -- the third appearance of one shape, and the first two
    are the cases directly above and below this one.

    Asked of EVERY path-bearing argument, off WRITE_SET_PATH_ARGS, because the
    defect has been in the argument the previous round did not test all three
    times. The consuming half is asserted STRUCTURALLY as well: the materializing
    call happens once per argument and nothing re-converts them, which is the
    property that makes the shape impossible rather than fixed at one site.
    """
    bad = []
    for arg in WRITE_SET_PATH_ARGS:
        for value, expect in (("data", "tracked_path"),
                              ("data/no-such-file.json", "not_ignored")):
            one_shot = _write_set_verdict(**{arg: (x for x in [value])})
            if one_shot != expect:
                bad.append(f"check_write_set({arg}=<generator [{value!r}]>) -> "
                           f"{one_shot or 'ACCEPTED'}, expected {expect} -- the "
                           "same verdict the tuple gets")
        if _write_set_verdict(**{arg: iter(["data"])}) != "tracked_path":
            bad.append(f"{arg}=iter([...]) is not checked like a tuple")
    # All three at once, and the accepting half: one-shot input at paths this
    # checkout really ignores must still be accepted.
    if _write_set_verdict(dirs=(x for x in ["data"]),
                          recursive=(x for x in ["data"]),
                          leaves=(x for x in ["data/x.json"])) != "tracked_path":
        bad.append("three generators together are not checked like three tuples")
    assert not bad, bad
    assert PE.check_write_set(ROOT,
                              dirs=(x for x in ["private/nonexistent-dir"]),
                              recursive=(x for x in ["private/nonexistent-tree"]),
                              leaves=(x for x in ["private/nonexistent-leaf.json"]))

    # A pathlib.Path entry used to crash in _is_pattern (`ch in rel` on a Path)
    # rather than reach any check; a BARE path where the sequence belongs is
    # iterated character by character, so it declares one path and evaluates
    # others -- a TypeError, like an unknown kind, not a verdict about a path.
    assert _write_set_verdict(dirs=(pathlib.Path("data"),)) == "tracked_path"
    for arg in WRITE_SET_PATH_ARGS:
        for value in ("data", pathlib.Path("data"), b"data"):
            try:
                PE.check_write_set(ROOT, **{arg: value})
                bad.append(f"{arg}={value!r} was accepted as a sequence of paths")
            except TypeError:
                pass
            except PE.DestinationRefused as e:
                bad.append(f"{arg}={value!r} produced a verdict ({e.reason}) about "
                           "paths the caller did not name")
    assert not bad, bad

    # Structural: each path argument is materialized exactly once, and nothing in
    # the function re-converts a sequence. A second tuple() is the whole defect.
    tree = ast.parse((ANALYSIS / "private_egress.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_check_write_set")
    materialized = [n.args[0].value for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_paths"
                    and n.args and isinstance(n.args[0], ast.Constant)]
    assert sorted(materialized) == sorted(WRITE_SET_PATH_ARGS), (
        f"_check_write_set() materializes {sorted(materialized)}; the path "
        f"arguments are {sorted(WRITE_SET_PATH_ARGS)} -- one that is not "
        "materialized once at entry can be consumed by the first phase")
    reconverted = [n.lineno for n in ast.walk(fn)
                   if isinstance(n, ast.Call)
                   and getattr(n.func, "id", "") in ("tuple", "list", "sorted", "set")]
    assert not reconverted, (
        f"_check_write_set() re-materializes a sequence at line(s) {reconverted}; "
        "the second conversion of a one-shot iterable is the defect itself")
    return (f"all {len(WRITE_SET_PATH_ARGS)} path arguments are materialized once "
            "and checked in full as one-shot iterables, and a bare path is a "
            "TypeError rather than a verdict")


@case
def case_a_declaration_that_plans_no_destination_is_never_accepted():
    """THE INVARIANT, asserted directly rather than as a fourth one-off case.

    Three review rounds found three instances of ONE shape: a public entry point
    returns SUCCESS having evaluated nothing, because an input was empty,
    unexpanded or consumed rather than absent. `recursive` skipped the
    committability phase; an unlistable glob_source expanded a declared leaf to
    nothing; a one-shot `dirs` was consumed by the first pass.

    Two things close it structurally, and both are checked here.
    _paths() materializes each sequence exactly once (the case above), which
    makes the consumed shape impossible. _require_every_declaration_planned()
    counts what came out the other end: every declaration must yield at least one
    concrete destination, so a declared path that vanished between the argument
    and the checks cannot be accepted -- whatever made it vanish.

    Proved by BREAKING the expansion at that seam, which is the next instance of
    the shape arriving: an expander that returns [], one that silently drops an
    entry, and -- the shape a COUNT cannot see -- one that empties a single
    declaration while its neighbour yields plenty. All three must fail the call
    rather than return a Destination. Deliberately NOT a DestinationRefused: no
    caller can provoke this state, so a refusal code for it would be a verdict
    about a destination that is really a statement about this module's own code.

    THE MIXED SHAPE IS WHY THE ACCOUNTING IS PER DECLARATION. Measured against
    the counting version, whose docstring claimed it caught this:

        _expand -> [] for 'b-*', two names otherwise
        check_write_set(ROOT, leaves=('private/a-*.json', 'private/b-*.json'))
            -> ACCEPTED. Two entries planned for two declarations, so the total
               held while 'private/b-*.json' was never evaluated.
    """
    real = PE._expand_leaves
    real_expand = PE._expand
    leaf = "private/nonexistent-leaf.json"
    assert PE.check_write_set(ROOT, leaves=(leaf, "private/other-leaf.json")), (
        "the control must be accepted, or this case proves nothing")

    def refuses_to_return(label, names=(), **kw):
        try:
            PE.check_write_set(ROOT, **kw)
        except AssertionError as e:
            assert "declared" in str(e), f"{label}: unhelpful invariant message"
            unnamed = [n for n in names if n not in str(e)]
            assert not unnamed, (
                f"{label}: the message does not name the declaration(s) that "
                f"planned nothing ({unnamed}), so it points at the wrong path: {e}")
            return
        except PE.DestinationRefused as e:
            raise AssertionError(f"{label}: refused as {e.reason}, but this is a "
                                 "fact about the module, not about the path")
        raise AssertionError(f"{label}: returned a Destination having planned no "
                             "destination for a declared path")

    try:
        PE._expand_leaves = lambda leaves, root, src: []
        refuses_to_return("an expander that returns nothing", names=(leaf,),
                          leaves=(leaf,))
        PE._expand_leaves = lambda leaves, root, src: list(real(leaves, root, src))[:-1]
        refuses_to_return("an expander that drops one entry",
                          names=("private/other-leaf.json",),
                          leaves=(leaf, "private/other-leaf.json"))
    finally:
        PE._expand_leaves = real
    try:
        # One declaration plans plenty, its neighbour plans nothing: the total
        # holds and the neighbour is never looked at.
        PE._expand = lambda rel, root, src: (
            [] if "b-" in rel else ["private/x1.json", "private/x2.json"])
        refuses_to_return("an expander that empties ONE declaration of several",
                          names=("private/b-*.json",),
                          leaves=("private/a-*.json", "private/b-*.json"),
                          glob_source=str(ROOT))
    finally:
        PE._expand = real_expand
    assert PE.check_write_set(ROOT, leaves=(leaf,)), "the seam was not restored"

    # The invariant must survive `python -O`, which strips the assert STATEMENT.
    # A check whose whole job is to notice that a check stopped running must not
    # be the one thing an optimization flag switches off.
    prog = (f"import sys; sys.path.insert(0, {str(ANALYSIS)!r});"
            "import private_egress as PE;"
            "PE._expand_leaves = lambda leaves, root, src: [];"
            f"PE.check_write_set({str(ROOT)!r}, leaves=({leaf!r},));"
            "print('ACCEPTED')")
    r = subprocess.run([sys.executable, "-O", "-B", "-c", prog],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode != 0 and "ACCEPTED" not in r.stdout, (
        "under python -O the invariant no longer fires:\n" + r.stdout + r.stderr)

    src = inspect.getsource(PE._require_every_declaration_planned)
    assert not [n for n in ast.walk(ast.parse(src.lstrip())) if isinstance(n, ast.Assert)], (
        "the invariant uses the `assert` statement, which -O removes")
    return ("a declared path that plans no destination fails the call instead of "
            "being accepted -- including when a neighbour planned plenty, and "
            "under -O")


@case
def case_a_declaration_that_is_not_worktree_relative_is_refused():
    """FINDING: a declaration git answers about a DIFFERENT path than the one
    named.

    Every declared path becomes a git pathspec through _pathspec(), which
    prefixes './'. So an ABSOLUTE entry became './/etc/passwd', which git
    resolves relative to the worktree -- the tracked/ignored verdict described
    <root>/etc/passwd. Measured before the fix:

        check_write_set(ROOT, leaves=("/etc/passwd",))
            -> REFUSED [not_ignored] -- path: /etc/passwd

    It did not ACCEPT, and the check that stopped it was measured as well: the
    os.path.join in one() leaves an absolute entry absolute, so it falls outside
    the register and _diagnose_outside refuses it `no_such_destination`. But
    that is a SECOND check covering for a wrong answer from the first, and the
    refusal the operator actually reads is the first one, naming a path nobody
    asked about -- the same lesson as asking check-ignore before ls-files. The
    refusal now comes from the entry itself, before git is asked anything.

    Asked of every path-bearing argument and of both spellings, because the
    defect has been in the argument the previous round did not test every time.
    """
    bad = []
    for arg in WRITE_SET_PATH_ARGS:
        for value in ("/etc/passwd", "/", "private/../../elsewhere.json",
                      "../outside.json", ".."):
            got = _write_set_verdict(**{arg: (value,)})
            if got != "unnormalized_path":
                bad.append(f"check_write_set(ROOT, {arg}=({value!r},)) -> "
                           f"{got or 'ACCEPTED'}, expected unnormalized_path")
    assert not bad, bad

    # The refusal must name the entry AS WRITTEN -- pointing at the path the
    # caller declared is the whole difference between this and the old verdict.
    try:
        PE.check_write_set(ROOT, leaves=("/etc/passwd",))
        raise AssertionError("an absolute declaration was accepted")
    except PE.DestinationRefused as e:
        assert e.path == "/etc/passwd", e
        assert os.path.join(str(ROOT), "etc") not in (e.detail or ""), e

    # The accepting half: an ordinary relative declaration, and one whose name
    # merely CONTAINS dots, are untouched.
    assert PE.check_write_set(ROOT, dirs=("private/nonexistent-dir",),
                              recursive=("private/nonexistent-tree",),
                              leaves=("private/..hidden.json",
                                      "private/a..b/c.json")), (
        "a relative declaration with dots in a NAME is not a '..' component")
    return ("an absolute or '..'-bearing declaration is refused in every path "
            "argument, naming the entry the caller wrote")


@case
def case_the_recursive_scan_refuses_a_probe_it_cannot_make():
    """FINDING: `e.stat()` inside the recursive scan raised straight out of both
    public APIs.

    os.scandir() only reads the directory; the per-entry questions go back to the
    kernel and can fail for reasons that are facts about the DESTINATION, not
    bugs here -- PermissionError in a directory that is readable but not
    searchable, FileNotFoundError for an entry unlinked between the listing and
    the stat. Only the listing was inside the handler, so a caller holding this
    module's documented `except DestinationRefused` got an unhandled
    PermissionError instead of a verdict it could act on.

    The real fixture is the permission one, because it needs no injection: a
    subdirectory chmod'ed 0o400 with a file in it. The vanishing entry is a race
    by definition, so it is injected -- and the same injection proves the other
    half of the requirement, that the handler was not widened until it swallows a
    programming error.

    Built inside a THROWAWAY worktree, like every other fixture here, never in
    this checkout's own private/ -- both so the case needs no archive and so it
    is not itself a write into the real archive.
    """
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            probe = wt / "private" / "scan-probe"
            (probe / "sub").mkdir(parents=True)
            (probe / "sub" / "f.txt").write_text("x")
            # The raw error is caught HERE rather than left to escape, so the
            # defect reads as a failing assertion naming what came out instead
            # of aborting the run -- "a raw OSError" IS the verdict under test.
            def verdict(call):
                try:
                    return call() and None
                except PE.DestinationRefused as e:
                    return e.reason
                except OSError as e:
                    return f"raw {type(e).__name__}"

            try:
                os.chmod(probe / "sub", 0o400)   # readable, NOT searchable
                if os.access(probe / "sub" / "f.txt", os.R_OK):
                    raise SkipCase("running as root, or a filesystem ignoring mode")
                for label, call in (
                        ("check_destination(kind='tree')",
                         lambda: PE.check_destination(probe, kind="tree")),
                        ("check_write_set(recursive=...)",
                         lambda: PE.check_write_set(wt, recursive=("private/scan-probe",)))):
                    got = verdict(call)
                    assert got == "scan_unreadable", (
                        f"{label} on a readable-but-unsearchable subdirectory gave "
                        f"{got or 'ACCEPTED'}; this module's contract is that a "
                        "destination problem is a DestinationRefused")
                OBSERVED.add(("tree", "scan_unreadable"))
            finally:
                os.chmod(probe / "sub", 0o700)

    # The injected half. os.scandir is restored in the finally, and the fake
    # answers only for the one directory under test.
    with tempfile.TemporaryDirectory() as td:
        target = pathlib.Path(td) / "tree"
        target.mkdir()

        class Entry:
            def __init__(self, exc, on="stat"):
                self.path, self.name, self._exc, self._on = str(target / "gone"), "gone", exc, on

            def _maybe(self, which):
                if which == self._on:
                    raise self._exc
                return False

            def is_symlink(self):
                return self._maybe("is_symlink")

            def stat(self, follow_symlinks=True):
                self._maybe("stat")
                return os.stat(__file__)

            def is_dir(self, follow_symlinks=True):
                return False

            def is_file(self, follow_symlinks=True):
                return True

        real_scandir = os.scandir

        def fake(path="."):
            if os.path.abspath(path) == str(target):
                return iter([entry])
            return real_scandir(path)

        for entry, expect in (
                (Entry(FileNotFoundError(2, "No such file or directory")), "scan_unreadable"),
                (Entry(PermissionError(13, "Permission denied"), on="is_symlink"), "scan_unreadable"),
                (Entry(TypeError("a bug in this module"), on="is_symlink"), TypeError)):
            os.scandir = fake
            try:
                got = PE._scan_tree(str(target)) or None
            except PE.DestinationRefused as e:
                got = e.reason
            except TypeError:
                got = TypeError
            finally:
                os.scandir = real_scandir
            assert got == expect, (
                f"a {type(entry._exc).__name__} at {entry._on}() gave {got!r}, "
                f"expected {expect!r} -- a programming error must not be reported "
                "as a fact about the destination, and a kernel refusal must not "
                "escape as a raw OSError")
    return ("every per-entry probe in the recursive scan reports scan_unreadable, "
            "and a TypeError from this module still propagates")


@case
def case_the_two_public_apis_differ_only_on_the_root_question():
    """The full comparison, printed: which refusal each destination kind can
    produce, with a written reason for every restriction, and the three
    root-shaped codes proved behaviorally rather than declared.

    Everything the two APIs disagree about is one question -- is this path a
    worktree ROOT -- and the disagreement is deliberate on both sides.
    """
    both = sorted(set(API_REACH) & set(WRITE_SET_ONLY_REASONS))
    assert not both, (
        f"{both} is classified as reachable by a kind AND as reachable by none; "
        "the two tables must partition REASONS, or a code can be described twice "
        "and checked never")
    classified = set(API_REACH) | set(WRITE_SET_ONLY_REASONS)
    assert classified == set(PE.REASONS), (
        "the reach map and the refusal vocabulary have drifted: only in the reach "
        f"tables {sorted(classified - set(PE.REASONS))}, only in REASONS "
        f"{sorted(set(PE.REASONS) - classified)}")
    bad = []
    for code, why in sorted(WRITE_SET_ONLY_REASONS.items()):
        if len(why) < 40:
            bad.append(f"{code}: declared reachable by no kind with no reason given")
        if code not in WRITE_SET_OBSERVED:
            bad.append(f"{code}: declared reachable by no kind and produced by no "
                       "case either -- a refusal nothing exercises")
    for code, (kinds, why) in sorted(API_REACH.items()):
        if not kinds:
            bad.append(f"{code}: reachable through no kind at all")
        if kinds != ALL_KINDS and len(why) < 20:
            bad.append(f"{code}: restricted to {sorted(kinds)} with no reason given")
        unknown = kinds - ALL_KINDS
        if unknown:
            bad.append(f"{code}: names kinds that do not exist: {sorted(unknown)}")
    assert not bad, bad

    # Behavioral, on the three codes the two questions do not share.
    with tempfile.TemporaryDirectory() as td:
        with _worktree(td) as wt:
            missing = wt / "private" / "not-created-yet"
            assert PE.refusal(missing, kind="root") == "no_such_destination"
            assert PE.refusal(missing, kind="dir") is None, (
                "an ordinary destination that does not exist yet must be accepted -- "
                "writers create their own directories")
            assert PE.refusal(wt / "analysis", kind="root") == "not_worktree_root"
            assert PE.refusal(wt / "analysis", kind="dir") == "tracked_path"
            assert PE.refusal(wt, kind="dir") == "worktree_root_itself"
            assert PE.refusal(wt, kind="root") is None
    OBSERVED.update({("root", "no_such_destination"), ("root", "not_worktree_root"),
                     ("dir", "tracked_path"), ("dir", "worktree_root_itself")})

    undeclared = sorted((k, r) for k, r in OBSERVED if r not in API_REACH
                        or k not in API_REACH[r][0])
    assert not undeclared, (
        f"this run produced (kind, reason) pairs the reach map does not allow: "
        f"{undeclared}")
    print("  kind reachability of every refusal (measured where the suite reaches "
          "it, declared otherwise):")
    for code, (kinds, why) in sorted(API_REACH.items()):
        seen = sorted(k for k in PE.KINDS if (k, code) in OBSERVED)
        mark = "all kinds" if kinds == ALL_KINDS else ",".join(
            k for k in PE.KINDS if k in kinds)
        print(f"    {code:<22} {mark:<20} observed={seen or '-'}"
              + (f"  ({why[:60]})" if why else ""))
    for code in sorted(WRITE_SET_ONLY_REASONS):
        print(f"    {code:<22} {'no kind':<20} observed=check_write_set()")
    restricted = {c for c, (k, _) in API_REACH.items() if k != ALL_KINDS}
    return (f"{len(API_REACH)} refusals mapped to the kinds that reach them; "
            f"{len(restricted)} are kind-restricted, each with a stated reason, "
            f"{len(WRITE_SET_ONLY_REASONS)} belong to the whole-set API's own input "
            f"and were produced there, and {len(OBSERVED)} (kind, reason) pairs "
            "were observed and all allowed")


# ===========================================================================
# THE SWEEP: every fact this module obtains from one source and then trusts for
# the rest of the call, and whether the thing it describes can differ from the
# thing that will actually be written.
#
# Every review round on this branch has found the same class in the argument the
# previous round did not test -- a guard reaching a verdict from a fact it
# obtained once and never rechecked, or from an input it never evaluated: an
# unlistable glob_source expanding to [], a pattern read as a literal directory,
# a consumed one-shot iterable, git answering about a different path than the one
# named, and a register entry believed without re-asking the directory. Four of
# the five were found one instance at a time. This table is the enumeration, so
# the next one is found by reading rather than by being shipped.
#
# WHAT IS AND IS NOT CLAIMED. This is not an atomicity claim: every fact below is
# TOCTOU-shaped by construction, because a predicate that returns and a caller
# that then writes are two moments, and the module says so at the top. The
# question each row answers is the narrower one that has actually produced
# defects here -- can this fact describe a DIFFERENT OBJECT than the one that
# will be written, in ORDINARY use, with nobody racing anything? That is what the
# stale register entry was: a fact about a directory that existed last week,
# believed about the directory there today.
#
# `stale` is that answer, `answer` is what was done about it, and `where` names
# the function that obtains the fact. The git rows are ANCHORED: the case below
# derives, from the module's AST, every function that asks git anything, and a
# new git question that is not classified here fails it. The filesystem rows
# cannot be anchored the same way -- os.path is called all over the module and a
# name-based scan would classify nothing -- so they are declared, with the
# function checked to exist so a rename cannot silently empty a row.
# ===========================================================================
ANSWERS = ("recheck", "reorder", "nothing -- it is the authority",
           "nothing -- TOCTOU only", "stated residue")

TRUSTED_FACTS = {
    "which repository this MODULE is in": dict(
        oracle="git", where=("self_common_git_dir", "common_git_dir"),
        about="the checkout the running copy of this file lives in, resolved from "
              "__file__ once per call",
        stale="no. It is not a destination and nothing compares it against a "
              "second object: it IS the object every other question is compared "
              "against. A copy of this module on another path deliberately "
              "authorizes that path's checkout and not this one",
        answer="nothing -- it is the authority",
        proof="case_the_register_is_read_from_this_checkout_not_from_the_destination"),

    "which directories are registered worktrees": dict(
        oracle="git", where=("registered_worktrees",),
        about="git's RECORD of the directories `git worktree add` created. Not a "
              "question asked of any of those directories",
        stale="YES, and this is the finding. git revalidates an entry only when "
              "the directory is gone; a directory deleted without `git worktree "
              "remove` and re-created by another project keeps its entry, so the "
              "register described a worktree of this checkout and the destination "
              "was an unrelated repository",
        answer="recheck",
        proof="case_a_stale_register_entry_cannot_admit_an_unrelated_repository"),

    "the matched entry is STILL a worktree of this checkout": dict(
        oracle="git", where=("_confirm_register_entry",),
        about="the directory the destination resolved into, asked for its own "
              "common dir at the moment of the check",
        stale="no -- it is the recheck. Asked of the matched entry only: a "
              "hijacked entry elsewhere in the register is not a fact about the "
              "destination in hand, and refusing for it would be a refusal the "
              "caller cannot act on",
        answer="recheck",
        proof="case_a_real_registered_worktree_is_still_accepted_through_a_"
              "symlinked_parent"),

    "why a path is in no registered worktree": dict(
        oracle="git", where=("_diagnose_outside",),
        about="the nearest EXISTING ancestor of the destination -- a diagnosis "
              "only, reached after the verdict is already refuse",
        stale="no, but it was once about the wrong object: it asked git about a "
              "path that does not exist and got an answer about git's cwd. It now "
              "asks about the ancestor it names",
        answer="reorder",
        proof="case_a_path_outside_every_registered_worktree_is_refused"),

    "is this path committable in that worktree": dict(
        oracle="git", where=("_require_uncommittable",),
        about="the destination path itself, asked of the worktree that contains "
              "it, through a pathspec that cannot be read as magic",
        stale="the PATH is right; the REPOSITORY answering was not. A hijacked "
              "entry made this question be asked of the foreign repository, which "
              "answered 'ignored' truthfully about a repository this checkout does "
              "not own -- which is how the hijack produced an ACCEPT rather than a "
              "refusal. Fixed upstream of here, by confirming the entry first",
        answer="reorder",
        proof="case_a_committable_destination_is_refused"),

    "does a register entry's directory exist, and where does it resolve": dict(
        oracle="filesystem", where=("_resolve_register", "_physical"),
        about="each entry git listed, resolved at listing time -- the only "
              "question this module asks of a register entry's directory apart "
              "from the recheck above",
        stale="one half only: an entry whose directory is GONE is dropped here, "
              "which is the prunable shape. An entry whose directory exists and "
              "belongs elsewhere resolves perfectly well and is settled by the "
              "recheck above -- the two are different remedies and are refused "
              "with different codes",
        answer="recheck",
        proof="case_a_stale_register_entry_cannot_admit_an_unrelated_repository"),

    "which registered worktree contains the destination": dict(
        oracle="filesystem", where=("_locate_worktree",),
        about="the resolved ancestors of the destination, compared with the "
              "resolved register; the LITERAL prefix is kept so the walk below "
              "still sees links under the root",
        stale="no. Both sides are resolved by one normalizer, so membership no "
              "longer depends on how a path was spelled",
        answer="nothing -- TOCTOU only",
        proof="case_a_supplied_register_is_normalized_the_way_the_read_one_is"),

    "what is at each component of the path": dict(
        oracle="filesystem", where=("_check_destination",),
        about="every component from the worktree root down, lstat'ed as written",
        stale="no, but it was INCOMPLETE: an existing non-final component was "
              "required not to be a link and nothing else, so a regular file or a "
              "FIFO there left the path below it accepted and uncreatable",
        answer="recheck",
        proof="case_a_non_directory_intermediate_component_is_refused"),

    "what is at the leaf": dict(
        oracle="filesystem", where=("_check_leaf",),
        about="the destination itself: link, kind, and link count, from one lstat "
              "plus follow-free predicates on a path already proved not to be a "
              "link",
        stale="no. Every probe is of the leaf, and which probes run is decided by "
              "the caller's declared kind rather than by a default",
        answer="nothing -- TOCTOU only",
        proof="case_a_special_file_and_a_hard_link_are_refused_at_a_leaf"),

    "what is inside a tree a recursive copy will descend": dict(
        oracle="filesystem", where=("_scan_tree",),
        about="the entries under the destination at scan time",
        stale="no -- it describes the tree that will be written, and an entry it "
              "cannot probe is a refusal rather than an assumption",
        answer="nothing -- TOCTOU only",
        proof="case_the_recursive_scan_refuses_a_probe_it_cannot_make"),

    "which leaves a pattern names": dict(
        oracle="filesystem",
        where=("_expand", "_expand_leaves", "_require_listable_source"),
        about="the SOURCE tree, which is not the destination and which no other "
              "check here looks at",
        stale="YES, and it is the one row that stays open: a glob_source that is "
              "not the copy's real source names fewer or other leaves than the "
              "copy will write. It can no longer take a declared pattern out of "
              "the check (an unlistable source is refused, a zero-match pattern is "
              "checked as its literal), so what is left is the residue written "
              "down in _expand(): what sits AT a name the expansion did not yield",
        answer="stated residue",
        proof="case_a_leaf_pattern_whose_source_cannot_be_listed_is_refused"),
}


@case
def case_every_fact_this_module_trusts_is_classified():
    """The standing sweep, made mechanical where it can be.

    Five review rounds have found five instances of one class. Four were found
    one at a time, in the argument the previous round did not test. So the class
    is enumerated here rather than patched again: every fact the module obtains
    from one source and then trusts, whether the thing it describes can differ
    from the thing that will be written, and what was done about it.

    THE ANCHOR is the git half. Every function in the module that asks git
    anything -- directly through _git() or through common_git_dir() -- is derived
    from the AST and must appear in a row above. A new git question is a new
    trusted fact by definition, and it cannot be added without classifying it.

    The two rows whose answer is "recheck" are also checked structurally, because
    a table that says a recheck exists is worth nothing if the recheck was
    deleted: _confirm_register_entry() must be reached from _check_destination()
    and from nowhere else (a second caller means the register is believed on one
    path and re-asked on another), and the component walk must still be able to
    refuse a non-directory. Behavioral proof is a named case per row, checked to
    exist and to be registered -- the same rule the MOVERS census applies to the
    guards it names.

    WHAT NO TEST HERE CAN DO is decide whether a NEW fact is stale. That is a
    human reading a row and writing down what the fact describes; the value of
    the table is that adding a row means writing that sentence under review, and
    a false one is visible next to ten true ones.
    """
    module = ast.parse((ANALYSIS / "private_egress.py").read_text())
    tops = {n.name: n for n in ast.walk(module) if isinstance(n, ast.FunctionDef)}
    names = {c.__name__ for c in CASES}

    bad = []
    for fact, row in sorted(TRUSTED_FACTS.items()):
        for field in ("about", "stale"):
            if len(row.get(field, "")) < 40:
                bad.append(f"{fact}: '{field}' says nothing")
        if row["answer"] not in ANSWERS:
            bad.append(f"{fact}: answer {row['answer']!r} is not one of {ANSWERS}")
        if row["oracle"] not in ("git", "filesystem"):
            bad.append(f"{fact}: unknown oracle {row['oracle']!r}")
        for fn in row["where"]:
            if fn not in tops:
                bad.append(f"{fact}: names {fn}(), which the module does not define")
        if row["proof"] not in names:
            bad.append(f"{fact}: names proof {row['proof']}, which is not a case "
                       "registered in this suite")
    assert not bad, bad

    # THE ANCHOR. Every function that asks git anything is classified above.
    askers = {name for name, fn in tops.items()
              for n in ast.walk(fn)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") in ("_git", "common_git_dir")}
    classified = {fn for row in TRUSTED_FACTS.values() if row["oracle"] == "git"
                  for fn in row["where"]}
    assert askers == classified, (
        f"these functions ask git a question that no TRUSTED_FACTS row classifies: "
        f"{sorted(askers - classified)}; and these are classified as asking git and "
        f"do not: {sorted(classified - askers)}. A fact obtained from git is a fact "
        "trusted for the rest of the call -- say what it describes and whether that "
        "can differ from what will be written")

    # The two rechecks, structurally. A table asserting a recheck exists proves
    # nothing if the recheck has been deleted.
    callers = sorted({name for name, fn in tops.items()
                      for n in ast.walk(fn)
                      if isinstance(n, ast.Call)
                      and getattr(n.func, "id", "") == "_confirm_register_entry"})
    assert callers == ["_check_destination"], (
        f"_confirm_register_entry() is called from {callers}; it must be reached "
        "from the one place every destination goes through, or the register is "
        "re-asked on one path and believed on another")
    walk_refusals = {n.exc.args[0].value for n in ast.walk(tops["_check_destination"])
                     if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
                     and getattr(n.exc.func, "id", "") == "DestinationRefused"
                     and n.exc.args and isinstance(n.exc.args[0], ast.Constant)}
    assert "not_a_directory" in walk_refusals, (
        "_check_destination() can no longer refuse a non-directory component, so "
        "an existing regular file or FIFO above the leaf is walked past again")

    rechecked = sorted(f for f, r in TRUSTED_FACTS.items() if r["answer"] == "recheck")
    print(f"  {len(TRUSTED_FACTS)} trusted facts classified "
          f"({len(askers)} git askers anchored); rechecked: {len(rechecked)}")
    return (f"all {len(TRUSTED_FACTS)} facts this module obtains once are "
            f"classified, every one of the {len(askers)} functions that asks git "
            f"is among them, and both rechecks are wired")


@case
def case_the_vocabulary_describes_every_fact_its_code_is_raised_for():
    """FINDING: REASONS['special_file'] read "a path exists and is neither a
    regular file nor a directory", and _check_leaf() raises that code for
    EXACTLY a directory where a regular file goes.

    REASONS is the module's published contract -- SHELL_HEADLINES maps the
    shell's refusals onto it and the agreement table compares codes, not prose --
    so a description that EXCLUDES one of the two facts its code is raised for
    sends a reader hunting for a FIFO that is not there. API_REACH, which is
    commentary in this file, had the wider meaning right; the authoritative dict
    did not.

    Tied to the fixtures that produce each fact rather than asserted on its own,
    because the claim is 'the description covers what the code is raised for':
    the table's FIFO row and its directory-where-a-file-belongs row both expect
    special_file, and both must stay.
    """
    producing = sorted(c.name for c in TABLE if c.single == "special_file")
    assert len(producing) >= 2, (
        f"only {producing} reaches special_file through check_destination; the "
        "claim below is that ONE code covers two different facts, so both "
        "fixtures have to exist")
    for label, text in (("REASONS", PE.REASONS["special_file"]),
                        ("API_REACH", API_REACH["special_file"][1])):
        assert "directory" in text, (
            f"{label}'s special_file description does not mention a directory, "
            "which is one of the two things it is raised for")
        assert "nor a directory" not in text, (
            f"{label} describes special_file as excluding a directory: {text!r}. "
            "_check_leaf() raises it for exactly a directory in a file slot -- "
            "the description must be as wide as the code, and no wider")
    return (f"special_file's description admits both facts it is raised for, "
            f"produced by {len(producing)} table fixtures")


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


# ===========================================================================
# THE CASES THAT MAY NOT SKIP
#
# A SkipCase used to cost nothing: main() returned 1 for failures only, so a
# skipped case exited 0 and a run that proved nothing was indistinguishable from
# a run that proved everything. That matters here more than in most suites,
# because the preconditions are not the private archive -- which nothing here
# needs -- but GIT ITSELF. _worktree() raises SkipCase on any `git worktree add`
# failure and source_files() on any `git ls-files` failure, so a runner where git
# refuses the checkout ("detected dubious ownership", a checkout-action change, a
# container whose worktree register is unusable) turns the census AND the whole
# agreement table into skips, and the step still goes green. That is the
# false-green-over-an-unexercised-guarantee shape this whole module is written
# against, sitting in its own CI wiring.
#
# NAMED CASES rather than a floor on the count, and the reason is the same one
# MOVERS_FLOOR gives: a count is satisfied by the wrong cases. MEASURED, with
# both seams made to raise SkipCase: 33 of 48 cases still PASS -- the static
# ones, which read this module's AST and its own signatures and never ask git
# anything -- so any floor those 33 can reach on their own is green in exactly
# the run where the guarantee went unexercised. The three below are the
# guarantees the CI step's name claims: discovery really ran over this tree, the
# two implementations really were compared on every fixture, and the
# stale-register recheck really was exercised.
#
# NOT every skippable case. Skipping stays legitimate where the precondition is
# a property of the machine and not of the guarantee: running as root makes the
# unreadable-directory probe meaningless (case_the_recursive_scan_refuses_a_
# probe_it_cannot_make), and it is not here. Adding a case here is a claim that
# there is no environment where its skip is honest.
# ===========================================================================
REQUIRED_CASES = frozenset({
    "case_every_discovered_mover_is_registered",
    "case_the_shell_and_the_python_predicate_agree_on_every_destination",
    "case_a_stale_register_entry_cannot_admit_an_unrelated_repository",
})


@case
def case_every_required_case_is_a_case_this_suite_runs():
    """A floor naming a case that no longer exists is not a floor. Renaming one
    of the three must fail here rather than silently empty the requirement."""
    names = {c.__name__ for c in CASES}
    gone = sorted(REQUIRED_CASES - names)
    assert not gone, (
        f"REQUIRED_CASES names {gone}, which this suite does not define. Rename "
        "the entry in the same commit, or the run can no longer tell whether the "
        "guarantee was exercised")
    return (f"all {len(REQUIRED_CASES)} cases that may not skip are registered in "
            "this suite")


def main():
    ran = skipped = failures = 0
    skips = []
    for c in CASES:
        try:
            msg = c()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {c.__name__} ({e})")
            skips.append((c.__name__, str(e)))
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {c.__name__}: {e}")
            failures += 1
    if skips:
        print(f"\n{'=' * 72}\nSKIPPED, and what each leaves unproven:")
        for name, why in skips:
            print(f"  · {name}\n      {why}")
        print("=" * 72)
    unexercised = sorted(n for n, _ in skips if n in REQUIRED_CASES)
    if unexercised:
        failures += len(unexercised)
        print(f"\nFAIL  the guarantees this suite exists for were not exercised: "
              f"{unexercised} SKIPPED rather than ran. Their preconditions are git "
              "itself -- a usable worktree register and file list -- not the "
              "private archive, so this is a broken run and not a reduced one. "
              "See REQUIRED_CASES.")
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
