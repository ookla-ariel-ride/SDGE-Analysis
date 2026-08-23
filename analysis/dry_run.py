#!/usr/bin/env python3
"""dry_run.py -- answer "what would this generator change?" without changing it.

    ./.venv/bin/python analysis/dry_run.py <generator.py> [--check] [-- args...]

WHY THIS EXISTS
    ~40 scripts in analysis/ write artifacts into data/. Until this file the only
    way to ask what one of them WOULD write was to let it write -- run it for
    real and read `git diff` -- or to hand-build a throwaway repo. That makes the
    CLAUDE.md section 9 regeneration gate expensive to check and makes a
    generator hard to test at all.

HOW A SANDBOX CAN CONTAIN A GENERATOR AT ALL
    Generators locate the repo two ways, and a sandbox has to satisfy both:

      ROOT = pathlib.Path(__file__).resolve().parent.parent
          root follows the SCRIPT's location and ignores the CWD entirely.

      _repo_root()   (analysis/carbon_fullyear.py and ~33 siblings)
          "nearest ancestor holding BOTH analysis/ and data/", tried from
          Path.cwd() first, then from Path(__file__).resolve().parent.

    So a sandbox works only if (a) it holds copies of both analysis/ and data/,
    and (b) the generator is executed FROM the sandbox's own copy of the script,
    with the sandbox as CWD. Then `parent.parent` resolves to the sandbox
    (analysis/gen.py -> sandbox) and `_repo_root()` resolves to the sandbox on
    its very first probe (the CWD). Neither idiom has any way to name the real
    repo. That is the whole safety argument, and test_dry_run.py exercises both
    idioms explicitly rather than trusting this paragraph.

    _repo_root()'s own fail-closed behaviour is the backstop: with no ancestor
    holding both directories it raises SystemExit rather than searching wider,
    so a malformed sandbox produces a non-zero exit -- reported here as a
    FAILURE -- instead of silently finding the real data/.

WHAT IS NEVER TOUCHED
    The real data/ is only ever read (hashed before the run, hashed again after,
    and any difference is a loud failure). private/ is likewise only ever read:
    the whole archive is COPIED into the sandbox (19 MB / ~800 files, about
    0.2 s against a 1800 s timeout) and the generator sees only that disposable
    copy, which lives under the 0700 temp dir and is removed by dispose(). The
    copy dereferences symlinks, so nothing inside the sandbox is a path back out
    to the real archive -- including the cwd fixtures (usage.csv, samA.csv,
    samB.csv), which are copied in rather than linked for the same reason, and
    including tracked symlinks, which are seeded by copying their target's
    CONTENT rather than by recreating the link (a tracked symlink with an
    absolute or escaping target would otherwise be a writable path out of the
    sandbox that neither guard sees: hash_tree() skips symlinks and
    stat_manifest() records the link instead of following it). A tracked symlink
    that cannot be dereferenced -- dangling, or pointing at a directory -- is a
    DryRunError, not a skip. A
    generator that writes under private/ therefore truncates a throwaway file,
    and the write is reported like any other sandbox write. private/ is still
    stat-manifested before and after the run: that check is no longer the only
    defence against a write reaching the archive, it is the proof that none
    did.

SILENT NO-OPS ARE FAILURES, NOT "NO CHANGES"
    A dry-run tool that reports "nothing would change" because the generator
    never ran is worse than no tool. Three separate things must hold before this
    file will report a diff at all: the generator exited 0, the sandbox was
    populated (analysis/ and data/ both present and non-empty), and the run left
    a TRACE -- it either WROTE something (every seeded file is stamped with an
    old mtime up front, so any write at all lands newer) or DELETED something
    (visible only in the diff, since _written_since() can inspect only files that
    still exist). Deleting an artifact is a real change -- diff_dirs() classifies
    it as Change("removed", ...) -- so a deletion-only run is reported as that
    removal, not rejected as a no-op. A run with neither a write nor a removal is
    a failure. The write half is the same
    mtime-not-content rule test_scripts_runnable.py already uses, and for the
    same reason: several generators legitimately reproduce the committed bytes,
    so content equality cannot distinguish "reproduced it" from "never opened
    it".

    A sandbox that could not be removed afterwards is a FAILURE too, for the
    same reason: it holds the whole copied private/ archive, so exiting 0 over
    a stranded copy of the raw PII would be exactly the silent success this
    file exists to refuse (CLAUDE.md section 4).

EXIT CODES
    0   ran cleanly (with --check, and nothing would change)
    1   --check, and at least one artifact would change
    2   the dry run itself failed -- never reported as "no changes"
"""
import argparse
import csv
import errno
import fcntl
import filecmp
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

SANDBOX_PREFIX = "sdge-dryrun-"
# A marker file inside every sandbox, flock'd exclusively for the sandbox's
# whole lifetime. _sweep_stale() tries a NON-BLOCKING lock on each candidate's
# marker before removing it: the OS releases a process's flocks the instant it
# exits, crash or not, so a marker that locks cleanly proves nobody is using
# that sandbox any more, and one that refuses WITH EWOULDBLOCK/EAGAIN proves a
# sibling run still is. A marker that fails for any other reason has proved
# neither, and is reported rather than skipped.
# Without this, two overlapping dry_run.py invocations could have the later
# one's sweep delete the earlier one's still-live sandbox out from under it --
# a race this sweep would introduce, not one it fixes (issue #187 follow-up).
# A candidate carrying NO marker is not evidence of abandonment either way: it
# is equally a sibling between mkdtemp() and its own lock, so the sweep reports
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
# dry_run() materialises the two comparison copies of data/ as SIBLINGS of the
# sandbox, named from the sandbox's own name -- so they inherit SANDBOX_PREFIX
# and sit in the same temp dir the sweep scans. Nothing holds them open, so
# they can carry no marker of their own, which makes them indistinguishable
# from an abandoned sandbox by the sweep's own liveness test. They are
# therefore never standalone sweep candidates: their liveness is inferred from
# the OWNING sandbox's marker instead, and they are removed only alongside an
# owner the sweep has already claimed. Kept as one constant so the creator
# (dry_run()) and the sweep cannot drift apart.
COMPARISON_SUFFIXES = ("-baseline", "-head")
# Inputs the documented private/verify sandbox stages next to the scripts; the
# generators look for them in the CWD, not under data/.
CWD_FIXTURES = ("usage.csv", "samA.csv", "samB.csv")
DEFAULT_TIMEOUT = 1800
_MTIME_SENTINEL_AGE = 86400  # seconds; every seeded file is backdated this far


class DryRunError(Exception):
    """The dry run could not be carried out. Never a diff result."""


class MarkerUnreadable(Exception):
    """_lock_marker() could not establish liveness AT ALL: the marker would not
    open (permissions, an I/O error), or flock() failed for a reason other than
    contention. Deliberately NOT the same signal as the None return, which
    means one specific, healthy thing -- a live sibling holds the lock.

    Collapsing the two is the defect this class exists to prevent (issue #187
    AC2). A sweep reading "unreadable" as "in use" skips an abandoned sandbox
    SILENTLY, on this run and every future one, while it holds a full copy of
    private/ -- neither removed nor reported, which is the one
    outcome AC2 forbids. Callers must decide: an owner locking its OWN fresh
    marker treats this as a hard error, a sweep reports the candidate and moves
    on."""


