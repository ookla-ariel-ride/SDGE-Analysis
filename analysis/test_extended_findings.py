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
import test_scripts_runnable as TSR   # the proven synthetic-fixture machinery


class SkipCase(Exception):
    pass


EXTRA_HOUSEHOLD = TSR.SYNTH_HOUSEHOLD.replace(
    "household:\n  pto_date: 2019-12-01\n",
    "household:\n  pto_date: 2019-12-01\n  has_ev: true\n  has_gas: false\n"
).replace(
    "gas:\n  therm_allin_usd: 2.0\n", "")   # has_gas: false requires this ABSENT

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


CASES = [case_extended_findings_end_to_end_on_a_synthetic_house]


def main():
    ran = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
