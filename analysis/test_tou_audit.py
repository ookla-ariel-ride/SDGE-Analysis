#!/usr/bin/env python3
"""Negative and boundary tests for tou_audit.py.

Every case here is corpus-independent: they build synthetic intervals and synthetic
billed buckets, so the whole file runs in a clean checkout with no private data
present. That matters because the audit's value is in its edge handling (DST days,
period boundaries, the structural changeover, holiday assignment), and a suite that
silently skipped without the private export would let a broken edge pass CI.

Run from the repo root:  ./.venv/bin/python analysis/test_tou_audit.py
"""
import contextlib
import csv
import datetime as dt
import os
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


def _day(d, imp=1.0, exp=0.0):
    """One well-formed day: exactly the slots the calendar requires for that date."""
    return [(d, h, imp, exp) for h in T.expected_slots(d).elements()]


def case_dst_days_do_not_break_bucketing():
    """A 25-hour day carries 100 slots and a 23-hour day 92; both must bucket."""
    for d, n in ((dt.date(2025, 11, 2), 100), (dt.date(2026, 3, 8), 92)):
        rows = _day(d)
        assert len(rows) == n, (d, len(rows))
        got = T.rebuild(rows, d, d, "as_billed", HOL)
        assert round(sum(got.values()), 6) == float(n), (d, got)
    return "25-hour and 23-hour days conserve energy through the bucketing"


def case_dst_dates_are_the_us_transition_sundays():
    assert T.dst_dates(2026) == (dt.date(2026, 3, 8), dt.date(2026, 11, 1))
    assert T.dst_dates(2025) == (dt.date(2025, 3, 9), dt.date(2025, 11, 2))
    return "DST transitions are the 2nd Sunday in March and 1st Sunday in November"


def case_wellformed_days_have_no_defect():
    for d in (dt.date(2026, 4, 8), dt.date(2026, 3, 8), dt.date(2025, 11, 2)):
        slots = [h for _, h, _, _ in _day(d)]
        assert T.day_defect(d, slots) is None, (d, T.day_defect(d, slots))
    return "ordinary, spring-forward and fall-back days all validate as well formed"


def case_truncated_day_is_a_defect():
    d = dt.date(2026, 4, 8)
    why = T.day_defect(d, [h for _, h, _, _ in _day(d)][:4])
    assert why and "4 slots, expected 96" in why, why
    assert "missing" in why
    return "a day short of slots is reported as malformed, not waved through"


def case_duplicated_slots_are_a_defect():
    d = dt.date(2026, 4, 8)
    slots = [h for _, h, _, _ in _day(d)]
    why = T.day_defect(d, slots + slots[:3])
    assert why and "duplicated" in why, why
    return "duplicated 15-minute slots are reported as malformed"


def case_dst_slot_counts_are_not_accepted_on_ordinary_days():
    """92 or 100 slots are only legitimate on the actual transition Sundays."""
    ordinary = dt.date(2026, 4, 8)
    slots = [h for h in T.CANONICAL_SLOTS if h not in T.SPRING_FORWARD_GAP]
    assert T.day_defect(ordinary, slots) is not None
    spring, _ = T.dst_dates(2026)
    assert T.day_defect(spring, slots) is None
    return "a 92-slot day is a defect except on the spring-forward Sunday"


def case_malformed_day_disqualifies_its_period():
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in (_day(d) if d != days[10] else _day(d)[:4])]
    billed = _billed_from(rows, period, start, end)
    try:
        T.audit(rows, billed, [period], HOL)
    except SystemExit as e:
        assert "nothing to audit" in str(e), e
        return "a period containing a truncated day is skipped, not reconciled"
    raise AssertionError("expected the period with a truncated day to be skipped")


def case_trailing_placeholder_day_is_dropped_from_coverage():
    """The export's last day is all zeros; it must not extend coverage."""
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days[:-1] for x in _day(d)]
    rows += _day(days[-1], imp=0.0)          # trailing placeholder: 96 zero slots
    billed = _billed_from(rows, period, start, end)
    try:
        T.audit(rows, billed, [period], HOL)
    except SystemExit as e:
        assert "nothing to audit" in str(e), e
        return "the trailing all-zero placeholder day does not extend coverage"
    raise AssertionError("expected the placeholder day to be dropped from coverage")


