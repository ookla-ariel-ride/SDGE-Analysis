#!/usr/bin/env python3
"""Canonical rate constants and billing engine — single source of truth.

Provenance: every value below was read off the detailed SDG&E bills
(EV-TOU-5 delivery + CEA "Clean Impact Plus" generation, effective 6/1/2026)
and matches the billed line items to the penny. Import THIS module; do not
re-declare rate constants in analysis scripts. (analyze.py, the legacy
cross-plan ranking model, retains published rate-table values for all plans —
fine for RANKING, not for absolute dollars; see TECHNICAL.md.)

Billing mechanics (NEM 2.0, matching the bills):
  * energy charges netted per (month, season, TOU period): positive net billed
    at UDC+CEA+PCIA; negative net credited at UDC+CEA
  * PCIA is applied to NET kWh — bill evidence: the same statement charges
    "PCIA 2023 224 kWh x rate" on a period with 224 kWh NET usage while charging
    "Wildfire Fund Charge 308 kWh" (gross imports) — so netting PCIA inside the
    energy rate is bill-correct. Exports offset PCIA only within net-positive
    buckets; every month of the audit year was net-positive, so the treatment of
    PCIA in a net-NEGATIVE period is untested against a bill (not determinable
    from available statements; bounded ambiguity only if a month goes negative)
  * non-bypassable charges (NBC) on GROSS imported kWh — never netted
    (bill evidence: wildfire charged on 308 gross kWh vs 224 net)
  * Base Services Charge per day
TOU windows as implemented here: on-peak 16-21 daily; super-off-peak 0-6 + 10-14
weekdays, 0-14 weekends; off-peak otherwise. This is the CURRENT tariff, which is
what a projection at constant current rates needs, and analysis/tou_audit.py
reconciles it against the three most recent statements to within 0.5 kWh.

APPLICABILITY (read before reusing for your own house): the netting STRUCTURE
above is NEM 2.0 -- monthly per-period netting with retail-rate export credit.
Solar interconnected after April 2023 bills under NEM 3.0 / the Solar Billing
Plan (hourly netting, avoided-cost export credits): swapping the rate constants
is NOT enough, bill_nem_monthly()'s structure must be replaced. Likewise the
window SHAPES in period() and SUMMER_MONTHS are EV-TOU-5-specific: a different
SDG&E plan (or another utility) changes the windows, not just the prices. In
every case the gate is CLAUDE.md section 1: the engine must reproduce YOUR
actual bills before any absolute dollar is quoted.

Two things it deliberately does not do, both established by that audit:
  * The weekday 10-14 super-off-peak window took effect 2026-03-01; before that
    those hours were off-peak. Applying today's window to earlier dates is correct
    for a forward projection and wrong for reproducing a historical statement,
    where it misallocates 250-360 kWh per period between off-peak and
    super-off-peak while leaving the period total right. Anything re-billing
    history needs the historical WINDOWS, not just historical prices.
  * The eight tariff holidays in research/rates-reference.md take WEEKEND windows.
    Each is confirmed against the bills by analysis/tou_audit.py: dropping any one
    makes its billing period reconcile worse, by 5.0 to 59.3 kWh against a 3.0 kWh
    rounding bound. Callers get this by passing off_peak_day(date) as period()'s
    is_weekend argument rather than a bare weekday test.
"""
import datetime as _dt

UDC = {"S": {"on": 0.30203, "off": 0.30203, "sop": 0.02606},
       "W": {"on": 0.31174, "off": 0.31174, "sop": 0.02606}}
CEA = {"S": {"on": 0.51684, "off": 0.15975, "sop": 0.04961},
       "W": {"on": 0.24430, "off": 0.15782, "sop": 0.05187}}
NBC = 0.021; PCIA = 0.02828; BSC = 0.79343
SUMMER_MONTHS = {6, 7, 8, 9, 10}   # tariff season; import this, never re-list the months

energy = lambda s, p: UDC[s][p] + CEA[s][p] + PCIA      # netted energy rate
credit = lambda s, p: UDC[s][p] + CEA[s][p]             # export credit
allin  = lambda s, p: UDC[s][p] + CEA[s][p] + PCIA + NBC  # marginal gross import

