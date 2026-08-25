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

A later group of cases covers _read_scenario_a's no-EV branch (issue #147): on a
household whose intake says household.has_ev is false, behavior_rebuild.py
publishes scenarios.a as an explicit not-applicable stub, which is a VALID
artifact and must not abort the carbon run -- while every genuinely malformed
shape must keep aborting.

A further group covers the EV-APPLICABILITY AGREEMENT between this run's intake
flag and whichever behavior artifact the run resolves (issue #147). Accepting a
stub was only half the job: the resolver would also fall back to the COMMITTED
data/behavior_rebuild.json of a household that HAS an EV and quote its
$1,220.85/yr into a no-EV household's cost_note, beside that same artifact's own
not-applicable EV stubs -- one artifact stating both that this household has no
EV and what its EV savings are. Both directions now refuse, on both resolution
paths, and the refusal writes nothing.

A final group covers the same household's ARTIFACT (issue #147, adversarial
round). Not aborting was only half the job: the EV-domain figures still came out
as numeric zeros, which read as a measured finding ("moving this household's
charging saves no carbon") rather than as "this household has no charging to
move". They now carry the repo's {"not_applicable": true, "reason": ...} marker.
These cases check BOTH sides of the boundary -- every EV-dependent field marked,
every grid/meter-measured field still a real number, and an EV household's
figures untouched -- by RUNNING main() and reading what it wrote, so they need
the real Green Button archive and SKIP without it. The validator case needs no
private data and runs everywhere.

Run from the repo root:  ./.venv/bin/python analysis/test_carbon_fullyear.py
"""
import contextlib
import copy
import glob
import io
import json
import os
import pathlib
import re
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


# The explicit not-applicable STUB behavior_rebuild.py publishes for scenarios.a
# when the intake flag household.has_ev is false (issue #147). Copied from a
# genuinely generated no-EV behavior_rebuild.json rather than invented: same two
# keys, marker value True, same reason wording, and no "saved" key at all.
_NA_REASON = ("household.has_ev is false (intake applicability flag, "
              "DATA-SOURCES-CHEATSHEET.md) — the EV-only shift scenario does "
              "not apply to this household; set the flag true and complete the "
              "intake (charger.kw) to compute it")
_NA_STUB = {"not_applicable": True, "reason": _NA_REASON}


def _behavior_doc(scenario_a):
    """A behavior_rebuild.json holding one scenarios.a node. carbon_fullyear.py
    reads nothing else out of this artifact, so c/d are carried only to keep the
    fixture the same SHAPE as the real thing."""
    return json.dumps({"scenarios": {"a": scenario_a, "b": dict(_NA_STUB),
                                     "c": {"saved": 428.83},
                                     "d": {"saved": 857.66}}})


def _write_behavior(td, scenario_a):
    p = pathlib.Path(td) / "behavior_rebuild.json"
    p.write_text(_behavior_doc(scenario_a))
    return p


def case_read_scenario_a_accepts_the_not_applicable_stub():
    """Issue #147: on a no-EV household, behavior_rebuild.py publishes
    scenarios.a as an explicit {"not_applicable": true, "reason": ...} stub.
    That is not_applicable, NOT not_determined -- the intake DID determine the
    answer -- so the stub is a VALID artifact and _read_scenario_a must return
    (None, reason) rather than aborting the whole carbon run."""
    with tempfile.TemporaryDirectory() as td:
        p = _write_behavior(td, dict(_NA_STUB))
        saved, reason = C._read_scenario_a(p)          # must not raise
        assert saved is None, saved
        assert reason, "the stub's reason was dropped, so the artifact cannot say why"
        assert "household.has_ev" in reason, reason
        # the words the NOTICE lines are built from must survive too
        desc = C._describe_scenario_a((saved, reason))
        assert "NOT APPLICABLE" in desc, desc
        assert "$" not in desc, desc
    return ("_read_scenario_a returns (None, reason) for the explicit "
            "not-applicable stub instead of raising, and _describe_scenario_a "
            "words it without inventing a dollar figure")


def case_read_scenario_a_still_returns_a_figure_for_an_ev_household():
    """Positive control for the case above: an ordinary artifact must still
    come back as (float, "") -- a two-tuple, with an empty reason. Without
    this, the stub case would pass just as happily against a function that had
    been broken into returning None for everything."""
    with tempfile.TemporaryDirectory() as td:
        p = _write_behavior(td, {"saved": 1220.85})
        v = C._read_scenario_a(p)
        assert isinstance(v, tuple) and len(v) == 2, v
        saved, reason = v
        assert isinstance(saved, float) and abs(saved - 1220.85) < 1e-9, saved
        assert reason == "", reason
        assert "$1,220.85/yr" in C._describe_scenario_a(v), C._describe_scenario_a(v)
    return ("_read_scenario_a returns (float, \"\") for an ordinary EV "
            "household, so the stub branch has not swallowed the normal path")


def case_read_scenario_a_fails_closed_on_every_malformed_shape():
    """A stub is valid; MALFORMED is not, and must keep aborting. The
    tolerated shape is the explicit marker, not "an a that cannot be read":
    if the branch ever widened to any unreadable a, a genuinely broken
    behavior artifact would be published as a no-EV household and the report
    would quietly lose the mistimed-charging figure instead of failing."""
    doc_variants = [
        ("no scenarios block at all", json.dumps({"baseline": {"model_bill": 1}})),
        ("scenarios with no a", json.dumps({"scenarios": {"c": {"saved": 1.0}}})),
        ("unparseable JSON", "{not valid json"),
    ]
    node_variants = [
        ("a with no saved and no marker", {"label": "a: EV only"}),
        ("a that is not a dict", 1220.85),
        ("an explicit not_applicable FALSE with no figure", {"not_applicable": False}),
        # A NON-NUMERIC string. A numeric string ("1220.85") is coerced by
        # float() and comes back as a figure -- long-standing behavior of this
        # reader, unchanged by issue #147, and deliberately not asserted here
        # in either direction so this case guards the stub/malformed boundary
        # rather than pinning an unrelated coercion.
        ("saved that is a string", {"saved": "one thousand"}),
        ("saved that is null", {"saved": None}),
    ]
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        for label, text in doc_variants:
            p = tdp / "b.json"
            p.write_text(text)
            try:
                got = C._read_scenario_a(p)
                raise AssertionError(f"{label}: accepted, returned {got!r}")
            except SystemExit as e:
                assert "cannot read scenarios.a.saved" in str(e), (label, str(e))
        for label, node in node_variants:
            p = _write_behavior(td, node)
            try:
                got = C._read_scenario_a(p)
                raise AssertionError(f"{label}: accepted, returned {got!r}")
            except SystemExit as e:
                assert "cannot read scenarios.a.saved" in str(e), (label, str(e))
        # a file that is not there at all
        try:
            got = C._read_scenario_a(tdp / "absent.json")
            raise AssertionError(f"absent file: accepted, returned {got!r}")
        except SystemExit as e:
            assert "cannot read scenarios.a.saved" in str(e), str(e)
    return (f"_read_scenario_a still fails closed on all "
            f"{len(doc_variants) + len(node_variants) + 1} malformed shapes "
            "(only the explicit not_applicable:true marker is tolerated)")


_EV_FIGURE = {"saved": 1220.85}     # an EV household's scenarios.a


def _resolve_scenario_a(ev_applies, run_node, committed_node):
    """Run _scenario_a_saved() against a chosen intake flag and a chosen
    behavior artifact in EACH of the two places the resolver looks.

    run_node/committed_node are scenarios.a nodes, or None to leave that copy
    absent, so one helper can build the current-run path, the committed-fallback
    path and the both-present path. Returns (value, printed output); a refusal
    propagates as SystemExit, which is what the mismatch cases catch.

    Nothing here needs private data: _scenario_a_saved() reads only the behavior
    artifact and behavior_rebuild's EV_ANALYSIS predicate.
    """
    import behavior_rebuild as br
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        run_dir = tdp / "run"
        run_dir.mkdir()
        data_dir = tdp / "data"
        data_dir.mkdir()
        if run_node is not None:
            _write_behavior(run_dir, run_node)
        if committed_node is not None:
            _write_behavior(data_dir, committed_node)
        old_data, old_flag, old_cwd = C.DATA, br.EV_ANALYSIS, os.getcwd()
        C.DATA = data_dir
        br.EV_ANALYSIS = ev_applies
        os.chdir(run_dir)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                v = C._scenario_a_saved()
        finally:
            C.DATA, br.EV_ANALYSIS = old_data, old_flag
            os.chdir(old_cwd)
        return v, buf.getvalue()


def _refused_mismatch(ev_applies, run_node, committed_node, expect_path_in):
    """One mismatch, resolved and refused. Returns the refusal message after
    checking the three things it has to carry: the intake flag it read, WHICH
    artifact disagreed (by path), and the remedy."""
    try:
        v, out = _resolve_scenario_a(ev_applies, run_node, committed_node)
    except SystemExit as e:
        msg = str(e)
        assert "EV APPLICABILITY MISMATCH" in msg, msg
        assert "household.has_ev" in msg, (
            "the refusal does not name the intake flag it read: " + msg)
        assert expect_path_in in msg, (
            f"the refusal does not name the artifact it actually read "
            f"({expect_path_in}): {msg}")
        assert "behavior_rebuild.py in this working directory" in msg, (
            "the refusal does not give the remedy: " + msg)
        return msg
    raise AssertionError(
        f"accepted a mismatched behavior artifact (ev_applies={ev_applies}, "
        f"run={run_node!r}, committed={committed_node!r}) and returned {v!r}; "
        f"output was: {out}")


def case_scenario_a_refuses_an_ev_figure_on_a_no_ev_household():
    """The reproduced defect (issue #147). A household whose intake says
    household.has_ev is false, with no current-run behavior artifact, fell back
    to the COMMITTED data/behavior_rebuild.json of a household that HAS an EV,
    printed a NOTICE and carried on -- publishing a carbon artifact whose
    household_inputs.ev_kwh_detected is a not-applicable stub ("this household
    has no EV") and whose cost_note quotes $1,220.85/yr of that OTHER
    household's mistimed-charging saving. One artifact, both claims.

    CLAUDE.md section 0: a figure has to be THIS household's. Checked on BOTH
    resolution paths -- the committed fallback where it was reproduced, and a
    current-run copy left in the working directory by a different household's
    run, which is the same defect one directory across. In that second case the
    committed copy MATCHES the intake, so a guard placed only on the fallback
    path would let it straight through.
    """
    msgs = []
    # committed fallback: no current-run copy at all
    msgs.append(_refused_mismatch(False, None, dict(_EV_FIGURE),
                                  os.path.join("data", "behavior_rebuild.json")))
    # current-run copy from another household, beside a MATCHING committed one
    msgs.append(_refused_mismatch(False, dict(_EV_FIGURE), dict(_NA_STUB),
                                  os.path.join("run", "behavior_rebuild.json")))
    for msg in msgs:
        assert "1,220.85" in msg, (
            "the refusal does not say what figure it is refusing: " + msg)
    return ("a no-EV household refuses an EV household's scenarios.a figure on "
            "both resolution paths (committed fallback and current-run copy), "
            "naming the flag, the artifact and the remedy")


def case_scenario_a_refuses_a_no_ev_stub_on_an_ev_household():
    """The mirror direction, which is a silent DELETION rather than a false
    figure: a household whose intake says it has an EV, resolving a behavior
    artifact whose scenarios.a is the no-EV not-applicable stub, would publish
    "there is no mistimed-charging saving to price" for a household that has
    one. Same two resolution paths."""
    msgs = []
    msgs.append(_refused_mismatch(True, None, dict(_NA_STUB),
                                  os.path.join("data", "behavior_rebuild.json")))
    msgs.append(_refused_mismatch(True, dict(_NA_STUB), dict(_EV_FIGURE),
                                  os.path.join("run", "behavior_rebuild.json")))
    for msg in msgs:
        assert "not-applicable stub" in msg, msg
        assert "$" not in msg, (
            "the refusal invents a dollar figure for an absent saving: " + msg)
    return ("an EV household refuses a no-EV household's not-applicable "
            "scenarios.a stub on both resolution paths, rather than silently "
            "publishing 'no saving to price' over a real saving")


def case_matching_households_still_resolve_the_behavior_artifact():
    """Positive control for the two cases above. Without it a build that
    refused EVERY artifact -- or that had broken the committed-fallback branch
    outright -- would pass them both.

    All four agreeing combinations resolve, and the committed-fallback NOTICE
    (the legitimate case the reproduction shares its shape with: a matching
    household that simply has not re-run behavior_rebuild.py in this working
    directory) must still print and still say where the value came from.
    """
    # EV household, committed fallback: the NOTICE path
    (saved, reason), out = _resolve_scenario_a(True, None, dict(_EV_FIGURE))
    assert abs(saved - 1220.85) < 1e-9, saved
    assert reason == "", reason
    assert "NOTICE: no current-run" in out, out
    assert "$1,220.85/yr" in out, out

    # no-EV household, committed fallback: the same NOTICE, no dollar figure
    (saved, reason), out = _resolve_scenario_a(False, None, dict(_NA_STUB))
    assert saved is None, saved
    assert "household.has_ev" in reason, reason
    assert "NOTICE: no current-run" in out, out
    assert "NOT APPLICABLE" in out, out
    assert "$" not in out, (
        "the no-EV NOTICE prices the absent saving: " + out)

    # both households with a current-run copy that agrees with the committed one
    (saved, _), out = _resolve_scenario_a(True, dict(_EV_FIGURE), dict(_EV_FIGURE))
    assert abs(saved - 1220.85) < 1e-9, saved
    assert "agrees with the committed" in out, out
    (saved, reason), out = _resolve_scenario_a(False, dict(_NA_STUB), dict(_NA_STUB))
    assert saved is None and reason, (saved, reason)
    assert "agrees with the committed" in out, out
    return ("a behavior artifact whose EV applicability MATCHES the intake "
            "still resolves in all four agreeing combinations, and the "
            "committed-fallback NOTICE still prints")


def case_ev_applicability_mismatch_writes_nothing():
    """A refusal has to leave the artifacts exactly as it found them.
    carbon_fullyear.py writes both artifacts atomically at the very end
    (TECHNICAL.md 3.15), and the applicability check sits before the household
    pipeline, so a mismatched run must abort with both committed files
    byte-unchanged and no .tmp/.bak debris beside them.

    Needs no private data: main() refuses in _scenario_a_saved(), before it
    ever loads usage.csv.
    """
    import behavior_rebuild as br
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        data_dir = tdp / "data"
        data_dir.mkdir()
        run_dir = tdp / "run"
        run_dir.mkdir()
        days = pd.date_range(C.YEAR_START, C.YEAR_END, freq="D")
        hourly_csv = data_dir / "caiso_hourly_intensity.csv"
        pd.DataFrame(_shaped_intensity_rows(days),
                     columns=["date", "hour", "kgco2_per_mwh"]).to_csv(
                         hourly_csv, index=False)
        results_json = data_dir / "carbon_fullyear_results.json"
        results_json.write_text(json.dumps({"sentinel": "untouched"}))
        # the reproduced shape: no current-run copy, an EV household's committed
        # artifact, a no-EV intake
        _write_behavior(data_dir, dict(_EV_FIGURE))
        before = {p.name: p.read_bytes() for p in (hourly_csv, results_json)}

        old = (C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON, C.DATA,
               br.EV_ANALYSIS, os.getcwd())
        C.CAISO_DIR = tdp / "no_raw_cache"
        C.HOURLY_CSV = hourly_csv
        C.RESULTS_JSON = results_json
        C.DATA = data_dir
        br.EV_ANALYSIS = False
        os.chdir(run_dir)
        refused = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                C.main()
        except SystemExit as e:
            refused = str(e)
        except BaseException as e:      # noqa: BLE001
            # Anything OTHER than the refusal means main() carried on past the
            # check and fell over somewhere downstream. That is the defect this
            # case names: the check has to refuse BEFORE the run does any work,
            # or a refusal can leave a half-written artifact behind.
            raise AssertionError(
                "main() got past the EV-applicability check on a mismatched "
                f"behavior artifact and failed later with {type(e).__name__}: "
                "{} -- the refusal must come before the run does any work"
                .format(e)) from None
        finally:
            (C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON, C.DATA,
             br.EV_ANALYSIS) = old[:5]
            os.chdir(old[5])
        assert refused is not None, "main ran with a mismatched behavior artifact"
        assert "EV APPLICABILITY MISMATCH" in refused, refused
        after = {p.name: p.read_bytes() for p in (hourly_csv, results_json)}
        assert after == before, (
            "the refused run rewrote an artifact: "
            + ", ".join(k for k in before if before[k] != after[k]))
        debris = sorted(p.name for p in data_dir.iterdir()
                        if p.suffix in (".tmp", ".bak"))
        assert not debris, f"the refused run left {debris} behind"
    return ("a run refused for EV-applicability mismatch leaves both committed "
            "artifacts byte-unchanged and no .tmp/.bak debris")


def _shaped_intensity_rows(days):
    """A synthetic 365-day intensity table with a real grid's diurnal SHAPE --
    dirty overnight, clean midday. A FLAT table would make the window-means
    comparison identically zero for every household, which is exactly the thing
    the grid-boundary case below has to be able to see, so a flat fixture would
    let that case pass against a script that had withheld the figure. The three
    numbers are invented fixture values; no published figure comes from them."""
    def kg(hour):
        if hour < 6 or hour >= 22:
            return 280.0                    # overnight: gas-heavy
        if 10 <= hour < 14:
            return 110.0                    # solar midday: cleanest
        return 190.0
    return [(d.strftime("%Y-%m-%d"), h, kg(h)) for d in days for h in range(24)]


_CARBON_RUNS = {}


def _carbon_artifact(ev_applies):
    """The artifact carbon_fullyear.main() ACTUALLY WRITES for a household with
    (True) or without (False) an EV, cached per household kind.

    Nothing about the carbon artifact is hand-mocked. The no-EV household is
    made no-EV the way a real one is: behavior_rebuild's own EV_ANALYSIS
    predicate (household.has_ev false) is switched off, so its detector returns
    an EV-free year and carbon_fullyear reads that same predicate -- and the
    behavior artifact in the working directory carries the matching
    not-applicable stub, so the fixture is one coherent household rather than a
    no-EV artifact beside an EV detection.

    main() reaches these fields only after the household 15-minute pipeline, so
    this needs the real Green Button archive and SKIPs without it, like the
    merge case above.
    """
    if ev_applies in _CARBON_RUNS:
        return _CARBON_RUNS[ev_applies]
    usage_files = sorted(glob.glob(
        str(C.ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")))
    if not usage_files:
        raise SkipCase("the rendered-artifact checks run the full pipeline, "
                       "which needs the real Green Button archive")
    import behavior_rebuild as br
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        days = pd.date_range(C.YEAR_START, C.YEAR_END, freq="D")
        committed_csv = tdp / "hourly.csv"
        pd.DataFrame(_shaped_intensity_rows(days),
                     columns=["date", "hour", "kgco2_per_mwh"]).to_csv(
                         committed_csv, index=False)
        # _scenario_a_saved() prefers the CURRENT-RUN copy in the working
        # directory, so put the matching artifact there and run from there.
        run_dir = tdp / "run"
        run_dir.mkdir()
        _write_behavior(run_dir,
                        {"saved": 1220.85} if ev_applies else dict(_NA_STUB))

        old = (C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON, br.CSV, os.getcwd(),
               br.EV_ANALYSIS)
        C.CAISO_DIR = tdp / "no_raw_cache"          # absent: committed CSV only
        C.HOURLY_CSV = committed_csv
        C.RESULTS_JSON = tdp / "results.json"       # no prior baseline
        br.CSV = usage_files[0]
        br.EV_ANALYSIS = ev_applies
        os.chdir(run_dir)
        try:
            C.main()
            doc = json.loads(C.RESULTS_JSON.read_text())
        finally:
            (C.CAISO_DIR, C.HOURLY_CSV, C.RESULTS_JSON, br.CSV) = old[:4]
            os.chdir(old[4])
            br.EV_ANALYSIS = old[5]
    _CARBON_RUNS[ev_applies] = doc
    return doc


# The EV-domain fields the adversarial finding named, written out here rather
# than read from C.EV_DEPENDENT_FIELDS: a case that took the list under test as
# its own expectation would keep passing after someone shrank that list, which
# is precisely the regression the marker exists to stop.
_EV_FIELDS_FROM_THE_FINDING = {
    "household_inputs.ev_kwh_detected",
    "household_inputs.ev_kwh_mistimed_on_off_peak",
    "footprints_kg_co2_per_yr.b_mistimed_ev_moved_to_sop_00_06",
    "footprints_kg_co2_per_yr.c_mistimed_ev_moved_to_midday_10_14",
    "footprints_kg_co2_per_yr.detail.mistimed_ev_kg_at_current_hours",
    "footprints_kg_co2_per_yr.detail.mistimed_ev_kg_if_charged_00_06",
    "footprints_kg_co2_per_yr.detail.mistimed_ev_kg_if_charged_10_14",
    "footprints_kg_co2_per_yr.detail.midday_cleaner_than_overnight_by",
    "old_vs_new.ev_shift_delta_to_sop_kg",
    "old_vs_new.ev_shift_delta_to_midday_kg",
    "old_vs_new.midday_cleaner_than_overnight_by_kg",
}


def _is_stub(v):
    return isinstance(v, dict) and v.get("not_applicable") is True


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def case_no_ev_artifact_marks_every_ev_field_not_applicable():
    """The rendered artifact, end to end: on a household with no EV, every
    EV-domain figure must carry the explicit not-applicable marker instead of
    the 0.0 the arithmetic produces.

    A zero here is a FALSE MEASUREMENT. "0 kg saved by moving the charging"
    reads as a finding -- the fix is worthless on this house -- when the truth
    is that there is no charging to move (CLAUDE.md section 0: a determinate
    "does not apply" is not a measured value). The b/c scenario footprints are
    the sharpest version: they come out as two identical numbers, presented as
    two distinct scenarios, identical only because the multiplier is zero.
    """
    doc = _carbon_artifact(False)
    inventory = {".".join(p) for p in C.EV_DEPENDENT_FIELDS}
    dropped = _EV_FIELDS_FROM_THE_FINDING - inventory
    assert not dropped, (
        f"these EV fields are no longer declared EV-dependent: {sorted(dropped)}")
    bad = [(".".join(p), C._dig(doc, p)) for p in C.EV_DEPENDENT_FIELDS
           if not _is_stub(C._dig(doc, p))]
    assert not bad, (
        "a household with no EV published these EV figures as values instead "
        f"of not-applicable markers: {bad} -- a 0.0 here reads as a measured "
        "finding, not as 'this household has no EV'")
    for path in C.EV_DEPENDENT_FIELDS:
        reason = str(C._dig(doc, path).get("reason", "")).strip()
        assert reason, f"{'.'.join(path)}: stub with no reason"
        assert "household.has_ev" in reason, (".".join(path), reason)
    return (f"a no-EV household publishes all {len(C.EV_DEPENDENT_FIELDS)} "
            "EV-domain carbon fields as not_applicable stubs naming "
            "household.has_ev, never as a computed zero")


def case_no_ev_artifact_keeps_the_grid_measured_figures():
    """The boundary, in the SAFE direction. Withholding a figure that is
    measured for every household is the same error as publishing a fake zero,
    just pointing the other way -- so the grid-intensity section, the
    current-import footprint, the metered import/export kWh and the avoided
    export carbon must all still be real numbers on a household with no EV.

    In particular intensity_kg_per_mwh.window_means_annual still answers "how
    much cleaner is midday than overnight" -- that is a property of CAISO, not
    of this house's charging habits. Only the EV-load APPLICATION of it
    (footprints...detail.midday_cleaner_than_overnight_by) becomes a stub, and
    that stub has to point the reader at the grid-side answer so the marker is
    never read as "unknown".
    """
    doc = _carbon_artifact(False)
    for path in C.GRID_MEASURED_FIELDS:
        v = C._dig(doc, path)
        assert _is_number(v), (
            f"{'.'.join(path)} is measured for every household but came back "
            f"{v!r} -- withholding a grid/meter figure over a question about "
            "the EV domain is as wrong as publishing a fake zero")
    hours = doc["intensity_kg_per_mwh"]["annual_avg_by_hour"]
    assert len(hours) == 24 and all(_is_number(x) for x in hours), hours
    wm = doc["intensity_kg_per_mwh"]["window_means_annual"]
    gap = wm["sop_overnight_00_06"] - wm["solar_midday_10_14"]
    assert gap != 0, (
        "the grid's midday-vs-overnight comparison came back as a flat zero on "
        f"a no-EV household: {wm}")
    mid = doc["footprints_kg_co2_per_yr"]["detail"]["midday_cleaner_than_overnight_by"]
    assert _is_stub(mid), (
        "the EV-load application of the window comparison is not marked "
        f"not-applicable on a household with no EV: {mid!r}")
    reason = str(mid.get("reason", ""))
    assert "window_means_annual" in reason, (
        "the stub that replaced the EV application of the window comparison "
        f"does not say where the grid-side answer still lives: {reason}")
    return ("a no-EV household keeps every grid/meter-measured carbon figure as "
            f"a real number (overnight {wm['sop_overnight_00_06']} vs midday "
            f"{wm['solar_midday_10_14']} kg CO2/MWh, gap {gap:.1f}), and the "
            "EV-application stub points at it")


def case_ev_household_keeps_every_carbon_figure_a_real_number():
    """Positive control. Without it the two cases above would pass just as
    happily against a script that had started stubbing these fields for EVERY
    household -- which would delete this household's actual carbon findings.

    Checks the switch itself both ways, then the artifact main() writes for an
    EV household: every field in the EV inventory comes back as a number (or,
    for the three old-vs-new comparison nodes, a pair of numbers), the detected
    EV energy is positive, and the two scenario footprints DIFFER -- the thing
    they cannot do on a household with no EV.
    """
    assert C._ev_field(449.0, True, "x") == 449.0, "an EV household lost a figure"
    assert not _is_stub(C._ev_field(0.0, True, "x")), "the marker leaked onto an EV household"
    assert _is_stub(C._ev_field(0.0, False, "x")), "a no-EV household published a value"

    doc = _carbon_artifact(True)
    for path in C.EV_DEPENDENT_FIELDS:
        v = C._dig(doc, path)
        assert not _is_stub(v), (
            f"{'.'.join(path)}: an EV household published the not-applicable "
            "marker instead of its measured figure")
        if isinstance(v, dict):                     # old_vs_new comparison node
            assert set(v) == {"old", "new"}, (".".join(path), sorted(v))
            for k in ("old", "new"):
                assert _is_number(v[k]), (".".join(path), k, v[k])
        else:
            assert _is_number(v), (".".join(path), v)
    hi = doc["household_inputs"]
    assert hi["ev_kwh_detected"] > 0, hi["ev_kwh_detected"]
    assert hi["ev_kwh_mistimed_on_off_peak"] > 0, hi["ev_kwh_mistimed_on_off_peak"]
    f = doc["footprints_kg_co2_per_yr"]
    assert (f["b_mistimed_ev_moved_to_sop_00_06"]
            != f["c_mistimed_ev_moved_to_midday_10_14"]), (
        "the two scenario footprints are identical on a household that HAS an "
        "EV -- the EV term has been lost somewhere upstream")
    return ("an EV household still publishes every EV-domain carbon figure as a "
            f"real number ({hi['ev_kwh_detected']} kWh detected), so the "
            "not-applicable marker has not leaked onto the measured path")


def case_applicability_validator_refuses_a_half_marked_artifact():
    """carbon_fullyear validates its OWN output before either temp file is
    written, so a partial edit cannot ship an artifact that is half marked and
    half zeros. Both directions, on the committed artifact for this household:
    numbers declared no-EV, one field converted and the rest not, a stub
    declared EV, a stub with no reason, and a grid figure withheld.

    Needs no private data -- data/carbon_fullyear_results.json is committed --
    so this case runs in CI, where the three above SKIP.
    """
    doc = json.loads((C.ROOT / "data" / "carbon_fullyear_results.json").read_text())
    C._validate_applicability(doc, True)        # the committed artifact is consistent

    def refused(results, ev_applies, want):
        try:
            C._validate_applicability(results, ev_applies)
        except SystemExit as e:
            assert want in str(e), (want, str(e))
            return
        raise AssertionError(f"accepted an artifact that should be refused ({want})")

    refused(doc, False, "never a computed zero")
    half = copy.deepcopy(doc)
    half["household_inputs"]["ev_kwh_detected"] = C._not_applicable("x")
    refused(half, False, "never a computed zero")
    refused(half, True, "household.has_ev is not false")

    marked = copy.deepcopy(doc)
    for path in C.EV_DEPENDENT_FIELDS:
        C._dig(marked, path[:-1])[path[-1]] = C._not_applicable("x")
    C._validate_applicability(marked, False)    # fully marked: must NOT raise

    no_reason = copy.deepcopy(marked)
    no_reason["household_inputs"]["ev_kwh_detected"]["reason"] = "  "
    refused(no_reason, False, "not_applicable stub with no reason")

    withheld = copy.deepcopy(marked)
    withheld["intensity_kg_per_mwh"]["window_means_annual"][
        "solar_midday_10_14"] = C._not_applicable("x")
    refused(withheld, False, "refusing to withhold a measured figure")
    return ("the applicability validator refuses a half-marked artifact, a "
            "reasonless stub, a stub on an EV household, and a withheld "
            "grid-measured figure")


def case_no_ev_cost_note_prices_nothing_instead_of_zero():
    """The rendered artifact, end to end: on a no-EV household the cost_note
    must SAY there is no mistimed-charging saving to price and name the flag
    that decided it -- and must not carry a dollar figure at all. An absent
    figure rendered as "$0.00" would read as a measured result showing the fix
    is worthless, which is a different claim from "the fix does not exist
    here" (CLAUDE.md section 0: not determined is not zero).
    """
    note = _carbon_artifact(False)["cost_note"]

    assert "household.has_ev" in note, note
    low = note.lower()
    assert "no mistimed-charging dollar saving to price" in low, note
    assert "not-applicable stub" in low, note
    # No dollar figure anywhere: not "$0.00", not "$0", not any amount at all.
    money = re.search(r"\$\s*-?[\d,]+(?:\.\d+)?", note)
    assert money is None, (
        f"the no-EV cost_note prices the absent saving as {money.group(0)!r}; "
        "an absent figure must never render as a dollar amount")
    assert "scenario 'a'" in low, note      # it still says WHICH figure is absent
    return ("the rendered no-EV cost_note names household.has_ev, states there "
            "is no mistimed-charging saving to price, and carries no dollar "
            "figure at all for the absent saving")


CASES = [
    case_hourly_intensity_parses_a_raw_day,
    case_build_covered_from_raw_reads_the_cache_and_legacy_days,
    case_committed_csv_schema_and_truncation_fail_closed,
    case_no_intensity_source_fails_closed,
    case_partial_raw_cache_merges_with_a_valid_committed_csv,
    case_intensity_sanity_bounds_reject_garbage,
    case_coverage_below_350_fails_closed_and_names_the_missing_dates,
    case_ac3_28day_reproduction_within_2pct,
    case_read_scenario_a_accepts_the_not_applicable_stub,
    case_read_scenario_a_still_returns_a_figure_for_an_ev_household,
    case_read_scenario_a_fails_closed_on_every_malformed_shape,
    case_scenario_a_refuses_an_ev_figure_on_a_no_ev_household,
    case_scenario_a_refuses_a_no_ev_stub_on_an_ev_household,
    case_matching_households_still_resolve_the_behavior_artifact,
    case_ev_applicability_mismatch_writes_nothing,
    case_no_ev_artifact_marks_every_ev_field_not_applicable,
    case_no_ev_artifact_keeps_the_grid_measured_figures,
    case_ev_household_keeps_every_carbon_figure_a_real_number,
    case_applicability_validator_refuses_a_half_marked_artifact,
    case_no_ev_cost_note_prices_nothing_instead_of_zero,
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