def case_interior_zero_energy_day_disqualifies_its_period():
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d, imp=0.0 if d == days[9] else 1.0)]
    billed = _billed_from(rows, period, start, end)
    try:
        T.audit(rows, billed, [period], HOL)
    except SystemExit as e:
        assert "nothing to audit" in str(e), e
        return "a zero-energy day inside a period disqualifies it, not just the last day"
    raise AssertionError("expected the interior zero-energy day to skip the period")


def _two_periods(corrupt_second):
    """One clean period and one that is well formed or corrupt, plus their bills."""
    periods = ["4/1/26 - 4/30/26", "5/1/26 - 5/31/26"]
    rows, billed = [], {}
    for period in periods:
        start, end = T.parse_period(period)
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        bad = corrupt_second and period == periods[1]
        chunk = [x for d in days
                 for x in (_day(d)[:4] if (bad and d == days[10]) else _day(d))]
        rows += chunk
        billed.update(_billed_from(chunk, period, start, end))
    return periods, rows, billed


def case_integrity_skip_blocks_a_clean_verdict():
    """One corrupt period must not be dropped while the rest report success.

    This is the multi-period path: with every period skipped the run already fails
    closed, but a corrupt period alongside a healthy one used to vanish from the
    result and leave a passing verdict behind.
    """
    periods, rows, billed = _two_periods(corrupt_second=True)
    out, audited, skipped, totals, _, _, _ = T.audit(rows, billed, periods, HOL)
    assert audited == [periods[0]], audited
    integrity = [s for s in skipped if s["reason"] != T.SKIP_OUT_OF_COVERAGE]
    assert len(integrity) == 1 and integrity[0]["period"] == periods[1], skipped
    s = T.summarise(out, totals, "as_billed")
    assert s["buckets_failing"] == 0 and s["period_totals_failing"] == 0, (
        "fixture must leave the surviving period reconciling, so the only thing "
        "that can taint the verdict is the excluded period")
    return "a corrupt period is reported as an integrity skip, not silently dropped"


def case_undelivered_declared_day_is_data_loss():
    """A day the export promised and did not supply must taint the verdict."""
    periods, rows, billed = _two_periods(corrupt_second=False)
    rows = [r for r in rows if r[0] != dt.date(2026, 5, 31)]   # drop a promised day
    declared = (dt.date(2026, 4, 1), dt.date(2026, 5, 31))
    *_, undelivered = T.audit(rows, billed, periods, HOL, declared)
    assert undelivered == ["2026-05-31"], undelivered
    return "a day inside the declared range but absent from the file is data loss"


def case_retention_boundary_is_not_data_loss():
    """Retention cuts mid-period; that is normal and must not read as degraded.

    The export declares what it contains, so a statement running past either end
    of that range is beyond what the file was ever going to hold. Inferring loss
    from row extents alone would flag a healthy export every month, because the
    retention date has nothing to do with billing-cycle boundaries.
    """
    periods, rows, billed = _two_periods(corrupt_second=False)
    cut = dt.date(2026, 5, 10)                  # the file stops mid-way through period two
    rows = [r for r in rows if r[0] <= cut]
    declared = (dt.date(2026, 4, 1), cut)
    out, audited, skipped, totals, _, _, undelivered = T.audit(
        rows, billed, periods, HOL, declared)
    assert not undelivered, undelivered
    assert audited == [periods[0]], audited
    assert [s["reason"] for s in skipped] == [T.SKIP_OUT_OF_COVERAGE], skipped
    integrity = [s for s in skipped if s["reason"] != T.SKIP_OUT_OF_COVERAGE]
    assert not integrity, "a retention cut must not be reported as degraded data"
    return "a retention cut landing mid-period is an expected coverage limit"


