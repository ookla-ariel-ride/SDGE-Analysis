#!/usr/bin/env python3
"""Crash-consistent multi-artifact publication for git-tracked data/ artifacts.

A loop of individually atomic os.replace calls is not a transaction: killed
between replacements, it leaves data/ holding files from two different runs,
which is precisely the mixed-generation corruption parse_bills.py went through
five hardening rounds to prevent. This module is that lesson extracted for the
generators that publish several artifacts at once, and it follows the same
protocol and makes the same, precisely stated, guarantee.

What IS guaranteed:
  * single-writer: an exclusive flock serializes publishers, so two concurrent
    runs cannot interleave promotions or consume each other's recovery copies
    (the kernel drops the lock if a process dies -- no stale-lock state);
  * no truncation: targets are only ever whole files, staged fully first;
  * recoverability: on any Python-level failure the pre-call set is restored --
    displaced originals from their .bak copies, and targets that did not exist
    before are removed again; on an abrupt kill mid-promotion the .bak recovery
    copies survive on disk, and the next run REFUSES to start until a human
    recovers them, because after a failed rollback they are the only good copy.

What is NOT guaranteed, deliberately: reader isolation. A reader overlapping
the promotion window can observe a mixed set for its duration. Fixing that
needs a versioned directory behind an atomically swapped pointer, which does
not fit a git-tracked data/ whose section 9 gates diff files in place; this
repo is single-operator, the same trade parse_bills made, and the lock plus
byte-diff gates make the window harmless in practice.
"""
import fcntl
import os

LOCK_NAME = ".publish.lock"


def promote_set(staged, dest_dir):
    """Promote {name: staged_path} into dest_dir with the guarantees above.

    Every staged file must exist and be fully written before calling. Raises on
    failure with the set restored to its pre-call state wherever possible.
    """
    staged = dict(staged)
    for name, src in staged.items():
        if not os.path.exists(src):
            raise SystemExit(f"publish: staged file missing for {name}: {src}")

    lock = open(os.path.join(dest_dir, LOCK_NAME), "w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(
                f"publish: another publication into {dest_dir} holds the lock; "
                "runs are serialized so they cannot consume each other's staging "
                "or recovery files -- wait for it and re-run.")
        return _promote_locked(staged, dest_dir)
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


def _promote_locked(staged, dest_dir):
    for name in staged:
        leftover = _bak_glob(dest_dir, name)
        if leftover:
            raise SystemExit(
                f"publish: leftover recovery copy {leftover[0]} exists -- a prior "
                "publication failed mid-rollback and that .bak is the only good "
                "copy of the original. Recover it by hand before publishing.")

    pid = os.getpid()
    baks = {}         # displaced originals: name -> .bak path
    created = []      # targets that did NOT exist before this run
    try:
        for name, src in staged.items():
            dst = os.path.join(dest_dir, name)
            if os.path.exists(dst):
                bak = f"{dst}.bak{pid}"
                os.replace(dst, bak)
                baks[name] = bak
            else:
                created.append(name)
            os.replace(src, dst)
    except BaseException:
        # Every recovery action is attempted independently: one failure must not
        # abort the rest, or a single bad os.remove would strand the displaced
        # originals in their .baks while masking the promotion failure itself.
        undeleted = []
        # a target promoted into existence this run has no original to restore;
        # returning to the pre-call state means removing it again
        for name in created:
            try:
                os.remove(os.path.join(dest_dir, name))
            except FileNotFoundError:
                pass
            except OSError:
                undeleted.append(name)
        stale = []
        for name, bak in baks.items():
            try:
                os.replace(bak, os.path.join(dest_dir, name))
            except OSError:
                stale.append(name)          # keep the .bak: it is the only copy
        if stale or undeleted:
            raise SystemExit(
                "publish: promotion failed AND recovery was incomplete -- "
                + (f"originals not restored for {stale} (their .bak{pid} files "
                   "are the only good copies, do not delete them)" if stale else "")
                + ("; " if stale and undeleted else "")
                + (f"created targets not removed: {undeleted}" if undeleted else ""))
        raise
    for bak in baks.values():
        os.remove(bak)
    return sorted(staged)


def _bak_glob(dest_dir, name):
    prefix = f"{name}.bak"
    try:
        entries = os.listdir(dest_dir)
    except FileNotFoundError:
        return []
    return sorted(os.path.join(dest_dir, e) for e in entries
                  if e.startswith(prefix))
