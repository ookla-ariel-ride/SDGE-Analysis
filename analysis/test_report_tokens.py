#!/usr/bin/env python3
"""Tests for report_tokens.py (issue #39, part 1).

Follows this repo's established CASES/@case/main() convention (see e.g.
test_reprice_by_vintage.py, test_gross_import_decomposition.py). The real
end-to-end resolution case needs the private archive (private/household.yaml,
the SAM 8760 exports, the raw Green Button export) that a handful of derived
tokens ultimately read through committed data/*.json and data/*.csv plus
household.yaml -- this checkout DOES have it staged, so that case exercises
the real path rather than skipping.

Run from the repo root:  ./.venv/bin/python analysis/test_report_tokens.py
"""
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import report_tokens as rt  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def _require_household():
    if not rt.hh.PATH.is_file():
        raise SkipCase(f"needs private/household.yaml ({rt.hh.PATH}), which this "
                       "checkout does not have")


# ---------------------------------------------------------------------------
# AC: "a test asserts the map's key set equals the template's token set
# exactly, so a new template token cannot ship unsourced". "The template's
# token set" for this module is BOTH the live tokens (the ~127 that survive in
# static markup) and the legitimate comment-only examples (the ~16 illustrative
# {{TOKEN}}s inside <!-- TODO --> blocks) -- the brief for this module is
# explicit that the comment-only ones are "legitimate tokens... the map must
# source them too", so equality is checked against their union, not just the
# live set. TOKENS is asserted equal to that union exactly (not just a
# superset), catching both an unsourced new template token AND a stale TOKENS
# entry for a token the template no longer has.
# ---------------------------------------------------------------------------
@case
def case_token_map_key_set_equals_the_templates_full_token_set():
    live, comment_only = rt.template_tokens()
    full = live | comment_only
    declared = set(rt.TOKENS)
    missing = full - declared
    extra = declared - full
    assert not missing, f"template token(s) with no TOKENS entry: {sorted(missing)}"
    assert not extra, f"TOKENS entries for tokens no longer in the template: {sorted(extra)}"
    assert len(full) > 100, f"only {len(full)} tokens parsed -- the parser probably broke"
    return (f"TOKENS' {len(declared)} entries exactly match the template's own "
            f"{len(live)} live + {len(comment_only)} comment-only tokens")


@case
def case_two_generic_meta_strings_are_excluded_not_treated_as_tokens():
    _, comment_only = rt.template_tokens()
    assert "TOKEN" not in comment_only
    assert "DOUBLE_BRACE_TOKENS" not in comment_only
    assert "TOKEN" not in rt.TOKENS
    assert "DOUBLE_BRACE_TOKENS" not in rt.TOKENS
    return "the two generic instructional strings are excluded from the token set"


@case
def case_template_parser_classifies_live_vs_comment_only_correctly():
    """A synthetic snippet exercising the classification rule directly: a
    token outside any <!-- --> span is live even if a comment sits right next
    to it; a token that ONLY ever appears inside a comment is comment-only;
    a token appearing both ways is live (comment-only is defined as ONLY
    inside comments)."""
    html = (
        "<p>{{LIVE_ONE}}</p>\n"
        "<!-- a comment mentioning {{COMMENT_ONLY}} as an example -->\n"
        "<p>{{BOTH}}</p><!-- {{BOTH}} appears here too -->\n"
        "<!-- generic instructional text says use {{TOKEN}} and "
        "{{DOUBLE_BRACE_TOKENS}} -->\n"
    )
    live, comment_only = rt.template_tokens(html)
    assert live == {"LIVE_ONE", "BOTH"}, live
    assert comment_only == {"COMMENT_ONLY"}, comment_only
    return "the live/comment-only split matches hand-traced expectations on a synthetic snippet"