def case_declared_range_is_read_from_the_export_header():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "usage.csv"
        # A bare "Meter Number,<id>" line precedes Reading Start in real exports;
        # only the DATA header row (Meter Number,Date,...) ends the metadata block.
        p.write_text("Name,SOMEONE\nMeter Number,05175731\n"
                     "Interval UOM,Minute(s)\nReading Start,6/27/2025 00:00\n"
                     "Reading End,7/26/2026 23:45\nUOM,kWh\n"
                     "Meter Number,Date,Start Time,Duration,Consumption,Generation,Net\n")
        assert T.read_declared_range(p) == (dt.date(2025, 6, 27), dt.date(2026, 7, 26))
        q = pathlib.Path(td) / "noheader.csv"
        q.write_text("Name,SOMEONE\nUOM,kWh\n")
        assert T.read_declared_range(q) is None
    return "the declared range is read from the header, and its absence is handled"


def case_out_of_coverage_skip_is_not_an_integrity_failure():
    """Statements the export simply does not reach are an expected coverage fact."""
    periods, rows, billed = _two_periods(corrupt_second=False)
    rows = [r for r in rows if r[0] < dt.date(2026, 5, 1)]      # drop period two
    out, audited, skipped, totals, _, _, _ = T.audit(rows, billed, periods, HOL)
    assert audited == [periods[0]], audited
    assert [s["reason"] for s in skipped] == [T.SKIP_OUT_OF_COVERAGE], skipped
    integrity = [s for s in skipped if s["reason"] != T.SKIP_OUT_OF_COVERAGE]
    assert not integrity, integrity
    return "an out-of-coverage period is an expected skip and does not taint the verdict"


def case_missing_billed_bucket_stops_the_run():
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d)]
    billed = _billed_from(rows, period, start, end)
    dropped = billed.pop((period, "winter", "off_peak"))
    assert abs(dropped) > 100, "fixture must drop a bucket holding real energy"
    try:
        T.audit(rows, billed, [period], HOL)
    except SystemExit as e:
        assert "expected buckets" in str(e) and "off_peak" in str(e), e
        return "an omitted billed bucket stops the run instead of reconciling"
    raise AssertionError("expected SystemExit when a billed bucket is missing")


def case_unexpected_billed_bucket_stops_the_run():
    """A summer bucket on an all-winter period means the season boundary moved."""
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d)]
    billed = _billed_from(rows, period, start, end)
    billed[(period, "summer", "on_peak")] = 10.0
    try:
        T.audit(rows, billed, [period], HOL)
    except SystemExit as e:
        assert "unexpected" in str(e), e
        return "a bucket outside the period's seasons stops the run"
    raise AssertionError("expected SystemExit on an unexpected billed bucket")


def case_holiday_evidence_is_leave_one_out():
    """A holiday is confirmed only when dropping it degrades its period's fit."""
    period = "8/27/25 - 9/25/25"          # contains Labor Day 2025-09-01
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    # Load only during 06:00-14:00, the hours the weekend rule reassigns, so the
    # holiday's treatment is the only thing that can move energy between buckets.
    rows = [(d, h, 1.0, 0.0) for d in days
            for h in T.expected_slots(d).elements() if 6 <= h < 14]
    billed = _billed_from(rows, period, start, end)
    ev = T.holiday_evidence(rows, billed, HOL, [period])
    labor = [e for e in ev if e["date"] == "2025-09-01"][0]
    assert labor["inside_an_audited_period"] and labor["confirmed"], labor
    assert labor["residual_with_holiday_rule_kwh"] < labor["residual_without_kwh"], labor
    outside = [e for e in ev if not e["inside_an_audited_period"]]
    assert outside and all(e["confirmed"] is None for e in outside), outside
    return "holiday evidence confirms by leave-one-out and reports untested dates as such"


def _shift_fixture(period, window_imp=None):
    """Period intervals: 1.0 kWh per slot, with 06:00-10:00 overridden per day.

    The 06:00-10:00 window is the only place a summer weekday and weekend differ
    under the current tariff, so the per-day override controls exactly how much
    energy each shift candidate can move.
    """
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = []
    for d in days:
        w = (window_imp or {}).get(d)
        for h in T.expected_slots(d).elements():
            rows.append((d, h, w if (w is not None and 6 <= h < 10) else 1.0, 0.0))
    return rows


