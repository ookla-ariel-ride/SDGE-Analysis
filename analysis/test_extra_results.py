#!/usr/bin/env python3
"""Behavioural tests for extra_results.py (issue #34).

The generator's own job is narrow: data/extra_results.json's `escalation`
block was an orphaned, hand-typed figure with no committed generator. It is
NOT the same computation as battery_dispatch_policies.json's own escalation
ladder that "drifted" -- they are two different dispatch scenarios' curves,
confirmed by reproducing both exactly from battery_dispatch_policies.py's own
escalation() formula at two different base-saving inputs (see the module's
own docstring for the full investigation). These tests check: the RETIRED
ladder reproduces from its own documented historical constant, the other six
keys survive untouched, and the file fails closed if its own assumed shape
ever changes.

Run from the repo root:  ./.venv/bin/python analysis/test_extra_results.py
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import extra_results as er  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def _write(path, obj):
    path.write_text(json.dumps(obj, indent=1) + "\n")


@case
def case_escalation_ladder_matches_a_hand_computation():
    """The core arithmetic, checked independently against a hand-derived
    single point: at 3% escalation, 1% fade, 5% discount, $14,500 install,
    save1=$1,743, payback should land just under 8 years (verified against
    the formula's own definition, not against a copy of the code)."""
    ladder = er._escalation_ladder(1743)
    assert ladder["3%"] == {"payback_yr": 7.8, "npv10": 102}, ladder["3%"]
    assert ladder["12%"] == {"payback_yr": 6.2, "npv10": 6973}, ladder["12%"]
    # a materially larger base saving must pay back materially faster
    faster = er._escalation_ladder(2238)
    assert faster["3%"]["payback_yr"] < ladder["3%"]["payback_yr"]
    return "the reimplemented escalation formula matches the documented committed figures"


@case
def case_build_reproduces_the_retired_ladder_from_the_documented_constant():
    """build() must reproduce the EXACT figures already committed (the
    retired evening-only variant), not the live/published dispatch ladder --
    that would silently change the committed numbers and contradict
    TECHNICAL.md's own still-published 7.8/7.3/6.8/6.2 figures."""
    real_out = er.OUT
    with tempfile.TemporaryDirectory() as td:
        tmp_out = pathlib.Path(td) / "extra_results.json"
        _write(tmp_out, {
            "phantom": {"a": 1}, "escalation": {"stale": True},
            "price_map": {"b": 2}, "nbt": {"c": 3}, "cleaning": {"d": 4},
            "trueup": {"e": 5}, "ev_fleet": {"f": 6},
        })
        er.OUT = tmp_out
        try:
            out = er.build()
        finally:
            er.OUT = real_out
    numeric = {k: v for k, v in out["escalation"].items() if k != "basis"}
    assert numeric == {
        "3%": {"payback_yr": 7.8, "npv10": 102},
        "5%": {"payback_yr": 7.3, "npv10": 1373},
        "8%": {"payback_yr": 6.8, "npv10": 3535},
        "12%": {"payback_yr": 6.2, "npv10": 6973},
    }, numeric
    assert "basis" in out["escalation"], "must self-document why this disagrees with the published ladder"
    return "build() reproduces the retired ladder's exact committed figures, plus a basis note"


@case
def case_the_other_six_keys_survive_unchanged():
    """A generator whose only job is patching ONE key must not touch the
    other six -- proven with distinctive sentinel values that would fail
    loudly if silently mutated, dropped, or reordered."""
    real_out = er.OUT
    with tempfile.TemporaryDirectory() as td:
        tmp_out = pathlib.Path(td) / "extra_results.json"
        frozen = {
            "phantom": {"quiet_nights": 44, "sentinel": "phantom-sentinel"},
            "escalation": {"stale": True},
            "price_map": {"sentinel": "price_map-sentinel"},
            "nbt": {"sentinel": "nbt-sentinel"},
            "cleaning": {"sentinel": "cleaning-sentinel"},
            "trueup": {"sentinel": "trueup-sentinel"},
            "ev_fleet": {"sentinel": "ev_fleet-sentinel"},
        }
        _write(tmp_out, frozen)
        er.OUT = tmp_out
        try:
            out = er.build()
        finally:
            er.OUT = real_out
    for key in er.FROZEN_KEYS:
        assert out[key] == frozen[key], (key, out[key], frozen[key])
    assert list(out.keys()) == list(frozen.keys()), (
        "key order must be preserved so the file regenerates byte-identically")
    return "the six frozen keys survive a run byte-for-byte, in their original order"


@case
def case_missing_frozen_key_aborts():
    """If the committed file's own shape ever changes (a key renamed or
    removed), fail loudly rather than silently publishing a file missing
    content this generator has no other source for."""
    real_out = er.OUT
    with tempfile.TemporaryDirectory() as td:
        tmp_out = pathlib.Path(td) / "extra_results.json"
        _write(tmp_out, {"phantom": {}, "escalation": {}})  # missing 5 frozen keys
        er.OUT = tmp_out
        try:
            er.build()
        except SystemExit as exc:
            assert "price_map" in str(exc), f"wrong refusal: {exc}"
            return "a committed file missing an expected frozen key aborts the run"
        else:
            raise AssertionError("a file missing frozen keys was silently accepted")
        finally:
            er.OUT = real_out


@case
def case_missing_escalation_key_aborts():
    """If a future edit strips the escalation key entirely rather than
    leaving a placeholder, fail loudly rather than silently inventing one."""
    real_out = er.OUT
    with tempfile.TemporaryDirectory() as td:
        tmp_out = pathlib.Path(td) / "extra_results.json"
        _write(tmp_out, {k: {} for k in er.FROZEN_KEYS})  # no escalation key
        er.OUT = tmp_out
        try:
            er.build()
        except SystemExit as exc:
            assert "escalation" in str(exc), f"wrong refusal: {exc}"
            return "a committed file missing the escalation key entirely aborts the run"
        else:
            raise AssertionError("a file missing the escalation key was silently accepted")
        finally:
            er.OUT = real_out


@case
def case_real_archive_regenerates_byte_identically():
    """No private data needed -- the only real input (data/extra_results.json)
    is a committed, public artifact."""
    if not er.OUT.exists():
        raise SkipCase(f"needs {er.OUT}, which this checkout does not have")
    before = er.OUT.read_text()
    er.main()
    after = er.OUT.read_text()
    assert before == after, "data/extra_results.json regeneration is not byte-identical"
    return "data/extra_results.json regenerates byte-identically"


@case
def case_real_archive_escalation_still_disagrees_with_battery_dispatch_policies_by_design():
    """The two ladders must NOT be forced to agree -- they are different
    scenarios. This is the opposite assertion of a naive "fix the
    contradiction" test, and is exactly the point of the investigation this
    generator's own docstring documents."""
    dispatch_path = ROOT / "data" / "battery_dispatch_policies.json"
    if not er.OUT.exists() or not dispatch_path.exists():
        raise SkipCase(f"needs {er.OUT} and {dispatch_path}, which this "
                       "checkout does not have")
    published = json.loads(er.OUT.read_text())["escalation"]
    ladder = json.loads(dispatch_path.read_text())["escalation_greedy_pw3_post_behavior"]
    assert published["3%"]["payback_yr"] != ladder["3%"]["payback"], (
        "the retired variant and the published ladder are different scenarios "
        "and should not coincidentally show the same payback")
    assert "basis" in published, "the disagreement must be self-documented in the JSON itself"
    return "the retired variant still (correctly) disagrees with the published ladder, and says why"


def run():
    passed = failed = skipped = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS  {fn.__name__}: {msg}")
            passed += 1
        except SkipCase as e:
            print(f"SKIP  {fn.__name__}: {e}")
            skipped += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(CASES)} passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
