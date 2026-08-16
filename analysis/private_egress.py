#!/usr/bin/env python3
"""May private-derived files be written to THIS path? (issue #186)

Every privacy control in this repo guards the GIT boundary: the gitleaks
pre-commit hook, the CI history scan, privacy_tiers.py's staged-content scan.
On 2026-08-13 this household's whole raw archive was written into an unrelated
project's worktree -- a directory that does not gitignore private/ -- and the
run exited 0. Nothing in the repo watched the FILESYSTEM boundary, so nothing
noticed. stage-private-data.sh now enforces the rule in shell (issue #184);
this module is the same rule, callable from Python, for the argument-derived
destinations that are guarded by nothing (generate_report.py's --cache-dir and
--manifest-path, publish.promote_set's dest_dir, the dest_dir= family across
the pipeline scripts).

It is the FILESYSTEM counterpart of llm_providers.py's network gate, and it
deliberately borrows that gate's vocabulary: a single refusal exception,
raised with the first problem found, naming the path -- never a boolean, and
never a silent pass. The two do not share a class: llm_providers.EgressRefused
means "this must not be SENT"; DestinationRefused here means "this must not be
WRITTEN THERE", and a caller that catches one must not accidentally swallow
the other.

THE RULE, matching what stage-private-data.sh already enforces:

  1. the git environment is SANITIZED before any probe. `git rev-parse` answers
     "which repository is this path in" from the environment first and the
     filesystem second, so GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE (and CDPATH,
     which changes what a bare relative path even MEANS) turn every check below
     into an answer the caller supplied. Cleared, not rejected, for the reason
     the shell gives at length: GIT_DIR is exported by every git hook.
  2. the destination lies inside a REGISTERED worktree of THIS checkout.
     Registration is decided against `git worktree list`, resolved from the
     common dir of the checkout THIS FILE lives in -- never from anything the
     destination said. A plain directory holding a one-line `.git` gitfile
     answers --git-common-dir and --show-toplevel exactly like a real worktree;
     a directory restored from a backup of a linked worktree does it by
     accident. Only git's own register tells them apart.
  3. NO PATH COMPONENT AT OR BELOW THE WORKTREE ROOT IS A SYMLINK. Symlinks
     ABOVE the root are fine and must be (on macOS /tmp and /var are links, and
     a legitimate worktree reached through one has to keep working) -- they are
     resolved before the register is consulted. Below the root a link is a route
     back out of the tree that just passed every check, and every writer here
     follows it.
  4. the path is GENUINELY IGNORED there: untracked, and reported ignored by
     that working tree's OWN git. Untracked is asked first and separately,
     because check-ignore consults the index and reports a tracked-though-
     ignored path as "not ignored" -- which would send the operator to edit a
     .gitignore that was already correct.
  5. the thing already at the path is the KIND the caller says it will write.
     A hard link, a FIFO, a device node and a directory-where-a-file-goes are
     each invisible to 1-4: they are inside the tree, they are ignored there,
     and they still send the bytes somewhere else (a second name on the inode,
     a reader on the other end of the pipe) or hang the run. Which checks apply
     is decided by the REQUIRED `kind` argument, never by a default -- see
     check_destination().

WHAT IT DOES NOT DO. It does not open, create or write anything, and it does
not wire itself into any caller: this is the predicate only. Wiring it into the
argument-derived writers is a separate change with its own review (see
analysis/test_private_egress.py's MOVERS registry for what that would touch).
A refusal is raised, never returned as a code the caller can forget to read.

RACE, stated rather than discovered later: the checks here are TOCTOU-shaped --
a link planted between this call and the caller's write is not caught, and
Python's stdlib copy calls have no O_NOFOLLOW to make it atomic. The hazard
this closes is a link planted BEFORE the run (a stale worktree, a leftover
`ln -sfn`), which is the shape the incident and every fixture in #184 had, not
an attacker racing the copy: anyone able to write inside the destination
mid-run already has the archive.

Run directly for a one-path verdict:
    ./.venv/bin/python analysis/private_egress.py <root|tree|dir|file> <path>
"""
import fnmatch
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# The refusal vocabulary. Machine-readable codes, because the agreement table
# in test_private_egress.py compares this module's verdict against
# stage-private-data.sh's REFUSED headline for the same fixture, and comparing
# prose would either be brittle or vacuous. Every code below is reachable by at
# least one case in that table (asserted there, against a floor).
# ---------------------------------------------------------------------------
REASONS = {
    "self_unlocatable":     "this module is not inside a git working tree, so it cannot say "
                            "which checkout's worktrees are eligible",
    "register_unavailable": "this checkout's worktrees could not be listed",
    "unnormalized_path":    "the path contains a '..' component",
    "no_such_destination":  "the destination does not exist, or is not a directory",
    "not_a_worktree":       "the destination is not inside a git working tree",
    "different_repository": "the destination belongs to a different repository",
    "no_worktree_of_its_own": "the destination is a bare repository or a git internal directory",
    "not_registered":       "the destination claims this checkout but appears in no entry of "
                            "its worktree register",
    "not_worktree_root":    "the destination is a subdirectory, not a worktree root",
    "worktree_root_itself": "the destination IS a worktree root, and no checkout ignores "
                            "its own root",
    "symlink_component":    "a path component at or below the worktree root is a symbolic link",
    "not_a_directory":      "a path that must be a directory exists and is not one",
    "special_file":         "a path exists and is neither a regular file nor a directory",
    "hard_link":            "a destination file has more than one name for its inode",
    "symlink_under":        "a directory that will be written into recursively contains a "
                            "symbolic link",
    "special_under":        "a directory that will be written into recursively contains a "
                            "special file",
    "hardlink_under":       "a directory that will be written into recursively contains a "
                            "hard-linked file",
    "scan_unreadable":      "a directory that will be written into recursively could not be "
                            "read, so it could not be cleared",
    "tracked_path":         "the destination's own git TRACKS this path",
    "not_ignored":          "the destination's own git does not ignore this path",
    "ignore_unanswerable":  "the destination could not say whether it ignores this path",
    "tracked_unanswerable": "the destination could not be asked which paths it tracks",
}


