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


CASES = [
    case_success_promotes_the_full_set,
    case_failure_at_every_promotion_point_leaves_no_mixed_set,
    case_failed_rollback_keeps_the_recovery_copies_and_names_them,
    case_leftover_recovery_copy_blocks_publication,
    case_missing_staged_file_refuses_before_touching_anything,
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
