#!/usr/bin/env python3
"""Failure-injection guards for the transactional set publication.

The property under test: a reader of dest_dir never observes a mixed-generation
set. Either every artifact is from the new run, or every artifact is the
original. The injection point walks THROUGH the promotion loop -- failing after
the first replace, after the second, and so on -- because failing before the
first promotion is the easy case and proves nothing about partial commit.

No private data; runs in CI.

Run from the repo root:  ./.venv/bin/python analysis/test_publish.py
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish

NAMES = ("a.csv", "b.csv", "c.csv", "d.json")


def _setup(td):
    dest = pathlib.Path(td) / "data"
    dest.mkdir()
    stage = pathlib.Path(td) / "stage"
    stage.mkdir()
    staged = {}
    for n in NAMES:
        (dest / n).write_text(f"OLD {n}\n")
        p = stage / (n + ".tmp")
        p.write_text(f"NEW {n}\n")
        staged[n] = str(p)
    return dest, staged


def _generation(dest):
    gens = {(dest / n).read_text().split()[0] for n in NAMES}
    return gens


def case_success_promotes_the_full_set():
    with tempfile.TemporaryDirectory() as td:
        dest, staged = _setup(td)
        done = publish.promote_set(staged, str(dest))
        assert sorted(done) == sorted(NAMES)
        assert _generation(dest) == {"NEW"}
        assert not list(dest.glob("*.bak*")), "recovery copies left after success"
    return "a clean promotion lands the full set and leaves no recovery copies"


def case_failure_at_every_promotion_point_leaves_no_mixed_set():
    """Inject a failure after k successful replacements, for every k."""
    for fail_at in range(1, len(NAMES) + 1):
        with tempfile.TemporaryDirectory() as td:
            dest, staged = _setup(td)
            real_replace = os.replace
            calls = {"n": 0}

            def flaky(src, dst, _real=real_replace, _calls=calls, _fail=fail_at):
                # every promotion is two replaces (aside + promote); fail on the
                # promote replace of artifact number `_fail`
                if str(dst).endswith(tuple(NAMES)) and not str(src).endswith(
                        tuple(f".bak{os.getpid()}" for _ in [0])):
                    _calls["n"] += 1
                    if _calls["n"] == _fail:
                        raise OSError(28, "injected: no space left on device")
                return _real(src, dst)

            os.replace = flaky
            try:
                publish.promote_set(staged, str(dest))
                raise AssertionError(f"injected failure at {fail_at} did not raise")
            except OSError:
                pass
            finally:
                os.replace = real_replace
            gen = _generation(dest)
            assert gen == {"OLD"}, (
                f"mixed-generation set observable after failure at promotion "
                f"{fail_at}: {gen}")
            assert not list(dest.glob("*.bak*")), (
                f"recovery copies left after a successful rollback at {fail_at}")
    return "failures injected at every promotion point roll back to a pure OLD set"


def case_failed_rollback_keeps_the_recovery_copies_and_names_them():
    with tempfile.TemporaryDirectory() as td:
        dest, staged = _setup(td)
        real_replace = os.replace
        state = {"promotes": 0, "in_rollback": False}

        def flaky(src, dst, _real=real_replace):
            if state["in_rollback"] and ".bak" in str(src):
                raise OSError(5, "injected: restore also fails")
            if str(dst).endswith(NAMES[1]) and ".bak" not in str(dst):
                if state["promotes"] == 1:
                    state["in_rollback"] = True
                    raise OSError(28, "injected: promotion fails")
            if str(dst).endswith(tuple(NAMES)) and ".bak" not in str(dst):
                state["promotes"] += 1
            return _real(src, dst)

        os.replace = flaky
        try:
            publish.promote_set(staged, str(dest))
            raise AssertionError("expected the double failure to raise")
        except SystemExit as e:
            assert "only good copies" in str(e), e
            assert "do not delete" in str(e), e
        finally:
            os.replace = real_replace
        assert list(dest.glob("*.bak*")), (
            "recovery copies were deleted although restore failed")
    return "a failed rollback keeps the .bak recovery copies and says so"


def case_leftover_recovery_copy_blocks_publication():
    """A retry after a failed rollback must not overwrite the only good copy."""
    with tempfile.TemporaryDirectory() as td:
        dest, staged = _setup(td)
        (dest / (NAMES[0] + ".bak99999")).write_text("the only good copy\n")
        try:
            publish.promote_set(staged, str(dest))
            raise AssertionError("publication started over a leftover .bak")
        except SystemExit as e:
            assert "Recover it by hand" in str(e), e
        assert _generation(dest) == {"OLD"}, "targets were touched despite refusal"
    return "publication refuses to start while a recovery copy exists"


def case_missing_staged_file_refuses_before_touching_anything():
    with tempfile.TemporaryDirectory() as td:
        dest, staged = _setup(td)
        os.remove(staged[NAMES[2]])
        try:
            publish.promote_set(staged, str(dest))
            raise AssertionError("promotion ran with an unstaged artifact")
        except SystemExit as e:
            assert NAMES[2] in str(e), e
        assert _generation(dest) == {"OLD"}
        assert not list(dest.glob("*.bak*"))
    return "an incompletely staged set is refused before any target is touched"


def case_rollback_removes_targets_created_this_run():
    """First-run shape: none of the targets exist yet (analyze.py's *_relief
    files). A failure after some promotions must remove the newly created
    targets again -- 'restore the pre-call state' when the pre-call state is
    absence. This was a real hole: absent targets had no .bak entry and the
    rollback left them in place as a partial new set."""
    for fail_at in range(2, len(NAMES) + 1):
        with tempfile.TemporaryDirectory() as td:
            dest, staged = _setup(td)
            for n in NAMES:                     # pre-call state: nothing exists
                (dest / n).unlink()
            real_replace = os.replace
            calls = {"n": 0}

            def flaky(src, dst, _real=real_replace, _calls=calls, _fail=fail_at):
                if str(dst).endswith(tuple(NAMES)) and ".bak" not in str(dst):
                    _calls["n"] += 1
                    if _calls["n"] == _fail:
                        raise OSError(28, "injected: no space left on device")
                return _real(src, dst)

            os.replace = flaky
            try:
                publish.promote_set(staged, str(dest))
                raise AssertionError(f"injected failure at {fail_at} did not raise")
            except OSError:
                pass
            finally:
                os.replace = real_replace
            left = [n for n in NAMES if (dest / n).exists()]
            assert not left, (
                f"partial new set left after failure at {fail_at}: {left}")
    return "a failure with absent pre-call targets removes every created file again"


def case_concurrent_publisher_is_locked_out():
    """Two publishers into one directory must serialize; the second refuses
    while the first holds the lock (parse_bills round-4 lesson: check-then-act
    without a lock corrupted 11 of 15 raced runs)."""
    import fcntl
    with tempfile.TemporaryDirectory() as td:
        dest, staged = _setup(td)
        holder = open(dest / publish.LOCK_NAME, "w")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            publish.promote_set(staged, str(dest))
            raise AssertionError("second publisher ran despite a held lock")
        except SystemExit as e:
            assert "serialized" in str(e), e
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        assert _generation(dest) == {"OLD"}, "targets touched while locked out"
        done = publish.promote_set(staged, str(dest))   # and works once released
        assert sorted(done) == sorted(NAMES)
    return "a concurrent publisher is refused while the lock is held, runs after"


def case_remove_failure_during_rollback_does_not_abort_the_restores():
    """The review's scenario: promotion fails on a mixed set (some targets
    displaced to .baks, some created fresh), and then os.remove on a created
    target ALSO fails. The bad remove used to escape as a raw OSError, aborting
    rollback before any .bak restore ran -- displaced originals stranded, the
    promotion failure masked. Every recovery action must be attempted
    independently, and the combined error must name both casualty lists."""
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td) / "data"; dest.mkdir()
        stage = pathlib.Path(td) / "stage"; stage.mkdir()
        # mixed pre-state: a exists (displace), b and c absent (create)
        (dest / "a.csv").write_text("OLD a.csv\n")
        staged = {}
        for n in ("a.csv", "b.csv", "c.csv"):
            q = stage / (n + ".tmp"); q.write_text(f"NEW {n}\n"); staged[n] = str(q)
        real_replace, real_remove = os.replace, os.remove
        calls = {"n": 0}

        def flaky_replace(src, dst, _r=real_replace):
            if str(dst).endswith((".csv",)) and ".bak" not in str(dst):
                calls["n"] += 1
                if calls["n"] == 3:                 # fail promoting c.csv
                    raise OSError(28, "injected: promotion fails")
            return _r(src, dst)

        def flaky_remove(path, _r=real_remove):
            if str(path).endswith("b.csv"):
                raise OSError(5, "injected: remove fails")
            return _r(path)

        os.replace, os.remove = flaky_replace, flaky_remove
        try:
            publish.promote_set(staged, str(dest))
            raise AssertionError("expected the double failure to raise")
        except SystemExit as e:
            msg = str(e)
            assert "created targets not removed" in msg and "b.csv" in msg, msg
        finally:
            os.replace, os.remove = real_replace, real_remove
        # the .bak restore MUST still have run despite the failed remove
        assert (dest / "a.csv").read_text() == "OLD a.csv\n", (
            "displaced original was not restored after the remove failure")
        assert not list(dest.glob("a.csv.bak*")), (
            "a.csv restore left its .bak behind despite succeeding")
    return "a failed created-target removal no longer aborts the .bak restores"


CASES = [
    case_success_promotes_the_full_set,
    case_failure_at_every_promotion_point_leaves_no_mixed_set,
    case_failed_rollback_keeps_the_recovery_copies_and_names_them,
    case_leftover_recovery_copy_blocks_publication,
    case_missing_staged_file_refuses_before_touching_anything,
    case_rollback_removes_targets_created_this_run,
    case_concurrent_publisher_is_locked_out,
    case_remove_failure_during_rollback_does_not_abort_the_restores,
]


def main():
    ran = failures = 0
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