def _discard_marker_temp(tmp_name):
    """Remove a marker that never reached its canonical name (see _lock_marker's
    create=True path). Best effort on purpose: the caller is already raising the
    real failure, and a leftover under this name is invisible to the sweep,
    which matches SANDBOX_MARKER exactly and nothing else."""
    try:
        os.unlink(tmp_name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# repo root -- resolved from the GENERATOR's path, not from this file's, so a
# synthetic repo built by a test can be dry-run exactly like the real one.
# ---------------------------------------------------------------------------
def repo_root_for(target):
    """Nearest ancestor of `target` holding both analysis/ and data/.

    Deliberately the same rule the generators' own _repo_root() applies, minus
    the CWD probe: the answer must depend only on where the script lives, so
    that dry-running a script cannot change meaning with the shell's CWD.
    """
    p = pathlib.Path(target).resolve()
    p = p if p.is_dir() else p.parent
    while True:
        if (p / "analysis").is_dir() and (p / "data").is_dir():
            return p
        if p.parent == p:
            raise DryRunError(
                f"no repo root above {target}: no ancestor holds both analysis/ "
                "and data/. Point this at a generator inside a checkout.")
        p = p.parent


# ---------------------------------------------------------------------------
# hashing / manifests
# ---------------------------------------------------------------------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root):
    """{relative posix path: sha256} for every regular file under `root`."""
    root = pathlib.Path(root)
    out = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            f = pathlib.Path(dirpath) / name
            if f.is_symlink() or not f.is_file():
                continue
            out[f.relative_to(root).as_posix()] = _sha256(f)
    return out


def stat_manifest(root):
    """{relative posix path: (size, mtime_ns)} -- a cheap tamper check.

    Used on private/, where hashing 19 MB of archive on every run would be waste
    but a write still has to be detectable. Symlinks are recorded by their
    target, not followed, so the walk cannot wander outside `root`.
    """
    root = pathlib.Path(root)
    out = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames + dirnames):
            f = pathlib.Path(dirpath) / name
            rel = f.relative_to(root).as_posix()
            try:
                st = f.lstat()
            except OSError:
                out[rel] = ("gone", 0)
                continue
            if f.is_symlink():
                out[rel] = ("link", os.readlink(f))
            elif f.is_file():
                out[rel] = (st.st_size, st.st_mtime_ns)
    return out


# ---------------------------------------------------------------------------
# sandbox construction
# ---------------------------------------------------------------------------
def _git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise DryRunError(f"git {' '.join(args)} failed in {root}: "
                          f"{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def tracked_files(root):
    """Tracked paths, as posix strings. Paths under private/ are excluded here
    because the whole directory is copied in wholesale afterwards (private/
    README.md is tracked, and seeding it first would leave sandbox/private
    already existing when copytree wants to create it)."""
    raw = _git(root, "ls-files", "-z").split("\0")
    out = []
    for rel in raw:
        if not rel:
            continue
        if rel.startswith("../") or ".." in pathlib.PurePosixPath(rel).parts \
                or pathlib.PurePosixPath(rel).is_absolute():
            raise DryRunError(f"refusing to seed an escaping path from git ls-files: {rel!r}")
        if rel == "private" or rel.startswith("private/"):
            continue
        out.append(rel)
    return out


def untracked_data_files(root):
    """Untracked, non-ignored paths under data/, as posix strings.

    `--baseline worktree` means "data/ as it stands on disk", but the sandbox is
    seeded from `git ls-files`, which sees tracked files only -- so without these an
    untracked artifact is in neither the sandbox nor the baseline copy taken from
    it, and a generator that reproduces it BYTE FOR BYTE is reported as an addition
    (issue #152). Seeding them puts the file on BOTH sides, which is what makes an
    exact reproduction a non-change while a real rewrite stays a modification.

    Seeding, rather than copying the real data/ over the baseline: a baseline filled
    from the working tree would hold files the sandbox never got, and every one of
    them would come out as a spurious `removed` -- the same bug with the sign
    flipped.

    `--exclude-standard` keeps ignored scratch (data/.parse_bills.lock) out of the
    baseline, matching gitignored()'s filter on the other side, so an ignored file
    stays out of both. Everything seeded here is backdated with the rest of the
    sandbox in build(), so it cannot be mistaken for a write by _written_since().
    """
    raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z",
               "--", "data").split("\0")
    out = []
    for rel in raw:
        if not rel:
            continue
        parts = pathlib.PurePosixPath(rel).parts
        if rel.startswith("../") or ".." in parts \
                or pathlib.PurePosixPath(rel).is_absolute():
            raise DryRunError(
                f"refusing to seed an escaping path from git ls-files: {rel!r}")
        if not parts or parts[0] != "data":
            continue          # `-- data` should make this unreachable; do not trust it
        out.append(rel)
    return out


def _backdate(root, when):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not (pathlib.Path(dirpath) / d).is_symlink()]
        for name in filenames:
            f = pathlib.Path(dirpath) / name
            if f.is_symlink():
                continue
            os.utime(f, (when, when))