def _billed_under(rows, period, hol):
    start, end = T.parse_period(period)
    return {(period, T.SEASON_NAME[s], T.TOU_NAME[t]): round(v)
            for (s, t), v in T.rebuild(rows, start, end, "as_billed", hol).items()}


def case_weekend_shift_scoring_detects_a_friday_shift():
    """Bills carrying a Friday observed holiday must be identified as such --
    and flagged as contradicting the documented Saturday-no-shift rule."""
    period = "6/27/26 - 7/26/26"          # contains Sat July 4 2026
    fri, mon = dt.date(2026, 7, 3), dt.date(2026, 7, 6)
    rows = _shift_fixture(period, {fri: 2.0, mon: 0.5})
    billed = _billed_under(rows, period, HOL | {fri})
    ev = T.weekend_shift_evidence(rows, billed, HOL, [period])
    e = [x for x in ev if x["date"] == "2026-07-04"][0]
    assert e["inside_an_audited_period"], e
    assert e["outcome"] == "determined:friday", e
    assert e["documented_rule"] == "none", e
    assert e["consistent_with_documented"] is False, e
    assert e["residual_kwh"]["friday"] < e["residual_kwh"]["monday"], e
    assert e["residual_kwh"]["friday"] < e["residual_kwh"]["none"], e
    for m in e["margins"].values():
        assert m["margin_kwh"] > m["identifiability_bound_kwh"], e
    return "a decisive Friday shift is determined and flagged against the documented rule"


def case_weekend_shift_scoring_confirms_no_shift():
    period = "6/27/26 - 7/26/26"
    fri, mon = dt.date(2026, 7, 3), dt.date(2026, 7, 6)
    rows = _shift_fixture(period, {fri: 2.0, mon: 0.5})
    billed = _billed_under(rows, period, HOL)          # bills follow no-shift
    ev = T.weekend_shift_evidence(rows, billed, HOL, [period])
    e = [x for x in ev if x["date"] == "2026-07-04"][0]
    assert e["outcome"] == "determined:none", e
    assert e["consistent_with_documented"] is True, e
    return "decisive no-shift evidence confirms the documented Saturday rule"


def case_weekend_shift_scoring_is_indeterminate_below_rounding():
    """Sub-rounding energy in the discriminating window must never pick a winner.

    The candidates differ only by the 06:00-10:00 kWh on the candidate days; when
    that is smaller than the joint bucket-rounding bound, ranking residuals is
    noise and the scorer must say so rather than crown the smallest number.
    """
    period = "6/27/26 - 7/26/26"
    fri, mon = dt.date(2026, 7, 3), dt.date(2026, 7, 6)
    rows = _shift_fixture(period, {fri: 0.02, mon: 0.01})
    billed = _billed_under(rows, period, HOL | {fri})   # even when a shift IS present
    ev = T.weekend_shift_evidence(rows, billed, HOL, [period])
    e = [x for x in ev if x["date"] == "2026-07-04"][0]
    assert e["outcome"] == "indeterminate", e
    assert e["consistent_with_documented"] is None, e
    return "a shift below the identifiability bound reports indeterminate, not a winner"


def case_weekend_shift_scoring_determines_a_sunday_monday_shift():
    """The first real Sunday case is July 4 2027; the scorer must handle it."""
    hol27 = T.holidays([2027])
    period = "6/27/27 - 7/26/27"          # contains Sun July 4 2027
    fri, mon = dt.date(2027, 7, 2), dt.date(2027, 7, 5)
    rows = _shift_fixture(period, {mon: 2.0, fri: 0.5})
    billed = _billed_under(rows, period, hol27 | {mon})
    ev = T.weekend_shift_evidence(rows, billed, hol27, [period])
    e = [x for x in ev if x["date"] == "2027-07-04"][0]
    assert e["outcome"] == "determined:monday", e
    assert e["documented_rule"] == "monday", e
    assert e["consistent_with_documented"] is True, e
    return "a decisive Sunday-to-Monday shift is determined and matches the documented rule"


