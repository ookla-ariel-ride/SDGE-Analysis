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


def case_period_total_catches_accumulated_bias():
    """Every bucket inside the 1 kWh floor, yet the period total does not reconcile."""
    period = "5/20/26 - 6/20/26"
    start, end = T.parse_period(period)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [x for d in days for x in _day(d, imp=0.26)]
    built = T.rebuild(rows, start, end, "as_billed", HOL)
    billed = {(period, T.SEASON_NAME[s], T.TOU_NAME[t]): round(round(v, 1) - 0.9, 1)
              for (s, t), v in built.items()}
    out, _, _, totals, _, _ = T.audit(rows, billed, [period], HOL)
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
    out, _, _, totals, _, _ = T.audit(rows, billed, [period], HOL)
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


def _billed_csv(tmp, buckets, gen_override=None, skip_generation=()):
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
    return p


def case_load_billed_rejects_asymmetric_sections():
    """Key SETS must match, not merely overlap: a both-sections omission must show."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        key = ("4/1/26 - 4/30/26", "winter", "off_peak")
        p = _billed_csv(tmp, {("4/1/26 - 4/30/26", "winter", "on_peak"): 100.0,
                              key: 50.0}, skip_generation=(key,))
        try:
            T.load_billed(p)
        except SystemExit as e:
            assert "different buckets" in str(e), e
            return "load_billed fails closed when a bucket exists in only one section"
    raise AssertionError("expected SystemExit on asymmetric delivery/generation keys")


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
    case_missing_billed_bucket_stops_the_run,
    case_unexpected_billed_bucket_stops_the_run,
    # coverage and totals
    case_load_intervals_rejects_a_file_with_no_data,
    case_partial_period_is_skipped_not_credited,
    case_interval_gap_inside_period_is_skipped,
    case_holiday_evidence_is_leave_one_out,
    case_period_total_catches_accumulated_bias,
    case_period_total_tolerates_pure_rounding,
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
