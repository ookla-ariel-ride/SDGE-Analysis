#!/usr/bin/env python3
"""Transactional multi-artifact publication: promote a staged set or none of it.

A loop of individually atomic os.replace calls is not a transaction: killed
between replacements, it leaves data/ holding files from two different runs,
which is precisely the mixed-generation corruption parse_bills.py went through
five hardening rounds to prevent. This module is that lesson extracted for the
generators that publish several artifacts at once.

Protocol, per parse_bills:
  1. refuse to start while any target has a leftover recovery copy (.bak) --
     after a failed rollback the .bak IS the only good copy, and a retry that
     overwrites it destroys the original permanently;
  2. move each existing target aside to a pid-suffixed .bak, then promote its
     staged replacement;
  3. on ANY failure, restore every displaced target from its .bak
     independently (one failed restore must not abort the others);
  4. delete the .baks only when the full set promoted; if a restore failed,
     KEEP the .baks and name the stale targets in the error.
"""
import os


def promote_set(staged, dest_dir):
    """Atomically-as-a-set promote {name: staged_path} into dest_dir.

    Every staged file must exist and be fully written before calling. Raises on
    failure with the set restored to its pre-call state wherever possible.
    """
    staged = dict(staged)
    for name, src in staged.items():
        if not os.path.exists(src):
            raise SystemExit(f"publish: staged file missing for {name}: {src}")
    for name in staged:
        leftover = _bak_glob(dest_dir, name)
        if leftover:
            raise SystemExit(
                f"publish: leftover recovery copy {leftover[0]} exists -- a prior "
                "publication failed mid-rollback and that .bak is the only good "
                "copy of the original. Recover it by hand before publishing.")

    pid = os.getpid()
    baks = {}
    promoted = []
    try:
        for name, src in staged.items():
            dst = os.path.join(dest_dir, name)
            if os.path.exists(dst):
                bak = f"{dst}.bak{pid}"
                os.replace(dst, bak)
                baks[name] = bak
            os.replace(src, dst)
            promoted.append(name)
    except BaseException:
        stale = []
        for name, bak in baks.items():
            try:
                os.replace(bak, os.path.join(dest_dir, name))
            except OSError:
                stale.append(name)          # keep the .bak: it is the only copy
        if stale:
            raise SystemExit(
                "publish: promotion failed AND restoring the originals failed for "
                f"{stale}; their .bak{pid} files are the only good copies -- do "
                "not delete them")
        raise
    for bak in baks.values():
        os.remove(bak)
    return promoted


def _bak_glob(dest_dir, name):
    prefix = f"{name}.bak"
    try:
        entries = os.listdir(dest_dir)
    except FileNotFoundError:
        return []
    return sorted(os.path.join(dest_dir, e) for e in entries
                  if e.startswith(prefix))