class Sandbox:
    """A throwaway repo-shaped tree outside the checkout.

    Owns nothing inside the real repo: it copies tracked files OUT and copies
    private/ OUT as well, and every write the generator makes lands here. No
    path inside the sandbox leads back to the checkout.
    """

    def __init__(self, root, notes=None):
        self.root = pathlib.Path(root).resolve()
        self.path = None
        self._marker_fd = None
        self.notes = list(notes or [])
        self.n_seeded = 0
        self.sentinel = None
        self.private_before = {}
        self.baseline_dir = None    # set by dry_run() once the baseline is captured

    # -- build ------------------------------------------------------------
    def build(self, extra_files=()):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX)).resolve()
        # A sandbox inside the checkout would defeat the entire point: a
        # generator's walk-up could then find the real root above it.
        if tmp == self.root or self.root in tmp.parents or tmp in self.root.parents:
            shutil.rmtree(tmp, ignore_errors=True)
            raise DryRunError(f"refusing a sandbox entangled with the repo: {tmp}")
        self.path = tmp
        # Lock our own marker BEFORE sweeping: a sweep that ran first could
        # otherwise see this brand-new, still-unmarked directory as unused.
        # An owner has no use for the contention/unreadable distinction: BOTH
        # mean this run cannot hold its own marker, and both must fail exactly
        # as loudly as before. Only the sweep, which judges directories it does
        # not own, needs to tell them apart.
        try:
            self._marker_fd = self._lock_marker(tmp)
        except MarkerUnreadable as e:
            shutil.rmtree(tmp, ignore_errors=True)
            # The directory self.path names no longer exists, so the attribute
            # must stop naming it. Left set, teardown operates on a deleted
            # path -- keep()'s rename raises FileNotFoundError over the top of
            # THIS error and hides the reason the run actually failed. Every
            # exit that removes the sandbox owes this line; the exits below
            # that raise WITHOUT removing it deliberately do not, because there
            # the directory is still there for dry_run()'s finally to dispose.
            self.path = None
            raise DryRunError(
                f"could not lock the sandbox's own marker file: {tmp} -- a "
                "freshly created, uniquely-named sandbox should never fail "
                f"this; refusing rather than running unmarked and sweepable. "
                f"Cause: {e}")
        if self._marker_fd is None:
            shutil.rmtree(tmp, ignore_errors=True)
            self.path = None            # same reason as the branch above
            raise DryRunError(
                f"could not lock the sandbox's own marker file: {tmp} -- a "
                "freshly created, uniquely-named sandbox should never fail "
                "this; refusing rather than running unmarked and sweepable.")
        self._sweep_stale()

        rels = tracked_files(self.root)
        for rel in rels:
            src = self.root / rel
            if not src.exists() and not src.is_symlink():
                continue  # tracked but deleted in the working tree
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                # Dereference rather than recreate the link -- the same decision
                # _copy_private() and _copy_cwd_fixtures() already make. A tracked
                # symlink whose target is absolute (or relative but escaping)
                # would, recreated verbatim, give the generator a writable path to
                # a file OUTSIDE the sandbox, and neither guard would notice:
                # hash_tree() skips symlinks and stat_manifest() records the link
                # without following it. Copying the content leaves no link at all.
                if not src.exists():        # follows: False for a dangling link
                    raise DryRunError(
                        f"tracked symlink {rel} is dangling (-> {os.readlink(src)}); "
                        "refusing to seed a sandbox that cannot dereference it.")
                if src.is_dir():
                    raise DryRunError(
                        f"tracked symlink {rel} points at a directory "
                        f"(-> {os.readlink(src)}); refusing to seed it -- recreating "
                        "the link would open a write path out of the sandbox.")
            shutil.copy2(src, dst)          # follows symlinks: content, never a link
            self.n_seeded += 1
        for rel in extra_files:
            src = self.root / rel
            dst = tmp / rel
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                self.n_seeded += 1
                self.notes.append(f"{rel} is untracked; copied into the sandbox from the working tree")

        for required in ("analysis", "data"):
            d = tmp / required
            if not d.is_dir() or not any(d.iterdir()):
                raise DryRunError(
                    f"sandbox {required}/ is missing or empty -- the generator's "
                    "root walk-up would escape. Refusing to run.")

        self._copy_private()
        self._copy_cwd_fixtures()
        self.private_before = stat_manifest(self.root / "private")

        # Backdate everything so any write at all is visibly newer. Done last so
        # the copies above are already in place.
        self.sentinel = time.time() - _MTIME_SENTINEL_AGE
        _backdate(tmp, self.sentinel)
        return self

    def _copy_private(self):
        """Copy the whole private/ archive into the sandbox.

        A symlink here would be a writable path from the sandbox straight into
        the authoritative raw archive: a generator with a stray write under
        private/ could truncate a bill PDF or the Green Button export, and a
        before/after manifest can only report that afterwards, never undo it. So
        the generator gets a disposable copy instead. symlinks=False matters --
        it dereferences, so a checkout that stages bill PDFs as symlinks does not
        smuggle a route back out into the sandbox.
        """
        real = self.root / "private"
        if not real.is_dir():
            self.notes.append(
                "private/ does not exist in this checkout -- nothing was copied. "
                "Generators needing the raw archive (bill PDFs, Green Button "
                "export, household.yaml) will fail; that is reported as a "
                "failure, not as 'no changes'.")
            return
        dst = self.path / "private"
        t0 = time.time()
        try:
            shutil.copytree(real, dst, symlinks=False,
                            ignore_dangling_symlinks=True)
        except (OSError, shutil.Error) as e:
            # Fail closed. Falling back to a symlink would silently restore the
            # very write path this copy exists to remove.
            raise DryRunError(f"could not copy private/ into the sandbox: {e}")
        self.notes.append(f"private/ copied (not symlinked) from {real} -- the "
                          f"generator sees a disposable copy ({time.time() - t0:.1f}s)")

    def _copy_cwd_fixtures(self):
        """Stage the CWD inputs the same way, and for the same reason: a
        generator that opens usage.csv for writing in its CWD must not be able
        to truncate the real fixture in private/verify/."""
        staged = []
        for name in CWD_FIXTURES:
            src = self.root / "private" / "verify" / name
            if src.is_file():
                try:
                    shutil.copy2(src, self.path / name)
                except OSError as e:
                    raise DryRunError(f"could not copy cwd fixture {name}: {e}")
                staged.append(name)
        if staged:
            self.notes.append("cwd fixtures copied from private/verify/: "
                              + ", ".join(staged))

    # -- teardown ---------------------------------------------------------
    def _safe_to_dispose(self, p):
        """The exact predicate a removal must satisfy: `p` can only ever be a
        directory this process (or a PRIOR run, for _sweep_stale()) created
        under the system temp dir with our own prefix -- never the checkout.
        Shared by dispose() and _sweep_stale() so the sweep can never remove
        anything dispose() itself would refuse."""
        tmpdir = pathlib.Path(tempfile.gettempdir()).resolve()
        return (p.is_absolute() and p.name.startswith(SANDBOX_PREFIX)
                and (tmpdir == p.parent or tmpdir in p.parents)
                and p != self.root and self.root not in p.parents
                and p not in self.root.parents)

    def dispose(self):
        """Remove the sandbox. Guarded so this can only ever delete a directory
        this process created under the system temp dir -- never the checkout.

        Everything inside is now a copy -- tracked symlinks are dereferenced at
        seeding time -- so there is no link out of the sandbox for a delete to
        travel along.

        Raises DryRunError if the path fails that guard; an OSError from rmtree
        propagates. Either way dry_run() reports the leftover as a failure
        rather than exiting 0 over a stranded copy of private/."""
        if self.path is None:
            return
        p = self.path
        if not self._safe_to_dispose(p):
            raise DryRunError(f"refusing to dispose of an unexpected path: {p}")
        # Hold our own marker lock THROUGH the removal, releasing only after:
        # closing it first would open exactly the TOCTOU window this marker
        # exists to close (a sibling's sweep could lock the now-unlocked
        # marker and start using this path a moment before we delete it).
        try:
            shutil.rmtree(p, ignore_errors=False)
        finally:
            if self._marker_fd is not None:
                self._marker_fd.close()
                self._marker_fd = None
        self.path = None

    def keep(self):
        """--keep-sandbox: leave this sandbox on disk on purpose, permanently
        outside the sweep's reach.

        A flock is held only while a process has an open fd to it -- ours
        releases the instant THIS process exits, keep-sandbox or not, which
        makes a merely-unlocked directory indistinguishable from an
        abandoned one. Without this, the very next dry_run.py invocation's
        startup sweep would find this directory's marker unlocked and
        recursively delete a sandbox the CLI just promised to leave in
        place. Renaming it OUT of SANDBOX_PREFIX (never a suffix -- the
        sweep matches on the START of the name) is a structural fix: no
        future sweep, here or in any sibling process, can ever match it by
        name again, so its lock state stops mattering at all. Returns the
        new path."""
        if self.path is None:
            raise DryRunError("keep() called with no sandbox built")
        p = self.path
        if not p.is_dir():
            # Nothing to rename. Saying so as a DryRunError keeps this in the
            # same failure vocabulary as the rest of the module, instead of a
            # bare FileNotFoundError from the rename below.
            raise DryRunError(
                f"keep() called with no sandbox on disk at {p} -- it was "
                "removed by whatever failed before this point")
        kept = p.parent / f"kept-{p.name}"
        p.rename(kept)
        # The COMPARISON_SUFFIXES copies travel WITH the sandbox, for the same
        # reason and by the same mechanism. They are the other half of what
        # --keep-sandbox is for: the kept tree is only diffable against the
        # baseline it was compared to. Renaming them out of SANDBOX_PREFIX too
        # is also what keeps them from becoming permanent litter -- _sweep_stale
        # deliberately never treats a comparison copy as a standalone candidate
        # (it can hold no marker, so its liveness is unknowable), and it reaches
        # them only through an owner it has just removed. Left under the old
        # name, an orphaned `X-baseline` would therefore never be collected by
        # anything, since its owner `X` no longer exists to lead the sweep to it.
        for suffix in COMPARISON_SUFFIXES:
            companion = p.parent / (p.name + suffix)
            if companion.exists():
                companion.rename(kept.parent / (kept.name + suffix))
        if self._marker_fd is not None:
            self._marker_fd.close()
            self._marker_fd = None
        self.path = kept
        return kept

    @staticmethod
    def _lock_marker(sandbox_dir, create=True):
        """Non-blocking-exclusive-lock the marker inside `sandbox_dir`. Three
        outcomes, and the caller MUST be able to tell them apart:

          * the open file object holding the lock -- we won it;
          * None -- CONTENTION, and only contention: flock refused with
            EWOULDBLOCK/EAGAIN, which means a live sibling holds the lock.
            This is the normal, expected, healthy answer for a sweep, and the
            one case it may act on silently;
          * MarkerUnreadable -- liveness could not be established at all: the
            open() failed (permissions, I/O error, or a marker that vanished
            under create=False), or flock() failed with some other errno.

        Returning None for that third case is the issue #187 AC2 defect: a
        genuinely abandoned sandbox whose marker cannot be opened would be read
        as "a sibling has it" and skipped in silence, forever, while holding a
        copy of private/ -- neither removed nor reported.

        `create` decides whether a MISSING marker is brought into existence.
        True (the default) is for a sandbox we own: we are the ones who put the
        marker there. False is mandatory for _sweep_stale(), which inspects
        directories it does NOT own: creating a marker in one and then locking
        the file we just made is a trivially-won lock that proves nothing about
        the owner, and it leaves our litter behind in someone else's directory.
        With create=False a missing marker fails the open, which is now a
        MarkerUnreadable rather than a None -- callers that expect a markerless
        candidate test for the file itself, before calling, and the raise
        covers only the narrow race where it disappears in between.

        A marker this call has to CREATE is published atomically, already
        locked. Making the file under its canonical name and locking it a
        moment later leaves a window -- between the open() and the flock() --
        in which SANDBOX_MARKER exists and is FREE, which is exactly the state
        _sweep_stale() is built to read as "provably abandoned, remove it": a
        sibling sweeping in that instant wins the lock and recursively deletes
        a LIVE sandbox. So the file is built under a unique temporary name,
        flocked THERE, and only then linked onto SANDBOX_MARKER. A flock lives
        on the open file DESCRIPTION, not on the name, so it survives intact,
        and os.link() is both atomic and non-clobbering; the temporary name is
        dropped afterwards either way, and the sweep never sees it, matching
        SANDBOX_MARKER exactly and nothing else. The canonical name therefore
        only ever becomes visible in an already-locked state.

        A marker that is ALREADY THERE is opened and locked exactly as before,
        create or not: there is no publication window to close for a file this
        call did not create, and overwriting a live owner's marker would be a
        far worse bug than the one that closes. os.link() refusing to clobber
        is what makes that split safe rather than a check-then-act race -- if a
        sibling publishes between the test and the link, its marker stands and
        we fall through to locking THAT file, which is where genuine contention
        gets reported. Any other failure to establish our own marker is raised,
        not swallowed: an owner that cannot mark itself must fail loudly rather
        than run on unmarked and sweepable."""
        path = sandbox_dir / SANDBOX_MARKER
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
                    # A private, just-created temporary has nobody to contend
                    # with, but the errno rule holds everywhere: only
                    # EWOULDBLOCK/EAGAIN returns None, and an owner treats that
                    # answer as fatal exactly as it treats a MarkerUnreadable.
                    if e.errno in _LOCK_CONTENTION_ERRNOS:
                        return None
                    raise MarkerUnreadable(
                        f"could not lock {tmp_name}, this run's own new "
                        f"marker for {path}: {e}") from e
                try:
                    os.link(tmp_name, path)
                except OSError as e:
                    fd.close()
                    if e.errno != errno.EEXIST:
                        raise MarkerUnreadable(
                            f"could not publish {tmp_name} as {path}: {e}") from e
                    # A sibling published between the test above and this
                    # link. Its marker stands; fall through and lock THAT one.
                else:
                    return fd     # published already locked, never unlocked
            finally:
                # On success the canonical link keeps the inode -- and this
                # fd's lock with it -- alive; on every failure path there is
                # nothing to keep. Either way the temporary name is finished.
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

    def _sweep_stale(self):
        """Remove sandboxes from PRIOR runs before this one starts (issue #187):
        a hard kill (SIGKILL, or an exception path that escapes dry_run()'s
        `finally`) between mkdtemp() above and dispose() at teardown strands a
        full copy of private/ under a name that already carries SANDBOX_PREFIX
        -- findable, but nothing ever looked. This closes that gap by scanning
        the same temp dir on every subsequent run.

        Reuses _safe_to_dispose() -- the exact predicate dispose() itself is
        bound by -- rather than a second ad hoc rmtree, so this sweep can never
        remove anything dispose() would refuse, and the "must start with our
        prefix and live under the temp dir" safety check is not weakened for
        it. A stale sandbox that cannot be removed (permissions) is reported to
        stderr and left in place: this is about a PRIOR run's leftover, never
        this run's own sandbox, so it must not fail the CURRENT run -- that
        contract belongs to dry_run()'s `finally` block alone, and is
        unchanged here.

        LIVENESS CHECK, before touching anything -- four outcomes, only one of
        which removes anything, and only one of which is silent:
          * marker present and WE CAN LOCK IT -> the owner's flock is gone, so
            the owner is gone: provably abandoned, remove it.
          * marker present and flock refuses with EWOULDBLOCK/EAGAIN -> a live
            sibling really does hold it: leave it alone, silently. This is the
            ONLY silent skip, because it is the only one that has actually
            established liveness.
          * marker present but UNREADABLE (it will not open, or flock fails for
            any other reason) -> liveness was never established. Not removed --
            we cannot prove it is dead -- but reported to stderr naming the path
            and the cause. Silence here was the issue #187 AC2 defect: an
            abandoned copy of private/ that is neither removed nor reported is
            indistinguishable from "nothing to do".
          * NO marker at all -> unknowable, and never removed. A sibling caught
            between its own mkdtemp() and its own _lock_marker() looks exactly
            like this, as does a pre-marker version of this script; the
            candidate is reported to stderr and left in place. The sweep never
            creates a marker to lock (_lock_marker(create=False)) -- locking a
            file we just made ourselves proves nothing about the owner, and it
            would litter a directory we do not own.
        Without this, an overlapping invocation's sweep could delete a SIBLING
        run's still-in-use sandbox, which is a race this sweep would introduce,
        not one it fixes.

        The two COMPARISON_SUFFIXES copies are excluded from candidacy for
        exactly that reason: they carry SANDBOX_PREFIX (they are named from
        their sandbox's own name) and live in this same temp dir, but nothing
        holds them open, so they can hold no marker and the liveness test above
        would read a LIVE run's -baseline as abandoned. The only sound liveness
        signal they have is their owner's marker, so they are removed only via
        _sweep_comparison_copies() below, after the sweep has won that owner's
        lock. Accepted, deliberate limit: a -baseline whose owning sandbox is
        already gone is never swept. That is tolerable because a comparison
        copy holds only committed data/ artifacts -- never private/ -- so it is
        not the exposure issue #187 exists to close."""
        tmpdir = pathlib.Path(tempfile.gettempdir()).resolve()
        try:
            entries = list(tmpdir.iterdir())
        except OSError as e:
            print(f"[stale sandbox sweep skipped: could not list {tmpdir}: {e}]",
                  file=sys.stderr)
            return
        for p in entries:
            if not p.name.startswith(SANDBOX_PREFIX):
                continue
            if p.name.endswith(COMPARISON_SUFFIXES):
                continue  # a sandbox's comparison copy -- swept with its owner, never alone
            # resolve() is the first thing here that touches the filesystem,
            # and a prefix-matching symlink loop makes it raise -- RuntimeError
            # on Python 3.9, OSError(ELOOP) on newer ones, so both are caught.
            # An entry belonging to a PRIOR run must never fail the CURRENT
            # one: that is this sweep's stated contract, and letting this
            # escape would stop every dry run from building until someone
            # cleared $TMPDIR by hand. Report the entry and move to the next.
            try:
                p = p.resolve()
                skip = p == self.path or not p.is_dir() or not self._safe_to_dispose(p)
            except (OSError, RuntimeError) as e:
                print(f"[stale sandbox candidate left in place: {p} -- it could "
                      f"not be resolved, so this run cannot tell what it is. "
                      f"Cause: {e}]", file=sys.stderr)
                continue
            if skip:
                continue
            # A candidate with NO marker is UNKNOWABLE, never abandoned: it is
            # equally a sibling caught between its own mkdtemp() and its own
            # _lock_marker() (the directory already carries SANDBOX_PREFIX but
            # is not marked yet), or a pre-marker version of this script still
            # running under the same prefix. Deleting either destroys a LIVE
            # run's copy of private/. Report it and move on -- issue #187's AC2
            # is satisfied by removing OR reporting a stale sandbox, and a
            # report is the only honest verdict available here.
            if not (p / SANDBOX_MARKER).exists():
                print(f"[stale sandbox candidate left in place: {p} -- it carries "
                      f"no {SANDBOX_MARKER}, so it is indistinguishable from a "
                      "live run that has not marked itself yet; if no dry run is "
                      "in progress this is a prior run's leftover holding a copy "
                      "of private/, and you should delete it by hand]",
                      file=sys.stderr)
                continue
            # A marker we cannot even READ is not a live sibling. Skipping it
            # silently -- which is what collapsing every OSError into None used
            # to do -- leaves an abandoned copy of private/ neither removed nor
            # reported, on this run and every future one (issue #187 AC2).
            # Report it in the same voice as the markerless case above; do NOT
            # remove it, since an unreadable marker is no proof of death either.
            try:
                marker_fd = self._lock_marker(p, create=False)
            except MarkerUnreadable as e:
                print(f"[stale sandbox candidate left in place: {p} -- its "
                      f"{SANDBOX_MARKER} could not be read, so this run cannot "
                      "tell a live sibling from a prior run's leftover holding "
                      "a copy of private/; fix the permissions or delete it by "
                      f"hand once no dry run is in progress. Cause: {e}]",
                      file=sys.stderr)
                continue
            if marker_fd is None:
                continue  # a live sibling holds the lock -- not our leftover to take
            # Hold the lock THROUGH the removal: releasing it first would let a
            # process that is about to legitimately create a sandbox at this
            # exact path acquire the now-unlocked marker and start using it a
            # moment before we delete it out from under them (TOCTOU).
            try:
                shutil.rmtree(p, ignore_errors=False)
            except OSError as e:
                print(f"[stale sandbox not removed: {p} -- a prior run's "
                      "leftover holding a copy of private/ (raw bill PDFs, "
                      "the Green Button export, household.yaml); delete it "
                      f"by hand. Cause: {e}]", file=sys.stderr)
                # Nothing to take back: the sweep never creates a marker (see
                # _lock_marker's `create`), so a candidate it fails to remove is
                # left exactly as it was found, marker and all.
            else:
                self._sweep_comparison_copies(p)
            finally:
                marker_fd.close()

    def _sweep_comparison_copies(self, sandbox_path):
        """Remove the `-baseline`/`-head` siblings of a stranded sandbox the
        sweep has just claimed and removed.

        Winning `sandbox_path`'s marker lock proved its owning run is dead, and
        these copies are named from that sandbox's own name, so they provably
        belong to that same dead run -- the only liveness signal they can ever
        have, since nothing holds them open to carry a marker of their own.
        Guarded by the same _safe_to_dispose() predicate as everything else
        here, and a copy that cannot be removed is a warning, never a failure of
        the current run: it holds committed data/ artifacts only."""
        for suffix in COMPARISON_SUFFIXES:
            copy = sandbox_path.parent / (sandbox_path.name + suffix)
            if not copy.is_dir() or not self._safe_to_dispose(copy):
                continue
            try:
                shutil.rmtree(copy, ignore_errors=False)
            except OSError as e:
                print(f"[stale baseline copy not removed: {copy} -- a prior "
                      f"run's leftover copy of data/. Cause: {e}]",
                      file=sys.stderr)


