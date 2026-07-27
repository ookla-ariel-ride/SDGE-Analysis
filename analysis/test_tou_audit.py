#!/usr/bin/env python3
"""Negative and boundary tests for tou_audit.py.

Every case here is corpus-independent: they build synthetic intervals and synthetic
billed buckets, so the whole file runs in a clean checkout with no private data
present. That matters because the audit's value is in its edge handling (DST days,
period boundaries, the structural changeover, holiday assignment), and a suite that
silently skipped without the private export would let a broken edge pass CI.

Run from the repo root:  ./.venv/bin/python analysis/test_tou_audit.py
"""
import csv
import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tou_audit as T

HOL = T.holidays([2025, 2026])
WEEKDAY_PRE = dt.date(2026, 2, 4)     # a Wednesday before the changeover
WEEKDAY_POST = dt.date(2026, 4, 8)    # a Wednesday after it
SATURDAY = dt.date(2026, 4, 11)
LABOR_DAY = dt.date(2025, 9, 1)


def case_on_peak_window_edges():
    for d in (WEEKDAY_PRE, WEEKDAY_POST, SATURDAY):
        assert T.assign(d, 15.75, "as_billed", HOL) != "on", d
        assert T.assign(d, 16.0, "as_billed", HOL) == "on", d
        assert T.assign(d, 20.75, "as_billed", HOL) == "on", d
        assert T.assign(d, 21.0, "as_billed", HOL) != "on", d
    return "on-peak is 16:00-21:00 inclusive of 20:45, on every day type"


def case_midday_sop_starts_at_changeover():
    assert T.assign(WEEKDAY_PRE, 11.0, "as_billed", HOL) == "off"
    assert T.assign(WEEKDAY_POST, 11.0, "as_billed", HOL) == "sop"
    day_before = T.MIDDAY_SOP_START - dt.timedelta(days=1)
    if day_before.weekday() < 5:
        assert T.assign(day_before, 11.0, "as_billed", HOL) == "off"
    assert T.assign(T.MIDDAY_SOP_START, 11.0, "as_billed", HOL) == "sop"
    return "weekday 10:00-14:00 is off-peak before the changeover, super-off-peak after"


def case_canonical_rule_ignores_the_changeover():
    """The canonical rule is the current tariff applied year-round, by design."""
    assert T.assign(WEEKDAY_PRE, 11.0, "canonical", HOL) == "sop"
    assert T.assign(WEEKDAY_POST, 11.0, "canonical", HOL) == "sop"
    return "canonical rule applies the current midday window to every date"


def case_holiday_takes_weekend_windows():
    assert LABOR_DAY.weekday() < 5, "fixture must be a weekday holiday"
    assert T.assign(LABOR_DAY, 11.0, "as_billed", HOL) == "sop"
    assert T.assign(LABOR_DAY, 11.0, "as_billed", set()) == "off"
    assert T.assign(LABOR_DAY, 11.0, "canonical", HOL) == "sop"
    plain = dt.date(2025, 9, 3)
    assert T.assign(plain, 8.0, "as_billed", HOL) == "off"
    assert T.assign(LABOR_DAY, 8.0, "as_billed", HOL) == "sop"
    return "a weekday holiday is assigned weekend windows under as_billed"


def case_weekend_super_off_peak_ends_at_14():
    assert T.assign(SATURDAY, 13.75, "as_billed", HOL) == "sop"
    assert T.assign(SATURDAY, 14.0, "as_billed", HOL) == "off"
    assert T.assign(SATURDAY, 5.0, "as_billed", HOL) == "sop"
    return "weekend super-off-peak runs midnight to 14:00"


def case_period_parse_is_inclusive():
    s, e = T.parse_period("7/29/25 - 8/26/25")
    assert (s, e) == (dt.date(2025, 7, 29), dt.date(2025, 8, 26))
    assert (e - s).days + 1 == 29, "statement prints 29 days for this period"
    return "period endpoints parse inclusively and match the statement day count"


def case_tolerance_floor_protects_small_buckets():
    assert T.cell_pass(21.0, 21.4), "0.4 kWh on a 21 kWh bucket is statement rounding"
    assert not T.cell_pass(21.0, 23.0), "2 kWh on a 21 kWh bucket is a real miss"
    assert T.cell_pass(1129.0, 1133.0), "0.35% on a large bucket is inside 0.5%"
    assert not T.cell_pass(1129.0, 1200.0)
    return "bucket tolerance is max(1 kWh, 0.5%), so rounding never fails a small bucket"


def _intervals(days, per_day=96, imp=1.0, exp=0.0):
    out = []
    for d in days:
        for i in range(per_day):
            out.append((d, (i % 96) * 0.25, imp, exp))
    return out


