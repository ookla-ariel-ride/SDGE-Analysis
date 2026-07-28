#!/usr/bin/env python3
"""Guards for the canonical TOU assignment in rates.py.

The holiday rule is confirmed against the bills by analysis/tou_audit.py, which
shows that dropping any one of the eight tariff holidays makes its own billing
period reconcile measurably worse. The risk is not that the rule is wrong, it is
that a caller quietly bypasses it: for a long time several scripts derived the
day type from `weekday() >= 5` alone and so priced weekday holidays as ordinary
weekdays. These cases pin the rule to the canonical entry point and assert that
no analysis module still contains a private copy of the window logic.

Run from the repo root:  ./.venv/bin/python analysis/test_rates.py
"""
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as R

ANALYSIS = pathlib.Path(__file__).resolve().parent
# Window-shape enforcement lives in test_scripts_runnable.py, which detects the
# TOU period LABELS via the AST and so catches vectorised rewrites that a text
# search misses. What remains here is the day-type half: a bare weekday test
# silently drops the holiday rule even when the windows themselves are canonical.
# The legacy cross-plan ranking pair keeps its own table rates and its own
# calendar by design (TECHNICAL.md 3.1/3.2); tou_audit deliberately models
# alternative day-type rules in order to score them against the bills.
EXEMPT = {"analyze.py", "analyze_norelief.py", "tou_audit.py", "test_tou_audit.py",
          "test_rates.py", "rates.py",
          # lifetime_payback._per_old models the PRE-2026 window structure on
          # purpose (no midday super-off-peak outside March/April), for a
          # historical blended rate. It takes (hour, month) and so cannot apply
          # the holiday rule; the resulting bias on a fallback blended $/kWh is
          # far below its own uncertainty.
          "lifetime_payback.py"}


def case_eight_tariff_holidays():
    got = R.holidays([2026])
    assert len(got) == 8, sorted(got)
    for d in (dt.date(2026, 1, 1), dt.date(2026, 7, 4), dt.date(2026, 11, 11),
              dt.date(2026, 12, 25), dt.date(2026, 2, 16), dt.date(2026, 5, 25),
              dt.date(2026, 9, 7), dt.date(2026, 11, 26)):
        assert d in got, d
    return "holidays() is the eight tariff holidays in research/rates-reference.md"


def case_off_peak_day_covers_weekends_and_holidays():
    assert R.off_peak_day(dt.date(2026, 4, 11))          # Saturday
    assert R.off_peak_day(dt.date(2026, 4, 12))          # Sunday
    assert R.off_peak_day(dt.date(2025, 9, 1))           # Labor Day, a Monday
    assert not R.off_peak_day(dt.date(2025, 9, 3))       # ordinary Wednesday
    return "off_peak_day is true for weekends and for weekday tariff holidays"


def case_period_at_applies_the_holiday_rule():
    """08:00 separates the two: off-peak on a weekday, super-off-peak otherwise."""
    labor = dt.datetime(2025, 9, 1, 8, 0)
    plain = dt.datetime(2025, 9, 3, 8, 0)
    assert R.period_at(labor) == "sop", R.period_at(labor)
    assert R.period_at(plain) == "off", R.period_at(plain)
    assert R.period_at(dt.datetime(2025, 9, 1, 17, 0)) == "on"
    return "period_at treats a weekday holiday as a weekend without being asked"


def case_period_at_matches_period_when_told_the_truth():
    for d in (dt.date(2025, 9, 1), dt.date(2025, 9, 3), dt.date(2026, 4, 11)):
        for h in (0, 5.75, 6, 9.75, 10, 13.75, 14, 15.75, 16, 20.75, 21, 23.75):
            ts = dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=h)
            assert R.period_at(ts) == R.period(h, R.off_peak_day(d)), (d, h)
    return "period_at is period() plus the day-type derivation, nothing else"


def case_holiday_rule_spans_the_reporting_era():
    """_HOL must cover every year any analysis window can reach, including 2039."""
    for y in (2019, 2026, 2039, 2040):
        assert R.off_peak_day(dt.date(y, 12, 25)), y
    return "the holiday set spans 2019-2040, covering the NEM 2.0 horizon"


def case_no_analysis_module_derives_the_day_type_by_weekday_alone():
    bare = re.compile(r"weekday\(\)\s*>=\s*5|dt\.weekday\s*>=\s*5")
    offenders = []
    for f in sorted(ANALYSIS.glob("*.py")):
        if f.name in EXEMPT or f.name.startswith("test_"):
            continue
        if bare.search(f.read_text()):
            offenders.append(f.name)
    assert not offenders, f"bare weekday day-type test still in: {offenders}"
    return "no non-exempt analysis module derives the day type from weekday alone"


CASES = [
    case_eight_tariff_holidays,
    case_off_peak_day_covers_weekends_and_holidays,
    case_period_at_applies_the_holiday_rule,
    case_period_at_matches_period_when_told_the_truth,
    case_holiday_rule_spans_the_reporting_era,
    case_no_analysis_module_derives_the_day_type_by_weekday_alone,
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