# ---------------------------------------------------------------------------
# AC: "every map entry actually resolves against the real committed archive" /
# "run your own resolver against the real data... fix every failure". Gap
# tokens are the one documented, named exception -- checked separately below.
# ---------------------------------------------------------------------------
@case
def case_every_non_gap_token_resolves_to_a_non_empty_string_on_the_real_archive():
    _require_household()
    resolved = rt.resolve_all(include_gaps=False)
    gap_names = {n for n, s in rt.TOKENS.items() if s.get("kind") == "gap"}
    assert set(resolved) == set(rt.TOKENS) - gap_names, (
        "resolve_all's key set does not match TOKENS minus the declared gaps")
    empty = [n for n, v in resolved.items() if not v.strip()]
    assert not empty, f"token(s) resolved to an empty/blank string: {empty}"
    return f"all {len(resolved)} non-gap tokens resolve to a non-empty string on the real archive"


@case
def case_known_gaps_are_small_and_each_fails_closed_by_name():
    """The honest-gap allowance (CLAUDE.md section 0: 'not determined' beats a
    guess) is small and every gap fails LOUDLY, naming itself and its reason --
    it never silently returns an empty string or a placeholder."""
    gap_names = {n for n, s in rt.TOKENS.items() if s.get("kind") == "gap"}
    assert gap_names == set(rt.KNOWN_GAPS), (
        f"TOKENS' gap set {sorted(gap_names)} != KNOWN_GAPS {sorted(rt.KNOWN_GAPS)}")
    assert len(gap_names) <= 8, (
        f"{len(gap_names)} unsourced tokens is no longer a small, well-justified "
        "list -- find real sources or reconsider the design")
    for name in gap_names:
        try:
            rt.resolve_token(name)
            raise AssertionError(f"{name}: gap token resolved instead of failing closed")
        except SystemExit as e:
            assert name in str(e), f"{name}: SystemExit message doesn't name the token: {e}"
            assert rt.KNOWN_GAPS[name][:20] in str(e) or rt.KNOWN_GAPS[name] in str(e), (
                f"{name}: SystemExit message doesn't carry its documented reason")
    return f"{len(gap_names)} named gap token(s), each fails closed naming itself and its reason"


# ---------------------------------------------------------------------------
# AC: privacy -- a household_yaml token pointing at a private-only field must
# be refused, not silently read.
# ---------------------------------------------------------------------------
@case
def case_household_yaml_token_at_a_private_only_field_refuses_to_resolve():
    tiers = rt._hh_tiers()
    private_paths = [p for p, tier in tiers.items() if tier == "private-only"]
    assert private_paths, "no private-only household.yaml path found to fabricate a test with"
    poisoned = "location.lat"
    assert poisoned in private_paths, (
        f"{poisoned!r} is expected to be private-only per the cheatsheet; tier map "
        f"disagrees ({tiers.get(poisoned)!r}) -- pick another path from {private_paths[:5]}")
    fake_spec = {"kind": "household_yaml", "path": poisoned, "fmt": None}
    try:
        rt.resolve_token("ZZZ_FABRICATED_PRIVATE_TOKEN", fake_spec)
        raise AssertionError("resolver read a private-only household.yaml field")
    except SystemExit as e:
        msg = str(e)
        assert poisoned in msg, msg
        assert "private-only" in msg, msg
    return (f"a fabricated token spec pointing at the private-only field {poisoned!r} "
            "is refused with SystemExit naming the path and its tier")


@case
def case_household_yaml_token_at_an_untiered_path_also_refuses():
    """Defense in depth: a path the cheatsheet has never heard of at all (a
    typo, or a future household.yaml key nobody has tiered yet) must fail
    exactly as closed as a known private-only one -- an untiered key is not
    an implicit public-ok."""
    fake_spec = {"kind": "household_yaml", "path": "this.path.does.not.exist.anywhere", "fmt": None}
    try:
        rt.resolve_token("ZZZ_FABRICATED_UNTIERED_TOKEN", fake_spec)
        raise AssertionError("resolver read an untiered household.yaml path")
    except SystemExit as e:
        assert "not a field" in str(e) or "not a cheatsheet-tiered" in str(e), e
    return "a fabricated token spec at an untiered household.yaml path is also refused"