def case_weekend_shift_scoring_reports_untested_dates():
    """A weekend holiday outside every audited period must appear as untested,
    so August intake cannot silently skip the check."""
    period = "4/1/26 - 4/30/26"           # contains no weekend holiday
    rows = _shift_fixture(period)
    billed = _billed_under(rows, period, HOL)
    ev = T.weekend_shift_evidence(rows, billed, HOL, [period])
    dates = {e["date"] for e in ev}
    assert "2026-07-04" in dates, dates
    e = [x for x in ev if x["date"] == "2026-07-04"][0]
    assert e["inside_an_audited_period"] is False, e
    assert e["outcome"] == "untested" and e["consistent_with_documented"] is None, e
    weekend_hols = {str(d) for d in HOL if d.weekday() >= 5}
    assert dates == weekend_hols, (dates, weekend_hols)
    return "weekend holidays outside audited periods are reported as untested, never dropped"


def case_period_total_catches_accumulated_bias():
    """Every bucket inside the 1 kWh floor, yet the period total does not reconcile."""
    period = "5/20/26 - 6/20/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d, imp=0.26)]
    built = T.rebuild(rows, start, end, "as_billed", HOL)
    billed = {(period, T.SEASON_NAME[s], T.TOU_NAME[t]): round(round(v, 1) - 0.9, 1)
              for (s, t), v in built.items()}
    out, _, _, totals, _, _, _ = T.audit(rows, billed, [period], HOL)
    s = T.summarise(out, totals, "as_billed")
    assert s["buckets"] == 6, s["buckets"]
    assert s["buckets_failing"] == 0, "fixture must keep every bucket inside the floor"
    assert s["period_totals_failing"] == 1, s
    assert s["worst_period_total_pct"] > T.REL_TOL_TOTAL * 100
    return "a period whose buckets each pass but whose total does not is caught"


def case_period_total_tolerates_pure_rounding():
    """Whole-kWh rounding on every bucket must not fail the period total."""
    period = "5/20/26 - 6/20/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d, imp=0.26)]
    built = T.rebuild(rows, start, end, "as_billed", HOL)
    billed = {(period, T.SEASON_NAME[s], T.TOU_NAME[t]): round(v)
              for (s, t), v in built.items()}
    out, _, _, totals, _, _, _ = T.audit(rows, billed, [period], HOL)
    s = T.summarise(out, totals, "as_billed")
    assert s["buckets_failing"] == 0 and s["period_totals_failing"] == 0, s
    return "rounding-only residuals pass both the bucket and the period-total check"


def case_rebuild_respects_period_bounds():
    days = [dt.date(2026, 4, 6) + dt.timedelta(days=i) for i in range(5)]
    rows = _intervals(days)
    inner = T.rebuild(rows, days[1], days[3], "as_billed", HOL)
    assert round(sum(inner.values()), 6) == 3 * 96.0, sum(inner.values())
    return "rebuild includes both endpoints and excludes days outside the period"


def _billed_from(intervals, period, start, end):
    """Billed buckets that agree exactly with the rebuild, as a clean fixture base."""
    return {(period, T.SEASON_NAME[s], T.TOU_NAME[t]): round(v)
            for (s, t), v in T.rebuild(intervals, start, end, "as_billed", HOL).items()}


def _billed_csv(tmp, buckets, gen_override=None, skip_generation=(),
                periods_override=None):
    """Write a detail file plus the statement file the audit cross-checks it against.

    periods_override lets a case describe statements the detail file does not
    cover, which is how a wholly missing period is simulated.
    """
    p = tmp / "bill_tou_detail.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["statement_date", "period", "section", "season", "segment",
                    "segment_days", "tou_period", "kwh", "rate_per_kwh"])
        for (period, season, tou), kwh in buckets.items():
            w.writerow(["2026-05-04", period, "delivery", season, 0, 30, tou, kwh, 0.3])
            if (period, season, tou) in skip_generation:
                continue
            g = gen_override if gen_override is not None else kwh
            w.writerow(["2026-05-04", period, "generation", season, 0, 30, tou, g, 0.2])

    nets = {}
    for (period, _, _), kwh in buckets.items():
        nets[period] = nets.get(period, 0.0) + kwh
    if periods_override is not None:
        nets = dict(periods_override)
    q = tmp / "bill_periods_electric.csv"
    with open(q, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["statement_date", "period", "days", "generation_provider",
                    "net_kwh", "gross_kwh", "sdge_delivery", "cca_generation",
                    "current_charges", "base_services_charge"])
        for period, net in nets.items():
            w.writerow(["2026-05-04", period, 30, "CCA", net, net, 0, 0, 0, 0])
    return p, q