class DestinationRefused(Exception):
    """Raised for the first problem found. `reason` is a key of REASONS."""

    def __init__(self, reason, detail, path=None):
        assert reason in REASONS, f"unknown refusal reason {reason!r}"
        self.reason = reason
        self.detail = detail
        self.path = None if path is None else str(path)
        super().__init__(f"REFUSED [{reason}] {detail}"
                         + (f" -- path: {self.path}" if self.path else ""))


class Destination:
    """An accepted destination: where it is, and which worktree owns it."""

    def __init__(self, path, worktree, relpath):
        self.path = str(path)          # the literal absolute path, links unresolved
        self.worktree = str(worktree)  # the registered worktree root, physically resolved
        self.relpath = relpath         # posix, relative to that root

    def __repr__(self):
        return f"Destination({self.path!r}, worktree={self.worktree!r}, relpath={self.relpath!r})"


# ---------------------------------------------------------------------------
# Environment sanitizing. The same list stage-private-data.sh clears, in the
# same two kinds, and test_private_egress.py asserts the two lists are equal by
# parsing the shell's own loop -- a variable added to one side and not the other
# is a build failure, not a divergence somebody notices later.
#
# GIT_CONFIG* is matched by PREFIX because GIT_CONFIG_KEY_<n>/VALUE_<n> are
# unbounded, and config can carry core.worktree -- a working-tree location under
# another name.
#
# Deliberately kept: GIT_EXEC_PATH, GIT_SSH_COMMAND, GIT_TEMPLATE_DIR and the
# like. They say HOW git runs, not which repository it is looking at, and on a
# relocatable install clearing GIT_EXEC_PATH breaks git outright -- turning a
# legitimate call into a refusal for no gain.
# ---------------------------------------------------------------------------
GIT_IDENTITY_VARS = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE",
)
PATH_MEANING_VARS = ("CDPATH",)
GIT_CONFIG_PREFIX = "GIT_CONFIG"


def sanitized_env(env=None):
    """`env` (default os.environ) with every variable that can move git's answer
    -- or change what a relative path means -- removed."""
    src = os.environ if env is None else env
    drop = set(GIT_IDENTITY_VARS) | set(PATH_MEANING_VARS)
    return {k: v for k, v in src.items()
            if k not in drop and not k.startswith(GIT_CONFIG_PREFIX)}