def case_dst_days_do_not_break_bucketing():
    """A 25-hour day carries 100 intervals and a 23-hour day 92; both must bucket."""
    long_day = [(dt.date(2025, 11, 2), h * 0.25, 1.0, 0.0) for h in range(100)]
    short_day = [(dt.date(2026, 3, 8), h * 0.25, 1.0, 0.0) for h in range(92)]
    for rows, d in ((long_day, dt.date(2025, 11, 2)), (short_day, dt.date(2026, 3, 8))):
        got = T.rebuild(rows, d, d, "as_billed", HOL)
        assert round(sum(got.values()), 6) == float(len(rows)), (d, got)
    return "25-hour and 23-hour days conserve energy through the bucketing"


def case_rebuild_respects_period_bounds():
    days = [dt.date(2026, 4, 6) + dt.timedelta(days=i) for i in range(5)]
    rows = _intervals(days)
    inner = T.rebuild(rows, days[1], days[3], "as_billed", HOL)
    assert round(sum(inner.values()), 6) == 3 * 96.0, sum(inner.values())
    return "rebuild includes both endpoints and excludes days outside the period"


def _billed_csv(tmp, buckets, gen_override=None):
    p = tmp / "bill_tou_detail.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["statement_date", "period", "section", "season", "segment",
                    "segment_days", "tou_period", "kwh", "rate_per_kwh"])
        for (period, season, tou), kwh in buckets.items():
            w.writerow(["2026-05-04", period, "delivery", season, 0, 30, tou, kwh, 0.3])
            g = gen_override if gen_override is not None else kwh
            w.writerow(["2026-05-04", period, "generation", season, 0, 30, tou, g, 0.2])
    return p


def case_load_billed_rejects_inconsistent_parse():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        p = _billed_csv(tmp, {("4/1/26 - 4/30/26", "winter", "on_peak"): 100.0},
                        gen_override=99.0)
        try:
            T.load_billed(p)
        except SystemExit as e:
            assert "disagree" in str(e), e
            return "load_billed fails closed when delivery and generation kWh differ"
    raise AssertionError("expected SystemExit on inconsistent delivery/generation kWh")


def case_load_intervals_rejects_a_file_with_no_data():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "usage.csv"
        p.write_text("Name,SOMEONE\nAddress,SOMEWHERE\nUOM,kWh\n")
        try:
            T.load_intervals(p)
        except SystemExit as e:
            assert "no interval rows" in str(e), e
            return "load_intervals fails closed on a file with no interval rows"
    raise AssertionError("expected SystemExit on an export with no interval rows")


def case_partial_period_is_skipped_not_credited():
    """A period only partly covered by the export must never be scored."""
    days = [dt.date(2026, 4, 10) + dt.timedelta(days=i) for i in range(10)]
    rows = _intervals(days)
    billed = {("4/1/26 - 4/30/26", "winter", "super_off_peak"): 100.0}
    try:
        T.audit(rows, billed, ["4/1/26 - 4/30/26"], HOL)
    except SystemExit as e:
        assert "nothing to audit" in str(e), e
        return "a partially covered period is skipped, and an empty audit fails closed"
    raise AssertionError("expected the partially covered period to be skipped")


def case_interval_gap_inside_period_is_skipped():
    days = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(30)]
    del days[10]                                   # punch a hole mid-period
    rows = _intervals(days)
    billed = {("4/1/26 - 4/30/26", "winter", "super_off_peak"): 100.0}
    try:
        T.audit(rows, billed, ["4/1/26 - 4/30/26"], HOL)
    except SystemExit as e:
        assert "nothing to audit" in str(e), e
        return "a period containing a missing day is skipped rather than under-counted"
    raise AssertionError("expected the gapped period to be skipped")


def case_holiday_set_matches_the_documented_tariff_list():
    got = {d for d in T.holidays([2026])}
    assert dt.date(2026, 1, 1) in got and dt.date(2026, 7, 4) in got
    assert dt.date(2026, 11, 11) in got and dt.date(2026, 12, 25) in got
    assert dt.date(2026, 2, 16) in got, "Presidents: 3rd Monday of February 2026"
    assert dt.date(2026, 5, 25) in got, "Memorial: last Monday of May 2026"
    assert dt.date(2026, 9, 7) in got, "Labor: 1st Monday of September 2026"
    assert dt.date(2026, 11, 26) in got, "Thanksgiving: 4th Thursday of November 2026"
    assert len(got) == 8, sorted(got)
    return "the holiday set is the eight tariff holidays in research/rates-reference.md"


CASES = [
    case_on_peak_window_edges,
    case_midday_sop_starts_at_changeover,
    case_canonical_rule_ignores_the_changeover,
    case_holiday_takes_weekend_windows,
    case_weekend_super_off_peak_ends_at_14,
    case_period_parse_is_inclusive,
    case_tolerance_floor_protects_small_buckets,
    case_dst_days_do_not_break_bucketing,
    case_rebuild_respects_period_bounds,
    case_load_billed_rejects_inconsistent_parse,
    case_load_intervals_rejects_a_file_with_no_data,
    case_partial_period_is_skipped_not_credited,
    case_interval_gap_inside_period_is_skipped,
    case_holiday_set_matches_the_documented_tariff_list,
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