def case_missing_period_stops_the_run():
    """A statement with no TOU rows must not vanish from the audit.

    Everything downstream is driven by the periods discovered in the detail file,
    so a statement whose rows are gone is never audited and never reported as
    skipped: the rest reconciles and the run declares success over a corpus with a
    statement missing from it. The statement file is the independent witness.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        present = "4/1/26 - 4/30/26"
        buckets = {(present, "winter", t): 100.0 for t in
                   ("on_peak", "off_peak", "super_off_peak")}
        p, q = _billed_csv(tmp, buckets,
                           periods_override={present: 300.0,
                                             "5/1/26 - 5/31/26": 900.0})
        try:
            T.load_billed(p, q)
        except SystemExit as e:
            assert "different billing periods" in str(e), e
            assert "5/1/26 - 5/31/26" in str(e), e
            return "a statement with no TOU rows stops the run instead of vanishing"
    raise AssertionError("expected SystemExit when a whole period has no TOU rows")


def case_bucket_sums_must_match_the_statement_net():
    """Partial loss inside a period survives a set comparison; net_kwh catches it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        period = "4/1/26 - 4/30/26"
        buckets = {(period, "winter", t): 100.0 for t in
                   ("on_peak", "off_peak", "super_off_peak")}
        p, q = _billed_csv(tmp, buckets, periods_override={period: 800.0})
        try:
            T.load_billed(p, q)
        except SystemExit as e:
            assert "net_kwh" in str(e), e
            return "buckets that do not sum to the statement's net_kwh stop the run"
    raise AssertionError("expected SystemExit when buckets disagree with net_kwh")