def _git(args, cwd, env=None):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True, env=sanitized_env(env))


def _physical(path):
    """The shell's `(cd -- "$1" && pwd -P)`: an absolute, symlink-resolved path,
    or None when it is not an existing directory."""
    try:
        if not os.path.isdir(path):
            return None
        return os.path.realpath(path)
    except OSError:
        return None


def common_git_dir(path, env=None):
    """`--git-common-dir` of `path`, absolute and symlink-resolved, or None.

    --path-format=absolute is load-bearing: without it rev-parse returns a
    RELATIVE ".git" from a repo root and an ABSOLUTE path from a linked
    worktree, so a naive comparison rejects a legitimate destination. git < 2.31
    has no such flag, so its (already cwd-relative) answer is normalized instead
    of being compared raw.
    """
    if not os.path.isdir(path):
        return None
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path, env)
    if r.returncode != 0:
        r = _git(["rev-parse", "--git-common-dir"], path, env)
        if r.returncode != 0:
            return None
    out = r.stdout.strip()
    if not out:
        return None
    if not os.path.isabs(out):
        out = os.path.join(str(path), out)
    return _physical(out)


def self_common_git_dir(env=None):
    """The common dir of the checkout THIS FILE lives in, or None.

    The reference is this file's own resolved location, exactly as the shell
    takes ${BASH_SOURCE[0]} through its link chain first: which COPY is running
    decides which repository's worktrees are eligible, and a copy of this module
    on some other path must not authorize this checkout's destinations.
    """
    return common_git_dir(str(ROOT), env)


def _parse_worktree_records(text, sep):
    """`git worktree list --porcelain` output -> worktree paths, bare ones dropped.

    Mirrors the shell's _scan_record: `worktree <path>` opens an entry, `bare`
    voids it, an empty record closes it. Read with -z where possible because git
    emits a worktree PATH RAW -- newlines included -- so a line-based parse of a
    path containing one recovers a TRUNCATED PREFIX: not merely a lost entry, an
    INVENTED one, and a genuinely registered destination refused.
    """
    paths, pending, entries = [], None, 0
    def close():
        nonlocal pending
        if pending is not None:
            paths.append(pending)
            pending = None
    for rec in text.split(sep):
        if rec.startswith("worktree "):
            close()
            entries += 1
            pending = rec[len("worktree "):]
        elif rec == "bare":
            pending = None
        elif rec == "":
            close()
    close()
    return paths, entries


def registered_worktrees(common_dir=None, env=None):
    """Every registered worktree of this checkout, physically resolved.

    Returns [] when the register could not be read at all -- callers treat that
    as a refusal, never as "no restrictions". A registered entry whose directory
    no longer exists resolves to nothing and is dropped, so a stale register
    entry cannot admit a destination.

    Falls back to the line-based listing whenever -z yields nothing (a git that
    predates `worktree list -z` writes usage to stderr and nothing to stdout),
    rather than declaring a minimum git version: a refusal a correct caller
    cannot fix is the failure mode this whole module exists to repair.
    """
    if common_dir is None:
        common_dir = self_common_git_dir(env)
    if not common_dir:
        return []
    r = _git(["worktree", "list", "--porcelain", "-z"], common_dir, env)
    paths, entries = ([], 0) if r.returncode != 0 else _parse_worktree_records(r.stdout, "\0")
    if entries == 0:
        r = _git(["worktree", "list", "--porcelain"], common_dir, env)
        if r.returncode == 0:
            paths, entries = _parse_worktree_records(r.stdout, "\n")
    out = []
    for p in paths:
        real = _physical(p)
        if real and real not in out:
            out.append(real)
    return out


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------
def _ancestors(path):
    """`path` and every ancestor, longest first."""
    p = pathlib.PurePath(path)
    out = [str(p)]
    while str(p.parent) != str(p):
        p = p.parent
        out.append(str(p))
    return out


def _nearest_existing(path):
    for a in _ancestors(path):
        if os.path.lexists(a):
            return a
    return None


