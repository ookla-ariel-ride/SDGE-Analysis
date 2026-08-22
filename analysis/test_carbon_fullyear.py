#!/usr/bin/env python3
"""Unit guards for carbon_fullyear.py's raw-cache path and fail-closed branches.

The raw CAISO day-cache (private/1-raw-data/caiso_raw/) does not exist on every
machine -- this repo's own does not have one -- so the branch that parses it, and
the corrupt-aggregate branches that protect the committed CSV, never execute in
an ordinary run. Untested fail-closed code is how this repo's silent failures
have survived before, so these cases build synthetic CAISO fixtures and exercise
those branches directly. No private data; runs in CI.

Exception: case_ac3_28day_reproduction_within_2pct (issue #8's AC3) genuinely
needs the real raw cache -- it is the "does the new full-year source agree with
the retired 28-day one" check CLAUDE.md's evidence-based principle demands
before a data source is retired, so a synthetic fixture would prove nothing.
It SKIPs (not FAILs) when the real archive is absent, following the same
private-archive-SKIP convention as test_bill_decomposition.py.

Run from the repo root:  ./.venv/bin/python analysis/test_carbon_fullyear.py
"""
import glob
import json
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402


class SkipCase(Exception):
    """Typed skip signal (matching test_parse_bills.py's convention, issue #44
    AC4) -- a case raises this instead of returning a "SKIP ..."-prefixed
    string, so a case that legitimately returns a message starting with those
    five letters can never be silently miscounted as skipped."""

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


