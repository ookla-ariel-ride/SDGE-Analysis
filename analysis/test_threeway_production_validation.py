#!/usr/bin/env python3
"""Tests for threeway_production_validation.py (issue #37).

Same pattern as test_quiet_night_floor.py: synthetic-frame unit tests that
need no private archive at all, plus a handful of real-archive cases gated
with SkipCase when the private Green Button / SAM export is absent. The
issue's central acceptance criterion -- the two DST dates (2025-11-02,
2026-03-08) must be EXPLICITLY null in `meter_derived`, never silently
dropped or silently averaged in, and the correlation/MAE validation must
exclude them -- gets both a synthetic case (with a planted outlier that would
change the stats if it leaked in) and a real-archive case against the
regenerated artifact itself.

Run from the repo root:  ./.venv/bin/python analysis/test_threeway_production_validation.py
"""
import csv
import datetime as dt
import glob
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import rates as R                                  # noqa: E402
import threeway_production_validation as TPV        # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "private" / "verify"
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
ARTIFACT = ROOT / "data" / "threeway_production_validation.csv"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button / SAM archive). Counted as neither pass nor fail."""


def _require_archive():
    files = sorted(glob.glob(USAGE_GLOB))
    if (not files or not (SANDBOX / "samA.csv").exists()
            or not (SANDBOX / "samB.csv").exists()):
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{SANDBOX}/samA.csv, samB.csv, none of which this "
                       "checkout has")
    return files[0]


# ---------------------------------------------------------------------------
# (a) dst_dates_in -- reads the SAME table analysis/rates.py publishes
# ---------------------------------------------------------------------------
@case
def case_dst_dates_in_matches_the_canonical_rates_table():
    dates = [dt.date(2025, 7, 24) + dt.timedelta(days=i) for i in range(365)]
    got = TPV.dst_dates_in(dates)
    spring_2026, _fall_2026 = R.dst_transition_sundays(2026)
    _spring_2025, fall_2025 = R.dst_transition_sundays(2025)
    assert got == {fall_2025, spring_2026}, got
    assert got == {dt.date(2025, 11, 2), dt.date(2026, 3, 8)}, got
    return f"dst_dates_in found exactly {sorted(got)}, matching rates.dst_transition_sundays"


@case
def case_dst_dates_in_is_empty_outside_the_transition_dates():
    dates = [dt.date(2026, 6, 1) + dt.timedelta(days=i) for i in range(10)]
    assert TPV.dst_dates_in(dates) == set()
    return "a window with no DST Sunday inside it returns an empty set"


# ---------------------------------------------------------------------------
# (b) window_dates -- the shared, contiguous pvoutput/enphase date range
# ---------------------------------------------------------------------------
@case
def case_window_dates_is_the_contiguous_shared_range():
    d0 = dt.date(2026, 1, 1)
    pv = {d0 + dt.timedelta(days=i): 1.0 for i in range(10)}
    en = {d0 + dt.timedelta(days=i): 1.0 for i in range(2, 12)}  # offset overlap
    dates = TPV.window_dates(pv, en)
    assert dates[0] == d0 + dt.timedelta(days=2), dates
    assert dates[-1] == d0 + dt.timedelta(days=9), dates
    assert len(dates) == 8, dates
    return f"window_dates found the {len(dates)}-day shared range {dates[0]}..{dates[-1]}"


@case
def case_window_dates_fails_closed_on_a_gap_in_the_shared_range():
    d0 = dt.date(2026, 1, 1)
    dates_present = [d0, d0 + dt.timedelta(days=1), d0 + dt.timedelta(days=3)]  # day 2 missing
    pv = {d: 1.0 for d in dates_present}
    en = {d: 1.0 for d in dates_present}
    try:
        TPV.window_dates(pv, en)
    except SystemExit as e:
        assert "contiguous" in str(e), e
        return "a gap in the shared pvoutput/enphase date range is refused, not silently spanned"
    raise AssertionError("window_dates should have refused a non-contiguous shared range")


# ---------------------------------------------------------------------------
# (c) derive_daily -- the core identity, and the DST exclusion
# ---------------------------------------------------------------------------
def _hourly_dicts_for(d, sam_by_hour, imp_by_hour, exp_by_hour=None):
    """Build one day's {(d,h): kwh} and {(d,h): (imp,exp)} entries."""
    exp_by_hour = exp_by_hour or {h: 0.0 for h in range(24)}
    sam = {(d, h): sam_by_hour[h] for h in range(24)}
    gb = {(d, h): (imp_by_hour[h], exp_by_hour[h]) for h in range(24)}
    return sam, gb


@case
def case_derive_daily_reproduces_the_load_minus_import_plus_export_identity():
    d = dt.date(2026, 6, 15)
    sam_h = {h: 2.0 for h in range(24)}
    imp_h = {h: 0.5 for h in range(24)}
    exp_h = {h: 0.2 for h in range(24)}
    sam, gb = _hourly_dicts_for(d, sam_h, imp_h, exp_h)
    daily = TPV.derive_daily([d], set(), sam, gb)
    # each hour: max(2.0 - 0.5 + 0.2, 0) = 1.7; 24 hours -> 40.8
    assert abs(daily[d] - 40.8) < 1e-9, daily
    return f"derive_daily reproduces max(sam-import+export,0) summed hourly: {daily[d]} kWh"