# ---------------------------------------------------------------------------
# running the generator
# ---------------------------------------------------------------------------
class RunResult:
    def __init__(self, returncode, stdout, stderr, wrote, seconds):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.wrote = wrote          # sandbox-relative posix paths, sorted
        self.seconds = seconds


def _written_since(sandbox_path, sentinel):
    """Sandbox-relative paths whose mtime is newer than the backdate stamp, plus
    anything created after it. Everything in the sandbox is a copy, so the walk
    covers the sandbox's private/ and its cwd fixtures too: a generator that
    overwrites usage.csv or scribbles under private/ shows up here rather than
    being invisible behind a symlink. Nothing seeded into the sandbox is a
    symlink, but any the generator creates itself are skipped -- following those
    would leave the sandbox."""
    cutoff = sentinel + 1.0
    out = []
    for dirpath, dirnames, filenames in os.walk(sandbox_path, followlinks=False):
        dirnames[:] = [d for d in sorted(dirnames)
                       if not (pathlib.Path(dirpath) / d).is_symlink()
                       and d != "__pycache__"]
        for name in sorted(filenames):
            f = pathlib.Path(dirpath) / name
            if f.is_symlink():
                continue
            try:
                if f.stat().st_mtime > cutoff:
                    out.append(f.relative_to(sandbox_path).as_posix())
            except OSError:
                continue
    return out