def _locate_worktree(path, worktrees):
    """(literal prefix, resolved worktree root) of the registered worktree that
    contains `path`, or (None, None).

    Matched by resolving each LITERAL ancestor and comparing against the
    register -- so symlinks above the root are resolved (they must be) while the
    literal prefix is kept, which is what makes the symlink walk below able to
    see a link BELOW the root. The longest match wins, so a worktree nested
    inside another is attributed to the inner one.
    """
    for a in _ancestors(path):          # longest first
        real = _physical(a)
        if real and real in worktrees:
            return a, real
    return None, None


def _diagnose_outside(path, self_git, env):
    """Why `path` is in no registered worktree -- the shell's four distinct
    refusals, kept distinct because their remedies differ."""
    near = _nearest_existing(path)
    if near is None or _physical(near) is None:
        return "no_such_destination", "no existing directory on this path"
    dst_git = common_git_dir(_physical(near), env)
    if dst_git is None:
        return "not_a_worktree", f"{near} is not inside a git repository"
    if dst_git != self_git:
        return ("different_repository",
                f"git common dir {dst_git}, expected {self_git}")
    r = _git(["rev-parse", "--show-toplevel"], _physical(near), env)
    if r.returncode != 0 or not r.stdout.strip() or _physical(r.stdout.strip()) is None:
        return ("no_worktree_of_its_own",
                "a bare repository or a git internal directory")
    return ("not_registered",
            "reports this checkout's common dir but appears in no entry of "
            "'git worktree list' for it -- a .git gitfile can claim membership "
            "a copied or restored directory never had")


KINDS = ("root", "tree", "dir", "file")