def case_partial_raw_cache_merges_with_a_valid_committed_csv():
    """A stray/partial raw cache used to be selected outright over the
    committed CSV whenever it had even one file, so a 1-day raw cache could
    fail the >=350 coverage gate even with a fully-covered committed CSV sitting
    right beside it, unused (an adversarial review finding). Build exactly
    that scenario -- a 1-day raw cache plus a fully-covered synthetic committed
    CSV -- and confirm main() succeeds using the MERGED coverage, not just the
    raw cache's 1 day."""
    with tempfile.TemporaryDirectory() as td:
        cdir = pathlib.Path(td) / "caiso_raw"
        cdir.mkdir()
        days = pd.date_range(C.YEAR_START, C.YEAR_END, freq="D")
        _write_caiso_day(cdir, days[0].strftime("%Y%m%d"))  # exactly 1 raw day

        rows = [(d.strftime("%Y-%m-%d"), h, 200.0) for d in days for h in range(24)]
        committed_csv = pathlib.Path(td) / "hourly.csv"
        pd.DataFrame(rows, columns=["date", "hour", "kgco2_per_mwh"]).to_csv(
            committed_csv, index=False)

        # main() calls behavior_rebuild.load(), whose CSV default is the bare
        # relative "usage.csv" (the private/verify sandbox convention) -- this
        # suite runs from the repo root under check_coverage.sh, not from
        # private/verify, so point it at the real archive by absolute path
        # (same fix test_carbon_dispatch_tradeoff.py's _load_modules() applies).
        import behavior_rebuild as br
        usage_files = sorted(glob.glob(
            str(C.ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")))
        if not usage_files:
            raise SkipCase("the merge-vs-shadow check needs the real Green Button "
                           "archive, which this checkout does not have")
        old_dir, old_csv, old_results, old_br_csv = (
            C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON, br.CSV)
        C.CAISO_DIR = cdir
        C.HOURLY_CSV = committed_csv
        C.RESULTS_JSON = pathlib.Path(td) / "results.json"  # no prior baseline
        br.CSV = usage_files[0]
        try:
            C.main()
            written = json.loads(C.RESULTS_JSON.read_text())
            n_cov = written["coverage"]["days_covered"]
            assert n_cov > 1, (
                f"only {n_cov} day(s) covered -- the 1-day raw cache shadowed "
                "the fully-covered committed CSV instead of merging with it")
            assert n_cov == len(days), (n_cov, len(days))
        finally:
            C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON = old_dir, old_csv, old_results
            br.CSV = old_br_csv
    return ("main merges a 1-day raw cache with a fully-covered committed CSV "
            "rather than letting the partial cache shadow it")


def case_intensity_sanity_bounds_reject_garbage():
    try:
        C._check("test", np.array([1e6] * 24, dtype=float))
        raise AssertionError("absurd intensity accepted")
    except AssertionError as e:
        if "absurd" in str(e):
            raise
    return "_check rejects intensities outside the plausible CAISO range"


def case_coverage_below_350_fails_closed_and_names_the_missing_dates():
    """Issue #8 AC2: the hard floor is COVERAGE_MIN = 350/365, and a run below it
    must name the missing calendar dates individually -- "never interpolated
    silently" means a human can see exactly which days are short, not just a
    count. Build a raw cache withholding the LAST 20 of the 365 analysis-year
    days (345 covered, 5 short of the 350 floor) and confirm main() aborts,
    naming every withheld date rather than just reporting a count.

    main() reaches this fail-closed check before it ever touches the household
    Green Button data (behavior_rebuild.json is read from the real committed
    data/, unaffected by the CAISO_DIR/RESULTS_JSON overrides below), so no
    usage.csv or cwd change is needed to exercise it standalone.
    """
    with tempfile.TemporaryDirectory() as td:
        cdir = pathlib.Path(td)
        days = pd.date_range(C.YEAR_START, C.YEAR_END, freq="D")
        # build_covered_from_raw() always folds the 4 legacy seasonal days back in
        # from the committed carbon_results.json even if their raw file is absent
        # -- so withholding one of THOSE 4 exact dates would not actually reduce
        # coverage. Exclude them from the withhold candidates to get an exact count.
        legacy = {pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:]}") for d in
                  json.loads(C.OLD_RESULTS.read_text())["source"]["sample_days"].values()}
        n_withhold = 20
        withheld = [d for d in days if d not in legacy][-n_withhold:]
        withheld_set = set(withheld)
        for dt_ in days:
            if dt_ not in withheld_set:
                _write_caiso_day(cdir, dt_.strftime("%Y%m%d"))
        old_dir, old_csv, old_results = C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON
        C.CAISO_DIR = cdir
        C.HOURLY_CSV = pathlib.Path(td) / "nope.csv"                  # unused (raw path wins)
        C.RESULTS_JSON = pathlib.Path(td) / "no_prior_results.json"   # no regression base
        try:
            C.main()
            raise AssertionError(
                f"main ran with only {len(days) - n_withhold}/{len(days)} days covered")
        except SystemExit as e:
            msg = str(e)
            n_cov_expected = len(days) - n_withhold
            assert str(n_cov_expected) in msg, f"expected count {n_cov_expected} in: {msg}"
            assert "FAIL-CLOSED" in msg, msg
            for dt_ in withheld:
                s = dt_.strftime("%Y-%m-%d")
                assert s in msg, f"missing date {s} not individually named in: {msg}"
        finally:
            C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON = old_dir, old_csv, old_results
    return (f"main aborts at {len(days) - n_withhold}/365 (< COVERAGE_MIN=350) and "
            f"individually names all {n_withhold} missing calendar dates, not just a count")


# Snapshot of the committed data/caiso_hourly_intensity.csv from BEFORE issue
# #8's regeneration: 28 real CAISO days (24 fetched individually plus the 4
# original seasonal days carried over from carbon_results.json), kg CO2/MWh by
# hour 0..23, rounded to 0.1 -- the same resolution the committed CSV stores.
# Embedded verbatim (not read from data/) because that file's shape is about to
# change from 28 days to 365 and the OLD values for this comparison must not
# depend on re-reading data/ after this issue's regeneration.
_OLD_28DAY_INTENSITY = {
    "2025-07-28": [220.5, 229.1, 245.0, 251.1, 242.2, 231.2, 197.3, 143.1, 94.7, 70.3, 67.6, 66.2, 56.7, 37.7, 27.7, 31.5, 41.1, 68.1, 90.7, 121.5, 136.0, 149.6, 198.0, 218.0],
    "2025-08-08": [289.1, 294.5, 304.4, 310.6, 309.2, 299.6, 289.4, 246.9, 201.1, 185.7, 180.7, 169.6, 150.7, 136.9, 128.0, 132.1, 144.7, 167.5, 185.8, 214.9, 216.5, 224.0, 260.8, 274.9],
    "2025-08-22": [306.2, 315.0, 324.0, 326.3, 318.1, 317.2, 304.1, 260.8, 214.2, 202.4, 191.9, 173.7, 167.0, 167.5, 174.1, 170.3, 182.3, 203.0, 227.5, 261.1, 256.8, 255.0, 287.3, 302.3],
    "2025-09-08": [307.2, 310.7, 319.9, 317.1, 306.7, 299.0, 274.3, 239.9, 157.8, 145.3, 136.6, 124.1, 101.7, 85.2, 77.8, 77.5, 94.0, 128.5, 155.7, 186.6, 198.4, 220.8, 256.7, 287.2],
    "2025-09-22": [273.2, 286.7, 302.9, 304.9, 301.7, 291.5, 267.5, 260.3, 214.9, 202.0, 182.3, 176.2, 155.8, 164.6, 148.0, 146.5, 165.4, 193.8, 219.4, 234.4, 248.1, 257.4, 283.0, 310.3],
    "2025-10-08": [309.3, 319.1, 325.5, 331.7, 326.9, 310.9, 274.0, 256.8, 169.1, 147.0, 145.5, 141.5, 124.8, 103.2, 79.1, 71.6, 89.7, 166.4, 191.1, 193.4, 199.6, 217.1, 252.2, 283.6],
    "2025-10-15": [314.8, 323.9, 333.7, 334.0, 309.3, 293.9, 254.2, 247.1, 158.4, 114.0, 106.7, 98.7, 79.6, 88.8, 97.0, 98.9, 108.0, 192.7, 202.9, 208.3, 214.2, 236.5, 260.3, 291.1],
    "2025-10-22": [324.4, 322.2, 331.8, 333.2, 323.2, 307.5, 272.4, 250.9, 204.3, 138.0, 132.4, 163.0, 145.5, 130.1, 139.2, 117.1, 102.3, 155.4, 183.0, 187.8, 207.5, 226.0, 251.8, 269.9],
    "2025-11-08": [310.4, 327.7, 335.7, 342.9, 340.6, 326.3, 318.6, 203.1, 110.5, 100.3, 96.1, 88.1, 79.6, 91.8, 92.6, 133.2, 196.5, 220.8, 232.6, 242.3, 250.6, 249.0, 273.5, 289.5],
    "2025-11-22": [324.7, 320.5, 317.4, 314.9, 307.2, 304.3, 282.2, 280.6, 250.4, 225.6, 235.3, 250.8, 254.9, 251.7, 236.6, 229.5, 235.6, 230.3, 244.6, 264.2, 272.7, 273.5, 284.6, 305.6],
    "2025-12-08": [338.8, 343.0, 351.4, 357.8, 347.6, 324.6, 278.3, 267.7, 231.8, 218.3, 227.9, 248.3, 257.8, 255.4, 240.4, 236.8, 256.3, 255.5, 262.5, 265.7, 260.8, 263.6, 293.2, 312.4],
    "2025-12-22": [318.3, 327.0, 343.2, 348.1, 333.3, 311.6, 283.5, 266.8, 232.5, 206.0, 194.9, 179.3, 179.5, 196.3, 219.2, 237.0, 242.4, 246.5, 258.6, 261.0, 273.5, 281.5, 298.2, 321.5],
    "2026-01-08": [242.9, 267.1, 287.5, 287.7, 271.3, 239.3, 186.9, 190.2, 138.4, 71.2, 76.1, 89.1, 110.4, 106.8, 81.4, 114.1, 192.3, 181.1, 184.8, 204.6, 213.1, 218.9, 245.4, 266.8],
    "2026-01-15": [306.1, 317.7, 326.6, 340.3, 331.5, 305.7, 255.4, 242.8, 186.1, 153.0, 147.7, 162.7, 180.4, 185.8, 161.9, 155.5, 216.7, 215.1, 215.9, 227.1, 234.4, 238.1, 255.0, 274.6],
    "2026-01-22": [328.1, 334.8, 342.4, 348.8, 334.5, 313.3, 264.6, 250.3, 243.8, 231.3, 225.1, 244.1, 249.8, 238.5, 228.5, 223.4, 248.1, 236.6, 239.6, 251.9, 262.1, 267.6, 298.6, 310.4],
    "2026-02-08": [280.2, 293.1, 309.0, 314.7, 310.2, 290.8, 256.2, 224.9, 117.7, 78.9, 56.3, 49.9, 53.0, 61.4, 59.1, 49.1, 144.6, 197.9, 195.5, 204.2, 212.6, 223.0, 240.0, 256.9],
    "2026-02-22": [296.0, 301.6, 314.8, 314.2, 312.4, 302.5, 270.9, 201.0, 97.9, 87.8, 67.0, 74.2, 108.4, 128.0, 97.6, 69.0, 105.4, 210.1, 221.2, 224.8, 229.4, 238.2, 268.9, 301.7],
    "2026-03-10": [225.1, 242.0, 252.7, 235.4, 223.8, 202.8, 164.1, 151.3, 98.3, 36.3, 30.1, 31.5, 18.0, 33.5, 50.9, 68.5, 81.7, 87.2, 141.9, 137.6, 132.9, 133.8, 163.4, 186.1],
    "2026-03-22": [198.3, 210.0, 231.0, 236.7, 232.2, 213.5, 197.5, 184.7, 72.4, 44.4, 16.7, 13.3, -1.0, -25.1, -56.2, -31.3, 12.0, 56.5, 100.9, 134.5, 134.9, 156.4, 179.9, 200.1],
    "2026-04-08": [203.4, 207.8, 212.5, 220.6, 211.7, 185.7, 148.5, 120.1, 73.1, 40.8, 33.4, 22.5, 6.0, -0.7, -7.6, -16.4, -12.0, 8.1, 90.2, 112.5, 122.6, 133.0, 153.5, 183.6],
    "2026-04-15": [182.8, 199.0, 211.3, 213.5, 205.9, 189.2, 161.7, 128.2, 99.7, 102.9, 97.6, 110.4, 133.8, 127.8, 125.5, 92.4, 84.6, 62.0, 96.7, 101.9, 104.1, 114.4, 136.3, 157.3],
    "2026-04-22": [185.3, 188.2, 185.7, 192.9, 188.1, 160.8, 166.3, 138.1, 110.2, 95.9, 113.6, 121.7, 122.7, 124.7, 130.1, 114.1, 83.0, 60.1, 85.6, 118.7, 116.0, 115.2, 143.5, 158.9],
    "2026-05-08": [181.3, 190.0, 182.1, 171.9, 160.1, 143.9, 114.7, 78.4, 49.2, 40.4, 21.6, 2.9, 6.4, 8.2, 4.0, -13.1, -23.2, -22.2, 38.8, 56.4, 75.1, 92.5, 108.5, 130.2],
    "2026-05-22": [188.7, 195.8, 202.9, 208.1, 201.9, 187.3, 153.9, 66.4, 46.3, 44.1, 41.6, 36.5, 29.4, 2.3, -1.7, -8.4, -6.0, 9.6, 51.7, 73.4, 82.8, 91.2, 107.3, 117.8],
    "2026-06-08": [181.7, 198.4, 204.1, 197.3, 179.0, 170.9, 154.3, 73.4, 37.9, -1.9, -21.0, -29.5, -7.1, -25.5, -38.0, -41.0, -4.2, 19.9, 62.2, 85.1, 94.4, 94.2, 130.3, 150.6],
    "2026-06-22": [168.8, 182.5, 190.8, 192.4, 191.7, 188.5, 166.0, 96.0, 62.4, 58.3, 42.2, 23.1, 3.6, 2.6, 6.4, 7.3, 18.4, 53.1, 89.4, 103.4, 121.4, 138.1, 170.5, 189.0],
    "2026-07-08": [247.8, 261.3, 277.0, 276.9, 267.3, 251.2, 219.3, 120.0, 91.6, 82.5, 63.0, 35.2, 15.5, 3.8, 10.7, 37.5, 82.0, 100.7, 122.1, 140.5, 145.0, 154.3, 177.9, 215.4],
    "2026-07-15": [261.1, 269.6, 275.9, 284.6, 287.7, 278.2, 242.2, 194.8, 169.8, 155.6, 133.0, 120.2, 113.7, 115.0, 116.2, 120.3, 127.0, 146.6, 161.6, 175.6, 183.6, 193.3, 219.8, 246.0],
}


def case_ac3_28day_reproduction_within_2pct():
    """Issue #8 AC3: before the 28-day intensity source is retired in favor of
    the full 365-day raw cache, confirm the new source agrees with the old one.
    _OLD_28DAY_INTENSITY below is the committed data/caiso_hourly_intensity.csv
    from BEFORE this issue's regeneration (28 real CAISO days, embedded here so
    the check is self-contained once that file's shape changes to 365 days).
    For each of those 28 dates, recompute hourly kg/MWh from the FRESH raw cache
    and compare hour-by-hour against the old snapshot. This is exactly the kind
    of cross-source check CLAUDE.md's evidence-based principle (0) demands
    before retiring a data source -- it must show the actual measured gap, not
    just assert a boolean pass.
    """
    if not C.CAISO_DIR.is_dir() or not list(C.CAISO_DIR.glob("caiso_co2_*.csv")):
        raise SkipCase("the AC3 reproduction check needs the real raw CAISO cache "
                       f"({C.CAISO_DIR}), which this checkout does not have")
    per_date = []
    worst = None
    for day, old_vals in sorted(_OLD_28DAY_INTENSITY.items()):
        compact = day.replace("-", "")
        co2_f = C.CAISO_DIR / f"caiso_co2_{compact}.csv"
        dem_f = C.CAISO_DIR / f"caiso_demand_{compact}.csv"
        if not (co2_f.exists() and dem_f.exists()):
            raise AssertionError(
                f"{day}: no raw CAISO file in {C.CAISO_DIR} for a date the old "
                "28-day snapshot claims was covered -- the archive regressed")
        old_arr = np.asarray(old_vals, dtype=float)
        fresh = C.hourly_intensity(compact).sort_index().values
        assert fresh.shape == old_arr.shape == (24,), (day, fresh.shape)
        diff = fresh - old_arr
        # Relative difference per hour, denominator floored at 1.0 kg/MWh: CAISO
        # intensity legitimately crosses zero and goes negative at sunny spring
        # middays (net export), where a bare percentage blows up on noise far
        # below the 0.1 kg/MWh resolution both sources are stored at. The floor
        # only matters near zero; every point here differs by <=0.05 kg/MWh
        # (pure rounding at the shared 0.1 kg/MWh resolution), so the choice of
        # floor does not change the conclusion.
        rel = np.abs(diff) / np.maximum(np.abs(old_arr), 1.0)
        mard = float(rel.mean())
        per_date.append((day, mard, float(np.abs(diff).max())))
        if worst is None or mard > worst[1]:
            worst = (day, mard)
    overall_mard = float(np.mean([m for _, m, _ in per_date]))
    print(f"      AC3: {len(per_date)} dates checked; overall mean absolute "
          f"relative difference = {overall_mard * 100:.3f}%; worst date "
          f"{worst[0]} = {worst[1] * 100:.3f}%")
    assert overall_mard < 0.02, (
        f"overall MARD {overall_mard * 100:.3f}% exceeds the 2% AC3 tolerance")
    for day, mard, maxdiff in per_date:
        assert mard < 0.02, (
            f"{day}: MARD {mard * 100:.3f}% exceeds 2% (max abs diff "
            f"{maxdiff:.2f} kg/MWh) -- investigate before trusting the fresh cache")
    return (f"the fresh raw cache reproduces all {len(per_date)} old 28-day-"
            f"snapshot dates within 2% (overall MARD {overall_mard * 100:.3f}%, "
            f"worst date {worst[0]} at {worst[1] * 100:.3f}%)")


CASES = [
    case_hourly_intensity_parses_a_raw_day,
    case_build_covered_from_raw_reads_the_cache_and_legacy_days,
    case_committed_csv_schema_and_truncation_fail_closed,
    case_no_intensity_source_fails_closed,
    case_partial_raw_cache_merges_with_a_valid_committed_csv,
    case_intensity_sanity_bounds_reject_garbage,
    case_coverage_below_350_fails_closed_and_names_the_missing_dates,
    case_ac3_28day_reproduction_within_2pct,
]


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