def run_generator(sandbox, generator_rel, args=(), timeout=DEFAULT_TIMEOUT):
    script = sandbox.path / generator_rel
    if not script.is_file():
        raise DryRunError(f"{generator_rel} is not present in the sandbox at {script}")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)        # never let the real analysis/ be importable
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    t0 = time.time()
    # Pass our marker fd through to the child (issue #187 follow-up): a
    # flock is held by the OPEN FILE DESCRIPTION, not the process, so a
    # child that inherits this fd keeps the sandbox looking "in use" to any
    # sibling's sweep even if THIS parent is SIGKILLed while the child is
    # still running -- exactly the crash shape #187 exists to survive.
    # close_fds defaults to True in Python 3, so without pass_fds the child
    # would get a fresh fd table and the lock would look released the
    # instant the parent's own fd closed, orphaned child or not.
    marker_fds = ((sandbox._marker_fd.fileno(),)
                  if sandbox._marker_fd is not None else ())
    try:
        r = subprocess.run([sys.executable, str(script), *args],
                           cwd=str(sandbox.path), env=env,
                           capture_output=True, text=True, timeout=timeout,
                           pass_fds=marker_fds)
    except subprocess.TimeoutExpired:
        raise DryRunError(f"{generator_rel} timed out after {timeout}s")
    seconds = time.time() - t0
    wrote = _written_since(sandbox.path, sandbox.sentinel)
    return RunResult(r.returncode, r.stdout or "", r.stderr or "", wrote, seconds)