def holidays(years):
    """The eight tariff holidays that take weekend TOU windows.

    Source: research/rates-reference.md. Confirmed against the bills individually
    by analysis/tou_audit.py -- this is measured, not assumed.

    WEEKEND-FALLING HOLIDAYS carry the documented observed-day rule: SDG&E's
    holiday-TOU documentation (sdge.ca/sdge-holiday-rates, read 2026-07-29) states
    an asymmetric shift -- a SUNDAY holiday is also observed the following Monday;
    a SATURDAY holiday does not shift. Only the four fixed-date holidays can fall
    on a weekend, so the rule is applied to those. This is DOCUMENTATION-SOURCED,
    not yet bill-confirmed: no holiday in the audited statement corpus falls on a
    weekend, and the documented rule changes no date inside any current analysis
    window -- the first date it adds is 2027-07-05 (July 4, 2027 is a Sunday).
    Encoding it now keeps this calendar aligned with the operational layers that
    already carry the observed dates (PVOutput's tariff Public Holidays list and
    the observatory holidays table, both seeded 2026-07-29); leaving it out would
    fork the calendars and silently mis-bucket every projection past July 2027.

    The empirical adjudication is EXECUTABLE, not prose: whenever a weekend
    holiday enters an audited billing period, tou_audit.weekend_shift_evidence()
    scores the no-shift, observed-Friday and observed-Monday variants against the
    statement's printed buckets with an explicit identifiability bound, and
    reports indeterminate when the discriminating energy is inside statement
    rounding. If a bill ever decisively contradicts the documented rule, that
    audit fails the run and this set must change to follow the bills.
    """
    def nth_weekday(y, m, wd, n):
        c = _dt.date(y, m, 1)
        return c + _dt.timedelta(days=(wd - c.weekday()) % 7 + 7 * (n - 1))

    out = set()
    for y in years:
        for fixed in (_dt.date(y, 1, 1), _dt.date(y, 7, 4),
                      _dt.date(y, 11, 11), _dt.date(y, 12, 25)):
            out.add(fixed)
            if fixed.weekday() == 6:            # Sunday: following Monday observed
                out.add(fixed + _dt.timedelta(days=1))
        out.add(nth_weekday(y, 2, 0, 3))    # Presidents: 3rd Monday, February
        out.add(nth_weekday(y, 9, 0, 1))    # Labor: 1st Monday, September
        out.add(nth_weekday(y, 11, 3, 4))   # Thanksgiving: 4th Thursday, November
        c = _dt.date(y, 5, 31)              # Memorial: last Monday, May
        while c.weekday() != 0:
            c -= _dt.timedelta(days=1)
        out.add(c)
    return out


_HOL_YEARS = (2019, 2050)          # PTO year .. past the 2039 NEM 2.0 horizon
_HOL = holidays(range(_HOL_YEARS[0], _HOL_YEARS[1] + 1))


def off_peak_day(date):
    """True when `date` takes weekend TOU windows: a weekend, or a tariff holiday.

    Pass this to period() as is_weekend. Deriving it from weekday() alone drops the
    holiday rule, which the bills show the tariff applies.

    Fails loudly outside the built holiday calendar rather than treating an
    uncovered date as a plain weekday: `date in _HOL` cannot distinguish "not a
    holiday" from "not in the calendar" or "not a date at all" (NaT's year is
    nan, so the bound check rejects it too). Everything was migrated onto this
    module precisely so correctness concentrates here; a soft fallback at the
    chokepoint would spread a silent bias to every caller at once.
    """
    if not (_HOL_YEARS[0] <= date.year <= _HOL_YEARS[1]):
        raise ValueError(f"date {date!r} outside the {_HOL_YEARS[0]}-{_HOL_YEARS[1]} "
                         "holiday calendar in rates.py; widen _HOL_YEARS rather than "
                         "letting holidays silently vanish")
    return date.weekday() >= 5 or date in _HOL


def period_at(ts):
    """TOU period for a timestamp. THE canonical assignment -- prefer this.

    Takes a datetime and derives both the hour and the weekend-or-holiday status
    itself, so a caller cannot forget the holiday rule. period() below leaves
    that flag to the caller, which is exactly how weekday holidays got priced as
    ordinary weekdays in several scripts for as long as they existed.

    No soft fallbacks: a value without .hour/.minute/.date raises. The previous
    getattr(ts, "minute", 0) defended no real caller and only widened what the
    function accepted without complaint.
    """
    d = ts.date() if hasattr(ts, "date") else ts
    hour = ts.hour + ts.minute / 60
    return period(hour, off_peak_day(d))