# ---------------------------------------------------------------------------
# AC: a token whose source path doesn't exist triggers SystemExit naming that
# specific token, not a generic crash.
# ---------------------------------------------------------------------------
@case
def case_a_broken_source_path_fails_closed_naming_the_specific_token():
    fake_spec = {"kind": "data_json", "file": "report_data.json",
                 "path": ("does_not_exist", "at_all"), "fmt": None}
    try:
        rt.resolve_token("ZZZ_FABRICATED_BROKEN_PATH_TOKEN", fake_spec)
        raise AssertionError("resolver did not fail on a nonexistent JSON path")
    except SystemExit as e:
        assert "ZZZ_FABRICATED_BROKEN_PATH_TOKEN" in str(e), (
            f"SystemExit does not name the failing token: {e}")
    return "a fabricated token with a broken data_json path fails closed naming itself"


@case
def case_a_missing_data_file_fails_closed_naming_the_file():
    fake_spec = {"kind": "data_csv", "file": "this_file_does_not_exist.csv",
                 "get": lambda rows: rows, "fmt": None}
    try:
        rt.resolve_token("ZZZ_FABRICATED_MISSING_FILE_TOKEN", fake_spec)
        raise AssertionError("resolver did not fail on a missing csv file")
    except SystemExit as e:
        assert "this_file_does_not_exist.csv" in str(e), e
    return "a fabricated token naming a missing data/*.csv file fails closed naming the file"