# ---------------------------------------------------------------------------
# diffing
# ---------------------------------------------------------------------------
class Change:
    def __init__(self, kind, path, detail=()):
        self.kind = kind            # "added" | "removed" | "modified"
        self.path = path
        self.detail = list(detail)

    def __repr__(self):
        return f"<Change {self.kind} {self.path}>"


def _describe_json(old_bytes, new_bytes):
    try:
        old = json.loads(old_bytes.decode("utf-8"))
        new = json.loads(new_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return [f"not parseable as JSON on both sides ({e.__class__.__name__}); "
                f"{len(old_bytes)} -> {len(new_bytes)} bytes"]
    if not (isinstance(old, dict) and isinstance(new, dict)):
        same = old == new
        return [f"top level is {type(new).__name__}, not an object; "
                f"values {'equal' if same else 'differ'}; "
                f"{len(old_bytes)} -> {len(new_bytes)} bytes"]
    ko, kn = set(old), set(new)
    lines = []
    if kn - ko:
        lines.append(f"keys added ({len(kn - ko)}): {_join(sorted(kn - ko))}")
    if ko - kn:
        lines.append(f"keys removed ({len(ko - kn)}): {_join(sorted(ko - kn))}")
    dumped = lambda v: json.dumps(v, sort_keys=True, default=str)
    changed = sorted(k for k in ko & kn if dumped(old[k]) != dumped(new[k]))
    if changed:
        lines.append(f"keys changed ({len(changed)}): {_join(changed)}")
    if not lines:
        lines.append("same top-level keys and values, but the bytes differ "
                     "(formatting, key order or float repr)")
    return lines


def _read_rows(raw):
    text = raw.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0] if rows else []
    return header, [tuple(r) for r in rows[1:]]


def _describe_csv(old_bytes, new_bytes, samples=4):
    oh, orows = _read_rows(old_bytes)
    nh, nrows = _read_rows(new_bytes)
    lines = []
    if oh != nh:
        lines.append(f"header changed: {_join(oh)}  ->  {_join(nh)}")
    lines.append(f"rows {len(orows)} -> {len(nrows)}")
    added = _multiset_diff(nrows, _counter(orows))
    removed = _multiset_diff(orows, _counter(nrows))
    if added:
        lines.append(f"rows added ({len(added)}):")
        lines += [f"    + {_join(r, 40, 400)}" for r in added[:samples]]
        if len(added) > samples:
            lines.append(f"    + ... {len(added) - samples} more")
    if removed:
        lines.append(f"rows removed ({len(removed)}):")
        lines += [f"    - {_join(r, 40, 400)}" for r in removed[:samples]]
        if len(removed) > samples:
            lines.append(f"    - ... {len(removed) - samples} more")
    if not added and not removed and oh == nh:
        lines.append("identical rows in a different order, or trailing-whitespace only")
    return lines


def _counter(rows):
    c = {}
    for r in rows:
        c[r] = c.get(r, 0) + 1
    return c


def _multiset_diff(rows, other_counts):
    """Rows present in `rows` beyond their multiplicity in `other_counts`."""
    other = dict(other_counts)
    out = []
    for r in rows:
        if other.get(r, 0) > 0:
            other[r] -= 1
        else:
            out.append(r)
    return out


def _join(items, limit=12, width=140):
    items = list(items)
    shown = ", ".join(str(x) for x in items[:limit])
    if len(items) > limit:
        shown += f", ... (+{len(items) - limit})"
    return shown if len(shown) <= width else shown[:width - 3] + "..."


def describe(path_name, old_bytes, new_bytes):
    if path_name.endswith(".json"):
        return _describe_json(old_bytes, new_bytes)
    if path_name.endswith(".csv"):
        return _describe_csv(old_bytes, new_bytes)
    return [f"binary/other content differs; {len(old_bytes)} -> {len(new_bytes)} bytes"]


def diff_dirs(baseline_dir, candidate_dir, label="data"):
    """Changes that applying `candidate_dir` over `baseline_dir` would make."""
    base, cand = hash_tree(baseline_dir), hash_tree(candidate_dir)
    changes = []
    for rel in sorted(set(base) | set(cand)):
        if rel not in base:
            changes.append(Change("added", f"{label}/{rel}",
                                  [f"new file, {(pathlib.Path(candidate_dir) / rel).stat().st_size} bytes"]))
        elif rel not in cand:
            changes.append(Change("removed", f"{label}/{rel}", ["file would be deleted"]))
        elif base[rel] != cand[rel]:
            old = (pathlib.Path(baseline_dir) / rel).read_bytes()
            new = (pathlib.Path(candidate_dir) / rel).read_bytes()
            changes.append(Change("modified", f"{label}/{rel}", describe(rel, old, new)))
    return changes


def gitignored(root, relpaths):
    """Subset of `relpaths` .gitignore would exclude from the repo anyway.

    Generators leave scratch behind (data/.parse_bills.lock is the publication
    lock); reporting it as "data/ would gain a file" would be false -- git would
    never record it. Ask git rather than pattern-matching here, so the answer
    tracks the committed .gitignore.
    """
    relpaths = [str(p) for p in relpaths]
    if not relpaths:
        return set()
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin"],
                       input="\n".join(relpaths), capture_output=True, text=True)
    if r.returncode not in (0, 1):    # 1 = "none of them are ignored"
        raise DryRunError("git check-ignore failed: " + (r.stderr or "").strip()[:200])
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def head_data_dir(root, dest):
    """Materialise data/ as of HEAD into `dest` (used by --baseline head)."""
    r = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "HEAD", "--", "data"],
                       capture_output=True)
    if r.returncode != 0:
        raise DryRunError("git archive HEAD -- data failed: "
                          + r.stderr.decode("utf-8", "replace").strip()[:300])
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            rel = pathlib.PurePosixPath(m.name)
            if rel.parts[0] != "data" or ".." in rel.parts:
                raise DryRunError(f"unexpected member in git archive: {m.name}")
            out = dest / pathlib.Path(*rel.parts[1:])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(m).read())
    return dest