def period(hour_frac, is_weekend):
    """Low-level assignment; the caller supplies the day type.

    Use period_at() unless you are deliberately modelling a different day-type
    rule (tou_audit.py does this to score day-type and window variants against
    the bills). A
    bare weekday>=5 test here silently drops the confirmed holiday rule.
    """
    if 16 <= hour_frac < 21: return "on"
    if is_weekend: return "sop" if hour_frac < 14 else "off"
    return "sop" if (hour_frac < 6 or 10 <= hour_frac < 14) else "off"

def dst_transition_sundays(year):
    """(spring-forward, fall-back) Sundays under the US rules in force."""
    def nth_sunday(month, n):
        c = _dt.date(year, month, 1)
        return c + _dt.timedelta(days=(6 - c.weekday()) % 7 + 7 * (n - 1))
    return nth_sunday(3, 2), nth_sunday(11, 1)


def expected_day_hours(date):
    """The exact multiset of 15-minute hour_frac slots a calendar day carries.

    96 slots ordinarily; the spring-forward Sunday lacks 02:00-02:45 (92); the
    fall-back Sunday repeats 01:00-01:45 (100). This is wall-clock tariff
    arithmetic, so it lives here with the rest of the tariff clock.
    """
    spring, fall = dst_transition_sundays(date.year)
    base = [i * 0.25 for i in range(96)]
    if date == spring:
        return sorted(h for h in base if not 2.0 <= h < 3.0)
    if date == fall:
        return sorted(base + [1.0, 1.25, 1.5, 1.75])
    return base


def validate_interval_coverage(pairs, start, end):
    """Fail loudly unless [start, end] is completely and exactly covered.

    `pairs` is an iterable of (date, hour_frac). Checks BOTH directions --
    every calendar day in the window present, no day outside it -- and each
    day's slot multiset against expected_day_hours, so a wholly missing day, a
    truncated day, and an offsetting duplicate-plus-omission (same count,
    wrong slots) are all rejected. A day absent from the data never shows up
    in a groupby, which is exactly how the 27-day October bill gap silently
    understated an annual baseline by 9% (CLAUDE.md section 1).
    """
    got = {}
    for d, h in pairs:
        got.setdefault(d, []).append(h)
    want_days = {start + _dt.timedelta(days=i) for i in range((end - start).days + 1)}
    missing = sorted(want_days - set(got))
    extra = sorted(set(got) - want_days)
    if missing or extra:
        raise SystemExit(
            f"interval coverage does not match the {start}..{end} window: "
            f"{len(missing)} day(s) missing entirely "
            f"({', '.join(map(str, missing[:5]))}{'...' if len(missing) > 5 else ''}), "
            f"{len(extra)} day(s) outside the window")
    bad = []
    for d in sorted(got):
        if sorted(got[d]) != expected_day_hours(d):
            bad.append(f"{d} has {len(got[d])} slots or wrong slot times")
    if bad:
        raise SystemExit("malformed interval days: " + "; ".join(bad[:5]))


def bill_nem_monthly(frame, imp="Consumption", exp="Generation"):
    """{month: $} via monthly per-period NEM netting + NBC on gross imports + BSC.
    frame needs columns: dt, seas ('S'/'W'), p ('on'/'off'/'sop'), ym, imp, exp."""
    out = {}
    for ym, m in frame.groupby("ym"):
        tot = m.dt.dt.date.nunique() * BSC + m[imp].sum() * NBC
        for s in ("S", "W"):
            for p in ("on", "off", "sop"):
                sub = m[(m.seas == s) & (m.p == p)]
                net = sub[imp].sum() - sub[exp].sum()
                tot += net * (energy(s, p) if net >= 0 else credit(s, p))
        out[str(ym)] = tot
    return out

def bill_nem(frame, imp="Consumption", exp="Generation"):
    """Annual $ (sum of bill_nem_monthly)."""
    return sum(bill_nem_monthly(frame, imp, exp).values())