# ---------------------------------------------------------------------------
# AC: at least 2 derived tokens verified against hand-computed expected
# values (independent of the resolver's own arithmetic).
# ---------------------------------------------------------------------------
@case
def case_specific_yield_derived_token_matches_hand_computation():
    _require_household()
    production = rt._annual_production_kwh(rt.CTX)
    kw_dc = rt.hh1("solar.kw_dc")
    expected = round(production / kw_dc)
    rendered = rt.resolve_token("SPECIFIC_YIELD")
    assert rendered == f"{expected:,.0f}", (rendered, expected)
    # independently re-derive production and kw_dc from the raw files/yaml
    # rather than trusting the module's own helper, so this is a real
    # hand-computed check and not a tautology against the code under test.
    import csv
    with open(rt.DATA / "enphase_daily_production.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    total_row = [r for r in rows if r["Date/Time"] == "Total"][0]
    prod2 = float(total_row["Energy Delivered (kWh)"].replace(",", ""))
    assert abs(prod2 - production) < 1e-6, (prod2, production)
    assert abs(prod2 / kw_dc - 1642) < 1.0, (
        f"specific yield {prod2 / kw_dc:.1f} kWh/kW/yr is far from the ~1,642 "
        "this household's live report cites -- sanity check failed")
    return f"SPECIFIC_YIELD = {rendered} kWh/kW/yr, matching an independent hand computation"


@case
def case_cleaned_ratio_derived_token_matches_hand_computation():
    _require_household()
    import csv
    import datetime as dt
    clean_date_raw = rt._cleaning_entry()["date"]
    d0 = rt._as_date(clean_date_raw)
    clean_date = dt.datetime(d0.year, d0.month, d0.day)
    with open(rt.DATA / "cleaning_study_daily.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    parsed = [(dt.datetime.strptime(r["date"], "%Y%m%d"), float(r["generated_kwh"]))
              for r in rows]
    pre = sorted(v for d, v in parsed if clean_date - dt.timedelta(days=30) <= d < clean_date)
    post = sorted(v for d, v in parsed if clean_date < d <= clean_date + dt.timedelta(days=30))

    def median(xs):
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    expected_ratio = round(median(post) / median(pre), 3)
    rendered = float(rt.resolve_token("CLEANED_RATIO"))
    assert abs(rendered - round(expected_ratio, 2)) < 0.01, (rendered, expected_ratio)
    return f"CLEANED_RATIO = {rendered}, matching an independent hand computation ({expected_ratio})"


@case
def case_annual_load_kwh_derived_token_matches_conservation_identity():
    _require_household()
    rd = rt._json("report_data.json")
    production = rt._annual_production_kwh(rt.CTX)
    expected = round(rd["totals"]["imp"] + (production - rd["totals"]["exp"]))
    rendered = int(rt.resolve_token("ANNUAL_LOAD_KWH").replace(",", ""))
    assert rendered == expected, (rendered, expected)
    return f"ANNUAL_LOAD_KWH = {rendered:,} kWh, matching the imports+self-consumed identity"


# ---------------------------------------------------------------------------
# Spot checks tying specific tokens to hand-verified expected substrings
# (guards against a format-spec regression even when the underlying number
# is right).
# ---------------------------------------------------------------------------
@case
def case_format_specs_render_as_expected_on_known_values():
    _require_household()
    assert rt.resolve_token("SUPER_OFF_PEAK_RATE") == "12.5¢"
    assert rt.resolve_token("PEAK_WINDOW") == "4–9pm"
    assert rt.resolve_token("CLIMATE_ZONE") == "Coastal"
    assert rt.resolve_token("BEST_PLAN") == "EV-TOU-5"
    assert rt.resolve_token("PANEL_COUNT") == "30"
    year = rt.resolve_token("FIRST_FULL_YEAR")
    assert "," not in year, f"FIRST_FULL_YEAR should render as a bare year, got {year!r}"
    return "spot-checked format specs render without stray commas or unit drift"


@case
def case_unknown_token_name_fails_closed_not_keyerror():
    """resolve_token(name) with no spec argument looks the name up in TOKENS itself
    -- that lookup must fail closed (SystemExit naming the token) rather than
    raising a bare KeyError, since a caller (e.g. the egress preflight in
    llm_providers.py) catches SystemExit to decide whether a token is safe to
    render, not KeyError."""
    try:
        rt.resolve_token("ZZZ_TOTALLY_UNKNOWN_TOKEN_NAME")
        raise AssertionError("resolver accepted an unknown token name")
    except SystemExit as e:
        assert "ZZZ_TOTALLY_UNKNOWN_TOKEN_NAME" in str(e), e
    return "an unknown token name raises SystemExit naming itself, not KeyError"


@case
def case_unknown_token_kind_fails_closed():
    fake_spec = {"kind": "not_a_real_kind", "fmt": None}
    try:
        rt.resolve_token("ZZZ_FABRICATED_BAD_KIND_TOKEN", fake_spec)
        raise AssertionError("resolver accepted an unknown token kind")
    except SystemExit as e:
        assert "ZZZ_FABRICATED_BAD_KIND_TOKEN" in str(e) or "unknown token kind" in str(e), e
    return "an unrecognized token kind fails closed rather than silently no-op'ing"


@case
def case_unknown_format_spec_fails_closed():
    fake_spec = {"kind": "cited_constant", "value": 42, "fmt": "not_a_real_format"}
    try:
        rt.resolve_token("ZZZ_FABRICATED_BAD_FORMAT_TOKEN", fake_spec)
        raise AssertionError("resolver accepted an unknown format spec")
    except SystemExit as e:
        assert "ZZZ_FABRICATED_BAD_FORMAT_TOKEN" in str(e), e
    return "an unrecognized format spec fails closed naming the token"


@case
def case_no_token_declared_twice():
    # _tok() itself raises SystemExit on a duplicate registration, so if this
    # module imported cleanly at all, TOKENS already has no duplicate keys --
    # this case documents and locks that guarantee.
    names = list(rt.TOKENS)
    assert len(names) == len(set(names)), "TOKENS has a duplicate key (impossible via _tok)"
    return f"{len(names)} distinct token names, none declared twice"


# ---------------------------------------------------------------------------
# Codex review (pass 3), finding 2: _paid_off() used to compare season NAMES
# as strings ("fall" < "summer" is alphabetically true even though fall
# comes after summer in the same year), which could report a same-year
# crossover as already paid off before it had actually happened. Fixed to
# compare (year, month) numerically. Each case fixes BOTH the crossover
# ("today" as far as _crossover_season_year is concerned) and the system
# clock, via monkeypatching, so the scenario is exact and repeatable rather
# than depending on whatever the real committed artifact's crossover date
# happens to be relative to whenever the suite runs.
# ---------------------------------------------------------------------------
class _patched:
    """Temporarily replace an attribute on `obj`, restoring it on exit."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


def _frozen_today(year, month, day=15):
    class _FakeDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(year, month, day)
    return _FakeDate


def _paid_off_for(crossover_year, crossover_month, today_year, today_month):
    fake_crossover = lambda which: ("fake-season", crossover_year, crossover_month)  # noqa: E731
    with _patched(rt, "_crossover_season_year", fake_crossover), \
         _patched(rt.dt, "date", _frozen_today(today_year, today_month)):
        return rt._paid_off(None)


@case
def case_paid_off_is_false_for_a_crossover_later_in_the_current_year():
    # crossover in December, "today" in January of the same year -- not yet.
    assert _paid_off_for(2026, 12, 2026, 1) is False
    return "a crossover later in the current year is correctly NOT YET paid off"


@case
def case_paid_off_is_true_for_a_crossover_earlier_in_the_current_year():
    # crossover in January, "today" in December of the same year -- already.
    assert _paid_off_for(2026, 1, 2026, 12) is True
    return "a crossover earlier in the current year is correctly already paid off"


@case
def case_paid_off_is_false_for_the_exact_bug_scenario():
    """The exact case Codex found wrong: crossover in fall (~month 10) of the
    current year, "today" in summer (~month 7) of the SAME year. String
    comparison said 'fall' < 'summer' (alphabetically true, f < s) and
    reported this as already paid off, even though fall is chronologically
    AFTER summer in the same calendar year -- the crossover had NOT
    happened yet. Numeric (year, month) comparison must get this right."""
    assert rt._season_for_month(10) == "fall"
    assert rt._season_for_month(7) == "summer"
    assert _paid_off_for(2026, 10, 2026, 7) is False, (
        "a fall crossover must not be reported as paid off when 'today' is "
        "only summer of the same year")
    return "crossover=fall/current year, today=summer/same year: correctly NOT YET paid off"


@case
def case_paid_off_boundary_same_year_same_month_is_not_yet_paid_off():
    """Boundary case, decided and documented in _paid_off()'s own docstring:
    a crossover landing in the CURRENT month is NOT reported as paid off.
    This module's resolution is monthly, not daily, so within the current
    month there's no way to know whether the crossover fell before or after
    "today" -- and CLAUDE.md section 0 records an event only once it has
    definitely happened, not "probably, sometime this month.\""""
    assert _paid_off_for(2026, 6, 2026, 6) is False
    return "a crossover in the current month is NOT YET paid off (strict '<', documented choice)"


@case
def case_paid_off_is_true_for_a_crossover_in_a_past_year():
    assert _paid_off_for(2024, 12, 2026, 1) is True
    return "a crossover in an earlier year is paid off regardless of season/month"


@case
def case_paid_off_is_false_for_a_crossover_in_a_future_year():
    assert _paid_off_for(2028, 1, 2026, 12) is False
    return "a crossover in a later year is not yet paid off regardless of season/month"


@case
def case_crossover_season_year_returns_a_three_tuple_with_a_numeric_month():
    result = rt._crossover_season_year("gross")
    assert len(result) == 3, result
    season, year, month = result
    assert isinstance(month, int) and 1 <= month <= 12, result
    assert rt._season_for_month(month) == season, result
    return f"_crossover_season_year returns (season, year, month) = {result}"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