# ---------------------------------------------------------------------------
# the whole flow
# ---------------------------------------------------------------------------
class DryRunReport:
    def __init__(self):
        self.root = None
        self.sandbox_path = None
        self.notes = []
        self.result = None
        self.changes = []
        self.cwd_outputs = []       # (name, True | Change) per cwd artifact: True when
                                    # identical to data/<name>, a "modified" Change when it
                                    # differs, an "added" Change when this run wrote a cwd
                                    # file data/ has no counterpart for
        self.dirty_baseline = []
        self.ignored = []           # sandbox scratch .gitignore would exclude
        self.failure = None

    @property
    def would_change(self):
        return bool(self.changes) or any(c is not True for _, c in self.cwd_outputs)


def dry_run(generator, args=(), baseline="worktree", keep_sandbox=False,
            timeout=DEFAULT_TIMEOUT, on_built=None):
    """Run `generator` in a sandbox and report what it would change in data/.

    `on_built(sandbox)` is a hook the test suite uses to perturb sandbox inputs
    after the sandbox is seeded but before the generator runs; production callers
    leave it None.

    Teardown is part of the result: failing to remove the sandbox sets
    rep.failure (it holds the copied private/ archive), while failing to remove
    the -baseline/-head copy of data/ is only a warning on stderr. With
    keep_sandbox the sandbox is left on purpose and neither applies.
    """
    rep = DryRunReport()
    gen_path = pathlib.Path(generator).resolve()
    if not gen_path.is_file():
        raise DryRunError(f"no such generator: {generator}")
    root = repo_root_for(gen_path)
    rep.root = root
    try:
        rel = gen_path.relative_to(root).as_posix()
    except ValueError:
        raise DryRunError(f"{gen_path} is not inside its own repo root {root}")

    real_data_before = hash_tree(root / "data")

    sb = Sandbox(root)
    try:
        # The generator itself may be untracked (a brand-new script), and under the
        # worktree baseline so may artifacts under data/. `head` is deliberately
        # excluded: it is defined against HEAD, where an untracked working-tree file
        # legitimately has no counterpart, and seeding one there would put it in the
        # sandbox but not in the HEAD baseline -- inventing an addition the generator
        # never made (issue #152).
        extra = [rel]
        if baseline != "head":
            extra += untracked_data_files(root)
        sb.build(extra_files=extra)
        rep.sandbox_path = sb.path
        rep.notes = list(sb.notes)

        # Named from the sandbox's own name (see COMPARISON_SUFFIXES): the two
        # suffixes here and the teardown/sweep that clean them up must agree.
        if baseline == "head":
            base_dir = head_data_dir(root, sb.path.parent / (sb.path.name + "-head"))
        else:
            base_dir = sb.path.parent / (sb.path.name + "-baseline")
            shutil.copytree(sb.path / "data", base_dir)
            porcelain = _git(root, "status", "--porcelain", "--", "data").strip()
            rep.dirty_baseline = [ln.strip() for ln in porcelain.splitlines() if ln.strip()]

        sb.baseline_dir = base_dir
        if on_built is not None:
            on_built(sb)

        rep.result = run_generator(sb, rel, args=args, timeout=timeout)

        # --- the guarantees, checked before any diff is believed ------------
        after = hash_tree(root / "data")
        if after != real_data_before:
            changed = sorted(k for k in set(after) | set(real_data_before)
                             if after.get(k) != real_data_before.get(k))
            rep.failure = ("THE REAL data/ CHANGED DURING A DRY RUN -- this must "
                           f"never happen. Affected: {_join(changed)}")
            return rep
        priv_after = stat_manifest(root / "private")
        if priv_after != sb.private_before:
            moved = sorted(k for k in set(priv_after) | set(sb.private_before)
                           if priv_after.get(k) != sb.private_before.get(k))
            rep.failure = ("the real private/ archive changed during a dry run -- "
                           "the sandbox holds only a copy, so nothing it ran should "
                           f"have been able to reach it. Affected: {_join(moved)}")
            return rep
        if rep.result.returncode != 0:
            tail = (rep.result.stderr.strip() or rep.result.stdout.strip()
                    or "(no output)").splitlines()[-6:]
            rep.failure = (f"{rel} exited {rep.result.returncode}; NOT reporting "
                           "'no changes'.\n      " + "\n      ".join(tail))
            return rep

        # --- what would change ---------------------------------------------
        # The diff is computed BEFORE the did-it-actually-run guard, on purpose.
        # _written_since() can only see files that still exist, so a generator
        # that exits 0 having only DELETED an artifact leaves an empty write set
        # -- and a deletion is a real change the diff engine already classifies
        # (Change("removed", ...)), so it must be reported, not refused.
        # The diff is the only evidence that separates that from a generator
        # that never ran, so it has to exist before the guard can judge. The
        # guard is not weakened by the reordering: it still fires whenever there
        # is NEITHER a write NOR a removal, and it still returns before
        # rep.changes is populated, so a failed run never publishes a diff.
        changes = diff_dirs(base_dir, sb.path / "data")
        if not rep.result.wrote and not any(c.kind == "removed" for c in changes):
            rep.failure = (f"{rel} exited 0 but wrote nothing and deleted nothing "
                           "in the sandbox. A generator that produces no output is "
                           "a silent no-op, not a clean dry run.")
            return rep
        ignored = gitignored(root, [c.path for c in changes if c.kind == "added"])
        rep.changes = [c for c in changes if c.path not in ignored]
        rep.ignored = sorted(ignored)
        rep.cwd_outputs = _cwd_output_diffs(sb, base_dir, rep.result.wrote)
        return rep
    finally:
        # `sb.path` is None when build() never got far enough to make one --
        # mkdtemp failing, the temp dir refused as entangled with the checkout,
        # the marker unlockable. keep() would then raise over the top of that
        # real setup error and hide it, so there is nothing to keep and nothing
        # to say: let the original exception out unchanged.
        # `sb.path` naming a directory that is no longer THERE is the same
        # failure wearing a different mask: a build() exit that removed its own
        # sandbox is supposed to clear the attribute (both marker branches do),
        # but this guard must not depend on every present and future exit
        # remembering to. Check the disk, not just the attribute.
        if keep_sandbox and sb.path is not None and sb.path.is_dir():
            kept_path = sb.keep()
            rep.sandbox_path = kept_path  # the pre-rename path no longer exists
            print(f"[sandbox kept] {kept_path}", file=sys.stderr)
        elif keep_sandbox and sb.path is not None:
            print(f"[sandbox not kept] {sb.path} no longer exists, so there is "
                  "nothing to keep -- the setup failure that removed it is the "
                  "error to read.", file=sys.stderr)
        else:
            # Two INDEPENDENT cleanups, deliberately asymmetric. The
            # -baseline/-head copies hold nothing but committed data/ artifacts,
            # so a leftover is untidy and gets a warning. The sandbox itself
            # holds the whole copied private/ archive -- raw bills, the Green
            # Button export, household.yaml -- so a leftover is a dry-run
            # FAILURE (CLAUDE.md section 4): reported on `rep` like every other
            # failure, which is what makes the CLI exit 2 instead of printing a
            # clean verdict over a stranded copy of the PII. They cannot share a
            # try: a baseline rmtree that raised would then skip dispose() and
            # strand exactly that copy.
            sandbox_path = sb.path
            for extra in (str(sandbox_path) + s for s in COMPARISON_SUFFIXES):
                try:
                    if pathlib.Path(extra).is_dir():
                        shutil.rmtree(extra)
                except OSError as e:
                    print(f"[baseline copy not removed: {extra}: {e}]", file=sys.stderr)
            try:
                sb.dispose()
            except (DryRunError, OSError) as e:
                msg = (f"the sandbox could not be removed: {sandbox_path} -- it holds "
                       "a full copy of private/ (raw bill PDFs, the Green Button "
                       "export, household.yaml). Delete it by hand. Cause: " + str(e))
                print(f"[sandbox not removed: {msg}]", file=sys.stderr)
                # rep is the object already being returned, so mutating it here
                # is what surfaces this on the report.
                rep.failure = msg if rep.failure is None else rep.failure + "\n      " + msg