@case
def case_derive_daily_clips_negative_hours_at_zero():
    d = dt.date(2026, 6, 16)
    sam_h = {h: 1.0 for h in range(24)}
    imp_h = {h: 5.0 for h in range(24)}  # import >> sam+export -> raw identity goes negative
    sam, gb = _hourly_dicts_for(d, sam_h, imp_h)
    daily = TPV.derive_daily([d], set(), sam, gb)
    assert daily[d] == 0.0, daily
    return "an hour whose raw identity goes negative (instrument noise) clips to zero, not negative"


@case
def case_derive_daily_nulls_the_dst_dates_without_attempting_reconstruction():
    """The DST date is given a hourly grid that DOES NOT reconcile physically
    (an SAM value far below import - export, which would otherwise blow the
    identity deeply negative) -- proving derive_daily skips it outright
    rather than computing a wrong number and only null-ing it after the
    fact."""
    ok_day = dt.date(2026, 3, 7)
    dst_day = dt.date(2026, 3, 8)  # a real DST spring-forward Sunday
    sam_h = {h: 2.0 for h in range(24)}
    imp_h = {h: 0.5 for h in range(24)}
    sam_ok, gb_ok = _hourly_dicts_for(ok_day, sam_h, imp_h)
    sam_dst, gb_dst = _hourly_dicts_for(dst_day, sam_h, imp_h)
    sam = {**sam_ok, **sam_dst}
    gb = {**gb_ok, **gb_dst}
    daily = TPV.derive_daily([ok_day, dst_day], {dst_day}, sam, gb)
    assert daily[dst_day] is None, daily
    assert daily[ok_day] is not None and daily[ok_day] > 0, daily
    return "the DST date is null; the adjacent ordinary day (identical hourly inputs) is not"


@case
def case_derive_daily_fails_closed_on_a_genuine_non_dst_gap():
    d = dt.date(2026, 6, 17)
    sam_h = {h: 2.0 for h in range(24)}
    imp_h = {h: 0.5 for h in range(24)}
    sam, gb = _hourly_dicts_for(d, sam_h, imp_h)
    del gb[(d, 12)]  # a real gap, not a DST date
    try:
        TPV.derive_daily([d], set(), sam, gb)
    except SystemExit as e:
        assert str(d) in str(e), e
        assert "23/24" in str(e), e
        return "a non-DST day missing an hour of raw coverage is refused, not silently nulled"
    raise AssertionError("derive_daily should have refused an incomplete non-DST day")


# ---------------------------------------------------------------------------
# (d) validation_stats -- must exclude the DST dates, proven with a planted
#     outlier that WOULD move the stats if it leaked into the computation
# ---------------------------------------------------------------------------
@case
def case_validation_stats_excludes_the_dst_dates_even_with_a_planted_outlier():
    d0 = dt.date(2026, 1, 1)
    dates = [d0 + dt.timedelta(days=i) for i in range(10)]
    dst_day = dates[5]
    derived = {d: 10.0 for d in dates}
    reference = {d: 10.0 for d in dates}
    derived[dst_day] = None          # what the real generator would write
    reference[dst_day] = 999.0       # an independent instrument, unaffected by the SAM/DST issue

    stats = TPV.validation_stats(dates, {dst_day}, derived, reference)
    assert stats["n_days"] == 9, stats
    assert stats["mae_kwh"] == 0.0, (
        "the planted 999.0 outlier on the DST date must not reach the MAE -- "
        f"got {stats['mae_kwh']}")
    assert abs(stats["ratio_derived_over_reference"] - 1.0) < 1e-9, stats
    return (f"validation_stats used {stats['n_days']} of {len(dates)} days, correctly "
           "excluding the DST date's planted outlier from MAE/ratio")


@case
def case_validation_stats_would_have_caught_the_outlier_if_it_leaked_in():
    """A control: prove the planted outlier in the case above is actually
    capable of moving the statistic, so the previous case's pass is not
    vacuous (a validation_stats that accidentally excluded EVERY day would
    pass it too)."""
    d0 = dt.date(2026, 1, 1)
    dates = [d0 + dt.timedelta(days=i) for i in range(10)]
    outlier_day = dates[5]
    derived = {d: 10.0 for d in dates}
    reference = {d: 10.0 for d in dates}
    reference[outlier_day] = 999.0
    stats = TPV.validation_stats(dates, set(), derived, reference)  # no DST exclusion this time
    assert stats["mae_kwh"] > 50, (
        "control failed: an unexcluded 999.0 outlier should move the MAE "
        f"sharply, got {stats['mae_kwh']}")
    return f"the same outlier, NOT excluded, moves MAE to {stats['mae_kwh']:.1f} -- the exclusion in the case above is real"