def case_load_billed_rejects_asymmetric_sections():
    """Key SETS must match, not merely overlap: a both-sections omission must show."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        key = ("4/1/26 - 4/30/26", "winter", "off_peak")
        p, q = _billed_csv(tmp, {("4/1/26 - 4/30/26", "winter", "on_peak"): 100.0,
                                 key: 50.0}, skip_generation=(key,))
        try:
            T.load_billed(p, q)
        except SystemExit as e:
            assert "different buckets" in str(e), e
            return "load_billed fails closed when a bucket exists in only one section"
    raise AssertionError("expected SystemExit on asymmetric delivery/generation keys")


def case_load_billed_rejects_inconsistent_parse():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        p, q = _billed_csv(tmp, {("4/1/26 - 4/30/26", "winter", "on_peak"): 100.0},
                           gen_override=99.0)
        try:
            T.load_billed(p, q)
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


# ---------------------------------------------------------------------------
# The published columns are a schema, not a sample of row 0 (issue #215)
# ---------------------------------------------------------------------------
def _synthetic_corpus():
    """(intervals, billed, periods) for one well-formed April 2026 statement."""
    period = "4/1/26 - 4/30/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d)]
    return rows, _billed_from(rows, period, start, end), [period]


def _one_real_row():
    """One bucket row exactly as audit() builds it, for the positive controls."""
    rows, billed, periods = _synthetic_corpus()
    built, audited, _, _, _, _, _ = T.audit(rows, billed, periods, HOL)
    assert audited == periods and built, (audited, len(built))
    return built[0]


def case_main_refuses_an_empty_audit_before_it_writes():
    """The refusal has to be WIRED, not merely defined.

    main() is driven for real over a synthetic corpus, with only its inputs and
    its writer stubbed. The negative run makes audit() return no buckets and
    requires main() to exit before write_artifacts is reached; the positive
    control is the same main() over the same corpus with the real audit(), which
    must run through to the write.
    """
    real_audit = T.audit
    real_writer = T.write_artifacts
    intervals, billed, periods = _synthetic_corpus()
    wrote = []
    day = dt.date(2026, 4, 1)
    saved = {n: getattr(T, n) for n in
             ("load_intervals", "load_billed", "read_declared_range")}
    try:
        T.load_intervals = lambda p: intervals
        T.load_billed = lambda a, b: (billed, periods)
        T.read_declared_range = lambda p: None
        T.write_artifacts = lambda *a, **k: wrote.append(a)
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            (pathlib.Path(td) / "usage.csv").write_text("")
            os.chdir(td)
            try:
                T.audit = lambda *a, **k: ([], periods, [], [], day, day, [])
                try:
                    T.main()
                except SystemExit as e:
                    msg = str(e)
                else:
                    raise AssertionError("main() published an empty audit result")
                assert "no TOU buckets were computed" in msg, msg
                assert not wrote, "main() wrote artifacts despite an empty result"
                # POSITIVE CONTROL: same main(), same corpus, the real audit().
                T.audit = real_audit
                with open(os.devnull, "w") as quiet:
                    with contextlib.redirect_stdout(quiet):
                        T.main()
                assert len(wrote) == 1, wrote
                assert wrote[0][0] and list(wrote[0][0][0]) == list(T.ROW_FIELDS), \
                    "the positive control did not reach the write with real rows"
            finally:
                os.chdir(cwd)
    finally:
        T.audit = real_audit
        T.write_artifacts = real_writer
        for name, fn in saved.items():
            setattr(T, name, fn)
    return "main() refuses an empty result before writing, and writes a real one"


def case_row_schema_is_the_committed_header():
    """ROW_FIELDS is the artifact's header AND the keys audit() actually emits.

    Both halves matter: naming the columns independently of the rows is only safe
    while the two agree, so the schema is checked against the committed file and
    against a row from a real audit run in the same case.
    """
    assert list(_one_real_row()) == list(T.ROW_FIELDS), list(_one_real_row())
    committed = T.DATA / "tou_audit.csv"
    if committed.exists():
        with open(committed, newline="") as fh:
            header = next(csv.reader(fh))
        assert header == list(T.ROW_FIELDS), header
    return "ROW_FIELDS matches both the committed header and a real audit row"


def case_empty_result_refuses_before_opening_anything():
    """rows[0]-as-schema raised IndexError with the handle already open (#215).

    The refusal has to arrive BEFORE any write, and it has to say what the run
    did. Proven by pointing the writer at a directory that already holds a
    populated artifact and checking those bytes survive the refusal.
    """
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td)
        prior = "period,season\nkept,bytes\n"
        (dest / "tou_audit.csv").write_text(prior)
        try:
            T.check_publishable([], ["4/1/26 - 4/30/26"], [{"period": "x"}],
                                dt.date(2026, 4, 1), dt.date(2026, 4, 30))
        except SystemExit as e:
            msg = str(e)
        else:
            raise AssertionError("an empty bucket result was accepted for publication")
        assert "1 period(s) audited" in msg and "1 skipped" in msg, msg
        assert "2026-04-01 .. 2026-04-30" in msg, msg
        assert "left as they were" in msg, msg
        assert (dest / "tou_audit.csv").read_text() == prior, "artifact was touched"
        # POSITIVE CONTROL: the same instrument passes a well-formed result, so
        # the refusal above is about the empty rows and not about the call itself.
        T.check_publishable([_one_real_row()], ["4/1/26 - 4/30/26"], [],
                            dt.date(2026, 4, 1), dt.date(2026, 4, 30))
    return "an empty bucket result is a stated refusal, not an IndexError mid-write"


def case_header_is_written_without_any_rows_to_read_it_off():
    """The writer names its columns from ROW_FIELDS, so no row is needed for them."""
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td)
        T.write_artifacts([], {"verdict": "none"}, dest=dest)
        empty = (dest / "tou_audit.csv").read_text()
        assert empty == ",".join(T.ROW_FIELDS) + "\n", empty
        # POSITIVE CONTROL: same writer, a real row, header plus one data line.
        row = _one_real_row()
        T.write_artifacts([row], {"verdict": "none"}, dest=dest)
        lines = (dest / "tou_audit.csv").read_text().splitlines()
        assert lines[0] == ",".join(T.ROW_FIELDS), lines[0]
        assert len(lines) == 2 and lines[1].startswith("4/1/26 - 4/30/26,"), lines
    return "the CSV header comes from the schema, with or without a row to sample"


def case_row_that_drifts_from_the_schema_stops_the_run():
    """A renamed or extra key would otherwise be dropped or blanked silently."""
    good = _one_real_row()
    drifted = {("as_billed_kWh" if k == "as_billed_kwh" else k): v
               for k, v in good.items()}
    try:
        T.check_publishable([good, drifted], ["4/1/26 - 4/30/26"], [],
                            dt.date(2026, 4, 1), dt.date(2026, 4, 30))
    except SystemExit as e:
        assert "1 of 2 bucket row(s)" in str(e), e
        assert "as_billed_kWh" in str(e), e
    else:
        raise AssertionError("a row that drifted from ROW_FIELDS was accepted")
    # POSITIVE CONTROL: the undrifted row alone passes the same check.
    T.check_publishable([good], ["4/1/26 - 4/30/26"], [],
                        dt.date(2026, 4, 1), dt.date(2026, 4, 30))
    return "a bucket row that drifts from the published schema stops the run"


CASES = [
    # TOU assignment
    case_on_peak_window_edges,
    case_midday_sop_starts_at_changeover,
    case_canonical_rule_ignores_the_changeover,
    case_holiday_takes_weekend_windows,
    case_weekend_super_off_peak_ends_at_14,
    case_holiday_set_matches_the_documented_tariff_list,
    # periods and tolerances
    case_period_parse_is_inclusive,
    case_tolerance_floor_protects_small_buckets,
    case_rebuild_respects_period_bounds,
    # interval-day integrity
    case_dst_days_do_not_break_bucketing,
    case_dst_dates_are_the_us_transition_sundays,
    case_wellformed_days_have_no_defect,
    case_truncated_day_is_a_defect,
    case_duplicated_slots_are_a_defect,
    case_dst_slot_counts_are_not_accepted_on_ordinary_days,
    case_malformed_day_disqualifies_its_period,
    case_trailing_placeholder_day_is_dropped_from_coverage,
    case_interior_zero_energy_day_disqualifies_its_period,
    # billed-bucket completeness
    case_load_billed_rejects_inconsistent_parse,
    case_load_billed_rejects_asymmetric_sections,
    case_missing_period_stops_the_run,
    case_bucket_sums_must_match_the_statement_net,
    case_integrity_skip_blocks_a_clean_verdict,
    case_undelivered_declared_day_is_data_loss,
    case_retention_boundary_is_not_data_loss,
    case_declared_range_is_read_from_the_export_header,
    case_out_of_coverage_skip_is_not_an_integrity_failure,
    case_missing_billed_bucket_stops_the_run,
    case_unexpected_billed_bucket_stops_the_run,
    # coverage and totals
    case_load_intervals_rejects_a_file_with_no_data,
    case_partial_period_is_skipped_not_credited,
    case_interval_gap_inside_period_is_skipped,
    case_holiday_evidence_is_leave_one_out,
    case_weekend_shift_scoring_detects_a_friday_shift,
    case_weekend_shift_scoring_confirms_no_shift,
    case_weekend_shift_scoring_is_indeterminate_below_rounding,
    case_weekend_shift_scoring_determines_a_sunday_monday_shift,
    case_weekend_shift_scoring_reports_untested_dates,
    case_period_total_catches_accumulated_bias,
    case_period_total_tolerates_pure_rounding,
    # published-column schema
    case_row_schema_is_the_committed_header,
    case_empty_result_refuses_before_opening_anything,
    case_header_is_written_without_any_rows_to_read_it_off,
    case_row_that_drifts_from_the_schema_stops_the_run,
    case_main_refuses_an_empty_audit_before_it_writes,
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
