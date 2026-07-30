#!/usr/bin/env python3
"""Unit guards for carbon_fullyear.py's raw-cache path and fail-closed branches.

The raw CAISO day-cache (private/1-raw-data/caiso_raw/) does not exist on every
machine -- this repo's own does not have one -- so the branch that parses it, and
the corrupt-aggregate branches that protect the committed CSV, never execute in
an ordinary run. Untested fail-closed code is how this repo's silent failures
have survived before, so these cases build synthetic CAISO fixtures and exercise
those branches directly. No private data; runs in CI.

Run from the repo root:  ./.venv/bin/python analysis/test_carbon_fullyear.py
"""
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# carbon_fullyear imports behavior_rebuild, whose module level reads the intake
# file and fails closed without it -- correct behavior, tested in
# test_household.py, but it would block THIS suite in a clean checkout (CI).
# Point the loader at a synthetic household before the import so these cases run
# identically everywhere. Values are invented; nothing here depends on them.
import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import carbon_fullyear as C


def _write_caiso_day(cdir, day, mw=25000.0, gas_mt=3000.0):
    """One synthetic CAISO day in the Today's Outlook export shape: 5-minute
    rows, a Time column of HH:MM strings, per-source CO2 mT/h and demand MW."""
    times = [f"{h:02d}:{m:02d}" for h in range(24) for m in range(0, 60, 5)]
    co2 = pd.DataFrame({"Time": times})
    for col in C.CO2_COLS:
        co2[col] = 0.0
    co2["Natural Gas CO2"] = gas_mt
    co2["Imports CO2"] = -100.0          # CAISO books negative when net-exporting
    dem = pd.DataFrame({"Time": times, "Current demand": mw})
    co2.to_csv(cdir / f"caiso_co2_{day}.csv", index=False)
    dem.to_csv(cdir / f"caiso_demand_{day}.csv", index=False)


def case_hourly_intensity_parses_a_raw_day():
    with tempfile.TemporaryDirectory() as td:
        cdir = pathlib.Path(td)
        _write_caiso_day(cdir, "20260115")
        old = C.CAISO_DIR
        C.CAISO_DIR = cdir
        try:
            s = C.hourly_intensity("20260115")
        finally:
            C.CAISO_DIR = old
        assert list(s.index) == list(range(24)), s.index
        want = 1000.0 * (3000.0 - 100.0) / 25000.0
        assert np.allclose(s.values, want), (s.values[0], want)
    return "hourly_intensity reproduces kg/MWh from a synthetic raw CAISO day"


def case_build_covered_from_raw_reads_the_cache_and_legacy_days():
    with tempfile.TemporaryDirectory() as td:
        cdir = pathlib.Path(td)
        _write_caiso_day(cdir, "20260113")
        _write_caiso_day(cdir, "20260114")
        # a co2 file without its demand twin must be skipped, not crash
        _write_caiso_day(cdir, "20260116")
        (cdir / "caiso_demand_20260116.csv").unlink()
        old = C.CAISO_DIR
        C.CAISO_DIR = cdir
        try:
            covered = C.build_covered_from_raw()
        finally:
            C.CAISO_DIR = old
        got = {ts.strftime("%Y%m%d") for ts in covered}
        assert {"20260113", "20260114"} <= got, got
        assert "20260116" not in got, "day without demand file was not skipped"
        # Beyond the raw cache, build_covered_from_raw always folds in the legacy
        # seasonal sample days preserved in data/carbon_results.json under
        # source.sample_days -- for this dataset, the four days (one per season)
        # of the original 4-day carbon study that predated the full-year cache.
        # Derive the expectation from that artifact rather than pinning this
        # dataset's 4+2=6, so a fork with a different legacy artifact still passes.
        legacy = set(json.loads(C.OLD_RESULTS.read_text())
                     ["source"]["sample_days"].values())
        assert got == {"20260113", "20260114"} | legacy, (
            f"expected the two parseable synthetic days plus the legacy sample "
            f"days {sorted(legacy)}, got {sorted(got)}")
        for v in covered.values():
            assert v.shape == (24,)
    return "build_covered_from_raw parses the cache, skips orphans, keeps legacy days"


def case_committed_csv_schema_and_truncation_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "hourly.csv"
        old = C.HOURLY_CSV
        C.HOURLY_CSV = p
        try:
            pd.DataFrame({"wrong": [1]}).to_csv(p, index=False)
            try:
                C.build_covered_from_committed_csv()
                raise AssertionError("wrong schema was accepted")
            except SystemExit as e:
                assert "unexpected schema" in str(e), e
            pd.DataFrame({"date": ["2026-01-15"] * 23, "hour": list(range(23)),
                          "kgco2_per_mwh": [200.0] * 23}).to_csv(p, index=False)
            try:
                C.build_covered_from_committed_csv()
                raise AssertionError("a 23-hour day was accepted")
            except SystemExit as e:
                assert "0..23" in str(e), e
        finally:
            C.HOURLY_CSV = old
    return "the committed-CSV reader fails closed on bad schema and truncated days"


def case_no_intensity_source_fails_closed():
    with tempfile.TemporaryDirectory() as td:
        old_dir, old_csv = C.CAISO_DIR, C.HOURLY_CSV
        C.CAISO_DIR = pathlib.Path(td) / "nope"
        C.HOURLY_CSV = pathlib.Path(td) / "nope.csv"
        try:
            C.main()
            raise AssertionError("main ran with no intensity source")
        except SystemExit as e:
            assert "no intensity source" in str(e), e
        finally:
            C.CAISO_DIR, C.HOURLY_CSV = old_dir, old_csv
    return "main refuses to run when neither intensity source exists"


def case_intensity_sanity_bounds_reject_garbage():
    try:
        C._check("test", np.array([1e6] * 24, dtype=float))
        raise AssertionError("absurd intensity accepted")
    except AssertionError as e:
        if "absurd" in str(e):
            raise
    return "_check rejects intensities outside the plausible CAISO range"


CASES = [
    case_hourly_intensity_parses_a_raw_day,
    case_build_covered_from_raw_reads_the_cache_and_legacy_days,
    case_committed_csv_schema_and_truncation_fail_closed,
    case_no_intensity_source_fails_closed,
    case_intensity_sanity_bounds_reject_garbage,
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