# ---------------------------------------------------------------------------
# (e) write_csv -- round-trips the null convention as a blank/NaN cell
# ---------------------------------------------------------------------------
@case
def case_write_csv_writes_dst_nulls_as_blank_cells_parsed_as_nan():
    import pandas as pd
    d0 = dt.date(2026, 1, 1)
    dates = [d0, d0 + dt.timedelta(days=1), d0 + dt.timedelta(days=2)]
    pv = {d: 10.0 + i for i, d in enumerate(dates)}
    en = {d: 20.0 + i for i, d in enumerate(dates)}
    derived = {dates[0]: 5.0, dates[1]: None, dates[2]: 7.25}
    with tempfile.TemporaryDirectory() as td:
        out_path = pathlib.Path(td) / "out.csv"
        old_out = TPV.OUT
        TPV.OUT = out_path
        try:
            TPV.write_csv(dates, pv, en, derived)
        finally:
            TPV.OUT = old_out
        text = out_path.read_text()
        assert text.splitlines()[0] == ",pvoutput,enphase_meter,meter_derived", text.splitlines()[0]
        assert not text.endswith("\r\n") and "\r" not in text, "must use plain LF line endings"
        df = pd.read_csv(out_path)
        assert df["meter_derived"].isna().iloc[1], df
        assert not df["meter_derived"].isna().iloc[0]
        assert not df["meter_derived"].isna().iloc[2]
        assert df["meter_derived"].iloc[2] == 7.25, df
    return "write_csv emits a blank cell for a null day, which pandas parses back as NaN"


# ---------------------------------------------------------------------------
# (f) real-archive cases
# ---------------------------------------------------------------------------
@case
def case_regenerates_byte_identically_from_private_verify():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    before = ARTIFACT.read_bytes()
    cwd = os.getcwd()
    os.chdir(str(SANDBOX))
    try:
        TPV.main()
    finally:
        os.chdir(cwd)
    after = ARTIFACT.read_bytes()
    assert after == before, "data/threeway_production_validation.csv is not reproducible"
    return "data/threeway_production_validation.csv regenerates byte-identically from private/verify"


@case
def case_the_two_real_dst_dates_are_null_in_meter_derived_only():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    rows = {r[""]: r for r in _read_artifact_rows()}
    for iso in ("2025-11-02", "2026-03-08"):
        assert iso in rows, f"{iso} missing from the committed artifact entirely"
        r = rows[iso]
        assert r["meter_derived"] == "", (
            f"{iso} must be null in meter_derived (DST grid/wall-clock "
            f"mismatch), got {r['meter_derived']!r}")
        assert r["pvoutput"] not in ("", None), (
            f"{iso}: pvoutput is an independent instrument and must still "
            "carry a real value")
        assert r["enphase_meter"] not in ("", None), (
            f"{iso}: enphase_meter is an independent instrument and must "
            "still carry a real value")
    return "both real DST dates are null in meter_derived only; pvoutput/enphase_meter are populated"


@case
def case_all_365_rows_survive_the_dst_exclusion():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    rows = _read_artifact_rows()
    assert len(rows) == 365, (
        f"the file must keep all 365 calendar rows -- the DST exclusion nulls "
        f"a column, it does not shrink the file -- got {len(rows)}")
    n_null = sum(1 for r in rows if r["meter_derived"] == "")
    assert n_null == 2, f"expected exactly 2 null meter_derived rows (the DST dates), got {n_null}"
    return f"all 365 rows present, exactly {n_null} null in meter_derived"


@case
def case_correlation_and_mae_on_the_real_archive_used_only_363_days():
    _require_archive()
    if not ARTIFACT.exists():
        raise SkipCase(f"{ARTIFACT} not committed in this checkout")
    pv = TPV.load_pvoutput(TPV.PVOUTPUT_CSV)
    en = TPV.load_enphase(TPV.ENPHASE_CSV)
    dates = TPV.window_dates(pv, en)
    dst_days = TPV.dst_dates_in(dates)
    assert len(dst_days) == 2, dst_days

    derived = {}
    for row in _read_artifact_rows():
        d = dt.date.fromisoformat(row[""])
        derived[d] = None if row["meter_derived"] == "" else float(row["meter_derived"])

    stats_en = TPV.validation_stats(dates, dst_days, derived, en)
    stats_pv = TPV.validation_stats(dates, dst_days, derived, pv)
    assert stats_en["n_days"] == 363, stats_en
    assert stats_pv["n_days"] == 363, stats_pv
    # A loose sanity bound, not a pinned figure (an ad-hoc estimate this
    # generator formalizes is not expected to reproduce to the last digit,
    # CLAUDE.md section 0) -- the derived series should track the Enphase
    # production CT closely, since both describe the same physical quantity.
    assert stats_en["correlation"] > 0.99, stats_en
    assert stats_en["mae_kwh"] < 2.0, stats_en
    return (f"corr={stats_en['correlation']:.5f} MAE={stats_en['mae_kwh']:.3f} kWh/day "
           f"vs enphase_meter, over exactly {stats_en['n_days']} non-DST days")


def _read_artifact_rows():
    with open(ARTIFACT, newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