def _cwd_output_diffs(sb, base_dir, wrote=()):
    """Several generators write their artifact into the CWD and the repo commits
    it under data/ (behavior_rebuild.json, deep_results.json, ...). CLAUDE.md's
    section 9 gate compares exactly those with `cmp`; do the same here.

    A CWD output with no counterpart under data/ is an ADDITION -- a new artifact
    would appear -- and dropping it would let the CLI print "nothing would
    change" over one. But the sandbox root is the whole repo root: it also holds
    every tracked root-level file (README.md, index.html, ...) and the seeded cwd
    fixtures (usage.csv, samA.csv, samB.csv), almost none of which have a data/
    counterpart. So the discriminator is `wrote` -- the run's own sandbox-root
    writes (entries with no "/" in them), which is exactly the set this run
    produced, because every seeded file is backdated before the generator
    starts."""
    written_here = {p for p in wrote if "/" not in p}
    out = []
    for rel in sb.path.iterdir():
        if not rel.is_file() or rel.is_symlink():
            continue
        counterpart = base_dir / rel.name
        if not counterpart.is_file():
            if rel.name in written_here:
                out.append((rel.name, Change("added", f"data/{rel.name}",
                                             [f"new file, {rel.stat().st_size} bytes; "
                                              "no counterpart in the baseline data/"])))
            continue
        if filecmp.cmp(rel, counterpart, shallow=False):
            out.append((rel.name, True))
        else:
            out.append((rel.name, Change("modified", f"data/{rel.name}",
                                         describe(rel.name, counterpart.read_bytes(),
                                                  rel.read_bytes()))))
    return sorted(out, key=lambda t: t[0])


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def render(rep, generator, verbose=False):
    L = []
    add = L.append
    add(f"dry run: {generator}")
    add(f"  repo root : {rep.root}")
    add(f"  sandbox   : {rep.sandbox_path}")
    for n in rep.notes:
        add(f"  note      : {n}")
    if rep.dirty_baseline:
        add("  baseline  : the working tree's data/ (NOT HEAD) -- these paths are "
            "uncommitted:")
        for ln in rep.dirty_baseline[:10]:
            add(f"              {ln}")
        if any(ln.startswith("??") for ln in rep.dirty_baseline):
            # Both kinds are in the baseline with their working-tree content: a
            # MODIFIED tracked file because Sandbox.build() seeds from the working
            # tree rather than from HEAD, an UNTRACKED one because
            # untracked_data_files() seeds it too (issue #152). Say so -- a reader
            # who is told the baseline is "the working tree" needs to know that
            # covers the ?? lines printed right above, since it once did not.
            add("              (?? paths are untracked; they are seeded into the "
                "sandbox too, so this diff covers them like any tracked file)")
    if rep.result is not None:
        add(f"  exit code : {rep.result.returncode}   ({rep.result.seconds:.1f}s, "
            f"wrote {len(rep.result.wrote)} file(s) in the sandbox)")
    if rep.failure:
        add("")
        add(f"FAILED: {rep.failure}")
        if verbose and rep.result is not None:
            add("--- stdout ---")
            add(rep.result.stdout[-4000:])
            add("--- stderr ---")
            add(rep.result.stderr[-4000:])
        return "\n".join(L)

    if verbose and rep.result is not None and rep.result.stdout.strip():
        add("--- generator stdout ---")
        add(rep.result.stdout.rstrip()[-8000:])
        add("--- end stdout ---")

    add("")
    if rep.changes:
        counts = {}
        for c in rep.changes:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        add(f"WOULD CHANGE data/  ({summary})")
        for c in rep.changes:
            sig = {"added": "+", "removed": "-", "modified": "~"}[c.kind]
            add(f"  {sig} {c.path}")
            for line in c.detail:
                add(f"      {line}")
    else:
        add("WOULD CHANGE data/  (nothing)")
    if rep.ignored:
        add(f"  (ignored scratch, not a repo change: {_join(rep.ignored)})")

    cwd_changed = [(n, c) for n, c in rep.cwd_outputs if c is not True]
    if rep.cwd_outputs:
        add("")
        add("CWD artifacts compared with data/  (the section 9 `cmp` gate)")
        for name, c in rep.cwd_outputs:
            if c is True:
                add(f"  = {name}  (identical to data/{name})")
                continue
            if c.kind == "added":
                add(f"  + {name}  (new; there is no data/{name} to compare with)")
            else:
                add(f"  ~ {name}  (differs from data/{name})")
            for line in c.detail:
                add(f"      {line}")
    add("")
    total = len(rep.changes) + len(cwd_changed)
    add("VERDICT: nothing would change." if total == 0
        else f"VERDICT: {total} artifact(s) would change.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dry_run.py",
        description="Run a data/ generator in a throwaway sandbox and report what "
                    "it WOULD change. Writes nothing into the repo.")
    ap.add_argument("generator", help="path to a generator, e.g. analysis/parse_bills.py")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any artifact would change (for the section 9 gate)")
    ap.add_argument("--baseline", choices=("worktree", "head"), default="worktree",
                    help="compare against data/ as it is on disk (default) or as of HEAD")
    ap.add_argument("--keep-sandbox", action="store_true",
                    help="leave the sandbox on disk and print its path")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="echo the generator's own stdout/stderr")
    ap.add_argument("rest", nargs="*",
                    help="arguments passed through to the generator (put them after --)")
    ns = ap.parse_args(argv)

    try:
        rep = dry_run(ns.generator, args=ns.rest, baseline=ns.baseline,
                      keep_sandbox=ns.keep_sandbox, timeout=ns.timeout)
    except DryRunError as e:
        print(f"dry run FAILED: {e}", file=sys.stderr)
        return 2
    except Exception as e:                       # noqa: BLE001 -- see below
        # Anything else is still the dry run failing, and it must exit 2 like
        # every other failure. Letting it propagate would exit 1, which is
        # --check's "an artifact would change" -- so a tool that ran out of disk
        # copying the sandbox would be read by a gate as a stale artifact. The
        # copies this makes (the tracked tree, 19 MB of private/, a second data/)
        # make OSError a realistic way to get here, not a theoretical one.
        traceback.print_exc()
        print(f"dry run FAILED: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2
    print(render(rep, ns.generator, verbose=ns.verbose))
    if rep.failure:
        return 2
    if ns.check and rep.would_change:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
