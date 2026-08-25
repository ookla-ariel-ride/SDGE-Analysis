#!/usr/bin/env python3
"""Guard suite for extended_findings.py -- run END TO END on a synthetic house.

extended_findings.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py):
CI has no usage.csv/SAM-8760 exports, so before this file existed its whole
main path (the dispatch tie-out gate, sections A-I, the publication gate) ran
only on the machine holding the private archive (issue #44).

Reuses test_scripts_runnable.py's already-proven synthetic Green Button fixture
(_build_throwaway_root/_synthetic_usage/SYNTH_HOUSEHOLD) rather than
re-deriving a second one -- it already runs behavior_rebuild.py and
battery_dispatch_policies.py cleanly in that suite's CI-runnable tier. This
case extends that household with has_ev/has_gas flags, runs
battery_dispatch_policies.py itself first and PROMOTES its output into the
throwaway data/ directory (extended_findings.py's dispatch tie-out gate reads
DATA/battery_dispatch_policies.json specifically, and a stale/real committed
copy there would fail the tie-out against this synthetic run's own numbers --
same ordering contract CLAUDE.md's Commands section documents for the real
private/verify sandbox), then runs extended_findings.py itself.

Hand-verified exactly: section A (ab205, a pure rates.py comparison) and
section F (representative_year, built from two flat SAM-8760 fixtures with a
designed 10% year-over-year delta). Everything else is checked structurally
(every required section present, publication gate satisfied, valid JSON) --
but the generator's OWN abundant internal fail-closed pins (the three-policy
dispatch tie-out within $1.5, the post-behavior marginal tie-out, the AB 205
model-vs-adopted pin, the NBT-vs-NEM2 ordering pin, the electrification-
dividend-sign pin) all have to hold for the run to exit 0 at all, so this case
already exercises a wide slice of the main path even before its own
assertions run -- a defect in section E's energy-conservation shift, for
instance, would trip the script's OWN `assert abs(...) < 1e-6` and fail this
case via a nonzero exit code.

SkipCase matches test_parse_bills.py's typed-exception convention (issue #44
AC4); there is no skip path in this file since the fixture is fully synthetic.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402
import test_scripts_runnable as TSR   # the proven synthetic-fixture machinery


class SkipCase(Exception):
    pass


EXTRA_HOUSEHOLD = TSR.SYNTH_HOUSEHOLD.replace(
    "household:\n  pto_date: 2019-12-01\n",
    "household:\n  pto_date: 2019-12-01\n  has_ev: true\n  has_gas: false\n"
).replace(
    "gas:\n  therm_allin_usd: 2.0\n", "")   # has_gas: false requires this ABSENT

def _noev_household():
    """EXTRA_HOUSEHOLD with household.has_ev flipped to false, and every input
    the false flag forbids removed. Three separate removals are needed, each
    enforced by a different guard: behavior_rebuild.py refuses a declared
    charger alongside a false has_ev, and extended_findings.py's own
    _gate_domain() refuses misc.miles_per_year or misc.supercharge_kwh_yr
    alongside it. A household that merely never charges an EV is NOT the same
    fixture -- the branch under test reads the FLAG.

    Every edit asserts it took. String surgery that silently matched nothing
    would leave the EV household in place and make the no-EV case below pass
    for entirely the wrong reason."""
    hh = EXTRA_HOUSEHOLD
    assert "has_ev: true\n" in hh, "EXTRA_HOUSEHOLD no longer sets has_ev"
    assert "charger:\n  kw: 11.5\n" in hh, "SYNTH_HOUSEHOLD no longer declares a charger"
    assert "misc:\n" in hh, "SYNTH_HOUSEHOLD no longer declares a misc block"
    hh = hh.replace("has_ev: true\n", "has_ev: false\n")
    hh = hh.replace("charger:\n  kw: 11.5\n", "")
    hh = hh.replace("misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n", "")
    assert "has_ev: false" in hh and "charger:" not in hh and "misc:" not in hh, hh
    return hh


SAM_2025_KWH = 10.0
SAM_2026_KWH = 11.0   # exactly a 10% year-over-year delta, hand-computable


def _write_flat_sam(path, value):
    path.write_text("kWh\n" + "".join(f"{value:.6f}\n" for _ in range(8760)))


def case_extended_findings_end_to_end_on_a_synthetic_house():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        (tmp / "private" / "household.yaml").write_text(EXTRA_HOUSEHOLD)
        raw = tmp / "private" / "1-raw-data"
        raw.mkdir(parents=True, exist_ok=True)
        _write_flat_sam(raw / "enphase_sam8760_2025.csv", SAM_2025_KWH)
        _write_flat_sam(raw / "enphase_sam8760_2026.csv", SAM_2026_KWH)

        # Ordering contract: battery_dispatch_policies.py FIRST, against the
        # SAME synthetic usage.csv, then promote its output into data/ so
        # extended_findings.py's dispatch tie-out gate (which reads
        # DATA/battery_dispatch_policies.json, not the cwd copy) compares
        # against a consistent artifact instead of the real committed one
        # TSR._build_throwaway_root already staged there.
        r0 = subprocess.run([sys.executable, "battery_dispatch_policies.py"], cwd=tmp,
                            capture_output=True, text=True, timeout=600)
        assert r0.returncode == 0, f"battery_dispatch_policies.py failed: {r0.stderr[-2000:]}"
        shutil.copy(tmp / "battery_dispatch_policies.json",
                    tmp / "data" / "battery_dispatch_policies.json")

        r = subprocess.run([sys.executable, "extended_findings.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"extended_findings.py failed: {r.stderr[-2000:]}"
        out = json.loads((tmp / "data" / "extended_results.json").read_text())

    # ---- section A: pure rates.py comparison, no fixture dependence --------
    ab = out["ab205"]
    assert ab["model_matches_adopted"] is True, ab
    assert abs(ab["bsc_adopted_daily"] - round(24.15 * 12 / 365.25, 5)) < 1e-9, ab

    # ---- section F: exact 10% YoY delta by construction --------------------
    ry = out["representative_year"]
    assert abs(ry["load_jan_jun_2025_kwh"] - round(181 * 24 * SAM_2025_KWH)) <= 1, ry
    assert abs(ry["load_jan_jun_2026_kwh"] - round(181 * 24 * SAM_2026_KWH)) <= 1, ry
    assert abs(ry["delta_pct"] - 10.0) < 0.1, ry

    # ---- structural: every required section present and internally sane ---
    required = ("ab205", "electrification_dividend", "away_days", "supercharge_delta",
                "weekend_sop", "representative_year", "gas_decomposition", "nbt_2039",
                "tornado_battery")
    for k in required:
        assert k in out, (k, out.keys())
    assert out["gas_decomposition"]["not_applicable"] is True, out["gas_decomposition"]
    assert out["electrification_dividend"]["home_ev_kwh"] > 0, out["electrification_dividend"]
    assert out["tornado_battery"]["base_payback_yr"] > 0, out["tornado_battery"]
    assert json.dumps(out), "extended_results.json is not JSON-serializable"
    return ("extended_findings.py runs end to end on a synthetic house; "
            "section A and section F match hand computation exactly, every "
            "required section publishes, and the generator's own dispatch "
            "tie-out / publication-gate pins all held")


def case_post_behavior_marginal_is_the_house_shift_when_the_household_has_no_ev():
    """issue #147: on a household whose intake says household.has_ev is false,
    extended_findings.py's G_POST must be the battery marginal AFTER behavior
    scenario c (the flexible house-load shift) -- the same free fix
    battery_dispatch_policies.py's post_behavior block applies for that
    household.

    The script used to build G_POST with an unconditional
    behavior_rebuild.shift_ev(). That moves nothing on an EV-free household, so
    G_POST was the battery on the BARE BASELINE while the dispatch artifact's
    post_behavior.mid.battery_marginal was the scenario-c figure. The two then
    disagreed by hundreds of dollars and the script's own tie-out assert fired,
    which meant a correctly regenerated no-EV chain could not finish at all.

    Two independent things are checked, deliberately. FIRST that the chain
    exits 0 (the tie-out assert against the dispatch artifact is left exactly
    as it was -- it is correct, and it is what fails on the defect). SECOND the
    ARITHMETIC, from figures this script publishes: the post_behavior tornado
    lever's two payback points must sit in the same ratio as the dispatch
    artifact's own pre- and post-behavior marginals. A G_POST that silently
    collapsed onto G would put that ratio at exactly 1.0, so this half cannot
    be satisfied by a run that merely labels an unshifted year correctly."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        TSR._build_throwaway_root(tmp, synthetic=True)
        (tmp / "private" / "household.yaml").write_text(_noev_household())
        raw = tmp / "private" / "1-raw-data"
        raw.mkdir(parents=True, exist_ok=True)
        _write_flat_sam(raw / "enphase_sam8760_2025.csv", SAM_2025_KWH)
        _write_flat_sam(raw / "enphase_sam8760_2026.csv", SAM_2026_KWH)

        # behavior_rebuild.py FIRST: its scenarios.c.house_kwh_moved is the
        # independent cross-check target for what the free fix should move
        # (a different script, a different entry point, the same household).
        rb = subprocess.run([sys.executable, "behavior_rebuild.py"], cwd=tmp,
                            capture_output=True, text=True, timeout=600)
        assert rb.returncode == 0, f"behavior_rebuild.py failed: {rb.stderr[-2000:]}"
        br_json = json.loads((tmp / "behavior_rebuild.json").read_text())
        assert br_json["scenarios"]["a"].get("not_applicable") is True, (
            "the no-EV fixture did not actually produce an EV-free household: "
            f"scenarios.a is {br_json['scenarios']['a']!r}")

        # then the dispatch artifact, promoted into data/ per the same
        # ordering contract the EV case above documents.
        r0 = subprocess.run([sys.executable, "battery_dispatch_policies.py"], cwd=tmp,
                            capture_output=True, text=True, timeout=600)
        assert r0.returncode == 0, f"battery_dispatch_policies.py failed: {r0.stderr[-2000:]}"
        shutil.copy(tmp / "battery_dispatch_policies.json",
                    tmp / "data" / "battery_dispatch_policies.json")
        bdp = json.loads((tmp / "battery_dispatch_policies.json").read_text())

        r = subprocess.run([sys.executable, "extended_findings.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, (
            "extended_findings.py failed on a household with household.has_ev "
            f"false: {r.stderr[-2000:]}")
        out = json.loads((tmp / "data" / "extended_results.json").read_text())

    # ---- the free fix that ran really was scenario c, and it moved energy --
    pb = bdp["post_behavior"]
    assert pb["free_fix_scenario"] == "c", pb
    scen_c = br_json["scenarios"]["c"]
    assert pb["kwh_moved"] == round(scen_c["house_kwh_moved"]), (
        "the free fix did not move behavior_rebuild scenarios.c's own "
        "house_kwh_moved", pb["kwh_moved"], scen_c["house_kwh_moved"])
    assert pb["kwh_moved"] > 0, (
        "the no-EV free fix moved nothing, so this case could not tell a "
        "working shift from the unconditional-shift_ev defect", pb)

    # ---- the arithmetic: G_POST really is the post-shift marginal ---------
    G = float(bdp["pw3"]["greedy"]["save"])                 # battery on baseline
    G_POST = float(pb["mid"]["battery_marginal"])           # battery after the fix
    assert abs(G - G_POST) > 1.5, (
        "the dispatch artifact's pre- and post-behavior marginals are equal, so "
        "this fixture cannot distinguish a working free fix from a no-op", G, G_POST)
    lever = out["tornado_battery"]["levers"]["post_behavior"]
    lo, hi = lever["payback_range_yr"]
    # both points are BATT_COST / (a marginal), so their RATIO is the inverse
    # ratio of the marginals, with BATT_COST cancelling out -- checkable
    # without knowing the install cost at all. 0.03 covers the 0.1-yr rounding
    # the artifact publishes; a collapsed G_POST would land at exactly 1.0.
    assert abs((hi / lo) - (G / G_POST)) < 0.03, (
        "the post_behavior tornado lever's payback ratio does not match the "
        "dispatch artifact's own pre/post marginal ratio -- G_POST was not "
        "computed from the scenario-c shifted year", lo, hi, G, G_POST)
    assert lever["swing_yr"] > 0, (
        "the post_behavior lever has zero swing: G_POST collapsed onto G, "
        "which is exactly what the unconditional EV shift did here", lever)
    return ("on a household with household.has_ev false, the whole chain "
            "(behavior_rebuild -> battery_dispatch_policies -> "
            "extended_findings) exits 0; the free fix behind G_POST is "
            f"behavior scenario c ({pb['kwh_moved']} kWh, behavior_rebuild's "
            "own scenarios.c.house_kwh_moved), and the post_behavior tornado "
            f"lever's payback ratio ({hi / lo:.3f}) matches the dispatch "
            f"artifact's own pre/post marginal ratio ({G / G_POST:.3f})")


CASES = [case_extended_findings_end_to_end_on_a_synthetic_house,
         case_post_behavior_marginal_is_the_house_shift_when_the_household_has_no_ev]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