def check_destination(path, *, kind, require_ignored=True, worktrees=None, env=None):
    """Raise DestinationRefused unless private-derived files may be written to
    `path`. Returns a Destination on success. Writes nothing, creates nothing.

    `path` need not exist -- writers create their own directories -- but every
    component of it that DOES exist is checked, and the containing worktree must
    exist and be registered.

    `kind` IS REQUIRED, and it has no default on purpose. It says what the
    caller will do with the path, and every check that depends on what is
    already there hangs off it:

      "root"  stage a whole archive INTO this registered worktree root. It must
              BE a root, and the ignore question is not asked of it (no checkout
              ignores its own root); the caller then passes the paths it will
              really write to check_write_set(). stage-private-data.sh's
              question, and the one the agreement table asks both
              implementations.
      "tree"  a directory a RECURSIVE copy will descend. Everything "dir"
              checks, plus a scan of what is already inside it for the three
              ways an existing entry redirects a write.
      "dir"   a directory the caller creates and then writes named leaves into,
              checking each leaf itself (what check_write_set does). It does NOT
              look inside: if the caller will copy a tree in, it must say
              "tree".
      "file"  a regular file the caller creates or overwrites.

    A kind was chosen over an optional `check_leaf=` flag because a flag that
    defaults to the weaker check is a hole a caller falls into silently -- which
    is exactly how the FIFO and hard-link cases reached this API unchecked while
    check_write_set() refused them. An unknown kind is a ValueError, not a
    refusal: a caller that cannot name what it is writing has a bug in itself,
    and must not be able to spell that bug as "accepted".
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    given = pathlib.PurePath(str(path))
    if ".." in given.parts:
        raise DestinationRefused(
            "unnormalized_path",
            "'..' cannot be normalized without following symlinks, so this "
            "module refuses it rather than guess which directory it names",
            path)
    lit = os.path.abspath(str(path))

    # Probed lazily: with `worktrees` supplied (check_write_set asks about a
    # dozen paths in one worktree) the identity question is already settled, and
    # a git subprocess per path buys nothing. It is still asked before any
    # refusal that needs it -- an unlocatable self must never read as "accepted".
    self_git = None
    if worktrees is None:
        self_git = self_common_git_dir(env)
        if not self_git:
            raise DestinationRefused(
                "self_unlocatable",
                f"{__file__} does not resolve into a git working tree, so which "
                "checkout's worktrees are eligible cannot be established", lit)

    if kind == "root" and not os.path.isdir(lit):
        # Refused, never created: a directory that does not exist cannot be a
        # working tree of anything, and "mkdir -p whatever I was handed" is
        # precisely what turned a failed `git worktree add` into a copy of the
        # archive.
        raise DestinationRefused(
            "no_such_destination",
            "not an existing directory" if not os.path.lexists(lit)
            else "exists but is not a directory", lit)

    if worktrees is None:
        worktrees = registered_worktrees(self_git, env)
    if not worktrees:
        raise DestinationRefused(
            "register_unavailable",
            f"'git worktree list' reported nothing for "
            f"{self_git or 'this checkout'}, with and without -z; membership is "
            "unproven, and unproven is refused", lit)

    wt_lit, wt_real = _locate_worktree(lit, worktrees)
    if wt_real is None:
        if self_git is None:
            self_git = self_common_git_dir(env)
        reason, detail = _diagnose_outside(lit, self_git, env)
        raise DestinationRefused(reason, detail, lit)

    if kind == "root" and _physical(lit) != wt_real:
        raise DestinationRefused(
            "not_worktree_root", f"the worktree root here is {wt_real}", lit)

    # Symlink walk, from the worktree root DOWN. `wt_lit` is the literal prefix
    # whose resolution is the root, so everything after it is checked as written
    # rather than as resolved: this is what sees a link planted at
    # <worktree>/private, which realpath would silently follow.
    walk = lit[len(wt_lit):].strip(os.sep)
    cur = wt_lit
    for part in ([] if not walk else walk.split(os.sep)):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise DestinationRefused(
                "symlink_component",
                f"{cur} -> {os.readlink(cur)}; a copy follows it, so the data "
                "would land outside the working tree that just passed every "
                "check", lit)
        if not os.path.lexists(cur):
            break                       # the rest is created by the caller

    relpath = os.path.relpath(lit, wt_lit).replace(os.sep, "/")
    if relpath == ".":
        relpath = ""
    # The worktree ROOT itself is never asked the ignore question: no checkout
    # ignores its own root, so demanding it would refuse every destination.
    # kind="root" callers are asking a different question anyway, and pass the
    # paths they intend to write to check_write_set().
    #
    # Which is why an ORDINARY call naming the root is REFUSED here rather than
    # allowed to skip the same check. An empty relpath used to fall through the
    # `and relpath` guard below and return accepted, so
    # check_destination(<checkout>) -- an argument-derived cache or manifest
    # directory pointed at the checkout root -- said yes to writing
    # private-derived files at a path every one of those files is committable
    # from. It has its own reason because its remedy is its own: name a path
    # INSIDE the tree that the tree ignores.
    if kind != "root" and not relpath:
        raise DestinationRefused(
            "worktree_root_itself",
            "a worktree root is not a place to write private-derived files: its "
            "own git ignores nothing about it, so anything written here is one "
            "'git add -A' from a commit. Name a path inside it that it ignores "
            "-- or, to stage a whole archive into it, ask with kind='root' and "
            "declare the paths to check_write_set()", lit)
    if require_ignored and relpath:
        _require_uncommittable(wt_lit, relpath, env)

    # The leaf, last, and part of the PUBLIC contract rather than of
    # check_write_set() alone. Skipped only for kind="root": the root is
    # resolved before the register is consulted (exactly as the shell resolves
    # DST to DST_REAL), so a symbolic link that NAMES a registered worktree is a
    # legitimate way to say where it is -- links BELOW the root are what the
    # walk above refuses -- and the isdir check above has already settled that
    # it is a directory.
    if kind != "root":
        _check_leaf(lit, "dir" if kind in ("tree", "dir") else "file")
        if kind == "tree":
            _scan_tree(lit)
    return Destination(lit, wt_real, relpath)


def _require_uncommittable(worktree, relpath, env=None):
    """`relpath` must be untracked AND ignored in `worktree`.

    TRACKED first, and the order is the whole point: check-ignore consults the
    index, so a tracked-though-ignored path reports "not ignored" -- answering
    "your .gitignore does not cover this" for a file whose .gitignore covers it
    perfectly well and which is committed anyway, sending the operator to edit
    the wrong file.

    Asked OF THE DESTINATION, because the answer comes from its own .gitignore,
    its info/exclude and its index -- and in the sanitized environment, because
    an inherited GIT_CONFIG could otherwise supply a core.excludesFile that
    manufactures the "ignored" answer.
    """
    r = _git(["ls-files", "--", relpath], worktree, env)
    if r.returncode != 0:
        raise DestinationRefused(
            "tracked_unanswerable",
            f"'git ls-files' failed in {worktree}: {(r.stderr or '').strip()[:200]}",
            os.path.join(worktree, relpath))
    if r.stdout.strip():
        raise DestinationRefused(
            "tracked_path",
            "a tracked file stays in the index whatever .gitignore says, so "
            "writing private data over one puts it straight into the next "
            "commit's diff", os.path.join(worktree, relpath))
    r = _git(["check-ignore", "-q", "--", relpath], worktree, env)
    if r.returncode == 0:
        return
    if r.returncode == 1:
        raise DestinationRefused(
            "not_ignored",
            "that working tree's own git would offer this path to 'git add' -- "
            "the half of the 2026-08-13 incident a repository check cannot see",
            os.path.join(worktree, relpath))
    raise DestinationRefused(
        "ignore_unanswerable",
        f"'git check-ignore' exited {r.returncode} -- neither 'ignored' (0) nor "
        "'not ignored' (1). 'Probably ignored' is not a property to write a "
        "private archive on", os.path.join(worktree, relpath))


# ---------------------------------------------------------------------------
# What is already AT the path
#
# The two below cover the three ways a destination path redirects a write
# without being outside the worktree: a symbolic link sends it elsewhere, a
# second name on the inode rewrites a file elsewhere in place, and a FIFO or
# device node hands the bytes to a process (and blocks the open until something
# reads, so the run hangs with no message). One surface, three checks; keep them
# in step.
#
# Both are reached from check_destination(), driven by its `kind`, so the
# single-path API and check_write_set() ask the same questions of the same path.
# They stay private because `kind` is the way to ask for them: a caller reaching
# past the public entry point gets the walk from the worktree root skipped,
# which is what sees a link planted ABOVE the leaf.
# ---------------------------------------------------------------------------
def _check_leaf(dest, kind):
    """`dest` is a path a caller will create or overwrite. `kind` here is the
    SLOT -- "dir" or "file" -- which check_destination() derives from its own
    four-valued public `kind` ("tree" wants a directory slot too, and then a
    scan). Absence is fine -- the caller creates it."""
    if os.path.islink(dest):
        raise DestinationRefused("symlink_component",
                                 f"{dest} -> {os.readlink(dest)}", dest)
    if not os.path.lexists(dest):
        return
    st = os.lstat(dest)
    if kind == "dir":
        if not os.path.isdir(dest):
            raise DestinationRefused("not_a_directory",
                                     "expected a directory, or a creatable name", dest)
        return
    if os.path.isdir(dest):
        raise DestinationRefused("special_file", "expected a regular file, found a directory", dest)
    if not os.path.isfile(dest):
        raise DestinationRefused(
            "special_file",
            "a FIFO, socket or device node: a copy opens whatever it is handed, "
            "so this run either blocks forever or hands the data to a process", dest)
    if st.st_nlink > 1:
        raise DestinationRefused(
            "hard_link",
            "more than one name for this inode -- a copy truncates and writes "
            "THROUGH it, so every other name, including one outside this "
            "working tree, becomes a copy of this household's private data", dest)


def _scan_tree(root):
    """Refuse a symlink, a special file or a hard-linked file anywhere beneath
    `root` -- the paths a recursive copy writes through. Never follows a link;
    the link itself is the refusal."""
    if not os.path.isdir(root) or os.path.islink(root):
        return
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError as e:
            # Its own reason, not one of the three below: "I could not look" and
            # "I looked and found a link" are different facts, and a guard that
            # reports the second for the first sends the reader hunting for a
            # link that is not there.
            raise DestinationRefused(
                "scan_unreadable", f"could not scan {cur}: {e}", root) from None
        for e in entries:
            if e.is_symlink():
                raise DestinationRefused(
                    "symlink_under",
                    f"{e.path} -> {os.readlink(e.path)}; a recursive copy writes "
                    "THROUGH it, and one pointing at the source archive makes the "
                    "run overwrite the originals", root)
            st = e.stat(follow_symlinks=False)
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
                continue
            if not e.is_file(follow_symlinks=False):
                raise DestinationRefused(
                    "special_under",
                    f"{e.path} is neither a directory nor a regular file", root)
            if st.st_nlink > 1:
                raise DestinationRefused(
                    "hardlink_under",
                    f"{e.path} has more than one name for its inode", root)


def check_write_set(root, *, dirs=(), leaves=(), recursive=(), glob_source=None,
                    env=None):
    """The whole question for a caller that stages a set of paths into `root`.

    `root` must be a registered worktree root (stage-private-data.sh's
    question). `dirs` and `leaves` are worktree-relative paths that must be
    ignored-and-untracked there and must not be a link, a special file or a
    hard link; `recursive` names directories a recursive copy descends, whose
    existing contents are scanned for all three. A `leaves` entry may be a glob
    pattern, expanded against `glob_source` (the SOURCE tree, whose basenames
    are what a copy will actually write) when given, else against `root`.

    Ordered checks first, writes never: this returns a Destination or raises.

    Every path it asks about goes through check_destination() -- the same public
    entry point an argument-derived writer calls -- so a destination cannot pass
    for one API and fail for the other. That was not true before: the leaf
    checks lived here, and the single-path API accepted a FIFO and a hard link.
    """
    dest = check_destination(root, kind="root", require_ignored=False, env=env)
    wts = [dest.worktree]

    def one(rel, kind):
        # check_destination re-walks every component from the worktree root
        # down, which is what sees a link planted ABOVE the leaf: a link at
        # <root>/private is invisible to an lstat of <root>/private/1-raw-data.
        # require_ignored=False because the ignore question is asked once per
        # DECLARED path in the phase above, not again per leaf.
        p = os.path.join(dest.path, rel)
        check_destination(p, kind=kind, require_ignored=False, worktrees=wts, env=env)
        return p

    # In PHASES, not path by path, because the phases answer different questions
    # and a caller reading a refusal wants the first REASON, not the first path:
    # "this tree could commit what you are about to write" is settled for the
    # whole set before any of it is inspected for links. It is also the order
    # stage-private-data.sh checks in, which is what lets the two be compared.
    #
    # The ignore/tracked question is asked of the DECLARED paths, and of a leaf
    # only when no declared directory already covers it: check-ignore and
    # ls-files both answer for a whole subtree, so asking again per leaf would
    # add a second verdict on the same fact and a second way to drift.
    covered = tuple(dirs) + tuple(recursive)
    expanded = [n for rel in leaves for n in _expand(rel, root, glob_source)]
    for rel in dirs:
        _require_uncommittable(dest.path, rel, env)
    for name in expanded:
        if not any(name == d or name.startswith(d + "/") for d in covered):
            _require_uncommittable(dest.path, name, env)
    for rel in dirs:
        one(rel, "dir")
    for rel in recursive:
        one(rel, "tree")
    for name in expanded:
        one(name, "file")
    return dest


def _expand(rel, root, glob_source):
    """A leaf pattern -> the names a copy will really write. A pattern matching
    nothing stays literal and simply names a path that does not exist, which
    every check above treats as absent -- the same way the shell's `cp` glob
    does."""
    if "*" not in rel and "?" not in rel:
        return [rel]
    base = os.path.dirname(rel)
    pat = os.path.basename(rel)
    src = os.path.join(str(glob_source if glob_source else root), base)
    try:
        names = sorted(n for n in os.listdir(src) if fnmatch.fnmatch(n, pat))
    except OSError:
        return []
    return [f"{base}/{n}" if base else n for n in names]


def refusal(path, *, kind, **kw):
    """Non-raising form: the refusal reason code, or None if accepted.

    `kind` is required here too. A convenience wrapper that quietly supplied one
    would be the optional-flag hole again, one layer out.
    """
    try:
        check_destination(path, kind=kind, **kw)
        return None
    except DestinationRefused as e:
        return e.reason


if __name__ == "__main__":   # pragma: no cover - manual smoke check
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        raise SystemExit(f"usage: private_egress.py <{'|'.join(KINDS)}> <destination path>")
    try:
        d = check_destination(sys.argv[2], kind=sys.argv[1])
    except DestinationRefused as exc:
        print(exc)
        raise SystemExit(1)
    print(f"ACCEPTED {d}")
