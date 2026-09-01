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
import ast
import contextlib
import copy
import datetime as dt
import html as _htmllib
import itertools
import pathlib
import re
import subprocess
import sys
import tempfile

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
def case_battery_charge_kw_tokens_match_the_dispatch_policies_source_constants():
    """Issue #71: BATTERY_CHARGE_KW / BATTERY_EXPANDED_CHARGE_KW are cited_constant
    tokens (report_tokens.py has no python import of battery_dispatch_policies.py,
    only JSON reads of its OUTPUT, so the values are literal copies) -- verify them
    against an independent import of the actual source module's CHARGE_KW /
    CHARGE_KW_WITH_EXPANSION constants, not just against the hardcoded literal, so a
    future edit to battery_dispatch_policies.py that changes the charge rating would
    be caught here rather than silently drifting from the cited value."""
    _require_household()
    import battery_dispatch_policies as bp
    assert bp.CHARGE_KW == 5.0, bp.CHARGE_KW
    assert bp.CHARGE_KW_WITH_EXPANSION == 8.0, bp.CHARGE_KW_WITH_EXPANSION
    bare = rt.resolve_token("BATTERY_CHARGE_KW")
    expanded = rt.resolve_token("BATTERY_EXPANDED_CHARGE_KW")
    assert bare == "5.0", bare
    assert expanded == "8.0", expanded
    assert bare == f"{bp.CHARGE_KW:.1f}", (bare, bp.CHARGE_KW)
    assert expanded == f"{bp.CHARGE_KW_WITH_EXPANSION:.1f}", (expanded, bp.CHARGE_KW_WITH_EXPANSION)
    return ("BATTERY_CHARGE_KW=5.0 kW (bare unit), BATTERY_EXPANDED_CHARGE_KW=8.0 kW "
            "(with expansion), both matching battery_dispatch_policies.py's own "
            "CHARGE_KW / CHARGE_KW_WITH_EXPANSION constants")


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


@case
def case_phantom_method_discrepancy_reconciles_the_two_live_pricings():
    """Issue #140. This token used to set section 9's figure against section
    13's, because the two sections priced one load out of two artifacts. They
    no longer do, so what it reconciles now is the two PRICING METHODS inside
    the one artifact that has a generator: a per-interval price map and a full
    monthly NEM re-bill.

    Three properties, and the third is the one that matters. It states BOTH
    totals (a reconciliation that prints one figure is not one) and how far
    apart they are; it cites neither superseded workpaper; and it REFUSES to
    render at all if the artifact ever stops saying what the agreement covers.
    That last one is the difference between reconciling two methods and
    implying they check each other: both start from the same floor allocation
    and the same rates.py, so their closeness tests the netting treatment and
    nothing else. Without scope_of_agreement in the artifact, a bare "1.2%
    apart" is a claim the report cannot support (CLAUDE.md section 0)."""
    pricing = rt._json("quiet_night_floor.json")["pricing"]
    a = pricing["method_a_price_map"]["total_usd"]
    b = pricing["method_b_rebill"]["total_usd"]
    rendered = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    for what, value in (("the price map total", f"${a:,.0f}/yr"),
                        ("the re-bill total", f"${b:,.0f}/yr"),
                        ("the gap between them",
                         f"{abs(pricing['reconciliation']['gap_pct']):.1f}%")):
        assert value in rendered, (
            f"the reconciliation must state {what} ({value}) -- got: {rendered}")
    # deep_results.json:phantom used to carry a THIRD price for this same load
    # -- its annual kWh times a hardcoded flat 0.20 $/kWh, about half what
    # rates.py implies for that energy. Issue #172 deleted the field instead of
    # repricing it, precisely so there is nothing here to cite, so the check is
    # now structural: that block states energy and no dollars.
    dp = rt._json("deep_results.json")["phantom"]
    assert not [k for k in dp if re.search(r"cost|usd|price|blend", k, re.I)], (
        f"deep_results.json:phantom publishes a dollar figure again ({sorted(dp)}) -- "
        "quiet_night_floor.json prices this physical load through rates.py off its OWN "
        "separately measured estimate of it, so a dollar figure here puts a third number "
        "on one load")
    assert "does not settle" not in rendered, (
        "the reconciliation still says the report does not settle which pricing is right, "
        f"which was true of the retired cross-section comparison, not of this one: {rendered}")

    # The refusal, driven rather than described.
    real = rt._json

    def drop_scope(doc):
        doc["pricing"]["reconciliation"]["scope_of_agreement"] = "   "

    rt._json = _stub_for("quiet_night_floor.json", drop_scope)
    try:
        rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    except SystemExit as e:
        assert "scope_of_agreement" in str(e), (
            f"the refusal must name the missing field: {e}")
    else:
        raise AssertionError(
            "PHANTOM_METHOD_DISCREPANCY rendered an agreement claim with no statement of "
            "what that agreement covers -- a bare percentage a reader would over-read")
    finally:
        rt._json = real
    return (f"the reconciliation states both pricings (${a:,.0f}/yr and ${b:,.0f}/yr) and "
            "their gap, cites no superseded workpaper, and refuses to render at all "
            "without the artifact's own scope_of_agreement")


# The assurance clause, quoted once. Both halves are asserted separately: a
# rewrite that keeps one and drops the other is still a sentence promising the
# reader something, and a test that matched the whole string would pass on it.
_NETTING_ASSURANCE = ("close enough to",
                      "not where a material error would be hiding")


@case
def case_the_netting_reconciliation_never_assures_what_it_has_not_checked():
    """ISSUE #140, ADVERSARIAL PASS 2, FINDING 1. PHANTOM_METHOD_DISCREPANCY
    read three things out of the artifact independently -- method (a)'s total,
    method (b)'s total, and the precomputed gap_usd/gap_pct -- never checked
    that the third described the first two, and then rendered the same
    assurance ("close enough to say the monthly-netting treatment is not where
    a material error would be hiding") whatever they said.

    That is a promise about a number the sentence never inspected. A stale or
    half-regenerated artifact -- two totals rewritten, the reconciliation block
    left behind -- published materially divergent figures beside an obsolete
    "1.2% apart" AND an explicit assurance to the reader that no material
    netting error exists. The token this one replaced claimed nothing of the
    kind; the hazard arrived with the rewrite, which is why it is pinned here
    by behaviour rather than left to the artifact staying fresh.

    Four properties, driven rather than described:

      1. THE GAP IS THE ARITHMETIC. On the committed artifact the published
         gap equals method (a) minus method (b), so the figure is derived from
         the two figures beside it and not read from a field that could
         disagree with them.
      2. AN INCONSISTENT ARTIFACT IS REFUSED BY NAME, in either field. Neither
         reading is preferred over the other -- publishing the recomputation
         would silently overwrite the artifact's own claim, publishing the
         field would restore the defect -- so the refusal names the field and
         stops.
      3. A WIDE GAP RENDERS WITHOUT ASSURANCE. It is not a refusal: a
         household regenerating this artifact can land on a wider gap and must
         still get a report. It states both totals and the distance between
         them, says the report does not settle which is right, and -- the
         assertion the defect is actually about -- does NOT contain the
         assurance sentence. The bug here is a sentence that should not
         appear, so its ABSENCE is what gets asserted.
      4. THE THRESHOLD IS THE GATE, not the artifact's mood: pinned on both
         sides of _NETTING_MATERIALITY_PCT, in both directions of sign (the
         committed gap is negative -- method (b) prices the floor higher)."""
    pricing = rt._json("quiet_night_floor.json")["pricing"]
    a = pricing["method_a_price_map"]["total_usd"]
    b = pricing["method_b_rebill"]["total_usd"]
    rec = pricing["reconciliation"]

    # (1) The committed artifact: assurance present, and earned.
    live = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    for phrase in _NETTING_ASSURANCE:
        assert phrase in live, (
            f"the committed artifact's {abs(rec['gap_pct'])}% gap is inside the "
            f"{rt._NETTING_MATERIALITY_PCT}% threshold, so the reconciliation must still "
            f"say {phrase!r} -- got: {live}")
    assert abs(rec["gap_pct"]) < rt._NETTING_MATERIALITY_PCT, (
        f"the committed gap ({rec['gap_pct']}%) is no longer inside "
        f"_NETTING_MATERIALITY_PCT ({rt._NETTING_MATERIALITY_PCT}%), so this case is "
        "asserting the wrong branch -- the artifact moved, not the token")
    assert f"${abs(round(a - b, 2)):,.0f}/yr" in live, (
        f"the published gap must be method (a) minus method (b) (${a} - ${b}), the two "
        f"figures printed beside it -- got: {live}")

    # The threshold's OWN justification, checked rather than left in a comment.
    # _NETTING_MATERIALITY_PCT is set below the one limitation this artifact
    # already quantifies and this sentence implicitly ranks itself under:
    # floor_assumption_violations.usd_dropped_at_export_rate, the energy the
    # constant-floor split cannot account for and drops. Netting is "not where
    # a material error would be hiding" only while the netting gap stays under
    # the limitation the artifact does own up to -- a threshold above it would
    # licence the assurance at gaps where netting is the LARGER of the two.
    dropped_pct = 100.0 * (pricing["floor_assumption_violations"]
                           ["usd_dropped_at_export_rate"]) / b
    assert rt._NETTING_MATERIALITY_PCT < dropped_pct, (
        f"_NETTING_MATERIALITY_PCT is {rt._NETTING_MATERIALITY_PCT}%, at or above the "
        f"{dropped_pct:.1f}% this artifact's own floor_assumption_violations already "
        "drops -- so the assurance clause could render while the netting gap is the "
        "LARGER of the two known limitations, which is the claim it denies. Re-justify "
        "the constant against this artifact or lower it")

    def priced(a_usd, b_usd, gap_usd=None, gap_pct=None):
        """An artifact whose two totals are `a_usd` and `b_usd`. Its own
        reconciliation fields are DERIVED from those totals the way
        quiet_night_floor.py derives them, unless the caller names one -- which
        is how a half-regenerated artifact is built: new totals, the previous
        run's gap left in place."""
        def edit(doc):
            p = doc["pricing"]
            p["method_a_price_map"]["total_usd"] = a_usd
            p["method_b_rebill"]["total_usd"] = b_usd
            p["reconciliation"]["gap_usd"] = (
                round(a_usd - b_usd, 2) if gap_usd is None else gap_usd)
            p["reconciliation"]["gap_pct"] = (
                round(100.0 * round(a_usd - b_usd, 2) / b_usd, 2)
                if gap_pct is None else gap_pct)
        return edit

    # (2) The half-regenerated artifact, both ways round.
    refused = {}
    for field, stub in (
            # totals rewritten, the whole reconciliation block left behind
            ("gap_usd", priced(round(a * 1.4, 2), b,
                               gap_usd=rec["gap_usd"], gap_pct=rec["gap_pct"])),
            # gap_usd kept coherent, only the percentage stale
            ("gap_pct", priced(a, b, gap_pct=round(rec["gap_pct"] * 4, 2)))):
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", stub)):
            try:
                text = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
            except SystemExit as e:
                refused[field] = str(e)
                assert field in refused[field], (
                    f"the refusal must name the field that disagrees ({field}): {e}")
                assert "PHANTOM_METHOD_DISCREPANCY" in refused[field], e
            else:
                raise AssertionError(
                    f"an artifact whose reconciliation.{field} does not describe its own "
                    f"two totals was published anyway: {text}")

    # (3) Materially divergent totals: the divergence renders, the assurance
    #     does not. Signed the way the committed gap is signed.
    wide_b = round(a * 1.4, 2)
    wide_pct = 100.0 * (a - wide_b) / wide_b
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json",
                                         priced(a, wide_b))):
        wide = _renders("PHANTOM_METHOD_DISCREPANCY")
    for phrase in _NETTING_ASSURANCE:
        assert phrase not in wide, (
            f"two totals {abs(wide_pct):.1f}% apart -- past the "
            f"{rt._NETTING_MATERIALITY_PCT}% threshold -- were published with the "
            f"assurance {phrase!r} still attached: {wide}")
    for what, value in (("the price map total", f"${a:,.0f}/yr"),
                        ("the re-bill total", f"${wide_b:,.0f}/yr"),
                        ("how far apart they are", f"{abs(wide_pct):.1f}%")):
        assert value in wide, (
            f"a divergence must still state {what} ({value}) -- got: {wide}")
    assert "does not settle" in wide, (
        f"a gap past the threshold must say the report does not settle which pricing is "
        f"right: {wide}")
    for label, pattern in _MALFORMED_RENDER:
        assert not pattern.search(wide), f"the divergence rendered {label}: {wide}"

    # (4) Both sides of the threshold, both signs. b = 100a / (pct + 100).
    #     The edges are derived FROM _NETTING_MATERIALITY_PCT rather than
    #     written as 1.9/2.1, so this stays a test of the gate at whatever the
    #     threshold is set to; the committed value is pinned separately, by the
    #     assertion above that today's gap sits inside it.
    edges = {}
    margin = 0.1
    for pct in (rt._NETTING_MATERIALITY_PCT - margin, -(rt._NETTING_MATERIALITY_PCT - margin),
                rt._NETTING_MATERIALITY_PCT + margin, -(rt._NETTING_MATERIALITY_PCT + margin)):
        edge_b = round(100.0 * a / (pct + 100.0), 2)
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json",
                                             priced(a, edge_b))):
            edges[pct] = _renders("PHANTOM_METHOD_DISCREPANCY")
        inside = abs(pct) < rt._NETTING_MATERIALITY_PCT
        for phrase in _NETTING_ASSURANCE:
            assert (phrase in edges[pct]) is inside, (
                f"a {pct}% gap against a {rt._NETTING_MATERIALITY_PCT}% threshold "
                f"{'must' if inside else 'must not'} render {phrase!r}: {edges[pct]}")

    after = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return (f"the published gap is method (a) minus method (b) (${abs(round(a - b, 2)):,.0f}"
            f"/yr, {abs(rec['gap_pct'])}%), a reconciliation that does not describe its own "
            f"totals refuses by name in both fields ({', '.join(sorted(refused))}), and the "
            f"assurance clause renders on both sides of the {rt._NETTING_MATERIALITY_PCT}% "
            f"threshold only where it is earned (4 edges checked; at "
            f"{abs(wide_pct):.1f}% apart the divergence is published without it)")


@case
def case_sec9_teaser_agrees_with_the_artifacts_section_9_itself_cites():
    """Issue #130. SEC9_TEASER introduces section 9, so every figure in it
    must come from the same artifact section 9's own body uses -- otherwise
    a mechanical fill ships a teaser contradicting the section directly
    beneath it, which is exactly what happened: the teaser said 580 EV
    sessions (deep_results.json) while the body says 563
    (behavior_rebuild.json). The two committed detectors gate differently
    (flat kw > 6.5 with a 3 kWh drop, versus a rolling-percentile baseline
    with duration and peak-excess gates); which is closer to truth is not
    settled here.

    Pins BOTH halves against their real artifacts, so neither can drift:
    sessions must track behavior_rebuild (the detector every downstream
    dollar figure uses), and the always-on floor must track
    quiet_night_floor.json.

    THE FLOOR HALF WAS RE-BASED BY ISSUE #140. It used to pin
    deep_results.json's phantom figures, on the reasoning that its 3-5am
    method matched section 9's own framing, while sections 0 and 13 carried a
    third artifact's figures -- one load with three numbers across three
    sections. Only quiet_night_floor.json can carry a published figure:
    extra_results.json's phantom has no generator anywhere in this repo's
    history, and deep_results.json's carries the load's energy but no price
    at all -- it used to be priced at a hardcoded flat $0.20/kWh against an
    hour-weighted all-in import rate of about $0.375/kWh, and issue #172
    deleted that field rather than reprice it. So both halves are pinned to the artifact AND to
    the values the section's own NIGHT_FLOOR_* tokens render, which is what
    stops the split from reopening one section at a time.

    Deliberately NOT gated on _require_household(): SEC9_TEASER reads only
    committed public artifacts, so this case must run in CI too -- gating it
    would let a reversion merge green (Codex adversarial review, issue #130)."""
    teaser = rt.resolve_token("SEC9_TEASER")
    br = rt._json("behavior_rebuild.json")["detection"]
    dr = rt._json("deep_results.json")
    qnf = rt._json("quiet_night_floor.json")

    sessions = br["sessions"]
    assert f"{sessions} EV charging sessions" in teaser, (
        f"teaser must cite behavior_rebuild's {sessions} sessions, not another "
        f"detector's -- got: {teaser}")
    stale = dr["ev_sessions"]["count"]
    if stale != sessions:
        assert not re.search(rf"(?<!\d){stale} EV charging sessions", teaser), (
            f"teaser is citing deep_results' {stale} sessions, which contradicts "
            f"section 9's own body ({sessions})")

    # The assertion above only discriminates while the two artifacts happen to
    # disagree. Pin the DECLARED source too, so a regeneration that made
    # deep_results report 563 as well could not quietly restore the pre-fix
    # wiring (Codex /review, issue #130).
    declared = " ".join(rt.TOKENS["SEC9_TEASER"]["sources"])
    assert "behavior_rebuild.json" in declared, (
        f"SEC9_TEASER must declare behavior_rebuild.json as its session source -- got: {declared}")
    assert "quiet_night_floor.json" in declared, (
        "SEC9_TEASER must declare quiet_night_floor.json as its floor source -- got: "
        f"{declared}")

    cost = qnf["pricing"]["method_a_price_map"]["total_usd"]
    assert f"${cost:,.0f}/yr" in teaser, (
        f"floor cost must track quiet_night_floor's price map (${cost:,.0f}/yr), the only "
        f"pricing of this load with a committed generator -- got: {teaser}")
    # The energy figure is not re-derived here: it is asserted to be the SAME
    # STRING the section's own token renders, so the teaser cannot drift from
    # the body by taking its own reading of the same artifact.
    kwh = rt.resolve_token("NIGHT_FLOOR_ANNUAL_KWH")
    assert kwh in teaser, (
        f"floor energy must be NIGHT_FLOOR_ANNUAL_KWH's own value ({kwh!r}), which sections "
        f"0 and 13 also render -- got: {teaser}")

    # The two retired workpapers, checked absent by VALUE. Both describe the
    # same load, so a teaser that quietly went back to either one would still
    # read plausibly; only the number tells them apart.
    for who, doc in (("deep_results.json", dr["phantom"]),
                     ("extra_results.json", rt._json("extra_results.json")["phantom"])):
        for key in ("annual_kwh", "annual_kwh_at_median"):
            if key in doc and f"{doc[key]:,}" != kwh.split()[0]:
                assert f"{doc[key]:,}" not in teaser, (
                    f"teaser cites {who}'s {key} ({doc[key]:,}), a superseded workpaper "
                    f"(TECHNICAL.md 3.5/3.11) -- got: {teaser}")
    # Its cost figure cannot be checked absent by value any more, because issue
    # #172 removed it: deep_results.json:phantom priced the energy at a flat
    # 0.20 $/kWh against a $0.375/kWh hour-weighted all-in import rate, and the
    # field was deleted rather than repriced: quiet_night_floor.json prices its
    # OWN separately measured estimate of this physical load -- an independently
    # designed per-NIGHT rule, not deep_results' per-INTERVAL one -- so a reprice
    # here would put a third number on one load, not restate a priced one. The
    # guard is that it stays gone.
    assert not [k for k in dr["phantom"] if re.search(r"cost|usd|price|blend", k, re.I)], (
        "deep_results.json:phantom publishes a dollar figure again "
        f"({sorted(dr['phantom'])}), so this teaser has a superseded price to drift back to")
    return (f"SEC9_TEASER cites behavior_rebuild's {sessions} sessions (not "
            f"deep_results' {stale}) and quiet_night_floor's own floor figures "
            f"({kwh} at ${cost:,.0f}/yr), with both superseded workpapers' values absent")


# ---------------------------------------------------------------------------
# Issue #131: the nine <p class="verdict"> section conclusions are token-owned.
# index.html is the rendered artifact of those tokens, so the two must agree
# CHARACTER FOR CHARACTER -- that equality is the whole anti-drift guard. An
# editor who rewrites a verdict line in index.html without moving the token
# (or vice versa) fails here.
#
# Split into two cases on purpose. Tokens that read only committed artifacts
# must run in CI UNGATED, or a regression could merge green on a checkout with
# no private archive (the same reasoning the SEC9 case above records). Only
# the tokens that genuinely need private/household.yaml are gated, and which
# ones those are is read off each token's own declared `sources` rather than
# hardcoded, so adding a household-sourced figure to a verdict re-partitions
# these cases automatically.
# ---------------------------------------------------------------------------
_VERDICT_TOKEN_RE = re.compile(r"\{\{(S\d+_VERDICT)\}\}")


def _verdict_token_names():
    names = sorted(set(_VERDICT_TOKEN_RE.findall(rt.TEMPLATE.read_text())))
    assert len(names) >= 10, f"only {len(names)} section-verdict token slots in the template"
    return names


def _needs_household(name):
    return any("private/household.yaml" in s for s in rt.TOKENS[name].get("sources", []))


# household.py's own fail-closed text, matched to tell "this checkout has no
# private archive" apart from "this token cannot state its answer". Taken from
# the module rather than retyped, so it cannot drift out of sync with it.
# WITH NO private/household.yaml ON DISK AT ALL, any refusal that cites the
# file is caused by its absence, not by the household's EV applicability --
# household.py's own "missing ..." message for the tokens that read it
# directly, and each token's own named refusal for the fields it cannot find
# inside it (REVIEW_TOOL_1/2's provenance block, issue #135). Both cite the
# path, and neither says anything about an EV, so the path is the honest test.
# Checked against household.py's message rather than a retyped copy, so a
# reworded fail-closed cannot silently stop matching.
_INTAKE_PATH_IN_MESSAGE = "private/household.yaml"
assert _INTAKE_PATH_IN_MESSAGE in (rt.hh._MSG % "private/household.yaml"), (
    "household.py's fail-closed message no longer names the intake path, so a "
    "missing-archive refusal can no longer be told apart from a real one")


def _runnable_here(names):
    """`names`, minus the ones this checkout cannot resolve for want of the
    intake.

    A case that PATCHES ARTIFACTS still lets a token reach the real
    private/household.yaml, so on a checkout without the private archive it
    refuses for a reason the case is not about -- S0_VERDICT reads
    household.plan through _plan_ranking, and a no-EV artifact case would read
    that refusal as "a no-EV household gets no report", while a case expecting
    a NAMED refusal would see the wrong name. Both shapes shipped green here
    and went red in CI (issue #147).

    Gate on the source the token DECLARES rather than on a hand-kept list, so
    a token that starts or stops reading the intake needs no edit here. On a
    checkout WITH the archive -- this one -- nothing is dropped and every
    token is still exercised."""
    if rt.hh.PATH.is_file():
        return tuple(names)
    return tuple(n for n in names if not _needs_household(n))


_H2_ID_RE = re.compile(r'<h2 id="([^"]+)"')


def _verdict_section_id(name):
    """The section a verdict token OWNS. S7_VERDICT is section 7's conclusion
    and belongs under <h2 id="s7">; nowhere else in the document is the right
    place for it."""
    m = re.fullmatch(r"S(\d+)_VERDICT", name)
    assert m, f"{name} is not a section-verdict token name"
    return f"s{m.group(1)}"


def _index_section(index_html, section_id):
    """index.html from this section's own <h2 id="..."> to the next <h2 id=.

    Document order, not numeric order: the report puts the "What to do Monday"
    appendix (id="s15") between sections 7 and 8, and three audit sections
    carry their h2 inside a <summary>. Slicing on the h2s as they appear
    handles both without a table of where anything is."""
    heads = [(m.group(1), m.start()) for m in _H2_ID_RE.finditer(index_html)]
    ids = [i for i, _ in heads]
    assert section_id in ids, (
        f'index.html has no <h2 id="{section_id}"> for this verdict to sit under; it '
        f"has {ids}")
    at = ids.index(section_id)
    end = heads[at + 1][1] if at + 1 < len(heads) else len(index_html)
    return index_html[heads[at][1]:end]


def _assert_verdict_matches_index(name, index_html):
    """The published line must equal the token AS RENDERED -- i.e. after the
    same html.escape(value, quote=True) generate_report.py applies. Comparing
    the raw value instead would have been wrong in both directions: "&" is
    fine (it escapes to "&amp;", which is exactly what index.html carries),
    while "'" is not (it escapes to "&#x27;"). Escaping here reproduces the
    render contract rather than approximating it.

    AND IN THE SECTION THAT OWNS IT, exactly once in the document. This used
    to be a bare `in index_html`, which is a membership test over 800 lines of
    report: a verdict line moved into a neighbouring section, or duplicated
    into two, satisfied it just as well as one sitting where it belongs
    (issue #131 review round 6, finding 8). "Section 7's conclusion appears
    somewhere on the page" is not the claim this case is making."""
    value = rt.resolve_token(name)
    assert value.startswith("In one sentence: "), (
        f"{name} must open with the same stem the other section verdicts use, got: {value!r}")
    assert not (set("<>") & set(value)), (
        f"{name} contains markup characters; these tokens carry plain text only: {value!r}")
    rendered = _htmllib.escape(value, quote=True)
    line = f'<p class="verdict">{rendered}</p>'
    section_id = _verdict_section_id(name)
    section = _index_section(index_html, section_id)
    assert line in section, (
        f"{name} does not round-trip into the section it owns "
        f'(<h2 id="{section_id}">):\n  token    : {value!r}\n  rendered : {rendered!r}\n'
        + ("  (it IS published elsewhere in index.html, under a different heading)"
           if line in index_html else "  (it is not published anywhere in index.html)"))
    assert index_html.count(line) == 1, (
        f"{name}'s line is published {index_html.count(line)} times in index.html; a "
        "verdict is one section's conclusion and belongs in one place")
    return value


@case
def case_artifact_only_section_verdicts_match_index_html_verbatim():
    index_html = (rt.ROOT / "index.html").read_text()
    names = [n for n in _verdict_token_names() if not _needs_household(n)]
    assert names, "no artifact-only section-verdict token found to check ungated"
    for name in names:
        _assert_verdict_matches_index(name, index_html)
    return (f"{len(names)} artifact-only section verdicts ({', '.join(names)}) resolve and "
            "appear verbatim in index.html, checked without the private archive")


@case
def case_household_sourced_section_verdicts_match_index_html_verbatim():
    _require_household()
    index_html = (rt.ROOT / "index.html").read_text()
    names = [n for n in _verdict_token_names() if _needs_household(n)]
    assert names, "no household-sourced section-verdict token found"
    for name in names:
        _assert_verdict_matches_index(name, index_html)
    return (f"{len(names)} household-sourced section verdicts ({', '.join(names)}) resolve "
            "and appear verbatim in index.html")


@case
def case_every_section_verdict_agrees_with_index_html_without_the_private_archive():
    """The gate above skips on a checkout with no private/household.yaml, and
    .github/workflows/tests.yml runs this suite on exactly that -- so every
    verdict _needs_household() routed into it (S0, S2, S3, S10) had NO
    token<->index.html agreement check on the runner that guards merges. Those
    four published lines could ship blank, reworded, or contradicting their
    tokens and CI would stay green, which is the failure _stub_household was
    written to prevent.

    So every verdict slot in the template is checked here, ungated, with the
    household answers substituted. Where the archive is staged the stubs are
    the household's own answers and nothing about the check changes.

    Nine of the ten round-trip EXACTLY, because their household inputs are
    recoverable from committed artifacts: the plan and generation provider off
    data/plan_results.csv, the two provider names off
    data/cca_bundled_counterfactual.json's own question line.

    S2_VERDICT cannot, and this is the one place a stand-in changes what is
    checked. Its sentence prints the array's age and nameplate and computes
    its specific yield from the nameplate; no committed artifact in this repo
    carries either, and writing the household's own answers into this
    committed file is what CLAUDE.md section 4 forbids. So it is checked by
    skeleton: the rendered sentence is split at those three figures and every
    literal fragment between them -- the whole wording, the production total,
    the export share, the tariff window, the EV clause -- must appear in
    order inside the published line, which still catches a blanked or
    reworded line. Only the two household figures go unchecked here; the
    gated case above checks those against the real archive."""
    index_html = (rt.ROOT / "index.html").read_text()
    exact, skeleton = [], []
    for name in _verdict_token_names():
        if not _needs_household(name):
            _assert_verdict_matches_index(name, index_html)
            exact.append(name)
        elif name == "S2_VERDICT":
            stubs = _s2_household_inputs()
            with _stub_household(stubs):
                value = rt.resolve_token(name)
            _assert_verdict_skeleton_matches_index(
                name, index_html, value, _s2_variable_figures(stubs))
            skeleton.append(name)
        else:
            with _verdict_stub(name):
                _assert_verdict_matches_index(name, index_html)
            exact.append(name)
    assert sorted(exact + skeleton) == _verdict_token_names(), (
        f"only {sorted(exact + skeleton)} of {_verdict_token_names()} were checked")
    assert skeleton == ["S2_VERDICT"], (
        f"a verdict other than S2_VERDICT dropped to a skeleton check: {skeleton}")
    return (f"all {len(exact) + len(skeleton)} section verdicts agree with index.html "
            f"without the private archive -- {len(exact)} verbatim, S2_VERDICT by "
            "skeleton (its age and nameplate figures have no committed source)")


@case
def case_section_verdict_guards_refuse_to_publish_a_false_claim():
    """S1's sentence makes a claim that would be FALSE if the artifact moved,
    so its formula fails closed instead of rendering it (CLAUDE.md section 0).
    Proven by driving the guard, not by reading it: S1 claims every billed TOU
    bucket rebuilds, so a failing bucket must raise SystemExit naming the token.

    S3's plan claim is checked here too, but the correct behaviour there is to
    INVERT, not to refuse -- a household on a plan that no longer wins is the
    household section 3 is written for. So the second half asserts the
    sentence turns around and names the plan that actually won; the full
    branch matrix is
    case_plan_verdicts_invert_rather_than_refusing_when_the_household_plan_loses.
    """
    audit = rt._json("tou_audit_summary.json")
    real_failing = audit["rules"]["as_billed"]["buckets_failing"]
    assert real_failing == 0, (
        f"data/tou_audit_summary.json now reports {real_failing} failing bucket(s) -- "
        "S1_VERDICT's claim is no longer true and the token should already be failing")
    audit["rules"]["as_billed"]["buckets_failing"] = 1
    try:
        rt.resolve_token("S1_VERDICT")
        raise AssertionError("S1_VERDICT rendered its all-buckets-rebuild claim while "
                             "the audit artifact reported a failing bucket")
    except SystemExit as e:
        assert "S1_VERDICT" in str(e), e
    finally:
        audit["rules"]["as_billed"]["buckets_failing"] = real_failing

    if not rt.hh.PATH.is_file():
        return ("S1_VERDICT refuses to publish its claim when a billed bucket fails "
                "(S3_VERDICT's plan guard needs private/household.yaml, not checked here)")
    rows = rt._csv_rows("plan_results.csv")
    plan = rt.hh1("household.plan")
    victim = next(r for r in rows if r["plan"] != plan)
    original = victim["total"]
    victim["total"] = "0.01"
    try:
        value = rt.resolve_token("S3_VERDICT")
        assert "is not the cheapest plan" in value and victim["plan"] in value, (
            f"S3_VERDICT still claims the household plan is cheapest while "
            f"{victim['plan']} prices lower: {value}")
    finally:
        victim["total"] = original
    return ("S1_VERDICT raises SystemExit naming itself rather than publishing a claim "
            "its artifact no longer supports, and S3_VERDICT inverts to name the plan "
            "that actually priced lower")


# ---------------------------------------------------------------------------
# Issue #131 follow-up: the verdict formulas that DIVIDE or COMPARE artifact
# values. A payback is a quotient, so a package that saves nothing (or costs
# more than it saves) yields a zero, infinite or NEGATIVE "payback" -- and a
# negative one sorts BELOW every real payback, so comparing it publishes the
# opposite purchase advice while every number in the sentence stays correct.
#
# Each case drives the formula with substituted artifact values rather than
# asserting on the code: the substitution goes into report_tokens' in-memory
# cache of a committed data/ file (the same technique the S1/S3 guard case
# above uses) and is restored on the way out, so nothing under data/ is
# touched. All of these read committed artifacts only -- no _require_household
# gate, so they run in CI on a checkout with no private archive.
# ---------------------------------------------------------------------------
class _swapped:
    """Temporarily replace node[key] in a cached artifact, restoring it."""

    def __init__(self, node, key, value):
        self.node, self.key, self.value = node, key, value

    def __enter__(self):
        self.old = self.node[self.key]
        self.node[self.key] = self.value
        return self.value

    def __exit__(self, *exc):
        self.node[self.key] = self.old


def _mid_battery_swaps(mid, pre, post):
    """The swaps that put packages.MID's battery on a substituted pair of
    battery-alone savings, WITH the paybacks package_results.py derives from
    them.

    analysis/package_results.py writes each payback as
    `round(packages.MID.cost / saving, 1)`, so a case that moves a saving and
    leaves its payback behind is not describing a household -- it is
    describing an artifact no run of the generator could produce, and
    report_tokens now (correctly) refuses that as an artifact contradicting
    itself. Cases that want to drive a HOUSEHOLD build the pair here; cases
    that want to drive the contradiction break it deliberately and say so."""
    cost = mid["cost"]
    return [_swapped(mid, "battery_alone_yr", pre),
            _swapped(mid, "battery_alone_post_ev_fix_yr", post),
            _swapped(mid, "battery_alone_payback_yr",
                     round(cost / pre, 1) if pre else float("inf")),
            _swapped(mid, "battery_alone_payback_post_fix_yr",
                     round(cost / post, 1) if post else float("inf"))]


@contextlib.contextmanager
def _mid_battery_at(mid, pre, post):
    """_mid_battery_swaps as one context manager."""
    with contextlib.ExitStack() as stack:
        for swap in _mid_battery_swaps(mid, pre, post):
            stack.enter_context(swap)
        yield


class _stub_household:
    """Substitute household.yaml answers BY PATH (and, optionally, the
    generation-provider acronym report_tokens derives from household.cca),
    restoring both on the way out. Paths not listed fall through to the real
    accessor.

    Same contract as _swapped, one level up: nothing on disk is touched and
    the substitution is in-memory only. It exists so the cases driving
    household-sourced verdicts run UNGATED. A case gated behind
    _require_household() SKIPS on a runner with no private archive, and
    .github/workflows/tests.yml runs this suite on exactly that -- so a gated
    guard case cannot stop a regression merging green, which is the failure
    the SEC9 and verdict round-trip cases above already record. Worse for the
    fail-closed cases specifically: without the archive, resolve_token's own
    "missing private/household.yaml" SystemExit ALSO names the token, so an
    `assert token in str(e)` case would have passed for entirely the wrong
    reason. Where the archive IS staged the callers feed it the household's
    real answers, so the stub changes nothing about what is exercised.
    """

    def __init__(self, values, provider=None):
        self.values, self.provider = values, provider

    def __enter__(self):
        self.old_hh1 = rt.hh1
        self.old_provider = rt._generation_provider_short
        rt.hh1 = lambda path: (self.values[path] if path in self.values
                               else self.old_hh1(path))
        if self.provider is not None:
            rt._generation_provider_short = lambda ctx: self.provider
        return self

    def __exit__(self, *exc):
        rt.hh1 = self.old_hh1
        rt._generation_provider_short = self.old_provider


def _stub_plan(plan, provider):
    """_stub_household narrowed to the two answers the plan-ranking guard
    reads (household.plan, and the provider acronym taken off household.cca)."""
    return _stub_household({"household.plan": plan}, provider=provider)


def _s2_household_inputs():
    """The two household answers S2_VERDICT reads: the household's own when
    the private archive is staged, clearly synthetic stand-ins otherwise.

    Never the real figures as literals -- this file is committed, and
    CLAUDE.md section 4 keeps household answers out of committed artifacts.
    The case does not need them: every assertion it makes is about the
    sentence's WORDING and about the committed artifacts' own production and
    export totals, neither of which depends on the array's size or its PTO
    date."""
    if rt.hh.PATH.is_file():
        return {"solar.kw_dc": rt.hh1("solar.kw_dc"),
                "household.pto_date": rt.hh1("household.pto_date")}
    return {"solar.kw_dc": 5.0, "household.pto_date": dt.date(2019, 1, 1)}


def _plan_ranking_inputs():
    """(provider, cheapest_plan, priced_rows) for the plan-guard cases.

    The provider is the household's own when the private archive is staged and
    the committed CSV's first provider column otherwise, so these cases assert
    the same thing in both places rather than skipping in one of them."""
    rows = rt._csv_rows("plan_results.csv")
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file()
                else rows[0]["provider"])
    priced = [r for r in rows if r["provider"] == provider]
    assert len(priced) > 1, (
        f"data/plan_results.csv prices only {len(priced)} plan(s) for {provider!r}; "
        "there is no runner-up to rank against")
    cheapest = min(priced, key=lambda r: float(r["total"]))
    return provider, cheapest["plan"], priced


@contextlib.contextmanager
def _plan_repriced(provider, prices):
    """data/plan_results.csv's total column, repriced {plan: total} for ONE
    generation provider's rows and restored on the way out.

    Module-level because three cases now need it -- the below-zero sweep, the
    plan-chrome states and the second-in-both-rankings report -- and a copy
    nested in one of them is a copy the others reprice slightly differently.
    Nothing on disk is touched: the substitution is in report_tokens' own
    parsed rows, the same contract _swapped keeps."""
    rows = rt._csv_rows("plan_results.csv")
    original = [r["total"] for r in rows]
    try:
        for row in rows:
            if row["provider"] == provider and row["plan"] in prices:
                row["total"] = f"{float(prices[row['plan']]):.2f}"
        yield
    finally:
        for row, was in zip(rows, original):
            row["total"] = was


# CLAUDE.md section 10's density cap applies to EVERY branch, but
# test_report_consistency.py can only see the one branch index.html happens to
# carry. Same lead-sentence rule, applied here to the branches that do not
# render today: a period glued to digits (6.5-yr) is not a sentence break.
_LEAD_BREAK_RE = re.compile(r"(?<!\d)\.(?=\s|$)")


def _assert_within_density_cap(name, value, note):
    m = _LEAD_BREAK_RE.search(value)
    lead = value[:m.end()] if m else value
    words = len(lead.split())
    asides = lead.count("(") + lead.count("—")
    assert words <= 35 and asides <= 1, (
        f"{name} on {note} leads in {words} words / {asides} asides, over CLAUDE.md "
        f"section 10's 35-word, 1-aside cap: {lead}")
    return words


@case
def case_s7_verdict_never_credits_a_payback_to_an_expansion_that_saves_nothing():
    """packages.HIGH.marginal_vs_mid_yr is the expansion's OWN annual saving.
    At or below zero the expansion can never pay back, and the clause must say
    so; dividing the extra hardware cost by it instead returns a negative
    number that compares as "faster than the first unit"."""
    pk = rt._json("package_results.json")["packages"]
    real = pk["HIGH"]["marginal_vs_mid_yr"]
    assert real > 0, (
        f"data/package_results.json now puts the expansion's marginal saving at "
        f"{real}/yr; the published branch below is no longer the live one")
    published = rt.resolve_token("S7_VERDICT")
    assert "saves too little to match that" in published, published
    widths = {"published (marginal +%d)" % real:
              _assert_within_density_cap("S7_VERDICT", published, "the published branch")}

    for label, marginal in (("negative (-400)", -400), ("zero", 0)):
        with _swapped(pk["HIGH"], "marginal_vs_mid_yr", marginal):
            value = rt.resolve_token("S7_VERDICT")
        assert "pays back faster" not in value, (
            f"S7_VERDICT tells the reader the expansion pack pays back faster than the "
            f"first unit while its own marginal saving is {marginal}/yr: {value}")
        assert "never repays its extra cost" in value, (
            f"S7_VERDICT must say plainly that an expansion saving {marginal}/yr does "
            f"not pay back: {value}")
        widths[label] = _assert_within_density_cap("S7_VERDICT", value, label)

    # The positive side must still discriminate BOTH ways, or "never claims a
    # faster payback" would pass trivially on a formula that never says it.
    exp_cost = pk["HIGH"]["cost"] - pk["MID"]["cost"]
    quick = exp_cost / (pk["MID"]["battery_alone_payback_post_fix_yr"] / 2)
    with _swapped(pk["HIGH"], "marginal_vs_mid_yr", quick):
        value = rt.resolve_token("S7_VERDICT")
    assert "pays back faster than that" in value, (
        f"S7_VERDICT withheld the faster-payback reading from an expansion that really "
        f"does pay back in half the time: {value}")
    widths["faster (positive)"] = _assert_within_density_cap(
        "S7_VERDICT", value, "the faster-payback branch")

    # An EXACT tie is its own branch: two branches would have sent it to
    # "pays back faster than that", which is false when the two paybacks are
    # the same number. The tie is constructed, not hoped for -- the payback is
    # swapped to the very quotient the formula computes (exp_cost / marginal,
    # the identical IEEE division), so equality holds on any platform rather
    # than depending on 5900/6.5 round-tripping.
    #
    # The first unit's saving moves with its payback, because the two are one
    # quantity in two forms (payback = cost / saving) and report_tokens checks
    # that derivation: swapping the payback alone builds an artifact no run of
    # package_results.py could write, and the refusal that produces is a
    # DIFFERENT finding's, not this one's.
    tie_marginal = 1000.0
    tie_payback = exp_cost / tie_marginal
    tie_saving = pk["MID"]["cost"] / tie_payback
    with _swapped(pk["HIGH"], "marginal_vs_mid_yr", tie_marginal), \
            _swapped(pk["MID"], "battery_alone_post_ev_fix_yr", tie_saving), \
            _swapped(pk["MID"], "battery_alone_payback_post_fix_yr", tie_payback):
        value = rt.resolve_token("S7_VERDICT")
    assert "faster" not in value, (
        f"S7_VERDICT calls an expansion repaying at exactly the first unit's rate "
        f"({tie_payback:.1f} yr both) the faster buy: {value}")
    assert "pays back at the same rate" in value, (
        f"S7_VERDICT must call an exact tie a tie: {value}")
    widths["tie (equal paybacks)"] = _assert_within_density_cap(
        "S7_VERDICT", value, "the tie branch")

    assert rt.resolve_token("S7_VERDICT") == published, (
        "the substituted marginal saving leaked out of this case")
    return ("S7_VERDICT reads slower / faster / tie / never-repays across marginal "
            f"+{real}, +{quick:.0f}, +{tie_marginal:.0f}, 0 and -400 per year, each inside "
            f"the density cap ({', '.join(f'{k} {v}w' for k, v in widths.items())})")


# Wordings that tell the reader the expansion pack saves NOTHING. The
# slow-payback branch renders while packages.HIGH.marginal_vs_mid_yr is
# POSITIVE, so any of these there states the artifact's own sign backwards.
_ABSENT_SAVING_RE = re.compile(
    r"not savings|no savings|nothing|never repays|saves? no\b|zero saving", re.I)


@case
def case_s7_verdict_never_calls_a_positive_marginal_saving_an_absence_of_savings():
    """packages.HIGH.marginal_vs_mid_yr > 0 means the expansion pack DOES save
    money -- $216/yr on the committed artifact, at a marginal payback slower
    than the first unit's. Saying it "buys endurance, not savings" there
    states the opposite of the artifact the sentence is derived from, and
    hands a reader deciding what to buy the wrong fact: "saves too little to
    be worth the money" and "saves no money at all" are different purchase
    advice, and only the second is a reason to rule the pack out on its own
    terms. Both positive branches are checked, so the wording cannot drift
    into an absence claim on either side of the payback comparison.

    The marginal <= 0 branch is checked to STILL read as an absence, which is
    what keeps the two claims distinguishable -- and keeps this case from
    passing on a formula that simply never mentions savings at all."""
    pk = rt._json("package_results.json")["packages"]
    exp_cost = pk["HIGH"]["cost"] - pk["MID"]["cost"]
    mid_payback = pk["MID"]["battery_alone_payback_post_fix_yr"]
    real = pk["HIGH"]["marginal_vs_mid_yr"]
    assert real > 0 and exp_cost / mid_payback > real, (
        f"data/package_results.json no longer puts the expansion on a positive, "
        f"slower-than-{mid_payback}-yr marginal saving ({real}/yr on ${exp_cost:,.0f}); "
        f"the published branch is no longer the one this case guards")

    slow, quick = exp_cost / (mid_payback * 4), exp_cost / (mid_payback / 2)
    checked = {}
    for label, marginal in (("published (+%d)" % real, real),
                            ("slower (+%.0f)" % slow, slow),
                            ("faster (+%.0f)" % quick, quick)):
        with _swapped(pk["HIGH"], "marginal_vs_mid_yr", marginal):
            value = rt.resolve_token("S7_VERDICT")
        hit = _ABSENT_SAVING_RE.search(value)
        assert not hit, (
            f"S7_VERDICT describes an expansion pack saving {marginal:.0f}/yr as an "
            f"absence of savings ({hit.group(0)!r}) -- data/package_results.json:"
            f"packages.HIGH.marginal_vs_mid_yr says it saves that money: {value}")
        checked[label] = value

    with _swapped(pk["HIGH"], "marginal_vs_mid_yr", 0):
        none_at_all = rt.resolve_token("S7_VERDICT")
    assert _ABSENT_SAVING_RE.search(none_at_all), (
        f"S7_VERDICT stopped saying plainly that an expansion saving nothing never "
        f"repays, so this case can no longer tell the two claims apart: {none_at_all}")
    assert all(v != none_at_all for v in checked.values()), (
        "S7_VERDICT gives a positive marginal saving the same clause it gives a zero one")
    return ("S7_VERDICT never words a positive marginal saving as an absence of savings "
            f"(+{real}, +{slow:.0f}, +{quick:.0f}/yr), while the zero case still says the "
            "expansion never repays")


def _s10_household_inputs():
    """The two household answers S10_VERDICT reads: the household's own when
    the private archive is staged, and otherwise the two provider names taken
    off data/cca_bundled_counterfactual.json -- the very artifact the sentence
    is derived from, whose own question line names both in committed,
    de-identified text.

    Same contract as _s2_household_inputs -- never the real answers as
    literals in a committed file (CLAUDE.md section 4), and the stub exists so
    the guard runs UNGATED on the archive-less runner CI uses rather than
    skipping there. Sourcing the stand-ins from the artifact rather than
    inventing them is what lets the round-trip against index.html be exact in
    both places: a synthetic provider name renders a sentence no published
    line could ever match."""
    if rt.hh.PATH.is_file():
        return {}
    utility, cca = _cca_provider_names()
    return {"household.utility": utility, "household.cca": cca}


def _cca_provider_names():
    """(utility, cca) off data/cca_bundled_counterfactual.json's question."""
    question = rt._json("cca_bundled_counterfactual.json")["question"]
    m = re.search(r"bundled (.+?) generation to the CCA \((.+?)\)", question)
    assert m, (
        "data/cca_bundled_counterfactual.json's question no longer names both "
        f"generation providers: {question!r}")
    return m.group(1), m.group(2)


def _verdict_stub(name):
    """The household answers one verdict reads, substituted so its round-trip
    against index.html runs on a checkout with no private archive.

    Where the archive IS staged every stub resolves to the household's own
    answer, so the substitution changes nothing about what is exercised."""
    if name in ("S0_VERDICT", "S3_VERDICT"):
        provider, cheapest, _priced = _plan_ranking_inputs()
        # Without the archive, stand in with the plan the committed CSV ranks
        # cheapest -- the branch index.html publishes today. If this household
        # ever moves onto a plan that loses, the published line becomes the
        # inverted one and this stub stops reproducing it, which fails loudly
        # here rather than passing on the wrong branch.
        plan = rt.hh1("household.plan") if rt.hh.PATH.is_file() else cheapest
        return _stub_plan(plan, provider)
    if name == "S10_VERDICT":
        return _stub_household(_s10_household_inputs())
    if name == "S2_VERDICT":
        return _stub_household(_s2_household_inputs())
    raise AssertionError(
        f"{name} declares private/household.yaml sources but has no stub, so it has no "
        "index.html agreement check on a runner without the private archive")


def _s2_variable_figures(stubs):
    """The three figures in S2_VERDICT that come off private/household.yaml:
    the array's age, its nameplate, and the specific yield divided out of it."""
    kw_dc = stubs["solar.kw_dc"]
    pto = rt._as_date(stubs["household.pto_date"])
    _start, end = rt._analysis_window_dates()
    age = end.year - pto.year - ((end.month, end.day) < (pto.month, pto.day))
    production = rt._annual_production_kwh(rt.CTX)
    return [f"{age}", f"{kw_dc:,.2f}", f"{production / kw_dc:,.0f}"]


def _assert_verdict_skeleton_matches_index(name, index_html, value, variables):
    """index.html agreement for a verdict carrying figures that have no
    committed source: every literal fragment BETWEEN those figures must appear,
    in order, inside the published <p class="verdict"> line, which must also
    end where the sentence does.

    Scoped to the section that owns the verdict, and required to be the only
    verdict line in it -- finding 8's other half. Searching every
    <p class="verdict"> in the document for one that happens to start the
    right way accepted a line that had moved sections, and would have picked
    an arbitrary one of two duplicates."""
    fragments, rest = [], value
    for var in variables:
        head, sep, rest = rest.partition(var)
        assert sep, (
            f"{name}'s rendering does not contain the substituted figure {var!r}, so "
            f"this case cannot tell its household figures from its wording: {value!r}")
        fragments.append(head)
    fragments.append(rest)
    assert sum(len(f) for f in fragments) > len(value) / 2, (
        f"{name} is mostly household figures ({fragments!r}); a skeleton check would "
        "assert almost nothing")

    section_id = _verdict_section_id(name)
    section = _index_section(index_html, section_id)
    in_section = re.findall(r'<p class="verdict">(.*?)</p>', section, re.S)
    assert len(in_section) == 1, (
        f'<h2 id="{section_id}"> carries {len(in_section)} verdict lines, so {name} '
        "cannot be matched to the one it owns")
    published = [p for p in in_section
                 if p.startswith(_htmllib.escape(fragments[0], quote=True))]
    assert published, (
        f"{name}'s own section publishes a verdict that does not open with "
        f"{fragments[0]!r}: {in_section[0]!r}")
    assert index_html.count(f'<p class="verdict">{in_section[0]}</p>') == 1, (
        f"{name}'s published line appears more than once in index.html")
    line, pos = published[0], 0
    for fragment in fragments:
        escaped = _htmllib.escape(fragment, quote=True)
        found = line.find(escaped, pos)
        assert found >= 0, (
            f"{name} does not round-trip into index.html: the published line is missing "
            f"{escaped!r} after character {pos}\n  token: {value!r}\n  index: {line!r}")
        pos = found + len(escaped)
    assert pos == len(line), (
        f"{name}'s published line carries {line[pos:]!r} past the end of the token's "
        f"own sentence")
    return line


@case
def case_s10_verdict_calls_an_exact_tie_a_tie_rather_than_a_cheaper_cca():
    """This clause reports a DIRECTION, and at delta_usd_per_year == 0 there
    is no direction to report. A two-way ternary still has to put that case
    somewhere, and it put it in "less than": "$0/yr less than bundled
    generation" tells a reader the CCA came out cheaper while the two priced
    out exactly the same. Same defect shape as the section 6 tie.

    Both signed branches are checked too, so the tie branch cannot be bought
    by a formula that stopped reporting a direction at all."""
    a = rt._json("cca_bundled_counterfactual.json")["direction_a_cca_repriced_at_bundled"]
    real = a["delta_usd_per_year"]
    assert real != 0, (
        "data/cca_bundled_counterfactual.json now prices the two identically; the "
        "published branch is the tie one and this case's premise no longer holds")

    with _stub_household(_s10_household_inputs()):
        published = rt.resolve_token("S10_VERDICT")
        for label, delta, expected in (("positive", abs(real), "more than"),
                                       ("negative", -abs(real), "less than")):
            with _swapped(a, "delta_usd_per_year", delta):
                value = rt.resolve_token("S10_VERDICT")
            assert expected in value, (
                f"S10_VERDICT dropped the {label} direction at a delta of {delta}: {value}")
        with _swapped(a, "delta_usd_per_year", 0.0):
            tie = rt.resolve_token("S10_VERDICT")

    for wrong in ("more than", "less than"):
        assert wrong not in tie, (
            f"S10_VERDICT reports a direction at an exact tie ({wrong!r}), telling the "
            f"reader one option won while the two cost the same: {tie}")
    assert "the same as bundled" in tie, (
        f"S10_VERDICT must say plainly that a zero delta means the two cost the same: {tie}")
    assert "$0/yr" not in tie, (
        f"S10_VERDICT quotes a $0/yr gap as if it were a priced difference: {tie}")
    # Section 10 is advanced-tier, so CLAUDE.md section 10's 35-word cap is
    # formally exempt here -- but the sentence is long already, so the tie
    # branch may not lengthen it.
    assert len(tie.split()) <= len(published.split()), (
        f"the tie branch is longer than the published one "
        f"({len(tie.split())} vs {len(published.split())} words): {tie}")
    return (f"S10_VERDICT calls a zero delta the same cost instead of ${0:,.0f}/yr less "
            f"(live delta ${real:,.2f}/yr), and still reports both directions when there "
            f"is one ({len(tie.split())} words, same as the published branch)")


@case
def case_s10_verdict_sizes_the_unpriced_effect_rather_than_refusing_to_render():
    """Section 10's closing caveat calls the excluded net-export effect
    "materially larger" than the priced delta it quotes. That adjective is a
    comparison, and it used to abort the whole report when the comparison went
    the other way -- withholding the section from the household with the
    BETTER-evidenced answer, since a small unpriced side is what makes the
    priced one worth reading.

    Only the adjective turns on the comparison. The caveat itself rests on the
    artifact's own excluded_net_export_note, which says the priced delta is
    not the full answer whatever the excluded side weighs, so it stands in
    every branch. All three sizes are driven; section 10 is advanced-tier and
    formally exempt from the density cap, so the check is that no branch runs
    longer than the published one."""
    a = rt._json("cca_bundled_counterfactual.json")["direction_a_cca_repriced_at_bundled"]
    unpriced = abs(a["excluded_net_export_cca_credit_usd"])
    priced = abs(a["delta_usd"])
    assert unpriced > priced, (
        f"data/cca_bundled_counterfactual.json already puts the unpriced effect "
        f"(${unpriced:,.2f}) at or below the priced delta (${priced:,.2f}); the published "
        "branch is no longer the live one")
    with _stub_household(_s10_household_inputs()):
        published = rt.resolve_token("S10_VERDICT")
        assert "a materially larger, unpriced net-export effect" in published, published
        lengths = {"published": len(published.split())}
        for label, value, expected in (
                ("equal", priced, "an equally large, unpriced net-export effect"),
                ("smaller", priced / 2, "a smaller, unpriced net-export effect")):
            with _swapped(a, "excluded_net_export_cca_credit_usd", -value):
                rendered = rt.resolve_token("S10_VERDICT")
            assert expected in rendered, (
                f"S10_VERDICT does not size the unpriced effect correctly when it is "
                f"{label} to the ${priced:,.2f} priced delta: {rendered}")
            assert "materially larger" not in rendered, (
                f"S10_VERDICT still calls the unpriced effect materially larger at "
                f"${value:,.2f}: {rendered}")
            assert "not fully settled" in rendered, (
                f"S10_VERDICT dropped the caveat its artifact's own "
                f"excluded_net_export_note supports at every size: {rendered}")
            lengths[label] = len(rendered.split())
            assert lengths[label] <= lengths["published"], (
                f"the {label} branch runs longer than the published one: {rendered}")
        assert rt.resolve_token("S10_VERDICT") == published, (
            "the substituted excluded credit leaked out of this case")
    return ("S10_VERDICT resizes its unpriced-effect adjective instead of aborting when "
            f"the excluded net-export credit stops dominating (live ${unpriced:,.2f} vs "
            f"${priced:,.2f}; " + ", ".join(f"{k} {v}w" for k, v in lengths.items()) + ")")


@case
def case_s7_verdict_refuses_a_battery_payback_its_own_artifact_contradicts():
    """The two refusals section 7 keeps, both of them artifacts contradicting
    themselves rather than households whose answer differs.

    1. A payback that is zero, negative or infinite WHILE the same package's
       battery_alone_post_ev_fix_yr is positive. The payback is that saving
       divided into the cost, so a positive saving and a non-quotable
       quotient cannot both be right; neither the published clause nor the
       never-repays one can be written on top of the contradiction. (A
       non-positive SAVING is the household case, and inverts -- see
       case_battery_verdicts_say_plainly_when_the_battery_never_repays.)
    2. A HIGH package costing no more than MID. HIGH is MID plus the
       expansion pack by construction -- that is what BATTERY_EXPANDED_MODEL
       and BATTERY_EXPANDED_COST name -- so a non-positive difference means
       the artifact no longer prices the thing the clause is about. There is
       no expansion cost to repay and no figure to quote for one."""
    pk = rt._json("package_results.json")["packages"]
    assert pk["MID"]["battery_alone_post_ev_fix_yr"] > 0, (
        "this case drives the positive-saving contradiction; the live saving is not positive")
    for bad in (0, -6.5, float("inf")):
        with _swapped(pk["MID"], "battery_alone_payback_post_fix_yr", bad):
            try:
                value = rt.resolve_token("S7_VERDICT")
                raise AssertionError(f"S7_VERDICT quoted a {bad}-year battery payback "
                                     f"as a sound purchase figure: {value}")
            except SystemExit as e:
                assert "S7_VERDICT" in str(e), e
    with _swapped(pk["HIGH"], "cost", pk["MID"]["cost"]):
        try:
            value = rt.resolve_token("S7_VERDICT")
            raise AssertionError(f"S7_VERDICT called HIGH an expansion pack while it "
                                 f"costs no more than MID: {value}")
        except SystemExit as e:
            assert "S7_VERDICT" in str(e), e
    return ("S7_VERDICT fails closed when a positive battery-alone saving is paired with "
            "a zero, negative or infinite payback, and when the HIGH package carries no "
            "extra cost for an expansion to repay")


@case
def case_battery_verdicts_say_plainly_when_the_battery_never_repays():
    """Sections 0 and 7 both sell the battery off packages.MID, and both used
    to raise SystemExit when its own savings stopped repaying it -- section 0
    on the payback RANGE it publishes, section 7 on the single payback it
    quotes. A household whose battery never pays back is as legitimate a
    reproducer as one on the wrong tariff, and it is the household most in
    need of being told; refusing there withheld all fifteen sections to avoid
    printing one clause the other way.

    The substitution moves the saving AND its payback together, the way a real
    run of package_results.py would: a battery saving nothing has an infinite
    payback, one losing money a negative one. Section 7's expansion tail is
    checked too, because its three comparative branches all measure against a
    first-unit payback that no longer exists -- it has to state the
    expansion's own instead of comparing to nothing."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    pk = rt._json("package_results.json")["packages"]
    mid = pk["MID"]
    live = (mid["battery_alone_yr"], mid["battery_alone_post_ev_fix_yr"])
    assert min(live) > 0, (
        f"data/package_results.json already puts a battery-alone saving at {min(live)}/yr; "
        "the published branch is no longer the live one")
    exp_cost = pk["HIGH"]["cost"] - mid["cost"]
    widths = {}
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in ("S0_VERDICT", "S7_VERDICT")}
        assert "sound optional buy" in published["S0_VERDICT"], published["S0_VERDICT"]
        assert "adds its own" in published["S7_VERDICT"], published["S7_VERDICT"]

        for label, saving, payback in (("saves nothing", 0, float("inf")),
                                       ("loses money", -400, -36.25)):
            with _swapped(mid, "battery_alone_yr", saving), \
                    _swapped(mid, "battery_alone_post_ev_fix_yr", saving), \
                    _swapped(mid, "battery_alone_payback_yr", payback), \
                    _swapped(mid, "battery_alone_payback_post_fix_yr", payback):
                s0 = rt.resolve_token("S0_VERDICT")
                s7 = rt.resolve_token("S7_VERDICT")
            assert "does not repay its own cost" in s0, (
                f"S0_VERDICT still calls a battery that {label} a sound buy: {s0}")
            assert "sound optional buy" not in s0 and "payback" not in s0, s0
            assert "never repays its own cost" in s7, (
                f"S7_VERDICT still quotes a payback for a battery that {label}: {s7}")
            assert "adds its own" not in s7, s7
            # The expansion tail loses its comparator, so it must stop
            # comparing rather than measure against a payback that is gone.
            marginal = pk["HIGH"]["marginal_vs_mid_yr"]
            assert marginal > 0, "this arm needs a positive expansion saving"
            assert f"repays its extra cost in {exp_cost / marginal:.1f} years" in s7, (
                f"S7_VERDICT's expansion tail still measures against a first-unit "
                f"payback that no longer exists: {s7}")
            for dangling in ("match that", "faster than that", "at the same rate"):
                assert dangling not in s7, (
                    f"S7_VERDICT compares the expansion to a payback it no longer "
                    f"quotes ({dangling!r}): {s7}")
            widths[f"S0 {label}"] = _assert_within_density_cap("S0_VERDICT", s0, label)
            widths[f"S7 {label}"] = _assert_within_density_cap("S7_VERDICT", s7, label)

            # And an expansion that also never repays still reads as absent.
            with _swapped(mid, "battery_alone_yr", saving), \
                    _swapped(mid, "battery_alone_post_ev_fix_yr", saving), \
                    _swapped(mid, "battery_alone_payback_yr", payback), \
                    _swapped(mid, "battery_alone_payback_post_fix_yr", payback), \
                    _swapped(pk["HIGH"], "marginal_vs_mid_yr", 0):
                neither = rt.resolve_token("S7_VERDICT")
            assert "never repays its own cost" in neither and \
                "expansion pack never repays its extra cost" in neither, neither
            widths[f"S7 {label}, no expansion saving"] = _assert_within_density_cap(
                "S7_VERDICT", neither, f"{label} with a dead expansion")

        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted battery saving leaked out of this case ({token})")
    return ("S0_VERDICT and S7_VERDICT say plainly that a battery saving nothing or "
            "losing money does not repay its cost, instead of aborting the report "
            f"(live battery-alone savings ${live[0]:,}/${live[1]:,}/yr; "
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_s0_verdict_refuses_a_payback_range_its_own_artifact_contradicts():
    """Section 0's headline quotes both battery-alone paybacks as a RANGE
    under "a sound optional buy". Each is the package's cost divided by one of
    its battery-alone savings, so a payback that is zero, negative or infinite
    WHILE both savings stay positive is the artifact contradicting itself --
    the quotients and the savings cannot both be right, and no branch can be
    written on top of that.

    The household case (a saving at or below zero, where the battery really
    does not repay) inverts instead; it is driven in
    case_battery_verdicts_say_plainly_when_the_battery_never_repays.

    Stubbed household plan, so this keeps running ungated now that S0 checks
    the tariff ranking too -- the substituted values are the household's own
    where the archive is staged."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    provider, cheapest, _rows = _plan_ranking_inputs()
    assert min(mid["battery_alone_yr"], mid["battery_alone_post_ev_fix_yr"]) > 0, (
        "this case drives the positive-savings contradiction; a live saving is not positive")
    for key in ("battery_alone_payback_yr", "battery_alone_payback_post_fix_yr"):
        real = mid[key]
        assert real > 0, f"data/package_results.json:{key} is already {real}"
        for bad in (0, -3.0, float("inf")):
            with _swapped(mid, key, bad), _stub_plan(cheapest, provider):
                try:
                    value = rt.resolve_token("S0_VERDICT")
                    raise AssertionError(f"S0_VERDICT called the battery a sound optional "
                                         f"buy at a {bad}-year payback: {value}")
                except SystemExit as e:
                    assert "S0_VERDICT" in str(e), e
    return ("S0_VERDICT fails closed when positive battery-alone savings are paired with "
            "a zero, negative or infinite payback at either end of the range it publishes")


@case
def case_s5_verdict_inverts_when_timing_stops_driving_the_bill():
    """"takes X% of imported kWh but Y% of the import energy cost, so timing
    sets this bill" is a comparison the sentence never states. It holds only
    while Y > X; at Y <= X the same words assert the opposite of the data.

    It used to refuse there, which is the wrong half of the answer to
    withhold: a house whose on-peak window costs no more than its share of
    kWh is a house where timing does NOT drive the bill, and section 5 exists
    to tell it so. Both directions and the exact tie are driven, each held to
    the density cap."""
    rd = rt._json("report_data.json")
    pc = rd["periods_chart"]
    kwh_share = pc["import_share"][pc["order"].index("on")]
    real = rd["onpeak"]["share_of_energy_cost"]
    assert real > kwh_share, (
        f"data/report_data.json already puts the on-peak cost share ({real}) at or below "
        f"its kWh share ({kwh_share}); the published branch is no longer the live one")
    published = rt.resolve_token("S5_VERDICT")
    assert "timing, more than total consumption, sets this bill" in published, published
    widths = {"published": _assert_within_density_cap(
        "S5_VERDICT", published, "the published branch")}

    for label, bad, expected in (
            ("cheaper on-peak energy", kwh_share / 2,
             "so total consumption, more than timing, sets this bill"),
            ("an exact tie", kwh_share,
             "so timing does not drive this bill on its own")):
        with _swapped(rd["onpeak"], "share_of_energy_cost", bad):
            value = rt.resolve_token("S5_VERDICT")
        assert expected in value, (
            f"S5_VERDICT does not state the {label} conclusion at a cost share of {bad} "
            f"against a kWh share of {kwh_share}: {value}")
        assert "timing, more than total consumption" not in value, (
            f"S5_VERDICT still claims timing sets this bill at {label}: {value}")
        widths[label] = _assert_within_density_cap("S5_VERDICT", value, label)
    assert rt.resolve_token("S5_VERDICT") == published, (
        "the substituted cost share leaked out of this case")
    return ("S5_VERDICT inverts to consumption-driven, and to neither, instead of "
            f"aborting when the on-peak cost share stops exceeding the kWh share "
            f"(live: {real} vs {kwh_share}; "
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_s1_verdict_refuses_to_call_anti_correlated_series_agreement():
    """The second half of section 1's verdict says the independent production
    series "agree at" the weakest pairwise daily correlation. A correlation is
    a signed ratio: at r <= 0 the pair does not agree at all, and printing the
    number after the word "agree" states the opposite of what it means."""
    rows = rt._csv_rows("threeway_production_validation.csv")
    victim = list(rows[0])[1]
    real = rt._min_pairwise_daily_correlation()
    assert real > 0, f"the committed production series already correlate at {real:.4f}"
    originals = [r[victim] for r in rows]
    for r in rows:
        if r[victim] not in (None, ""):
            r[victim] = str(-float(r[victim]))
    try:
        value = rt.resolve_token("S1_VERDICT")
        raise AssertionError(f"S1_VERDICT said the series agree while {victim!r} runs "
                             f"anti-correlated with the others: {value}")
    except SystemExit as e:
        assert "S1_VERDICT" in str(e), e
    finally:
        for r, original in zip(rows, originals):
            r[victim] = original
    assert abs(rt._min_pairwise_daily_correlation() - real) < 1e-12, (
        "the negated production column leaked out of this case")
    return (f"S1_VERDICT refuses the word 'agree' when the weakest pair anti-correlates "
            f"(live: {real:.4f})")


@case
def case_s1_verdict_guards_the_rounding_digit_its_sentence_claims():
    """Section 1 says the billed TOU buckets rebuild "to the whole-kWh
    rounding digit the statements print" -- a +/-0.5 kWh claim about the WORST
    residual in the file. The guard used to read only
    rules.as_billed.buckets_failing, and analysis/tou_audit.py passes a bucket
    within max(1.0 kWh, 0.5% of the billed magnitude): its floor alone is
    twice the digit this sentence quotes, and on the largest committed bucket
    (1,379 kWh) the pass band is +/-6.9 kWh. So zero failing buckets can hold
    while the real residual is ten times what the words assert.

    Driven at the quantity the sentence actually asserts, with
    buckets_failing left at zero throughout -- otherwise the old guard would
    have caught the substitution for the wrong reason."""
    ab = rt._json("tou_audit_summary.json")["rules"]["as_billed"]
    bound = rt._whole_kwh_rounding_bound()
    assert ab["buckets_failing"] == 0, ab["buckets_failing"]
    assert ab["max_abs_residual_kwh"] <= bound, (
        f"data/tou_audit_summary.json's worst as-billed residual is already "
        f"{ab['max_abs_residual_kwh']} kWh against a {bound} kWh bound; S1_VERDICT's "
        "claim is no longer true and the token should already be failing")
    assert rt.ta.ABS_TOL_KWH > bound, (
        f"tou_audit's per-bucket pass floor ({rt.ta.ABS_TOL_KWH} kWh) no longer exceeds "
        f"the {bound} kWh rounding digit S1_VERDICT quotes, so buckets_failing would "
        "establish the claim on its own and this case's premise is gone")
    declared = " ".join(rt.TOKENS["S1_VERDICT"]["sources"])
    assert "max_abs_residual_kwh" in declared, (
        f"S1_VERDICT must declare the residual it is guarded on: {declared}")

    published = rt.resolve_token("S1_VERDICT")
    assert "whole-kWh rounding digit" in published, published
    # Residuals that PASS tou_audit's own rule (its 1.0 kWh floor, and the
    # 6.9 kWh band the largest committed bucket earns at 0.5%) but blow the
    # digit the sentence quotes.
    for bad in (rt.ta.ABS_TOL_KWH, 6.9):
        with _swapped(ab, "max_abs_residual_kwh", bad):
            assert ab["buckets_failing"] == 0, "this arm must not lean on a failing bucket"
            try:
                value = rt.resolve_token("S1_VERDICT")
                raise AssertionError(
                    f"S1_VERDICT claimed a whole-kWh ({bound} kWh) rebuild while its own "
                    f"artifact reports a {bad} kWh worst residual: {value}")
            except SystemExit as e:
                assert "S1_VERDICT" in str(e) and str(bad) in str(e), e
    # Discriminates: a residual sitting exactly on the printed digit still
    # renders, so this is a bound and not a blanket refusal.
    with _swapped(ab, "max_abs_residual_kwh", bound):
        assert rt.resolve_token("S1_VERDICT") == published

    # The bound itself is read off the artifact, so a file that stops stating
    # it fails closed rather than falling back on a number restated here.
    tol = rt._json("tou_audit_summary.json")["tolerance"]
    with _swapped(tol, "basis", "statements print kWh"):
        try:
            value = rt.resolve_token("S1_VERDICT")
            raise AssertionError(
                f"S1_VERDICT rendered while its artifact no longer states the rounding "
                f"bound its sentence quotes: {value}")
        except SystemExit as e:
            assert "S1_VERDICT" in str(e), e
    # And an artifact whose stated bound has drifted from the generator that
    # wrote it fails closed rather than quietly picking one of the two.
    with _patched(rt.ta, "ROUNDING_PER_BUCKET", bound + 0.25):
        try:
            value = rt.resolve_token("S1_VERDICT")
            raise AssertionError(
                f"S1_VERDICT rendered while data/tou_audit_summary.json and "
                f"analysis/tou_audit.py disagree about the rounding bound: {value}")
        except SystemExit as e:
            assert "S1_VERDICT" in str(e), e
    assert rt.resolve_token("S1_VERDICT") == published, (
        "the substituted tolerance leaked out of this case")
    return (f"S1_VERDICT is guarded on max_abs_residual_kwh against the {bound} kWh "
            f"digit its own sentence quotes, not on tou_audit's looser "
            f"max({rt.ta.ABS_TOL_KWH} kWh, {rt.ta.REL_TOL_CELL:.1%}) pass rule "
            f"(live worst residual {ab['max_abs_residual_kwh']} kWh)")


@case
def case_s5_verdict_compares_the_rounded_shares_the_reader_sees():
    """The sentence prints both shares as whole percents and concludes from
    the gap between them. Guarding the UNROUNDED shares passes at kwh 0.1720 /
    cost 0.1740, where the published words read "takes 17% of imported kWh but
    17% of the import energy cost, so timing sets this bill" -- a conclusion
    its own two printed figures contradict. The guard has to run on what the
    reader is shown."""
    rd = rt._json("report_data.json")
    pc = rd["periods_chart"]
    kwh_share = pc["import_share"][pc["order"].index("on")]
    real = rd["onpeak"]["share_of_energy_cost"]
    kwh_pct = round(kwh_share * 100)
    assert round(real * 100) > kwh_pct, (
        f"data/report_data.json already prints both shares as {kwh_pct}%; the published "
        "branch is no longer the live one")
    published = rt.resolve_token("S5_VERDICT")
    assert f"{kwh_pct}% of imported kWh" in published, published

    # Strictly larger unrounded, identical once printed: the exact case the
    # unrounded guard waves through.
    same_pct = kwh_share + 0.0002
    assert same_pct > kwh_share and round(same_pct * 100) == kwh_pct, (
        f"{same_pct} is not a cost share that beats {kwh_share} yet prints the same")
    with _swapped(rd["onpeak"], "share_of_energy_cost", same_pct):
        value = rt.resolve_token("S5_VERDICT")
    assert "timing, more than total consumption" not in value, (
        f"S5_VERDICT concluded that timing sets this bill while showing the reader "
        f"{kwh_pct}% against {kwh_pct}%: {value}")
    assert "the same share of the import energy cost" in value, (
        f"S5_VERDICT does not call two figures that print alike the same share: {value}")

    # Discriminates: one whole percent of divergence is still the published
    # claim, so this is a rounding check and not a blanket refusal.
    with _swapped(rd["onpeak"], "share_of_energy_cost", (kwh_pct + 1) / 100):
        value = rt.resolve_token("S5_VERDICT")
    assert f"but {kwh_pct + 1}% of the import energy" in value, value
    assert rt.resolve_token("S5_VERDICT") == published, (
        "the substituted cost share leaked out of this case")
    return (f"S5_VERDICT refuses the timing conclusion whenever its two printed figures "
            f"round to the same {kwh_pct}%, and keeps it at one point of divergence "
            f"(live: {real:.4f} vs {kwh_share:.4f})")


@case
def case_s2_verdict_fails_closed_instead_of_dividing_by_zero():
    """resolve_token catches KeyError, IndexError, TypeError and ValueError.
    ZeroDivisionError is not among them, so a division by an artifact value
    that can legitimately be zero escapes this module's documented
    named-SystemExit contract and surfaces as a raw traceback -- from a
    household with no exports, or none recorded, which is an ordinary thing
    for a report generator to meet.

    Both divisions section 2's sentence performs are driven: the year's export
    total (the share's denominator) and the array nameplate (the specific
    yield's). A ZeroDivisionError fails this case by propagating out of it."""
    rd = rt._json("report_data.json")
    stubs = _s2_household_inputs()
    with _stub_household(stubs):
        published = rt.resolve_token("S2_VERDICT")
        for bad in (0, 0.0):
            with _swapped(rd["totals"], "exp", bad):
                try:
                    value = rt.resolve_token("S2_VERDICT")
                    raise AssertionError(
                        f"S2_VERDICT published an export-timing share of a {bad} kWh "
                        f"export total: {value}")
                except SystemExit as e:
                    assert "S2_VERDICT" in str(e) and "totals.exp" in str(e), e
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted export total leaked out of this case")
    for bad in (0, 0.0):
        with _stub_household(dict(stubs, **{"solar.kw_dc": bad})):
            try:
                value = rt.resolve_token("S2_VERDICT")
                raise AssertionError(
                    f"S2_VERDICT quoted a specific yield against a {bad} kW array: {value}")
            except SystemExit as e:
                assert "S2_VERDICT" in str(e) and "kw_dc" in str(e), e
    return ("S2_VERDICT raises SystemExit naming itself on a zero export total and a "
            "zero array nameplate, rather than a ZeroDivisionError resolve_token does "
            "not catch")


@case
def case_s2_verdict_refuses_windows_its_hourly_sources_cannot_key():
    """Both halves of section 2's timing clause read HOURLY sources with an
    int() on the tariff's own window bounds. A tariff whose overnight
    super-off-peak run ended at 06:30 would truncate to the "0-6h" census key
    and borrow a count nobody ever made for it -- the exact borrowing the
    lookup's docstring promises to fail closed on -- and the midday export
    slice would silently drop the half hour from a whole-hour bucket profile.

    Driven by handing the formulas a fractional tariff window, which is what
    makes this case fail on the truncating version: "0-6h" IS a key the
    committed census carries, so the old code rendered a confident answer."""
    stubs = _s2_household_inputs()
    with _stub_household(stubs):
        published = rt.resolve_token("S2_VERDICT")
        for label, attr, run in (
                ("the overnight EV census", "_overnight_cheap_run", (0.0, 6.5, "sop")),
                ("the midday export slice", "_cheap_run", (10.0, 14.5, "sop"))):
            with _patched(rt, attr, lambda run=run: run):
                try:
                    value = rt.resolve_token("S2_VERDICT")
                    raise AssertionError(
                        f"S2_VERDICT answered {label} for a window running to "
                        f"{run[1]}h off a whole-hour source: {value}")
                except SystemExit as e:
                    assert "S2_VERDICT" in str(e), e
                    assert str(run[1]) in str(e), (
                        f"the refusal does not name the fractional bound it turned on: {e}")
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted tariff window leaked out of this case")
    return ("S2_VERDICT fails closed on a tariff window its hourly sources cannot key "
            "(overnight run to 6.5h, midday run to 14.5h) instead of truncating to the "
            "neighbouring whole hour")


def _s6_verdict_at(greedy, evening, expanded):
    """S6_VERDICT rendered against a substituted dispatch artifact."""
    dp = rt._json("battery_dispatch_policies.json")
    with _swapped(dp["pw3"]["greedy"], "save", greedy), \
         _swapped(dp["pw3"]["evening"], "save", evening), \
         _swapped(dp["pw3x"]["greedy"], "save", expanded):
        return rt.resolve_token("S6_VERDICT")


@case
def case_s0_verdict_does_not_claim_the_free_move_is_the_largest_saving():
    """"the biggest win costs nothing" is a superlative no artifact supports.
    On this household's own numbers the battery's post-fix saving is LARGER
    than the free EV fix, so on the plain reading -- the largest available
    saving is the free one -- the sentence was false. There is nothing to
    guard: a superlative with no computation behind it has to go, leaving
    the true and useful claim (a large saving that needs no purchase)."""
    saved = rt._json("behavior_rebuild.json")["scenarios"]["a"]["saved"]
    battery = rt._json("package_results.json")["packages"]["MID"][
        "battery_alone_post_ev_fix_yr"]
    assert battery > saved, (
        f"the battery's own saving (${battery}/yr) no longer exceeds the free fix "
        f"(${saved}/yr); re-derive whether a superlative would now be defensible")
    provider, cheapest, _rows = _plan_ranking_inputs()
    with _stub_plan(cheapest, provider):
        value = rt.resolve_token("S0_VERDICT")
    for superlative in ("biggest", "largest", "greatest", "best"):
        assert superlative not in value.lower(), (
            f"S0_VERDICT ranks the free fix as the {superlative} win while the battery "
            f"alone saves ${battery}/yr against its ${saved}/yr: {value}")
    assert f"{rt._usd0(saved)}/yr" in value and "modeled" in value, (
        f"S0_VERDICT dropped the free fix's figure or its modeled label: {value}")
    _assert_within_density_cap("S0_VERDICT", value, "the published branch")
    return (f"S0_VERDICT states the free fix's ${saved:,.0f}/yr without ranking it above "
            f"the battery's own ${battery:,.0f}/yr")


@case
def case_plan_verdicts_invert_rather_than_refusing_when_the_household_plan_loses():
    """Section 0 opens "the rate plan is right" and section 3 says the plan is
    "still the cheapest" -- the same claim, from the same ranking. Both used to
    raise SystemExit when the ranking said otherwise, which took the ENTIRE
    report down: generate_report.py folds a token-resolution failure into its
    own failure list and writes nothing. A household sitting on a plan that no
    longer wins is precisely the household section 3 exists to help, so the
    sentences have to turn around instead.

    Every branch of the ranking is driven, for both tokens together (which is
    what proves they share one ranking rather than two that can drift):
    sole cheapest, an exact tie, and beaten by a cheaper plan. Each rendering
    is held to CLAUDE.md section 10's density cap, since section 0's published
    branch already spends 34 of its 35 words.

    The two genuine fail-closed paths are driven too -- no priced rows for the
    household's provider, and no priced row for the household's own plan --
    because there the sentence has no figure to quote at all, which is the one
    thing refusing is still for.

    Ungated: the household plan and provider are stubbed, so this runs in CI."""
    provider, cheapest, priced = _plan_ranking_inputs()
    index_html = (rt.ROOT / "index.html").read_text()

    # Positive control first, or "inverts" could pass on a token stuck in one
    # branch -- and it doubles as the ungated round-trip check for the S0
    # line, which the verbatim case above cannot make without the private
    # archive now that S0 declares household sources.
    with _stub_plan(cheapest, provider):
        published = _assert_verdict_matches_index("S0_VERDICT", index_html)
        published3 = rt.resolve_token("S3_VERDICT")
    assert "the rate plan is right" in published, (
        f"S0_VERDICT no longer carries the tariff claim this case guards: {published}")
    assert "still the cheapest plan" in published3, published3
    widths = {"S0 sole cheapest": _assert_within_density_cap(
        "S0_VERDICT", published, "the published branch"),
        "S3 sole cheapest": _assert_within_density_cap(
        "S3_VERDICT", published3, "the published branch")}

    runner_up = min((r for r in priced if r["plan"] != cheapest),
                    key=lambda r: float(r["total"]))
    cheapest_total = next(r["total"] for r in priced if r["plan"] == cheapest)
    gap = float(runner_up["total"]) - float(cheapest_total)

    # 1. Another plan really is cheaper: the household sits on the runner-up.
    #    Both sentences must render, say plainly that this plan does not win,
    #    and name the one that does.
    with _stub_plan(runner_up["plan"], provider):
        beaten0 = rt.resolve_token("S0_VERDICT")
        beaten3 = rt.resolve_token("S3_VERDICT")
    assert "a cheaper rate plan exists" in beaten0, (
        f"S0_VERDICT still tells a household on {runner_up['plan']} its rate plan is "
        f"right while {cheapest} prices ${gap:,.2f} lower: {beaten0}")
    assert "the rate plan is right" not in beaten0, beaten0
    assert "is not the cheapest plan" in beaten3 and cheapest in beaten3, (
        f"S3_VERDICT does not say plainly that {runner_up['plan']} loses to "
        f"{cheapest}: {beaten3}")
    assert rt._usd0(gap) in beaten3, (
        f"S3_VERDICT does not quote the ${gap:,.2f}/yr the household is leaving on the "
        f"table: {beaten3}")
    widths["S0 beaten"] = _assert_within_density_cap("S0_VERDICT", beaten0, "the beaten branch")
    widths["S3 beaten"] = _assert_within_density_cap("S3_VERDICT", beaten3, "the beaten branch")

    # 2. An exact tie: the runner-up is repriced to the cheapest total, so the
    #    household's plan is joint-cheapest. "THE cheapest plan" is false
    #    there, and so is "a cheaper rate plan exists" -- it is its own branch.
    with _swapped(runner_up, "total", cheapest_total), _stub_plan(cheapest, provider):
        tie0 = rt.resolve_token("S0_VERDICT")
        tie3 = rt.resolve_token("S3_VERDICT")
    assert "ties for cheapest" in tie0, (
        f"S0_VERDICT does not report the tie between {cheapest} and "
        f"{runner_up['plan']}: {tie0}")
    assert "the rate plan is right" not in tie0 and "cheaper rate plan" not in tie0, tie0
    assert f"ties {runner_up['plan']}" in tie3, (
        f"S3_VERDICT does not name the plan tying {cheapest} at "
        f"${float(cheapest_total):,.2f}: {tie3}")
    assert "still the cheapest" not in tie3 and "is not the cheapest" not in tie3, tie3
    widths["S0 tie"] = _assert_within_density_cap("S0_VERDICT", tie0, "the tie branch")
    widths["S3 tie"] = _assert_within_density_cap("S3_VERDICT", tie3, "the tie branch")

    # 2b. More than one winner has to read as prose in both directions: the
    #     tie branch joins the names, the beaten branch conjugates its verb.
    third = min((r for r in priced if r["plan"] not in (cheapest, runner_up["plan"])),
                key=lambda r: float(r["total"]))
    with _swapped(runner_up, "total", cheapest_total), \
            _swapped(third, "total", cheapest_total), _stub_plan(cheapest, provider):
        many_tie = rt.resolve_token("S3_VERDICT")
    joined = " and ".join(sorted((runner_up["plan"], third["plan"])))
    assert f"ties {joined} as the cheapest plan" in many_tie, (
        f"S3_VERDICT does not name both plans tying {cheapest}: {many_tie}")
    with _swapped(runner_up, "total", cheapest_total), _stub_plan(third["plan"], provider):
        many_beaten = rt.resolve_token("S3_VERDICT")
    joined = " and ".join(sorted((runner_up["plan"], cheapest)))
    assert f"because {joined} each price" in many_beaten, (
        f"S3_VERDICT does not conjugate two winners: {many_beaten}")
    widths["S3 tie, two others"] = _assert_within_density_cap(
        "S3_VERDICT", many_tie, "a three-way tie")
    widths["S3 beaten, two winners"] = _assert_within_density_cap(
        "S3_VERDICT", many_beaten, "two winners")

    # 3. The two cases with no ranking to report at all still fail closed:
    #    a provider column that prices nothing, and one that prices everything
    #    except the household's own plan.
    for label, plan, prov in (("an unpriced provider", cheapest, "ZZZ_NO_SUCH_PROVIDER"),
                              ("an unpriced plan", "ZZZ_NO_SUCH_PLAN", provider)):
        for token in ("S0_VERDICT", "S3_VERDICT"):
            with _stub_plan(plan, prov):
                try:
                    value = rt.resolve_token(token)
                    raise AssertionError(
                        f"{token} ranked plans with {label}: {value}")
                except SystemExit as e:
                    assert token in str(e), e

    assert float(runner_up["total"]) != float(cheapest_total), (
        "the substituted plan total leaked out of this case")
    with _stub_plan(cheapest, provider):
        assert rt.resolve_token("S0_VERDICT") == published, (
            "S0_VERDICT no longer renders its published line after the substitutions")
        assert rt.resolve_token("S3_VERDICT") == published3, (
            "S3_VERDICT no longer renders its published line after the substitutions")
    return (f"S0_VERDICT and S3_VERDICT invert instead of aborting the report when "
            f"{runner_up['plan']} ties or beats {cheapest} on the {provider} column, "
            f"still fail closed with no priced ranking at all, and S0's published line "
            f"round-trips into index.html ungated "
            f"({', '.join(f'{k} {v}w' for k, v in widths.items())})")


@case
def case_plan_lead_tokens_report_a_gap_the_matrix_does_not_call_a_lead():
    """The same shape one artifact over. S4_VERDICT_SHORT says the battery
    "widens EV-TOU-5's lead over" the runner-up and PLAN_MARGIN_VS_RUNNER_UP
    publishes that lead as a dollar figure; both take the difference without
    checking its sign. A runner-up pricing at or below the household's plan
    would print a negative lead and a battery that "widens" it -- so the
    shared _runner_up() helper used to refuse, taking the whole report down
    for any household the matrix does not put first.

    A margin is a real figure at every sign and "this plan trails" is a real
    sentence, so both now word themselves off the sign. This case owns only
    the words around the prefix -- that they stop claiming a lead that is not
    there. The Yes/No/Too-close prefix itself and the widens/narrows verb are
    driven by their own cases: issue #141 re-derived the prefix off the plans
    cheapest at BOTH battery states
    (case_the_heading_never_denies_a_winner_the_row_beneath_it_names) and
    gated the verb on a single rival across both columns
    (case_the_runner_up_is_taken_per_column_not_reused_from_the_no_battery_one).

    Ranked on battery_plan_matrix.json's own no-battery column -- the numbers
    these two sentences actually quote -- not on plan_results.csv."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    ordered = sorted(plans, key=lambda k: plans[k]["no_battery"])
    best, runner_up = ordered[0], ordered[1]
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file()
                else "CEA")
    widths, readings = {}, {}
    with _stub_plan(best, provider):
        lead = plans[runner_up]["no_battery"] - plans[best]["no_battery"]
        assert lead > 0, f"data/battery_plan_matrix.json shows no lead for {best}"
        assert rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP") == rt._usd0(lead)
        published = rt.resolve_token("S4_VERDICT_SHORT")
        assert f"lead over {runner_up}" in published, published
        widths["published"] = _assert_within_density_cap(
            "S4_VERDICT_SHORT", published, "the published branch")

        for label, beaten, margin, standing in (
                ("tie", _bill_of(plans[best], "no_battery"), "$0", f"ties {runner_up}"),
                ("beaten", plans[best]["no_battery"] - 500, "-$500",
                 f"trails {runner_up} by $500/yr")):
            with _cell_priced(plans[runner_up], "no_battery", beaten):
                got_margin = rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP")
                value = rt.resolve_token("S4_VERDICT_SHORT")
            assert got_margin == margin, (
                f"PLAN_MARGIN_VS_RUNNER_UP published {got_margin} for a {label} against "
                f"{runner_up}, not {margin}")
            assert f"{standing} without a battery" in value, (
                f"S4_VERDICT_SHORT does not say where {best} actually stands without a "
                f"battery at a {label}: {value}")
            # "leads by ... with one" stays legal: with a battery this plan
            # really may lead. What may not survive is a lead in the
            # NO-battery ranking, or a verb that presupposes one.
            for phrase in (f"lead over {runner_up}", "widens", "narrows",
                           "leads without a battery"):
                assert phrase not in value, (
                    f"S4_VERDICT_SHORT still claims {phrase!r} for {best} while "
                    f"{runner_up} prices ${beaten:,} against ${plans[best]['no_battery']:,}: "
                    f"{value}")
            widths[label] = _assert_within_density_cap("S4_VERDICT_SHORT", value, label)
            readings[label] = got_margin

        assert rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP") == rt._usd0(lead), (
            "the substituted no-battery total leaked out of this case")
        assert rt.resolve_token("S4_VERDICT_SHORT") == published, (
            "the substituted no-battery total leaked out of this case")
    return (f"PLAN_MARGIN_VS_RUNNER_UP publishes a signed margin ({', '.join(readings.values())}) "
            f"and S4_VERDICT_SHORT drops the word 'lead' when {runner_up} ties or beats "
            f"{best}, instead of both aborting the report (live lead ${lead:,}; "
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


# ---------------------------------------------------------------------------
# THE WHOLE-DOLLAR ROUNDING: ORDER SURVIVES IT, SIZE DOES NOT
# (issue #141 adversarial review).
#
# analysis/battery_plan_matrix.py writes every cell it publishes as round(x):
#
#     plans[plan] = {"no_battery": round(no_b), "with_battery": round(with_b),
#                    "battery_value": round(no_b - with_b)}
#
# Section 4's heading asks whether a battery changes which plan is best, and
# report_tokens answers it by comparing the two columns' cheapest-plan sets.
# The tempting reading of that rounding -- "the cents are gone, so a $1 gap
# settles nothing" -- is half right, and the half that is wrong was briefly
# shipped as a $1.00 band on the word "cheapest".
#
#   ORDER survives. round() is non-decreasing, so a cell storing 101 came
#   from a strictly dearer bill than a cell storing 100. Which plan a column
#   prices cheapest is settled; equal cells are a real tie.
#
#   SIZE does not. A stored $1 gap is a real gap of a cent to just under $2,
#   and a stored $1 CHANGE in a gap is four roundings deep, so it may be a
#   narrowing.
#
# Reading the band into "cheapest" put a plan the matrix ranks SECOND into
# `winners`, and section 4's household row was fixed class="win" markup -- so
# the runner-up would have been published as the winner. Since issue #178 that
# row's class is a token, which moves the same hazard one branch along: the
# token decides each column's standing on this identity, so a band read into
# it paints a runner-up as the winner just as surely. The cases below hold
# both halves: identity is exact, sizes are hedged, and the household the
# matrix ranks second in one column gets a whole report whose row says it is
# cheapest in the other column only.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _matrix_priced(plans, cells):
    """Substitute a whole synthetic matrix -- {plan: (no_battery, with_battery)}
    -- into the cached artifact, restoring every cell on the way out.

    battery_value is DERIVED here rather than passed, for _mid_battery_swaps'
    reason: battery_plan_matrix.py writes it as round(no_b - with_b) off the
    same two bills, so a case that moves a column and leaves battery_value
    behind is not describing a household, it is describing an artifact no run
    of the generator could produce.

    EVERY CELL IS WRITTEN THE WAY THE GENERATOR WRITES IT (issue #177). A
    value here is a modeled bill in dollars and cents; it lands as the
    whole-dollar display cell round(v) AND as the integer-cents ranking cell
    round(v * 100), the two fields battery_plan_matrix.py writes side by side.
    A case that wants the cents to disagree with the dollars -- two bills
    that round to the same dollar, say -- passes values with cents in them;
    integer values behave exactly as they did before the cents existed."""
    with contextlib.ExitStack() as stack:
        for plan, (no_b, with_b) in cells.items():
            for column, v in (("no_battery", no_b), ("with_battery", with_b)):
                for swap in _cell_swaps(plans[plan], column, v):
                    stack.enter_context(swap)
            stack.enter_context(_swapped(plans[plan], "battery_value",
                                         round(no_b - with_b)))
            stack.enter_context(_swapped(plans[plan], "battery_value_cents",
                                         round(no_b * 100) - round(with_b * 100)))
        yield


def _cell_swaps(row, column, value):
    """The two swaps that put ONE matrix cell at `value` the way
    battery_plan_matrix.py would have written it: the whole-dollar display
    field round(value) and its integer-cents ranking twin round(value * 100).

    report_tokens ranks the columns on the cents and refuses a row whose two
    fields disagree by more than the dollar rounding (a half-updated artifact
    is not a household), so a case that moves a dollar cell alone is not
    describing a matrix the generator could produce -- it is describing the
    refusal. Cases that want a household use this; the one case that wants
    the refusal swaps the dollar field by itself and says so."""
    return [_swapped(row, column, round(value)),
            _swapped(row, f"{column}_cents", round(value * 100))]


@contextlib.contextmanager
def _cell_priced(row, column, value):
    """_cell_swaps as one context manager."""
    with contextlib.ExitStack() as stack:
        for swap in _cell_swaps(row, column, value):
            stack.enter_context(swap)
        yield


def _matrix_row(no_b, with_b):
    """A whole matrix row the way battery_plan_matrix.py writes one: the two
    bills as whole-dollar cells and as integer cents, and the battery value
    as the rounded difference and the exact difference of the cents. A
    non-finite bill is carried through as itself in both fields, which is
    what a case poisoning a cell wants the ranking to meet."""
    def dollars(v):
        return round(v) if rt._finite(v) else v

    def cents(v):
        return round(v * 100) if rt._finite(v) else v

    row = {"no_battery": dollars(no_b), "with_battery": dollars(with_b),
           "battery_value": dollars(no_b - with_b),
           "no_battery_cents": cents(no_b), "with_battery_cents": cents(with_b)}
    row["battery_value_cents"] = (row["no_battery_cents"] - row["with_battery_cents"]
                                  if rt._finite(no_b, with_b) else no_b - with_b)
    return row


def _bill_of(row, column):
    """The modeled bill a matrix cell carries, in dollars and cents -- what a
    case prices a rival AT when it wants a TIE. The whole-dollar cell is not
    that bill: this household's no-battery total is $4,881.73, stored as
    4882, and a rival priced at 4882 flat is 27 cents dearer, which the
    ranking now sees. A tie is two bills equal to the cent, so it is built
    from the cents."""
    return row[f"{column}_cents"] / 100


def _matrix_pair():
    """(plans, household plan, its nearest rival, everyone else) for the band
    cases, read off the committed matrix rather than named as literals."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    ordered = sorted(plans, key=lambda k: plans[k]["no_battery"])
    assert len(ordered) > 1, (
        "data/battery_plan_matrix.json prices one plan; these cases need a rival")
    return plans, ordered[0], ordered[1], ordered[2:]


def _s4_at(plans, cells):
    """S4_VERDICT_SHORT (and the two cheapest-plan sets it decides on) rendered
    against a synthetic matrix."""
    with _matrix_priced(plans, cells):
        value = rt.resolve_token("S4_VERDICT_SHORT")
        sets = tuple(rt._bpm_cheapest("S4_VERDICT_SHORT", c)
                     for c in ("no_battery", "with_battery"))
    return value, sets


@case
def case_the_matrix_rounding_settles_the_order_and_only_blurs_the_size():
    """THE PREMISE UNDER EVERY CASE BELOW, asserted rather than commented.

    MONOTONICITY. round() is non-decreasing, so round(x) > round(y) implies
    x > y -- there is no pair of bills whose rounded cells rank one way and
    whose real values rank the other. Driven here on the half-dollar
    boundaries, where Python rounds to even and where a counter-example would
    have to live if there were one, and then over a fine sweep. This is why
    "cheapest" is an equality on the stored cells: a stored $1 gap IS an
    ordering.

    THE SIZE BOUND, which is all the band is for. Per cell,
    |stored - modeled| <= $0.50. Per difference of two cells, twice that:
    $1.00, which is _BPM_TIE_USD. Per difference of two GAPS -- the
    widens/narrows verb, (rival - us) without a battery against the same with
    one -- four cells: 4 x $0.50 = $2.00, which is _BPM_GAP_TIE_USD. A stored
    gap of $1 therefore covers real gaps from about a cent to just under $2,
    which the sweep below exhibits at both ends.

    The rounding itself is asserted against the artifact: every published
    dollar cell is a whole number of dollars. If a future generator keeps the
    cents in those cells, this case fails and both constants have to be
    re-derived rather than silently applied to values that no longer need
    them.

    WHAT THE BAND NO LONGER DECIDES (issue #177). The bounds above are for
    the SIZES the sentences quote, which are still differences of whole-dollar
    cells. Which plan is cheapest is not read off those cells any more: the
    generator writes each modeled bill a second time as integer cents
    (`no_battery_cents`, `with_battery_cents`), and report_tokens ranks on
    those, with a tie meaning two bills that agree to the cent
    (_BPM_MATERIAL_USD, one cent: the resolution a bill is settled in, not a
    rounding the artifact applied). Both fields are asserted here against
    each other: the cents are integers, and every dollar cell is within the
    half-dollar its rounding allows of its cents twin."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    fractional = [f"{p}.{k} = {v!r}" for p, row in sorted(plans.items())
                  for k, v in sorted(row.items())
                  if not k.endswith("_cents") and float(v) != int(v)]
    assert not fractional, (
        "data/battery_plan_matrix.json no longer rounds every display cell to whole "
        "dollars (" + ", ".join(fractional) + "), so report_tokens._BPM_TIE_USD is "
        "derived from a rounding the generator has stopped applying")
    assert rt._BPM_MATERIAL_USD == 0.01, (
        f"_BPM_MATERIAL_USD is {rt._BPM_MATERIAL_USD}, not the one cent a bill is settled "
        "in; anything wider is a band, and a band on 'cheapest' admits a runner-up")
    for plan, row in sorted(plans.items()):
        for column in ("no_battery", "with_battery", "battery_value"):
            cents = row.get(f"{column}_cents")
            assert isinstance(cents, int) and not isinstance(cents, bool), (
                f"data/battery_plan_matrix.json:plans.{plan}.{column}_cents is {cents!r}, "
                "not the integer cents report_tokens ranks on")
            assert abs(cents / 100 - row[column]) <= 0.5 + 1e-9, (
                f"data/battery_plan_matrix.json:plans.{plan}.{column} ({row[column]}) is "
                f"not the whole-dollar rounding of its cents twin ({cents})")
        assert row["battery_value_cents"] == row["no_battery_cents"] - row["with_battery_cents"], (
            f"plans.{plan}.battery_value_cents is not no_battery_cents - with_battery_cents")
    # ORDER. Every half-dollar boundary in a $200 window, plus a 1-cent sweep
    # across a narrower one: round(a) > round(b) must never hold for a <= b.
    probes = [n / 2 for n in range(-100, 301)]
    probes += [5_000 + n / 100 for n in range(-200, 201)]
    inverted = [(a, b) for a in probes for b in probes
                if round(a) > round(b) and a <= b]
    assert not inverted, (
        f"round() inverted an ordering on {len(inverted)} pair(s), e.g. {inverted[:3]} "
        "-- report_tokens decides which plan is cheapest on the stored cells because "
        "rounding cannot do that")
    # SIZE. The same monotone rounding leaves a stored $1 gap unsized: these
    # two pairs both store 101 against 100.
    tight, wide = (100.501, 100.5), (101.499, 99.5)
    for hi, lo in (tight, wide):
        assert (round(hi), round(lo)) == (101, 100), (
            f"{hi}/{lo} no longer stores as 101/100, so this case is not exhibiting "
            "the spread a $1 stored gap can hide")
    assert (tight[0] - tight[1]) < 0.01 < 1.9 < (wide[0] - wide[1]) < 2 * rt._BPM_TIE_USD, (
        "the two pairs above no longer bracket the range a $1 stored gap covers")
    assert rt._BPM_TIE_USD == 0.5 + 0.5, (
        f"_BPM_TIE_USD is {rt._BPM_TIE_USD}, not the $0.50 + $0.50 the two rounded "
        "cells of one comparison can each be out by")
    assert rt._BPM_GAP_TIE_USD == 4 * 0.5 == 2 * rt._BPM_TIE_USD, (
        f"_BPM_GAP_TIE_USD is {rt._BPM_GAP_TIE_USD}, not the four cells a comparison "
        "of two gaps rounds")
    return (f"the matrix's whole-dollar rounding is monotone over {len(probes)} probes, so "
            f"it settles which plan is cheapest and only blurs by how much: a stored $1 gap "
            f"is a real ${tight[0] - tight[1]:.3f} to ${wide[0] - wide[1]:.3f}, inside "
            f"_BPM_TIE_USD ${rt._BPM_TIE_USD:.2f} per comparison and "
            f"${rt._BPM_GAP_TIE_USD:.2f} per gap change, across all "
            f"{sum(len(r) for r in plans.values())} published cells")


@case
def case_a_dollar_of_stored_difference_ranks_and_a_stored_tie_does_not():
    """THE TWO HALVES IN ONE MATRIX. A stores 100 / 90, B stores 101 / 90.

    Without a battery the cells differ by a dollar, so A's bill was strictly
    the cheaper: the cheapest set is {A} ALONE, and a band that put B in it
    would be claiming B might be cheapest when the artifact says it is not.
    With a battery the cells are equal, which is a genuine tie -- two bills
    within a dollar of each other that nothing published can separate.

    So the heading may not say "Yes": A is cheapest in both columns and the
    only thing that changed is that B caught up to a tie, which is not a
    ranking change. It says "No", because A is the ONE plan that is
    cheapest-or-joint-cheapest at both battery states -- strictly cheapest
    without one, unbeaten with one -- so staying on A is the answer whatever
    the battery does. That is also the only reading consistent with the markup
    this case resolves below: joint-cheapest is cheapest, so section 4's row
    reads "tie-win" -- cheapest in both columns, tied in one -- and keeps A's
    cells, and a heading calling the question unsettleable would be
    contradicting the row underneath it (issue #141 review round 3). The row
    may not read a plain "tie" either: A is the SOLE cheapest plan without a
    battery, and that badge would deny it a column it wins outright.
    And the size clause still has to hedge the $1 lead it
    cannot size -- "leads by $1/yr" is a precision two rounded cells do not
    carry."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t)
                     for t in ("S4_VERDICT_SHORT",) + _MATRIX_PLAN_TOKENS}
        cells = {best: (100, 90), rival: (101, 90)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        assert no_batt == {best}, (
            f"a $1 stored gap did not rank: the no-battery column prices {best} at 100 "
            f"and {rival} at 101, so {best} alone is cheapest, not {sorted(no_batt)}")
        assert with_batt == {best, rival}, (
            f"identical cells (90/90) did not read as a tie with the battery: "
            f"{sorted(with_batt)}")
        assert not value.startswith("Yes"), (
            f"S4_VERDICT_SHORT answers 'Yes' where {best} is cheapest without a battery "
            f"and TIES {rival} with one -- a tie is not a ranking change: {value}")
        assert value.startswith("No —"), (
            f"S4_VERDICT_SHORT will not answer 'No' where {best} is strictly cheapest "
            f"without a battery and unbeaten with one, which makes it the single plan "
            f"cheapest-or-joint-cheapest at both states: {value}")
        assert f"leads {rival} by under {rt._usd0(1 + rt._BPM_TIE_USD)}/yr" in value, (
            f"S4_VERDICT_SHORT does not name the holder of the $1 lead while bounding a "
            f"size two rounded cells cannot carry: {value}")
        assert "$1/yr" not in value, (
            f"S4_VERDICT_SHORT quotes a $1/yr lead off two cells that could be a cent "
            f"apart: {value}")
        # The tie renders the rest of section 4: joint-cheapest is cheapest,
        # so the row keeps this household's cells -- and says "tie-win",
        # because a plan that shares the minimum with a rival in one column is
        # not the sole winner there, and one that stores the minimum alone in
        # the other column is not merely tied for cheapest either.
        with _matrix_priced(plans, cells):
            for token in _MATRIX_PLAN_TOKENS:
                assert rt.resolve_token(token).strip(), (
                    f"{token} refused a matrix in which {best} is cheapest without a "
                    f"battery and tied with one")
            assert rt.resolve_token("S4_ROW_CLASS") == "tie-win", (
                f"section 4's row reads {rt.resolve_token('S4_ROW_CLASS')!r} where {best} "
                f"is strictly cheapest without a battery and TIES {rival} with one; a "
                "shared minimum is not a sole win, and a column won outright is not a tie")
        words = _assert_within_density_cap("S4_VERDICT_SHORT", value, "a lead into a tie")
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"a $1 stored gap ranks ({sorted(no_batt)} alone cheapest) and equal cells tie "
            f"({sorted(with_batt)}), so the heading calls the tie no change of plan while "
            f"hedging the size it cannot carry ({value!r}, {words}w)")


@case
def case_a_winner_flip_wider_than_the_band_is_still_reported():
    """The band's other edge: it must not swallow a flip the artifact really
    does resolve. {best} is $100 cheaper without a battery and $200 dearer
    with one -- both sides of the flip outside $1.00 -- so the cheapest sets
    share no plan at all and the heading says so."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = rt.resolve_token("S4_VERDICT_SHORT")
        cells = {best: (5_000, 5_000), rival: (5_100, 4_800)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        assert (no_batt, with_batt) == ({best}, {rival}), (
            f"the band swallowed a $100/$200 winner flip: without a battery "
            f"{sorted(no_batt)}, with one {sorted(with_batt)}")
        assert value.startswith("Yes"), (
            f"S4_VERDICT_SHORT does not report a winner flip the artifact resolves "
            f"({best} $100/yr ahead without a battery, $200/yr behind with one): {value}")
        assert f"trails {rival} by" not in value and "trails by $200/yr with one" in value, (
            f"S4_VERDICT_SHORT does not say where {best} stands with the battery: {value}")
        words = _assert_within_density_cap("S4_VERDICT_SHORT", value, "a real winner flip")
        assert rt.resolve_token("S4_VERDICT_SHORT") == published, (
            "the synthetic matrix leaked out of this case")
    return (f"a winner flip wider than the ${rt._BPM_TIE_USD:.2f} band is still reported as "
            f"one ({value!r}, {words}w)")


@case
def case_the_widens_verb_needs_a_gap_change_the_rounding_can_resolve():
    """The same defect one level up, swept rather than patched. The heading's
    verb compares (rival - us) without a battery against the same difference
    with one -- FOUR rounded cells, so the change it asserts is worth nothing
    below $2.00. A lead of $100/yr becoming $101/yr published "the battery
    widens EV-TOU-5's lead", a direction that is entirely an artifact of where
    four roundings happened to fall.

    Both sides are driven: a $1 change says the rounding cannot resolve it, a
    $3 change is over the bound and keeps the verb."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    seen = {}
    with _stub_plan(best, provider):
        published = rt.resolve_token("S4_VERDICT_SHORT")
        for label, with_battery_rival, verb in (
                ("inside the bound", 5_101, None), ("outside it", 5_103, "widens")):
            cells = {best: (5_000, 5_000), rival: (5_100, with_battery_rival)}
            cells.update({p: (9_000, 9_000) for p in rest})
            value, _sets = _s4_at(plans, cells)
            gap_change = with_battery_rival - 5_100
            if verb is None:
                for wrong in ("widens", "narrows"):
                    assert wrong not in value, (
                        f"S4_VERDICT_SHORT still says the battery {wrong} the lead on a "
                        f"${gap_change} change in it, under the "
                        f"${rt._BPM_GAP_TIE_USD:.2f} four rounded cells can hide: {value}")
                assert "$100/yr" in value and "$101/yr" in value, (
                    f"S4_VERDICT_SHORT dropped one of the two gaps it is comparing: {value}")
            else:
                assert verb in value, (
                    f"S4_VERDICT_SHORT dropped the verb for a ${gap_change} change in the "
                    f"lead, which is outside the ${rt._BPM_GAP_TIE_USD:.2f} bound: {value}")
            seen[label] = _assert_within_density_cap("S4_VERDICT_SHORT", value, label)
        assert rt.resolve_token("S4_VERDICT_SHORT") == published, (
            "the synthetic matrix leaked out of this case")
    return ("the widens/narrows verb needs a gap change bigger than the "
            f"${rt._BPM_GAP_TIE_USD:.2f} four rounded cells can invent ("
            + ", ".join(f"{k} {v}w" for k, v in seen.items()) + ")")


# ---------------------------------------------------------------------------
# THE RANKING IS TAKEN ON THE CENTS, NOT ON THE DOLLAR CELLS (issue #177).
#
# Every case above this line lived with the whole-dollar cells as the only
# thing the artifact carried, and derived what could and could not be read
# off them. That derivation was right about the cells and wrong about the
# household: two bills a few cents apart round to the same dollar, and the
# heading then called a real ordering a tie -- or, the other way round, two
# bills that round a dollar apart were reported as a settled ordering across
# both columns when the battery had in fact flipped them by cents. On this
# checkout's household the margins are hundreds of dollars and nothing
# published depended on it; the exposure is the reproducing household whose
# plans sit close together, which is the household section 4 exists for.
#
# battery_plan_matrix.py now writes each modeled bill twice: the whole-dollar
# cell the tables display, and its integer-cents twin. report_tokens ranks on
# the cents. The cases below drive both directions the issue named, with
# matrices whose dollar cells cannot tell the plans apart.
# ---------------------------------------------------------------------------
@case
def case_a_winner_flip_of_cents_is_ranked_on_the_cents_not_the_dollar_cells():
    """THE MASKED FLIP. us 100.40 / 90.60, B 100.49 / 90.51.

    Every dollar cell agrees: both plans store 100 without a battery and 91
    with one. On the cells alone that is a tie in both columns, and the
    heading used to say "Too close to call" over a row badged "tie". The
    bills say otherwise: we are nine cents cheaper without a battery and nine
    cents dearer with one, so the battery really does change which plan is
    cheapest. The ranking has to come from the cents, the heading has to say
    "Yes", and the row has to say we win one column and trail the other.

    The SIZES are still quoted off the dollar cells, which are what the table
    under the heading shows, so a nine-cent margin is stated as a bound the
    display can carry ("under $1/yr") and never as "$0/yr"."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t)
                     for t in ("S4_VERDICT_SHORT", "S4_ROW_CLASS") + _MATRIX_PLAN_TOKENS}
        cells = {best: (100.40, 90.60), rival: (100.49, 90.51)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        with _matrix_priced(plans, cells):
            for plan, column in ((best, "no_battery"), (rival, "no_battery"),
                                 (best, "with_battery"), (rival, "with_battery")):
                assert plans[plan][column] == plans[best][column], (
                    f"the fixture no longer puts {best} and {rival} on the same dollar "
                    f"cell in the {column} column, so it is not driving a masked flip")
            row_class = rt.resolve_token("S4_ROW_CLASS")
        assert (no_batt, with_batt) == ({best}, {rival}), (
            f"a nine-cent winner flip hidden inside equal dollar cells was not ranked on "
            f"the cents: without a battery {sorted(no_batt)}, with one {sorted(with_batt)}")
        assert value.startswith("Yes"), (
            f"S4_VERDICT_SHORT does not report a winner flip the cents settle: {value}")
        assert "Too close to call" not in value, value
        assert f"{best} leads {rival} by under {rt._usd0(rt._BPM_TIE_USD)}/yr without a battery" in value, (
            f"S4_VERDICT_SHORT does not state the sub-dollar no-battery lead as a bound "
            f"the dollar cells can carry: {value}")
        assert f"trails by under {rt._usd0(rt._BPM_TIE_USD)}/yr with one" in value, (
            f"S4_VERDICT_SHORT does not state the sub-dollar with-battery deficit: {value}")
        assert "$0/yr" not in value and "ties" not in value, value
        assert row_class == "trails-win", (
            f"section 4's row reads {row_class!r} where the cents put {best} alone "
            f"cheapest without a battery and behind {rival} with one")
        words = _assert_within_density_cap("S4_VERDICT_SHORT", value, "a flip of cents")
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"a winner flip of nine cents inside equal dollar cells is ranked on the cents "
            f"({sorted(no_batt)} -> {sorted(with_batt)}) and reported ({value!r}, "
            f"{row_class}, {words}w)")


@case
def case_a_rounding_tie_the_cents_resolve_is_not_reported_as_a_tie():
    """THE ISSUE'S FIRST EXAMPLE. us 100.49 / 90.40, B 100.51 / 90.49.

    Without a battery the dollar cells store 100 and 101; with one they both
    store 90. Read off the cells, the battery turned a sole win into a tie
    and the row was badged "tie-win". Read off the bills, we are cheaper at
    both battery states -- by two cents, then by nine -- so nothing tied and
    the row is a plain "win". The dollar cells' equality is a rounding
    artifact, and it may not be reported as a change of standing.

    ONE CENT IS ENOUGH, and no less is: the third matrix prices B one cent
    dearer without a battery and to the cent with one, which is a sole win in
    the first column and a real tie in the second. _BPM_MATERIAL_USD is the
    cent because a bill is settled in cents; two totals that agree to the
    cent are one bill, and two that differ by one are not."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t)
                     for t in ("S4_VERDICT_SHORT", "S4_ROW_CLASS") + _MATRIX_PLAN_TOKENS}
        seen = {}
        for label, cells, expect_sets, expect_class, expect_with in (
                ("a rounding tie with the battery",
                 {best: (100.49, 90.40), rival: (100.51, 90.49)},
                 ({best}, {best}), "win",
                 f"leads by under {rt._usd0(rt._BPM_TIE_USD)}/yr with one"),
                ("one cent without, to the cent with",
                 {best: (100.00, 100.00), rival: (100.01, 100.00)},
                 ({best}, {best, rival}), "tie-win", "ties with one")):
            cells = dict(cells)
            cells.update({p: (9_000, 9_000) for p in rest})
            value, sets = _s4_at(plans, cells)
            with _matrix_priced(plans, cells):
                assert plans[best]["with_battery"] == plans[rival]["with_battery"], (
                    f"{label}: the fixture no longer stores equal with-battery dollar "
                    "cells, so it is not driving a rounding tie")
                row_class = rt.resolve_token("S4_ROW_CLASS")
            assert sets == expect_sets, (
                f"{label}: the cheapest sets were read off the dollar cells, not the cents: "
                f"{[sorted(x) for x in sets]}")
            assert value.startswith("No —"), (
                f"{label}: S4_VERDICT_SHORT stopped answering 'No' for a plan the cents "
                f"price cheapest at both battery states: {value}")
            assert expect_with in value, (
                f"{label}: S4_VERDICT_SHORT does not state the with-battery standing the "
                f"cents settle ({expect_with!r}): {value}")
            assert "Too close to call" not in value and "$0/yr" not in value, value
            assert row_class == expect_class, (
                f"{label}: section 4's row reads {row_class!r}, not {expect_class!r}")
            seen[label] = f"{value!r} / {row_class}"
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return ("equal dollar cells are not a tie when the cents disagree, and one cent is: "
            + "; ".join(f"{k}: {v}" for k, v in seen.items()))


@case
def case_a_dollar_cell_moved_without_its_cents_is_refused_not_ranked():
    """The two fields describe ONE bill, and the ranking says so. A matrix
    whose dollar cell and cents twin disagree by more than the half-dollar
    the rounding allows was not written by battery_plan_matrix.py -- it was
    hand-edited, or half-updated -- and ranking it would publish a standing
    off whichever field the reader did not look at. Every token that ranks
    or differences the matrix refuses it, naming itself, the plan and the
    column -- for a RIVAL's row and for the HOUSEHOLD'S OWN row alike. The
    own row is the one the first version of this case did not move: the
    rivals were checked where they were ranked, but PLAN_MARGIN_VS_RUNNER_UP
    subtracts the household's own cell without ranking it, so an own cell
    moved $500 priced a $1,461 margin while S4_VERDICT_SHORT refused the
    same matrix (issue #177 review).

    This is also why every fixture in this suite that moves a ranked cell
    goes through _cell_priced: moving the dollar field alone is this
    refusal, not a household."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    refused = []
    with _stub_plan(best, provider):
        for moved_plan, column, token in (
                (rival, "no_battery", "S4_ROW_CLASS"),
                (rival, "with_battery", "S4_VERDICT_SHORT"),
                (rival, "no_battery", "PLAN_MARGIN_VS_RUNNER_UP"),
                (best, "no_battery", "S4_ROW_CLASS"),
                (best, "with_battery", "S4_VERDICT_SHORT"),
                (best, "no_battery", "S4_VERDICT_SHORT"),
                (best, "no_battery", "PLAN_MARGIN_VS_RUNNER_UP")):
            with _swapped(plans[moved_plan], column, plans[moved_plan][column] - 500):
                try:
                    value = rt.resolve_token(token)
                    raise AssertionError(
                        f"{token} ranked a matrix whose {moved_plan}.{column} dollar "
                        f"cell moved $500 while its cents twin did not: {value!r}")
                except SystemExit as e:
                    for needle in (token, moved_plan, column):
                        assert needle in str(e), (
                            f"{token}'s refusal does not name {needle!r}: {e}")
            refused.append(f"{token}:{'own' if moved_plan == best else 'rival'}.{column}")
    return ("a dollar cell that disagrees with its cents twin is refused by name, in a "
            "rival's row and in the household's own (" + ", ".join(refused) + ")")


@case
def case_a_matrix_without_cents_fields_is_refused_naming_the_generator():
    """A data/battery_plan_matrix.json written before issue #177 has dollar
    cells and no _cents twins. That is not a bad cell the ranking can report
    as "None"; it is an artifact of the wrong shape, and the refusal has to
    tell whoever reads it what to run. Every ranked token refuses it, and the
    message names the token, the missing field and battery_plan_matrix.py.

    SECTION 4's TABLE TOO, and not with a KeyError (round-2 review). The row
    builder report_blocks._s4_battery_plan_rows orders the rival rows on the
    cents, and a bare key read there raised KeyError -- which escapes
    generate_report's data builders as a traceback, before the manifest is
    written, so the remedy never printed. It now goes through
    rt._bpm_column_cents and refuses the same way the tokens do.

    A matrix missing the cents on ONE row only -- the household's own -- is
    the same refusal, and the message may not call it a pre-#177 artifact:
    that shape is a hand edit, and the wording covers both."""
    import report_blocks as rb
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    root = rt._json("battery_plan_matrix.json")
    stripped = {p: {k: v for k, v in row.items() if not k.endswith("_cents")}
                for p, row in plans.items()}
    assert all("no_battery_cents" not in row for row in stripped.values())
    own_only = dict(plans)
    own_only[best] = stripped[best]
    refused = []
    readers = (("S4_ROW_CLASS", lambda: rt.resolve_token("S4_ROW_CLASS")),
               ("S4_VERDICT_SHORT", lambda: rt.resolve_token("S4_VERDICT_SHORT")),
               ("PLAN_MARGIN_VS_RUNNER_UP",
                lambda: rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP")),
               ("S4 battery-plan rows", rb._s4_battery_plan_rows))
    for shape, matrix, expected in (("no row has cents", stripped, readers),
                                    ("only the household's own row lacks them", own_only,
                                     readers[:3])):
        with _stub_plan(best, provider), _swapped(root, "plans", matrix):
            for name, read in expected:
                try:
                    value = read()
                    raise AssertionError(
                        f"{name} ranked a matrix where {shape}: {value!r}")
                except KeyError as e:
                    raise AssertionError(
                        f"{name} raised a bare KeyError ({e}) on a matrix where {shape} "
                        "instead of the named refusal; generate_report would die with a "
                        "traceback and never print the remedy")
                except SystemExit as e:
                    msg = str(e)
                    for needle in (name, "_cents", "regenerate", "battery_plan_matrix.py"):
                        assert needle.lower() in msg.lower(), (
                            f"{name}'s refusal of a matrix where {shape} does not say "
                            f"{needle!r}: {msg}")
                    assert "is None" not in msg, (
                        f"{name} reports a missing field as a None it cannot settle "
                        f"instead of naming the remedy: {msg}")
                    assert "predates" not in msg, (
                        f"{name} asserts the whole artifact predates #177, which is false "
                        f"when {shape}: {msg}")
                refused.append(f"{name} ({shape})")
    return ("a matrix with no _cents fields is refused naming battery_plan_matrix.py as "
            "the remedy, by the tokens and by section 4's row builder, with no KeyError ("
            + ", ".join(refused) + ")")


@case
def case_section_4s_rival_rows_and_the_runner_up_follow_the_cents_when_the_dollars_tie():
    """Two rankings outside the verdict read the same column: section 4's
    table orders its rival rows, and section 0 names the runner-up. Both used
    to sort on the whole-dollar cell, so two rivals that round to the same
    dollar took the JSON's key order, which is not a ranking. Priced 100.49
    first in key order and 100.40 second, the dollar cells tie at 100 and the
    cents put the second rival first. The row order and the runner-up pick
    have to follow the cents; the cells printed stay the whole-dollar ones.

    Confirmed against this household's own rows as well: its real rivals are
    dollars apart, so the committed section 4 rows are the same either way."""
    import report_blocks as rb
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    first, second = [p for p in plans if p in (near, far)]   # JSON key order
    cells = {best: (50, 50), first: (100.49, 100.49), second: (100.40, 100.40)}
    cells.update({p: (9_000, 9_000) for p in rest})
    with _stub_plan(best, provider):
        live = rb._s4_battery_plan_rows()
        with _matrix_priced(plans, cells):
            assert plans[first]["no_battery"] == plans[second]["no_battery"] == 100, (
                "the fixture no longer stores the two rivals on the same dollar cell")
            assert plans[first]["no_battery_cents"] > plans[second]["no_battery_cents"]
            rows = rb._s4_battery_plan_rows()
            assert rows.index(f">{second}<") < rows.index(f">{first}<"), (
                f"section 4 orders {first} (100.49) ahead of {second} (100.40): the row "
                f"order followed the JSON key order, not the cents: {rows}")
            assert rows.count("<td>$100</td>") == 4, rows   # both rivals, both columns
            runner_up = rt._runner_up("PLAN_MARGIN_VS_RUNNER_UP", "no_battery")[0]
            assert runner_up == second, (
                f"the runner-up is {runner_up}, not the rival the cents rank second")
            assert rt._bpm_rivals("PLAN_MARGIN_VS_RUNNER_UP", "no_battery") == {second}
        assert rb._s4_battery_plan_rows() == live, "the synthetic matrix leaked out"
    # This household's own rows: the cents and the dollars rank the rivals the
    # same way, so the published table is unchanged by the re-ranking.
    rivals = [p for p in plans if p != best]
    by_dollars = sorted(rivals, key=lambda p: plans[p]["no_battery"])
    by_cents = sorted(rivals, key=lambda p: plans[p]["no_battery_cents"])
    assert by_dollars == by_cents, (by_dollars, by_cents)
    return (f"with {first} at 100.49 ahead of {second} at 100.40 in key order, section 4 "
            f"lists {second} first and the runner-up is {second}; this household's own "
            f"rows rank the same on cents and dollars ({by_cents})")


# ---------------------------------------------------------------------------
# THE RUNNER-UP IS A PER-COLUMN FACT (issue #141 adversarial review).
#
# S4_VERDICT_SHORT quotes two figures, one per column of
# battery_plan_matrix.json, and both used to be measured against ONE rival --
# whichever plan the NO-BATTERY column ranked second. Nothing makes that plan
# the with-battery runner-up: a battery is worth a different amount under each
# tariff, which is the entire reason the matrix has three plans in it.
#
# The reviewer's worked example, all three plans valid:
#
#     plan   no_battery  with_battery
#     us            100           100
#     B             200           200
#     C             300           150
#
# The household is cheapest in BOTH columns, so the Yes/No prefix was never
# wrong. What was wrong is the clause after it: measured against B in both
# columns it reads "leaves us's lead over B at $100/yr against $100/yr", while
# the margin that actually survives the battery is $50 -- against C, the plan
# the with-battery column really ranks second. Push C down and the published
# sentence keeps reporting a $100 lead over a near-tie.
#
# The cases below hold three things. The rival is picked inside each column.
# When the two columns disagree about who it is, the sentence says the rival
# changed and names both, rather than quoting a gap change that is partly a
# change of opponent. And a TIE between two rivals in one column may not
# invent that identity change out of key order -- which is why the decision is
# taken on _bpm_rivals' SETS.
#
# Every scenario here leaves the household cheapest in both columns on
# purpose. A household the matrix ranks second is refused by the win-row
# tokens (issue #178, case_a_household_the_matrix_ranks_second_is_refused_by_name),
# and these cases sweep the WHOLE token set expecting it to resolve, so mixing
# the two would assert against that refusal instead of alongside it.
# ---------------------------------------------------------------------------
def _matrix_trio():
    """(plans, household plan, its no-battery rival, a third plan, everyone
    else). The per-column cases need three plans: two rivals, so the columns
    can rank them differently."""
    plans, best, near, rest = _matrix_pair()
    assert rest, ("data/battery_plan_matrix.json prices two plans; the per-column "
                  "runner-up cases need a third to move between the columns")
    return plans, best, near, rest[0], rest[1:]


@case
def case_the_runner_up_is_taken_per_column_not_reused_from_the_no_battery_one():
    """THE REVIEWER'S CASE, verbatim: us 100/100, B 200/200, C 300/150.

    B is the no-battery runner-up and C is the with-battery one, and the true
    margin therefore HALVES, from $100/yr to $50/yr. Reusing B for both
    columns published "the battery leaves ...'s lead over B at $100/yr against
    $100/yr" -- a reassuring sentence about a margin that had contracted, sat
    directly above a matrix rendering all six of these cells.

    The fix is not to quote C's number under B's name; that would still be one
    named opponent and two figures, which reads as a margin moving against a
    fixed rival. Both rivals are named and the sentence states that the
    cheapest rival changed, because that is the fact -- and the widens /
    narrows / leaves verbs are dropped, since each of them asserts something
    about a single comparison priced twice and this is not one."""
    _require_household()   # _resolve_every_token() below needs the archive
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = _resolve_every_token()
        cells = {best: (100, 100), near: (200, 200), far: (300, 150)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        # The prefix was never the defect and must not become one: this
        # household is cheapest in both columns, so the answer is still "No".
        assert no_batt == with_batt == {best}, (
            f"this case no longer has {best} cheapest in both columns (without a battery "
            f"{sorted(no_batt)}, with one {sorted(with_batt)}), so it is testing the "
            f"prefix rather than the clause after it")
        assert value.startswith("No —"), (
            f"S4_VERDICT_SHORT stopped answering 'No' for a household the matrix prices "
            f"cheapest in both columns: {value}")
        # THE DEFECT, by its exact published signature.
        assert "$100/yr against $100/yr" not in value, (
            f"S4_VERDICT_SHORT still measures both columns against {near}: {far} is the "
            f"with-battery runner-up at 150 against this household's 100, so the margin "
            f"that survives the battery is $50/yr, not $100/yr: {value}")
        assert f"lead over {near}" not in value and f"lead over {far}" not in value, (
            f"S4_VERDICT_SHORT still frames one named opponent across both columns while "
            f"the runner-up changes from {near} to {far}: {value}")
        for verb in ("widens", "narrows", "leaves"):
            assert verb not in value, (
                f"S4_VERDICT_SHORT says the battery {verb!r} a gap it measured against "
                f"{near} without one and {far} with one -- a change of opponent is not a "
                f"change in a lead: {value}")
        assert "the cheapest rival changes with the battery" in value, (
            f"S4_VERDICT_SHORT does not tell the reader the comparison changed identity: "
            f"{value}")
        assert f"{best} leads {near} by $100/yr without one" in value, (
            f"S4_VERDICT_SHORT does not state the no-battery standing against the "
            f"no-battery runner-up {near}: {value}")
        assert f"leads {far} by $50/yr with one" in value, (
            f"S4_VERDICT_SHORT does not state the with-battery standing against the "
            f"with-battery runner-up {far}, whose 150 leaves a $50/yr margin: {value}")
        words = _assert_within_density_cap(
            "S4_VERDICT_SHORT", value, "a runner-up that changes between the columns")
        # "this sentence renders" is not the claim that matters; "this
        # household still gets a report" is.
        with _matrix_priced(plans, cells):
            _resolve_every_token()
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"the runner-up is taken in each column ({near} without a battery, {far} with "
            f"one), so a margin that halves is published as $100/yr then $50/yr against "
            f"two named rivals rather than as $100/yr twice ({value!r}, {words}w)")


@case
def case_a_changed_runner_up_that_leaves_a_near_tie_does_not_read_as_reassuring():
    """The same shape pushed to where the old sentence did the most damage:
    us 100/100, B 200/200, C 300/101.

    The household still wins both columns, and against B the battery still
    "leaves the lead at $100/yr against $100/yr". Against C -- the plan the
    with-battery column actually ranks second -- the margin is ONE STORED
    DOLLAR, which two roundings cannot even separate from a cent. The old
    sentence reported a comfortable, unchanged $100 lead over a household that
    is very nearly tied on the plan it is being told to keep.

    The near-tie also has to be hedged rather than quoted: "$1/yr" is a
    precision two rounded cells do not carry, so the clause bounds it at
    _BPM_TIE_USD instead."""
    _require_household()   # _resolve_every_token() below needs the archive
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = _resolve_every_token()
        cells = {best: (100, 100), near: (200, 200), far: (300, 101)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        assert no_batt == with_batt == {best}, (
            f"this case no longer has {best} cheapest in both columns: "
            f"{sorted(no_batt)} / {sorted(with_batt)}")
        assert "$100/yr against $100/yr" not in value and "$100/yr to $100/yr" not in value, (
            f"S4_VERDICT_SHORT reports an unchanged $100/yr lead while {far} prices "
            f"within a dollar of this household with a battery: {value}")
        for verb in ("widens", "narrows", "leaves"):
            assert verb not in value, (
                f"S4_VERDICT_SHORT attributes to the battery a gap change measured "
                f"against two different rivals: {value}")
        assert f"leads {far} by under {rt._usd0(1 + rt._BPM_TIE_USD)}/yr with one" in value, (
            f"S4_VERDICT_SHORT does not report the near-tie against {far}, or quotes a "
            f"$1/yr size two rounded cells cannot carry: {value}")
        assert "$1/yr" not in value, (
            f"S4_VERDICT_SHORT quotes a $1/yr margin off two cells that could be a cent "
            f"apart: {value}")
        words = _assert_within_density_cap(
            "S4_VERDICT_SHORT", value, "a changed runner-up leaving a near-tie")
        with _matrix_priced(plans, cells):
            _resolve_every_token()
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"a runner-up change that leaves a near-tie is published as one ({value!r}, "
            f"{words}w) rather than as an unchanged $100/yr lead over {near}")


@case
def case_one_runner_up_across_both_columns_still_reads_as_a_single_comparison():
    """THE OTHER EDGE. Per-column selection must not start announcing a
    changed rival where the rival did not change, and a TIE must not be able
    to announce one either.

    ONE RIVAL, BOTH COLUMNS. us 5000/5000, B 5100/5300: B is the runner-up
    twice, so there is a single comparison priced at two battery states and
    the widens verb is exactly the right claim. Nothing about this branch
    moves.

    A TIE IN ONE COLUMN, which is where a name-based check breaks. us
    100/100, B 200/200, C 200/150: B and C price IDENTICALLY without a
    battery, so both are runner-up there, and C is the runner-up with one.
    C is therefore a cheapest rival in BOTH columns -- one comparison, no
    identity change. Pick a single name per column instead and whichever key
    min() reaches first decides: B without a battery, C with one, and the
    sentence announces a change of rival that the artifact does not contain.
    The decision is taken on the SETS, which intersect, so it does not."""
    _require_household()   # _resolve_every_token() below needs the archive
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    seen = {}
    with _stub_plan(best, provider):
        published = _resolve_every_token()
        for label, cells, expected in (
                ("one rival, both columns",
                 {best: (5_000, 5_000), near: (5_100, 5_300)},
                 f"the battery widens {best}'s lead over {near} from $100/yr to $300/yr"),
                ("a tie in the no-battery column",
                 {best: (100, 100), near: (200, 200), far: (200, 150)},
                 f"the battery narrows {best}'s lead over {far} from $100/yr to $50/yr")):
            cells = dict(cells)
            cells.update({p: (9_000, 9_000) for p in rest if p not in cells})
            value, (no_batt, with_batt) = _s4_at(plans, cells)
            assert no_batt == with_batt == {best}, (
                f"{label}: {best} is no longer cheapest in both columns "
                f"({sorted(no_batt)} / {sorted(with_batt)})")
            assert value == f"No — {expected}", (
                f"{label}: S4_VERDICT_SHORT no longer renders the single-rival wording. "
                f"Expected 'No — {expected}', got {value!r}")
            assert "the cheapest rival changes" not in value, (
                f"{label}: S4_VERDICT_SHORT announces a change of rival that the matrix "
                f"does not contain: {value}")
            seen[label] = _assert_within_density_cap("S4_VERDICT_SHORT", value, label)
            with _matrix_priced(plans, cells):
                _resolve_every_token()
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return ("one rival across both columns keeps the single-comparison wording, and a tie "
            "between two rivals in one column does not invent a change of rival ("
            + ", ".join(f"{k} {v}w" for k, v in seen.items()) + ")")


@case
def case_an_exact_tie_in_both_columns_is_rendered_as_a_tie():
    """Two plans priced identically in both columns. There is no ranking
    change, so the heading may not say "Yes" -- and there is no ranking
    either, so it may not say "No" and hand the reader a plan choice the
    artifact never made. Both readings are claims the cells do not support;
    what the sentence has to do is render, and say the plans tie."""
    plans, best, rival, rest = _matrix_pair()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t) for t in ("S4_VERDICT_SHORT",) + _MATRIX_PLAN_TOKENS}
        cells = {best: (5_000, 4_000), rival: (5_000, 4_000)}
        cells.update({p: (9_000, 9_000) for p in rest})
        value, (no_batt, with_batt) = _s4_at(plans, cells)
        assert no_batt == with_batt == {best, rival}, (
            f"an exact tie in both columns did not read as one: without a battery "
            f"{sorted(no_batt)}, with one {sorted(with_batt)}")
        for wrong in ("Yes", "No"):
            assert not value.startswith(wrong), (
                f"S4_VERDICT_SHORT answers {wrong!r} while {best} and {rival} price "
                f"identically in both of the matrix's columns: {value}")
        assert value.count("ties") == 2, (
            f"S4_VERDICT_SHORT does not report both battery states as ties: {value}")
        # The tie renders the rest of section 4 too: joint-cheapest is cheapest,
        # and the win row's own gate must not read a tie as a household on the
        # wrong plan.
        with _matrix_priced(plans, cells):
            for token in _MATRIX_PLAN_TOKENS:
                assert rt.resolve_token(token).strip(), (
                    f"{token} refused a matrix that ties {best} with {rival}")
        words = _assert_within_density_cap("S4_VERDICT_SHORT", value, "an exact tie")
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"an exact tie in both columns renders as a tie rather than as a Yes or a No "
            f"({value!r}, {words}w)")


def _s4_row_line():
    """report-template.html's own section 4 household-row line.

    Read out of the template rather than written down, so a case that talks
    about "the row" is talking about the markup that ships. The two markers
    are the cell every version of this row has carried and the <tr> it opens
    with -- section 4's remaining rows are a TODO block, not a token line."""
    lines = [ln for ln in rt.TEMPLATE.read_text().splitlines()
             if "{{BEST_PLAN_BATT_MODELED}}" in ln and ln.lstrip().startswith("<tr")]
    assert len(lines) == 1, (
        f"report-template.html carries {len(lines)} section 4 household rows, not one; "
        "the cases below cannot say which markup a reader is shown")
    return lines[0]


def _s4_row_markup(plans, cells):
    """(rendered row markup, its class) for section 4's household row against
    a synthetic matrix.

    Filled the way generate_report.py fills it -- every {{TOKEN}} replaced by
    its resolved value, HTML-escaped -- so what these cases read is the markup
    a reader would be shown and not a paraphrase of it. The class is returned
    separately because that is the claim the row makes about this household;
    the markup is returned so a case can prove two states are actually
    DISTINGUISHABLE rather than merely computed differently."""
    line = _s4_row_line()
    with _matrix_priced(plans, cells):
        rendered = re.sub(
            r"\{\{([A-Z0-9_]+)\}\}",
            lambda m: _htmllib.escape(rt.resolve_token(m.group(1)), quote=True), line)
        return rendered, rt.resolve_token("S4_ROW_CLASS")


def _s4_plan_cell(markup):
    """The text of a household row's first cell -- the plan name and, in
    every state but a sole win, the badge after it -- exactly as the rendered
    markup carries it (still HTML-escaped, so a caller compares it against an
    escaped expectation). Section 3's case reads its row through this too.

    Read out of the row rather than off the token, because the claim the
    issue #198 guard exists for is what the DOCUMENT says: a badge only a
    stylesheet paints is not here, and a cell carrying a tag (a span, a
    comment) is not text either. So the cell must be a single text node."""
    m = re.match(r'<tr class="[^"]*"><td>(.*?)</td>', markup)
    assert m, f"section 4's household row does not open with a plan-name cell: {markup}"
    cell = m.group(1)
    assert "<" not in cell, (
        f"section 4's plan-name cell carries markup, so its badge is not plain text a "
        f"reader mode or a text guard reads as one string: {cell!r}")
    return cell


# THE STYLESHEET GUARD (issue #198 review). A literal check for
# `tr.<state> td:first-child::after` passed five rewrites of the same claim:
# `tr.trails>td:first-child::after`, `:after` with one colon,
# `td:first-of-type::after`, `::before`, and the same words under a new
# selector. The axis the issue names is not one selector string; it is any
# CSS `content:` rule that puts words on a row-state class. So every rule in
# the <style> block that sets `content:` is parsed -- selector and value --
# and held to two rules: its selector names no row-state class in any form,
# and its value is one of the decorations the template actually draws.
_STYLE_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_STYLE_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_CONTENT_DECL_RE = re.compile(
    r"""content\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^;}]*)""")
# Every content: value the shipped template draws, by value: the two
# open/closed affordances, the .rec/.note eyebrow labels and their
# data-label override. Words on a row are none of these.
_DECORATIVE_CONTENT = frozenset({'"▸"', '"▾"', '"Verdict"', '"Caveat"', "attr(data-label)"})


def _template_style():
    """The one <style> block report-template.html carries, comments removed
    (a comment may mention a class name without painting anything)."""
    text = rt.TEMPLATE.read_text()
    assert text.count("<style>") == 1, (
        f"report-template.html carries {text.count('<style>')} <style> blocks; the "
        "stylesheet guards read one")
    return _STYLE_COMMENT_RE.sub("", text[text.index("<style>"):text.index("</style>")])


def _style_content_rules(style):
    """[(selector, content value)] for every rule in `style` that sets
    content:, whether it sits at the top level or inside an @media block.
    The selector is the text between the previous brace and this rule's
    opening brace, whitespace-collapsed."""
    out = []
    for m in _STYLE_RULE_RE.finditer(style):
        selector, decls = " ".join(m.group(1).split()), m.group(2)
        c = _CONTENT_DECL_RE.search(decls)
        if c:
            out.append((selector, c.group(1).strip()))
    return out


def _assert_no_row_badge_in_stylesheet(style, classes, section):
    """Refuse any content: rule whose selector names one of `classes` (the
    row-state classes) in any form -- a class selector, a descendant or
    child combinator, an attribute selector, a single- or double-colon
    pseudo-element -- and any content: value that is not a known decoration.
    Both, because a claim can be moved to a selector that names no class."""
    rules = _style_content_rules(style)
    assert rules, (
        "the stylesheet parser found no content: rule at all, so it sees nothing and "
        "would pass a badge it cannot read")
    for selector, content in rules:
        named = [cls for cls in classes
                 if re.search(rf"(?<![\w-]){re.escape(cls)}(?![\w-])", selector)]
        assert not named and "[class" not in selector, (
            f"report-template.html's stylesheet writes {content} onto a {section} row "
            f"state ({selector!r} names {named or 'a class attribute'}). Generated "
            "content never enters the DOM, so no guard can read the claim; the badge is "
            "the row's own cell text and the stylesheet must not carry any")
        assert content in _DECORATIVE_CONTENT, (
            f"report-template.html's stylesheet writes {content} through {selector!r}, "
            f"which is not one of the decorations it draws {sorted(_DECORATIVE_CONTENT)}; "
            "words a reader is shown belong in the document's text, where the guards "
            "can read them")


# What the reviewer got past the literal check, each a whole rule that a
# browser would paint as a badge on a row. The guard must refuse every one.
_BADGE_BYPASS_RULES = (
    'tr.trails>td:first-child::after{content:"not the cheapest in either column"}',
    'tr.trails td:first-child:after{content:"not the cheapest in either column"}',
    'tr.trails td:first-of-type::after{content:"not the cheapest in either column"}',
    '.trails td:first-child::before{content:"not the cheapest in either column"}',
    'tr.trails td:first-child::after{content:"cheapest in neither column"}',
    'tr[class~="s3-trails"] td:first-child::after{content:"not the cheapest here"}',
    '@media print{tr.tie td:first-child::after{content:"tied"}}',
    '.plan-badge::after{content:"tied for cheapest in both columns"}',
)


@case
def case_the_stylesheet_badge_guard_reads_every_selector_form():
    """The guard above is proven on the bypasses, not assumed: each of
    _BADGE_BYPASS_RULES appended to the shipped stylesheet must be refused,
    and the shipped stylesheet on its own must pass -- for both sections'
    class vocabularies."""
    style = _template_style()
    classes = tuple(rt._S3_ROW_CLASSES) + tuple(rt._S4_ROW_CLASSES)
    _assert_no_row_badge_in_stylesheet(style, classes, "section 3/4")
    refused = []
    for rule in _BADGE_BYPASS_RULES:
        try:
            _assert_no_row_badge_in_stylesheet(style + "\n  " + rule + "\n", classes,
                                               "section 3/4")
        except AssertionError as e:
            refused.append(f"{rule.split('{')[0]} -> refused ({str(e)[:40]}...)")
        else:
            raise AssertionError(
                f"the stylesheet guard passed a badge written as {rule!r}; a claim in "
                "that form would reach a reader with nothing here reading it")
    return (f"the shipped stylesheet passes and all {len(refused)} badge bypasses are "
            "refused: " + "; ".join(refused))


# The two families of row class, written out rather than pattern-matched off
# the names. Section 4's row states BOTH columns' standings (report_tokens
# ._S4_ROW_CLASSES), and the question the heading is read against is the
# coarser one -- is this household's plan a cheapest plan in BOTH columns, or
# is it beaten in at least one? A rename in the module leaves these stale, and
# stale is loud here: the assertions below are iffs, so a class in neither
# family fails them the first time a matrix reaches it.
# case_section_4s_row_class_is_a_state_the_stylesheet_can_paint holds the two
# to a partition of the whole vocabulary.
_S4_CHEAPEST_IN_BOTH = ("tie", "tie-win", "win")
_S4_BEATEN_IN_A_COLUMN = ("trails", "trails-tie", "trails-win")


@case
def case_the_heading_never_denies_a_winner_the_row_beneath_it_names():
    """ISSUE #141, REVIEW ROUND 3, FINDING 1. The heading and the markup
    directly under it are one statement, and they may not disagree.

    Section 4's h2 carries {{S4_VERDICT_SHORT}} and the table immediately
    below it opens the household's row with {{S4_ROW_CLASS}}, which states
    both columns' standings. So the two are read together, and a row painted
    as the sole winner under a heading that declines to name one is a page
    that contradicts itself in adjacent lines.

    The old branch produced exactly that. It answered "Too close to call"
    whenever EITHER column named more than one cheapest plan, on the premise
    that "no single plan is the answer at both states" -- which is not what a
    tie in one column means. us 100/100, B 100/200, C 300/300 ties B without a
    battery and beats it outright with one: the household's plan is never
    beaten, the row names it as a cheapest plan, and the heading said the
    question could not be settled. The mirror (a tie WITH the battery instead)
    is the same shape, and so is a matrix that ties against a different rival
    in each column.

    What actually leaves the plan choice unanswerable is TWO OR MORE plans
    cheapest-or-joint-cheapest at both states -- which, since every member of
    a cheapest set stores that column's minimum, means two plans priced
    identically in BOTH columns. Then the row can say "tie" and the heading
    can decline to pick, and neither is claiming more than the cells carry.

    WHAT ISSUE #178 CHANGED HERE. The row used to be fixed `class="win"`
    markup whose only gate was the three cells refusing, so "does the row
    render" was the whole of what it asserted. It now states both columns'
    standings, off _bpm_cheapest -- the same helper the heading decides on --
    so the agreement this case drives is between two readings of one ranking,
    which is why it is an invariant and not a pair of expected strings.

    Driven over five matrices, in every direction:

      * a class in _S4_CHEAPEST_IN_BOTH <-> the plan is
        cheapest-or-joint-cheapest at BOTH battery states. None of them may be
        painted over a plan some column prices above a rival, and no class in
        _S4_BEATEN_IN_A_COLUMN may be painted over one that is never beaten;
      * class "win" -> the plan is the ONLY such plan -> the heading must
        answer "No";
      * "Too close to call" -> some OTHER plan is cheapest at both states
        too, so the row may not read "win";
      * "Yes" -> no plan is cheapest at both, so this household's is not
        either, and the row must say it is beaten in a column.

    The two mixed scenarios are the ones issue #178's review added. A plan
    joint-cheapest in one column and alone cheapest in the other is "tie-win",
    not "tie": the heading answers "No" for it, and a badge reading "ties for
    cheapest" over a column it wins outright would be the same false claim
    this case exists to stop, one state along. Likewise the changed winner is
    "trails-win" and not "trails" -- it is beaten with a battery and alone
    cheapest without one, so "not the cheapest" would be false about the
    no-battery cell sitting two columns to its right.
    """
    template = rt.TEMPLATE.read_text()
    heading = [ln for ln in template.splitlines()
               if "{{S4_VERDICT_SHORT}}" in ln and 'id="s4"' in ln]
    assert len(heading) == 1 and "{{S4_ROW_CLASS}}" in _s4_row_line(), (
        f"report-template.html no longer pairs one section 4 heading carrying "
        f"S4_VERDICT_SHORT ({len(heading)} found) with a household row whose class is a "
        f"token ({_s4_row_line()!r}); this case's premise has to be re-derived")

    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    # (label, cells, the answer the cells settle, the class they settle)
    scenarios = (
        ("joint-cheapest without a battery, alone with one",
         {best: (100, 100), near: (100, 200), far: (300, 300)}, "No", "tie-win"),
        ("alone without a battery, joint-cheapest with one",
         {best: (100, 100), near: (200, 100), far: (300, 300)}, "No", "tie-win"),
        ("a different rival tying in each column",
         {best: (100, 100), near: (100, 200), far: (300, 100)}, "No", "tie"),
        ("two plans priced identically in both columns",
         {best: (100, 100), near: (100, 100), far: (300, 300)},
         "Too close to call", "tie"),
        ("a winner the battery really changes",
         {best: (100, 300), near: (200, 100), far: (300, 200)}, "Yes", "trails-win"),
    )
    seen = {}
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t)
                     for t in ("S4_VERDICT_SHORT", "S4_ROW_CLASS") + _MATRIX_PLAN_TOKENS}
        for label, cells, expected, expected_class in scenarios:
            cells = dict(cells, **{p: (9_000, 9_000) for p in rest})
            value, (no_batt, with_batt) = _s4_at(plans, cells)
            at_both = no_batt & with_batt
            markup, row_class = _s4_row_markup(plans, cells)
            # THE CONTRADICTION FIRST, and tested as a contradiction: the row
            # asserts a standing, the heading asserts another. The expected-
            # answer assertions below are an anchor on the scenarios
            # themselves -- they would catch a wording change too, which is
            # not what this case is for.
            assert (row_class in _S4_CHEAPEST_IN_BOTH) == (best in at_both), (
                f"{label}: section 4's row reads {row_class!r} while the matrix prices "
                f"{sorted(at_both)} cheapest at both battery states -- a row calling this "
                f"household's plan a cheapest plan when some column prices a rival below "
                f"it (or refusing to when none does) is the claim {best}'s own cells "
                f"cannot carry: {markup}")
            if row_class == "win":
                assert at_both == {best} and value.startswith("No"), (
                    f"{label}: section 4 paints {best}'s row as the sole winner while the "
                    f"heading above it reads {value!r} and the matrix prices "
                    f"{sorted(at_both)} cheapest at both states: {markup}")
            if value.startswith("Too close to call"):
                assert len(at_both) > 1 and row_class != "win", (
                    f"{label}: the heading declines to name a best plan while the matrix "
                    f"prices exactly {sorted(at_both)} cheapest at both battery states, "
                    f"and section 4's row reads {row_class!r}: {value}")
            if value.startswith("Yes"):
                assert not at_both and row_class in _S4_BEATEN_IN_A_COLUMN, (
                    f"{label}: the heading reports a changed winner while "
                    f"{sorted(at_both)} is cheapest at both states and the row reads "
                    f"{row_class!r}: {value}")
            assert value.startswith(expected), (
                f"{label}: the matrix prices {sorted(no_batt)} cheapest without a battery "
                f"and {sorted(with_batt)} with one, which answers {expected!r}, but "
                f"S4_VERDICT_SHORT published: {value}")
            assert row_class == expected_class, (
                f"{label}: the same two columns make the row {expected_class!r}, not "
                f"{row_class!r}: {markup}")
            seen[label] = (value, sorted(at_both), row_class,
                           _assert_within_density_cap("S4_VERDICT_SHORT", value, label))
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    answers = ", ".join(f"{label}: {v.split(' —')[0]!r}/{c}"
                        for label, (v, _s, c, _w) in seen.items())
    return (f"across {len(seen)} matrices the section 4 heading and the household row "
            f"beneath it never contradict each other -- a 'win' row is only ever painted "
            f"over a plan uniquely cheapest at both battery states and always answered "
            f"'No', 'Too close to call' never sits over one, and 'Yes' always sits over a "
            f"row that names a column this plan is beaten in ({answers})")


@case
def case_a_non_finite_rival_cell_refuses_rather_than_electing_a_runner_up_by_key_order():
    """ISSUE #141, REVIEW ROUND 3, FINDING 2. The runner-up is picked with
    min() over the rival cells, and every comparison a nan takes part in is
    False -- so a nan does not lose that min(), it hands the decision to the
    order battery_plan_matrix.json happens to store its keys in.

    Which is a published figure, not a crash. PLAN_MARGIN_VS_RUNNER_UP guards
    only the MARGIN it ends up with, and that margin is perfectly finite when
    the nan cell loses the min() to a plan listed ahead of it: the token then
    prints a real dollar figure measured against a plan the artifact never
    ranked second. Same matrix, rivals stored the other way round, and the nan
    wins the min() instead, the margin comes out nan and the token refuses.
    One artifact, two answers, chosen by JSON key order.

    So this case drives BOTH orders and demands the SAME answer -- a refusal
    naming the token, the poisoned plan and the column -- for every rival, in
    every column the tokens rank, on nan and on inf. An ordering that changes
    what the report says is the defect, and asserting only "it refuses" in one
    order would not see it.

    The household's OWN cell is not swept here: it is not ranked against
    anything by these helpers, and both callers subtract it and check the
    difference by name, which is driven in
    case_every_comparison_in_this_module_refuses_a_non_finite_input."""
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    root = rt._json("battery_plan_matrix.json")
    base = {best: (100, 100), near: (200, 200), far: (300, 300)}
    base.update({p: (9_000, 9_000) for p in rest})
    columns = ("no_battery", "with_battery")
    # PLAN_MARGIN_VS_RUNNER_UP ranks the no-battery column only;
    # S4_VERDICT_SHORT ranks both.
    ranked = {"no_battery": ("PLAN_MARGIN_VS_RUNNER_UP", "S4_VERDICT_SHORT"),
              "with_battery": ("S4_VERDICT_SHORT",)}
    refusals = []
    with _stub_plan(best, provider):
        published = {t: rt.resolve_token(t)
                     for t in ("S4_VERDICT_SHORT", "PLAN_MARGIN_VS_RUNNER_UP")}
        for bad in (float("nan"), float("inf")):
            for poisoned in (near, far):
                others = [p for p in base if p != poisoned]
                for column in columns:
                    cells = dict(base)
                    cells[poisoned] = tuple(bad if c == column else v
                                            for c, v in zip(columns, cells[poisoned]))
                    # The order that HIDES the defect first: with a finite
                    # rival ahead of it, the nan loses the min() and the
                    # margin that reaches the caller's guard is a real number.
                    for order in (others + [poisoned], [poisoned] + others):
                        priced = {p: _matrix_row(*cells[p]) for p in order}
                        with _swapped(root, "plans", priced):
                            for token in ranked[column]:
                                try:
                                    value = rt.resolve_token(token)
                                except SystemExit as e:
                                    msg = str(e)
                                    assert token in msg and poisoned in msg and column in msg, (
                                        f"{token} refused a {bad!r} in {poisoned}'s {column} "
                                        f"cell without naming the token, the plan and the "
                                        f"column: {msg}")
                                    refusals.append((token, poisoned, column, order[0]))
                                else:
                                    raise AssertionError(
                                        f"{token} published {value!r} while {poisoned}'s "
                                        f"{column} cell is {bad!r}: min() cannot rank a "
                                        f"non-finite cell, so which plan it elects as the "
                                        f"runner-up was decided by the order the artifact "
                                        f"stores its keys in ({order}) -- and the figure "
                                        f"published is measured against whichever plan that "
                                        f"happened to be")
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    orders = {o for _t, _p, _c, o in refusals}
    return (f"a non-finite rival cell refuses in {len(refusals)} sweeps -- 2 poison values x "
            f"2 rivals x the columns each token ranks x both key orders ({len(orders)} "
            f"leading keys) -- rather than letting min() elect a runner-up the artifact "
            f"never ranked")


@case
def case_a_household_the_matrix_ranks_second_still_gets_a_whole_report():
    """THE REVIEWER'S REGRESSION CASE, AND WHAT IT COST UNTIL ISSUE #178.

    A household whose plan stores $101 against a rival's $100 is on the
    second-cheapest plan -- the rounding is monotone, so the rival's bill was
    strictly lower. Section 4's three cells used to refuse for exactly that
    household, because the row they sit in was fixed `<tr class="win">`
    markup and no figure rendered into a cell makes that assertion true.

    A refusal is not a missing sentence. generate_report.py folds every
    token-resolution failure into `failures`, and failures stop the run
    before any index.html is written -- so a household the matrix ranked
    second in EITHER column got NO REPORT AT ALL, over three cells that are
    simply its own modeled bills. The claim that matters is not "this
    sentence renders", it is "this household gets a report", so this case
    sweeps the WHOLE token set and catches BaseException: SystemExit is the
    refusal, and an `except Exception` walks straight past it.

    Widening "cheapest" to a $1.00 band was the other way out and it is the
    wrong one -- it puts a plan the artifact ranks SECOND into the winners'
    set, which under the old markup published the runner-up as the winner.
    Order survives the rounding exactly; what changed is that the row can now
    say where the plan stands, so nothing has to be widened and nothing has
    to be refused.

    THE ROW SAYS "trails-win", not "trails". The dollar moves ONE column, so
    this household is beaten in that column and still alone cheapest in the
    other, and "trails" would badge the row "not the cheapest in either
    column" beside a cell that is the cheapest in its own. Naming the pair is
    the fix; taking the weaker of the two was the same false-markup defect
    with one column's claim missing.

    Both directions are driven, in both columns. A dollar the OTHER way is
    the same rounding and the same $1, and it must leave the row reading
    "win": the class has to track the ranking, not merely avoid claiming one.
    And the three cells must not MOVE when a rival's cell does -- they are
    this household's own row, and a runner-up's bills are as real as a
    winner's."""
    _require_household()
    plans, _best, _rival, _rest = _matrix_pair()
    provider, cheapest, _priced = _plan_ranking_inputs()
    assert cheapest in plans, (
        f"the CSV's cheapest plan {cheapest!r} is not priced in "
        f"battery_plan_matrix.json ({sorted(plans)}); this case cannot drive the gate")
    rivals = [p for p in plans if p != cheapest]
    rival = min(rivals, key=lambda p: plans[p]["no_battery"])
    seen = {}
    with _stub_plan(cheapest, provider):
        published = _resolve_every_token()
        assert published["S4_ROW_CLASS"] == "win", (
            "this checkout's matrix does not price the household's plan alone cheapest in "
            f"both columns ({published['S4_ROW_CLASS']!r}), so a $1 move cannot make it "
            "second; this case cannot be driven here")
        for column, phrase in rt._BPM_COLUMNS:
            for label, offset in (("a dollar below", -1), ("a dollar above", 1)):
                moved = dict(zip(("no_battery", "with_battery"),
                                 (plans[rival]["no_battery"], plans[rival]["with_battery"])))
                # offset -1 puts the RIVAL a dollar under this household, which
                # makes the household second; +1 leaves the household ahead.
                moved[column] = plans[cheapest][column] + offset
                with _matrix_priced(plans, {rival: (moved["no_battery"],
                                                    moved["with_battery"])}):
                    # THE WHOLE SET, both directions: a household second in
                    # one column must lose nothing a household first keeps.
                    rendered = _resolve_every_token()
                    cheapest_set = rt._bpm_cheapest("S4_ROW_CLASS", column)
                    markup, row_class = _s4_row_markup(plans, {})
                if offset > 0:
                    assert cheapest_set == {cheapest}, (
                        f"a rival priced $1 ABOVE this household did not leave it alone "
                        f"cheapest in the {column} column: {sorted(cheapest_set)}")
                    assert set(rendered) == set(published), (
                        "the token set is not the same for a household the matrix still "
                        f"ranks first: {sorted(set(published) - set(rendered))}")
                    assert row_class == "win" and 'class="win"' in markup, (
                        f"a rival priced $1 ABOVE this household left section 4's row "
                        f"reading {row_class!r}; the class has to track the ranking, not "
                        f"merely avoid claiming one: {markup}")
                    seen[f"{column} {label}"] = f"{row_class} row, whole report"
                    continue
                assert cheapest_set == {rival}, (
                    f"a rival priced $1 BELOW this household is the cheaper bill -- "
                    f"round() is monotone -- but the {column} column's cheapest set is "
                    f"{sorted(cheapest_set)}")
                # 1. THE REPORT SURVIVES. _resolve_every_token above already
                #    asserts it by name for every token; this records which
                #    ones the old gate used to take down.
                assert set(rendered) == set(published), (
                    "the token set is not the same for a household the matrix ranks "
                    f"second: {sorted(set(published) - set(rendered))}")
                # 2. THE ROW STOPS CLAIMING THE WIN, in the markup a reader
                #    is shown and not merely in a helper's return value --
                #    and stops at the truth: beaten in this column, still
                #    alone cheapest in the other.
                assert row_class == "trails-win" and 'class="trails-win"' in markup, (
                    f"section 4's row reads {row_class!r} while the matrix prices {rival} "
                    f"below {cheapest} {phrase} and {cheapest} is still alone cheapest in "
                    f"the other column: {markup}")
                assert 'class="win"' not in markup, (
                    f"section 4 still paints {cheapest} as the winner {phrase} while the "
                    f"matrix prices {rival} below it: {markup}")
                # 3. THE CELLS ARE UNCHANGED. They print this household's own
                #    row, which a rival's cell does not touch.
                for token in _MATRIX_PLAN_TOKENS:
                    assert rendered[token] == published[token], (
                        f"{token} moved from {published[token]!r} to {rendered[token]!r} "
                        f"when {rival}'s {column} cell did; it prints {cheapest}'s row")
                # 4. And this module's own sentence about the same cells says
                #    the same thing the row now says.
                assert "trails" in rendered["S4_VERDICT_SHORT"], (
                    f"S4_VERDICT_SHORT does not say {cheapest} trails while the matrix "
                    f"prices {rival} below it {phrase}: {rendered['S4_VERDICT_SHORT']}")
                seen[f"{column} {label}"] = f"{row_class} row, whole report"
        assert _resolve_every_token() == published, (
            "the substituted matrix cell leaked out of this case")
    return (f"a household the matrix ranks second keeps all {len(published)} tokens -- "
            f"{sorted(_MATRIX_PLAN_TOKENS)} included, which the old fixed class=\"win\" "
            "row refused -- and its row says 'trails-win' instead, cheapest in the column "
            "the dollar did not move and beaten in the one it did, in both columns and in "
            "both directions (" + ", ".join(f"{k}: {v}" for k, v in seen.items()) + ")")


# The per-column standing each of these rival cells produces, against a
# household cell of 100: strictly above it, equal to it, strictly below it.
# Written as (rival cell, the state it makes) rather than as three named
# matrices so the case below can take their PRODUCT over the two columns.
_S4_COLUMN_STATES = ((200, "win"), (100, "tie"), (50, "trails"))


# THE 9-WAY MAPPING, written out rather than derived. The module builds the
# class by sorting the two standings worst-first and joining them; a test that
# recomputed that rule would agree with whatever rule the module happened to
# have, which is not a check. These are the nine combinations of
# (no_battery standing, with_battery standing) and the class each one settles.
_S4_PAIR_CLASSES = {
    ("win", "win"): "win",
    ("win", "tie"): "tie-win",
    ("tie", "win"): "tie-win",
    ("tie", "tie"): "tie",
    ("win", "trails"): "trails-win",
    ("trails", "win"): "trails-win",
    ("tie", "trails"): "trails-tie",
    ("trails", "tie"): "trails-tie",
    ("trails", "trails"): "trails",
}


# What section 4's row is allowed to stamp on the plan-name cell, and the
# arithmetic each stamp asserts:
#
#     state -> (badge text, how many of the matrix's two columns price this
#               plan cheapest, how many of those it is the ONLY plan in)
#
# "win" carries no badge: the row is painted as the winner and there is
# nothing to qualify.
#
# Written here as well as in report_tokens deliberately. The token
# (S4_ROW_PLAN_CELL) is where the WORDS are; this table is where their
# MEANING is, and the case below checks both halves -- the text against the
# plan-name cell the row actually renders, read out of the markup and not out
# of any stylesheet (issue #198), and the two counts against the cheapest sets
# the artifact actually produces, over all nine combinations above. So a badge
# cannot be reworded without re-deriving what it claims, and cannot claim
# something the columns do not support in any combination that reaches it.
# That is the whole of what went wrong: "not the cheapest" was printed for a
# row cheapest in one column, and "ties for cheapest" for a row that won one
# outright.
_S4_ROW_BADGES = {
    "win":        (None,                                   2, 2),
    "tie-win":    ("cheapest in both columns, tied in one", 2, 1),
    "tie":        ("tied for cheapest in both columns",     2, 0),
    "trails-win": ("cheapest in one column only",           1, 1),
    "trails-tie": ("tied for cheapest in one column only",  1, 0),
    "trails":     ("not the cheapest in either column",     0, 0),
}


@case
def case_section_4s_row_class_is_a_state_the_stylesheet_can_paint():
    """ISSUE #178. The row's class is a token, which makes it a value in an
    HTML ATTRIBUTE -- a seam none of the three seam rules in this file can
    read, since every one of them is anchored on a figure (a doubled sigil, a
    lost dimension, an echoed number) and a CSS class name has no digits, no
    unit and no dimension to lose. `_SEAM_FMT_DIMENSIONS` is derived by
    running the NUMERIC formatters, so it has no entry for this token's "raw"
    by construction. Saying "the seam guard covers every token" would
    therefore be false about this one, and the honest response is the guard
    an attribute actually needs, which is this case.

    WHAT CAN GO WRONG WITH A CLASS NAME is not a malformed render. It is a
    class the stylesheet does not paint: the row then draws exactly like
    every other row, a runner-up loses the one mark that says it is this
    household's, and nothing about the page looks broken. The other thing
    that can go wrong is a class the stylesheet paints with a FALSE badge,
    which looks even less broken. So four things are held:

      1. THE VOCABULARY IS COMPLETE AND CLOSED. Every state the resolver can
         reach is a member of report_tokens._S4_ROW_CLASSES, driven over the
         PRODUCT of the three standings each column can be in -- nine
         matrices, not the six the module happens to name.
      2. THE CLASS CARRIES BOTH COLUMNS. One row spans two battery states,
         and a class reporting only one of them badges the row with a claim
         that is false about the other. Driven off _S4_PAIR_CLASSES, which is
         the 9-way mapping written out rather than recomputed from the
         module's own naming rule.
      3. EVERY MEMBER IS PAINTED, by a rule in report-template.html's own
         <style> block, and every state that is not a sole win in both
         columns SAYS SO in the row -- as TEXT in the plan-name cell, because
         a colour alone does not tell a reader where they stand. Text in the
         document and not CSS `content:` (issue #198): generated ::after
         content never enters the DOM, so it is outside every guard in this
         repo that reads markup text, and is dropped by text extraction and
         reader modes. The badge is read here out of the RENDERED ROW, and
         the stylesheet is held to carry no ::after badge for any of these
         states -- a claim in both places would be one no guard checks half
         of.
      4. EVERY BADGE IS TRUE OF EVERY COMBINATION THAT REACHES IT. Each badge
         asserts how many of the two columns price this plan cheapest and how
         many of those it is alone in (_S4_ROW_BADGES); both counts are taken
         from the cheapest sets the synthetic matrix actually produces, in all
         nine combinations, and must match the badge the reader would be
         shown.

    That fourth check is the one this case was missing. The row class used to
    be the WEAKER of the two columns' standings, which is never an overclaim
    and is still false half the time: a household alone cheapest without a
    battery and beaten with one resolved "trails", and the stylesheet stamped
    "not the cheapest" on the plan-name cell of a row whose no-battery cell is
    the cheapest in its column. The mirror -- "ties for cheapest" over a
    column won outright -- is the same defect pointing the other way. Both are
    the defect this token exists to remove, one state along, and a check that
    only asks "is the class painted" cannot see either.

    And the states must be DISTINGUISHABLE in the rendered markup: two states
    that compute differently and render identically have not been
    expressed."""
    plans, best, near, far, rest = _matrix_trio()
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    style = _template_style()

    assert set(_S4_ROW_BADGES) == set(rt._S4_ROW_CLASSES), (
        f"this case declares a badge for {sorted(_S4_ROW_BADGES)} while S4_ROW_CLASS can "
        f"reach {list(rt._S4_ROW_CLASSES)}; a state with no declared claim is a state "
        "whose badge nothing here reads")
    assert set(_S4_PAIR_CLASSES.values()) == set(rt._S4_ROW_CLASSES), (
        f"the 9-way mapping settles {sorted(set(_S4_PAIR_CLASSES.values()))}, which is not "
        f"the vocabulary {list(rt._S4_ROW_CLASSES)}")
    # The two families the heading case reads, held to a partition of the
    # vocabulary and to the badges' own arithmetic.
    families = (set(_S4_CHEAPEST_IN_BOTH), set(_S4_BEATEN_IN_A_COLUMN))
    assert families[0] | families[1] == set(rt._S4_ROW_CLASSES) and not families[0] & families[1], (
        f"_S4_CHEAPEST_IN_BOTH {sorted(families[0])} and _S4_BEATEN_IN_A_COLUMN "
        f"{sorted(families[1])} are not a partition of {list(rt._S4_ROW_CLASSES)}, so the "
        "heading case is reading a family that no longer covers every row a household can get")
    for state, (_text, cheapest_in, _sole_in) in _S4_ROW_BADGES.items():
        assert (state in _S4_CHEAPEST_IN_BOTH) == (cheapest_in == 2), (
            f"tr.{state} is in the wrong family: its badge claims this plan is cheapest in "
            f"{cheapest_in} of the two columns")

    for state in rt._S4_ROW_CLASSES:
        assert f"tr.{state} td" in style, (
            f"report-template.html's stylesheet has no rule for tr.{state}, a state "
            f"S4_ROW_CLASS can put on section 4's household row; an unpainted class is "
            f"not a broken render, it is a runner-up's row drawn like every other row")
    # The badge is DOM text, so the stylesheet may not carry one in any form:
    # a content: rule on a row state either doubles the text the cell already
    # says or, with no DOM text beside it, puts the claim back where no guard
    # reads it (issue #198). Parsed, not substring-matched -- see the guard.
    _assert_no_row_badge_in_stylesheet(style, rt._S4_ROW_CLASSES, "section 4")

    seen, markups = {}, {}
    with _stub_plan(best, provider):
        for no_cell, no_state in _S4_COLUMN_STATES:
            for with_cell, with_state in _S4_COLUMN_STATES:
                cells = {best: (100, 100), near: (no_cell, with_cell), far: (9_000, 9_000)}
                cells.update({p: (9_000, 9_000) for p in rest})
                markup, row_class = _s4_row_markup(plans, cells)
                assert row_class in rt._S4_ROW_CLASSES, (
                    f"S4_ROW_CLASS resolved {row_class!r}, which is not one of "
                    f"{list(rt._S4_ROW_CLASSES)} and so is a class nothing in "
                    f"report-template.html paints: {markup}")
                # THE BADGE, READ OUT OF THE RENDERED ROW. The plan-name cell
                # is the first <td>; what it says is what a reader, a text
                # extractor and every markup-reading guard in this repo see.
                # It must be the plan name alone in the win state (this
                # household's row, byte-identical to the published one) and
                # the plan name followed by the badge the class implies in
                # every other state -- as text, with no tag inside the cell.
                cell = _s4_plan_cell(markup)
                text, cheapest_in, sole_in = _S4_ROW_BADGES[row_class]
                expected_cell = _htmllib.escape(
                    best if text is None else f"{best} {rt._ROW_BADGE_SEPARATOR} {text}",
                    quote=True)
                assert cell == expected_cell, (
                    f"a row of class {row_class!r} renders its plan-name cell as {cell!r}; "
                    f"the class implies the badge {text!r}, so the cell must read "
                    f"{expected_cell!r} -- the badge is the claim the class stands for, "
                    f"and the two cannot disagree: {markup}")
                # THE FALSE SENTENCE FIRST, and tested as one: what the badge
                # this row would print CLAIMS, against the cheapest sets the
                # artifact produces -- not against the labels this case named
                # the matrices with. The mapping assertion below is an anchor
                # on the vocabulary; this one is the defect. A class that
                # collapses the two columns back into the weaker of them
                # passes every name check written in terms of itself and
                # fails here, which is the order that failure has to be
                # reported in.
                with _matrix_priced(plans, cells):
                    cheapest = [rt._bpm_cheapest("S4_ROW_CLASS", column)
                                for column, _phrase in rt._BPM_COLUMNS]
                claimed = (cheapest_in, sole_in)
                actual = (sum(best in s for s in cheapest),
                          sum(s == {best} for s in cheapest))
                assert actual == claimed, (
                    f"a row {no_state!r}/{with_state!r} took class {row_class!r} and is "
                    f"badged {text!r}, which claims {best} is cheapest in {cheapest_in} of "
                    f"the two columns and alone in {sole_in} of them -- the matrix prices "
                    f"it cheapest in {actual[0]} and alone in {actual[1]} "
                    f"({[sorted(s) for s in cheapest]}). The badge is published markup, so "
                    f"this is a false sentence on the page: {markup}")
                expected = _S4_PAIR_CLASSES[(no_state, with_state)]
                assert row_class == expected, (
                    f"a row {no_state!r} without a battery and {with_state!r} with one "
                    f"resolved {row_class!r}; the class states BOTH columns' standings, so "
                    f"these two settle {expected!r}")
                assert f'class="{row_class}"' in markup, (
                    f"the row's class did not reach the markup a reader is shown: {markup}")
                seen[(no_state, with_state)] = row_class
                markups.setdefault(row_class, markup)

    assert set(seen.values()) == set(rt._S4_ROW_CLASSES), (
        f"the nine matrices reached only {sorted(set(seen.values()))} of "
        f"{list(rt._S4_ROW_CLASSES)}; a state nothing can reach is a state nothing checks")
    assert len(set(markups.values())) == len(markups), (
        "two of section 4's row states render identical markup, so a tie and a win are "
        f"not actually distinguishable on the page: {markups}")
    return ("section 4's row reaches exactly the "
            f"{len(rt._S4_ROW_CLASSES)} states report-template.html paints, over the 9 "
            "combinations of the two columns' standings, and every badge it prints is true "
            f"of every combination that reaches it -- {len(markups)} distinguishable rows ("
            + ", ".join(f"{a}/{b} -> {c}" for (a, b), c in sorted(seen.items())) + ")")


@case
def case_section_4s_published_row_round_trips_into_index_html():
    """The household row, resolved at the standing the PUBLISHED household
    has, renders exactly the row index.html carries -- character for
    character, the same anti-drift equality section 3's chrome keeps.

    This is what makes issue #198 inert on the winning path: the plan-name
    cell is a token now (S4_ROW_PLAN_CELL), and in the win state it must
    resolve to the bare plan name -- no badge, no span, no separator -- or
    regenerating the report moves a published row whose ranking did not
    change. The plan is read off index.html's own row rather than assumed,
    so this runs with or without the private archive; every other cell in
    the row comes from the committed matrix artifact."""
    index_html = (rt.ROOT / "index.html").read_text()
    start = index_html.index('<h2 id="s4">')
    m = re.search(r'<tr class="([a-z0-9-]+)"><td>([^<]*)</td><td>[^<]*</td><td>[^<]*</td>'
                  r'<td>[^<]*/yr</td></tr>', index_html[start:])
    assert m, "index.html has no section 4 household row for this case to read"
    published_row, published_class, published_cell = m.group(0), m.group(1), m.group(2)
    assert published_class == "win" and published_cell == _htmllib.escape(
        published_cell, quote=True) and rt._ROW_BADGE_SEPARATOR not in published_cell, (
        "this checkout publishes a section 4 row that is not a bare sole win, so the "
        f"byte-identity this case asserts is not the state it starts from: {published_row}")
    provider, _cheapest, _priced = _plan_ranking_inputs()
    line = _s4_row_line()
    with _stub_plan(published_cell, provider):
        rendered = re.sub(
            r"\{\{([A-Z0-9_]+)\}\}",
            lambda m: _htmllib.escape(rt.resolve_token(m.group(1)), quote=True), line)
        assert rt.resolve_token("S4_ROW_PLAN_CELL") == published_cell, (
            f"S4_ROW_PLAN_CELL resolves {rt.resolve_token('S4_ROW_PLAN_CELL')!r} for the "
            f"plan index.html publishes ({published_cell}), whose row carries the bare "
            "plan name: the win state emits no badge at all")
    assert rendered == published_row, (
        "section 4's household row renders markup index.html does not carry; "
        f"regenerating the report would change the published page:\n  rendered:  "
        f"{rendered!r}\n  published: {published_row!r}")
    assert index_html.count(published_row) == 1, (
        f"index.html carries section 4's household row {index_html.count(published_row)} "
        "time(s), not once")
    return ("section 4's household row renders into index.html verbatim at the published "
            f"household's standing ({published_class!r}, cell {published_cell!r})")


@case
def case_section_4s_row_class_refuses_a_column_count_it_cannot_name():
    """The class is built from EXACTLY two standings, one per battery column,
    and the guard on that count is not decorative.

    A row class of "trails-tie" says the plan is joint-cheapest in one of the
    two columns and beaten in the other. Take _BPM_COLUMNS down to one column
    and the same code has one standing to work with; take it up to three and
    it has three. Neither produces a malformed value -- one column yields
    "win", three yield a two-part name off whichever two sorted first -- and
    both are members of the vocabulary the stylesheet paints. So the failure
    mode is a row badged with a claim about "both columns" over a table that
    no longer has two, which is exactly the class of defect this token exists
    to remove: markup asserting a standing the artifact does not carry.

    Hence a refusal, and hence this case. Both directions are driven, the
    columns are real ones (a duplicate rather than an invented key, so the
    ranking underneath still reads live cells and the refusal can only be the
    count), and the message has to name the token and the columns it was
    handed.

    STUBBED, NOT GATED, for _stub_household's reason. S4_ROW_CLASS ranks the
    household's plan, so it needs an answer for household.plan -- and
    .github/workflows/tests.yml runs this suite with no private archive, where
    a case behind _require_household() skips and cannot stop this refusal
    regressing on the one machine a bad merge is caught on. household.plan is
    the ONLY household answer under this token (the cells it ranks are the
    committed matrix's own), so substituting it is enough: the household's
    real plan where the archive is staged, the plan the matrix prices cheapest
    where it is not, and the same three resolutions exercised either way.
    Unstubbed and archive-less, every resolve_token below refuses for the
    missing archive rather than for the column count: the one at the top sits
    outside any try and aborts the whole suite before the first probe, and the
    two inside the loop would be judged on the wording of a household.yaml
    message instead of on the count they were handed."""
    _plans, cheapest_plan, _near, _rest = _matrix_pair()
    plan = rt.hh1("household.plan") if rt.hh.PATH.is_file() else cheapest_plan
    saved = rt._BPM_COLUMNS
    probes = {
        "one column": (("no_battery", "without a battery"),),
        "three columns": saved + (("no_battery", "without a battery"),),
    }
    refusals = {}
    with _stub_household({"household.plan": plan}):
        published = rt.resolve_token("S4_ROW_CLASS")
        try:
            for label, columns in probes.items():
                rt._BPM_COLUMNS = columns
                try:
                    value = rt.resolve_token("S4_ROW_CLASS")
                except SystemExit as e:
                    msg = str(e)
                    assert "S4_ROW_CLASS" in msg or "row" in msg, (
                        f"the {label} refusal does not say which token could not name a "
                        f"state: {msg}")
                    assert str(len(columns)) in msg and "_BPM_COLUMNS" in msg, (
                        f"the {label} refusal does not name the column count it was handed "
                        f"or where that count comes from: {msg}")
                    refusals[label] = msg.split(";")[0]
                else:
                    raise AssertionError(
                        f"S4_ROW_CLASS resolved {value!r} off {len(columns)} battery "
                        f"column(s). Every state in {list(rt._S4_ROW_CLASSES)} is a claim "
                        f"about the two columns section 4's table prints, so this is a badge "
                        f"the artifact cannot support rather than a state of this household")
        finally:
            rt._BPM_COLUMNS = saved
        assert rt.resolve_token("S4_ROW_CLASS") == published, (
            "the substituted column list leaked out of this case (S4_ROW_CLASS)")
    return ("S4_ROW_CLASS refuses rather than naming a state off a column count its "
            "vocabulary cannot describe (" + "; ".join(f"{k}: {v}" for k, v in
                                                        refusals.items()) + ")")


@case
def case_free_fix_verdicts_invert_rather_than_refusing_when_there_is_no_free_saving():
    """Three sentences sell the same free move: section 0 ("saves a modeled
    $X/yr whatever you buy"), section 7 ("is worth a modeled $X/yr") and the
    Monday appendix ("captures the free savings", which quotes no figure at
    all and so can never be caught by reading the rendered line). At X <= 0
    the shift saves nothing: the first two would print a negative figure
    straight after the word "saves", and the third would send the reader
    after a loss.

    All three used to raise SystemExit there, which is wrong for the same
    reason the plan guard was: a household that ALREADY charges inside the
    cheap window has no free saving left to capture, and that is an ordinary,
    useful conclusion -- not grounds for refusing to generate any of the
    report's fifteen sections. So the clauses invert.

    Both artifacts are still driven -- behavior_rebuild's scenarios.a.saved
    and package_results' packages.LOW.savings_yr -- in two ways. Moving BOTH
    to zero or negative must invert all three sentences. Moving ONE must still
    fail closed: two committed artifacts disagreeing about the sign of the
    same move is a contradiction no sentence here can be written on top of,
    and it also catches a regression that re-pointed one sentence at the other
    artifact. Ungated (the plan answers S0 needs are stubbed)."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    scenario = rt._json("behavior_rebuild.json")["scenarios"]["a"]
    low = rt._json("package_results.json")["packages"]["LOW"]
    live = {"behavior_rebuild:scenarios.a.saved": scenario["saved"],
            "package_results:LOW.savings_yr": low["savings_yr"]}
    for label, value in live.items():
        assert value > 0, (
            f"data/{label} is already {value}/yr; the free-fix sentences in sections "
            "0, 7 and 15 are no longer the published branch")

    tokens = ("S0_VERDICT", "S7_VERDICT", "S15_VERDICT")
    widths = {}
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in tokens}
        for t in tokens:
            assert "free" in published[t], (
                f"{t} no longer sells the free move this case guards: {published[t]}")

        # 1. Both artifacts agree there is nothing to capture: invert.
        with _swapped(scenario, "saved", 0), _swapped(low, "savings_yr", 0):
            for token in tokens:
                value = rt.resolve_token(token)
                assert "adds no modeled saving" in value, (
                    "{} does not say plainly that a 0/yr EV-charging shift has "
                    "nothing to capture: {}".format(token, value))
                assert value != published[token], (
                    f"{token} renders its published sentence at a 0/yr free fix: {value}")
                for sold in ("captures the free savings", "whatever you buy"):
                    assert sold not in value, (
                        f"{token} still sells the free move at 0/yr ({sold!r}): {value}")
                widths[f"{token} at 0"] = _assert_within_density_cap(
                    token, value, "the no-saving branch at 0")

        # 2. The two artifacts coming APART is still a refusal -- but the test
        #    is the derivation, not the sign. packages.LOW.savings_yr is
        #    literally round() of scenarios.a.saved (analysis/package_results.py),
        #    so a pair further apart than the rounding explains means the two
        #    artifacts were composed from different runs and nothing here can
        #    be written on top of them. Every ordered pair below is hundreds of
        #    dollars apart, so each is a genuine drift rather than a rounding
        #    boundary.
        for a_saved, b_low in ((0, 400), (-400, 400), (0, -400), (-400, 0),
                               (400, 0), (400, -400)):
            with _swapped(scenario, "saved", a_saved), _swapped(low, "savings_yr", b_low):
                for token in tokens:
                    try:
                        rendered = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} rendered while behavior_rebuild puts the free fix "
                            f"at {a_saved}/yr and package_results at {b_low}/yr -- a gap "
                            f"no rounding explains: {rendered}")
                    except SystemExit as e:
                        assert token in str(e), e
                        assert "what the free EV-charging fix is worth" in str(e), (
                            f"{token}'s refusal does not name the quantity that was "
                            f"indeterminate: {e}")

        # 3. Non-finite on either side is the same state: nothing to compare.
        for bad in (float("nan"), float("inf")):
            with _swapped(scenario, "saved", bad), _swapped(low, "savings_yr", bad):
                for token in tokens:
                    try:
                        rendered = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} rendered a free-fix clause off a {bad} saving: "
                            f"{rendered}")
                    except SystemExit as e:
                        assert token in str(e), e

        for token in tokens:
            assert rt.resolve_token(token) == published[token], (
                f"the substituted free-fix saving leaked out of this case ({token})")
    return ("S0_VERDICT, S7_VERDICT and S15_VERDICT invert to 'adds no modeled saving' "
            "when both artifacts put the free fix at exactly zero, and fail closed on "
            "every mixed-sign and non-finite pair (live: "
            + ", ".join(f"{k.split(':')[0]} ${v:,.0f}" for k, v in live.items())
            + "; " + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_free_fix_verdicts_state_a_modeled_loss_as_a_loss_not_as_a_non_event():
    """FINDING 4. Zero and negative were one branch, so a free fix the model
    prices at -$800/yr rendered "shifting EV charging adds no modeled saving"
    in sections 0, 7 and 15 -- section 15 being the Monday INSTRUCTION list,
    which then gave the reader no reason not to spend the afternoon on a move
    the model prices as a loss.

    They are different states of the same claim: an exact zero is
    SUPPORTED-OPPOSITE ("nothing left to capture"), a loss is a real finding
    that has to be stated as one. It is STATED rather than refused because the
    artifacts settle it -- both agree and the sign is unambiguous -- and
    NOT_DETERMINED is for questions they do not settle (driven in the mixed-sign
    arms of the case above).

    Both magnitudes are driven, so the loss clause quotes the loss rather than
    printing a constant, and the zero and loss renderings must differ from each
    other as well as from the published one."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    scenario = rt._json("behavior_rebuild.json")["scenarios"]["a"]
    low = rt._json("package_results.json")["packages"]["LOW"]
    assert scenario["saved"] > 0 and low["savings_yr"] > 0, (
        "the free fix is no longer positive on the committed artifacts; the published "
        "branch this case contrasts against is gone")
    tokens = ("S0_VERDICT", "S7_VERDICT", "S15_VERDICT")
    widths, seen = {}, {}
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in tokens}
        with _swapped(scenario, "saved", 0), _swapped(low, "savings_yr", 0):
            zero = {t: rt.resolve_token(t) for t in tokens}
        for loss in (-800, -125.5):
            with _swapped(scenario, "saved", loss), _swapped(low, "savings_yr", loss):
                for token in tokens:
                    value = rt.resolve_token(token)
                    assert "adds no modeled saving" not in value, (
                        f"{token} renders a modeled {loss}/yr LOSS as a neutral "
                        f"non-event: {value}")
                    assert f"costs a modeled {rt._usd0(-loss)}/yr" in value, (
                        f"{token} does not state the {loss}/yr loss its two artifacts "
                        f"agree on: {value}")
                    assert "$-" not in value, (
                        f"{token} prints a minus inside the dollar sigil: {value}")
                    for sold in ("captures the free savings", "whatever you buy",
                                 "is worth a modeled", "saves a modeled"):
                        assert sold not in value, (
                            f"{token} still sells the free move at a {loss}/yr loss "
                            f"({sold!r}): {value}")
                    assert value not in (published[token], zero[token]), (
                        f"{token} gives a {loss}/yr loss the same sentence it gives a "
                        f"zero or a positive saving: {value}")
                    widths[f"{token} at {loss}"] = _assert_within_density_cap(
                        token, value, f"the loss branch at {loss}")
                    seen[token] = value
        # Section 15 is the instruction list: it must actively tell the reader
        # not to make the move, not merely stop recommending it.
        assert "leave the charger schedules alone" in seen["S15_VERDICT"], (
            f"S15_VERDICT's Monday list does not tell the reader to leave the schedules "
            f"alone while the model prices moving them as a loss: {seen['S15_VERDICT']}")
        for token in tokens:
            assert rt.resolve_token(token) == published[token], (
                f"the substituted free-fix loss leaked out of this case ({token})")
    return ("S0_VERDICT, S7_VERDICT and S15_VERDICT state a modeled free-fix loss as a "
            "loss (-$800, -$126/yr), distinct from both the zero and the positive "
            "branch, and S15 tells the reader to leave the schedules alone ("
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_s2_verdict_reports_what_was_measured_rather_than_an_array_health_verdict():
    """"the array is healthy" rendered identically whatever the meter said:
    no committed artifact carries a specific-yield expectation or a
    degradation trend to compare against. Unlike S1's correlation floor
    there is no committed gate to anchor a threshold to, so the judgment is
    dropped instead of guarded -- the measured yield and the export-timing
    finding carry the section.

    Ungated on purpose, and stubbed to stay that way: S2_VERDICT reads two
    household answers (solar.kw_dc, household.pto_date), so without the
    substitution this case raised SystemExit -- not SkipCase -- on an
    archive-less runner, and CI runs this suite on exactly that. A wording
    guard that cannot run in CI cannot stop the "healthy" verdict coming
    back."""
    with _stub_household(_s2_household_inputs()):
        value = rt.resolve_token("S2_VERDICT")
    for judgment in ("healthy", "degraded", "underperforming", "good shape"):
        assert judgment not in value.lower(), (
            f"S2_VERDICT passes a {judgment!r} verdict on the array with no committed "
            f"benchmark behind it: {value}")
    production = rt._annual_production_kwh(rt.CTX)
    assert f"{production:,.0f} kWh" in value, (
        f"S2_VERDICT dropped the measured production total: {value}")
    assert "kWh/kW" in value, f"S2_VERDICT dropped the measured specific yield: {value}"
    # The closing clause is the section's conclusion, and both halves of it are
    # about TIMING. The export share must be the one the hour-of-day profiles
    # produce for the tariff's own midday window -- not the annual
    # exports/production ratio the sentence used to print, which is the same
    # number whether the array exports at noon or at dusk.
    share = rt._midday_export_share(rt.CTX)
    assert f"{round(share * 100)}% of its exports leave in the " \
        f"{rt._cheap_window()} window" in value, (
        f"S2_VERDICT dropped the hour-resolved export-timing conclusion: {value}")
    assert "charges overnight" in value, (
        f"S2_VERDICT dropped the EV-timing conclusion: {value}")
    _assert_within_density_cap("S2_VERDICT", value, "the published branch")
    return ("S2_VERDICT keeps the measured yield, states the midday export share the "
            "hour-of-day profiles support, and passes no health verdict")


@case
def case_s2_verdict_refuses_to_time_exports_it_cannot_rebuild():
    """The midday share is only this year's if report_data.json's two
    season-mean hour-of-day export profiles, weighted by the window's real
    summer/winter day counts, still rebuild its own annual export total.
    Driven, not read: a profile scaled off the year it describes must take the
    sentence's whole timing clause down with it, rather than publishing a
    share of the wrong denominator."""
    rd = rt._json("report_data.json")
    live = rt._midday_export_share(rt.CTX)
    assert 0 < live < 1, f"the live midday export share is {live}, not a share"
    with _stub_household(_s2_household_inputs()):
        assert rt.resolve_token("S2_VERDICT")
        with _swapped(rd["hourly_S"], "exp", [v * 2 for v in rd["hourly_S"]["exp"]]):
            try:
                value = rt.resolve_token("S2_VERDICT")
            except SystemExit as e:
                assert "S2_VERDICT" in str(e) and "rebuild" in str(e), e
            else:
                raise AssertionError(
                    "S2_VERDICT published an export-timing share from profiles that no "
                    f"longer rebuild data/report_data.json's own export total: {value}")
        # Nothing leaked: the same sentence comes back once the artifact does.
        assert rt.resolve_token("S2_VERDICT")
    return ("S2_VERDICT fails closed when the hour-of-day export profiles stop "
            f"rebuilding the year's exports (live midday share {live:.1%})")


# THE TWO §8 VERDICT TOKENS ARE PINNED BY VALUE, NOT BY VOCABULARY (issue
# #183). Both quote the ALL-HOURS export share, exports over production, the
# figure EXPORTED_SHARE publishes. That ratio is the same number whether the
# exports leave at noon or at dusk, so a clause that put it "at the wrong time
# of day" or "at low value" was describing the midday slice and pointing at
# the whole; the heading carried the second wording and the expansion token
# the first. A blocklist of time and value words was the first pin written
# here and it was the wrong shape: "for pennies", "into a glut", "when the
# grid least needs it" and "60 percent" all walked past it (the same hole
# issue #180 names in the page-wide guard). So each token is asserted EQUAL
# to the sentence built from its own helper values -- the all-hours share, the
# grandfathering bracket, the midday share and the tariff window -- and any
# wording change, in either direction, fails by value. The retired-wording
# case below feeds the pin the two retired strings and shows it refusing them,
# so the pin stays a test of the defect rather than of a spelling.
def _s8_scaffolds():
    """{token: the sentence it must render}, from the helpers the tokens read."""
    exp_pct = rt._all_hours_export_pct(rt.CTX)
    midday_pct = round(rt._midday_export_share(rt.CTX) * 100)
    window = rt._cheap_window()
    return {
        "S8_VERDICT_SHORT": (
            f"No, no, and not yet — the array already exports {exp_pct}% of production, "
            f"and expansion risks the {rt._grandfathering_bracket('S8_VERDICT_SHORT')} "
            "NEM 2.0 grandfathering"),
        "EXPANSION_VERDICT_SHORT": (
            f"No — expansion risks the "
            f"{rt._grandfathering_bracket('EXPANSION_VERDICT_SHORT')} NEM 2.0 "
            f"grandfathering, and {midday_pct}% of the year's exports already leave in "
            f"the {window} window"),
    }


def _assert_s8_tokens_match_their_scaffolds():
    for token, expected in sorted(_s8_scaffolds().items()):
        value = rt.resolve_token(token)
        assert value == expected, (
            f"{token} renders {value!r}, not the sentence its own helper values build "
            f"({expected!r}) -- the wording moved, and the only wordings this pin has "
            "ever refused attached a time of day or a value to the all-hours export "
            "share, which says nothing about either")


@case
def case_s8_verdict_tokens_render_exactly_their_helper_values():
    """The heading and the expansion verdict each render the one sentence
    their helpers build: the all-hours share (a plain fraction), the
    grandfathering bracket (the reason for the "no"), and, on the expansion
    token, the midday slice timed by its own share. Both stay inside the
    basic-tier density cap."""
    _assert_s8_tokens_match_their_scaffolds()
    for token in ("S8_VERDICT_SHORT", "EXPANSION_VERDICT_SHORT"):
        _assert_within_density_cap(token, rt.resolve_token(token), "the §8 verdict")
    exp_pct = rt._all_hours_export_pct(rt.CTX)
    midday_pct = round(rt._midday_export_share(rt.CTX) * 100)
    return (f"S8_VERDICT_SHORT and EXPANSION_VERDICT_SHORT render exactly their helper "
            f"values (all-hours {exp_pct}%, midday {midday_pct}%, "
            f"{rt._grandfathering_bracket('S8_VERDICT_SHORT')} at risk), inside the "
            "density cap")


@case
def case_s8_value_pin_refuses_both_retired_wordings():
    """Self-test of the pin above: each retired wording, rendered with the live
    figures, is put behind the token's own `get` and the pin has to refuse it.
    Without this the pin is a spelling check that happens to pass today."""
    exp_pct = rt._all_hours_export_pct(rt.CTX)
    bracket = rt._grandfathering_bracket("S8_VERDICT_SHORT")
    retired = (
        ("S8_VERDICT_SHORT",
         f"No, no, and not yet — the array already exports {exp_pct}% of production at "
         f"low value, and expansion risks the {bracket} NEM 2.0 grandfathering"),
        ("EXPANSION_VERDICT_SHORT",
         f"No — already exports {exp_pct}% of production at the wrong time of day"),
        # The same conflation past the old blocklist: no listed word, same claim.
        ("EXPANSION_VERDICT_SHORT",
         f"No — already exports {exp_pct}% of production for pennies"),
    )
    for token, wording in retired:
        with _swapped(rt.TOKENS[token], "get", lambda ctx, w=wording: w):
            try:
                _assert_s8_tokens_match_their_scaffolds()
            except AssertionError as e:
                assert token in str(e) and wording in str(e), e
            else:
                raise AssertionError(
                    f"the §8 value pin accepts the retired wording {wording!r} for {token}")
    # Nothing leaked: the live tokens pass again.
    _assert_s8_tokens_match_their_scaffolds()
    return (f"the §8 value pin refuses all {len(retired)} retired wordings, including one "
            "no vocabulary list would have caught")


# The two published ends of what an exported kWh is worth, and the rates.py
# function each one prices the profile through. Written here as a table so the
# pins below sweep both ends with one body: an end added or renamed on the
# generator side has to be added here before any of them can pass.
_EXPORT_BOUNDS = (("EXPORT_VALUE_SURPLUS_BOUND", "credit"),
                  ("EXPORT_VALUE_NETTING_BOUND", "energy"))


def _price_map(rate_name):
    """Every (season, period) cell of one of rates.py's price maps."""
    rate = getattr(rt.R, rate_name)
    return {(s, p): rate(s, p) for s in ("S", "W") for p in ("sop", "off", "on")}


def _export_bound_recomputed(rate_name):
    """One end of the export-value range, rebuilt here rather than read from
    report_tokens -- the recompute convention this suite already follows, so a
    bug in the generator's weighting fails this case instead of being
    reproduced by it."""
    rate = getattr(rt.R, rate_name)
    rd = rt._json("report_data.json")
    start, end = rt._analysis_window_dates()
    total = value = 0.0
    d = start
    while d <= end:
        seas = "S" if d.month in rt.R.SUMMER_MONTHS else "W"
        off_day = rt.R.off_peak_day(d)
        for hour, kwh in enumerate(rd[f"hourly_{seas}"]["exp"]):
            total += kwh
            value += kwh * rate(seas, rt.R.period(hour, off_day))
        d += dt.timedelta(days=1)
    return value / total


def _analysis_window_day_count():
    start, end = rt._analysis_window_dates()
    return (end - start).days + 1


@case
def case_each_export_bound_prices_the_whole_export_profile_not_one_period():
    """issue #182. Section 8 priced the year's exports at the MIDDAY cell of
    the price map, generalizing one cell of six to a whole year of exports.
    About a third of this array's exports leave in off-peak and on-peak hours,
    which pay six to eleven times more, so the single-cell figure was roughly
    half the profile-weighted one.

    WHAT THESE TOKENS ARE NOT, and what no pin here may be read as endorsing:
    they are the price the year's EXPORTS fetched, not what one more kW of
    panels would earn. Exports are the residual left after household load, so
    added production is not shaped like them (issue #190).

    Both ends of the range are swept by the same three pins, because the first
    two alone would both survive the revert this case exists to catch:

      1. the token equals the independently recomputed weighted average;
      2. it equals NO single cell of its own price map -- the literal shape of
         the defect, checked against rates.py rather than against the digits
         that happen to be published today;
      3. it MOVES WITH THE PROFILE. A constant, or a value read off one cell,
         passes 1 and 2 by luck on some household's numbers; only a live
         weighting answers a profile whose exports all land in one period with
         that period's own price. Driven for all three periods.

    RELATIONSHIP, the recomputation against the token: SAME QUANTITY,
    INDEPENDENTLY COMPUTED from the same committed artifacts. Both read
    data/report_data.json's hour-of-day export profiles and the same rates.py
    price map; neither is derived from the other. They are entitled to agree
    exactly, and a disagreement is a bug in one of the two weightings."""
    rd = rt._json("report_data.json")
    swept = []
    for token, rate_name in _EXPORT_BOUNDS:
        expected = _export_bound_recomputed(rate_name)
        live = rt._export_value_bound(token, "probe", getattr(rt.R, rate_name),
                                      rate_name)
        assert abs(live - expected) < 1e-12, (
            f"{token} derives {live:.9f}/kWh; weighting rates.{rate_name}() by "
            f"data/report_data.json's own export profiles gives {expected:.9f}/kWh")
        rendered = rt.resolve_token(token)
        assert rendered == f"{expected * 100:.1f}¢", (
            f"{token} renders {rendered!r}, not the {expected * 100:.1f}¢ the "
            f"profile-weighted rates.{rate_name}() comes to")

        cells = _price_map(rate_name)
        for (seas, period), rate in cells.items():
            assert abs(live - rate) > 5e-4, (
                f"{token} has reverted to a single period price: it renders "
                f"{rendered}, which is rates.{rate_name}({seas!r}, {period!r}). An "
                f"exported kWh is priced across every hour the array exports in, and "
                f"this array's exports do not all leave in one period")

        # 3. Driven. An all-in-one-period profile must price at that period's
        #    own rate, and the two seasons' cells differ, so the answer has to
        #    land between them rather than on either -- which is itself
        #    evidence the season weighting is live too.
        for period in ("sop", "off", "on"):
            # The probe hour is FOUND, not named: it has to carry `period` on
            # both day types, or the driven profile prices one hour two ways
            # and the bracket below proves nothing. Derived from rates.period()
            # so a tariff whose windows differ picks its own hour instead of
            # inheriting this household's.
            agree = [h for h in range(24)
                     if rt.R.period(h, False) == period == rt.R.period(h, True)]
            assert agree, (
                f"no whole hour of this tariff carries {period} on both day types, so "
                "an all-in-one-period export profile cannot be built for it -- the "
                "probe needs rewriting against the new windows, not deleting")
            hour = agree[0]
            flat = [0.0] * 24
            flat[hour] = rd["totals"]["exp"] / _analysis_window_day_count()
            with _swapped(rd["hourly_S"], "exp", list(flat)), \
                 _swapped(rd["hourly_W"], "exp", list(flat)):
                driven = rt._export_value_bound(token, "probe",
                                                getattr(rt.R, rate_name), rate_name)
            lo, hi = sorted((cells[("S", period)], cells[("W", period)]))
            assert lo - 1e-12 <= driven <= hi + 1e-12, (
                f"an export profile whose every kWh leaves in {period} hours should "
                f"price between that period's two seasonal rates ({lo:.5f}-{hi:.5f}"
                f"/kWh); {token} returned {driven:.5f}/kWh, so it is not reading "
                "the profile")
        swept.append(f"{token}={rendered}")
    return (f"both export-value bounds ({', '.join(swept)}) are profile-weighted, "
            "match an independent recomputation to 1e-12, sit on no single cell of "
            "their own six-cell price map, and follow a driven profile into each of "
            "the three periods")


@case
def case_the_two_export_bounds_are_the_two_settlement_treatments():
    """issue #182, the finding this pair of tokens exists to answer: one
    profile-weighted figure was published as "what an exported kWh earns" when
    it was only the ALL-SURPLUS end of the answer.

    rates.bill_nem_monthly() settles NEM 2.0 by monthly per-period netting, so
    an exported kWh either cancels an import inside its own month and period or
    is paid the surplus credit. Two treatments, two prices, and the artifacts
    do not resolve which one any individual month took -- so both are
    published, and neither may be published as the settled value.

    Four pins, and the fourth is the one that fails if a bound is dressed up as
    the value:

      1. the ORDER is right: surplus is the low end, netting the high end. A
         swap of the two rate functions renders the range backwards, and pins
         1-3 of the sibling case survive it, since each end would still be a
         valid profile weighting of a real price map;
      2. the gap between them IS the difference between the two rates -- PCIA,
         the only term rates.energy() adds to rates.credit() -- rather than
         some other quantity that happens to sit between the two figures;
      3. the netting end is rates.energy() and NOT rates.allin(). MEASURED
         AGAINST THE ENGINE, not argued from the constants: one more exported
         kWh in a netting cell moves rates.bill_nem() by exactly energy(), and
         in a surplus cell by exactly credit(). allin() = energy() + NBC is
         what a GROSS import costs, and bill_nem_monthly() bills NBC on gross
         imports before any netting, so an export never avoids it. Pricing an
         export at allin() re-commits the NBC-netting error CLAUDE.md section 9
         records;
      4. NEITHER END IS PUBLISHED AS THE VALUE. index.html must state both, and
         must say the settlement lies between them. A page that names one and
         drops the other, or names both and calls either one the answer, fails
         here -- which is the whole failure class this issue closes."""
    import pandas as pd

    surplus = rt.resolve_token("EXPORT_VALUE_SURPLUS_BOUND")
    netting = rt.resolve_token("EXPORT_VALUE_NETTING_BOUND")
    # THE LIVE VALUES, off the registered getters, not two recomputations done
    # here. A recomputation cannot see which rate map each TOKEN was wired to,
    # so an order pin written against one passes with the two getters swapped
    # -- measured, not supposed: the first draft of pin 1 did exactly that and
    # reported "ordered low-to-high" on a report rendering 25.7¢-22.9¢.
    lo_live = rt.TOKENS["EXPORT_VALUE_SURPLUS_BOUND"]["get"](rt.CTX)
    hi_live = rt.TOKENS["EXPORT_VALUE_NETTING_BOUND"]["get"](rt.CTX)

    # 1. Order, and each end wired to its own price map.
    assert lo_live < hi_live, (
        f"the surplus bound ({lo_live:.5f}/kWh) is not below the netting bound "
        f"({hi_live:.5f}/kWh) -- the two rate functions are the wrong way round, and "
        "the report publishes the range backwards")
    for token, live, rate_name in (("EXPORT_VALUE_SURPLUS_BOUND", lo_live, "credit"),
                                   ("EXPORT_VALUE_NETTING_BOUND", hi_live, "energy")):
        expected = _export_bound_recomputed(rate_name)
        assert abs(live - expected) < 1e-12, (
            f"{token} resolves to {live:.6f}/kWh where weighting rates.{rate_name}() "
            f"by the export profile gives {expected:.6f}/kWh -- this end is wired to "
            "the wrong settlement treatment")

    # 2. The gap is PCIA and nothing else.
    assert abs((hi_live - lo_live) - rt.R.PCIA) < 1e-12, (
        f"the two bounds differ by {hi_live - lo_live:.6f}/kWh where rates.energy() "
        f"adds only PCIA ({rt.R.PCIA:.6f}) to rates.credit() -- one of the ends is not "
        "the price map it claims to be")

    # 3. The netting end, measured against the billing engine itself.
    frame = pd.DataFrame([
        dict(dt=pd.Timestamp("2025-07-10 12:00"), seas="S", p="sop", ym="2025-07",
             Consumption=100.0, Generation=40.0)])
    bumped = frame.copy()
    bumped.loc[0, "Generation"] += 1.0
    netting_delta = rt.R.bill_nem(frame) - rt.R.bill_nem(bumped)
    assert abs(netting_delta - rt.R.energy("S", "sop")) < 1e-9, (
        f"one more exported kWh in a NETTING cell moves rates.bill_nem() by "
        f"{netting_delta:.6f}, not rates.energy() ({rt.R.energy('S', 'sop'):.6f}) -- "
        "the high end of the published range is not the engine's own netting value")
    assert abs(netting_delta - rt.R.allin("S", "sop")) > 1e-6, (
        "an exported kWh in a netting cell is worth rates.allin() to this engine, so "
        "NBC is being netted away by an export; CLAUDE.md section 9 records that "
        "defect and bill_nem_monthly() is supposed to bill NBC on GROSS imports")
    surplus_frame = frame.copy()
    surplus_frame.loc[0, "Generation"] = 200.0
    bumped = surplus_frame.copy()
    bumped.loc[0, "Generation"] += 1.0
    surplus_delta = rt.R.bill_nem(surplus_frame) - rt.R.bill_nem(bumped)
    assert abs(surplus_delta - rt.R.credit("S", "sop")) < 1e-9, (
        f"one more exported kWh in a SURPLUS cell moves rates.bill_nem() by "
        f"{surplus_delta:.6f}, not rates.credit() "
        f"({rt.R.credit('S', 'sop'):.6f}) -- the low end of the published range is "
        "not the engine's own surplus value")

    # 4. The page publishes the range, not one end of it.
    html = (rt.ROOT / "index.html").read_text()
    for token, rendered in (("EXPORT_VALUE_SURPLUS_BOUND", surplus),
                            ("EXPORT_VALUE_NETTING_BOUND", netting)):
        assert rendered in html, (
            f"index.html does not state {rendered}, the {token} end of what an "
            "exported kWh is worth. A bound published on its own reads as the value, "
            "which is the failure this pair of tokens exists to prevent")
    # The valuation SENTENCE, located by its own words rather than by which
    # paragraph it sits in, and read to its sentence end on the repo's own rule
    # (a period followed by whitespace, a tag or the end -- never the period
    # inside "22.9¢"). It has to frame the figure as a range: a sentence naming
    # both bounds without "between" can still be reporting one of them as the
    # answer and the other as a foil.
    claim = re.search(r"an exported kWh on this profile.*?\.(?=\s|<|$)", html, re.S)
    assert claim, ("index.html no longer says what an exported kWh on this profile is "
                   "worth, so the range this pair of tokens publishes cannot be found")
    assert re.search(r"is worth between\b", claim.group(0)), (
        "section 8 no longer frames the export value as a range between two "
        f"settlement treatments: {claim.group(0)!r}")
    for rendered in (surplus, netting):
        assert rendered in claim.group(0), (
            f"the valuation sentence names only one end of the range: "
            f"{claim.group(0)!r} does not carry {rendered}")
    return (f"the export value is published as the range {surplus}-{netting}, the two "
            f"NEM 2.0 settlement treatments, ordered low-to-high and separated by "
            f"exactly PCIA; the netting end is the engine's own marginal value for an "
            f"export that cancels an import, measured against rates.bill_nem() rather "
            f"than assumed, and is rates.energy() rather than rates.allin()")


def _export_bound_recomputed_on_the_weekday_schedule(rate_name):
    """The same weighting as _export_bound_recomputed with the day-type rule
    switched OFF -- every day of the window priced on the weekday schedule.
    This is the comparison _export_value_bound's docstring publishes, and it
    exists so nobody re-derives the day-type rule's worth as a rounding
    argument."""
    rate = getattr(rt.R, rate_name)
    rd = rt._json("report_data.json")
    start, end = rt._analysis_window_dates()
    total = value = 0.0
    d = start
    while d <= end:
        seas = "S" if d.month in rt.R.SUMMER_MONTHS else "W"
        for hour, kwh in enumerate(rd[f"hourly_{seas}"]["exp"]):
            total += kwh
            value += kwh * rate(seas, rt.R.period(hour, False))
        d += dt.timedelta(days=1)
    return value / total


# The sentence in _export_value_bound's docstring that prices the day-type
# rule. Parsed rather than read, because its whole job is to save a reader the
# re-derivation, and a reader who re-derives from its own digits must not find
# a contradiction: as first written it claimed "0.7 cents at either end" beside
# a netting pair whose printed digits differ by 0.8.
_DAY_TYPE_WORTH_RE = re.compile(
    r"adds ([\d.]+) cents at either end \(([\d.]+) against ([\d.]+) surplus, "
    r"([\d.]+) against ([\d.]+) netting\)")


@case
def case_the_day_type_rules_worth_is_stated_in_digits_that_agree():
    """issue #182 review, finding 7. _export_value_bound's docstring publishes
    what rates.off_peak_day() is worth so that nobody re-derives it as a
    rounding argument. Three pins, and the third is the one that failed:

      1. each of the four figures is the one the artifacts produce, at the
         precision it is printed to -- the weekday-only pair recomputed here
         with the day-type rule switched off, the published pair with it on;
      2. the stated delta is the real difference between them;
      3. the printed digits SUBTRACT to the printed delta, at both ends. A
         paragraph whose own numbers contradict its claim teaches the reader
         to distrust it, which is the opposite of why it exists."""
    # Whitespace-normalized: the sentence is wrapped across docstring lines,
    # and the pin is about its digits, not about where its line breaks fall.
    doc = re.sub(r"\s+", " ", rt._export_value_bound.__doc__)
    m = _DAY_TYPE_WORTH_RE.search(doc)
    assert m, (
        "_export_value_bound's docstring no longer prices the day-type rule in the "
        "form this case reads (\"adds N cents at either end (A against B surplus, C "
        "against D netting)\"). That paragraph is what stops the rule being re-derived "
        "as a rounding argument; reword the pin with it, do not drop it")
    delta, wk_surplus, day_surplus, wk_netting, day_netting = m.groups()

    checked = []
    for printed, rate_name, weekday_only, label in (
            (wk_surplus, "credit", True, "the weekday-only surplus figure"),
            (day_surplus, "credit", False, "the published surplus figure"),
            (wk_netting, "energy", True, "the weekday-only netting figure"),
            (day_netting, "energy", False, "the published netting figure")):
        live = (_export_bound_recomputed_on_the_weekday_schedule(rate_name)
                if weekday_only else _export_bound_recomputed(rate_name))
        places = len(printed.split(".")[1]) if "." in printed else 0
        assert printed == f"{live * 100:.{places}f}", (
            f"the docstring's {label} is {printed} cents where weighting "
            f"rates.{rate_name}() by data/report_data.json's export profiles "
            f"{'on the weekday schedule' if weekday_only else 'at each day type'} "
            f"gives {live * 100:.{places}f}")
        checked.append(f"{label} {printed}")

    for end, weekday, published in (("surplus", wk_surplus, day_surplus),
                                    ("netting", wk_netting, day_netting)):
        places = max(len(x.split(".")[1]) if "." in x else 0
                     for x in (weekday, published, delta))
        printed_delta = f"{float(weekday) - float(published):.{places}f}"
        assert printed_delta == f"{float(delta):.{places}f}", (
            f"the docstring's {end} pair prints {weekday} against {published}, a "
            f"difference of {printed_delta} cents, while the sentence above them "
            f"claims {delta}. A reader re-deriving the difference from these digits "
            "finds the contradiction the paragraph exists to prevent -- print enough "
            "decimals that the subtraction comes out, or restate the claim")

    surplus_gap = (_export_bound_recomputed_on_the_weekday_schedule("credit")
                   - _export_bound_recomputed("credit")) * 100
    return (f"the docstring's day-type paragraph checks out: {', '.join(checked)}, "
            f"a real gap of {surplus_gap:.4f} cents stated as {delta}")


@case
def case_the_rebuild_refusal_describes_no_callers_own_operation():
    """issue #182 review, finding 6. _assert_profiles_rebuild_the_year was
    parameterized on (token, subject) when the export-value bounds started
    using it, but its FIRST refusal -- the one for an artifact whose
    totals.exp is zero -- kept the tail it was written with for the
    midday-SHARE caller: "...so there is no year of exports to take a share
    of". Handed to a caller that prices a kWh, that names an operation it does
    not perform, in the one message a reader gets when nothing else worked.

    Driven through the real callers rather than the helper, so a caller wired
    to a private copy of the message is caught too. Two pins: every caller's
    refusal carries the same tail (it is parameterized, not per-caller), and
    that tail names no single caller's own operation."""
    rd = rt._json("report_data.json")
    # Each caller invoked the way the token really reaches the helper. The
    # midday-share caller is called directly rather than through S2_VERDICT:
    # that token needs the private household, and the refusal under test is
    # raised before any household value is read.
    callers = (("S2_VERDICT", lambda: rt._midday_export_share(rt.CTX)),
               ("EXPANSION_VERDICT_SHORT",
                lambda: rt.TOKENS["EXPANSION_VERDICT_SHORT"]["get"](rt.CTX)),
               ("EXPORT_VALUE_SURPLUS_BOUND",
                lambda: rt.TOKENS["EXPORT_VALUE_SURPLUS_BOUND"]["get"](rt.CTX)),
               ("EXPORT_VALUE_NETTING_BOUND",
                lambda: rt.TOKENS["EXPORT_VALUE_NETTING_BOUND"]["get"](rt.CTX)))
    # Vocabulary that belongs to ONE caller. A refusal about an artifact with
    # no exports in it may describe the ARTIFACT; the moment it describes an
    # operation, it is describing whichever caller it was written for.
    operations = {"share": "the midday-share caller", "worth": "the export-value "
                  "callers", "price": "the export-value callers"}
    tails = {}
    with _swapped(rd["totals"], "exp", 0):
        for token, call in callers:
            try:
                value = call()
            except SystemExit as e:
                message = str(e)
            else:
                raise AssertionError(
                    f"{token} published {value!r} from an artifact whose totals.exp is "
                    "0 kWh, instead of refusing")
            parsed = re.match(r"report_tokens: (\S+) cannot say (.+?) -- (.+)\Z",
                              message, re.S)
            assert parsed, (
                f"{token}'s zero-exports refusal no longer names the token and the "
                f"subject it was refusing to state: {message!r}")
            assert parsed.group(1) == token, (
                f"the refusal names {parsed.group(1)}, not the token that asked "
                f"({token}): {message!r}")
            tails[token] = parsed.group(3)
    # Nothing leaked: the artifact is back and every caller resolves again.
    for token, call in callers:
        assert call() is not None

    distinct = set(tails.values())
    assert len(distinct) == 1, (
        f"the zero-exports refusal reads differently for different callers "
        f"({tails}) -- the message beyond the token and its subject is supposed to be "
        "one statement about the artifact")
    tail = distinct.pop()
    for word, whose in sorted(operations.items()):
        assert word not in tail.casefold(), (
            f"the zero-exports refusal ends {tail!r}, which names an operation only "
            f"{whose} perform(s) ({word!r}). Every caller of "
            "_assert_profiles_rebuild_the_year "
            "gets this sentence, so it may state what the artifact holds and nothing "
            "about what the caller was going to do with it")
    return (f"all {len(tails)} callers' zero-exports refusals share one tail ({tail!r}) "
            "that names no single caller's own operation")


# The two names whose SIZE has already been typed into prose and gone stale:
# report_tokens.KNOWN_GAPS and report_blocks.LIVE_GAP_TOKENS. Both are computed
# -- one declared in this repo's own source, one derived from the template at
# import time -- so any count of them written into a comment is a second copy
# of a fact the code already states.
_GAP_SET_NAMES = r"(?:KNOWN_GAPS|LIVE_GAP_TOKENS)"
_PLURAL_CARDINAL = (r"(?:[2-9]|\d\d+|two|three|four|five|six|seven|eight|nine|ten|"
                    r"eleven|twelve)")
_CARDINAL = rf"(?:1|one|{_PLURAL_CARDINAL})"
#
# The cardinal has to be QUANTIFYING the set, not merely near it: only a
# possessive ("report_tokens.py's five KNOWN_GAPS") or a plain adjective
# ("two live KNOWN_GAPS") may sit between them, and behind the name only a
# bare plural ("the KNOWN_GAPS five"). A looser window flags ordinary prose --
# "a KNOWN_GAPS token: that ONE is about storage" reads as a count to any
# pattern that allows two free words, and a guard that refuses correct writing
# gets deleted rather than obeyed.
_TYPED_GAP_COUNT_RES = (
    # "...py's five KNOWN_GAPS", "two live KNOWN_GAPS"
    re.compile(rf"(?<![\w#]){_CARDINAL}\s+(?:\S+?'s\s+)?"
               rf"(?:live\s+|declared\s+|remaining\s+|current\s+)?{_GAP_SET_NAMES}",
               re.I),
    # "the KNOWN_GAPS five", "LIVE_GAP_TOKENS holds three"
    re.compile(rf"{_GAP_SET_NAMES}\s+"
               rf"(?:tokens?|entries|set|holds|has|have|carries|lists|names)?\s*"
               rf"{_CARDINAL}\b", re.I),
    # "three of the KNOWN_GAPS tokens". The cardinal is PLURAL on purpose: "one
    # of the KNOWN_GAPS tokens" points at a member, which is ordinary correct
    # writing, while "three of" is a count.
    re.compile(rf"(?<![\w#]){_PLURAL_CARDINAL}\s+of\s+(?:\S+\s+){{0,2}}?"
               rf"{_GAP_SET_NAMES}", re.I),
)
# Scoped to the two modules that DEFINE these sets -- the place a maintainer
# looks the count up instead of re-deriving it, and where both stale copies
# were found. generate_report.py's narrative about two gap tokens sitting in
# two different sections is a claim about the template's shape rather than a
# documented inventory count, and that file is not this case's to police.
_GAP_COUNT_SOURCES = ("report_tokens.py", "report_blocks.py")


@case
def case_no_module_types_a_count_of_the_gap_token_sets():
    """issue #182 review, finding 5. report_blocks.py said "Three of
    report_tokens.py's FIVE KNOWN_GAPS tokens" in two places, one of them the
    header of the LIVE_GAP_TOKENS derivation itself. KNOWN_GAPS had held four
    since a token left it, and the live count moved to two when the template
    stopped pricing added capacity -- so both halves of the sentence were
    wrong, in a comment written precisely so a maintainer would not have to
    re-derive them.

    A count of a computed set does not belong in prose beside the computation.
    len(report_tokens.KNOWN_GAPS) and report_blocks.LIVE_GAP_TOKENS are both
    one expression away, and report_blocks.main() prints the live set."""
    offenders = []
    for name in _GAP_COUNT_SOURCES:
        text = (rt.ROOT / "analysis" / name).read_text()
        for rx in _TYPED_GAP_COUNT_RES:
            for m in rx.finditer(text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{name}:{line} {m.group(0)!r}")
    assert not offenders, (
        f"{len(offenders)} typed count(s) of a computed gap-token set: "
        + "; ".join(offenders)
        + ". Both counts have gone stale here before, in the same sentence. Name the "
          "tokens or let len(KNOWN_GAPS) and LIVE_GAP_TOKENS say how many there are")
    return (f"neither {' nor '.join(_GAP_COUNT_SOURCES)} types a count of KNOWN_GAPS "
            f"or LIVE_GAP_TOKENS (live now: {len(rt.KNOWN_GAPS)} declared)")


@case
def case_s2_verdict_reports_a_daytime_charger_rather_than_refusing_to_render():
    """"while the EV charges overnight" was asserted outright, and on a
    household that charges during the day it published a falsehood. It now
    reads quiet_night_floor.json's EV-absence census for the tariff's own
    overnight super-off-peak window, and says the habit only when the EV
    charges there on more nights than it skips. Both sides of that boundary
    are driven here, including the exact tie -- a house split evenly between
    day and night charging has no overnight habit to report.

    Below the boundary the clause INVERTS; it does not take the report down
    with it. A daytime charger is an ordinary household whose section 2 is
    just as worth generating, and "does not usually charge overnight" is true
    at every count from an even split down to none. What still fails closed is
    a census that never counted this tariff's window at all, since there the
    clause has no measurement to invert on."""
    census = (rt._json("quiet_night_floor.json")["night_floor"]
              ["issue_114_investigation"]["ev_absence_by_window"])
    lo, hi, _lab = rt._overnight_cheap_run()
    label = f"{int(lo)}-{int(hi)}h"
    assert label in census, (
        f"data/quiet_night_floor.json no longer censuses the tariff's overnight "
        f"super-off-peak window {label}; it has {sorted(census)}")
    entry = census[label]
    charging, absent, observed = rt._overnight_ev_night_counts(rt.CTX)
    assert charging > absent, (
        f"data/quiet_night_floor.json now finds overnight charging on only {charging} of "
        f"{observed} nights; the published clause is no longer the live one")
    widths = {}
    with _stub_household(_s2_household_inputs()):
        published = rt.resolve_token("S2_VERDICT")
        assert "while the EV charges overnight" in published, published
        widths["published"] = _assert_within_density_cap(
            "S2_VERDICT", published, "the published branch")
        # A daytime charger, and an exact 50/50 split -- neither may claim the
        # habit, and both must still render. The half-night in the second case
        # is synthetic on purpose: it is the value that lands the comparison
        # exactly on the boundary for an odd night count, the way the section 6
        # and 7 tie branches are driven.
        for label_, absent_n in (("daytime charger", observed - 1),
                                 ("even split", observed / 2)):
            with _swapped(entry, "n", absent_n):
                value = rt.resolve_token("S2_VERDICT")
            assert "does not usually charge overnight" in value, (
                f"S2_VERDICT does not say plainly that a {label_} lacks the overnight "
                f"habit: {value}")
            assert "while the EV charges overnight" not in value, (
                f"S2_VERDICT told a {label_} that its EV charges overnight: {value}")
            widths[label_] = _assert_within_density_cap("S2_VERDICT", value, label_)
        # A census that never counted this tariff's overnight window is a gap,
        # not a licence to answer from a neighbouring window.
        with _swapped(census, label, None):
            del census[label]
            try:
                value = rt.resolve_token("S2_VERDICT")
            except SystemExit as e:
                assert "S2_VERDICT" in str(e) and label in str(e), e
            else:
                raise AssertionError(
                    "S2_VERDICT timed the EV's charging from a census that never counted "
                    f"the {label} window: {value}")
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted EV census leaked out of this case")
    return (f"S2_VERDICT claims an overnight charging habit only on a majority of nights "
            f"and inverts below that instead of aborting the report (live: {charging} of "
            f"{observed} in the {rt._overnight_cheap_window()} window; "
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


def _centered(values):
    mean = sum(values) / len(values)
    return [v - mean for v in values]


def _blend_to_correlation(rows, column, target):
    """Overwrite `column` in place so its weakest pairwise daily correlation
    with the artifact's other series is `target`, and return the original
    cells so the caller can restore them.

    Deterministic and solved, not "add noise until it looks weak": a fixed
    LCG sequence is projected orthogonal to EVERY series in the file (the
    victim's own original included), so mixing in A units of it moves the
    correlation to 1/sqrt(1 + A^2 var_z/var_x) and A can be solved for the
    target outright. Orthogonalizing against the other columns as well is
    what keeps a 0.0010 target from landing on the wrong SIDE of zero: the
    other two series are near-copies of the victim, so any leftover
    projection would dominate a correlation that small. Rows where any
    column is blank are dropped from the victim so every pair correlates
    over the same dates the projection was computed on."""
    numeric = list(rows[0])[1:]                # column 0 is the date key
    complete = [r for r in rows if all(r[c] not in (None, "") for c in numeric)]
    xs = [float(r[column]) for r in complete]
    others = [_centered([float(r[c]) for r in complete])
              for c in numeric if c != column]
    state = 12345
    zs = []
    for _ in complete:
        state = (1103515245 * state + 12345) % 2147483648
        zs.append(state / 2147483648 * 2 - 1)
    cx = _centered(xs)
    orth = _centered(zs)
    for _pass in range(2):                     # twice, for numerical stability
        for basis in [cx] + others:
            denom = sum(v * v for v in basis)
            beta = sum(u * v for u, v in zip(orth, basis)) / denom
            orth = [o - beta * b for o, b in zip(orth, basis)]
    var_x = sum(v * v for v in cx)
    var_o = sum(v * v for v in orth)
    amplitude = ((1.0 / target ** 2 - 1.0) * var_x / var_o) ** 0.5
    originals = [r[column] for r in rows]
    for r in rows:
        r[column] = ""
    for r, x, o in zip(complete, xs, orth):
        r[column] = repr(x + amplitude * o)
    return originals


@case
def case_s1_verdict_needs_real_agreement_not_merely_a_positive_correlation():
    """Section 1's second claim is the word "agree", not the sign of a
    number. A guard that only rejects r <= 0 still lets "the independent
    production series agree at 0.0010 daily correlation" render, and 0.0010
    is three series with essentially nothing in common. The bound the token
    uses is not invented: it is the weakest pairwise correlation a passing
    run of analysis/threeway_production_validation.py can put in the file
    (its reference-sanity floor less its derived-series allowance)."""
    rows = rt._csv_rows("threeway_production_validation.csv")
    victim = list(rows[0])[1]
    real = rt._min_pairwise_daily_correlation()
    floor = rt.MIN_AGREEMENT_CORRELATION
    assert real >= floor, (
        f"the committed production series already correlate at only {real:.4f}, under "
        f"the {floor} floor S1_VERDICT requires")
    originals = None
    measured = {}
    try:
        # Positive, and still not agreement: the review's own 0.0010 example,
        # a correlation most readers would call strong (0.60), and one just
        # under the floor.
        for target in (0.0010, 0.60, floor - 0.002):
            originals = _blend_to_correlation(rows, victim, target)
            weakest = rt._min_pairwise_daily_correlation()
            measured[f"{target:.4f}"] = weakest
            assert 0 < weakest < floor, (
                f"the {target:.4f} blend measured {weakest:.4f}, which is not the "
                "positive-but-under-the-floor case this asserts")
            try:
                value = rt.resolve_token("S1_VERDICT")
                raise AssertionError(
                    f"S1_VERDICT said the independent production series agree at a "
                    f"weakest pairwise correlation of {weakest:.4f}: {value}")
            except SystemExit as e:
                assert "S1_VERDICT" in str(e), e
            for r, original in zip(rows, originals):
                r[victim] = original
            originals = None
        # Discriminates both ways: a pair that is measurably worse than
        # today's data but still clears the floor keeps the word.
        originals = _blend_to_correlation(rows, victim, floor + 0.007)
        weakest = rt._min_pairwise_daily_correlation()
        measured["above the floor"] = weakest
        assert floor < weakest < real, (
            f"the above-floor blend measured {weakest:.4f}, outside the "
            f"({floor}, {real:.4f}) band this arm needs")
        value = rt.resolve_token("S1_VERDICT")
        assert f"agree at {weakest:.4f} daily correlation" in value, value
    finally:
        if originals is not None:
            for r, original in zip(rows, originals):
                r[victim] = original
    assert abs(rt._min_pairwise_daily_correlation() - real) < 1e-12, (
        "the blended production column leaked out of this case")
    return ("S1_VERDICT withholds the word 'agree' at weakest pairwise correlations of "
            + ", ".join(f"{v:.4f}" for k, v in measured.items() if k != "above the floor")
            + f" and keeps it at {measured['above the floor']:.4f} "
            f"(floor {floor}, live {real:.4f})")


@case
def case_s6_verdict_ranks_the_larger_gain_whenever_one_option_really_gains():
    """S6's closing clause is the other shape: a comparison of two DIFFERENCES
    ("worth more than"), not a quotient. Where one of the two options really
    does add money, the ranking must follow the larger gap in both
    directions -- asserted across the grid rather than by reading the code,
    since the neighbouring S7 defect looked equally harmless."""
    grid = [(1000, 900, 1050), (1000, 900, 1200), (1000, 1100, 1050),
            (1000, 900, 800), (1000, 1050, 1400)]
    widths = {}
    for greedy, evening, expanded in grid:
        value = _s6_verdict_at(greedy, evening, expanded)
        policy_gap, capacity_gap = greedy - evening, expanded - greedy
        assert max(policy_gap, capacity_gap) > 0, (
            f"grid entry {(greedy, evening, expanded)} has no winning option, so it "
            "belongs in the both-lose case, not this one")
        expected = ("so the dispatch settings are worth more than a bigger pack"
                    if policy_gap > capacity_gap else
                    "so a bigger pack is worth more than the dispatch settings")
        assert expected in value, (
            f"S6_VERDICT ranks the wrong side at policy gap {policy_gap} vs capacity gap "
            f"{capacity_gap}: {value}")
        widths[f"{policy_gap:+d}/{capacity_gap:+d}"] = _assert_within_density_cap(
            "S6_VERDICT", value, f"policy {policy_gap:+d} vs capacity {capacity_gap:+d}")
    return (f"S6_VERDICT's 'worth more than' clause tracks the larger marginal gain across "
            f"{len(grid)} sign combinations, each inside the density cap "
            f"({', '.join(f'{k} {v}w' for k, v in widths.items())})")


@case
def case_s6_verdict_calls_an_exact_tie_a_tie_rather_than_a_win_for_the_pack():
    """"policy_gap > capacity_gap" splits three orderings into two branches:
    at an exact tie the else arm fires and tells the reader a bigger pack is
    worth MORE than the dispatch settings while the artifact says the two
    marginal gains are identical."""
    real = rt._json("battery_dispatch_policies.json")
    live_policy = real["pw3"]["greedy"]["save"] - real["pw3"]["evening"]["save"]
    live_capacity = real["pw3x"]["greedy"]["save"] - real["pw3"]["greedy"]["save"]
    assert live_policy != live_capacity, (
        f"data/battery_dispatch_policies.json now ties the two gaps at {live_policy}; "
        "the published branch below is no longer the live one")
    widths = {}
    for greedy, evening, expanded in ((1000, 900, 1100), (1000, 700, 1300)):
        value = _s6_verdict_at(greedy, evening, expanded)
        gap = greedy - evening
        assert "worth more than" not in value, (
            f"S6_VERDICT ranks one option above the other while both add exactly "
            f"${gap}/yr: {value}")
        assert "are worth the same" in value, (
            f"S6_VERDICT must say plainly that two ${gap}/yr gains are worth the same: "
            f"{value}")
        widths[f"tie at +{gap}"] = _assert_within_density_cap(
            "S6_VERDICT", value, f"an exact tie at +{gap}")
    return ("S6_VERDICT calls an exact tie equal marginal value instead of handing the "
            f"win to the bigger pack ({', '.join(f'{k} {v}w' for k, v in widths.items())})")


@case
def case_s6_verdict_does_not_sell_the_better_of_two_losing_options():
    """The comparison is still TRUE when both gaps are negative -- and that is
    the problem. "a bigger pack is worth more than the dispatch settings"
    reads as purchase guidance, so ranking two options that each REDUCE the
    modeled saving recommends a detrimental buy on a technicality. Zero
    counts as no gain, so a tie at zero belongs here too, not in the
    equal-value branch."""
    widths = {}
    # (greedy, evening, expanded): policy gap / capacity gap, in that order --
    # pack loses less, dispatch loses less, both exactly zero, one zero.
    grid = [(1000, 1100, 950), (1000, 1100, 850), (1000, 1000, 1000), (1000, 1050, 1000)]
    for greedy, evening, expanded in grid:
        value = _s6_verdict_at(greedy, evening, expanded)
        policy_gap, capacity_gap = greedy - evening, expanded - greedy
        assert "worth more than" not in value, (
            f"S6_VERDICT recommends the better of two options that each add "
            f"{policy_gap:+d}/{capacity_gap:+d} per year: {value}")
        assert "are worth the same" not in value, (
            f"S6_VERDICT calls two non-gains equal marginal value at "
            f"{policy_gap:+d}/{capacity_gap:+d}: {value}")
        assert "neither the dispatch settings nor a bigger pack adds any saving" in value, (
            f"S6_VERDICT must say neither option adds anything at "
            f"{policy_gap:+d}/{capacity_gap:+d}: {value}")
        widths[f"{policy_gap:+d}/{capacity_gap:+d}"] = _assert_within_density_cap(
            "S6_VERDICT", value, f"policy {policy_gap:+d} vs capacity {capacity_gap:+d}")
    return ("S6_VERDICT refuses to rank two options that both fail to add a saving "
            f"({', '.join(f'{k} {v}w' for k, v in widths.items())})")


# ---------------------------------------------------------------------------
# Issue #131, REVIEW ROUND 2. The round-one fix turned every aborting guard
# into an INVERSION, which left each clause binary -- render the confident
# claim, or render the confident opposite -- with no state for "the data does
# not settle this". Degenerate inputs then selected a confident branch.
#
# Every case below drives a DEGENERATE input the previous round's tests never
# fed the formulas: zero observations, an exact zero magnitude, a negative
# one, mixed signs across two artifacts, a non-finite quotient. Each is
# ungated wherever the token permits (household answers stubbed), because CI
# runs this suite without the private archive.
# ---------------------------------------------------------------------------
@case
def case_the_three_claim_states_are_three_and_the_third_names_its_quantity():
    """The mechanism itself, driven directly: two states RENDER and the third
    REFUSES, naming both the token and the quantity that was indeterminate.
    Without this, "we introduced a tri-state" is a claim about the code rather
    than about its behaviour."""
    assert rt._claim("T", "x", rt.SUPPORTED, "d") is True
    assert rt._claim("T", "x", rt.SUPPORTED_OPPOSITE, "d") is False
    try:
        rt._claim("TOKEN_X", "whether the widget widgets", rt.NOT_DETERMINED,
                  "artifact says 0 of 0")
        raise AssertionError("_claim rendered a NOT_DETERMINED clause")
    except SystemExit as e:
        for expected in ("TOKEN_X", "whether the widget widgets", "artifact says 0 of 0",
                         "do not settle it"):
            assert expected in str(e), (
                f"the NOT_DETERMINED refusal does not carry {expected!r}: {e}")
    # The override exists for the one obstacle that is not the artifacts.
    try:
        rt._claim("T", "x", rt.NOT_DETERMINED, "d", unsettled="the template asserts it")
        raise AssertionError("_claim rendered a NOT_DETERMINED clause")
    except SystemExit as e:
        assert "the template asserts it" in str(e), e
    # No bare three-way sign helper survives in the module: the two
    # comparisons that used one were both reading pairs a sign test could not
    # settle, and leaving it available is how that gets written a third time.
    assert not hasattr(rt, "_sign"), (
        "report_tokens grew a bare _sign() helper back; a clause branching on one "
        "magnitude goes through _sign_claim, and a PAIR of fields is compared only "
        "against its own traced relationship")
    assert rt._sign_claim("T", "x", 3, "d") is True
    assert rt._sign_claim("T", "x", 0, "d") is False
    assert rt._sign_claim("T", "x", -3, "d") is False
    try:
        rt._sign_claim("TOKEN_Y", "whether the widget widgets", float("nan"), "d")
        raise AssertionError("_sign_claim ranked a nan magnitude")
    except SystemExit as e:
        assert "TOKEN_Y" in str(e), e
    assert rt._finite(0, -1.5, 2)
    for bad in (float("nan"), float("inf"), float("-inf"), None, "3", True):
        assert not rt._finite(bad), f"_finite accepted {bad!r} as a magnitude"
    return ("_claim renders SUPPORTED and SUPPORTED-OPPOSITE and refuses NOT_DETERMINED "
            "naming its token, its subject and the artifact values behind it")


def _battery_reading(s0, s7):
    """(section 0's verdict, section 7's verdict) on the SAME battery, as the
    two words a reader would take away."""
    def read(value, sells, denies):
        # Section 0's selling clause has TWO shapes since round 6's finding 6
        # -- "a sound optional buy at a 6.2-6.5-year payback" where every
        # payback lands inside the warranted life, and "repays in N years,
        # past its 10-year warranty" where it does not. Both say the battery
        # repays; only one recommends buying it, which is a distinction this
        # helper is not the place to make.
        if any(s in value for s in sells):
            return "repays"
        if denies in value:
            return "does not repay"
        raise AssertionError(f"neither battery clause is present: {value}")
    return (read(s0, ("sound optional buy", "-year warranty"),
                 "does not repay its own cost"),
            read(s7, ("adds its own",), "never repays its own cost"))


@case
def case_a_battery_worth_less_after_the_free_fix_still_gets_a_report():
    """ROUND 4, FINDING 1. The guard here required packages.MID's two
    battery-alone savings to AGREE IN SIGN, on the theory that two committed
    figures for one battery pointing opposite ways is a contradiction between
    artifacts.

    They are not two figures for one battery. Trace them through
    analysis/package_results.py: battery_alone_yr is
    battery_dispatch_policies.json's pw3.greedy.save, the battery billed
    against the UNSHIFTED baseline, and battery_alone_post_ev_fix_yr is that
    artifact's post_behavior.mid.battery_marginal, the same battery billed
    against the EV-SHIFTED year. Different scenarios, and CLAUDE.md section 1b
    describes exactly that overlap as the expected result of modelling
    behavior and hardware together.

    So a household whose battery value is mostly EV arbitrage the free fix
    already captures -- +$2,328 before the fix, -$50 after it -- is an
    ORDINARY household with an ordinary report to generate, and the guard
    aborted all fifteen sections of it. This case drives that household and
    asserts the report RENDERS, which is the assertion the previous round's
    case could not make: it moved both fields together, so the one shape that
    reproduces the defect never appeared in it.

    What must still hold is that the two sentences agree with EACH OTHER, off
    the post-fix scenario, which is the finding-1 property from round two."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    mid = rt._json("package_results.json")["packages"]["MID"]
    live = (mid["battery_alone_yr"], mid["battery_alone_post_ev_fix_yr"])
    assert min(live) > 0, (
        f"data/package_results.json already puts a battery-alone saving at {min(live)}/yr")
    readings, widths = {}, {}
    with _stub_plan(cheapest, provider):
        s0, s7 = rt.resolve_token("S0_VERDICT"), rt.resolve_token("S7_VERDICT")
        readings["published"] = _battery_reading(s0, s7)

        # 1. THE REPRODUCTION. Every ordered pair whose two scenarios point
        #    different ways, starting with the review's own (+2,328, -50).
        #    Each must RENDER -- both sentences -- and each must read off the
        #    POST-fix scenario, which is the one the packages are built on.
        for pre, post in ((2328, -50), (-100, live[1]), (live[0], -100),
                          (0, live[1]), (live[0], 0), (-100, 0), (0, -100)):
            label = f"pre {pre}, post {post}"
            with _mid_battery_at(mid, pre, post):
                s0_at = rt.resolve_token("S0_VERDICT")
                s7_at = rt.resolve_token("S7_VERDICT")
            pair = _battery_reading(s0_at, s7_at)
            expected = "repays" if post > 0 else "does not repay"
            assert pair == (expected, expected), (
                f"at {label} the two sections read {pair}, which is neither one verdict "
                f"nor the post-fix scenario's own ({expected})")
            widths[f"S0 {label}"] = _assert_within_density_cap("S0_VERDICT", s0_at, label)
            widths[f"S7 {label}"] = _assert_within_density_cap("S7_VERDICT", s7_at, label)
            readings[label] = pair

        # 2. Sign-AGREEING pairs still render and still agree, so the case
        #    discriminates rather than passing on a formula that never refuses.
        for label, pre, post in (("both zero", 0, 0),
                                 ("both losing", -100, -400),
                                 ("both positive, different sizes",
                                  live[0] * 2, live[1])):
            with _mid_battery_at(mid, pre, post):
                s0_at = rt.resolve_token("S0_VERDICT")
                s7_at = rt.resolve_token("S7_VERDICT")
            pair = _battery_reading(s0_at, s7_at)
            assert pair[0] == pair[1], (
                f"sections 0 and 7 reached opposite verdicts on the same battery at "
                f"{label} ({pre}/{post} per year): {pair}")
            widths[f"S0 {label}"] = _assert_within_density_cap("S0_VERDICT", s0_at, label)
            widths[f"S7 {label}"] = _assert_within_density_cap("S7_VERDICT", s7_at, label)
            readings[label] = pair
        assert len({p[0] for p in readings.values()}) > 1, (
            f"every arm read the same way, so agreement proves nothing: {readings}")

        # 3. A NON-FINITE post-fix saving settles nothing -- that is the state
        #    the sign gate was standing in for, and it is the one that remains.
        for bad in (float("nan"), float("inf")):
            with _swapped(mid, "battery_alone_post_ev_fix_yr", bad):
                for token in ("S0_VERDICT", "S7_VERDICT"):
                    try:
                        value = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} read a battery verdict off a {bad} saving: {value}")
                    except SystemExit as e:
                        assert token in str(e), e
                        assert "whether the battery repays its own cost" in str(e), (
                            f"{token}'s refusal does not name the indeterminate "
                            f"quantity: {e}")

        assert rt.resolve_token("S0_VERDICT") == s0 and rt.resolve_token("S7_VERDICT") == s7, (
            "the substituted battery savings leaked out of this case")
    return ("a battery worth +$2,328 before the free EV fix and -$50 after it gets a "
            "report, with sections 0 and 7 reading the post-fix scenario together across "
            f"every mixed pair ({'; '.join(f'{k}: {v[0]}' for k, v in readings.items())})")


@case
def case_the_two_battery_scenarios_are_never_cross_checked_against_each_other():
    """The same finding, asserted on the RESOLVER rather than on a sentence:
    no branch anywhere in this module may key on the two battery-alone savings
    agreeing.

    Driven by holding the post-fix scenario FIXED at the committed artifact's
    own value and sweeping the pre-fix one across both signs and zero. Every
    figure the resolver reports about the post-fix scenario -- the verdict,
    the saving, the payback -- has to come back identical, because none of
    them is a function of the other scenario. A guard that reads the pair
    fails here the moment the sweep crosses zero."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    post = mid["battery_alone_post_ev_fix_yr"]
    assert post > 0, post
    baseline = None
    swept = []
    for pre in (-5000, -1, 0, 1, 5000, post, post * 3):
        with _mid_battery_at(mid, pre, post):
            repays, saving, payback, _quotable = rt._battery_alone("PROBE")
        got = (repays, saving, payback)
        if baseline is None:
            baseline = got
        assert got == baseline, (
            f"the post-fix verdict moved from {baseline} to {got} when the UNSHIFTED "
            f"baseline scenario's saving changed to {pre}/yr -- the two scenarios are "
            "still cross-checked against each other")
        swept.append(pre)
    return ("the post-fix battery verdict, saving and payback are unchanged across "
            f"pre-fix baseline savings of {swept}, so no branch reads the two scenarios "
            "against each other")


# THE THREE SLOTS THAT USED TO ASSERT THE WIN (issue #196). Each of these was
# fixed markup no token could reach, so BEST_PLAN and the two annual cells
# refused rather than render figures the chrome around them contradicted --
# and a refusal is not a missing sentence, it is no report at all. They are
# named here as literals that must be GONE: if any comes back, the tokens
# below are decorating a page that has already made the claim for them.
_S3_CHROME_ASSERTIONS = (
    "Best plan in every scenario — the solid conclusion",
    "Why {{BEST_PLAN}} wins:",
    '<tr class="win"><td>{{BEST_PLAN}}',
)
# THE SAME ASSERTION, ONE LAYER BACK: the AUTHORING PROMPTS. Sections 0 and 3
# each carry <!-- TODO --> blocks that generate_report.py hands to a model
# together with the token values in that block's own scope, and a reference
# voice reading "stay on {{BEST_PLAN}} -- cheapest with or without a battery
# ... and a battery only widens its lead" is the fixed win-claim again, in the
# one place no rendered-markup case looks. It shipped: BEST_PLAN resolves for a
# beaten household now, so on the path issue #196 newly opens the prompt told
# the model to recommend a plan the card beside it calls beaten, under a
# verdict line saying a cheaper plan exists.
#
# Phrases that ASSERT this household's plan is the cheapest one. A prompt is
# free to use any of them -- the winning household needs a reference voice too
# -- but only tied to the token that decides the standing, never as the flat
# instruction they were.
_PLAN_WIN_ASSERTIONS = ("stay on", "clear of the runner-up", "widens its lead",
                        "wins", "cheapest with or without")
# The tokens that STATE the standing. Naming one of these inside the block is
# what puts its value in the block's own scope (report_blocks.tokens_mentioned,
# or already-live-in-section) and so in front of the model that reads the
# prompt.
_PLAN_STANDING_TOKENS = ("S0_BEST_PLAN_CARD", "S0_VERDICT", "S3_VERDICT",
                         "S3_WHY_LEAD", "S3_ROW_CLASS", "S3_ROW_PLAN_CELL")
# What replaced them, keyed by the slot each one fills: the section 0 card's
# label, the class on section 3's household row, and the bold lead-in over the
# paragraph that explains the ranking.
_S3_CHROME_SLOTS = {
    "card": '<div class="lbl">{{S0_BEST_PLAN_CARD}}</div>',
    "row": '<tr class="{{S3_ROW_CLASS}}">',
    "cell": "<td>{{S3_ROW_PLAN_CELL}}</td>",
    "lead": "{{S3_WHY_LEAD}}</b>",
}
# The token each slot exists to carry -- what a case comparing that slot's
# markup has to find inside it.
_S3_SLOT_TOKENS = {"card": "S0_BEST_PLAN_CARD", "row": "S3_ROW_CLASS",
                   "cell": "S3_ROW_PLAN_CELL", "lead": "S3_WHY_LEAD"}
# The two families are rendered from different artifacts, and NEITHER is gated
# any more: section 4's row carries {{S4_ROW_CLASS}} (issue #178) and section
# 3's three slots carry the tokens above (issue #196). Both state the standing
# their own artifact supports -- see the two cases below and
# case_section_4s_row_class_tracks_the_matrix_its_cells_come_from.
_CSV_PLAN_TOKENS = ("BEST_PLAN", "BEST_PLAN_ANNUAL_CCA", "BEST_PLAN_ANNUAL_BUNDLED")
_MATRIX_PLAN_TOKENS = ("BEST_PLAN_NOBATT_MODELED", "BEST_PLAN_BATT_MODELED",
                       "BATTERY_VALUE_BEST_PLAN")
_BEST_PLAN_TOKENS = _CSV_PLAN_TOKENS + _MATRIX_PLAN_TOKENS


def _s3_chrome_lines():
    """{slot: the one template line that fills it}, with the fixed assertions
    those slots used to carry checked GONE.

    One line each, asserted: a second copy of any of these is a second place
    the page states this household's standing, and the cases below could not
    say which markup a reader is shown."""
    template = rt.TEMPLATE.read_text()
    for literal in _S3_CHROME_ASSERTIONS:
        assert literal not in template, (
            f"report-template.html has gone back to asserting the plan ranking as fixed "
            f"markup ({literal!r}). The tokens {sorted(_S3_CHROME_SLOTS)} cannot make that "
            "true for a household the CSV ranks second, which is the whole of issue #196")
    lines = {}
    for slot, needle in _S3_CHROME_SLOTS.items():
        hits = [ln for ln in template.splitlines() if needle in ln]
        assert len(hits) == 1, (
            f"report-template.html carries {len(hits)} line(s) holding {needle!r}, not the "
            f"one {slot} slot these cases read: {hits}")
        lines[slot] = hits[0]
    return lines


def _s3_chrome_markup():
    """{slot: the markup a reader would be shown}, filled the way
    generate_report.py fills it -- every {{TOKEN}} replaced by its resolved
    value, HTML-escaped -- so these cases read the page and not a paraphrase.

    Two deliberate stops. The line is CUT at its first <!-- TODO -->: what
    follows is the LLM's brief and the fragment it will write, neither of
    which these tokens own (the lead-in's brief also quotes its own token, so
    deleting the comment in place would leave a second copy of the value in
    the "markup"). KNOWN_GAPS tokens are left standing as {{NAME}}:
    resolve_token refuses them by design (UTILITY_TOOL_BEST_PLAN_FIGURE has no
    committed source) and generate_report fills them from a human block, so a
    case that resolved them would assert something this pipeline never
    renders."""
    out = {}

    def fill(m):
        name = m.group(1)
        if rt.TOKENS.get(name, {}).get("kind") == "gap":
            return m.group(0)
        return _htmllib.escape(rt.resolve_token(name), quote=True)

    for slot, line in _s3_chrome_lines().items():
        out[slot] = re.sub(r"\{\{([A-Z0-9_]+)\}\}", fill, line.split("<!--")[0])
    return out


@case
def case_section_3s_plan_chrome_states_what_the_ranking_supports():
    """ISSUE #196. Section 3's household row, section 0's plan card and the
    "Why ... wins:" lead-in all ASSERTED that this household is on the
    cheapest plan, in fixed markup no token could reach. So BEST_PLAN and the
    two annual cells inside that row failed closed instead -- naming the
    chrome as the reason -- and generate_report folds a refusal into
    `failures`, which stops the run. A household one dollar off the cheapest
    plan got NO REPORT AT ALL over its own plan's name and its own two
    modeled bills. Section 4's row was the same defect; issue #178 fixed that
    half.

    THE THREE STATES ARE DRIVEN THROUGH THE THREE SLOTS AT ONCE, in the
    rendered markup rather than in a helper's return value, because the claim
    at issue is what the page says. A rival priced $1 above this household, at
    exactly its total, and $1 below it: sole winner, genuine tie, beaten.
    Identity is the stored `==` -- $1 IS an ordering, and widening it into a
    band is what once put a runner-up into the winners' set (issue #141).

    AND THEY ARE CHECKED AGAINST THE TWO VERDICT SENTENCES, in every state.
    The card sits a few hundred pixels above section 3's verdict and the
    lead-in sits inside the section; a card calling this plan the best over a
    sentence saying it is not is the contradiction that made the gate look
    necessary. Each of the four assertions below is an IFF against
    S3_VERDICT's own sole-winner clause, so neither side can drift: the row
    paints `win` exactly when the verdict says the plan is still cheapest, the
    card claims "Best plan" exactly then, the lead-in says "Why <this plan>
    wins" exactly then, and section 0's headline says the rate plan is right
    exactly then."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    seen = {}
    with _stub_plan(cheapest, provider):
        # The card counts THREE scenarios and only one of them is the CSV this
        # case reprices, so its published wording is only reachable while the
        # other two also rank this plan alone cheapest. Asserted, not assumed.
        assert rt.resolve_token("S4_ROW_CLASS") == "win", (
            "this checkout's battery matrix does not price the household's plan alone "
            "cheapest in both columns, so section 0's card cannot reach its "
            "every-scenario wording and this case cannot be driven here")
        assert rt._wildcard_scenario(rt.CTX)[1] == "win", (
            "this checkout's deep_results.json:wildcard does not rank the household's "
            "plan ahead, so section 0's card cannot reach its every-scenario wording")
        _baseline_ok, baseline_refused = _sweep_every_token()
        # The card reads BOTH matrix columns, through the same helper section
        # 4's row class ranks with, and counts them against a CSV standing and
        # a wildcard standing. So every standing that helper can hand it has to
        # be one this module recognises, or the card is scoring a state it
        # cannot read.
        for standing in rt._bpm_standing_pair("S0_BEST_PLAN_CARD"):
            assert standing in rt._PLAN_STANDINGS, (
                f"the matrix column standing {standing!r} is not one of "
                f"{list(rt._PLAN_STANDINGS)}, so section 0's card cannot score it")
        for standing, rival_total in (("win", own + 1), ("tie", own),
                                      ("trails", own - 1)):
            with _plan_repriced(provider, {cheapest: own, rival: rival_total}):
                markup = _s3_chrome_markup()
                card = rt.resolve_token("S0_BEST_PLAN_CARD")
                lead = rt.resolve_token("S3_WHY_LEAD")
                row_class = rt.resolve_token("S3_ROW_CLASS")
                s3, s0 = rt.resolve_token("S3_VERDICT"), rt.resolve_token("S0_VERDICT")
                # THE CLAIM THAT MATTERS is not "this sentence renders" but
                # "this household gets a report", so the whole set is swept
                # in every state, catching BaseException -- against the
                # winning path's own sweep, so this runs with or without the
                # private archive (see _assert_no_new_refusals).
                rendered, refused = _sweep_every_token()
            _assert_no_new_refusals(baseline_refused, refused,
                                    f"a household in the {standing} state")

            # 1. NO SLOT MAY CONTRADICT THE VERDICT UNDER IT. Four iffs, one
            #    per slot plus section 0's headline clause, all against
            #    section 3's own sole-winner sentence.
            wins = "is still the cheapest plan" in s3
            assert wins == (standing == "win"), (
                f"section 3's verdict does not report the {standing} state a rival priced "
                f"{rival_total - own:+.0f} against {cheapest} produces: {s3}")
            assert ('class="win"' in markup["row"]) == wins, (
                f"section 3's row paints {row_class!r} while its verdict says {s3!r}: "
                f"{markup['row']}")
            assert card.startswith("Best plan in every scenario") == wins, (
                f"section 0's card reads {card!r} under a section 3 verdict reading {s3!r}")
            assert (f"Why {cheapest} wins:" in markup["lead"]) == wins, (
                f"section 3's lead-in reads {lead!r} while its verdict says {s3!r}")
            assert ("the rate plan is right" in s0) == wins, (
                f"section 0's headline clause and section 3's verdict disagree about the "
                f"same ranking: {s0!r} vs {s3!r}")

            # 2. AND EACH STATE SAYS THE PARTICULAR TRUE THING, in the markup.
            if standing == "win":
                assert row_class == "win", row_class
                assert lead == f"Why {cheapest} wins:", lead
                assert card.startswith("Best plan in every scenario tested ("), card
            elif standing == "tie":
                assert row_class == "s3-tie" and 'class="s3-tie"' in markup["row"], markup
                assert lead == f"Why {cheapest} ties {rival}:", lead
                assert card.startswith("Cheapest plan in every scenario tested ("), card
                assert "level with a rival" in card, card
                assert f"ties {rival}" in s3, s3
            else:
                assert row_class == "s3-trails" and 'class="s3-trails"' in markup["row"], \
                    markup
                assert lead == f"Why {rival} wins instead:", lead
                assert "Best plan" not in card and "every scenario tested" not in card, card
                assert "beaten in the rest" in card, card
                assert "is not the cheapest plan" in s3, s3
            assert set(rendered) >= set(_BEST_PLAN_TOKENS), (
                f"{sorted(set(_BEST_PLAN_TOKENS) - set(rendered))} refused in the "
                f"{standing} state -- these six are the cells the two chrome gates used "
                "to take the whole report down over")
            seen[standing] = f"{row_class} row, lead-in {lead!r}, {len(rendered)} tokens"

        # Nothing leaked: the published state is what it was before this case.
        assert rt.resolve_token("S3_ROW_CLASS") == "win", (
            "the substituted plan total leaked out of this case")
    return ("section 3's row, its lead-in and section 0's card each state the standing "
            "data/plan_results.csv supports, agree with both verdict sentences in all "
            "three states, and leave the whole token set resolvable ("
            + "; ".join(f"{k}: {v}" for k, v in seen.items()) + ")")


@case
def case_a_household_second_in_both_rankings_still_gets_a_whole_report():
    """ISSUE #196's acceptance case, and the one #178's could not make: a
    household ranked second in data/plan_results.csv AND in
    data/battery_plan_matrix.json, sweeping the WHOLE token set.

    #178 took the matrix half of the gate down, so a household second in the
    matrix alone already got its report -- but moving a rival $1 under it in
    the CSV was still enough on its own to refuse BEST_PLAN,
    BEST_PLAN_ANNUAL_CCA and BEST_PLAN_ANNUAL_BUNDLED, and three refusals are
    an empty index.html. Both rankings are moved here because that is the
    household the issue names, and because a fix that only reached one
    artifact would pass a case that only moved one.

    BaseException, through _resolve_every_token: resolve_token refuses with
    SystemExit, which inherits from BaseException, and an `except Exception`
    walks straight past it. The claim is "this household gets a report"."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    plans = rt._json("battery_plan_matrix.json")["plans"]
    assert cheapest in plans, (
        f"the CSV's cheapest plan {cheapest!r} is not priced in battery_plan_matrix.json "
        f"({sorted(plans)}); this case cannot put the household second in both")
    m_rival = min((p for p in plans if p != cheapest),
                  key=lambda p: plans[p]["no_battery"])
    beaten = {m_rival: (plans[cheapest]["no_battery"] - 1,
                        plans[cheapest]["with_battery"] - 1)}
    with _stub_plan(cheapest, provider):
        published, baseline_refused = _sweep_every_token()
        with _plan_repriced(provider, {cheapest: own, rival: own - 1}), \
                _matrix_priced(plans, beaten):
            rendered, refused = _sweep_every_token()
            row_class = rt.resolve_token("S3_ROW_CLASS")
            s4_class = rt.resolve_token("S4_ROW_CLASS")
            card = rt.resolve_token("S0_BEST_PLAN_CARD")
        _assert_no_new_refusals(baseline_refused, refused,
                                "a household ranked second in both artifacts")
        assert set(rendered) == set(published), (
            "the token set is not the same for a household ranked second in both "
            f"artifacts: {sorted(set(published) - set(rendered))}")
        for token in _BEST_PLAN_TOKENS:
            assert rendered[token], f"{token} rendered blank"
        assert rendered["BEST_PLAN"] == cheapest, rendered["BEST_PLAN"]
        # Neither row claims the win, and the card counts instead of
        # quantifying -- the three slots and section 4's row all inverted.
        assert row_class == "s3-trails" and s4_class == "trails", (row_class, s4_class)
        assert "Best plan" not in card and "every scenario tested" not in card, card
        assert _sweep_every_token()[0] == published, (
            "the substituted totals leaked out of this case")
    return (f"a household priced $1 above {rival} in data/plan_results.csv and $1 above "
            f"{m_rival} in both columns of data/battery_plan_matrix.json resolves the same "
            f"{len(rendered)} tokens the winning path does -- {sorted(_BEST_PLAN_TOKENS)} "
            "included, all six of "
            "which the two chrome gates used to refuse -- with section 3's row reading "
            f"{row_class!r}, section 4's {s4_class!r} and the card counting scenarios "
            f"({card!r})")


@case
def case_section_0s_card_counts_every_scenario_it_names():
    """The card's label quantifies -- "in every scenario tested (no-battery,
    battery×plan matrix, <rival> wildcard)" -- over three artifacts, and it
    used to be fixed text over three rankings nothing read.

    So each of the three is driven to a LOSS on its own, with the other two
    left winning. Each must drop the count by one and must take the
    every-scenario wording off the card, while the parenthetical keeps naming
    all three: a scenario that was tested and lost is still a scenario that
    was tested. A scenario that changed nothing would be a scenario the label
    names and does not read -- which is the fixed-text defect with a token
    wrapped round it.

    THE MATRIX IS DRIVEN TO A WHOLE LOSS HERE, both columns together, which is
    the only drive that leaves the count at two of three with nothing carved
    out of it. A matrix lost in ONE column is a different published sentence
    and case_section_0s_card_reads_a_half_won_battery_matrix owns it.

    The wildcard is moved by repricing data/deep_results.json:wildcard's own
    key for this household's plan, since that artifact's keys are prose and
    the module identifies the household's entry by parsing them."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    plans = rt._json("battery_plan_matrix.json")["plans"]
    m_rival = min((p for p in plans if p != cheapest),
                  key=lambda p: plans[p]["no_battery"])
    wildcard = rt._json("deep_results.json")["wildcard"]
    ours = [k for k in wildcard
            if rt._wildcard_key(k)[0] == cheapest]
    assert len(ours) == 1, (
        f"deep_results.json:wildcard names {ours} for {cheapest}; this case moves exactly "
        "one entry")
    dearest = max(wildcard.values())

    seen = {}
    with _stub_plan(cheapest, provider):
        published = rt.resolve_token("S0_BEST_PLAN_CARD")
        assert published.startswith("Best plan in every scenario tested ("), (
            f"this checkout's artifacts do not put the household's plan alone cheapest in "
            f"all three scenarios, so this case cannot drive them one at a time: "
            f"{published}")
        named = published[published.index("(") + 1:published.index(")")].split(", ")
        assert len(named) == 3, named
        for scenario, losing in (
                ("no-battery", _plan_repriced(provider, {cheapest: own, rival: own - 1})),
                ("battery×plan matrix",
                 _matrix_priced(plans, {m_rival: (plans[cheapest]["no_battery"] - 1,
                                                  plans[cheapest]["with_battery"] - 1)})),
                (named[2], _swapped(wildcard, ours[0], dearest + 1))):
            with losing:
                card = rt.resolve_token("S0_BEST_PLAN_CARD")
            assert card.startswith("Cheapest in 2 of the 3 scenarios tested ("), (
                f"losing the {scenario} scenario did not drop section 0's card to two of "
                f"three; the card names that scenario and does not read it: {card}")
            assert "every scenario tested" not in card and "Best plan" not in card, card
            # A WHOLE loss carries no exception clause: the split wording is
            # for a matrix cheapest in one of its two columns, and stamping it
            # on a scenario lost outright would be the mirror of the defect
            # case_section_0s_card_reads_a_half_won_battery_matrix is about.
            assert "except in one of the battery×plan matrix" not in card, (
                f"losing the {scenario} scenario outright published the half-won matrix's "
                f"exception clause: {card}")
            for phrase in named:
                assert phrase in card, (
                    f"the card stopped naming the {phrase!r} scenario once this household "
                    f"lost the {scenario} one; a scenario that was tested and lost is "
                    f"still a scenario that was tested: {card}")
            seen[scenario] = card
        assert rt.resolve_token("S0_BEST_PLAN_CARD") == published, (
            "a substituted artifact leaked out of this case")
    return (f"section 0's card reads all three scenarios it names ({', '.join(named)}): "
            "each one driven to a loss on its own drops the count to two of three and "
            "takes the every-scenario claim off the card, and all three stay named")


def _wildcard_priced(totals):
    """data/deep_results.json:wildcard substituted whole, restored on the way
    out -- the same in-memory contract _swapped and _matrix_priced keep.

    Keys are built in the artifact's own prose shape ("<plan> + PW3", "<plan>
    no battery"), because _wildcard_totals identifies the plan and the
    configuration by parsing them there, and the plans are ones
    data/plan_results.csv prices (_wildcard_plans), because it refuses any
    other; a case that passed bare or invented plan names would be driving a
    shape no run of the deep-dive workup produces."""
    return _swapped(rt._json("deep_results.json"), "wildcard", totals)


def _wildcard_plans():
    """(plan, rivals): the cheapest plan data/plan_results.csv prices for the
    provider _plan_ranking_inputs ranks, and every other plan it prices,
    sorted -- the only names _wildcard_totals accepts in a wildcard key."""
    _provider, plan, priced = _plan_ranking_inputs()
    rivals = sorted({r["plan"] for r in priced} - {plan})
    assert len(rivals) >= 2, (
        f"data/plan_results.csv prices {len(rivals)} plan(s) beside {plan}; the "
        "wildcard cases need two rivals")
    return plan, rivals


@case
def case_a_non_finite_wildcard_total_drops_the_scenario_instead_of_ranking_the_rest():
    """A nan in data/deep_results.json:wildcard used to be FILTERED OUT and
    the surviving totals ranked -- `[t for t in ours if _finite(t)]` on this
    household's side, `any(_finite(t) ...)` on the rivals' -- while section
    0's card went on counting the wildcard as a scenario it had scored.

    WHICH INVERTS THE STANDING, not merely blurs it. The discarded total is
    exactly the one that could have beaten this plan: a rival priced [90, 300]
    against our 100 TRAILS, and with the 90 poisoned the filter ranks us
    against the 300 and publishes a WIN -- inside a label reading "Best plan
    in every scenario tested (..., <rival> wildcard)", over a comparison whose
    cheaper side the artifact never priced. That is this branch's own
    mixed-matrix finding one input along: the computation drops a value and
    the sentence keeps counting it.

    THE FIX IS A DROP, NOT A REFUSAL, and the two halves are asserted
    separately below. _bpm_cheapest and _plan_ranking REFUSE a non-finite cell
    because section 3's verdict, section 4's row class and section 7's footing
    all have to state that ranking and have no absent state to land in
    (case_a_non_finite_rival_cell_refuses_rather_than_electing_a_runner_up_by
    _key_order holds that, and this case must not loosen it). The wildcard
    does have one: _wildcard_scenario already returns None for a workup that
    prices a single plan, and the card drops what it is not handed. So the
    card must keep RENDERING here -- one scenario shorter, and identical to
    the label a household with no wildcard plan at all gets.

    Every artifact here is synthetic and every household answer is stubbed, so
    this runs with or without the private archive -- the fail-closed reason
    _stub_household's docstring gives."""
    plan, rivals = _wildcard_plans()
    rival = rivals[0]
    # OURS 100, THEIRS 90 -- the rival wins, so a filter that loses the 90 is
    # visible as a flipped standing rather than as a lost tie.
    clean = {f"{plan} + PW3": 100, f"{plan} no battery": 140,
             f"{rival} + PW3": 90, f"{rival} no battery": 300}
    poisons = {}
    for bad in (float("nan"), float("inf")):
        poisons[f"the rival's cheaper total is {bad!r}"] = {
            **clean, f"{rival} + PW3": bad}
        poisons[f"the rival's dearer total is {bad!r}"] = {
            **clean, f"{rival} no battery": bad}
        poisons[f"one of this plan's own totals is {bad!r}"] = {
            **clean, f"{plan} no battery": bad}
        # EVERY total of the only rival: that plan used to be dropped out of
        # `rivals` wholesale, taking the name out of the card's parenthetical
        # with it and leaving the scenario counted against nobody.
        poisons[f"every total the rival carries is {bad!r}"] = {
            **clean, f"{rival} + PW3": bad, f"{rival} no battery": bad}
    with _stub_household({"household.plan": plan}):
        with _wildcard_priced(clean):
            baseline = rt._wildcard_scenario(rt.CTX)
        assert baseline == (f"{rival} wildcard", "trails"), (
            f"the finite artifact {clean} ranks {plan} behind {rival}, which is the "
            f"standing a filtered ranking has to be able to flip: {baseline}")
        for label, totals in poisons.items():
            with _wildcard_priced(totals):
                got = rt._wildcard_scenario(rt.CTX)
            assert got is None, (
                f"the wildcard artifact where {label} was ranked over its finite subset "
                f"and answered {got!r}; a total the artifact does not carry as a number "
                "cannot be dropped and the remainder ranked")

    # AND THE CARD: it must still render, name one scenario fewer, and land on
    # exactly the label a household whose workup prices ONE plan already gets.
    provider, cheapest, priced = _plan_ranking_inputs()
    assert cheapest == plan, (cheapest, plan)
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    csv_rival = min((r for r in priced if r["plan"] != cheapest),
                    key=lambda r: float(r["total"]))["plan"]
    named = f"{rival} wildcard"
    ranked = {f"{cheapest} + PW3": 100, f"{rival} + PW3": 90, f"{rival} no battery": 300}
    dropped = {f"{cheapest} + PW3": 100}          # no rival priced: the absent state
    cards = {}
    with _stub_plan(cheapest, provider):
        published = rt.resolve_token("S0_BEST_PLAN_CARD")
        for standing, states in (("winning", contextlib.nullcontext()),
                                 ("beaten", _plan_repriced(provider,
                                                           {cheapest: own,
                                                            csv_rival: own - 1}))):
            with states:
                for label, totals in (("ranked", ranked),
                                      ("non-finite", {**ranked,
                                                      f"{rival} + PW3": float("nan")}),
                                      ("absent", dropped)):
                    with _wildcard_priced(totals):
                        cards[standing, label] = rt.resolve_token("S0_BEST_PLAN_CARD")
            assert named in cards[standing, "ranked"], (
                f"the {standing} card does not name the wildcard even with a rankable "
                f"artifact, so this case is not driving it: {cards[standing, 'ranked']}")
            assert named not in cards[standing, "non-finite"], (
                f"the {standing} card still counts a wildcard whose artifact carries a "
                f"nan: {cards[standing, 'non-finite']}")
            assert cards[standing, "non-finite"] == cards[standing, "absent"], (
                "a dropped wildcard does not land where an absent one does, so the drop "
                f"is being special-cased somewhere: {cards[standing, 'non-finite']!r} vs "
                f"{cards[standing, 'absent']!r}")
            counts = [_card_totals(cards[standing, k]) for k in ("ranked", "non-finite")]
            assert counts[0] - counts[1] == 1, (
                f"the {standing} card counted {counts[0]} scenarios with the wildcard "
                f"ranked and {counts[1]} with it dropped: {cards[standing, 'ranked']!r} "
                f"-> {cards[standing, 'non-finite']!r}")
        assert rt.resolve_token("S0_BEST_PLAN_CARD") == published, (
            "a substituted artifact leaked out of this case")
    return ("a non-finite wildcard total drops section 0's card's third scenario instead "
            f"of ranking what is left ({len(poisons)} poisoned artifacts, every one "
            f"answering None, against the finite artifact's {baseline[1]!r} -- the "
            "standing the discarded total used to flip to a 'win'), and the card renders "
            "one scenario shorter in both standings, identical to the absent-wildcard "
            "label: "
            + "; ".join(f"{s}/{k} -> {v[:v.index(')') + 1]}"
                        for (s, k), v in cards.items() if k != "absent"))


def _card_totals(label):
    """How many scenarios section 0's card's label says it tested, read out of
    the sentence rather than recomputed -- _card_claim's rule, narrowed to the
    one number this case compares. The two branches that state no total say
    "every scenario tested" over the parenthetical instead, so the count is
    taken off the names it lists."""
    named = label[label.index("(") + 1:label.index(")")].split(", ")
    m = re.search(r"of the (\d+)", label)
    if m:
        assert int(m.group(1)) == len(named), (
            f"the card says {m.group(1)} scenarios and names {named}: {label}")
    return len(named)


@case
def case_the_plan_prompts_in_sections_0_and_3_assert_no_standing():
    """ISSUE #196, THE PROMPT SIDE. Section 0's item-1 brief read:

        Reference voice: "Plan: stay on {{BEST_PLAN}} — cheapest with or
        without a battery (~{{PLAN_MARGIN_VS_RUNNER_UP}}/yr clear of the
        runner-up), and a battery only widens its lead."

    Fixed text, no condition, and BEST_PLAN resolves for a beaten household
    now -- so on the path this issue opens, generate_report.py handed a model
    that instruction beside an S0_BEST_PLAN_CARD reading "Not the cheapest in
    any of the 3 scenarios tested" and an S0_VERDICT reading "a cheaper rate
    plan exists". Three contradictions on one page, and every rendered-markup
    case passed: the defect was in the brief, not in the render.

    THE RULE, over every TODO block in sections 0 and 3: a block may use a
    phrase that asserts this household's plan is cheapest ONLY if it also
    names a token that STATES the standing, and only if it says what the other
    standing looks like. Naming the token is not decoration -- it is what puts
    that value in the block's own scope (report_blocks.scope_tokens_for_block),
    which is asserted here too, so the model is actually handed the answer it
    is being told to follow rather than pointed at a name it cannot see.

    Sections 0 and 3 only, which is where the standing is stated. Prompts
    elsewhere that lean on the same assumption are reported rather than
    rewritten here; §4's conclusion brief ("by how much it moves the lead") is
    the live example. §7's "All packages keep {{BEST_PLAN}}" was the other one
    and is not any more -- it carries {{S7_PLAN_FOOTING}} now, which states the
    footing the packages are priced on
    (case_section_7s_package_footing_states_the_plan_it_prices_on).

    A TOKEN THAT WROTE THE RECOMMENDATION would move the choice out of the
    model's hands, and was not the fix: "switch to X" is an ACTION, and no
    committed artifact prices switching -- plan_results.csv ranks modeled
    annual totals on one rate vintage, and eligibility, the utility's own
    comparison tool and the switch itself are outside it. Every other sentence
    in this family (S0_VERDICT, S3_VERDICT, S3_WHY_LEAD, this card) states the
    STANDING and stops, so the prompt routes the model to those and lets it
    write the recommendation the standing supports."""
    import report_blocks as rb                                    # noqa: PLC0415

    html = rt.TEMPLATE.read_text()
    blocks = [b for b in rb.parse_todo_blocks(html) if b.section in ("s0", "s3")]
    assert blocks, "no TODO blocks parsed out of sections 0 and 3"
    checked = {}
    for block in blocks:
        asserted = [p for p in _PLAN_WIN_ASSERTIONS if p in block.text]
        if not asserted:
            continue
        named = [t for t in _PLAN_STANDING_TOKENS if t in block.text]
        assert named, (
            f"report-template.html's {block.id} brief asserts this household's plan is "
            f"cheapest ({asserted}) without naming any of {list(_PLAN_STANDING_TOKENS)}, "
            "so the model is told to recommend a plan the page beside it may call beaten")
        scope = rb.scope_tokens_for_block(html, block)
        missing = [t for t in named if t not in scope]
        assert not missing, (
            f"{block.id} points the model at {missing}, which are not in that block's own "
            f"scope -- so generate_report.py never hands the value over and the "
            "instruction cannot be followed")
        assert "beaten" in block.text, (
            f"{block.id} tells the model what to write when this plan is cheapest and "
            "nothing about the standing where it is not; a reference voice with only the "
            "winning branch is the flat instruction again")
        checked[block.id] = (asserted, named)
    assert checked, (
        "no block in sections 0 or 3 uses any of "
        f"{list(_PLAN_WIN_ASSERTIONS)}, so this guard read nothing -- either the phrase "
        "list has gone stale or the plan briefs have been rewritten out from under it")
    return ("every plan brief in sections 0 and 3 that asserts a win ties it to a token "
            "that states the standing, in its own scope, and says what a beaten plan "
            "reads like ("
            + "; ".join(f"{bid}: {a} -> {n}" for bid, (a, n) in sorted(checked.items()))
            + ")")


@case
def case_section_0s_card_reads_a_half_won_battery_matrix():
    """THE MIXED MATRIX, BOTH WAYS ROUND, against the two controls.

    The card scored data/battery_plan_matrix.json as ONE scenario taken at the
    WORSE of its two columns, and three different households came out as one
    sentence:

        loses no-batt, WINS with-batt  ->  "Cheapest in 2 of the 3 ... beaten in the rest"
        WINS no-batt, loses with-batt  ->  the same sentence
        loses BOTH columns             ->  the same sentence

    So a household the matrix ranks cheapest in one of its two columns was
    published as having lost that scenario outright, and no reader could tell
    it from a household that lost both. That is issue #178's finding one
    section along: the weaker of two columns is right for a row's CSS CLASS,
    whose badge may say only what BOTH columns support, and wrong for a
    sentence, which is all this card is.

    ONLY THE MATRIX MOVES. data/plan_results.csv and the wildcard are left as
    committed, so the household stays alone-cheapest in both and every
    difference between the four cards below comes from the two columns this
    case is about.

    WHAT EACH STATE MUST PUBLISH. The count still scores the matrix as a whole
    (cheapest in one of two columns is not cheapest in the matrix -- section
    4's row applies the same rule), and the label carves the won column out of
    its own absolute: a half-won matrix says "beaten in the rest, except in one
    of the battery×plan matrix's two columns, where it is the cheapest plan",
    and a matrix lost outright does not. Both mixed directions publish the same
    sentence, which is correct -- it is true of both -- and WHICH column was
    won is section 4's row to state, so its class is read here too.

    The whole token set is swept in each state, against the winning path's own
    baseline: a household half-priced by the matrix must still get a report,
    which is the claim issue #196 is about."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    plans = rt._json("battery_plan_matrix.json")["plans"]
    m_rival = min((p for p in plans if p != cheapest),
                  key=lambda p: plans[p]["no_battery"])
    own_nb, own_wb = plans[cheapest]["no_battery"], plans[cheapest]["with_battery"]
    exception = ("— beaten in the rest, except in one of the battery×plan matrix's two "
                 "columns, where it is the cheapest plan")
    # (label, the rival's two cells, the wording the state must publish)
    states = (
        ("wins both columns", (own_nb + 1, own_wb + 1),
         "Best plan in every scenario tested ("),
        ("loses the no-battery column only", (own_nb - 1, own_wb + 1),
         "Cheapest in 2 of the 3 scenarios tested ("),
        ("loses the with-battery column only", (own_nb + 1, own_wb - 1),
         "Cheapest in 2 of the 3 scenarios tested ("),
        ("loses both columns", (own_nb - 1, own_wb - 1),
         "Cheapest in 2 of the 3 scenarios tested ("),
    )
    cards, classes = {}, {}
    with _stub_plan(cheapest, provider):
        assert rt.resolve_token("S4_ROW_CLASS") == "win", (
            "this checkout's matrix does not price the household's plan alone cheapest in "
            "both columns, so these four states are not the ones this case describes")
        assert rt._wildcard_scenario(rt.CTX)[1] == "win", (
            "this checkout's wildcard does not rank the household's plan ahead, so the "
            "counts below would not be the ones this case reads")
        _baseline_ok, baseline_refused = _sweep_every_token()
        for label, cells, opening in states:
            with _matrix_priced(plans, {m_rival: cells}):
                cards[label] = rt.resolve_token("S0_BEST_PLAN_CARD")
                classes[label] = rt.resolve_token("S4_ROW_CLASS")
                rendered, refused = _sweep_every_token()
            _assert_no_new_refusals(baseline_refused, refused,
                                    f"a household that {label} of the battery matrix")
            assert set(rendered) >= set(_BEST_PLAN_TOKENS), (
                f"{sorted(set(_BEST_PLAN_TOKENS) - set(rendered))} refused while this "
                f"household {label}")
            assert cards[label].startswith(opening), (
                f"a household that {label} is published as {cards[label]!r}, which does "
                f"not open {opening!r}")
        # THE HALF-WON MATRIX IS DISTINGUISHABLE FROM THE WHOLE LOSS, and each
        # sentence is true of its own state -- the whole of this finding.
        for direction in ("loses the no-battery column only",
                          "loses the with-battery column only"):
            assert cards[direction].endswith(exception), (
                f"a household that {direction} is published without the clause that says "
                f"it is the cheapest plan in the other one: {cards[direction]!r}")
            assert cards[direction] != cards["loses both columns"], (
                "a household cheapest in one of the matrix's two columns is published "
                f"exactly like one that lost both: {cards[direction]!r}")
        assert not cards["loses both columns"].endswith(exception), (
            "a household beaten in BOTH matrix columns is published as cheapest in one of "
            f"them: {cards['loses both columns']!r}")
        assert (cards["loses the no-battery column only"]
                == cards["loses the with-battery column only"]), cards
        # Section 4's row separates the two directions from the total loss, so
        # which column was won is still on the page.
        assert classes["loses the no-battery column only"] == "trails-win", classes
        assert classes["loses the with-battery column only"] == "trails-win", classes
        assert classes["loses both columns"] == "trails", classes
        assert rt.resolve_token("S4_ROW_CLASS") == "win", (
            "a substituted matrix leaked out of this case")
    return ("section 0's card tells a half-won battery matrix from a lost one: "
            + "; ".join(f"{label} -> {cards[label][cards[label].index(')') + 1:].strip()}"
                        for label, _cells, _opening in states))


def _wildcard_phrase_for(rivals, plan):
    """The scenario phrase rt._wildcard_scenario builds for a workup pricing
    `plan` against `rivals` (real plan names, from _wildcard_plans).

    TAKEN FROM THE MODULE, never typed: the phrase is one item of the card's
    ", "-joined parenthetical, so how the rivals are joined is the card's
    punctuation and not this file's opinion. A case that spells the phrase out
    here would keep passing after the joining rule changed under it -- which is
    how three rivals came to publish a five-item list inside a three-item
    count."""
    totals = {f"{plan} + PW3": 100, f"{plan} no battery": 140}
    for i, rival in enumerate(rivals):
        totals[f"{rival} + PW3"] = 200 + i
        totals[f"{rival} no battery"] = 300 + i
    with _stub_household({"household.plan": plan}), _wildcard_priced(totals):
        phrase, _standing = rt._wildcard_scenario(rt.CTX)
    return phrase


# Every standing the wildcard can be in: the card's own vocabulary plus the one
# thing that scenario can do and the others cannot -- not be there at all
# (_wildcard_scenario returns None when deep_results.json cannot rank this
# household's plan against another, and the card DROPS it rather than counting
# a test that did not happen).
_CARD_WILDCARD_STATES = rt._PLAN_STANDINGS + (None,)

# The label's four branches, each parsed back to what the sentence CLAIMS:
# (cheapest_in, tied_in, named, exception). Read out of the sentence rather
# than recomputed from the standings that produced it -- a checker that
# re-derives the counts agrees with the formula by construction and proves
# nothing about the English around them. `exception` is the split-matrix
# carve-out or "".
#
# EVERY BRANCH STATES A TIE COUNT, and this guard used to let two of them not
# to. `tied_in` was None for the "Cheapest in N of the M" and "Not the cheapest
# in any" branches, so the tie assertion below SKIPPED exactly where the label
# was silent about ties -- and the label was silent there because it collapsed
# them: a plan tying in two scenarios and beaten in a third published "Cheapest
# in 2 of the 3 scenarios tested (...) — beaten in the rest", claiming sole
# cheapest in two scenarios it drew. The ambiguity check keyed on (count,
# half-won) and could not see it either, so 108 cases passed over the branch
# that needed them. A guard with a hole in it is worse than none, because it
# gets cited: the count is read out of all four branches now.
#
# TWO WAYS A SENTENCE CAN STATE "no ties", both read here rather than assumed.
# The partial branch adds ", level with a rival in N of those" only when there
# is a tie to declare, so the clause's ABSENCE is the claim "none of the
# counted scenarios is a tie" -- optional group, 0 when it does not fire. The
# last branch counts nothing cheapest at all, and a tie IS a cheapest standing,
# so "not the cheapest in any" says zero ties in as many words.
_CARD_EXCEPTION_RE = (r"(?P<exception>, except in one of the battery×plan matrix's two "
                      r"columns, where it (?:is the cheapest plan|ties for cheapest))?")
_CARD_CLAIM_PATTERNS = (
    (re.compile(r"^Best plan in every scenario tested \((?P<named>.*)\) — "
                r"the solid conclusion$"),
     lambda m, total: (total, 0)),
    (re.compile(r"^Cheapest plan in every scenario tested \((?P<named>.*)\), level with "
                r"a rival in (?P<tied>\d+) of the (?P<total>\d+) — nothing priced "
                r"beats it$"),
     lambda m, total: (total, int(m.group("tied")))),
    (re.compile(r"^Cheapest in (?P<cheapest>\d+) of the (?P<total>\d+) scenarios tested "
                r"\((?P<named>.*)\)(?:, level with a rival in (?P<tied>\d+) of those)?"
                r" — beaten in the rest" + _CARD_EXCEPTION_RE + "$"),
     lambda m, total: (int(m.group("cheapest")),
                       int(m.group("tied")) if m.group("tied") else 0)),
    (re.compile(r"^Not the cheapest in any of the (?P<total>\d+) scenarios tested "
                r"\((?P<named>.*)\) — a cheaper plan exists in each"
                + _CARD_EXCEPTION_RE + "$"),
     lambda m, total: (0, 0)),
)


def _card_claim(label, scenarios):
    """(cheapest_in, tied_in, named, exception) READ OUT OF the card's own
    sentence -- every element of it, from every branch.

    The stated total is checked against the number of scenarios the label was
    handed, and every branch that prints a total prints it in the same place.
    A label no branch matches raises rather than passing unread."""
    for pattern, counts in _CARD_CLAIM_PATTERNS:
        m = pattern.fullmatch(label)
        if not m:
            continue
        if "total" in m.groupdict():
            assert int(m.group("total")) == len(scenarios), (
                f"the card says {m.group('total')} scenarios and was handed "
                f"{len(scenarios)}: {label}")
        cheapest_in, tied_in = counts(m, len(scenarios))
        return (cheapest_in, tied_in, m.group("named").split(", "),
                m.groupdict().get("exception") or "")
    raise AssertionError(
        f"section 0's card produced wording no branch of this guard recognises, so "
        f"nothing here checked whether it is true: {label!r}")


@case
def case_section_0s_card_is_true_of_every_ranking_it_can_be_handed():
    """THE WHOLE PRODUCT OF STANDINGS THAT CAN REACH THE LABEL, enumerated.

    Three artifacts reach section 0's card and one of them is ranked twice:
    data/plan_results.csv's standing, data/battery_plan_matrix.json's two
    COLUMNS, and the wildcard -- which can also be absent. Three standings
    each for the first three, four states for the wildcard: 3 x 3 x 3 x 4 =
    108 combinations, small enough to enumerate exhaustively rather than in
    equivalence classes, so this case does. The two matrix columns are walked
    in both orders, which is how the mixed directions get here.

    Each label is PARSED BACK to what it claims and every claim is checked
    against the standings that produced it:

      * the number of scenarios claimed cheapest is the number of SCORED
        scenarios that are not "trails", where the matrix scores as ONE, at the
        standing both its columns support;
      * the tie count the label states -- in EVERY branch, whether it declares
        one, leaves the declaring clause off, or counts nothing cheapest at
        all -- is the number that are "tie". This assertion used to be skipped
        wherever the branch said nothing about ties, which was exactly where
        the label was collapsing them into outright wins;
      * the stated total is the number of scenarios handed over, and the
        parenthetical names all of them and nothing else;
      * the "except in one of the battery×plan matrix's two columns" clause
        appears EXACTLY when the matrix trails one column and is cheapest in
        the other, and says "is the cheapest plan" / "ties for cheapest" as
        that other column supports;
      * where no exception is stated, every uncounted scenario really is a
        clean loss -- which is what "beaten in the rest" and "a cheaper plan
        exists in each" assert.

    AND THE WORDING SEPARATES THE STATES A READER HAS TO TELL APART: no label
    is published for two different (cheapest-in count, TIE count, matrix pair)
    readings. "Lost half the matrix" against "lost the whole matrix" is the
    pair the split-clause finding was about; "won two" against "won one and
    drew one" is the pair the tie collapse was about, and the key could not see
    it until the tie count went into it. Both are asserted over every reading
    in the enumeration rather than over the two named ones.

    AND THE WILDCARD PHRASE IS DRIVEN AT BOTH SHAPES IT CAN TAKE, one rival
    and three. The parenthetical is a ", "-joined list and the phrase is one
    of its items, so a phrase carrying that separator publishes a list of five
    beside a sentence counting three -- which the `named` assertion below
    catches, but only if it is ever handed a multi-rival phrase. It was not.
    The phrases come from rt._wildcard_scenario's own joining rule rather than
    typed here, so a change to that rule is driven through this whole
    enumeration instead of past it.

    Driven through rt._plan_card_label rather than the artifacts, because 108
    combinations of three artifacts is not a fixture; the artifact path is
    driven by the cases above, which share that function."""
    plan, rivals = _wildcard_plans()
    wildcard_phrases = [_wildcard_phrase_for(rivals[:1], plan),
                        _wildcard_phrase_for(rivals[:3], plan)]
    by_reading, checked = {}, 0
    for wildcard_phrase, csv_standing, nb, wb, wc in itertools.product(
            wildcard_phrases, rt._PLAN_STANDINGS, rt._PLAN_STANDINGS,
            rt._PLAN_STANDINGS, _CARD_WILDCARD_STATES):
        pair = tuple(sorted((nb, wb), key=rt._S4_COLUMN_STANDINGS.index))
        wildcard = None if wc is None else (wildcard_phrase, wc)
        label = rt._plan_card_label(csv_standing, pair, wildcard)
        # The scenarios the card SCORES: the CSV, the matrix as one (at the
        # standing both columns support), and the wildcard when it exists.
        scored = [("no-battery", csv_standing), ("battery×plan matrix", pair[0])]
        if wildcard:
            scored.append(wildcard)
        standings = [s for _p, s in scored]
        cheapest_in, tied_in, named, exception = _card_claim(label, scored)
        truth = sum(s != "trails" for s in standings)
        ties = sum(s == "tie" for s in standings)
        assert cheapest_in == truth, (
            f"the card claims this plan is cheapest in {cheapest_in} of "
            f"{len(standings)} scenarios standing {standings}, where the "
            f"true count is {truth}: {label!r}")
        assert tied_in == ties, (
            f"the card claims {tied_in} tie(s) over standings {standings}, where "
            f"there are {ties}: {label!r}")
        assert named == [p for p, _s in scored], (
            f"the card names {named} for scenarios {[p for p, _s in scored]}: {label!r}")
        half_won = pair[0] == "trails" and pair[1] != "trails"
        assert bool(exception) == half_won, (
            f"the split-matrix clause is {'present' if exception else 'absent'}"
            f" for matrix columns {pair}, which is {'' if half_won else 'not '}"
            f"a half-won matrix: {label!r}")
        if half_won:
            wanted = ("is the cheapest plan" if pair[1] == "win"
                      else "ties for cheapest")
            assert exception.endswith(wanted), (
                f"the matrix's won column stands {pair[1]!r} and the card says "
                f"{exception!r}: {label!r}")
        else:
            # No exception is stated, so the absolutes have to hold: every
            # scenario not counted as cheapest is a clean loss.
            assert truth + sum(s == "trails" for s in standings) \
                == len(standings), (standings, label)
        by_reading.setdefault(label, set()).add((truth, ties, pair))
        checked += 1
    assert checked == 108 * len(wildcard_phrases), checked
    ambiguous = {label: readings for label, readings in by_reading.items()
                 if len({(count, tied, p[0] == "trails" and p[1] != "trails")
                         for count, tied, p in readings}) > 1}
    assert not ambiguous, (
        "one label is published for two readings a reader has to tell apart "
        f"(scenarios won, of those how many only tied, half-won matrix or not): "
        f"{ambiguous}")
    return (f"all {checked} standing combinations that can reach section 0's card "
            f"({len(wildcard_phrases)} wildcard phrasings × 108 standings) parse back to "
            f"the claims they state, ties included, over {len(by_reading)} distinct "
            "labels, none shared between two readings")


def _report_tokens_under_O(source, code):
    """Run `code` under `python -O` against a report_tokens.py built from
    `source`, returning the finished CompletedProcess.

    The mutated module is written to a temp dir placed FIRST on sys.path so it
    shadows the real one; its own imports (household, rates) still resolve out
    of analysis/. Nothing in this repo is touched, and -O is the point: the
    optimiser is what strips an assert statement out of the bytecode."""
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "report_tokens.py").write_text(source)
        return subprocess.run(
            [sys.executable, "-O", "-B", "-c",
             f"import sys; sys.path[:0] = [{td!r}, "
             f"{str(pathlib.Path(rt.__file__).parent)!r}]\n" + code],
            capture_output=True, text=True)


@case
def case_report_tokens_guards_survive_python_dash_O():
    """A GUARD WRITTEN AS `assert` IS ABSENT UNDER `python -O`, which is the
    run where nothing else is watching.

    report_tokens checked its two standing vocabularies -- the matrix's
    per-column words and the CSV's -- with a bare module-level assert. Under
    -O that statement is not compiled at all, so a module whose vocabularies
    had drifted apart imported cleanly and section 0's card went on scoring a
    standing it cannot read, publishing "Best plan in every scenario tested"
    off it. And when the assert DID fire it raised AssertionError at import of
    report_tokens, taking report_blocks, generate_report and every suite down
    with a message in none of this module's refusal vocabulary.

    Both halves are checked here, on a real interpreter with -O actually set:
    the module imports under -O, and a copy whose vocabularies disagree refuses
    under -O by name. And the class is swept rather than the one site --
    report_tokens.py is parsed and asserted to contain no assert statement
    anywhere, module level or inside a function, so the next guard cannot be
    written in the form this one was."""
    source = pathlib.Path(rt.__file__).read_text()
    asserts = [n.lineno for n in ast.walk(ast.parse(source))
               if isinstance(n, ast.Assert)]
    assert not asserts, (
        f"report_tokens.py states a guard as an `assert` at line(s) {asserts}; "
        "`python -O` compiles those out, so the check is absent exactly where the "
        "module is being run for real. Raise instead")

    clean = _report_tokens_under_O(
        source, "import report_tokens as rt; print(rt._plan_card_label("
                "'win', ('win', 'win'), None))")
    assert clean.returncode == 0 and "Best plan in every scenario" in clean.stdout, (
        f"report_tokens does not import under python -O: {clean.returncode}\n"
        f"{clean.stderr}")

    marker = '_S4_COLUMN_STANDINGS = ("trails", "tie", "win")'
    assert source.count(marker) == 1, (
        f"the matrix's standing vocabulary is no longer declared once as {marker!r}; "
        "this case mutates that declaration to drive the guard")
    broken = _report_tokens_under_O(
        source.replace(marker, '_S4_COLUMN_STANDINGS = ("trails", "tie", "beats")'),
        "import report_tokens as rt; print('SCORED:', rt._plan_card_label("
        "'win', ('win', 'win'), None))")
    assert broken.returncode != 0, (
        "under python -O, a report_tokens whose matrix vocabulary ('trails', 'tie', "
        "'beats') no longer matches the CSV's imported cleanly and section 0's card "
        f"scored it anyway: {broken.stdout.strip()!r}")
    assert "SCORED:" not in broken.stdout, broken.stdout
    assert "report_tokens: the matrix's per-column standings" in broken.stderr, (
        f"the refusal does not name this module and what drifted: {broken.stderr!r}")
    assert "AssertionError" not in broken.stderr, (
        "the vocabulary guard still raises AssertionError, which is neither this "
        f"module's refusal vocabulary nor -O-proof: {broken.stderr!r}")
    return ("report_tokens.py states no guard as an `assert` (0 assert statements in "
            f"{len(source.splitlines())} lines), imports under python -O, and refuses "
            "under python -O by name when its two standing vocabularies disagree: "
            + broken.stderr.strip().splitlines()[-1][:110])


def _s9_wildcard_heading():
    """report-template.html's section 9 wildcard heading, rendered -- every
    resolvable {{TOKEN}} filled and escaped the way generate_report.render()
    fills it. The heading names WILDCARD_PLAN; the card a few screens up names
    the same artifact's rivals, and this is the markup where a disagreement
    between the two becomes visible."""
    hits = [ln for ln in rt.TEMPLATE.read_text().splitlines()
            if "{{WILDCARD_PLAN}}" in ln]
    assert len(hits) == 1, (
        f"report-template.html carries {len(hits)} line(s) naming the wildcard plan, "
        f"not the one section 9 heading this case reads: {hits}")
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}",
                  lambda m: _htmllib.escape(rt.resolve_token(m.group(1)), quote=True),
                  hits[0])


@case
def case_the_card_and_section_9s_heading_name_the_same_wildcard_plan():
    """data/deep_results.json:wildcard's keys are PROSE, and this module used
    to parse them twice with two different rules:

        _wildcard_totals   r"\\s*\\+|\\s+no battery"        (the card's rivals)
        _wildcard_plan     r"\\s*\\+\\s*PW3|\\s+no battery"   (section 9's heading)

    with this household's battery written into the second. Both agree on the
    keys this checkout carries, which is why it shipped, and they part company
    on any other battery. A workup labelled "Powerwall-3" left the card saying
    "TOU-DR-P wildcard" while WILDCARD_PLAN resolved to the whole key, so
    section 9 asked "can TOU-DR-P + Powerwall-3 (15 events dodged) + a battery
    beat EV-TOU-5?" -- a battery named twice, a plan name that is not one, and
    a heading naming something the card above it does not.

    So the split lives in ONE place now (_wildcard_key), and this case
    drives batteries that checkout's regex never saw. The heading is rendered
    from report-template.html rather than described, because the disagreement
    was only ever visible in the markup.

    Every household answer is stubbed and every artifact substituted in memory,
    so this runs with or without the private archive."""
    provider, plan, _priced = _plan_ranking_inputs()
    rival = "TOU-DR-P" if plan != "TOU-DR-P" else "TOU-DR1"
    batteries = ["PW3", "Powerwall-3", "PW3 (15 events dodged)",
                 "Powerwall-3 (15 events dodged)", "IQ-Battery-10C", "2xPW3"]
    seen = {}
    with _stub_plan(plan, provider):
        for battery in batteries:
            totals = {f"{plan} + {battery}": 100, f"{plan} no battery": 140,
                      f"{rival} + {battery}": 90, f"{rival} no battery": 300}
            with _wildcard_priced(totals):
                named = rt._wildcard_plan(rt.CTX)
                phrase, standing = rt._wildcard_scenario(rt.CTX)
                heading = _s9_wildcard_heading()
                card = rt._plan_card_label("win", ("win", "win"), (phrase, standing))
            assert named == rival, (
                f"WILDCARD_PLAN reads {named!r} out of a workup pricing {plan} against "
                f"{rival} with a battery labelled {battery!r}; the battery's name is not "
                "part of the plan's")
            assert phrase == f"{rival} wildcard", (
                f"section 0's card calls the same scenario {phrase!r}: {totals}")
            assert named in card and battery not in card, (
                f"the card names a wildcard the heading does not, or carries the battery "
                f"label {battery!r}: {card}")
            assert f"can {named} + a battery" in heading, (
                f"section 9's heading does not ask about {named!r}, the plan the card "
                f"beside it names: {heading}")
            assert battery not in heading, (
                f"section 9's heading names the battery twice once the artifact labels it "
                f"{battery!r}: {heading}")
            seen[battery] = heading.strip()

    # AND THE TWO READ THE SAME LIST. Both are derived from _wildcard_rivals,
    # which is the point of the fix: the heading asks about its first entry and
    # the card names all of them, off one parse of one artifact.
    _plan, others = _wildcard_plans()
    first, last = others[0], others[-1]
    with _stub_household({"household.plan": plan}):
        with _wildcard_priced({f"{plan} + PW3": 100, f"{plan} no battery": 140,
                               f"{last} + PW3": 90, f"{first} + PW3": 95}):
            rivals = rt._wildcard_rivals(plan)
            assert rivals == sorted([first, last]) and first != last, rivals
            assert rt._wildcard_plan(rt.CTX) == rivals[0], (
                "section 9's heading and section 0's card order the same rivals "
                "differently")
            phrase, _standing = rt._wildcard_scenario(rt.CTX)
            assert all(r in phrase for r in rivals), (phrase, rivals)
    return ("section 0's card and section 9's heading name the same wildcard plan for "
            f"every battery label tested ({', '.join(map(repr, batteries))}), through one "
            "split of deep_results.json:wildcard's keys; e.g. "
            f"{seen['Powerwall-3 (15 events dodged)']!r}")


@case
def case_the_wildcard_phrase_stays_one_item_of_the_cards_list():
    """The card lists the scenarios it scored in a ", "-joined parenthetical
    and the wildcard phrase is one of its items, so the phrase may not contain
    that separator. It did: _join_plan_names emits ", " from three names up, so
    a workup pricing three rivals published

        Cheapest in 2 of the 3 scenarios tested (no-battery, battery×plan
        matrix, TOU-DR-1, TOU-DR-2 and TOU-DR-P wildcard)

    -- five items in a list beside a sentence counting three, where the count
    is the whole claim. The rivals are joined with "/" instead, which no reader
    and no split can mistake for the list's own comma, and one rival renders
    exactly as before.

    Driven up to four rivals, and the parenthetical is split the way a reader
    reads it rather than the way it was built."""
    plan, priced = _wildcard_plans()
    rows = []
    for count in range(1, min(4, len(priced)) + 1):
        rivals = priced[:count]
        phrase = _wildcard_phrase_for(rivals, plan)
        assert ", " not in phrase, (
            f"the wildcard phrase for {count} rival(s) carries the card's own list "
            f"separator: {phrase!r}")
        assert all(r in phrase for r in rivals) and phrase.endswith(" wildcard"), (
            f"the phrase for {rivals} names something else: {phrase!r}")
        card = rt._plan_card_label("tie", ("tie", "tie"), (phrase, "trails"))
        named = card[card.index("(") + 1:card.index(")")].split(", ")
        assert named == ["no-battery", "battery×plan matrix", phrase], (
            f"the card's parenthetical reads as {len(named)} items over {count} "
            f"rival(s): {card}")
        rows.append(f"{count} rival(s) -> {phrase!r}")
    assert _wildcard_phrase_for(priced[:1], plan) == f"{priced[0]} wildcard", (
        "the single-rival phrase, which is the one this household publishes, changed")
    return ("the wildcard scenario stays one item of section 0's card's list at every "
            "rival count (" + "; ".join(rows) + ")")


def _wildcard_refusal(totals, plan):
    """The SystemExit message _wildcard_scenario fails closed with over a
    wildcard artifact whose keys break the naming convention, with a render
    treated as the failure -- _refuses' contract, narrowed to this one
    artifact."""
    with _stub_household({"household.plan": plan}), _wildcard_priced(totals):
        try:
            got = rt._wildcard_scenario(rt.CTX)
        except SystemExit as e:
            return str(e)
    raise AssertionError(
        f"_wildcard_scenario RANKED {got!r} off a wildcard artifact whose keys do not "
        f"follow the naming convention, instead of failing closed: {totals}")


@case
def case_the_wildcard_ranks_the_battery_configuration_only():
    """issue #202: _wildcard_scenario used to rank min() over EVERY total on
    each side of data/deep_results.json:wildcard, so a plan priced in several
    configurations was represented by its cheapest one, whichever that was.
    This household's artifact prices the rival with and without a battery and
    its own plan only with one, and the rival's minimum HAPPENED to be the
    battery entry, which is the only reason the published card compared like
    with like. A rival whose no-battery total came in cheapest would have been
    ranked, without a battery, against this plan with one, and the card would
    have counted that as a scenario it scored.

    THE QUESTION IS THE HEADING'S. Section 9 asks "can <rival> + a battery
    beat <plan>?", so the standing is that one pair: this plan's battery
    total against the cheapest rival's battery total. The no-battery totals
    stay in the block as context and never decide it (a standing taken at the
    worst of every shared configuration put "beaten" on the card beside a
    heading whose question the household wins). The configuration is part of
    the key's contract, stated beside the keys in deep_analyses.py and parsed
    by ONE regex here: "<plan> + <battery>" or "<plan> no battery", a plan
    data/plan_results.csv prices, a one-token battery, an optional trailing
    parenthetical note. The note is parsed so that it stays out of the
    configuration and is never compared: runs differ per plan by design. A
    block outside that shape refuses by name -- every probe below is one the
    regex's first draft read as something plausible -- and a block that some
    plan does not carry the battery entry in refuses too, rather than being
    dropped: deep_analyses.py always emits it.

    AND THE HEADING AND THE CARD READ ONE STRUCTURE: a refusal reaches both,
    and a drop (a non-finite total) is the card's absent state and the
    heading's refusal, since the heading has no absent state to land in.

    Every household answer is stubbed and every artifact substituted in
    memory, so this runs with or without the private archive."""
    plan, rivals = _wildcard_plans()
    rival, other = rivals[0], rivals[1]
    drives = [
        ("the rival's cheapest entry is a configuration this plan is not priced in",
         {f"{plan} + PW3": 100, f"{rival} + PW3": 120, f"{rival} no battery": 90},
         "win"),
        ("each side's cheapest entry is a different configuration",
         {f"{plan} + PW3": 100, f"{plan} no battery": 140,
          f"{rival} + PW3": 130, f"{rival} no battery": 120},
         "win"),
        ("the no-battery pair goes the other way and decides nothing",
         {f"{plan} + PW3": 100, f"{plan} no battery": 200,
          f"{rival} + PW3": 120, f"{rival} no battery": 50},
         "win"),
        ("this household's own key shape: a note on one side only",
         {f"{plan} + PW3": 100, f"{rival} + PW3 (15 events dodged)": 120,
          f"{rival} no battery (events hit)": 90},
         "win"),
        ("notes that differ between the sides pair as one configuration",
         {f"{plan} + PW3 (no events)": 100, f"{rival} + PW3 (15 events dodged)": 120},
         "win"),
        ("the rival priced cheaper with the battery",
         {f"{plan} + PW3": 100, f"{rival} + PW3": 90, f"{rival} no battery": 300},
         "trails"),
        ("level with the battery, ahead without it",
         {f"{plan} + PW3": 100, f"{plan} no battery": 140,
          f"{rival} + PW3": 100, f"{rival} no battery": 150},
         "tie"),
        ("two rivals, the dearer one level",
         {f"{plan} + PW3": 100, f"{rival} + PW3": 100, f"{other} + PW3": 120},
         "tie"),
        ("two rivals, the second one cheaper with the battery",
         {f"{plan} + PW3": 100, f"{rival} + PW3": 120, f"{other} + PW3": 90},
         "trails"),
    ]
    seen = []
    with _stub_household({"household.plan": plan}):
        for label, totals, expected in drives:
            with _wildcard_priced(totals):
                got = rt._wildcard_scenario(rt.CTX)
            assert got is not None and got[1] == expected, (
                f"{label}: expected {expected!r}, got {got!r} off {totals}")
            seen.append(f"{label} -> {got[1]}")

    # THE CONVENTION IS ENFORCED, NOT ASSUMED. (probe, artifact, what the
    # refusal must name) -- the first three are the reviewer's regex probes.
    probes = [
        ("a plan name containing ' + '",
         {f"{plan} + PW3": 100, f"{rival} + CEA + PW3": 90}, [f"{rival} + CEA + PW3"]),
        ("an empty configuration",
         {f"{plan} + PW3": 100, f"{rival} + ": 90}, [f"{rival} + "]),
        ("a note outside parentheses",
         {f"{plan} + PW3": 100, f"{rival} + PW3 15 events dodged": 90},
         [f"{rival} + PW3 15 events dodged"]),
        ("a plan data/plan_results.csv does not price",
         {f"{plan} + PW3": 100, "TEST-PLAN + PW3": 90},
         ["TEST-PLAN + PW3", "plan_results.csv"]),
        ("a key outside the convention",
         {f"{plan} + PW3": 100, f"{rival} PW3": 90}, [f"{rival} PW3"]),
        ("one configuration priced twice",
         {f"{plan} + PW3": 100, f"{rival} + PW3 (5 events)": 90,
          f"{rival} + PW3 (15 events)": 120}, [f"{rival} + PW3 (15 events)"]),
        ("two batteries across the block",
         {f"{plan} + PW3": 100, f"{rival} + PW2": 90}, ["PW2", "PW3"]),
        ("the rival priced without the battery",
         {f"{plan} + PW3": 100, f"{rival} no battery": 90}, [rival, "+ PW3"]),
        ("this plan priced without the battery",
         {f"{plan} no battery": 100, f"{rival} + PW3": 90}, [plan, "+ PW3"]),
        ("a second rival priced without the battery",
         {f"{plan} + PW3": 100, f"{rival} + PW3": 120, f"{other} no battery": 50},
         [other, "+ PW3"]),
    ]
    for label, totals, names in probes:
        msg = _wildcard_refusal(totals, plan)
        assert "deep_results.json:wildcard" in msg and all(n in msg for n in names), (
            f"{label}: the refusal does not name {names}: {msg}")

    # AND THE CARD AND THE HEADING. The unlike artifact must not put "beaten"
    # on the card off the rival's no-battery entry; a refused block refuses
    # both readers by name; a non-finite block is the card's absent state and
    # the heading's refusal.
    provider, cheapest, _priced = _plan_ranking_inputs()
    assert cheapest == plan, (cheapest, plan)
    named = f"{rival} wildcard"
    unlike = {f"{plan} + PW3": 100, f"{rival} + PW3": 120, f"{rival} no battery": 90}
    dropped = {**unlike, f"{rival} + PW3": float("nan")}
    refused = {f"{plan} + PW3": 100, f"{rival} no battery": 90}
    absent = {f"{plan} + PW3": 100}
    cards, refusals = {}, {}
    with _stub_plan(plan, provider):
        published = rt.resolve_token("S0_BEST_PLAN_CARD")
        for label, totals in (("unlike", unlike), ("dropped", dropped),
                              ("absent", absent)):
            with _wildcard_priced(totals):
                cards[label] = rt.resolve_token("S0_BEST_PLAN_CARD")
        with _wildcard_priced(unlike):
            heading_plan = rt.resolve_token("WILDCARD_PLAN")
        for token in ("S0_BEST_PLAN_CARD", "WILDCARD_PLAN"):
            with _wildcard_priced(refused):
                try:
                    rt.resolve_token(token)
                except SystemExit as e:
                    refusals[token] = str(e)
        with _wildcard_priced(dropped):
            try:
                rt.resolve_token("WILDCARD_PLAN")
            except SystemExit as e:
                refusals["WILDCARD_PLAN dropped"] = str(e)
        assert rt.resolve_token("S0_BEST_PLAN_CARD") == published, (
            "a substituted artifact leaked out of this case")
    assert named in cards["unlike"] and "beaten" not in cards["unlike"], (
        "the card counts the wildcard as lost off a rival priced cheaper only in a "
        f"configuration this plan was never priced in: {cards['unlike']}")
    assert heading_plan == rival, (heading_plan, rival)
    assert named not in cards["dropped"] and cards["dropped"] == cards["absent"], (
        f"a dropped wildcard does not land where an absent one does: "
        f"{cards['dropped']!r} vs {cards['absent']!r}")
    for token in ("S0_BEST_PLAN_CARD", "WILDCARD_PLAN"):
        assert "deep_results.json:wildcard" in refusals.get(token, ""), (
            f"{token} rendered off a block the other reader refuses: {refusals}")
    assert "WILDCARD_PLAN dropped" in refusals and rival not in refusals[
        "WILDCARD_PLAN dropped"].split("section 0")[0].split("(")[0], (
        "section 9's heading names a rival the card does not count: "
        f"{refusals.get('WILDCARD_PLAN dropped')!r} beside {cards['dropped']!r}")
    return ("the wildcard is ranked on the battery configuration alone, the one "
            f"section 9's heading asks about ({'; '.join(seen)}); {len(probes)} "
            "artifacts outside the key convention refuse by name for both readers; a "
            "non-finite block is the card's absent state and the heading's refusal")


# What section 3's row is allowed to stamp on the plan-name cell, per state:
#
#     state -> (badge text, whether this plan is a cheapest plan in the one
#               column this table RANKS, whether it is the only one there)
#
# "win" carries no badge and is deliberately the same class section 4's sole
# winner uses: `tr.win td` paints it, and a state with nothing to qualify has
# no badge that could be wrong in either section. The other two need names of
# their own -- section 4's `tie` and `trails` badges count that table's two
# battery columns, and section 3 has none to count, so borrowing them would
# stamp a false claim on the row.
#
# Written here as well as in report_tokens on purpose: the token
# (S3_ROW_PLAN_CELL) is where the WORDS are, this table is where their MEANING
# is, and the case below checks the text against the plan-name cell the row
# actually renders -- read out of the markup, not out of any stylesheet
# (issue #198) -- and the meaning against the rankings the CSV actually
# produces.
_S3_ROW_BADGES = {
    "win": (None, True, True),
    "s3-tie": ("tied for cheapest at the generation rates this house pays", True, False),
    "s3-trails": ("not the cheapest at the generation rates this house pays", False, False),
}


@case
def case_section_3s_row_class_is_a_state_the_stylesheet_can_paint():
    """ISSUE #196, the guard an attribute value needs. S3_ROW_CLASS resolves
    into an HTML attribute, and every seam rule in this file is anchored on a
    figure (a doubled sigil, a lost dimension, an echoed number), so none of
    them can read a CSS class name.

    What goes wrong with a class name is not a malformed render. It is a class
    the stylesheet does not paint -- the row then draws like every other row
    and this household loses the one mark saying which plan is its own -- or a
    class painted with a FALSE badge, which looks even less broken. So:

      1. THE VOCABULARY IS CLOSED. Every state the resolver can reach is a
         member of report_tokens._S3_ROW_CLASSES, driven over all three
         standings a rival's total can produce.
      2. EVERY MEMBER IS PAINTED by a rule in report-template.html's own
         <style> block, and every state that is not a sole win SAYS SO in the
         row -- as TEXT in the plan-name cell, read here out of the rendered
         row, because colour alone does not tell a reader where they stand
         and CSS content: never enters the DOM where a guard could read it
         (issue #198). The stylesheet is held to carry no ::after badge for
         any of these states.
      3. EVERY BADGE IS TRUE OF THE STATE THAT REACHES IT, checked against the
         cheapest set the repriced CSV actually produces rather than against
         the module's own naming rule.
      4. SECTION 4's BADGES ARE NOT BORROWED. Section 3's table ranks one
         column -- this household's own generation provider -- so a badge
         counting "both columns" would be false here even when section 4's is
         true. The two vocabularies are asserted disjoint apart from `win`,
         which carries no badge at all."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    style = _template_style()

    assert set(_S3_ROW_BADGES) == set(rt._S3_ROW_CLASSES), (
        f"this case declares a badge for {sorted(_S3_ROW_BADGES)} while S3_ROW_CLASS can "
        f"reach {list(rt._S3_ROW_CLASSES)}; a state with no declared claim is a state "
        "whose badge nothing here reads")
    shared = (set(rt._S3_ROW_CLASSES) & set(rt._S4_ROW_CLASSES)) - {"win"}
    assert not shared, (
        f"section 3 and section 4 both use the class(es) {sorted(shared)}, whose badge is "
        "written for the battery matrix's two columns; section 3's table ranks one column "
        "and cannot carry a claim about two")

    for state, (text, _cheapest_here, _sole) in _S3_ROW_BADGES.items():
        assert f"tr.{state} td" in style, (
            f"report-template.html's stylesheet has no rule for tr.{state}, a state "
            f"S3_ROW_CLASS can put on section 3's household row; an unpainted class is not "
            "a broken render, it is a runner-up's row drawn like every other row")
    # No content: rule may name a section 3 row state in any form, and no
    # content: value may be anything but a known decoration -- parsed, not
    # substring-matched (issue #198 review; see _assert_no_row_badge_in_stylesheet).
    _assert_no_row_badge_in_stylesheet(style, rt._S3_ROW_CLASSES, "section 3")

    seen = {}
    with _stub_plan(cheapest, provider):
        for rival_total in (own + 1, own, own - 1):
            with _plan_repriced(provider, {cheapest: own, rival: rival_total}):
                state = rt.resolve_token("S3_ROW_CLASS")
                row = _s3_chrome_markup()["row"]
                _standing, plan, _t, _c, winners = rt._plan_standing(rt.CTX, "PROBE")
            assert state in rt._S3_ROW_CLASSES, (
                f"S3_ROW_CLASS resolved {state!r}, which is not one of "
                f"{list(rt._S3_ROW_CLASSES)} and so is a class nothing in "
                "report-template.html paints")
            badge, cheapest_here, sole = _S3_ROW_BADGES[state]
            # THE BADGE, READ OUT OF THE RENDERED ROW: the plan name and its
            # current-plan mark in the win state (the published cell), and
            # after them the badge the class implies otherwise, as one text
            # node. The mark qualifies the plan, so it precedes the badge.
            cell = _s4_plan_cell(row)
            marked = f"{plan} {rt._S3_CURRENT_MARK}"
            expected_cell = _htmllib.escape(
                marked if badge is None else f"{marked} {rt._ROW_BADGE_SEPARATOR} {badge}",
                quote=True)
            assert cell == expected_cell, (
                f"a row of class {state!r} renders its plan-name cell as {cell!r}; the "
                f"class implies the badge {badge!r}, so the cell must read "
                f"{expected_cell!r} -- the badge is the claim the class stands for, and "
                f"the two cannot disagree: {row}")
            assert (plan in winners) == cheapest_here, (
                f"tr.{state}'s badge says this plan {'is' if cheapest_here else 'is not'} "
                f"a cheapest plan, but the CSV's cheapest set is {winners}")
            only = "the only" if sole else "not the only"
            assert (winners == [plan]) == sole, (
                f"tr.{state}'s badge says this plan is {only} cheapest, but the CSV's "
                f"cheapest set is {winners}")
            seen[f"rival at {rival_total - own:+.0f}"] = state
        assert rt.resolve_token("S3_ROW_CLASS") == "win", (
            "the substituted plan total leaked out of this case")
    return (f"every state S3_ROW_CLASS reaches ({seen}) is painted by "
            "report-template.html's own <style> block and badged as text in the rendered "
            "row, each badge is true of the CSV ranking that produced it, and none of "
            "section 4's two-column badges is borrowed")


@case
def case_section_3s_published_chrome_round_trips_into_index_html():
    """The three slots, resolved at the standing the PUBLISHED household has,
    must render exactly the markup index.html carries -- character for
    character, the same anti-drift equality the section-verdict cases keep.

    This is what makes issue #196's fix inert on the winning path: the card's
    label, the row's class and the lead-in are computed now, and a computed
    value that does not reproduce the published page is a silent rewrite of a
    report nobody re-checked. The household's plan is read off index.html's
    own section 3 row rather than assumed, so this runs with or without the
    private archive.

    Comparison stops at the first KNOWN_GAPS token, which resolve_token
    refuses by design and generate_report fills from a human block: everything
    before it is what these tokens own."""
    index_html = (rt.ROOT / "index.html").read_text()
    m = re.search(r'<tr class="([a-z0-9-]+)"><td>([A-Za-z0-9-]+) ✓ current</td>', index_html)
    assert m, "index.html has no section 3 household row for this case to read"
    published_class, published_plan = m.group(1), m.group(2)
    provider, _cheapest, _priced = _plan_ranking_inputs()
    checked = {}
    with _stub_plan(published_plan, provider):
        assert rt.resolve_token("S3_ROW_CLASS") == published_class, (
            f"S3_ROW_CLASS resolves {rt.resolve_token('S3_ROW_CLASS')!r} for the plan "
            f"index.html publishes ({published_plan}), whose row is class "
            f"{published_class!r}")
        for slot, markup in _s3_chrome_markup().items():
            fragment = markup.split("{{")[0].rstrip()
            # The compared fragment must actually contain the value the slot's
            # own token resolved to, or this case is comparing boilerplate
            # either side of it and would pass with the token unrendered.
            owned = _htmllib.escape(rt.resolve_token(_S3_SLOT_TOKENS[slot]), quote=True)
            assert owned in fragment, (
                f"the {slot} slot's compared markup does not contain {owned!r}, the value "
                f"{_S3_SLOT_TOKENS[slot]} resolved to, so this case is comparing markup "
                f"the token does not own: {markup!r}")
            assert index_html.count(fragment) == 1, (
                f"the {slot} slot renders markup index.html does not carry exactly once "
                f"({index_html.count(fragment)} occurrence(s)); regenerating the report "
                f"would change the published page:\n  rendered: {fragment!r}")
            checked[slot] = fragment
    return ("section 0's plan card, section 3's household row and its lead-in each render "
            "into index.html verbatim at the published household's standing ("
            + "; ".join(f"{k}: {v[:60]!r}" for k, v in checked.items()) + ")")


# ---------------------------------------------------------------------------
# SECTION 7'S PACKAGE FOOTING -- the last sentence in this family that stated
# no standing at all (issue #196).
# ---------------------------------------------------------------------------
# THE FIXED FORM: the bold plan name closed by a full stop the template owned,
# with nothing between it and the baseline clause. "All packages keep <plan>."
# is TRUE of every household -- data/package_results.json models all three
# packages on the plan the house is on -- but it says nothing about that being
# a modelling basis, and it sits in a section headed "The decision", over three
# package cards and a recommendation. So a household section 0 has just told
# "a cheaper rate plan exists", and section 3 has just told by how much, read
# on into three packages priced on the losing tariff with no sign that the
# comparison and the packages are on different footings.
#
# Restore this literal and the trails state goes straight back to that, which
# is what the case below fails on.
_S7_FOOTING_FIXED = "<b>{{BEST_PLAN}}</b>. <b>Baseline"
# WHAT REPLACED IT: one slot, immediately after the bold plan name, carrying
# the sentence's own punctuation. The plan name cannot come out of the slot --
# generate_report.render() HTML-escapes every token value, so no token can emit
# the <b> around it -- which is why the footing sits after the name rather than
# rewriting the clause in front of it.
_S7_FOOTING_SLOT = "<b>{{BEST_PLAN}}</b>{{S7_PLAN_FOOTING}}"
# Where the half these tokens own stops. Everything from here is the baseline
# and method half of the paragraph, which states no standing and is not this
# case's to read.
_S7_FOOTING_STOP = "<b>Baseline"


def _s7_footing_line():
    """report-template.html's section 7 opening paragraph, with the fixed
    full-stop assertion checked GONE and the slot checked present exactly once.

    One line, asserted: a second copy is a second place section 7 states this
    household's footing, and the case below could not say which one a reader is
    shown."""
    template = rt.TEMPLATE.read_text()
    assert _S7_FOOTING_FIXED not in template, (
        f"report-template.html has gone back to closing section 7's opening clause with "
        f"a fixed full stop ({_S7_FOOTING_FIXED!r}), so a household the ranking beats is "
        "told every package keeps its plan with nothing saying the plan comparison above "
        "is on a different footing -- the whole of this half of issue #196")
    hits = [ln for ln in template.splitlines() if _S7_FOOTING_SLOT in ln]
    assert len(hits) == 1, (
        f"report-template.html carries {len(hits)} line(s) holding {_S7_FOOTING_SLOT!r}, "
        f"not the one section 7 opening this case reads: {hits}")
    return hits[0]


def _s7_footing_markup():
    """That paragraph's opening as a reader would be shown it, cut where these
    tokens' own sentence ends -- every {{TOKEN}} resolved and HTML-escaped the
    way generate_report.render() fills it, so this reads the page rather than a
    paraphrase."""
    def fill(m):
        name = m.group(1)
        if rt.TOKENS.get(name, {}).get("kind") == "gap":
            return m.group(0)
        return _htmllib.escape(rt.resolve_token(name), quote=True)

    head = _s7_footing_line().split(_S7_FOOTING_STOP)[0]
    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", fill, head).rstrip()


@case
def case_section_7s_package_footing_states_the_plan_it_prices_on():
    """ISSUE #196, THE DECISION SECTION. Sections 0 and 3 state this
    household's standing; section 7 then prices three packages on the
    household's own plan and used to say only "All packages keep <plan>." For a
    household the ranking beats, that is a recommendation to stay written over
    a page that has just said staying costs more, and the reader cannot tell
    that the plan comparison and the packages are on different footings.

    WHAT THE FOOTING MUST SAY, AND WHAT IT MAY NOT. It states which plan the
    packages hold, whether the ranking beats it, and -- when it does -- that
    switching is in no saving below, then what the switch is worth. Issue #200
    gave that clause its artifact: battery_plan_matrix.json's
    mid_package_on_plans re-bills the MID package end-to-end per plan against
    the same plan's no-package year, so the trails state must quote exactly
    that block's package_save for the winning plan -- read HERE off the same
    artifact the token reads, never a hardcoded figure -- and the win and tie
    states still carry no dollar figure, because there is no switch to price.
    A winner the block does not price gets a stated why-not, not a number and
    not a refusal, driven further below.

    THE THREE STATES ARE DRIVEN THROUGH THE RENDERED MARKUP, a rival priced $1
    above this household, at exactly its total, and $1 below it -- the same
    stored `==`/min() identity the rest of this family ranks on, with no band
    (issue #141). Each state is checked as an IFF against section 3's own
    sole-winner sentence, and against section 0's headline clause and its card,
    so the four statements a reader meets on one page cannot drift apart: the
    footing is bare punctuation exactly when section 3 says the plan is still
    cheapest, and carries the disclosure exactly when it does not.

    AND THE WINNING STATE ROUND-TRIPS INTO index.html character for character.
    The published household is sole cheapest, so this fix has to be inert on
    the path that exists today: the rendered opening must be markup the
    published page already carries, or regenerating the report would silently
    rewrite a sentence nobody re-checked.

    The whole token set is swept in every state, catching BaseException through
    _sweep_every_token, because the claim that matters is "this household gets
    a report" rather than "this sentence renders"."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    index_html = (rt.ROOT / "index.html").read_text()
    m = re.search(r'<tr class="[a-z0-9-]+"><td>([A-Za-z0-9-]+) ✓ current</td>', index_html)
    assert m, "index.html has no section 3 household row for this case to read"
    published_plan = m.group(1)
    assert published_plan == cheapest, (
        f"index.html publishes {published_plan!r} while data/plan_results.csv ranks "
        f"{cheapest!r} cheapest in the {provider!r} column; the round trip below assumes "
        "the published household is the sole cheapest one")
    seen = {}
    with _stub_plan(cheapest, provider):
        _baseline_ok, baseline_refused = _sweep_every_token()
        for standing, rival_total in (("win", own + 1), ("tie", own),
                                      ("trails", own - 1)):
            with _plan_repriced(provider, {cheapest: own, rival: rival_total}):
                opening = _s7_footing_markup()
                footing = rt.resolve_token("S7_PLAN_FOOTING")
                s3, s0 = rt.resolve_token("S3_VERDICT"), rt.resolve_token("S0_VERDICT")
                card = rt.resolve_token("S0_BEST_PLAN_CARD")
                rendered, refused = _sweep_every_token()
            _assert_no_new_refusals(baseline_refused, refused,
                                    f"a household in the {standing} state")

            # 1. THE FOUR STATEMENTS AGREE, by construction: section 3's own
            #    sole-winner sentence decides, and the footing, section 0's
            #    headline clause and its card each track it both ways.
            wins = "is still the cheapest plan" in s3
            assert wins == (standing == "win"), (
                f"section 3's verdict does not report the {standing} state a rival priced "
                f"{rival_total - own:+.0f} against {cheapest} produces: {s3}")
            assert (footing == ".") == wins, (
                f"section 7's footing reads {footing!r} while section 3's verdict says "
                f"{s3!r}")
            assert ("the rate plan is right" in s0) == wins, (s0, s3)
            assert card.startswith("Best plan in every scenario") == wins, (card, s3)

            # 2. THE FIGURE IS EARNED, STATE BY STATE. Win and tie carry no
            #    dollar figure -- there is no switch to price, and the gap
            #    between plans is section 3's. Trails quotes the artifact:
            #    mid_package_on_plans.plans[<winner>].package_save, formatted
            #    the way the token formats it, read here off the same
            #    rt._json the token reads. Asserting the exact figure is what
            #    makes quoting the block's OTHER dollar cell (package_bill)
            #    fail by name.
            if standing == "trails":
                mid = rt._json("battery_plan_matrix.json")["mid_package_on_plans"]
                row = mid["plans"].get(rival)
                assert row is not None, (
                    f"data/plan_results.csv's nearest rival {rival!r} is no longer a "
                    f"plan mid_package_on_plans prices ({sorted(mid['plans'])}); the "
                    "priced branch below needs one, and the unpriced winner is driven "
                    "separately after this loop")
                expect = f"${row['package_save']:,.0f}/yr"
                assert expect in footing, (
                    f"section 7's footing does not quote mid_package_on_plans' "
                    f"package_save for {rival} ({expect}) in the trails state: "
                    f"{footing!r}")
                assert f"Re-billed end-to-end on {rival}" in footing, (
                    f"section 7's footing quotes a figure without naming the plan it "
                    f"is re-billed on: {footing!r}")
                assert ("no-package year" in footing
                        and "same plan" in footing
                        and "one rate vintage" in footing), (
                    f"section 7's footing quotes a switch figure without stating its "
                    f"baseline -- same plan, no-package year, one rate vintage: "
                    f"{footing!r}")
            else:
                assert "$" not in footing, (
                    f"section 7's footing quotes a figure in the {standing} state "
                    f"({footing!r}) -- there is no switch to price when the plan is "
                    "not beaten, and the gap between plans belongs to section 3")

            # 3. AND EACH STATE SAYS THE PARTICULAR TRUE THING, in the markup a
            #    reader is shown.
            if standing == "win":
                assert opening.endswith(f"<b>{cheapest}</b>."), opening
                assert index_html.count(opening) == 1, (
                    f"section 7's winning-state opening is not markup index.html carries "
                    f"exactly once ({index_html.count(opening)} occurrence(s)); "
                    f"regenerating the report would change the published page:\n"
                    f"  rendered: {opening!r}")
            else:
                assert not opening.endswith(f"<b>{cheapest}</b>."), (
                    f"section 7 tells a household in the {standing} state that all "
                    f"packages keep its plan and stops there: {opening}")
                assert rival in opening, (
                    f"section 7's footing does not name the plan that ranks at or above "
                    f"this household in the {standing} state: {opening}")
                assert "rate plan comparison above" in opening, (
                    f"section 7's footing does not point the reader at the section that "
                    f"ranks the plans: {opening}")
            if standing == "tie":
                assert f"level with {rival}" in opening, opening
                assert "not the cheapest" not in opening, (
                    f"section 7 calls a plan tied for cheapest beaten: {opening}")
            if standing == "trails":
                assert "the plan this house is on, not the cheapest one" in opening, (
                    opening)
                # No apostrophe in any branch: render() escapes with
                # quote=True, so a possessive would publish as "house&#x27;s".
                assert "&#x27;" not in opening and "&quot;" not in opening, (
                    f"section 7's footing publishes an escaped quote into the page "
                    f"source: {opening}")
                assert "none of the savings below includes switching to it" in opening, (
                    f"section 7 leaves a beaten household to assume the packages below "
                    f"include the switch section 3 has just said is cheaper: {opening}")
            assert set(rendered) >= set(_BEST_PLAN_TOKENS), (
                f"{sorted(set(_BEST_PLAN_TOKENS) - set(rendered))} refused in the "
                f"{standing} state")
            seen[standing] = opening[opening.index("</b>"):][4:].strip() or "(bare stop)"

        # 4. A WINNER THE BLOCK DOES NOT PRICE. mid_package_on_plans prices
        #    three plans; the CSV ranks more. A household beaten by one the
        #    block lacks must read WHY the switch is not priced -- no figure,
        #    and above all no refusal: this module twice shipped chrome guards
        #    that cost ordinary households their whole report.
        mid_plans = set(
            rt._json("battery_plan_matrix.json")["mid_package_on_plans"]["plans"])
        outside = sorted(r["plan"] for r in priced
                         if r["plan"] != cheapest and r["plan"] not in mid_plans)
        assert outside, (
            f"every plan data/plan_results.csv prices for {provider!r} is also in "
            f"mid_package_on_plans ({sorted(mid_plans)}); this drive needs a winner "
            "the block does not price")
        with _plan_repriced(provider, {cheapest: own, outside[0]: own - 1}):
            footing = rt.resolve_token("S7_PLAN_FOOTING")
        assert "$" not in footing, (
            f"section 7's footing quotes a figure for {outside[0]}, a plan "
            f"mid_package_on_plans does not price: {footing!r}")
        assert (f"does not price the MID package on {outside[0]}" in footing
                and "not modeled here" in footing), (
            f"section 7's footing does not state why the switch to {outside[0]} "
            f"is unpriced: {footing!r}")
        seen["trails, winner unpriced"] = footing.strip()

        # 5. AN OLDER ARTIFACT WITHOUT THE BLOCK -- the committed JSON minus
        #    mid_package_on_plans, swapped into the cache the way every other
        #    fixture here substitutes artifacts. Same contract: stated
        #    why-not, no figure, no refusal.
        full = rt._json("battery_plan_matrix.json")
        stripped = {k: v for k, v in full.items() if k != "mid_package_on_plans"}
        with _plan_repriced(provider, {cheapest: own, rival: own - 1}), \
                _swapped(rt._json_cache, "battery_plan_matrix.json", stripped):
            footing = rt.resolve_token("S7_PLAN_FOOTING")
        assert "$" not in footing and "not modeled here" in footing, (
            f"an artifact without mid_package_on_plans must leave the footing "
            f"stating the switch is not modeled, not quoting or refusing: {footing!r}")
        assert f"does not price the MID package on {rival}" in footing, footing
        seen["trails, older artifact"] = footing.strip()

        assert rt.resolve_token("S7_PLAN_FOOTING") == ".", (
            "the substituted plan total leaked out of this case")
    return ("section 7's opening states the footing its packages are priced on in all "
            "three standings, agrees with section 3's verdict, section 0's headline and "
            "section 0's card in each, prices the switch off mid_package_on_plans "
            "exactly when the sole winner is priced there (stated why-not otherwise), "
            "and renders into index.html verbatim on the winning path ("
            + "; ".join(f"{k}: {v[:70]!r}" for k, v in seen.items()) + ")")


@case
def case_section_7s_footing_agrees_with_however_many_plans_beat_this_one():
    """A tie AT THE TOP puts two or more plans ahead of this household, and
    section 7's footing has to point back at all of them.

    Its verb already did -- "TOU-DR-P and TOU-DR-1 each price lower", not
    "prices lower" -- and its closing clause did not: "and none of the savings
    below includes switching to it" was fixed text, so the sentence made its
    subject plural and then referred back to it in the singular, leaving the
    reader to work out which of the two plans the packages below do not
    include switching to. Half-fixed agreement is the shape worth guarding:
    the verb was corrected when the winners' set became a set, and the pronoun
    six words later was not.

    Both counts are driven, at both standings that name other plans, off
    data/plan_results.csv repriced in memory. Nothing here needs the private
    archive: the household's plan is stubbed to a plan the committed CSV
    prices."""
    provider, plan, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == plan))
    others = sorted(r["plan"] for r in priced if r["plan"] != plan)
    assert len(others) >= 2, (
        f"data/plan_results.csv prices {len(others)} rival(s) for {provider!r}; two are "
        "needed to put a plural winners' set ahead of this household")
    one, two = others[0], others[1]
    seen = {}
    with _stub_plan(plan, provider):
        for label, prices in (
                ("one plan ahead", {plan: own, one: own - 1}),
                ("two plans tied ahead", {plan: own, one: own - 1, two: own - 1}),
                ("one plan level", {plan: own, one: own}),
                ("two plans level", {plan: own, one: own, two: own})):
            with _plan_repriced(provider, prices):
                footing = rt.resolve_token("S7_PLAN_FOOTING")
                winners = rt._plan_standing(rt.CTX, "S7_PLAN_FOOTING")[4]
            plural = len(winners) > 1
            named = [p for p in (one, two) if p in prices and prices[p] <= own
                     and p in winners]
            assert all(p in footing for p in named), (
                f"section 7's footing names {footing!r} where {named} rank at or above "
                "this household")
            if "not the cheapest one" in footing:
                # The beaten branch: subject and pronoun, checked against the
                # same winners' set rather than against each other.
                assert ("each price lower" in footing) == plural, (
                    f"section 7's footing disagrees in NUMBER with its own winners' set "
                    f"{winners}: {footing!r}")
                assert ("switching to any of them" in footing) == plural, (
                    f"section 7's footing refers back to {len(winners)} plan(s) as "
                    f"{'a single one' if plural else 'several'}: {footing!r}")
                assert ("switching to it." in footing) == (not plural), footing
                # The switch-pricing clause holds the same number: package_save
                # is per plan, so a TIED set ahead has no single re-based
                # figure, and the footing must say so rather than pick one of
                # the tie to quote.
                assert plural == (
                    "no switch to a set of tied plans is modeled here" in footing), (
                    f"section 7's switch-pricing clause disagrees in NUMBER with its "
                    f"own winners' set {winners}: {footing!r}")
                if plural:
                    assert "$" not in footing, (
                        f"section 7's footing quotes one figure for a tied set of "
                        f"plans: {footing!r}")
            seen[label] = footing
        # Inside the household stub, which is what makes this assertion (and
        # this whole case) run on a checkout with no private archive.
        assert rt.resolve_token("S7_PLAN_FOOTING") == ".", (
            "the substituted plan totals leaked out of this case")
    return ("section 7's footing agrees in number with the set of plans that rank at or "
            "above this household, in its verb and in the pronoun it closes with ("
            + "; ".join(f"{k}: {v.strip()[-58:]!r}" for k, v in seen.items()) + ")")


# ---------------------------------------------------------------------------
# SECTION 3'S FIFTH COLUMN -- "<utility>'s own tool says" -- and the last
# fixed win-claim left in that row.
# ---------------------------------------------------------------------------
# Marks that state a verdict all by themselves. None may sit in the FIXED half
# of that cell: a check beside the tool's label is the claim "the tool named
# this plan", written where nothing can make it false. The row's FIRST cell
# keeps its own "✓ current", which says which row is the household's and
# nothing about any ranking -- so this list is applied to the fifth cell only.
_TOOL_VERDICT_MARKS = ("✓", "✔", "✅", "✗", "✘", "❌", "×")
# The two halves of that cell, both KNOWN_GAPS: what the tool quoted and what
# the tool concluded. Neither is derivable here -- see report_tokens.KNOWN_GAPS.
_S3_TOOL_CELL_GAPS = ("UTILITY_TOOL_BEST_PLAN_FIGURE", "UTILITY_TOOL_BEST_PLAN_VERDICT")


def _s3_row_line():
    """report-template.html's section 3 household row, cut at any comment."""
    return _s3_chrome_lines()["row"].split("<!--")[0].rstrip()


def _s3_tool_cell():
    """That row's FIFTH <td> -- the utility-tool column's cell."""
    cells = re.findall(r"<td>(.*?)</td>", _s3_row_line())
    assert len(cells) == 5, (
        f"section 3's household row no longer carries the five cells its <thead> "
        f"declares ({len(cells)}); the utility-tool column is the fifth and these cases "
        f"read it by position: {_s3_row_line()!r}")
    return cells[4]


def _s3_row_rendered(answers):
    """That row as generate_report.render() would write it: every resolvable
    token resolved, every KNOWN_GAPS token taken from `answers` -- which is
    how generate_report.run() fills them, out of human_answers["TOKEN:<name>"]
    -- and EVERY substituted value HTML-escaped with quote=True, the same
    _sub() the published page goes through.

    The escaping is not incidental here, it is the constraint that shaped the
    fix: a human answer carrying `"Your Best Plan"` publishes as
    `&quot;Your Best Plan&quot;`, so the straight quotes index.html carries can
    only come from the template. The tool's LABEL therefore stays fixed and
    this row's tokens carry the FIGURE and the VERDICT beside it.

    A gap with no attested answer raises rather than defaulting, which is what
    generate_report does with one (it appends a "gap-token" failure and writes
    nothing)."""
    def fill(m):
        name = m.group(1)
        if rt.TOKENS.get(name, {}).get("kind") == "gap":
            if name not in answers:
                raise KeyError(name)
            return _htmllib.escape(answers[name], quote=True)
        return _htmllib.escape(rt.resolve_token(name), quote=True)

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", fill, _s3_row_line())


def _published_tool_answers():
    """(index.html's section 3 household row, {gap token: the value the human
    attested when that page was published}).

    RECOVERED FROM THE PAGE, never typed here: the figure is read off an
    account-specific screenshot and CLAUDE.md section 4 keeps that class of
    answer out of a committed file.

    The recovery is itself the assertion. The template line is turned into a
    regex whose ONLY wildcards are the two gap slots, so a match proves the
    fixed markup around them is character-for-character what index.html
    carries, and the captured groups are exactly what the two tokens have to
    supply to reproduce it."""
    index_html = (rt.ROOT / "index.html").read_text()
    names, pattern = [], []
    for part in re.split(r"(\{\{[A-Z0-9_]+\}\})", _s3_row_line()):
        m = re.fullmatch(r"\{\{([A-Z0-9_]+)\}\}", part)
        if m is None:
            pattern.append(re.escape(part))
        elif rt.TOKENS.get(m.group(1), {}).get("kind") == "gap":
            names.append(m.group(1))
            pattern.append("(.+?)")
        else:
            pattern.append(re.escape(
                _htmllib.escape(rt.resolve_token(m.group(1)), quote=True)))
    hits = list(re.finditer("".join(pattern), index_html))
    assert len(hits) == 1, (
        f"section 3's household row renders markup index.html carries {len(hits)} "
        "time(s) once its two KNOWN_GAPS slots are left open; regenerating the report "
        f"would change the published page:\n  pattern: {''.join(pattern)!r}")
    return hits[0].group(0), dict(zip(names, hits[0].groups()))


@case
def case_the_utility_tools_verdict_is_attested_rather_than_asserted():
    """ISSUE #196, THE LAST CELL. Section 3's household row ended:

        <td>{{UTILITY_TOOL_BEST_PLAN_FIGURE}} — "Your Best Plan" ✓</td>

    The figure was a token; the verdict beside it was fixed markup. So the
    page told every household that the utility's own comparison tool had named
    ITS plan the best one, with nothing able to make that false -- the same
    shape as the three assertions this issue removed from the card above, the
    row's class and the lead-in below. On a household the ranking beats it was
    a fourth contradiction, sitting inside the very row S0_BEST_PLAN_CARD,
    S0_VERDICT and S3_VERDICT had just been taught to report honestly.

    WHY IT IS NOT SIMPLY DELETED. The two rankings can legitimately disagree,
    and the disagreement is the most informative thing that column can show: a
    third party pricing the same plans and reaching another answer is worth
    printing, next to a repo that says so plainly. What is not defensible is
    this repo choosing which of the two verdicts to print. It cannot: nothing
    committed reads a verdict out of DATA-SOURCES-CHEATSHEET.md's
    plan_comparison_capture (a private-only screenshot), and
    data/plan_results.csv ranks THIS repo's modeled totals, which is evidence
    about the model and none at all about the tool. So the verdict became a
    KNOWN_GAPS token beside the figure, and a human attests both.

    WHY THE LABEL STAYS IN THE TEMPLATE. One token owning the whole cell is
    the tidier shape and it cannot work: generate_report.render() escapes
    every substituted value with quote=True, so an answer carrying
    `"Your Best Plan"` publishes as `&quot;Your Best Plan&quot;` and this
    household's page would silently change. The template therefore keeps the
    tool's own WORDS -- the label being asked about, under a <th> that names
    whose tool it is -- and the token carries the ANSWER that follows them.
    Asserted below both ways: no verdict mark survives in the fixed half, and
    the published row round-trips character for character.

    FOUR STATES, all printed by this case: the tool agreeing, the tool naming
    another plan, the same attestation under all three of this repo's own
    standings (it must not move -- the column is somebody else's), and the
    answer withheld, where the row refuses to render rather than defaulting to
    either verdict."""
    index_html = (rt.ROOT / "index.html").read_text()
    head = re.search(r'<tr class="([a-z0-9-]+)"><td>([A-Za-z0-9-]+) ✓ current</td>',
                     index_html)
    assert head, "index.html has no section 3 household row for this case to read"
    published_plan = head.group(2)
    provider, cheapest, priced = _plan_ranking_inputs()
    template = rt.TEMPLATE.read_text()

    # 1. THE CELL IS ATTRIBUTED, and its fixed half states nothing.
    headers = [ln for ln in template.splitlines() if "'s own tool says" in ln]
    assert len(headers) == 1 and "{{UTILITY_NAME}}" in headers[0], (
        "section 3's fifth column no longer carries exactly one header naming whose "
        f"tool it reports ({headers}); without it the cell below reads as this report's "
        "own verdict rather than a third party's")
    cell = _s3_tool_cell()
    fixed_half = re.sub(r"\{\{[A-Z0-9_]+\}\}", "", cell)
    asserted = [mark for mark in _TOOL_VERDICT_MARKS if mark in fixed_half]
    assert not asserted, (
        f"section 3's utility-tool cell states the tool's verdict in fixed markup "
        f"({asserted}) -- true for every household, false for any whose tool named "
        f"another plan, and unanswerable by the tokens beside it: {cell!r}")
    for name in _S3_TOOL_CELL_GAPS:
        assert "{{" + name + "}}" in cell, (
            f"section 3's utility-tool cell no longer carries {{{{{name}}}}}, so the "
            f"half of that cell it owns is being stated by something else: {cell!r}")
        assert rt.TOKENS.get(name, {}).get("kind") == "gap", (
            f"{name} is no longer a KNOWN_GAPS token, so this cell claims a source "
            "this repo does not have")

    # 2. THE ATTESTATION DOES NOT FOLLOW THIS REPO'S RANKING. One answer,
    #    rendered under a sole win, a tie and a loss in data/plan_results.csv:
    #    the cell must be identical in all three, because the column is headed
    #    with somebody else's name. (The row's CLASS must move, or the drive
    #    did nothing and the claim above is vacuous.)
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))["plan"]
    agrees = {"UTILITY_TOOL_BEST_PLAN_FIGURE": "$1,234.56",
              "UTILITY_TOOL_BEST_PLAN_VERDICT": "✓"}
    names_other = {"UTILITY_TOOL_BEST_PLAN_FIGURE": "$1,234.56",
                   "UTILITY_TOOL_BEST_PLAN_VERDICT": f"✗ it names {rival}"}
    seen, classes = {}, {}
    with _stub_plan(cheapest, provider):
        for standing, rival_total in (("win", own + 1), ("tie", own),
                                      ("trails", own - 1)):
            with _plan_repriced(provider, {cheapest: own, rival: rival_total}):
                rows = {label: _s3_row_rendered(a)
                        for label, a in (("agrees", agrees), ("names-other", names_other))}
            classes[standing] = re.search(r'<tr class="([a-z0-9-]+)"',
                                          rows["agrees"]).group(1)
            seen[standing] = {label: re.findall(r"<td>(.*?)</td>", row)[4]
                              for label, row in rows.items()}
    assert len(set(classes.values())) == 3, (
        f"the three standings did not move section 3's row class ({classes}), so "
        "'the cell is the same in all three' is asserting nothing")
    for label in ("agrees", "names-other"):
        rendered = {seen[s][label] for s in seen}
        assert len(rendered) == 1, (
            f"the {label!r} attestation renders {len(rendered)} different utility-tool "
            f"cells across this repo's own three standings ({sorted(rendered)}); that "
            "column reports a third party's tool and cannot track our ranking")
    agreed, other = seen["win"]["agrees"], seen["win"]["names-other"]
    assert agreed != other, (
        f"the tool agreeing and the tool naming {rival} publish the same cell: {agreed!r}")
    assert all(v in agreed for v in agrees.values()), (agreed, agrees)
    assert rival in other and "✓" not in other, (
        f"the cell for a tool that names {rival} instead still reads as agreement: "
        f"{other!r}")

    # 3. WITHHELD. Neither half resolves, both refuse by name with the reason
    #    generate_report shows the human, and the row cannot be rendered at
    #    all -- no default verdict, in either direction.
    refusals = {}
    for name in _S3_TOOL_CELL_GAPS:
        try:
            value = rt.resolve_token(name)
        except SystemExit as e:
            refusals[name] = str(e)
        else:
            raise AssertionError(
                f"{name} resolved to {value!r}; a verdict this repo cannot read must "
                "refuse, not produce a value")
        assert name in refusals[name] and rt.KNOWN_GAPS[name][:20] in refusals[name], (
            f"{name}'s refusal does not name the token and its reason: {refusals[name]}")
    with _stub_plan(cheapest, provider):
        try:
            published_anyway = _s3_row_rendered({})
        except KeyError as e:
            withheld = str(e)
        else:
            raise AssertionError(
                "section 3's household row rendered with nothing attested about the "
                f"utility's tool: {published_anyway!r}")

    # 4. AND THE PUBLISHED HOUSEHOLD'S ROW IS UNCHANGED, character for
    #    character, once its own two answers are supplied -- recovered from
    #    index.html rather than typed, so this runs with or without the
    #    private archive and cannot drift from the page it is about.
    with _stub_plan(published_plan, provider):
        published_row, answers = _published_tool_answers()
        assert set(answers) == set(_S3_TOOL_CELL_GAPS), (
            f"the published row's open slots are {sorted(answers)}, not the two this "
            f"cell declares ({sorted(_S3_TOOL_CELL_GAPS)})")
        assert _s3_row_rendered(answers) == published_row, (
            "section 3's household row does not reproduce the one index.html "
            f"publishes:\n  rendered:  {_s3_row_rendered(answers)!r}\n  published: "
            f"{published_row!r}")
        for name, value in answers.items():
            # Escape-stable, which is what forces the tool's label to stay in
            # the template: an answer containing a straight quote would render
            # &quot; and could not reproduce this row.
            assert _htmllib.escape(value, quote=True) == value, (
                f"the published value for {name} does not survive render()'s escaping "
                f"({value!r} -> {_htmllib.escape(value, quote=True)!r}), so it cannot be "
                "supplied as a human answer")
    return ("section 3's utility-tool cell attributes its verdict instead of asserting "
            f"it: fixed half {fixed_half!r} states none, one attestation renders the "
            f"same cell under all three of our standings ({classes}), agreement "
            f"{agreed!r} and disagreement {other!r} are different cells, withholding it "
            f"refuses ({withheld}), and the published row round-trips into index.html "
            f"verbatim over {sorted(answers)}")


@case
def case_an_answer_that_states_no_verdict_is_refused_rather_than_published():
    """WITHHOLDING A GAP REFUSES; ANSWERING IT WITH NOTHING USED TO PUBLISH.

    The case above proves the row cannot render with the utility-tool gaps
    unanswered. It says nothing about an answer that is not one, and that is
    the hole: the label "Your Best Plan" is fixed markup and only the mark
    after it is a token, so an answer of "" publishes

        <td>$4,519.65 — "Your Best Plan" </td>

    which reads exactly as the tool having applied that label to this plan --
    the fixed win-claim issue #196 removed, restored by an empty string. A
    bare plan name reads the same way. Both went through silently: the answer
    is spliced into the page with no check of any kind.

    SO THE CONTRACT LIVES ON THE TOKEN. report_tokens.validate_gap_answer
    refuses a blank answer for EVERY gap -- a gap exists because this repo
    will not state something on the human's behalf, and an empty attestation
    states it as loudly as a wrong one -- and refuses an answer that does not
    carry what the sentence around the slot needs: a leading verdict mark for
    the verdict, a digit for the figure.

    WHAT THIS CASE CANNOT ASSERT, said plainly rather than left implied.
    generate_report.run() splices the operator's answer with
    `resolved[name] = human_answers[override_key]` and asks nothing; that call
    site has to pass the value through validate_gap_answer for the refusal to
    reach the page, and that file is not this change's to edit. So the render
    below is the UNVALIDATED splice, deliberately: it shows what each refused
    answer would publish, which is the whole argument for calling the
    validator there.

    The published household's own answers are recovered from index.html and
    put through the contract too -- a rule that refuses the attestation this
    page was published with would be a rule about nothing."""
    contracts = {n: rt.TOKENS[n].get("answer_contract") for n in rt.KNOWN_GAPS}
    assert set(rt.GAP_ANSWER_CONTRACTS) <= set(rt.KNOWN_GAPS), (
        f"a shape rule is declared for {sorted(set(rt.GAP_ANSWER_CONTRACTS) - set(rt.KNOWN_GAPS))}, "
        "which is not a gap token")

    # 1. THE BLANK FLOOR, on every gap there is.
    for name in rt.KNOWN_GAPS:
        for blank in ("", "   ", "\n\t "):
            try:
                rt.validate_gap_answer(name, blank)
            except SystemExit as e:
                assert name in str(e) and rt.KNOWN_GAPS[name][:20] in str(e), (
                    f"{name}'s blank-answer refusal does not name the token and what the "
                    f"gap needs: {e}")
            else:
                raise AssertionError(
                    f"{name} accepted {blank!r} as an attestation")
    assert rt.validate_gap_answer("INCENTIVE_STATUS", "ITC expired 2025-12-31") == (
        "ITC expired 2025-12-31"), "a gap with no shape rule stopped accepting an answer"

    # The rendered rows below resolve section 3's own tokens, so the household
    # answers they read are stubbed from the published page -- which is what
    # lets this case run on a checkout with no private archive.
    provider, _cheapest, _priced = _plan_ranking_inputs()
    index_html = (rt.ROOT / "index.html").read_text()
    m = re.search(r'<tr class="[a-z0-9-]+"><td>([A-Za-z0-9-]+) ✓ current</td>', index_html)
    assert m, "index.html has no section 3 household row for this case to read"

    # 2. THE UTILITY-TOOL CELL'S OWN SHAPES, and what each refused one would
    #    publish if the splice asked nobody.
    verdict, figure = _S3_TOOL_CELL_GAPS[1], _S3_TOOL_CELL_GAPS[0]
    assert contracts[verdict] and contracts[figure], (
        f"the two halves of section 3's utility-tool cell no longer declare what an "
        f"answer has to carry: {contracts}")
    accepted = {verdict: ["✓", "✔", "✗ it names TOU-DR-P instead", "× TOU-DR-P"],
                figure: ["$1,234.56", "1234.56"]}
    refused = {verdict: ["TOU-DR-P", "Your Best Plan", "the tool agrees", "n/a", "-"],
                figure: ["n/a", "not captured", "—"]}
    for name, answers in accepted.items():
        for answer in answers:
            assert rt.validate_gap_answer(name, answer) == answer, answer
    published = {}
    with _stub_plan(m.group(1), provider):
        for name, answers in refused.items():
            for answer in answers:
                try:
                    rt.validate_gap_answer(name, answer)
                except SystemExit as e:
                    assert name in str(e) and repr(answer) in str(e), (
                        f"{name}'s refusal of {answer!r} does not say which answer it "
                        f"read: {e}")
                else:
                    raise AssertionError(
                        f"{name} accepted {answer!r}, which states no "
                        f"{'verdict' if name == verdict else 'figure'}")
                # What the unvalidated splice would put on the page.
                others = {n: a[0] for n, a in accepted.items() if n != name}
                row = _s3_row_rendered({name: answer, **others})
                published[answer] = re.findall(r"<td>(.*?)</td>", row)[4]
    for answer, cell in published.items():
        assert "Your Best Plan" in cell, (answer, cell)

    # 3. AND THE ATTESTATION THIS PAGE WAS PUBLISHED WITH PASSES.
    with _stub_plan(m.group(1), provider):
        _row, answers = _published_tool_answers()
    for name, answer in answers.items():
        assert rt.validate_gap_answer(name, answer) == answer, (
            f"the contract on {name} refuses the answer index.html was published with "
            f"({answer!r}), so it is a rule about no household at all")

    # 4. AND IT IS A RULE ABOUT GAPS, not about tokens generally.
    try:
        rt.validate_gap_answer("BEST_PLAN", "EV-TOU-5")
    except SystemExit as e:
        assert "not a gap token" in str(e), e
    else:
        raise AssertionError(
            "a hand-written answer was accepted for a token that resolves from a "
            "committed source")
    return ("every gap refuses a blank answer by name, section 3's utility-tool cell "
            f"refuses {sorted(a for v in refused.values() for a in v)} -- each of which "
            f"would otherwise publish e.g. {published['TOU-DR-P']!r} -- accepts "
            f"{sorted(a for v in accepted.values() for a in v)}, and passes the "
            f"attestation index.html carries ({sorted(answers)})")


@case
def case_section_4s_row_class_tracks_the_matrix_its_cells_come_from():
    """ROUND 4, FINDING 3, AND WHAT ISSUE #178 DID WITH IT. Section 4's
    household row renders three cells, all three out of
    data/battery_plan_matrix.json -- and the gate in front of them ranked
    data/plan_results.csv instead.

    The two artifacts are not interchangeable. battery_plan_matrix.py asserts
    its no_battery column against plan_results.csv's CEA column to within
    $1.00, so they agree about THAT column for the three plans the matrix
    prices; plan_results.csv has no battery column at all, so nothing in it
    constrains with_battery or battery_value. Move a rival plan's matrix
    with_battery below this household's and plan_results.csv does not change
    by a cent: the old gate passed, and section 4 rendered "trails by $500/yr
    with one" from S4_VERDICT_SHORT -- which reads the matrix, correctly --
    directly above a row marked as the winner.

    Round 4 fixed that by ranking the right artifact and REFUSING. Issue #178
    replaced the refusal, because a refusal is a token-resolution failure and
    that took the whole report down for a household the matrix ranks second:
    the row's class is a token now, so the cells render and the ROW says
    where the plan stands. So what this case drives is no longer "do the
    cells fail closed" but "does the row's own state follow the artifact its
    cells come from", which is the same defect one level up -- a class that
    ignored the matrix would paint a runner-up as the winner just as fixed
    markup did.

    Both columns are driven, because the row spans both and section 4's
    heading question is exactly whether the answer survives the battery. Four
    things are asserted at each: the class, the cells (which are the
    household's OWN row and must not move when a RIVAL's cell does), the
    absence of any refusal, and the two SIBLING sentences off the same matrix
    (S4_VERDICT_SHORT and PLAN_MARGIN_VS_RUNNER_UP), which have always had to
    keep rendering for a household the matrix does not put first."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    provider, cheapest, _priced = _plan_ranking_inputs()
    with _stub_plan(cheapest, provider):
        assert cheapest in plans, (
            f"the CSV's cheapest plan {cheapest!r} is not priced in "
            f"battery_plan_matrix.json ({sorted(plans)}); this case cannot drive the gate")
        published = {t: rt.resolve_token(t) for t in _MATRIX_PLAN_TOKENS}
        assert rt.resolve_token("S4_ROW_CLASS") == "win", (
            "this checkout's matrix does not put the household's plan alone cheapest in "
            "both columns, so 'win' is not the state this case starts from: "
            + rt.resolve_token("S4_ROW_CLASS"))
        best = plans[cheapest]
        rivals = [p for p in plans if p != cheapest]
        assert rivals, "the matrix prices only one plan; there is no rival to promote"
        rival = min(rivals, key=lambda p: plans[p]["no_battery"])

        checked = []
        for column in ("no_battery", "with_battery"):
            # Undercut the household's plan in ONE matrix column at a time,
            # leaving data/plan_results.csv untouched. ONE column moving is
            # enough to move the class, because the class names BOTH columns'
            # standings: the untouched column stays a sole win, so the row
            # goes from "win" to "trails-win" or "tie-win" and says which
            # column it lost rather than writing off both.
            for state, moved in (("trails-win", best[column] - 500),
                                 ("tie-win", _bill_of(best, column))):
                with _cell_priced(plans[rival], column, moved):
                    assert rt.resolve_token("S4_ROW_CLASS") == state, (
                        f"battery_plan_matrix.json prices {rival} at {moved} against "
                        f"{cheapest}'s {best[column]} in its {column} column, which is a "
                        f"{state!r} row, but section 4's row class resolved "
                        f"{rt.resolve_token('S4_ROW_CLASS')!r} -- the class is what the "
                        "page uses to paint that row as the winner")
                    # THE CELLS ARE THE HOUSEHOLD'S OWN and a rival moving
                    # cannot move them. They must also not REFUSE: a refusal
                    # stops generate_report.py before it writes anything, so
                    # a household the matrix ranks second would get no report
                    # at all (issue #178).
                    for token in _MATRIX_PLAN_TOKENS:
                        try:
                            value = rt.resolve_token(token)
                        except BaseException as exc:   # noqa: BLE001 - SystemExit
                            raise AssertionError(
                                f"{token} refused for a household the matrix ranks "
                                f"{state} in its {column} column, which stops the whole "
                                f"report over a figure that is simply this household's "
                                f"own modeled bill: {exc}") from None
                        assert value == published[token], (
                            f"{token} moved from {published[token]!r} to {value!r} when a "
                            f"RIVAL's {column} cell changed; it prints "
                            f"{cheapest}'s own row")
                    # The sibling sentences off the same artifact keep rendering.
                    for sibling in ("S4_VERDICT_SHORT", "PLAN_MARGIN_VS_RUNNER_UP"):
                        assert rt.resolve_token(sibling).strip(), (
                            f"{sibling} was taken down by a {state} matrix; it words "
                            "itself off the sign and must render for a household the "
                            "matrix does not put first")
                checked.append(f"{column}/{state}")

        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted matrix cell leaked out of this case ({token})")
        assert rt.resolve_token("S4_ROW_CLASS") == "win", (
            "the substituted matrix cell leaked out of this case (S4_ROW_CLASS)")
    return (f"section 4's row class follows battery_plan_matrix.json at every one of "
            f"{checked} -- and the {len(_MATRIX_PLAN_TOKENS)} cells inside the row keep "
            f"rendering {cheapest}'s own unchanged figures throughout, as do "
            "S4_VERDICT_SHORT and PLAN_MARGIN_VS_RUNNER_UP")


@case
def case_s2_verdict_refuses_an_overnight_habit_it_never_observed():
    """FINDING 3. With the census entry at n_eligible_nights = 0 and n = 0,
    `charging > absent` is False, so section 2 published "while the EV does
    not usually charge overnight" -- a habit claim resting on ZERO
    observations. Both readings are claims about an observed habit, so the
    unmeasured case is neither of them.

    An incoherent census (more absences than nights watched, so the charging
    count comes out negative) goes the same way. The measured cases are driven
    alongside, so this cannot pass on a formula that stopped rendering."""
    census = (rt._json("quiet_night_floor.json")["night_floor"]
              ["issue_114_investigation"]["ev_absence_by_window"])
    lo, hi, _lab = rt._overnight_cheap_run()
    label = f"{int(lo)}-{int(hi)}h"
    entry = census[label]
    live_nights = entry["n_eligible_nights"]
    assert live_nights > 0, f"the committed census already watched {live_nights} nights"
    with _stub_household(_s2_household_inputs()):
        published = rt.resolve_token("S2_VERDICT")
        assert "while the EV charges overnight" in published, published

        for label_, bad in (
                ("no eligible nights at all", {"n": 0, "n_eligible_nights": 0}),
                ("nights watched but none counted", {"n": 0, "n_eligible_nights": 0.0}),
                ("more absences than nights watched",
                 {"n": live_nights + 5, "n_eligible_nights": live_nights}),
                ("a negative night count", {"n": -3, "n_eligible_nights": live_nights})):
            with _swapped(census, label, bad):
                try:
                    value = rt.resolve_token("S2_VERDICT")
                    raise AssertionError(
                        f"S2_VERDICT published an overnight-charging habit claim on "
                        f"{label_} ({bad}): {value}")
                except SystemExit as e:
                    assert "S2_VERDICT" in str(e), e
                    assert "whether the EV usually charges overnight" in str(e), (
                        f"the refusal does not name the indeterminate quantity: {e}")

        # Discriminates: one observed night either way still renders, so this
        # is a "no observations" gate and not a new blanket refusal.
        for label_, bad, expected in (
                ("a single night, charging", {"n": 0, "n_eligible_nights": 1},
                 "while the EV charges overnight"),
                ("a single night, absent", {"n": 1, "n_eligible_nights": 1},
                 "while the EV does not usually charge overnight")):
            with _swapped(census, label, bad):
                value = rt.resolve_token("S2_VERDICT")
            assert expected in value, (
                f"S2_VERDICT does not report {label_} as {expected!r}: {value}")
            _assert_within_density_cap("S2_VERDICT", value, label_)
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted census leaked out of this case")
    return ("S2_VERDICT fails closed on a census with no observed nights (and on an "
            "incoherent one) instead of publishing a habit claim, while still reading "
            "both ways on a single observed night")


@case
def case_no_verdict_prints_a_minus_inside_the_dollar_sigil():
    """FINDING 5. _s4_verdict_short's gap_no > 0 branch tested only the
    NO-battery gap, so a plan leading without a battery and TRAILING with one
    printed "narrows EV-TOU-5's lead over EV-TOU-2 from $200/yr to $-500/yr":
    a "lead" that is not one, and the minus sign inside the sigil. The same
    commit had already fixed exactly this in _plan_margin_vs_runner_up and in
    the branch beside it, which is why this is swept rather than patched --
    every signed figure now goes through _usd0 / _usd0_signed.

    Driven across the matrix's sign combinations for the two plan tokens, and
    across a losing dispatch run for section 6's two modeled savings."""
    assert rt._usd0_signed(-500) == "-$500" and rt._usd0_signed(500) == "$500", (
        "the shared signed-currency formatter no longer puts the sign outside the sigil")
    plans = rt._json("battery_plan_matrix.json")["plans"]
    ordered = sorted(plans, key=lambda k: plans[k]["no_battery"])
    best, runner_up = ordered[0], ordered[1]
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file() else "CEA")
    widths, seen = {}, {}
    with _stub_plan(best, provider):
        published = rt.resolve_token("S4_VERDICT_SHORT")
        assert f"lead over {runner_up}" in published, published
        # Leads without a battery, TRAILS with one: the combination the
        # previous branch structure sent into the "lead ... from X to Y"
        # wording. It is reachable only by moving the WITH-battery column.
        for label, with_battery, expect in (
                ("trails with a battery", plans[best]["with_battery"] - 500,
                 "trails by $500/yr with one"),
                ("ties with a battery", _bill_of(plans[best], "with_battery"),
                 "ties with one")):
            with _cell_priced(plans[runner_up], "with_battery", with_battery):
                value = rt.resolve_token("S4_VERDICT_SHORT")
            assert expect in value, (
                f"S4_VERDICT_SHORT does not say where {best} stands when it {label}: "
                f"{value}")
            for wrong in ("$-", f"lead over {runner_up}", "widens", "narrows"):
                assert wrong not in value, (
                    f"S4_VERDICT_SHORT still prints {wrong!r} while {best} {label}: "
                    f"{value}")
            assert f"leads {runner_up} by" in value, (
                f"S4_VERDICT_SHORT dropped the no-battery lead it really does have: "
                f"{value}")
            widths[label] = _assert_within_density_cap("S4_VERDICT_SHORT", value, label)
            seen[label] = value
        assert rt.resolve_token("S4_VERDICT_SHORT") == published, (
            "the substituted with-battery total leaked out of this case")

        # And the whole no-battery sign grid, for both plan tokens at once.
        for label, no_battery in (("beaten", plans[best]["no_battery"] - 500),
                                  ("tied", _bill_of(plans[best], "no_battery"))):
            with _cell_priced(plans[runner_up], "no_battery", no_battery):
                for token in ("S4_VERDICT_SHORT", "PLAN_MARGIN_VS_RUNNER_UP"):
                    value = rt.resolve_token(token)
                    assert "$-" not in value, (
                        f"{token} prints a minus inside the dollar sigil when {best} is "
                        f"{label}: {value}")

    # Section 6 quotes two modeled savings that another household's dispatch
    # run can return negative.
    for greedy, evening in ((-120, -200), (0, -200), (-120, 0)):
        value = _s6_verdict_at(greedy, evening, greedy - 50)
        assert "$-" not in value, (
            f"S6_VERDICT prints a minus inside the dollar sigil at greedy {greedy}, "
            f"evening {evening}: {value}")
        assert rt._usd0_signed(greedy) in value and rt._usd0_signed(evening) in value, (
            f"S6_VERDICT dropped one of its two modeled savings: {value}")
        widths[f"S6 {greedy}/{evening}"] = _assert_within_density_cap(
            "S6_VERDICT", value, f"greedy {greedy}, evening {evening}")
    return ("no verdict prints a minus inside the dollar sigil: S4_VERDICT_SHORT drops "
            "the word 'lead' when the battery turns the gap negative, and S4/S6/"
            "PLAN_MARGIN_VS_RUNNER_UP all format signed figures through _usd0_signed ("
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_s10_verdict_claims_no_unpriced_effect_when_the_artifact_excludes_nothing():
    """FINDING 6. The size adjective had no zero branch, so an excluded
    net-export credit of exactly $0 still published "a smaller, unpriced
    net-export effect means the whole-household answer is not fully settled"
    -- asserting an effect exists while the artifact says nothing at all was
    excluded. Whether anything IS excluded is asked first, three ways; at zero
    the adjective and the caveat that rests on its existence both go.

    Both signed zeros are driven (the artifact stores this figure as a
    negative credit, so -0.0 is the value a real run would write for "no
    exclusions"), and a non-finite one settles nothing."""
    a = rt._json("cca_bundled_counterfactual.json")["direction_a_cca_repriced_at_bundled"]
    live = a["excluded_net_export_cca_credit_usd"]
    assert live != 0, f"the committed artifact already excludes nothing ({live})"
    with _stub_household(_s10_household_inputs()):
        published = rt.resolve_token("S10_VERDICT")
        assert "unpriced net-export effect" in published and "not fully settled" in published
        for label, zero in (("+0.0", 0.0), ("-0.0", -0.0), ("integer 0", 0)):
            with _swapped(a, "excluded_net_export_cca_credit_usd", zero):
                value = rt.resolve_token("S10_VERDICT")
            for wrong in ("unpriced net-export effect", "not fully settled",
                          "a smaller", "materially larger", "an equally large"):
                assert wrong not in value, (
                    f"S10_VERDICT asserts {wrong!r} at an excluded credit of {label}, "
                    f"while its own artifact says nothing was excluded: {value}")
            assert "no net-export credit excluded" in value, (
                f"S10_VERDICT does not say plainly that nothing was excluded at {label}: "
                f"{value}")
            assert len(value.split()) <= len(published.split()), (
                f"the no-exclusion branch runs longer than the published one: {value}")
        for bad in (float("nan"), float("inf")):
            with _swapped(a, "excluded_net_export_cca_credit_usd", bad):
                try:
                    value = rt.resolve_token("S10_VERDICT")
                    raise AssertionError(
                        f"S10_VERDICT sized an excluded effect of {bad}: {value}")
                except SystemExit as e:
                    assert "S10_VERDICT" in str(e), e
        assert rt.resolve_token("S10_VERDICT") == published, (
            "the substituted excluded credit leaked out of this case")
    return ("S10_VERDICT states that nothing was excluded at an excluded net-export "
            "credit of exactly zero (+0.0, -0.0 and 0), instead of sizing an effect the "
            "artifact does not report")


@case
def case_s7_verdict_prints_a_real_expansion_payback_with_an_agreeing_plural():
    """FINDING 7. The S7 tail printed the expansion's own payback with ":.0f"
    and a hardcoded "years", so it could publish "repays its extra cost in 1
    years" (anything from 0.5 to 1.5 yr) or "in 0 years" (anything under half
    a year) -- a rounded-away figure and a plural that does not agree.

    This branch is only reachable where the first unit never repays, so the
    battery savings are moved with it. Each arm asserts the exact printed
    string, so a regression to ":.0f" fails on the number as well as on the
    noun."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    pk = rt._json("package_results.json")["packages"]
    mid = pk["MID"]
    exp_cost = pk["HIGH"]["cost"] - mid["cost"]
    assert exp_cost > 0, exp_cost
    widths = {}
    with _stub_plan(cheapest, provider):
        published = rt.resolve_token("S7_VERDICT")
        for label, marginal, expected in (
                ("exactly one year", exp_cost, "in 1.0 year"),
                ("just over a year", exp_cost / 1.2, "in 1.2 years"),
                ("under half a year", exp_cost / 0.3, "in 0.3 years"),
                ("many years", exp_cost / 27.3, "in 27.3 years")):
            with _swapped(mid, "battery_alone_yr", 0), \
                    _swapped(mid, "battery_alone_post_ev_fix_yr", 0), \
                    _swapped(mid, "battery_alone_payback_yr", float("inf")), \
                    _swapped(mid, "battery_alone_payback_post_fix_yr", float("inf")), \
                    _swapped(pk["HIGH"], "marginal_vs_mid_yr", marginal):
                value = rt.resolve_token("S7_VERDICT")
            assert f"repays its extra cost {expected}" in value, (
                f"S7_VERDICT does not print the expansion payback at {label} as "
                f"{expected!r}: {value}")
            assert "in 1 years" not in value and "in 0 years" not in value, (
                f"S7_VERDICT prints a truncated payback with a hardcoded plural at "
                f"{label}: {value}")
            widths[label] = _assert_within_density_cap("S7_VERDICT", value, label)
        assert rt.resolve_token("S7_VERDICT") == published, (
            "the substituted expansion saving leaked out of this case")
    return ("S7_VERDICT prints the expansion payback to one decimal with an agreeing "
            "plural (1.0 year, 1.2 / 0.3 / 27.3 years) instead of truncating to "
            '"1 years" or "0 years" ('
            + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


@case
def case_rounding_bound_is_derived_from_the_generator_constant_not_the_prose():
    """FINDING 8. _whole_kwh_rounding_bound's docstring said the bound came
    from analysis/tou_audit.py's ROUNDING_PER_BUCKET, while the code
    regex-parsed a hand-typed sentence out of the artifact's tolerance.basis
    and returned THAT number. The two disagreed about which was the source.

    Resolved by deriving: the constant is the bound and the prose corroborates
    it. Proven by IDENTITY -- the returned object is the constant itself,
    which a float parsed out of a string can never be -- driven under a
    patched constant with matching prose, so the arm cannot pass by the two
    happening to be equal. Both refusals are still driven: a prose bound that
    disagrees, and a basis that no longer states one."""
    bound = rt._whole_kwh_rounding_bound()
    assert bound is rt.ta.ROUNDING_PER_BUCKET, (
        f"the rounding bound {bound} is not analysis/tou_audit.py's own "
        f"ROUNDING_PER_BUCKET object, so it is still being parsed out of prose")
    tol = rt._json("tou_audit_summary.json")["tolerance"]
    moved = 0.25
    assert moved != rt.ta.ROUNDING_PER_BUCKET, moved
    with _patched(rt.ta, "ROUNDING_PER_BUCKET", moved), \
            _swapped(tol, "basis", "statements print whole kWh, so the per-bucket "
                                   "rounding bound is 0.25 kWh and a period's is more"):
        got = rt._whole_kwh_rounding_bound()
        assert got is rt.ta.ROUNDING_PER_BUCKET and got == moved, (
            f"the bound followed the artifact's prose ({got}) rather than the "
            f"generator constant ({moved})")
    # Disagreement and absence both still fail closed, naming both sides.
    with _patched(rt.ta, "ROUNDING_PER_BUCKET", moved):
        try:
            rt._whole_kwh_rounding_bound()
            raise AssertionError("the bound resolved while the artifact and its "
                                 "generator disagree about it")
        except SystemExit as e:
            assert str(moved) in str(e) and "tou_audit" in str(e), e
    with _swapped(tol, "basis", "statements print whole kWh"):
        try:
            rt._whole_kwh_rounding_bound()
            raise AssertionError("the bound resolved off a basis that no longer states one")
        except SystemExit as e:
            assert "ROUNDING_PER_BUCKET" in str(e), (
                f"the refusal does not name the constant it could not corroborate: {e}")
    assert rt._whole_kwh_rounding_bound() is rt.ta.ROUNDING_PER_BUCKET, (
        "the substituted bound leaked out of this case")
    return (f"the whole-kWh bound S1_VERDICT quotes IS analysis/tou_audit.py's "
            f"ROUNDING_PER_BUCKET ({bound} kWh), cross-checked against "
            "data/tou_audit_summary.json's prose rather than parsed out of it")


@case
def case_hour_labels_carry_the_minutes_of_a_fractional_tariff_bound():
    """FINDING 9. The int() sweep missed _hour_label, which truncated the same
    fractional run bounds into the window NAMES sections 2, 5 and 15 print --
    so a tariff whose overnight super-off-peak run ends at 06:30 was labelled
    "12am–6am", mislabelling the very window the report tells the reader to
    charge inside.

    The bound is determinable to the minute, so it is rendered to the minute
    rather than refused; a bound that is not a whole number of minutes has no
    clock label at all and fails closed. Driven end-to-end through S15_VERDICT
    as well as on the labeller, because the label is what ships."""
    for hour, expected in ((0, "12am"), (6, "6am"), (12, "12pm"), (16, "4pm"),
                           (21, "9pm"), (0.25, "12:15am"), (6.5, "6:30am"),
                           (12.5, "12:30pm"), (14.5, "2:30pm"), (23.75, "11:45pm")):
        got = rt._hour_label(hour)
        assert got == expected, f"_hour_label({hour}) is {got!r}, expected {expected!r}"
    assert rt._fmt_hour_range(0, 6.5) == "12am–6:30am", rt._fmt_hour_range(0, 6.5)
    assert rt._fmt_hour_range(16, 21) == "4–9pm", rt._fmt_hour_range(16, 21)
    for bad in (6.001, 0.4321):
        try:
            got = rt._hour_label(bad)
            raise AssertionError(
                f"_hour_label named a bound that is not a whole minute: {bad} -> {got!r}")
        except SystemExit as e:
            assert str(bad) in str(e), e

    # End to end: the Monday appendix tells the reader which window to charge
    # in, and that window's name has to be the tariff's own.
    published = rt.resolve_token("S15_VERDICT")
    assert rt._overnight_cheap_window() in published, published
    with _patched(rt, "_overnight_cheap_run", lambda: (0.0, 6.5, "sop")):
        value = rt.resolve_token("S15_VERDICT")
    assert "12am–6:30am" in value, (
        f"S15_VERDICT names a window half an hour shorter than the tariff's own: {value}")
    assert "12am–6am" not in value, (
        f"S15_VERDICT truncated the tariff's 06:30 boundary into the window it tells the "
        f"reader to charge inside: {value}")
    _assert_within_density_cap("S15_VERDICT", value, "a fractional overnight window")
    assert rt.resolve_token("S15_VERDICT") == published, (
        "the substituted tariff window leaked out of this case")
    return ("_hour_label renders a fractional tariff bound to the minute (06:30 -> "
            '"6:30am") instead of truncating it, so S15_VERDICT names the window the '
            "tariff actually has, and refuses a bound that is not a whole minute")


@case
def case_no_comparison_clause_picks_a_branch_off_a_non_finite_input():
    """The same defect class as findings 1, 3, 4 and 6, swept across every
    remaining COMPARISON in this module rather than patched at the sites the
    review named.

    A nan satisfies no comparison at all -- `nan > 0`, `nan < 0` and
    `nan == x` are every one of them False -- so it falls through each test in
    a branch chain and lands in whichever arm happens to be written last,
    which is a CONFIDENT clause selected by a degenerate input. An infinity is
    the same problem going the other way: it wins every `>` it meets. Neither
    is settled by the artifacts, so both are state 3.

    Every clause that branches on a magnitude is driven at both, and the
    refusal has to name the quantity, or a maintainer reading the failure
    cannot tell which artifact field went bad."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    plans = rt._json("battery_plan_matrix.json")["plans"]
    ordered = sorted(plans, key=lambda k: plans[k]["no_battery"])
    best, runner_up = ordered[0], ordered[1]
    dp = rt._json("battery_dispatch_policies.json")
    pk = rt._json("package_results.json")["packages"]
    a = rt._json("cca_bundled_counterfactual.json")["direction_a_cca_repriced_at_bundled"]
    checked = []
    for bad in (float("nan"), float("inf")):
        # The household's OWN matrix row, not the runner-up's: _runner_up()
        # ranks the others on no_battery, so poisoning a rival's cell just
        # elects a different runner-up and the gap stays finite.
        arms = (
            ("S4_VERDICT_SHORT", plans[best], "with_battery", "with_battery_gap", best),
            ("PLAN_MARGIN_VS_RUNNER_UP", plans[best], "no_battery", "margin", best),
            ("S6_VERDICT", dp["pw3"]["greedy"], "save", "greedy_save", None),
            ("S6_VERDICT", dp["pw3x"]["greedy"], "save", "expanded_save", None),
            ("S7_VERDICT", pk["HIGH"], "marginal_vs_mid_yr",
             "expansion_marginal_saving", cheapest),
            ("S10_VERDICT", a, "delta_usd_per_year", "delta_usd_per_year", None),
            # The four comparisons the previous sweep did not reach (issue
            # #131 review round 4, findings 4 and 5; section 4's own cell
            # check; and issue #178's row class, which is where the ranking
            # of the matrix's two columns moved to). Named the same way, in
            # the same place, so "swept" means the whole module and not the
            # arms that were easy to reach from a verdict token.
            ("S0_VERDICT", pk["MID"], "battery_alone_post_ev_fix_yr",
             "whether the battery repays its own cost", cheapest),
            ("BEST_PLAN_BATT_MODELED", plans[best], "with_battery",
             f"what data/battery_plan_matrix.json prices {best}'s with_battery at", best),
            ("S4_ROW_CLASS", plans[best], "with_battery",
             "cheapest in its with_battery column", best),
            ("BATTERY_PAYBACK_RANGE", pk["MID"], "battery_alone_post_ev_fix_yr",
             "whether the battery repays its own cost", None),
        )
        for token, node, key, named, plan in arms:
            stub = (_stub_plan(plan, provider) if plan is not None
                    else _stub_household(_s10_household_inputs()))
            with stub, _swapped(node, key, bad):
                try:
                    value = rt.resolve_token(token)
                    raise AssertionError(
                        f"{token} picked a branch with {key} = {bad}: {value}")
                except SystemExit as e:
                    assert token in str(e), e
                    assert named in str(e), (
                        f"{token}'s refusal does not name the quantity that went "
                        f"non-finite ({named}): {e}")
            checked.append(f"{token}:{key}")
    return (f"{len(checked)} comparison clauses fail closed naming their own quantity on "
            "a nan or an infinity, instead of falling through into the last branch "
            f"({', '.join(sorted(set(checked)))})")


@case
def case_a_free_fix_worth_under_a_dollar_still_gets_a_report():
    """ROUND 4, FINDING 2. The guard compared the SIGNS of
    behavior_rebuild's scenarios.a.saved and package_results'
    packages.LOW.savings_yr, calling a disagreement a contradiction between
    two committed artifacts.

    analysis/package_results.py writes `savings_yr` as literally
    `round(scenarios.a.saved)`. They are one figure and its whole-dollar
    rounding. So a household whose EV shift is worth $0.37/yr had sign +1
    against sign 0 and got NO REPORT AT ALL -- and every household under fifty
    cents a year with it.

    This case drives that household and asserts the three sentences RENDER.
    The previous round's case moved both fields together, so the pair that
    reproduces the defect never occurred in it.

    The rounding boundary is driven from both sides, and a pair that really
    HAS come apart still refuses -- otherwise this case would pass on a
    formula that simply stopped checking."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    scenario = rt._json("behavior_rebuild.json")["scenarios"]["a"]
    low = rt._json("package_results.json")["packages"]["LOW"]
    tokens = ("S0_VERDICT", "S7_VERDICT", "S15_VERDICT")
    rendered = {}
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in tokens}

        # 1. THE REPRODUCTION, plus the rest of the sub-dollar band. Each pair
        #    is (saved, round(saved)) -- exactly what the generator writes.
        for saved in (0.37, 0.5, -0.37, 0.49, -0.5, 1220.85):
            rounded = round(saved)
            with _swapped(scenario, "saved", saved), \
                    _swapped(low, "savings_yr", rounded):
                for token in tokens:
                    value = rt.resolve_token(token)
                    assert value.strip(), f"{token} rendered blank at a {saved}/yr fix"
                    # Whatever it says, it may not print a mangled figure: the
                    # sign is taken on the WHOLE-DOLLAR value all three print,
                    # so a $0.37 saving cannot reach the loss clause and render
                    # "costs a modeled $-0/yr".
                    assert "$-" not in value and "$0/yr" not in value, (
                        f"{token} printed a mangled figure at a {saved}/yr free fix: "
                        f"{value}")
                    _assert_within_density_cap(token, value, f"a {saved}/yr free fix")
                    rendered[f"{token} at {saved}"] = value
            # Sub-dollar savings round to nothing, and the sentence says so
            # rather than selling a move it would price at $0.
            if rounded == 0:
                for token in tokens:
                    assert "adds no modeled saving" in rendered[f"{token} at {saved}"], (
                        f"{token} sells a free fix its own printed figure rounds to "
                        f"zero ({saved}/yr): {rendered[f'{token} at {saved}']}")

        # 2. A pair the rounding cannot explain still fails closed, so the
        #    tolerance did not simply delete the check.
        for saved, drifted in ((1220.85, 1222), (1220.85, 1220), (0.37, 1)):
            apart = abs(saved - drifted) > 0.5
            with _swapped(scenario, "saved", saved), \
                    _swapped(low, "savings_yr", drifted):
                for token in tokens:
                    try:
                        value = rt.resolve_token(token)
                        assert not apart, value
                    except SystemExit as e:
                        assert apart, (
                            f"{token} refused a pair inside the half-dollar the rounding "
                            f"itself moves ({saved} vs {drifted}): {e}")
                        assert token in str(e) and "rounding" in str(e), e

        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted free-fix saving leaked out of this case ({token})")
    return ("a $0.37/yr free EV-charging fix gets a report -- all three sentences render, "
            "reading the whole-dollar figure they print -- while a pair further apart "
            "than the rounding explains still fails closed")


@case
def case_the_plan_ranking_refuses_a_non_finite_total_instead_of_emptying_itself():
    """ROUND 4, FINDING 4. The non-finite sweep reached this module's other
    comparisons and skipped _plan_ranking, which feeds three sentences.

    A nan in data/plan_results.csv's total column makes `t == cheapest` False
    for EVERY row, including its own, so `winners` came back EMPTY: S0_VERDICT
    published "a cheaper rate plan exists" off a non-finite input, S3_VERDICT
    named a plan that had not won anything, and BEST_PLAN died several tokens
    later with a bare IndexError rather than this module's named refusal.

    Both non-finite values are driven, on the household's own row and on a
    rival's, and the refusal has to name the PLAN -- the message is the only
    thing telling a maintainer which CSV cell went bad."""
    provider, cheapest, priced = _plan_ranking_inputs()
    rival = min((r for r in priced if r["plan"] != cheapest),
                key=lambda r: float(r["total"]))
    own = next(r for r in priced if r["plan"] == cheapest)
    tokens = ("S0_VERDICT", "S3_VERDICT", "BEST_PLAN")
    checked = []
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in tokens}
        for row in (own, rival):
            for bad in ("nan", "inf", "-inf"):
                with _swapped(row, "total", bad):
                    for token in tokens:
                        try:
                            value = rt.resolve_token(token)
                            raise AssertionError(
                                f"{token} ranked plans with {row['plan']} priced at "
                                f"{bad}/yr: {value}")
                        except SystemExit as e:
                            assert token in str(e), e
                            assert "which rate plan is cheapest" in str(e), (
                                f"{token}'s refusal does not name the indeterminate "
                                f"quantity: {e}")
                            assert row["plan"] in str(e) and bad in str(e), (
                                f"{token}'s refusal does not name the plan whose total "
                                f"went non-finite: {e}")
                checked.append(f"{row['plan']}={bad}")
        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted plan total leaked out of this case ({token})")
    return ("the shared plan ranking fails closed naming the plan whose total went "
            f"non-finite ({', '.join(checked)}), instead of returning an empty winner "
            "list that published a confident clause and an IndexError")


@case
def case_s2_verdict_refuses_a_non_finite_night_census():
    """ROUND 4, FINDING 5. Section 2's EV-timing clause is three-state, but
    the gate never tested finiteness -- and Python's json parser accepts a
    bare NaN.

    A nan census satisfies none of `observed <= 0`, `absent < 0`,
    `charging < 0` or `charging > absent`, so it fell straight through into
    the confident opposite branch: "the EV does not usually charge overnight",
    a claim about an observed habit, selected by a non-number.

    Both census fields are driven at both non-finite values. The coherent
    cases either side are driven too, so this cannot pass on a formula that
    refuses everything."""
    census = (rt._json("quiet_night_floor.json")["night_floor"]
              ["issue_114_investigation"]["ev_absence_by_window"])
    lo, hi, _lab = rt._overnight_cheap_run()
    entry = census[f"{int(lo)}-{int(hi)}h"]
    checked = []
    with _stub_household(_s2_household_inputs()):
        published = rt.resolve_token("S2_VERDICT")
        for key in ("n", "n_eligible_nights"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with _swapped(entry, key, bad):
                    try:
                        value = rt.resolve_token("S2_VERDICT")
                        raise AssertionError(
                            f"S2_VERDICT published an overnight-charging habit with "
                            f"{key} = {bad}: {value}")
                    except SystemExit as e:
                        assert "S2_VERDICT" in str(e), e
                        assert "whether the EV usually charges overnight" in str(e), (
                            f"S2_VERDICT's refusal does not name the indeterminate "
                            f"quantity: {e}")
                checked.append(f"{key}={bad}")

        # It still DISCRIMINATES: a real census either way renders, and the
        # two coherent answers are different sentences.
        observed = entry["n_eligible_nights"]
        assert observed > 2, observed
        readings = {}
        for label, absent in (("mostly charging", 0), ("mostly absent", observed)):
            with _swapped(entry, "n", absent):
                readings[label] = rt.resolve_token("S2_VERDICT")
        assert "while the EV charges overnight" in readings["mostly charging"], readings
        assert "does not usually charge overnight" in readings["mostly absent"], readings
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted census leaked out of this case")
    return ("S2_VERDICT fails closed naming the habit it cannot claim on a non-finite "
            f"night census ({', '.join(checked)}), and still reads both ways on a real "
            "one")


@case
def case_the_payback_card_never_prints_a_payback_the_battery_does_not_have():
    """ROUND 4, FINDING 6. BATTERY_PAYBACK_RANGE fills the section 0 card
    "Battery-alone payback with price-aware dispatch". It read packages.MID's
    two payback fields directly and sorted them, bypassing every check the
    verdict printed beside it goes through.

    So with a negative battery-alone saving the card printed the package cost
    divided by that loss as though it were a length of time -- "~-290.0-6.2
    yr" -- and it printed it directly above a verdict saying the battery does
    not repay its own cost.

    It goes through the shared resolver now and spans only the paybacks that
    EXIST. Driven at: the PRE-fix scenario losing money (the card names the
    post-fix payback, which is the one the report's verdicts are written on),
    the POST-fix scenario losing money (no payback the report says the battery
    has, so the card fails closed -- see the round-5 note below), both losing,
    and an artifact whose payback contradicts its own saving (the resolver's
    own refusal, which this token used to skip).

    ROUND 5, FINDING 3 amended arm 1 of this case. It used to assert that a
    battery losing money AFTER the free fix but repaying before it prints the
    PRE-fix payback -- pinning, as the correct answer, the card reading "6.2
    yr" a few pixels above a verdict that reads "does not repay its own cost"
    on the very household round 4's own finding 1 introduced (+$2,328 before
    the fix, -$50 after it). The card and the verdict describe one purchase,
    the verdict is written on the post-fix scenario, and a card asserting a
    payback the sentence beside it denies is the defect this case exists to
    prevent. So that arm now asserts the refusal, and arm 5 below drives the
    card and both verdicts together across every mixed pair."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    live = (mid["battery_alone_yr"], mid["battery_alone_post_ev_fix_yr"])
    assert min(live) > 0, live
    published = rt.resolve_token("BATTERY_PAYBACK_RANGE")
    assert "-" not in published.replace("–", ""), (
        f"the published card already carries a minus sign: {published!r}")

    # 1. The PRE-fix scenario losing money: the card quotes the post-fix
    #    payback, which is the one every verdict is written on, and never the
    #    cost divided by a loss.
    with _mid_battery_at(mid, -50, live[1]):
        value = rt.resolve_token("BATTERY_PAYBACK_RANGE")
    expected = f"{round(mid['cost'] / live[1], 1):.1f} yr"
    assert value == expected, (
        f"the payback card reads {value!r} with a -50/yr pre-fix battery-alone saving; "
        f"the only payback the report's verdicts stand behind is {expected!r}")
    assert "-" not in value, (
        f"the payback card printed a cost divided by a loss as a payback: {value}")

    # 2. The post-fix scenario does not repay -- whatever the other one says.
    #    There is no payback the report stands behind, so the card refuses
    #    rather than quoting the surviving scenario's. (2328, -50) is the
    #    review's own household, the one arm 1 used to pin the other way.
    for pre, post in ((-50, -400), (0, 0), (0, -400),
                      (2328, -50), (live[0], -50), (live[0], 0)):
        with _mid_battery_at(mid, pre, post):
            try:
                value = rt.resolve_token("BATTERY_PAYBACK_RANGE")
                raise AssertionError(
                    f"the payback card printed {value!r} for a battery whose savings are "
                    f"{pre}/{post} per year -- neither scenario has a payback")
            except SystemExit as e:
                assert "BATTERY_PAYBACK_RANGE" in str(e), e
                assert "report-template.html" in str(e), (
                    f"the refusal does not name the chrome that makes it one: {e}")

    # 3. The consistency check the token used to bypass entirely.
    for bad in (0, -3.0, float("inf")):
        with _swapped(mid, "battery_alone_payback_post_fix_yr", bad):
            try:
                value = rt.resolve_token("BATTERY_PAYBACK_RANGE")
                raise AssertionError(
                    f"the payback card printed {value!r} while packages.MID pairs a "
                    f"{live[1]}/yr saving with a {bad}-year payback")
            except SystemExit as e:
                assert "BATTERY_PAYBACK_RANGE" in str(e), e

    # 4. ...and it cannot be switched off from the numerator's side. Without a
    #    usable cost there is nothing to divide by a saving, so the paybacks
    #    would be quoted on the artifact's own say-so.
    for bad in (float("nan"), 0, -14500):
        with _swapped(mid, "cost", bad):
            try:
                value = rt.resolve_token("BATTERY_PAYBACK_RANGE")
                raise AssertionError(
                    f"the payback card printed {value!r} unchecked while packages.MID "
                    f"reports a cost of {bad}")
            except SystemExit as e:
                assert "BATTERY_PAYBACK_RANGE" in str(e) and "cost" in str(e), e

    # 5. ROUND 5, FINDING 3, as the property rather than as three examples:
    #    the card never asserts a payback while the two verdicts printed
    #    around it deny the battery repays. Driven across every mixed pair,
    #    with the verdicts resolved in the same substituted state.
    provider, cheapest, _priced = _plan_ranking_inputs()
    agreed = []
    with _stub_plan(cheapest, provider):
        for pre, post in ((2328, -50), (live[0], -50), (-50, live[1]),
                          (live[0], 0), (0, live[1]), (live[0], live[1])):
            with _mid_battery_at(mid, pre, post):
                s0, s7 = rt.resolve_token("S0_VERDICT"), rt.resolve_token("S7_VERDICT")
                try:
                    card = rt.resolve_token("BATTERY_PAYBACK_RANGE")
                except SystemExit:
                    card = None
            says_repays = _battery_reading(s0, s7) == ("repays", "repays")
            assert (card is not None) == says_repays, (
                f"at pre {pre}/post {post} the section 0 card reads {card!r} while the "
                f"two verdicts read {_battery_reading(s0, s7)} -- a card asserting a "
                "payback the sentences beside it deny")
            agreed.append(f"{pre}/{post}:{'quoted' if card else 'withheld'}")
        assert len({a.split(':')[1] for a in agreed}) == 2, (
            f"every pair went the same way, so agreement proves nothing: {agreed}")

    assert rt.resolve_token("BATTERY_PAYBACK_RANGE") == published, (
        "the substituted battery figures leaked out of this case")
    return (f"the section 0 payback card reads {published!r} on the committed artifact, "
            "names the post-fix payback when the pre-fix scenario loses money, withholds "
            "the card whenever the verdicts beside it say the battery does not repay "
            f"({', '.join(agreed)}), and fails closed rather than printing a cost "
            "divided by a loss")


@case
def case_a_window_ending_at_midnight_is_named_midnight_not_noon():
    """ROUND 4, FINDING 7. _weekday_runs() closes its last run at h = 24.0 --
    the same instant as h = 0.0, and "12am" on a clock. The meridiem test was
    a bare `hour < 12`, and 24 is not less than 12, so the label came out
    "12pm": a tariff whose last run ends at midnight named a window ending at
    NOON, twelve hours wrong, in the sentence telling the reader when the
    cheap window closes.

    Driven on the labeller, on the range formatter, and end to end through the
    Monday appendix, which is the sentence a reader would act on."""
    assert rt._hour_label(24) == "12am", rt._hour_label(24)
    assert rt._hour_label(24.5) == "12:30am", rt._hour_label(24.5)
    assert rt._hour_label(0) == "12am" and rt._hour_label(12) == "12pm"
    assert rt._fmt_hour_range(21, 24) == "9pm–12am", rt._fmt_hour_range(21, 24)
    assert rt._fmt_hour_range(22, 24) == "10pm–12am", rt._fmt_hour_range(22, 24)
    # A run that BEGINS at the midnight bound elides no meridiem either -- the
    # "12" hours are excluded from the elision modulo the day.
    assert rt._fmt_hour_range(24, 30) == "12am–6am", rt._fmt_hour_range(24, 30)

    published = rt.resolve_token("S15_VERDICT")
    with _patched(rt, "_overnight_cheap_run", lambda: (18.0, 24.0, "sop")):
        value = rt.resolve_token("S15_VERDICT")
    assert "6pm–12am" in value, (
        f"S15_VERDICT names a window ending at midnight as ending at noon: {value}")
    assert "12pm" not in value, value
    _assert_within_density_cap("S15_VERDICT", value, "a window ending at midnight")
    assert rt.resolve_token("S15_VERDICT") == published, (
        "the substituted tariff window leaked out of this case")
    return ('a tariff window bound at h = 24.0 is named "12am", so a run ending at '
            'midnight prints "6pm–12am" rather than a window ending at noon')


# Every quantity in this module whose SIGN the artifact decides rather than
# the schema: a difference, a modeled saving or value, an NPV, an overlap
# deduction. Matched against a token's name AND its source path, so a figure
# named for what it is cannot be declared with a formatter that hides a minus
# inside the dollar sigil.
_SIGNED_QUANTITY_RE = re.compile(
    r"save|saving|marginal|delta|value|npv|double_count|margin|gap|overlap", re.I)
_UNSIGNED_CURRENCY_FMTS = {"usd0", "usd0_tilde", "usd2", "usd3"}
# Tokens the name-and-path probe flags whose quantity is NOT signed, each one
# traced to its generator rather than waved through on the word:
#
#   FIRST_YEAR_VALUE -- analysis/lifetime_payback.py computes
#   years[].value_usd as `PROD[y] * blended_rate * RATE_IDX[y] /
#   RATE_IDX[cur_year]`: a product of a kWh count and two rates, every factor
#   non-negative by construction. It is a LEVEL that happens to be called a
#   value, not a difference. (Contrast SOLAR_ANNUAL_VALUE, which really is
#   nosolar_bill_usd minus the modeled baseline, and is signed.)
_UNSIGNED_BY_CONSTRUCTION = {"FIRST_YEAR_VALUE"}


@case
def case_no_signed_currency_token_prints_a_minus_inside_the_dollar_sigil():
    """ROUND 4, FINDINGS 8 AND 10, swept. This defect has now been found at
    five exits over three review rounds: two verdict clauses, the section 7
    expansion-cost refusal, and BATTERY_MARGINAL_SAVINGS, which rendered
    packages.MID.battery_alone_yr -- a modeled saving that is negative on any
    household the battery costs money -- through fmt="usd0".

    Two sweeps, so the class is closed rather than patched again:

      1. MECHANICAL. Every token whose name or source path says it carries a
         signed quantity must be declared with a signed formatter. A new token
         called ..._SAVINGS or pointing at a ...delta field cannot ship with
         fmt="usd0".
      2. BEHAVIOURAL. Every signed formatter, and every refusal message in
         this module that can print a non-positive currency figure, is driven
         at a negative value and must put the sign OUTSIDE the sigil."""
    offenders, exempt = [], []
    for name, spec in rt.TOKENS.items():
        if spec.get("fmt") not in _UNSIGNED_CURRENCY_FMTS:
            continue
        probe = name + " " + " ".join(str(k) for k in spec.get("path", ()))
        if not _SIGNED_QUANTITY_RE.search(probe):
            continue
        (exempt if name in _UNSIGNED_BY_CONSTRUCTION else offenders).append(
            f"{name} ({spec['fmt']}, {probe.strip()})")
    assert not offenders, (
        "token(s) carrying a signed quantity are declared with an unsigned currency "
        f"formatter, so a negative renders as \"$-500\": {sorted(offenders)}")
    # The reasoned exemptions stay live: an entry that no longer matches the
    # probe at all has stopped documenting anything and should come out.
    assert len(exempt) == len(_UNSIGNED_BY_CONSTRUCTION), (
        f"_UNSIGNED_BY_CONSTRUCTION lists {sorted(_UNSIGNED_BY_CONSTRUCTION)} but the "
        f"probe only reaches {sorted(exempt)}; a stale exemption hides nothing and "
        "should be deleted")

    for fmt in ("usd0_signed", "usd0_tilde_signed", "usd0_plus"):
        rendered = rt.FORMATTERS[fmt](-500)
        assert "$-" not in rendered and rendered.endswith("$500"), (
            f"FORMATTERS[{fmt!r}] renders -500 as {rendered!r}")
    assert rt.FORMATTERS["usd0_plus"](8656) == "+$8,656"
    assert rt.FORMATTERS["usd0_plus"](0) == "$0"
    assert rt.FORMATTERS["usd0_tilde_signed"](4900) == "~$4,900"

    # End to end, on the token the review named. The substitution moves the
    # POST-fix scenario, which is the one this token has read since round 5's
    # finding 10 re-based it onto the scenario section 7's verdict quotes --
    # and it moves the payback with it, because package_results.py writes the
    # payback as cost / saving and report_tokens checks that derivation.
    mid = rt._json("package_results.json")["packages"]["MID"]
    with _mid_battery_at(mid, mid["battery_alone_yr"], -120):
        value = rt.resolve_token("BATTERY_MARGINAL_SAVINGS")
    assert value == "-$120", value

    # And in the refusal messages, which ship to a maintainer rather than a
    # reader but are the exits this class keeps reappearing at.
    pk = rt._json("package_results.json")["packages"]
    provider, cheapest, _priced = _plan_ranking_inputs()
    messages = []
    with _stub_plan(cheapest, provider):
        for node, key, bad in ((pk["HIGH"], "cost", pk["MID"]["cost"] - 500),
                               (pk["LOW"], "cost", -250)):
            with _swapped(node, key, bad):
                try:
                    value = rt.resolve_token("S7_VERDICT")
                    raise AssertionError(f"S7_VERDICT rendered at {key} = {bad}: {value}")
                except SystemExit as e:
                    assert "$-" not in str(e), (
                        f"S7_VERDICT's refusal prints a minus inside the dollar sigil: {e}")
                    messages.append(str(e).split(" -- ")[-1])
    return (f"no token declared with an unsigned currency formatter carries a signed "
            f"quantity, the three signed formatters put the sign outside the sigil, "
            f"BATTERY_MARGINAL_SAVINGS renders -$120 at a negative saving, and section "
            f"7's two cost refusals read {messages}")


@case
def case_both_worth_doing_clauses_resolve_through_one_shared_ladder():
    """ROUND 4, FINDING 9. _free_fix_saving and _battery_alone carried
    byte-identical three-state ladders -- finite, then above zero, then
    otherwise -- which is how the same wrong comparison ended up written twice
    in two different shapes.

    The ladder is _sign_claim now, and this asserts BOTH resolvers reach it
    rather than that the source happens to look similar: the shared helper is
    replaced with a recorder, and each resolver must be seen calling it with
    its own subject.

    What is deliberately NOT shared is the relationship check in front of it.
    Those two sites read differently-related artifact pairs -- a rounding pair
    and a two-scenario pair -- and folding their checks together is what put
    both through one sign test to begin with. So the recorder also asserts the
    two arrive with DIFFERENT subjects and different magnitudes."""
    seen = []

    def recorder(token, subject, magnitude, detail):
        seen.append((token, subject, magnitude))
        return real(token, subject, magnitude, detail)

    real = rt._sign_claim
    with _patched(rt, "_sign_claim", recorder):
        rt._free_fix_saving("PROBE_FIX")
        rt._battery_alone("PROBE_BATTERY")
    subjects = {token: subject for token, subject, _m in seen}
    assert set(subjects) == {"PROBE_FIX", "PROBE_BATTERY"}, (
        f"one of the two 'is this worth doing' resolvers does not go through the shared "
        f"three-state ladder: {seen}")
    assert len(set(subjects.values())) == 2, (
        f"the two resolvers reached the ladder with the same subject, so a refusal "
        f"cannot say which claim it is about: {subjects}")
    magnitudes = {token: m for token, _s, m in seen}
    assert magnitudes["PROBE_FIX"] != magnitudes["PROBE_BATTERY"], (
        "both resolvers judged the same magnitude; they read different artifacts")
    assert not seen[len(seen):], seen
    return ("_free_fix_saving and _battery_alone both resolve through _sign_claim, each "
            f"with its own subject ({sorted(subjects.values())}) and its own magnitude")


# ===========================================================================
# THE POISON HARNESS (issue #131, review round 5, part B).
#
# WHAT ROUND 4 SHIPPED, AND WHY IT COULD NOT WORK. Round 4 closed its
# "minus inside the dollar sigil" finding with a MECHANICAL sweep -- the probe
# in case_no_signed_currency_token_prints_a_minus_inside_the_dollar_sigil,
# which walks TOKENS and matches each entry's NAME and declared source PATH
# against a regex of signed-quantity words. It is a structural probe over
# DECLARATIONS, and the population it can see is the population that declares
# a `fmt` and a `path`. Twenty-one tokens in report_tokens.py are `derived`
# formulas that build a dollar string with an f-string; the probe cannot see
# one of them, and every one of them could publish "$-1,234", "$nan" or
# "inf kg CO2/MWh" out of a single bad artifact field. Round 5 found four such
# tokens by reading, which is how the previous four rounds found their ten
# each. Reading does not scale and does not repeat.
#
# WHAT THIS DOES INSTEAD. It drives REAL RESOLUTION and reads the OUTPUT.
# For every token, for every numeric field of every artifact the token is
# observed to touch, it substitutes each of nan, inf, -inf, -0.0, 0 and a
# negative value into report_tokens' in-memory artifact cache, resolves the
# token, and requires the outcome to be one of exactly two things:
#
#     a SystemExit whose message NAMES the token, or
#     a rendered string carrying none of the malformed shapes below.
#
# Anything else -- a nan on the page, a minus inside a sigil, a doubled
# sigil, an empty numeric field, a blank render, an unnamed refusal, or any
# other exception type escaping resolve_token -- is a failure.
#
# HOW THE FIELDS A TOKEN READS ARE DISCOVERED. Two mechanisms, and neither
# trusts a declaration:
#
#   1. WHICH ARTIFACTS, by a RECORDING LOADER. report_tokens._json,
#      ._csv_rows and .hh1 are wrapped for one clean resolution per token and
#      record what they were asked for. This is the recording-loader option
#      the round-5 brief judged sturdiest, and for the same reason: it
#      observes the reads rather than believing TOKENS' `sources` list, which
#      is prose, is not consumed by anything, and is exactly the kind of
#      declaration finding 9 proved unreliable.
#
#   2. WHICH FIELDS INSIDE THEM, by OBSERVABLE EFFECT. Every numeric leaf of
#      each touched JSON file (and every fully-numeric column of each touched
#      CSV) is poisoned in turn and the token re-resolved. If the outcome is
#      byte-identical to the clean one, the token PROVABLY does not read that
#      field through that value, and there is nothing to assert. If it
#      differs at all, the token reads it, and the outcome must satisfy the
#      contract above.
#
#      This is deliberately NOT a path-recording proxy over the parsed JSON.
#      A proxy has to intercept every way Python reaches a value --
#      __getitem__, .get, .items(), .values(), iteration, slicing, max(key=)
#      -- and a single missed access path is a silent blind spot pointing in
#      exactly the direction this harness exists to remove. Poison-and-compare
#      does not need to know how the value was reached, only whether the
#      answer moved.
#
# AND THE HARNESS PROVES ITS OWN COVERAGE, which is the part round 5 left out
# and round 6 had to add (issue #131 review round 6, finding 1).
#
#      Round 5's version selected the CSV columns to poison with a filter that
#      kept a column only if every cell passed bare float().
#      data/enphase_daily_production.csv's one numeric column ends in the
#      Total footer "16,501.77" -- a thousands separator float() rejects, and
#      the very cell report_tokens._annual_production_kwh reads after
#      .replace(",", ""). So the swept-column list for that file was EMPTY,
#      the TWELVE tokens that read it got ZERO probes, and the run still
#      reported 51,414 probes and PASS. A guard reporting success while
#      covering nothing is the exact shape six review rounds have been
#      finding, and a filter that just learns about commas leaves the next
#      such gap equally silent.
#
#      So coverage is no longer a by-product to be reported; it is a
#      PROPERTY THIS CASE ASSERTS, in four parts:
#
#        (a) an artifact a token is OBSERVED to read must contribute at
#            least one poisoned field. Zero is a named FAILURE carrying the
#            token and the artifact, never a silent skip.
#        (b) every field the sweep DECLINES to poison is recorded with a
#            reason from a closed set, and the whole declined set is asserted
#            against the ledger below -- so a new unparseable column, or a
#            column that stops parsing, cannot silently join it. A column
#            that is PARTLY numeric is a failure outright: that ambiguity is
#            where a blind spot hides.
#        (c) a token that reads any artifact must end with a non-zero probe
#            count, and the tokens that legitimately end at zero (their only
#            reads are household answers that hold no number at all -- a plan
#            name, a PTO date) are named in a frozen set, not inferred.
#        (d) a token this checkout cannot resolve must be unresolvable
#            BECAUSE the private archive is absent, proven twice over: its
#            refusal names household.yaml AND it was observed reading a
#            household path. Nothing else may shrink coverage. Round 5
#            asserted that bucket only where the archive is staged and it is
#            always empty, so on the archive-less runner CI actually uses it
#            was never asserted at all (review round 6, finding 9).
#
#      The reads themselves are recorded at report_tokens' own chokepoints:
#      _json, _csv_rows and _hh_value. _hh_value, not hh1 -- hh1 is one of
#      its callers, and the household_yaml leaf tokens, `vehicles`,
#      `monitoring[]` and `cleaning_history` all reach household.yaml past
#      hh1, so wrapping hh1 recorded none of them (same class as finding 1,
#      found by asking which accessors exist rather than which one was
#      already wrapped). A household answer is poisoned at its NUMERIC
#      LEAVES, so the $200 inside cleaning_history's list of dicts is swept
#      the same way a JSON leaf is.
#
# THERE IS NO ALLOWLIST, and that is a consequence of the contract rather than
# an omission. The contract is on the OUTPUT, not on refusing: a count that
# renders "0 days" on a poisoned input, a date, a name, a share that reads
# "-1,234%" are all already permitted, because none of them puts a non-number
# or a misplaced sign in front of a reader. The exemption mechanism a token
# would need is therefore never reached, so adding one would document nothing.
# ===========================================================================
_POISON_NUMBERS = (float("nan"), float("inf"), float("-inf"), -0.0, 0, -1234.5)
# The CSV cache holds the strings csv.DictReader produced, and every consumer
# in report_tokens calls float() on them -- so the poison goes in as text, and
# a nan reaches the arithmetic the same way a nan in a JSON artifact does.
_POISON_STRINGS = ("nan", "inf", "-inf", "-0.0", "0", "-1234.5")

_MALFORMED_RENDER = (
    # "nan"/"inf" as a NUMERAL, not as a substring of a word: the lookarounds
    # keep "information" and "infrastructure" out of it.
    ("a non-finite numeral",
     re.compile(r"(?<![A-Za-z])(nan|inf|infinity)(?![A-Za-z])", re.I)),
    ("a minus inside a sigil", re.compile(r"\$-|\+\$-")),
    ("a doubled sigil", re.compile(r"\$\$|%%|¢¢|~~")),
    # A sigil with no number behind it, and a unit with no number in front:
    # the shapes a formula produces when its figure formats to nothing.
    #
    # THE "]" EXEMPTION, AND WHY IT IS CONDITIONAL. tou_spread.py writes its
    # confidence intervals as "[1.68, 21.0]%/yr" and publishes that string in
    # the not_determined_because reasons SPREAD_TREND_WINTER renders verbatim
    # (issue #132) -- a percent sign with a fully-formed number behind it,
    # which is the opposite of the shape this pattern hunts.
    #
    # The first version of that exemption was `(?<![\d)\]])%`, which exempts a
    # BRACKET rather than a NUMBER: it let "[]%" through -- an interval that
    # formatted to nothing, wearing a percent sign -- which is precisely the
    # class this pattern exists for. Reviewer catch on issue #132; latent
    # rather than live (an empty ci95 list sends both spread tokens down the
    # not-determined branch, which prints no interval at all), but a guard that
    # covers less than it claims is the failure this project keeps paying for.
    #
    # So the exemption is two fixed-width lookbehinds and requires a DIGIT
    # immediately before the bracket: "21.0]%" passes, "[]%" and "[nan]%" are
    # flagged, and "%" after a space or a letter is untouched. "(1.3)%" still
    # passes on the first lookbehind, which has exempted ")" since round 5.
    #
    # The cents pattern is left exactly as it was BEFORE issue #132. Nothing
    # renders "]¢", so widening it bought no render and only ever cost
    # coverage; the smallest correct change is to give that back rather than
    # to grant a narrower version of an exemption no token uses.
    ("an empty numeric field",
     re.compile(r"\$(?![\d,])|(?<![\d)])(?<!\d\])%|(?<![\d)])¢")),
)


def _outcome(name, spec):
    """(kind, payload) for one resolution: 'ok' with the rendered string,
    'exit' with the refusal text, or 'raised' with the exception type."""
    try:
        return ("ok", rt.resolve_token(name, spec))
    except SystemExit as e:
        return ("exit", str(e))
    except BaseException as e:                                   # noqa: BLE001
        return ("raised", f"{type(e).__name__}: {e}")


def _assert_poison_outcome(failures, name, field, poison, got):
    kind, payload = got
    if kind == "exit":
        if name not in payload:
            failures.append(f"{name} at {field} = {poison!r}: refused WITHOUT naming "
                            f"itself, so nothing tells a maintainer which token broke "
                            f"-- {payload[:200]}")
        return
    if kind == "raised":
        failures.append(f"{name} at {field} = {poison!r}: escaped resolve_token as "
                        f"{payload[:200]} instead of this module's named refusal")
        return
    if not payload.strip():
        failures.append(f"{name} at {field} = {poison!r}: rendered blank")
        return
    for label, pattern in _MALFORMED_RENDER:
        m = pattern.search(payload)
        if m:
            failures.append(f"{name} at {field} = {poison!r}: rendered {label} "
                            f"({m.group(0)!r}) -- {payload[:200]}")
            return


def _numeric_leaf_paths(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _numeric_leaf_paths(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _numeric_leaf_paths(value, path + (i,))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield path


def _at_path(node, path):
    for key in path[:-1]:
        node = node[key]
    return node, path[-1]


def _csv_number(cell):
    """The number a report_tokens consumer gets out of a CSV cell, or None.

    NORMALISED THE WAY THE CONSUMERS NORMALISE, which is the whole point of
    the function existing. report_tokens._annual_production_kwh reads
    data/enphase_daily_production.csv's Total footer as
    float(cell.replace(",", "")); _daily_production_series skips a blank cell
    instead of parsing it; nothing in that module hands a raw cell to float()
    without one of those two accommodations.

    Round 5's filter was a bare float(), which is STRICTER than either -- and
    a filter stricter than the consumers it stands in for stops sweeping
    exactly the fields they read, without saying so. Three columns were
    silently unswept by it: enphase_daily_production's only numeric column
    (the "16,501.77" footer), gas_bill_summary[nonbaseline_rate] and
    threeway_production_validation[meter_derived] (both blank in a few rows).
    """
    if not isinstance(cell, str):
        return None
    text = cell.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# The closed set of reasons the sweep may decline to poison a field. Closed on
# purpose: a decline that does not fit one of these is not a decline, it is a
# gap, and _poisonable_columns says so rather than inventing a fourth reason.
_DECLINE_TEXT = "no cell parses as a number"
_DECLINE_BLANK = "every cell is blank"
_DECLINE_RAGGED = "csv.DictReader overflow key, not a column"
_DECLINE_NO_NUMBER = "the household answer holds no number"
_DECLINE_MIXED = ("SOME cells parse as numbers and some do not -- account for this "
                  "column explicitly instead of letting it fall out of the sweep")


def _poisonable_columns(rows):
    """([columns to poison], {column: why it was declined}) for one CSV table.

    Every column of the table lands in exactly one of the two, so the sweep
    can assert it looked at all of them."""
    poison, declined = [], {}
    for column in list(rows[0]):
        cells = [r.get(column) for r in rows]
        if any(not isinstance(c, str) for c in cells):
            declined[column] = _DECLINE_RAGGED
            continue
        filled = [c for c in cells if c.strip()]
        if not filled:
            declined[column] = _DECLINE_BLANK
            continue
        parsed = [_csv_number(c) for c in filled]
        if all(p is not None for p in parsed):
            poison.append(column)
        elif all(p is None for p in parsed):
            declined[column] = _DECLINE_TEXT
        else:
            declined[column] = _DECLINE_MIXED
    return poison, declined


# ---------------------------------------------------------------------------
# THE COVERAGE LEDGER. Every field the sweep declines to poison, and every
# token that therefore ends with no probes, named here rather than inferred at
# run time -- that is what makes a NEW silent gap fail this case instead of
# quietly enlarging the exempt set.
#
# The CSV half is asserted on every checkout (these files are committed). The
# household half needs private/household.yaml to be reachable at all, so it is
# asserted as an exact set where the archive is staged and as a subset where
# it is not; the unresolvable-bucket assertion is what stops coverage
# shrinking on that runner.
# ---------------------------------------------------------------------------
_DECLINED_CSV_COLUMNS = {
    # Dates, plan names, TOU-period labels and provider names: text columns
    # with no numeric form to substitute. Nothing here is a NUMBER a token
    # could publish malformed, which is why declining them covers nothing up.
    "data/bill_tou_detail.csv[period]": _DECLINE_TEXT,
    "data/bill_tou_detail.csv[season]": _DECLINE_TEXT,
    "data/bill_tou_detail.csv[section]": _DECLINE_TEXT,
    "data/bill_tou_detail.csv[statement_date]": _DECLINE_TEXT,
    "data/bill_tou_detail.csv[tou_period]": _DECLINE_TEXT,
    "data/electric_bill_summary.csv[period]": _DECLINE_TEXT,
    "data/enphase_daily_production.csv[Date/Time]": _DECLINE_TEXT,
    "data/gas_bill_summary.csv[file_month]": _DECLINE_TEXT,
    "data/plan_results.csv[plan]": _DECLINE_TEXT,
    "data/plan_results.csv[provider]": _DECLINE_TEXT,
    "data/pvoutput_daily.csv[date]": _DECLINE_TEXT,
    "data/threeway_production_validation.csv[]": _DECLINE_TEXT,
}

_DECLINED_HOUSEHOLD_PATHS = {
    # Household answers that are names, dates or lists of strings. A plan
    # name has no non-finite form; a PTO date has no minus to misplace.
    "private/household.yaml:household.cca": _DECLINE_NO_NUMBER,
    "private/household.yaml:household.climate_zone": _DECLINE_NO_NUMBER,
    "private/household.yaml:household.nem_version": _DECLINE_NO_NUMBER,
    "private/household.yaml:household.plan": _DECLINE_NO_NUMBER,
    "private/household.yaml:household.pto_date": _DECLINE_NO_NUMBER,
    "private/household.yaml:household.utility": _DECLINE_NO_NUMBER,
    # The provenance three (issue #135): tool names. "Claude Code (Opus 5)"
    # carries digits, but they are part of a product name, not a quantity --
    # there is no non-finite form of a model number and nothing downstream does
    # arithmetic on it.
    "private/household.yaml:provenance.generation_tool": _DECLINE_NO_NUMBER,
    "private/household.yaml:provenance.review_tool_adversarial": _DECLINE_NO_NUMBER,
    "private/household.yaml:provenance.review_tool_independent": _DECLINE_NO_NUMBER,
    "private/household.yaml:monitoring[].measures": _DECLINE_NO_NUMBER,
    "private/household.yaml:monitoring[].resolution": _DECLINE_NO_NUMBER,
    "private/household.yaml:monitoring[].source": _DECLINE_NO_NUMBER,
    "private/household.yaml:solar.install_paid_date": _DECLINE_NO_NUMBER,
    "private/household.yaml:solar.inverter_model": _DECLINE_NO_NUMBER,
}

# Tokens whose ONLY observed reads are declined fields, so zero probes is the
# right answer for them and not a gap. Every one is a name, a date or a
# provider string -- there is no number in any of them to publish malformed.
_TOKENS_WITH_NO_POISONABLE_FIELD = {
    "CLIMATE_ZONE", "DATA_SOURCES_DETAIL", "DATA_SOURCES_SUMMARY",
    "GENERATION_PROVIDER", "GENERATION_PROVIDER_SHORT", "INSTALL_PAYMENT_DATE",
    "NEM_EXPIRY_YEAR", "NEM_STATUS", "PTO_DATE", "RATE_SOURCES_DETAIL",
    "SIZE_VERIFICATION_SOURCE", "UTILITY_NAME",
    # The provenance three (issue #135). They were cited_constant tokens, which
    # this sweep excludes by KIND; moving them onto private/household.yaml made
    # them derived, so they now need naming here for the same reason the twelve
    # above do. Their only field is a tool name, whose digits are part of a
    # product name rather than a quantity -- see the matching entries in
    # _DECLINED_HOUSEHOLD_PATHS.
    "GENERATION_TOOL", "REVIEW_TOOL_1", "REVIEW_TOOL_2",
}

# Tokens that read no committed artifact at all. Every one resolves out of
# analysis/rates.py (the tariff windows, the day-band prices, the effective
# date) or the clock, neither of which this sweep can poison -- rates.py is
# code, and its own guard tests live in analysis/test_rates.py. `cited_constant`
# tokens are in the same position by construction and are excluded by KIND
# rather than by name, so adding one needs no edit here; a DERIVED token that
# suddenly reads nothing does need one, which is the point.
_TOKENS_THAT_READ_NO_ARTIFACT = {
    "BILLED_GENERATION_RATES", "CHEAP_WINDOW", "DAYBAND_OFFPEAK_PRICE",
    "DAYBAND_ONPEAK_PRICE", "DAYBAND_SEASON_LABEL", "DAYBAND_SOP_PRICE",
    "MIDDAY_MARGINAL_VALUE_RANGE",
    "PEAK_WINDOW", "RATES_EFFECTIVE_DATE",
    "RECOMMENDED_PACKAGE_SUMMARY",
    "REPORT_VERSION", "S14_VERDICT", "SUMMER_ONPEAK_EXPORT_RATE",
    "SUMMER_ONPEAK_IMPORT_RATE", "SUPER_OFF_PEAK_RATE",
}


class _recording_loaders:
    """Wrap report_tokens' three artifact CHOKEPOINTS so one resolution
    records WHICH artifacts it touched, without changing what any of them
    return.

    _hh_value and not hh1: hh1 is one of _hh_value's callers, and
    resolve_token's own household_yaml branch, `vehicles`, `monitoring[]` and
    `cleaning_history` all reach household.yaml without going through hh1. A
    recorder on hh1 saw none of those reads."""

    def __init__(self, seen):
        self.seen = seen

    def __enter__(self):
        self.real = (rt._json, rt._csv_rows, rt._hh_value)
        real_json, real_csv, real_hh = self.real

        def json_(name):
            self.seen.add(("json", name))
            return real_json(name)

        def csv_(name):
            self.seen.add(("csv", name))
            return real_csv(name)

        def hh_(path):
            self.seen.add(("household", path))
            return real_hh(path)

        rt._json, rt._csv_rows, rt._hh_value = json_, csv_, hh_
        return self

    def __exit__(self, *exc):
        rt._json, rt._csv_rows, rt._hh_value = self.real


class _poisoned_household_leaf:
    """One numeric leaf of one household.yaml answer, replaced for the length
    of the block.

    A COPY is substituted rather than the cached document mutated: household
    answers come back through privacy_tiers.resolve(), and a substitution that
    silently failed to reach the consumer would make the sweep conclude "this
    token does not read that field" -- a blind spot pointing the way the whole
    harness exists to look. Returning the poisoned copy from the accessor
    cannot fail that way, and it leaves nothing behind to restore."""

    def __init__(self, path, leaf, value):
        self.path, self.leaf, self.value = path, leaf, value

    def __enter__(self):
        self.real = rt._hh_value

        def hh_(path):
            values = self.real(path)
            if path != self.path:
                return values
            values = copy.deepcopy(values)
            parent, key = _at_path(values, self.leaf)
            parent[key] = self.value
            return values

        rt._hh_value = hh_
        return self

    def __exit__(self, *exc):
        rt._hh_value = self.real


class _Sweep:
    """Everything one run of the sweep observed. Assertions live in the cases
    that call it, so the run itself can be driven under a deliberately broken
    discovery step and INSPECTED rather than only passing or failing."""

    def __init__(self):
        self.failures = []        # contract violations: a malformed render
        self.coverage = []        # coverage violations: a field never swept
        self.probes = {}          # token -> how many poison probes ran
        self.reads = {}           # token -> {(kind, who)} observed
        self.declined = {}        # field -> why the sweep did not poison it
        self.unresolvable = {}    # token -> (refusal text, reads observed)
        self.baseline = {}        # token -> its clean render


def _sweep_json(sw, name, spec, who, clean):
    artifact = rt._json(who)
    paths = list(_numeric_leaf_paths(artifact))
    if not paths:
        sw.coverage.append(
            f"{name} reads data/{who}, which contributes NO poisonable field: the "
            "artifact holds no numeric leaf, so this token is swept against none of it")
        return
    for path in paths:
        parent, key = _at_path(artifact, path)
        original = parent[key]
        field = f"data/{who}:{'.'.join(str(k) for k in path)}"
        for poison in _POISON_NUMBERS:
            parent[key] = poison
            try:
                got = _outcome(name, spec)
            finally:
                parent[key] = original
            sw.probes[name] += 1
            if got != clean:
                _assert_poison_outcome(sw.failures, name, field, poison, got)


def _sweep_csv(sw, name, spec, who, clean):
    rows = rt._csv_rows(who)
    if not rows:
        sw.coverage.append(f"{name} reads data/{who}, which is empty")
        return
    columns, declined = _poisonable_columns(rows)
    for column, why in declined.items():
        sw.declined[f"data/{who}[{column}]"] = why
    if not columns:
        sw.coverage.append(
            f"{name} reads data/{who}, which contributes NO poisonable field: every "
            f"column was declined ({sorted(declined.items())}), so this token is swept "
            "against none of it")
        return
    # Poisoned a COLUMN at a time: a per-cell sweep over a 8,736-row series is
    # 150k resolutions for one token and localises a failure this harness does
    # not need localised -- the contract is on the rendered string, not on
    # which row produced it. Cells that are BLANK stay blank: a consumer that
    # skips them (_daily_production_series does) is then exercised on the same
    # row shape it really sees, with only the values changed.
    for column in columns:
        original = [r[column] for r in rows]
        field = f"data/{who}[{column}]"
        for poison in _POISON_STRINGS:
            for row in rows:
                if row[column].strip():
                    row[column] = poison
            try:
                got = _outcome(name, spec)
            finally:
                for row, was in zip(rows, original):
                    row[column] = was
            sw.probes[name] += 1
            if got != clean:
                _assert_poison_outcome(sw.failures, name, field, poison, got)


def _sweep_household(sw, name, spec, who, clean):
    """A household answer, poisoned at every NUMERIC LEAF of it.

    Leaf-wise and not value-wise, because _hh_value answers with a list and
    some of those answers are containers: cleaning_history is a list of dicts
    whose cost_usd a token publishes behind a dollar sigil. Poisoning only
    scalar answers left every number inside a structured answer unswept."""
    values = rt._hh_value(who)
    field = f"private/household.yaml:{who}"
    leaves = list(_numeric_leaf_paths(values))
    if not leaves:
        sw.declined[field] = _DECLINE_NO_NUMBER
        return
    for leaf in leaves:
        for poison in _POISON_NUMBERS:
            with _poisoned_household_leaf(who, leaf, poison):
                got = _outcome(name, spec)
            sw.probes[name] += 1
            if got != clean:
                _assert_poison_outcome(sw.failures, name, field, poison, got)


def _unresolvable_gaps(sw):
    """The tokens that dropped out of a sweep for any reason OTHER than the
    absent private archive -- {token: why}, empty when coverage shrank only
    through the one door it is allowed to shrink through.

    Two independent tests, so neither can be satisfied by accident: the
    refusal must NAME household.yaml, and the token must have been OBSERVED
    reading a household path on its way to it. Round 5 asserted this bucket
    only under `if rt.hh.PATH.is_file()`, where it is always empty -- so on
    the archive-less runner .github/workflows/tests.yml actually uses, the
    bucket that holds every lost token was never asserted on at all, and
    coverage could shrink to nothing while the case reported the same success
    (issue #131 review round 6, finding 9)."""
    return {name: why[:200] for name, (why, seen) in sw.unresolvable.items()
            if "household.yaml" not in why
            or not any(kind == "household" for kind, _who in seen)}


def _run_poison_sweep(tokens=None):
    """Drive every token through the sweep and return what it observed."""
    tokens = rt.TOKENS if tokens is None else tokens
    sw = _Sweep()
    for name, spec in tokens.items():
        if spec.get("kind") == "gap":
            continue
        seen = set()
        with _recording_loaders(seen):
            clean = _outcome(name, spec)
        sw.reads[name] = seen
        if clean[0] != "ok":
            # A token this checkout cannot resolve at all has no clean answer
            # to compare a poisoned one against. Recorded WITH its reason and
            # its observed reads, both of which the case asserts on.
            sw.unresolvable[name] = (clean[1], seen)
            continue
        sw.baseline[name] = clean[1]
        sw.probes[name] = 0
        for kind, who in sorted(seen):
            if kind == "json":
                _sweep_json(sw, name, spec, who, clean)
            elif kind == "csv":
                _sweep_csv(sw, name, spec, who, clean)
            else:
                _sweep_household(sw, name, spec, who, clean)
    return sw


@case
def case_no_token_publishes_a_malformed_number_from_any_poisoned_artifact_field():
    """ROUND 5, PART B. The behavioural sweep described in the block comment
    above: every token, every numeric field it is observed to read, six poison
    values each, and one contract on the outcome -- a refusal that names the
    token, or a render carrying none of the malformed shapes.

    WHAT THIS CASE DOES AND DOES NOT CLOSE, stated exactly, because the
    version of this docstring that claimed to close six named findings was
    describing something else it does (issue #131 review round 6, finding 4).
    Its contract is on the SHAPE OF THE RENDERED STRING, so the class it
    closes is the malformed-number one: "$nan", "-$nan", "$inf", a minus
    inside a sigil, a doubled sigil, a sigil with no number behind it, a blank
    render, an unnamed refusal, and any other exception escaping resolve_token.
    It closes that class for tokens that do not exist yet, because it
    discovers what a token reads instead of being told.

    It does NOT close, and never could:
      * PAIRING defects -- two well-formed figures for one quantity (round 5's
        finding 10), guarded by
        case_the_two_figures_for_the_batterys_own_saving_quote_one_scenario;
      * refusals that are too BROAD -- a report withheld over a figure nothing
        prints (round 5's finding 4), guarded by
        case_an_unusable_package_cost_only_stops_the_figures_that_need_it;
      * a well-formed number that is simply WRONG, or a confident sentence
        selected by a degenerate-but-finite input (round 6's finding 6).
    Each round-5 finding keeps its own named case below; this sweep is the
    floor under all of them, not a replacement for any.

    The second half of the case asserts the sweep's own COVERAGE -- see the
    block comment's parts (a) to (d). A harness that can report success while
    sweeping nothing is worth less than no harness, because it also stops
    anyone looking (review round 6, finding 1)."""
    sw = _run_poison_sweep()
    probes = sum(sw.probes.values())
    covered = sorted(sw.probes)

    assert not sw.failures, (
        f"{len(sw.failures)} token/field/value combination(s) publish a malformed number "
        "or fail without naming themselves:\n  " + "\n  ".join(sorted(sw.failures)[:40]))

    # (a) every artifact a token reads contributed at least one poisoned field.
    assert not sw.coverage, (
        f"{len(sw.coverage)} token/artifact pair(s) were swept against NOTHING, so this "
        "harness would have reported success while covering them not at all:\n  " +
        "\n  ".join(sorted(sw.coverage)[:40]))

    # (b) every declined field is accounted for, by name and by reason.
    mixed = {f: why for f, why in sw.declined.items() if why == _DECLINE_MIXED}
    assert not mixed, (
        f"{len(mixed)} column(s) are partly numeric, which is where a blind spot hides: "
        f"{sorted(mixed)}")
    csv_declined = {f: w for f, w in sw.declined.items() if f.startswith("data/")}
    hh_declined = {f: w for f, w in sw.declined.items() if not f.startswith("data/")}
    # Per FILE, and exactly, so the check has the same force on the runner
    # with no private archive -- where a few CSVs are simply never reached,
    # and a whole-set equality would have to be relaxed to a subset for all of
    # them. Every file the sweep DID open is accounted for column by column.
    for who in sorted({w for reads in sw.reads.values() for k, w in reads if k == "csv"}):
        prefix = f"data/{who}["
        observed = {f: w for f, w in csv_declined.items() if f.startswith(prefix)}
        expected = {f: w for f, w in _DECLINED_CSV_COLUMNS.items() if f.startswith(prefix)}
        assert observed == expected, (
            f"the columns the sweep declines to poison in data/{who} have changed; "
            "account for each new one in _DECLINED_CSV_COLUMNS rather than letting it "
            f"fall out of the sweep\n  joined : {sorted(set(observed) - set(expected))}\n"
            f"  left   : {sorted(set(expected) - set(observed))}")
    assert set(csv_declined) <= set(_DECLINED_CSV_COLUMNS), (
        f"undeclared CSV columns: {sorted(set(csv_declined) - set(_DECLINED_CSV_COLUMNS))}")
    if rt.hh.PATH.is_file():
        assert csv_declined == _DECLINED_CSV_COLUMNS, (
            "a whole CSV dropped out of the sweep on the staged archive: "
            f"{sorted(set(_DECLINED_CSV_COLUMNS) - set(csv_declined))}")
    if rt.hh.PATH.is_file():
        assert hh_declined == _DECLINED_HOUSEHOLD_PATHS, (
            "the set of household answers the sweep declines to poison has changed\n"
            f"  joined : {sorted(set(hh_declined) - set(_DECLINED_HOUSEHOLD_PATHS))}\n"
            f"  left   : {sorted(set(_DECLINED_HOUSEHOLD_PATHS) - set(hh_declined))}")
    else:
        assert set(hh_declined) <= set(_DECLINED_HOUSEHOLD_PATHS), sorted(hh_declined)

    # (c) a token that read an artifact got probes; the ones that could not are named.
    silent = {n for n in covered if sw.probes[n] == 0 and sw.reads[n]}
    unnamed = silent - _TOKENS_WITH_NO_POISONABLE_FIELD
    assert not unnamed, (
        f"token(s) read an artifact and got ZERO poison probes: {sorted(unnamed)} -- "
        "either the discovery step stopped seeing their fields, or they belong in "
        "_TOKENS_WITH_NO_POISONABLE_FIELD with the reason written down")
    if rt.hh.PATH.is_file():
        assert silent == _TOKENS_WITH_NO_POISONABLE_FIELD, (
            "_TOKENS_WITH_NO_POISONABLE_FIELD no longer matches the tokens that "
            f"actually end at zero probes: {sorted(silent)}")
    readless = {n for n in covered
                if not sw.reads[n] and rt.TOKENS[n]["kind"] != "cited_constant"}
    assert readless <= _TOKENS_THAT_READ_NO_ARTIFACT, (
        f"token(s) resolve without reading any artifact this sweep can poison: "
        f"{sorted(readless - _TOKENS_THAT_READ_NO_ARTIFACT)} -- if that is right, name "
        "them in _TOKENS_THAT_READ_NO_ARTIFACT; if it is not, they reach a data file "
        "past _json / _csv_rows / _hh_value and the recorder cannot see it")

    # (d) coverage may shrink ONLY through the missing-private-archive door,
    #     and every token that went through it is made to prove it did.
    wrong = _unresolvable_gaps(sw)
    assert not wrong, (
        f"{len(wrong)} token(s) dropped out of the sweep for a reason other than the "
        f"absent private archive, so coverage shrank silently: {wrong}")
    if rt.hh.PATH.is_file():
        assert not sw.unresolvable, (
            f"token(s) do not resolve on this staged archive: {sorted(sw.unresolvable)}")

    # The sweep has to have SWEPT something -- an empty run passes trivially.
    assert probes > 20000, f"only {probes} poison probes ran; the discovery step broke"
    assert len(covered) > 100, f"only {len(covered)} tokens were driven: {covered}"

    # And nothing leaked: every substitution was restored.
    after = {n: rt.resolve_token(n, rt.TOKENS[n]) for n in covered}
    moved = {n: (sw.baseline[n], after[n]) for n in covered if sw.baseline[n] != after[n]}
    assert not moved, f"the sweep left substituted values behind: {moved}"
    return (f"{probes:,} poison probes across {len(covered)} tokens and their observed "
            f"artifact fields: every outcome is either a refusal naming the token or a "
            f"clean render, every artifact read contributed at least one probe, and all "
            f"{len(sw.declined)} declined field(s) are accounted for by name"
            + (f" ({len(sw.unresolvable)} token(s) held out: no private archive)"
               if sw.unresolvable else ""))


@case
def case_the_sweep_fails_when_it_covers_an_artifact_with_nothing():
    """ROUND 6, FINDING 1. The harness above reported 51,414 probes and PASS
    while sweeping data/enphase_daily_production.csv with NOTHING.

    Its column filter kept a column only where every cell passed bare
    float(). That file's one numeric column ends in the Total footer
    "16,501.77" -- the thousands separator float() rejects, and the very cell
    report_tokens._annual_production_kwh reads after .replace(",", ""). So the
    swept-column list was empty and the twelve tokens that read the file got
    no probes at all, silently, while the run reported a five-figure probe
    count and its own comment asserted it "has no blind spot available to it".

    Two properties, and the second is the one that matters. FIRST, the parser
    now normalises a cell the way the consumers do, so the three columns that
    were silently skipped are swept. SECOND, and independently of any parser:
    a token observed to read an artifact that contributes no poisonable field
    is a COVERAGE FAILURE naming both, so the NEXT such gap cannot be silent
    either. Driven by reintroducing the round-5 filter and by blinding a
    column outright, and asserting the run FAILS on coverage rather than
    narrowing itself."""
    rows = rt._csv_rows("enphase_daily_production.csv")
    columns, declined = _poisonable_columns(rows)
    assert columns == ["Energy Delivered (kWh)"], (
        f"the production column is no longer the swept one: {columns} / {declined}")
    assert _csv_number("16,501.77") == 16501.77, "the Total footer must parse"
    assert _csv_number("") is None and _csv_number("Total") is None

    # The three columns round 5's filter dropped, each swept now.
    for who, column in (("enphase_daily_production.csv", "Energy Delivered (kWh)"),
                        ("gas_bill_summary.csv", "nonbaseline_rate"),
                        ("threeway_production_validation.csv", "meter_derived")):
        keep, _why = _poisonable_columns(rt._csv_rows(who))
        assert column in keep, f"data/{who}[{column}] is still unswept: kept {keep}"

    # A token that reads the file and ignores what it says: with the real
    # parser it is covered, and it is the vehicle for blinding the column
    # without also making the token unresolvable (an unresolvable token is a
    # different bucket, asserted separately).
    probe = "ZZZ_FABRICATED_READS_A_CSV_TOKEN"

    def reads_it(ctx):
        rt._csv_rows("enphase_daily_production.csv")
        return "a constant"

    fabricated = {probe: {"kind": "derived", "get": reads_it, "fmt": None},
                  "ANNUAL_PRODUCTION_KWH": rt.TOKENS["ANNUAL_PRODUCTION_KWH"]}
    good = _run_poison_sweep(fabricated)
    assert not good.coverage, good.coverage
    assert good.probes[probe] > 0 and good.probes["ANNUAL_PRODUCTION_KWH"] > 0, good.probes
    real_probes = good.probes["ANNUAL_PRODUCTION_KWH"]

    # 1. The round-5 filter, put back exactly: bare float() per cell.
    def round_five_filter(cell):
        try:
            return float(cell)
        except (TypeError, ValueError):
            return None

    with _patched(sys.modules[__name__], "_csv_number", round_five_filter):
        regressed = _run_poison_sweep(fabricated)
    assert regressed.probes[probe] == 0, (
        "the round-5 filter did not reproduce the defect; this case is not testing it")
    named = [f for f in regressed.coverage
             if probe in f and "enphase_daily_production.csv" in f]
    assert named, (
        "the round-5 column filter left data/enphase_daily_production.csv swept with "
        f"nothing and the harness did not say so: {regressed.coverage}")

    # 2. Any other reason a column stops parsing, not just a comma.
    real_csv_rows = rt._csv_rows
    blinded = [dict(r, **{"Energy Delivered (kWh)": "n/a"}) for r in rows]
    with _patched(rt, "_csv_rows",
                  lambda n: blinded if n == "enphase_daily_production.csv"
                  else real_csv_rows(n)):
        outright = _run_poison_sweep({probe: fabricated[probe]})
    assert outright.probes[probe] == 0, outright.probes
    assert any(probe in f for f in outright.coverage), outright.coverage
    return (f"the three columns round 5's filter silently dropped are swept again "
            f"(ANNUAL_PRODUCTION_KWH: {real_probes} probes), and a token reading an "
            f"artifact that contributes no poisonable field is a named coverage FAILURE "
            f"-- reproduced twice, on the round-5 filter and on a blinded column")


def _stub_for(artifact, edit):
    """A drop-in rt._json that hands out an EDITED DEEP COPY of one artifact
    and the real document for every other, so a stub cannot leak into the
    module's cache or into a later case."""
    real = rt._json

    def stubbed(name):
        if name != artifact:
            return real(name)
        doc = copy.deepcopy(real(name))
        edit(doc)
        return doc

    return stubbed


def _stub_for_many(edits):
    """_stub_for, for a household that reads differently in MORE THAN ONE
    artifact.

    A household with no EV is the instance (issue #147): behavior_rebuild.py
    stubs four of its own blocks AND package_results.py prices a different
    scenario in packages.LOW, and a case that patched only one of the two
    would be checking a pair of artifacts no run ever produces -- an
    inconsistent pair is a state _free_fix_saving deliberately REFUSES, so
    stubbing half of it tests the refusal rather than the household.

    `edits` is {artifact: edit}; every other artifact is the real document,
    and each edited one is a deep copy, so nothing here reaches rt._json_cache
    or a later case."""
    real = rt._json

    def stubbed(name):
        doc = real(name)
        if name in edits:
            doc = copy.deepcopy(doc)
            edits[name](doc)
        return doc

    return stubbed


def _renders(token):
    """resolve_token, with a REFUSAL turned into this case's own failure.

    resolve_token signals refusal with SystemExit, which is a BaseException:
    left to propagate it would end the whole run instead of failing the case
    that provoked it, and "the suite stopped" reads nothing like "this token
    refuses a household that merely differs", which is the defect being
    tested."""
    try:
        return rt.resolve_token(token)
    except SystemExit as e:
        raise AssertionError(
            f"{token} REFUSED an artifact state that is simply the other reading, "
            f"instead of rendering the sentence written for it: {e}")


def _refuses(token):
    """The mirror of _renders: resolve_token on artifacts that CONTRADICT each
    other, with the refusal returned as its message and a render treated as
    the failure.

    _renders exists because a refusal on an ordinary household withholds the
    whole report. This one exists because the opposite is just as bad: a
    figure published off two artifacts that disagree about what it measures is
    a number with no evidence behind it, which CLAUDE.md section 0 forbids
    outright."""
    try:
        value = rt.resolve_token(token)
    except SystemExit as e:
        return str(e)
    raise AssertionError(
        f"{token} RENDERED {value!r} on artifacts that contradict each other about "
        f"which scenario the free behavior fix IS, instead of failing closed")


# ---------------------------------------------------------------------------
# THE NO-EV HOUSEHOLD, IN THE SHAPE analysis/behavior_rebuild.py ACTUALLY
# WRITES IT (issue #147).
#
# The fixture below used to stub `detection` and nothing else. Everything the
# six section-9 EV tokens read was covered by that, so those six passed --
# against a document no generator emits. scenarios a and b still carried real
# `saved` figures; `battery` still carried the post-EV-shift pair; and
# package_results.json still priced scenario a. SEVEN further tokens read
# exactly the fields that fixture left intact, every one of them aborted the
# ENTIRE report on a real no-EV household (generate_report.run() blocks the
# write on any non-gap token failure), and no case could see it. That gap is
# issue #147, and a fixture narrower than the generator's real output is the
# mechanism by which it stayed invisible -- so the shape below is checked
# field-for-field against a genuinely generated no-EV artifact set rather than
# written from a reading of the generator.
#
# The FIGURES stay this household's own (scenarios c and d are real numbers
# here and are pure house-load shifts in this branch); only the SHAPE is the
# no-EV one.
# ---------------------------------------------------------------------------
_NO_EV_REASON = ("household.has_ev is false (intake applicability flag, "
                 "DATA-SOURCES-CHEATSHEET.md) — ")
_NO_EV_INTAKE_TAIL = ("; set the flag true and complete the intake (charger.kw) "
                      "to compute it")


def _no_ev_stub(what):
    return {"not_applicable": True,
            "reason": f"{_NO_EV_REASON}{what}{_NO_EV_INTAKE_TAIL}"}


def _no_ev_behavior(doc):
    """data/behavior_rebuild.json exactly as the generator writes it when the
    intake says household.has_ev is false: FOUR explicit stubs, a battery
    block that has lost its post-EV-shift pair and gained a stub in their
    place, and a different top-level note."""
    doc["detection"] = _no_ev_stub(
        "EV charging detection does not apply to this household")
    for key in ("a", "b"):
        doc["scenarios"][key] = _no_ev_stub(
            "the EV-only shift scenario does not apply to this household")
    # c and d keep their figures and lose the EV half of their labels: with no
    # EV rung under them they are the whole of the move, so every kWh they
    # shift is house load.
    for key, label in (("c", "c: 25% flexible house load"),
                       ("d", "d: stretch - 50% house load")):
        node = doc["scenarios"][key]
        node["label"] = label
        node["kwh_moved"] = node["house_kwh_moved"]
    battery = doc["battery"]
    battery.pop("marginal_after_scenario_a", None)
    battery.pop("double_count_avoided", None)
    battery["marginal_after_ev_shift"] = _no_ev_stub(
        "the post-EV-shift battery marginal does not apply to this household")
    battery["note"] = ("no EV shift to sit on top of, so there is no "
                       "behavior/battery double-count to avoid")
    doc["note"] = ("household.has_ev is false: the EV rungs (detection, scenarios "
                   "a/b) are not applicable and scenarios c/d are pure house-load "
                   "shifts. Report DELTAS between scenarios, not the absolute "
                   "model bill.")


def _no_ev_package(c, d):
    """data/package_results.json's LOW package for that same household: the
    free fix is scenario c, and savings_yr is its whole-dollar rounding.

    Written as the generator writes it -- savings_yr IS round(c.saved),
    free_fix_scenario says so, and the projected bill is that same scenario's
    -- because _free_fix_saving checks that derivation, and a fixture that
    only half-agreed would be testing the drift guard rather than the
    household."""
    def edit(doc):
        low = doc["packages"]["LOW"]
        low["free_fix_scenario"] = "c"
        low["savings_yr"] = round(c["saved"])
        low["savings_range"] = [round(c["saved"]), round(d["saved"])]
        low["projected_bill_current_rates_yr"] = round(c["bill"])
        low["note"] = ("household.has_ev is false (intake applicability flag), so "
                       "there is no EV charging to reschedule; the free fix here is "
                       "moving flexible on-peak house load off peak, into the "
                       f"super-off-peak window: 25% of it = ${round(c['saved']):,}; "
                       f"stretch (50%) = ${round(d['saved']):,}")
    return edit


# ---------------------------------------------------------------------------
# THE OTHER TWO ARTIFACTS THE SAME FLAG REWRITES.
#
# The fixture above covered behavior_rebuild.json and package_results.json and
# stopped there, and that boundary is not a design -- it is where the last
# reader stopped looking. Two artifacts further downstream also read
# household.has_ev and also publish not-applicable stubs when it is false, and
# because neither was in the fixture, every token that reads them was resolved
# against an EV-household document by every no-EV case in this file:
#
#   data/carbon_fullyear_results.json -- 13 EV-domain fields become stubs.
#     SEC13_TEASER subscripted one of them (footprints_kg_co2_per_yr.detail.
#     midday_cleaner_than_overnight_by) unconditionally, handed _figures a
#     dict, and took the WHOLE REPORT down on a household with no EV.
#   data/extended_results.json -- electrification_dividend and
#     supercharge_delta become whole-section stubs.
#
# The shapes below are checked field-for-field against a genuinely generated
# no-EV artifact set (both generators run end to end on a no-EV intake), not
# written from a reading of the generators: the key sets, the stub contract
# (`not_applicable` + `reason`), the two DIFFERENT reason tails the two
# generators use, the extra `see` sentence carbon_fullyear.py appends to its
# two midday-vs-overnight stubs, the caveat that disappears, and the cost_note
# that changes. As above, the FIGURES stay this household's own; only the
# SHAPE is the no-EV one.
# ---------------------------------------------------------------------------

# analysis/extended_findings.py:_not_applicable's tail. It is NOT
# _NO_EV_INTAKE_TAIL: what is missing there is a whole section, not one
# charger fact, so that generator asks for "the intake" rather than
# "the intake (charger.kw)". A fixture that used the other tail would be
# checking a string no generator writes.
_NO_EV_SECTION_TAIL = ("; set the flag true and complete the intake to "
                       "compute it")

# analysis/carbon_fullyear.py:_SEE_WINDOW_MEANS, appended to the two stubs
# whose grid-side half IS still measured -- the sentence that tells a reader
# where the answer went, and the one SEC13_TEASER acts on.
_NO_EV_SEE_WINDOW_MEANS = (
    "The GRID-side comparison this figure applies EV load to is measured for "
    "every household and is published unchanged at "
    "intensity_kg_per_mwh.window_means_annual (sop_overnight_00_06 vs "
    "solar_midday_10_14, kg CO2/MWh); only the household-EV application of it "
    "is absent here")


def _no_ev_section_stub(flag="household.has_ev"):
    """extended_findings.py's whole-section stub, verbatim in shape."""
    return {"not_applicable": True,
            "reason": (f"{flag} is false (intake applicability flag, "
                       "DATA-SOURCES-CHEATSHEET.md) — the section does not "
                       "apply to this household" + _NO_EV_SECTION_TAIL)}


def _no_ev_carbon_stub(what, see=""):
    """carbon_fullyear.py's _not_applicable: _no_ev_stub's contract, plus the
    optional trailing sentence naming where the grid-side figure still is.

    `what` is the generator's own `what` argument, so the "does not apply to
    this household" clause its format string supplies is added HERE rather
    than typed into every entry of the inventory. It was left out of the
    first draft of this fixture, and the resulting reason strings were
    plausible English that no generator writes -- the same class of drift as
    the fixture that stubbed only `detection`."""
    stub = _no_ev_stub(f"{what} does not apply to this household")
    if see:
        stub["reason"] += f". {see}"
    return stub


# Every EV-domain field carbon_fullyear.py stubs when household.has_ev is
# false, with the `what` clause each one carries. Written as an inventory
# rather than thirteen assignments for the same reason the generator keeps
# one: a field added on either side has one obvious place to appear.
_NO_EV_CARBON_FIELDS = (
    (("household_inputs", "ev_kwh_detected"),
     "detected EV charging energy", ""),
    (("household_inputs", "ev_kwh_mistimed_on_off_peak"),
     "EV charging energy mistimed into on/off-peak hours", ""),
    (("footprints_kg_co2_per_yr", "b_mistimed_ev_moved_to_sop_00_06"),
     "the footprint with mistimed EV charging moved to 00:00-06:00", ""),
    (("footprints_kg_co2_per_yr", "c_mistimed_ev_moved_to_midday_10_14"),
     "the footprint with mistimed EV charging moved to 10:00-14:00", ""),
    (("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_at_current_hours"),
     "the carbon carried by mistimed EV charging at its current hours", ""),
    (("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_if_charged_00_06"),
     "the carbon of that same EV energy charged 00:00-06:00", ""),
    (("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_if_charged_10_14"),
     "the carbon of that same EV energy charged 10:00-14:00", ""),
    (("footprints_kg_co2_per_yr", "detail", "delta_b_vs_a"),
     "the footprint change from moving EV charging to 00:00-06:00", ""),
    (("footprints_kg_co2_per_yr", "detail", "delta_c_vs_a"),
     "the footprint change from moving EV charging to 10:00-14:00", ""),
    (("footprints_kg_co2_per_yr", "detail", "midday_cleaner_than_overnight_by"),
     "the midday-vs-overnight carbon gap on this household's mistimed EV "
     "charging", _NO_EV_SEE_WINDOW_MEANS),
    (("old_vs_new", "ev_shift_delta_to_sop_kg"),
     "the old-vs-new comparison of the EV-shift delta to 00:00-06:00", ""),
    (("old_vs_new", "ev_shift_delta_to_midday_kg"),
     "the old-vs-new comparison of the EV-shift delta to 10:00-14:00", ""),
    (("old_vs_new", "midday_cleaner_than_overnight_by_kg"),
     "the old-vs-new comparison of the midday-vs-overnight gap on this "
     "household's mistimed EV charging", _NO_EV_SEE_WINDOW_MEANS),
)

# The method caveat carbon_fullyear.py emits only when the EV shift was
# actually computed. It disappears with the EV, and a fixture that kept it
# would publish a method note for a computation this household never ran.
_NO_EV_CARBON_DROPPED_CAVEAT = ("Moved EV energy assumed spread uniformly "
                                "across the destination window on its own day.")


def _no_ev_carbon(doc):
    """data/carbon_fullyear_results.json as carbon_fullyear.py writes it when
    household.has_ev is false.

    WHAT STAYS REAL IS THE POINT. intensity_kg_per_mwh is a CAISO measurement
    -- what a MWh drawn overnight costs against one drawn at midday, whoever
    draws it -- and so are the current-import footprint, the import/export
    kWh, and the avoided-export carbon. Only the figures that APPLY an absent
    EV load to that grid measurement become stubs. SEC13_TEASER's whole no-EV
    branch stands on that distinction, so a fixture that blanked the intensity
    section too would make the fixed token look broken and the broken one look
    fine."""
    for path, what, see in _NO_EV_CARBON_FIELDS:
        node = doc
        for key in path[:-1]:
            node = node[key]
        assert path[-1] in node, (
            f"carbon_fullyear_results.json has no {'.'.join(path)} to stub -- the "
            "artifact moved and this fixture now describes a document no "
            "generator writes")
        node[path[-1]] = _no_ev_carbon_stub(what, see)
    doc["caveats"] = [c for c in doc["caveats"]
                      if c != _NO_EV_CARBON_DROPPED_CAVEAT]
    doc["cost_note"] = (
        "This household has no EV (household.has_ev is false), so there is no "
        "charge timing to fix and NO mistimed-charging dollar saving to price: "
        "behavior_rebuild.json publishes scenario 'a' as an explicit "
        "not-applicable stub rather than a figure. The carbon figures above "
        "are unaffected -- they are measured on this household's own imports "
        "and exports. Artifact reason: "
        + _no_ev_stub("the EV-only shift scenario does not apply to this "
                      "household")["reason"])


def _no_ev_extended(doc):
    """data/extended_results.json's two EV sections, as extended_findings.py
    writes them for the same household. gas_decomposition is NOT touched: it
    is governed by household.has_gas, a different flag and a different
    fixture."""
    for section in ("electrification_dividend", "supercharge_delta"):
        assert section in doc, (
            f"extended_results.json no longer carries {section} -- this fixture "
            "describes a document no generator writes")
        doc[section] = _no_ev_section_stub()


def _no_ev_quiet_night(doc):
    """data/quiet_night_floor.json's EV-absence census as quiet_night_floor.py
    writes it for that same household.

    The census classifies each night with behavior_rebuild.detect_sessions(),
    which returns NO sessions at all when household.has_ev is false, so every
    eligible night comes back EV-free: `n` == `n_eligible_nights` in every
    window. That is the whole of the edit -- nothing else in the artifact is
    EV-derived.

    THIS IS THE STATE THAT MAKES THE FLIP, and it is why the fixture cannot
    stop at the four artifacts above. Read through S2_VERDICT's three-state
    gate, a full house of absences is not "not determined": absent >= charging
    selects the state written for the OPPOSITE reading, and the section
    published "while the EV does not usually charge overnight" -- a habit
    claim about a car the intake says is not there. An EV household's census
    (fewer absences than charging nights) never reaches that branch, so no
    fixture built on this household's own counts could see it.

    median_kw/p10_kw are left alone. They are the median of the EV-free
    nights' floors, and on this household that subset is 42 of 365 nights
    while on a no-EV one it would be all 365 -- a figure this fixture has no
    way to compute and no business inventing (CLAUDE.md section 0). Nothing
    reads them through the EV branch; SEC9_TEASER reads night_floor.median_kw,
    a different field, which is EV-free for every household."""
    census = doc["night_floor"]["issue_114_investigation"]["ev_absence_by_window"]
    assert census, ("quiet_night_floor.json no longer carries an ev_absence_by_window "
                    "census, so this fixture describes a document no generator writes")
    for label, entry in census.items():
        eligible = entry["n_eligible_nights"]
        assert entry["n"] < eligible, (
            f"the committed census already counts every {label} night EV-free "
            f"({entry['n']}/{eligible}); this edit would be a no-op and the no-EV "
            "fixture would be indistinguishable from the EV household's")
        entry["n"] = eligible


def _no_ev_household():
    """The consistent set: behavior_rebuild.json, package_results.json,
    carbon_fullyear_results.json, extended_results.json and
    quiet_night_floor.json as ONE no-EV run writes them.

    All five, because a household is not a document -- it is every artifact
    the flag reaches, and a fixture that stops early does not test a narrower
    household, it tests one that cannot exist. Two report-aborting defects on
    this branch survived a 165-case suite for exactly that reason, and a third
    (S2_VERDICT's overnight-charging clause) survived the four-artifact
    version of this fixture -- see _no_ev_quiet_night."""
    scen = rt._json("behavior_rebuild.json")["scenarios"]
    return _stub_for_many({
        "behavior_rebuild.json": _no_ev_behavior,
        "package_results.json": _no_ev_package(scen["c"], scen["d"]),
        "carbon_fullyear_results.json": _no_ev_carbon,
        "extended_results.json": _no_ev_extended,
        "quiet_night_floor.json": _no_ev_quiet_night})


@case
def case_a_household_whose_artifacts_read_the_other_way_still_gets_its_sentences():
    """ISSUE #132. Round one's defect, in the shape issue #132's own tokens
    could take: four of them BRANCH on what the artifacts say, and this
    household's artifacts only ever select one branch each. An array that
    gained instead of declining, a cadence model whose soiling scenarios pick
    different months, a spread trend that survives its structural-break check,
    a battery re-run that produces a real payback -- every one of those is an
    ordinary household with an ordinary sentence to print, and none of them may
    reach a refusal or a branch written for the opposite reading.

    The committed artifacts are never edited; each stub is a deep copy."""
    got = {}

    # 1. An array that GAINED, and one whose three estimators disagree on the
    #    direction -- neither is a refusal, and neither may print "decline".
    def gained(doc):
        d = doc["degradation"]
        d["ols_pct_per_yr"], d["cagr_pct_per_yr"], d["theil_sen_pct_per_yr"] = 0.4, 1.1, 0.9

    def split(doc):
        d = doc["degradation"]
        d["ols_pct_per_yr"], d["cagr_pct_per_yr"], d["theil_sen_pct_per_yr"] = -0.6, 0.2, -0.1

    for label, edit in (("gain", gained), ("split", split)):
        with _patched(rt, "_json", _stub_for("gross_import_decomposition.json", edit)):
            got[f"degradation_{label}"] = _renders("DEGRADATION_NAIVE_RANGE")
    assert "of gain" in got["degradation_gain"], got["degradation_gain"]
    assert "decline" not in got["degradation_gain"], got["degradation_gain"]
    assert "disagree on the direction" in got["degradation_split"], got["degradation_split"]

    # 2. Soiling scenarios that pick DIFFERENT best months: every scenario's
    #    own answer is named, rather than one of them being published as "the"
    #    month or the disagreement being refused.
    def months_differ(doc):
        for i, key in enumerate(sorted(doc["cleaning"], key=float)):
            doc["cleaning"][key]["best1"] = ["Jun 2", "Jul 17", "Aug 30"][i % 3]

    with _patched(rt, "_json", _stub_for("extra_results.json", months_differ)):
        got["cleaning"] = _renders("CLEANING_BEST_MONTH")
    for month in ("Jun 2", "Jul 17", "Aug 30"):
        assert month in got["cleaning"], got["cleaning"]
    assert "every soiling rate" not in got["cleaning"], got["cleaning"]

    # 3. A spread trend that SURVIVES, and a battery re-run that produces a
    #    real payback and NPV on it.
    def determined(doc):
        for season in ("summer", "winter"):
            doc["delivery_spread"][season]["verdict"] = "escalating"
            doc["delivery_spread"][season]["reportable"] = True
        for season, npv in (("summer", 4200), ("winter", -900)):
            doc["battery"]["per_period"][season] = {
                "verdict": "escalating", "payback_yr": 5.4, "npv10": npv}

    with _patched(rt, "_json", _stub_for("tou_spread.json", determined)):
        got["summer"] = _renders("SPREAD_TREND_SUMMER")
        got["battery"] = _renders("BATTERY_ON_MEASURED_SPREAD")
    assert "%/yr" in got["summer"] and "95% CI" in got["summer"], got["summer"]
    assert "not determined" not in got["summer"], got["summer"]
    assert "5.4 yr payback" in got["battery"], got["battery"]
    assert "+$4,200" in got["battery"] and "-$900" in got["battery"], got["battery"]

    # 4. And an instrument reading ABOVE the inverter nameplate says so rather
    #    than printing a negative headroom as though it were clearance.
    def over_nameplate(doc):
        doc["gross_reconstruction"]["pv_ac_ceiling"]["corroboration"][0][
            "below_nameplate_by_kw"] = -0.4

    with _patched(rt, "_json", _stub_for("service_headroom.json", over_nameplate)):
        got["headroom"] = _renders("PV_PEAK_HEADROOM")
    assert "ABOVE the inverter nameplate" in got["headroom"], got["headroom"]

    for name, text in got.items():
        assert text.strip(), f"{name} rendered blank"
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"{name} rendered {label}: {text}"
    return ("the alternate reading of every branching issue-#132 token renders a real "
            f"sentence rather than a refusal: {sorted(got)}")


@case
def case_a_legitimate_null_or_not_applicable_emission_renders_rather_than_aborts():
    """ISSUE #132, ADVERSARIAL PASS 2. _figures / _amounts / _quantities refuse
    on sight, which is what makes them worth having and what makes them the
    wrong thing to point at a field its generator deliberately writes as null,
    empty or "does not apply".

    The instance: tou_spread._payback returns payback_yr None when a narrowing
    spread leaves the battery unrecovered inside its horizon, and the generator
    says in a comment above the emission that this is "a real result, not an
    error ... the one verdict it most needs to be able to report".
    BATTERY_ON_MEASURED_SPREAD refused it, so the household whose battery does
    not pay back got NO REPORT AT ALL -- a determinate answer treated as
    NOT_DETERMINED, the mirror image of the class this module already guards.

    Each stub below is one legitimate emission traced to the generator that
    writes it, and the assertion is the same every time: it RENDERS, it says
    what happened, and nothing about it is malformed."""
    got = {}

    # 1. The named defect: a REPORTABLE per-season run whose battery never
    #    repays inside the horizon. NPV is present and meaningful here.
    def unpaid(doc):
        doc["battery"]["per_period"]["summer"] = {
            "verdict": "widening", "payback_yr": None, "npv10": -3100}

    with _patched(rt, "_json", _stub_for("tou_spread.json", unpaid)):
        got["battery_never_repays"] = _renders("BATTERY_ON_MEASURED_SPREAD")
    assert "does not repay within the model horizon" in got["battery_never_repays"], \
        got["battery_never_repays"]
    assert "-$3,100" in got["battery_never_repays"], got["battery_never_repays"]

    # 2. tou_spread._fit_spread's two degenerate exits: a short corpus returns
    #    {"n", "verdict"} with the reason INSIDE the verdict string, and no
    #    escalation/span fields at all.
    def short_corpus(doc):
        doc["delivery_spread"]["summer"] = {
            "n": 2, "verdict": "not determined -- fewer than 3 paired observations"}
        doc["delivery_spread"]["winter"] = {
            "n": 4, "n_independent": 1,
            "verdict": "not determined -- fit undefined on 1 independent level(s)"}

    with _patched(rt, "_json", _stub_for("tou_spread.json", short_corpus)):
        got["short_summer"] = _renders("SPREAD_TREND_SUMMER")
        got["short_winter"] = _renders("SPREAD_TREND_WINTER")
    assert "fewer than 3 paired observations" in got["short_summer"], got["short_summer"]
    assert "fit undefined" in got["short_winter"], got["short_winter"]

    # 3. A reportable fit whose r2 is null (tou_spread writes the null itself)
    #    and whose reason list came back empty.
    def null_r2(doc):
        s = doc["delivery_spread"]["summer"]
        s["verdict"], s["r2"] = "widening", None
        doc["delivery_spread"]["winter"]["not_determined_because"] = []

    with _patched(rt, "_json", _stub_for("tou_spread.json", null_r2)):
        got["null_r2"] = _renders("SPREAD_TREND_SUMMER")
        got["no_reason"] = _renders("SPREAD_TREND_WINTER")
    assert "r²" not in got["null_r2"], got["null_r2"]
    assert "%/yr" in got["null_r2"], got["null_r2"]
    assert got["no_reason"].startswith("not determined"), got["no_reason"]

    # 4. THE WHOLE no-EV household, not one stubbed block of it (issue #147):
    #    behavior_rebuild.py's four not-applicable stubs, the battery block
    #    that swaps its post-EV-shift pair for a fifth, and the LOW package
    #    that prices scenario c instead of scenario a. See _no_ev_behavior for
    #    why the narrower fixture hid seven report-aborting tokens.
    scen = rt._json("behavior_rebuild.json")["scenarios"]
    low_yr = round(scen["c"]["saved"])

    with _patched(rt, "_json", _no_ev_household()):
        for token in ("EV_SESSION_COUNT", "EV_ANNUAL_KWH", "EV_AVG_SESSION_KWH",
                      "EV_WINDOW_DECOMPOSITION", "EV_SOP_COMPLIANCE_PCT",
                      "EV_DETECTION_BASIS"):
            got[f"no_ev_{token}"] = _renders(token)
            assert "not applicable" in got[f"no_ev_{token}"], got[f"no_ev_{token}"]
            assert "has_ev" in got[f"no_ev_{token}"], got[f"no_ev_{token}"]

        # 4a. The two EV shift-scenario figures. Both were leaf data_json
        #     tokens digging scenarios.<k>.saved out of a stub that has no
        #     such field; both now render the artifact's OWN stated reason.
        for token in ("EV_FIX_SAVINGS_100", "EV_FIX_SAVINGS_80"):
            value = got[f"no_ev_{token}"] = _renders(token)
            assert "not applicable" in value, value
            assert "has_ev" in value, value

        # 4b. Section 9's <summary> teaser is that section's ONLY permitted
        #     one-line conclusion (CLAUDE.md section 10 calls a section with
        #     none a bug), so it may not degrade into a bare disclaimer: the
        #     always-on overnight floor is measured for every household, off
        #     an artifact that knows nothing about EVs, and it has to carry
        #     the sentence on its own. Both halves of that clause are checked
        #     -- the energy and the money -- because a teaser reduced to
        #     "not applicable" would still be non-blank.
        teaser = got["no_ev_SEC9_TEASER"] = _renders("SEC9_TEASER")
        assert re.search(r"[\d,.]+\s*kWh", teaser), (
            f"SEC9_TEASER lost the overnight-floor energy figure, so section 9 has "
            f"no one-line conclusion left: {teaser!r}")
        assert re.search(r"\$[\d,]+", teaser), (
            f"SEC9_TEASER lost the overnight-floor cost figure: {teaser!r}")
        assert "not applicable" not in teaser, (
            f"SEC9_TEASER degraded section 9's conclusion into not-applicable "
            f"boilerplate instead of dropping the EV clause: {teaser!r}")

        # 4c. HOUSE_KWH_DAY lands in a <thead> cell, where prose has nowhere
        #     to go, so the no-EV answer is the same arithmetic with the
        #     subtraction at zero -- a NUMBER, never a sentence.
        kwh_day = got["no_ev_HOUSE_KWH_DAY"] = _renders("HOUSE_KWH_DAY")
        assert re.fullmatch(r"[\d,]+", kwh_day), (
            f"HOUSE_KWH_DAY is a table-header figure and must render a number on a "
            f"household with no EV, not prose: {kwh_day!r}")
        assert "not applicable" not in kwh_day, kwh_day

        # 4d. ...and the words BESIDE that number are the other half of the
        #     same truth: "House minus EV" over a house with no EV is a false
        #     claim standing on a correct figure, and no value the token
        #     rendered could fix it.
        header = got["no_ev_HOUSE_LOAD_COLUMN_HEADER"] = \
            _renders("HOUSE_LOAD_COLUMN_HEADER")
        assert "minus EV" not in header, (
            f"HOUSE_LOAD_COLUMN_HEADER claims an EV subtraction on a household with "
            f"no EV: {header!r}")
        assert re.search(r"~[\d,]+ kWh/d", header), header
        assert kwh_day in header, (f"the heading dropped its own figure: {header!r}")

        # 4e. The three verdict sentences that reach _free_fix_saving. Each
        #     must state the move this household can actually make -- naming
        #     a charger is an instruction that cannot be carried out, and in
        #     the Monday appendix it is the ONE instruction on the page --
        #     and each stays inside CLAUDE.md section 10's density cap, which
        #     governs every branch and not just the one index.html carries.
        #     Sections 0 and 7 name the FIX ("the free load-shift fix"); the
        #     Monday appendix names what to MOVE, because it is an
        #     instruction rather than a valuation. Both come out of
        #     _free_fix_move off the scenario packages.LOW prices, so each is
        #     pinned to the phrase its own sentence is built from.
        # This stub patches ARTIFACTS, not the intake, so a token that also
        # reads private/household.yaml still reaches the real file. S0_VERDICT
        # does, through _plan_ranking's household.plan, and declares it in its
        # own sources -- so on a checkout without the private archive it
        # refuses for a reason that has nothing to do with EV applicability.
        # Gate by the DECLARED source rather than by name, so a token that
        # starts or stops reading the intake is handled without editing this
        # list.
        _here = _runnable_here(("S0_VERDICT", "S7_VERDICT", "S15_VERDICT"))
        for token, names_the_move in (("S0_VERDICT", "free load-shift fix"),
                                      ("S7_VERDICT", "free load-shift fix"),
                                      ("S15_VERDICT", "flexible house load")):
            if token not in _here:
                continue
            value = got[f"no_ev_{token}"] = _renders(token)
            low_value = value.lower()
            assert "charger" not in low_value and "reprogram" not in low_value, (
                f"{token} tells a household with no EV to reprogram a charger: "
                f"{value!r}")
            assert "EV-charging fix" not in value, value
            assert names_the_move in value, (
                f"{token} does not name the move packages.LOW actually prices "
                f"(scenario c, a pure house-load shift): {value!r}")
            _assert_within_density_cap(token, value, "a household with no EV")
        for token in ("S0_VERDICT", "S7_VERDICT"):
            if f"no_ev_{token}" not in got:      # held out for want of the intake
                continue
            assert f"${low_yr:,}/yr" in got[f"no_ev_{token}"], (
                f"{token} does not quote packages.LOW.savings_yr (${low_yr:,}/yr): "
                f"{got[f'no_ev_{token}']!r}")

    # 5. The two gas artifacts collapsing to {"applicable": False} for a
    #    household with no gas service.
    def no_gas(doc):
        doc.clear()
        doc.update({"applicable": False, "reason": "household.has_gas is false"})

    for artifact, tokens in (
            ("all_electric_endgame.json",
             ("HPWH_INSTALL_COST", "HPWH_PAYBACK", "HPWH_NET_SAVINGS",
              "HPWH_SHARE_CAVEAT", "HPWH_PAYBACK_SENSITIVITY", "HPWH_SAVINGS_BOUND",
              "ELECTRIFICATION_SEQUENCE", "ELECTRIFICATION_COMBINED_PAYBACK")),
            ("heat_pump_conversion.json",
             ("HEAT_PUMP_INSTALL_COST", "HEAT_PUMP_PAYBACK", "HEAT_PUMP_COST_BASIS",
              "ELECTRIFICATION_INCENTIVES"))):
        with _patched(rt, "_json", _stub_for(artifact, no_gas)):
            for token in tokens:
                got[f"no_gas_{token}"] = _renders(token)
                assert "not applicable" in got[f"no_gas_{token}"], got[f"no_gas_{token}"]
                assert "has_gas" in got[f"no_gas_{token}"], got[f"no_gas_{token}"]

    for name, text in got.items():
        assert text.strip(), f"{name} rendered blank"
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"{name} rendered {label}: {text}"
    return (f"{len(got)} legitimate null / degenerate / not-applicable emission(s) render "
            "their own answer instead of aborting the report")


@case
def case_the_free_fix_guard_refuses_a_package_that_cannot_name_its_own_scenario():
    """ISSUE #147. _free_fix_saving stopped RE-DERIVING which behavior_rebuild
    scenario packages.LOW prices and started READING it, off
    data/package_results.json:packages.LOW.free_fix_scenario. That field is a
    contract between two generators, and the three ways it can fail to hold
    all have to fail closed:

      (a) the field is absent -- an artifact written before it existed. The
          obvious fallback is "a", and on a household with no EV that
          fallback points the whole-dollar rounding guard at a stub with no
          `saved` field at all: the exact wrong-scenario check the field was
          added to make impossible.
      (b) it names something that is not one of the four shift scenarios, so
          nothing here knows what the move shifts or what to call it.
      (c) it names a scenario behavior_rebuild.json publishes as NOT
          APPLICABLE. This is not a household with no EV -- it is two
          committed artifacts contradicting each other about which rung the
          LOW package IS, and either figure published off it would be a
          number with no evidence behind it.

    All three refusals were new and untested; (c) is the one a real mixed
    fixture hits by accident. Every one is driven through all THREE sentences
    that reach _free_fix_saving, because each of them alone blocks the whole
    report, and a guard that fired in one of the three would still let the
    other two publish the contradiction."""
    tokens = _runnable_here(("S0_VERDICT", "S7_VERDICT", "S15_VERDICT"))
    said = {}

    # (a) The artifact that predates the field.
    def no_field(doc):
        doc["packages"]["LOW"].pop("free_fix_scenario", None)

    with _patched(rt, "_json", _stub_for("package_results.json", no_field)):
        for token in tokens:
            said[f"absent {token}"] = _refuses(token)
            assert "analysis/package_results.py" in said[f"absent {token}"], (
                f"the refusal does not tell the reader which generator writes the "
                f"missing field: {said[f'absent {token}']!r}")

    # (b) A key that is not one of behavior_rebuild.py's four scenarios. Both
    #     an unknown letter and a plausible-looking non-scenario, so the guard
    #     is a membership test rather than a spelling test.
    for bogus in ("z", "a2"):
        def wrong_key(doc, bogus=bogus):
            doc["packages"]["LOW"]["free_fix_scenario"] = bogus

        with _patched(rt, "_json", _stub_for("package_results.json", wrong_key)):
            for token in tokens:
                message = said[f"bogus {bogus} {token}"] = _refuses(token)
                assert repr(bogus) in message, (
                    f"the refusal does not quote the key it rejected: {message!r}")
                assert "(a, b, c, d)" in message, (
                    f"the refusal does not say what the four scenarios ARE, so the "
                    f"reader cannot tell what a valid value looks like: {message!r}")

    # (c) A package naming a scenario its own source says does not exist here:
    #     behavior_rebuild.json read the no-EV way, package_results.json still
    #     pricing the EV rung. Only this state proves the guard reads the
    #     SCENARIO rather than merely the field.
    def prices_the_ev_rung(doc):
        doc["packages"]["LOW"]["free_fix_scenario"] = "a"

    contradiction = _stub_for_many({
        "behavior_rebuild.json": _no_ev_behavior,
        "package_results.json": prices_the_ev_rung})
    with _patched(rt, "_json", contradiction):
        for token in tokens:
            message = said[f"stubbed scenario {token}"] = _refuses(token)
            assert "not applicable" in message, (
                f"the refusal does not say that the named scenario is one the "
                f"behavior artifact publishes as not applicable: {message!r}")
            assert "has_ev" in message, (
                f"the refusal does not carry behavior_rebuild.py's own reason: "
                f"{message!r}")
            assert "'a'" in message, (
                f"the refusal does not name the scenario the package priced: "
                f"{message!r}")

    # Every refusal names the artifact whose field is wrong, so the reader is
    # sent at a generator rather than at the line that raised.
    for label, message in said.items():
        # A REFUSAL, NOT A CRASH CONVERTED INTO ONE. resolve_token wraps any
        # stray KeyError or TypeError in a SystemExit as well, so "failed to
        # resolve token S0_VERDICT (derived): KeyError: 'z'" would satisfy a
        # test that only checked that something was raised -- while telling
        # the reader nothing about which artifact is wrong or which generator
        # writes it. Each of these three states has a sentence of its own.
        assert "failed to resolve token" not in message, (
            f"{label}: the contradiction reached the reader as an unhandled "
            f"exception rather than as a named refusal: {message!r}")
        assert "package_results" in message, f"{label}: {message!r}"
        assert "free_fix_scenario" in message, f"{label}: {message!r}"
    return (f"all {len(said)} free-fix contract violations fail closed across "
            "S0/S7/S15 -- a missing field, a key that is not a scenario, and a "
            "package pricing a scenario its own source publishes as not applicable")


# The failures a COMPLETE no-EV artifact set is allowed to have, because they
# are not the household's doing. Both reproduce identically on a household
# that HAS an EV and on main: they read
# battery_dispatch_policies.json:stored_kwh_cost.solar_surplus.by_period, and
# a solar profile that puts no surplus in the midday TOU bucket leaves that
# bucket absent. An empty bucket, not an absent car.
#
# The assertion below is a SUBSET test, not equality. A token that starts
# passing must not fail this case -- an allow-list that has to be emptied by
# hand is one a future reader edits to make the suite green -- but a NEW
# failure must, and the message has to name it.
_NO_EV_ALLOWED_TOKEN_FAILURES = frozenset({
    "STORED_KWH_COST_SOLAR_MIDDAY",
    "STORED_KWH_MIDDAY_SHARE",
})

# The five artifacts one no-EV run rewrites, and a token that reads each --
# the witness that proves the fixture is not silently a no-op. There is no
# witness for extended_results.json BECAUSE NO TOKEN READS IT TODAY: it is in
# the fixture so the first one that does is swept from its first commit
# rather than after it aborts someone's report, which is the whole lesson of
# carbon_fullyear_results.json below.
#
# quiet_night_floor.json has none either, for the OPPOSITE reason, and it is
# the fix working rather than a gap: the only token that reads its EV-absence
# census is S2_VERDICT, and the whole point of issue #147's fourth path there
# is that a household with no EV never reaches the census at all. A witness
# would have to be a token whose value MOVES with the census on a no-EV
# household, and after the fix there is deliberately no such token. The stub
# still belongs in the set -- _no_ev_quiet_night is what makes the census say
# what a real no-EV run's says, and the dedicated case below drives S2_VERDICT
# through it directly.
_NO_EV_ARTIFACT_WITNESS = {
    "behavior_rebuild.json": "EV_SESSION_COUNT",
    # S7_VERDICT, not S0_VERDICT: both read packages.LOW, but S0 also reads
    # household.plan through _plan_ranking, so it is held out on a checkout
    # with no private archive and could not witness anything there -- which is
    # exactly where this case has to keep working.
    "package_results.json": "S7_VERDICT",
    "carbon_fullyear_results.json": "SEC13_TEASER",
    "extended_results.json": None,
    "quiet_night_floor.json": None,
}


@case
def case_every_token_in_the_report_resolves_on_a_complete_no_ev_artifact_set():
    """ISSUE #147, THE STRUCTURAL GUARD. Two regressions on this branch had
    one cause, and it was not a hard token: it was a FIXTURE that stopped at
    two artifacts.

      1. SEC13_TEASER subscripted carbon_fullyear_results.json's
         footprints_kg_co2_per_yr.detail.midday_cleaner_than_overnight_by,
         which is a not-applicable stub when household.has_ev is false. It
         raised SystemExit, and generate_report.run() blocks the write on any
         non-gap token failure, so the household got NO REPORT AT ALL.
      2. The same shape, one artifact over, in extended_findings.py.

    Neither was caught by the 165 cases here. Every no-EV case in this file
    patched behavior_rebuild.json and package_results.json and resolved a
    NAMED handful of tokens against them; carbon_fullyear_results.json was
    never in the fixture, so SEC13_TEASER was never exercised on a household
    with no EV, and no list of token names could have included a token nobody
    had thought about yet.

    So this case names no tokens. It resolves EVERY token in TOKENS whose
    kind is not "gap" -- 224 of them today, and whatever is added next year
    without editing this case -- against a COMPLETE and CONSISTENT no-EV
    artifact set, and asserts the set of failures is a subset of the two
    pre-existing ones that reproduce identically on an EV household. A new
    failure here is not a cosmetic defect: it is a household that gets no
    report.

    The positive half matters as much. A token can also survive by giving up
    -- rendering a bare disclaimer where a conclusion belongs -- and that
    passes any test that only asks whether resolution raised. SEC13_TEASER
    and SEC9_TEASER are <summary> teasers, which CLAUDE.md section 10 makes
    the ONLY permitted one-line conclusion for their sections, so both are
    checked for a real measured figure and against not-applicable
    boilerplate."""
    stub = _no_ev_household()

    # 0. THE FIXTURE ITSELF, BEFORE ANYTHING IS RESOLVED THROUGH IT. Each of
    #    the four artifacts must actually come back CHANGED, or a mis-keyed
    #    edit map would leave this case sweeping the EV household under a
    #    no-EV name and passing for the wrong reason.
    for artifact in _NO_EV_ARTIFACT_WITNESS:
        assert stub(artifact) != rt._json(artifact), (
            f"the no-EV fixture returns data/{artifact} unchanged, so this case is "
            "resolving the EV household's artifacts under a no-EV label")

    # This fixture patches ARTIFACTS, not the intake, so a token that reads
    # private/household.yaml still reaches the real file. On a checkout that
    # has one (this one) they resolve and are swept like everything else; in
    # CI, which has no private archive, they would refuse for a reason that
    # has nothing to do with EV applicability and would read here as "a no-EV
    # household gets no report". Gate ONLY those, by the source they declare,
    # so the rest of the sweep keeps running in CI -- which is where this case
    # earns its keep, since the regressions it exists to catch all reached
    # main through CI-visible paths.
    have_intake = rt.hh.PATH.is_file()
    swept, gaps, no_intake = {}, set(), set()
    failures, values = {}, {}
    with _patched(rt, "_json", stub):
        for name, spec in rt.TOKENS.items():
            if spec.get("kind") == "gap":
                gaps.add(name)
                continue
            swept[name] = spec
            try:
                values[name] = rt.resolve_token(name)
            except SystemExit as e:
                # TELL THE TWO REFUSALS APART. A token can refuse here for a
                # reason that is nothing to do with EV applicability: on a
                # checkout with no private archive, anything reaching
                # private/household.yaml hits household.py's own fail-closed
                # message. Counting those as "a no-EV household gets no
                # report" made this case red in CI and green here, which is
                # the wrong way round for a guard.
                #
                # Detected by the intake's OWN message rather than by a list
                # of token names or declared sources: `sources` misses the
                # kind="household_yaml" tokens and every derived one that
                # reaches the intake indirectly (S4_VERDICT_SHORT and the
                # BEST_PLAN_* family go through _plan_ranking), and a list
                # would need editing every time a token starts or stops
                # reading it.
                if not have_intake and _INTAKE_PATH_IN_MESSAGE in str(e):
                    del swept[name]
                    no_intake.add(name)
                    continue
                failures[name] = str(e)

    # 1. NOTHING IS SMUGGLED OUT OF THE SWEEP. The only tokens skipped are the
    #    declared gaps, which resolve for nobody by design; declaring a token
    #    a gap to quiet this case would move it into KNOWN_GAPS, where
    #    case_known_gaps_are_small_and_each_fails_closed_by_name is waiting.
    assert gaps == set(rt.KNOWN_GAPS), (
        f"the sweep skipped {sorted(gaps - set(rt.KNOWN_GAPS))} beyond KNOWN_GAPS")
    # EXACT ACCOUNTING, not a magic floor. Every token is swept, declared a
    # gap, or held out for want of the intake -- nothing may simply vanish.
    # A floor was the first draft and it was wrong twice over: it passed a
    # sweep that had quietly lost tokens as long as enough remained, and it
    # failed CI for the innocent reason that a checkout with no private
    # archive legitimately holds ~40 back. Conservation catches the first and
    # is indifferent to the second.
    assert len(swept) + len(gaps) + len(no_intake) == len(rt.TOKENS), (
        f"{len(rt.TOKENS)} tokens went in but {len(swept)} were swept, "
        f"{len(gaps)} are gaps and {len(no_intake)} were held out -- "
        "the sweep lost some, so the assertions below cover less than they claim")
    assert swept, "no token was swept at all -- TOKENS did not load"

    # 2. THE WHOLE POINT.
    new = sorted(set(failures) - _NO_EV_ALLOWED_TOKEN_FAILURES)
    assert not new, (
        "a household whose intake says household.has_ev is false gets NO REPORT AT "
        "ALL: generate_report.run() blocks the write on any non-gap token failure, "
        f"and {len(new)} token(s) fail to resolve against a complete, consistent "
        "no-EV artifact set -- "
        + "; ".join(f"{n}: {failures[n]}" for n in new)
        + ". Fix the token to read the not-applicable stub its artifact publishes "
          "(see _applicability), the way SEC13_TEASER falls back to the grid-side "
          "window means. Do NOT add it to _NO_EV_ALLOWED_TOKEN_FAILURES: that list "
          "is for failures that reproduce identically on a household WITH an EV.")

    # 3. THE FIXTURE REACHED THE TOKENS. Each witness resolves, and resolves
    #    DIFFERENTLY from the EV household -- a stub that edited a key nothing
    #    reads would satisfy step 0 and still leave the sweep blind.
    for artifact, witness in _NO_EV_ARTIFACT_WITNESS.items():
        if witness is None:
            continue
        assert witness in values, f"{witness} did not resolve: {failures.get(witness)}"
        assert values[witness] != rt.resolve_token(witness), (
            f"{witness} renders the same value with and without the no-EV fixture, "
            f"so data/{artifact} is in the fixture but reaches no token")

    # 4. SECTION 13'S TEASER STATES A MEASURED FINDING, NOT A DISCLAIMER.
    #    intensity_kg_per_mwh.window_means_annual is a CAISO measurement --
    #    what a MWh drawn overnight costs against one drawn at midday --
    #    and it is identical on both households, which is exactly why the
    #    teaser may state it when the EV-scaled swing above it cannot be.
    windows = rt._json("carbon_fullyear_results.json")[
        "intensity_kg_per_mwh"]["window_means_annual"]
    gap_kg_mwh = (windows["sop_overnight_00_06"] - windows["solar_midday_10_14"])
    sec13 = values["SEC13_TEASER"]
    assert f"{gap_kg_mwh:,.0f} kg CO₂/MWh" in sec13, (
        f"SEC13_TEASER does not state the measured overnight-vs-midday grid gap "
        f"({gap_kg_mwh:,.0f} kg CO₂/MWh, from carbon_fullyear_results.json:"
        f"intensity_kg_per_mwh.window_means_annual): {sec13!r}")
    assert re.search(r"NEM 2\.0 worth \$[\d,]+–[\d,]+/yr", sec13), (
        f"SEC13_TEASER lost the NEM clause, which never depended on an EV: {sec13!r}")
    assert "not applicable" not in sec13 and "does not apply" not in sec13, (
        f"SEC13_TEASER degraded section 13's ONE permitted conclusion into a "
        f"not-applicable disclaimer: {sec13!r}")
    # THE CONTROL: the same token on the same artifacts WITH the EV states the
    # EV-scaled swing instead, in kg CO2/yr. Without this, "states the grid
    # gap" would pass on a token that had simply stopped reading the swing.
    with_ev = rt.resolve_token("SEC13_TEASER")
    assert "kg CO₂/yr" in with_ev and "kg CO₂/MWh" not in with_ev, (
        f"the EV household's SEC13_TEASER no longer states the EV-scaled carbon "
        f"swing, so the no-EV branch above is not a branch: {with_ev!r}")

    # 5. SECTION 9'S TEASER, the same rule one section up: the always-on
    #    overnight floor is measured for every household, off an artifact that
    #    knows nothing about EVs, so the teaser carries it when the charging
    #    clause cannot.
    sec9 = values["SEC9_TEASER"]
    assert re.search(r"[\d,.]+\s*kWh", sec9) and re.search(r"\$[\d,]+", sec9), (
        f"SEC9_TEASER lost the overnight-floor figures, so section 9 has no "
        f"one-line conclusion left: {sec9!r}")
    assert "not applicable" not in sec9, (
        f"SEC9_TEASER degraded section 9's conclusion into not-applicable "
        f"boilerplate: {sec9!r}")

    # 6. Nothing anywhere in the swept set is blank or malformed. A token that
    #    renders "$None/yr" or "nan" has not failed closed -- it has published
    #    a defect, which is worse.
    for name, text in values.items():
        assert text.strip(), f"{name} rendered blank on a household with no EV"
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), (
                f"{name} rendered {label} on a household with no EV: {text!r}")

    allowed = sorted(set(failures) & _NO_EV_ALLOWED_TOKEN_FAILURES)
    return (f"all {len(swept)} non-gap token(s) resolve against a complete no-EV "
            f"artifact set (behavior_rebuild, package_results, "
            f"carbon_fullyear_results, extended_results, quiet_night_floor), "
            f"none blank or malformed"
            + (f"; {len(allowed)} pre-existing non-EV failure(s) allowed: {allowed}"
               if allowed else "; zero failures, allowed or otherwise")
            + f"; section 13's teaser states the measured grid gap "
              f"({gap_kg_mwh:,.0f} kg CO₂/MWh) and section 9's the overnight floor"
            # NO SILENT CAPS: a sweep that quietly covered less than it claims
            # reads as "everything passes" when it is really "everything I
            # still looked at passes", so say what was held out and why.
            + (f"; {len(no_intake)} token(s) held out for needing "
               f"private/household.yaml, which this checkout lacks: "
               f"{sorted(no_intake)}" if no_intake else ""))


@case
def case_section_7s_switch_clause_names_the_move_this_household_can_make():
    """ISSUE #147, CODEX REVIEW. S7_PLAN_FOOTING's priced beaten branch named
    the MID package's own contents in FIXED TEXT -- "the MID package (the EV
    fix plus one battery)" -- so a household whose intake says
    household.has_ev is false read a live section 7 sentence about a car it
    does not own, in the one clause telling it what switching plans is worth.

    THE NAME IS NOT RE-DERIVED HERE EITHER. The assertion is that the clause
    carries whatever {{FREE_FIX_SHORT_NAME}} publishes in the SAME state --
    the token the Monday appendix's heading is built from -- so the two
    sentences cannot come apart the way this one came apart from section 7's
    own verdict. A second reading of packages.LOW.free_fix_scenario written
    here would be free to disagree with both.

    Ungated: _stub_plan supplies the two intake answers the plan ranking
    reads, so the beaten branch is driven on a checkout with no private
    archive, which is where the report is generated in CI."""
    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    mid_plans = set(
        rt._json("battery_plan_matrix.json")["mid_package_on_plans"]["plans"])
    rivals = [r for r in priced if r["plan"] != cheapest and r["plan"] in mid_plans]
    assert rivals, (
        f"no plan data/plan_results.csv prices for {provider!r} is also priced by "
        f"battery_plan_matrix.json's mid_package_on_plans ({sorted(mid_plans)}); "
        "this case needs one, because the clause it is about only renders when the "
        "winner is priced there")
    rival = min(rivals, key=lambda r: float(r["total"]))["plan"]

    seen = {}
    for label, household in (("with an EV", None),
                             ("with no EV", _no_ev_household())):
        with contextlib.ExitStack() as stack:
            if household is not None:
                stack.enter_context(_patched(rt, "_json", household))
            stack.enter_context(_stub_plan(cheapest, provider))
            stack.enter_context(
                _plan_repriced(provider, {cheapest: own, rival: own - 1}))
            footing = rt.resolve_token("S7_PLAN_FOOTING")
            short = rt.resolve_token("FREE_FIX_SHORT_NAME")
        # The priced branch really was driven -- the two stated-why-not
        # branches name no package contents at all and would pass every
        # assertion below vacuously.
        assert "$" in footing and "Re-billed end-to-end on" in footing, (
            f"a household {label} did not reach section 7's PRICED switch clause, so "
            f"this case checked nothing: {footing!r}")
        assert f"the {short} fix plus one battery" in footing, (
            f"section 7's switch clause does not name the move "
            f"{{{{FREE_FIX_SHORT_NAME}}}} publishes ({short!r}) for a household "
            f"{label}: {footing!r}")
        seen[label] = (short, footing)

    ev_short, ev_footing = seen["with an EV"]
    no_ev_short, no_ev_footing = seen["with no EV"]
    # THE POSITIVE CONTROL. This household has an EV, so the published clause
    # must be unchanged to the character -- and if it were not, "no EV fix on
    # a no-EV household" below would be passing on a clause that had simply
    # stopped naming the package's contents at all.
    assert ev_short == "EV" and "the EV fix plus one battery" in ev_footing, (
        f"the EV household's switch clause no longer reads 'the EV fix plus one "
        f"battery' ({ev_short!r}): {ev_footing!r}")
    assert no_ev_short != ev_short, (
        f"the free fix has the same short name on both households ({no_ev_short!r}), "
        "so this case cannot tell the two clauses apart")
    assert "the EV fix" not in no_ev_footing and "EV fix" not in no_ev_footing, (
        f"section 7 tells a household whose intake says household.has_ev is false "
        f"that the MID package contains an EV fix: {no_ev_footing!r}")
    return ("section 7's priced switch clause names the move packages.LOW actually "
            "prices, in the same words FREE_FIX_SHORT_NAME publishes -- "
            + "; ".join(f"{k}: {v[0]!r}" for k, v in sorted(seen.items())))


@case
def case_section_2_asks_about_overnight_charging_only_where_there_is_an_ev():
    """ISSUE #147, CODEX REVIEW. S2_VERDICT closes on a clause about when the
    EV charges, and ALL THREE of its states assert an EV: the supported one
    says it charges overnight, and the state written for the opposite reading
    says it "does not usually charge overnight", which is still a claim about
    a car.

    On a household with no EV the census cannot select anything else.
    quiet_night_floor.py classifies each night with
    behavior_rebuild.detect_sessions(), which finds nothing there, so every
    eligible night is counted EV-free, absent == observed, and the clause
    lands in the confident second sentence. That is DRIVEN below, not
    asserted: the census-only fixture (the EV detection block left in place)
    is rendered first and shown to produce exactly that sentence, so the
    fourth path is measured against the falsehood it replaces rather than
    against an imagined one.

    Ungated for the same reason case_s2_verdict_reports_a_daytime_charger...
    is: _s2_household_inputs supplies the array size and PTO date, so the
    guard runs in CI, where the report is actually generated."""
    census_only = _stub_for("quiet_night_floor.json", _no_ev_quiet_night)
    whole_household = _no_ev_household()
    with _stub_household(_s2_household_inputs()):
        published = rt.resolve_token("S2_VERDICT")
        # 1. THE CONTROL, on this household's own artifacts: the clause is
        #    live, and it is the SUPPORTED state.
        assert "while the EV charges overnight" in published, published

        # 2. THE FALSEHOOD, driven. Census as a no-EV run writes it, EV
        #    detection block left alone -- i.e. the code before the fourth
        #    path existed.
        with _patched(rt, "_json", census_only):
            charging, absent, observed = rt._overnight_ev_night_counts(rt.CTX)
            flipped = rt.resolve_token("S2_VERDICT")
        assert charging == 0 and absent == observed > 0, (
            f"the no-EV census fixture does not put every one of its {observed} "
            f"eligible nights in the EV-free column ({charging} charging, {absent} "
            "absent), so it is not the census a household with no EV produces")
        assert "while the EV does not usually charge overnight" in flipped, (
            f"the three-state gate no longer sends a full house of absences into the "
            f"habit-denial branch, so this case is not driving the defect it names: "
            f"{flipped!r}")

        # 3. THE FIX: the whole no-EV household drops the question instead of
        #    answering it the other way.
        with _patched(rt, "_json", whole_household):
            value = rt.resolve_token("S2_VERDICT")
    assert not re.search(r"\bEVs?\b", value), (
        f"S2_VERDICT still states something about an EV on a household whose intake "
        f"says household.has_ev is false: {value!r}")
    for gone in ("charge overnight", "charges overnight", "charger", "charging"):
        assert gone not in value, (
            f"S2_VERDICT's closing clause still talks about charging on a household "
            f"with no EV ({gone!r}): {value!r}")
    # 4. THE CLAUSE IS DROPPED, NOT MANGLED. The window name is the last thing
    #    in the sentence and the stop follows it directly -- a clause replaced
    #    by an empty string leaves "window ." behind.
    assert value.endswith(f"{rt._cheap_window()} window."), (
        f"S2_VERDICT does not close cleanly on the export-timing clause: {value!r}")
    assert " ." not in value, f"S2_VERDICT left a dangling stop: {value!r}"
    # 5. AND THE REST OF THE SECTION'S CONCLUSION SURVIVES. A token can also
    #    "pass" by giving up on the whole sentence.
    production = rt._annual_production_kwh(rt.CTX)
    assert f"{production:,.0f} kWh" in value and "kWh/kW" in value, (
        f"S2_VERDICT dropped its measured production figures along with the EV "
        f"clause: {value!r}")
    share = rt._midday_export_share(rt.CTX)
    assert f"{round(share * 100)}% of its exports leave in the" in value, (
        f"S2_VERDICT dropped the export-timing conclusion: {value!r}")
    _assert_within_density_cap("S2_VERDICT", value, "a household with no EV")
    with _stub_household(_s2_household_inputs()):
        assert rt.resolve_token("S2_VERDICT") == published, (
            "the substituted no-EV artifacts leaked out of this case")
    return ("S2_VERDICT drops its overnight-charging clause on a household whose "
            "intake says household.has_ev is false, instead of inverting it into "
            f"{'while the EV does not usually charge overnight'!r} the way the "
            f"census alone ({absent}/{observed} nights EV-free) makes it")


# Words that only mean something on a household with a car. A token rendering
# one of these for a household whose intake says household.has_ev is false is
# either asserting an EV or giving an instruction that cannot be carried out.
_EV_WORD_RE = re.compile(r"\bEVs?\b|\bchargers?\b|\bcharging\b|\breprogram\w*")

# The renderings that DO carry an EV word truthfully on such a household, each
# named with the whole phrase it is allowed. Deleted from the value before the
# sweep below looks at what is left, so a token may not smuggle a second,
# unlisted EV claim through on the back of a listed one.
#
# OTHER_MAJOR_LOADS is the finding this list records rather than fixes. It
# renders a COUNT off the intake's `vehicles` list ("2 EVs" here), and on a
# household with no EV that list is [] and it renders "0 EVs" -- a denial, not
# an assertion, but it lands in report-template.html's header meta row as
# "<solar size> kW solar + 0 EVs", which reads as a category nobody has. The
# whole phrase is fixed markup in the template, not in this module, so the fix
# is a template change and not a token change: see the PR notes for issue #147.
_NO_EV_TRUTHFUL_EV_PHRASES = {
    "SEC9_EV_HEADING": ("EV charging — not applicable to this household",),
    "SEC9_TEASER": ("no EV charging to shift here",),
    "OTHER_MAJOR_LOADS": ("0 EVs",),
}


@case
def case_no_token_says_ev_to_a_household_that_has_none():
    """ISSUE #147, THE STRUCTURAL GUARD FOR WORDING. The sweep above proves
    every token RESOLVES for a household with no EV. Both defects Codex found
    after it went green resolved perfectly well -- section 7 priced "the EV
    fix plus one battery" and section 2 closed "while the EV does not usually
    charge overnight" -- so resolution was never the property that mattered
    for them. This case sweeps the same complete no-EV artifact set for what
    the tokens SAY.

    It names no tokens. Every non-gap token is rendered, the tariff names are
    removed (EV-TOU-5 is a plan, not a car), the listed truthful phrases are
    removed, and nothing else may contain an EV word. A token added next year
    that hardcodes "the EV fix" fails here without anyone editing this case.

    THE INTAKE IS PART OF THE HOUSEHOLD. `vehicles` is [] on a household with
    no EV, so it is stubbed to [] here -- the artifacts and the intake have to
    describe the same house, which is the lesson _no_ev_household already
    carries one level down.

    AND SO IS THE PLAN STANDING. A token renders one branch at a time, and
    this household is on the cheapest plan, so a single pass never reaches
    section 7's beaten-branch switch clause at all -- which is exactly where
    Codex found "the MID package (the EV fix plus one battery)". The sweep
    therefore runs TWICE: as published, and with the household repriced just
    below a rival that battery_plan_matrix.json prices, which is the state an
    ordinary household on the wrong tariff generates. Both passes are the same
    household; only which sentence it is shown changes."""
    plan_names = sorted({r["plan"] for r in rt._csv_rows("plan_results.csv")},
                        key=len, reverse=True)
    assert any(_EV_WORD_RE.search(p) for p in plan_names), (
        f"no plan in data/plan_results.csv carries an EV word ({plan_names}); the "
        "tariff-name removal below is doing nothing, so drop it rather than leave a "
        "filter nobody can see the effect of")
    stub = _no_ev_household()
    real_hh_value = rt._hh_value

    def no_vehicles(path):
        return [[]] if path == "vehicles" else real_hh_value(path)

    provider, cheapest, priced = _plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    mid_plans = set(
        rt._json("battery_plan_matrix.json")["mid_package_on_plans"]["plans"])
    rivals = [r for r in priced if r["plan"] != cheapest and r["plan"] in mid_plans]
    assert rivals, (
        f"no plan data/plan_results.csv prices for {provider!r} is also priced by "
        "mid_package_on_plans, so the beaten pass below cannot reach the priced "
        "switch clause")
    rival = min(rivals, key=lambda r: float(r["total"]))["plan"]
    states = {
        "as published": lambda: [],
        "beaten by a priced rival": lambda: [
            _stub_plan(cheapest, provider),
            _plan_repriced(provider, {cheapest: own, rival: own - 1})],
    }

    have_intake = rt.hh.PATH.is_file()
    swept, held_out, offenders = {}, set(), {}
    for state, contexts in states.items():
        with contextlib.ExitStack() as stack:
            stack.enter_context(_patched(rt, "_json", stub))
            stack.enter_context(_patched(rt, "_hh_value", no_vehicles))
            for ctx in contexts():
                stack.enter_context(ctx)
            for name, spec in rt.TOKENS.items():
                if spec.get("kind") == "gap":
                    continue
                where = f"{name} ({state})"
                try:
                    value = rt.resolve_token(name)
                except SystemExit:
                    # A REFUSAL IS THE OTHER CASE'S BUSINESS, not this one's.
                    # case_every_token_in_the_report_resolves_on_a_complete_no_ev
                    # _artifact_set asserts the failure set and tells an
                    # archive-less checkout's refusals apart from real ones;
                    # duplicating that here would make one defect fail two
                    # cases with two different stories. This case is only
                    # about what a token that DOES render says.
                    held_out.add(where)
                    continue
                swept[where] = value
                # THE ARTIFACTS' OWN NOT-APPLICABLE STATEMENTS pass through
                # whole. They name an EV domain in order to say it does not
                # exist here, and they name the flag that settled it -- the
                # opposite of the defect this case is about, and the answer
                # every fix for it renders. Both halves are required: the flag
                # alone could sit inside a sentence still claiming something,
                # so the disclaimer has to be there too.
                if "household.has_ev is false" in value:
                    assert "not applicable" in value or "does not apply" in value, (
                        f"{where} names household.has_ev without saying the domain "
                        f"does not apply, so this exemption is covering a live "
                        f"claim: {value!r}")
                    continue
                residue = value
                for phrase in _NO_EV_TRUTHFUL_EV_PHRASES.get(name, ()):
                    assert phrase in residue, (
                        f"{where} no longer renders the phrase this case exempts "
                        f"({phrase!r}) on a household with no EV, so the exemption "
                        f"is stale and hiding whatever it says now: {residue!r}")
                    residue = residue.replace(phrase, "")
                for plan in plan_names:
                    residue = residue.replace(plan, "")
                hit = _EV_WORD_RE.search(residue)
                if hit:
                    offenders[where] = (hit.group(0), value)

    assert swept, "no token was rendered at all -- TOKENS did not load"
    # THE BEATEN PASS REALLY DID SHOW A DIFFERENT SENTENCE. Without this, a
    # repricing that stopped taking would leave the second pass sweeping the
    # winning state twice and the case would still be green.
    beaten_footing = swept.get("S7_PLAN_FOOTING (beaten by a priced rival)")
    assert beaten_footing and "Re-billed end-to-end on" in beaten_footing, (
        "the beaten pass did not reach section 7's priced switch clause, so half "
        f"this sweep checked the published state twice: {beaten_footing!r}")
    assert not offenders, (
        "a household whose intake says household.has_ev is false is told about an EV "
        f"in {len(offenders)} live token rendering(s): "
        + "; ".join(f"{n} says {w!r} in {v!r}" for n, (w, v) in sorted(offenders.items()))
        + ". Branch the wording on the artifact's own not-applicable stub the way "
          "HOUSE_LOAD_COLUMN_HEADER and _free_fix_move do; do NOT add it to "
          "_NO_EV_TRUTHFUL_EV_PHRASES unless the phrase is true for a house with no car.")
    return (f"none of the {len(swept)} token rendering(s) across "
            f"{len(states)} plan standing(s) says EV, charger, charging or reprogram "
            f"to a household with no EV, beyond the "
            f"{len(_NO_EV_TRUTHFUL_EV_PHRASES)} listed truthful phrase(s) and the "
            f"{len(plan_names)} tariff name(s)"
            + (f"; {len(held_out)} rendering(s) held out" if held_out else ""))


# ===========================================================================
# THE MARKUP AROUND THE TOKENS (ISSUE #147, CODEX ADVERSARIAL REVIEW).
#
# Every case above this point checks a token VALUE. That is exactly half of
# what the reader sees, and the half that was already right: issue #147's
# first pass made seven tokens render truthfully for a household whose intake
# says household.has_ev is false, and the page stayed false anyway, because
# report-template.html went on asserting an EV in the fixed markup AROUND
# them --
#
#   <div class="big">{{EV_FIX_SAVINGS_100}}/yr</div>
#   <div class="lbl">Free win: fully super-off-peak EV charging ...</div>
#   <h3>1 · Reprogram charging (this week, $0)</h3>
#
# -- a "/yr" welded onto a token that now renders a SENTENCE, a label
# announcing a free EV-charging win, and a Monday instruction to reprogram a
# charger the household does not own, sitting directly above an S15_VERDICT
# the same issue had already fixed to say "moving flexible house load".
#
# A token-level case cannot see any of that. So the two cases below assert on
# the RENDERED LINE -- template markup with its tokens substituted -- and the
# second one generalises the defect rather than pinning the three sites: no
# live line may weld a unit onto ANY token that can render a not-applicable
# sentence, including the tokens nobody has written yet.
# ===========================================================================
def _live_template_text():
    """report-template.html with every comment, <script> and <style> body
    blanked out -- the fixed markup a reader actually sees. Same masking
    _template_token_positions uses, so "live" means one thing in this file."""
    text = rt.TEMPLATE.read_text()
    buf = list(text)
    for m in _MASKED_RE.finditer(text):
        for i in range(*m.span()):
            if buf[i] != "\n":
                buf[i] = " "
    return "".join(buf)


def _rendered_live_template():
    """The live template with every resolvable token substituted -- the words
    a reader actually sees, as opposed to the token values every case above
    checks in isolation. The five KNOWN_GAPS keep their {{...}} (nothing
    resolves them by design); any OTHER refusal is left to raise, because a
    token that cannot resolve is a household that gets no report."""
    def sub(m):
        spec = rt.TOKENS.get(m.group(1))
        if spec is None or spec.get("kind") == "gap":
            return m.group(0)
        return rt.resolve_token(m.group(1))
    return rt._TOKEN_RE.sub(sub, _live_template_text())


_H2_ID_RE = re.compile(r'<h2 id="([^"]+)"')


def _rendered_sections():
    """{section id: that section's rendered markup}, split at each <h2 id>."""
    doc = _rendered_live_template()
    hits = list(_H2_ID_RE.finditer(doc))
    return {m.group(1): doc[m.start():(hits[i + 1].start() if i + 1 < len(hits)
                                       else len(doc))]
            for i, m in enumerate(hits)}


# THE SITES ARE LOCATED STRUCTURALLY, NOT BY THE TOKENS THAT NOW FILL THEM.
# Anchoring on {{S0_FREE_WIN_CARD_FIGURE}} would make this case fail with
# "re-anchor me" if someone put the literal markup back -- guidance, not a
# finding, and re-anchoring would then let the defect ship. A section id and
# an element class survive the revert, so the revert fails on what is WRONG
# with it (issue #147, and the tests-must-fail-on-their-own-defect rule).
_CARD_RE = re.compile(r'<div class="card">.*?</div></div>', re.S)
_H3_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.S)
# "EV-TOU-5" IS A TARIFF NAME, NOT A CLAIM ABOUT THE HOUSEHOLD. SDG&E calls
# the plan that whoever is on it drives, the name comes from
# private/household.yaml rather than from any artifact, and section 0's
# best-plan card carries it. Everything else here is a statement that this
# house charges a car.
_EV_VOCABULARY = re.compile(r"\bEVs?\b(?!-TOU)|\bcharger\b|\bcharging\b|"
                            r"\breprogram\w*\b|\bplug-?in\b", re.I)

# What this household -- which HAS an EV -- must go on publishing to the
# character. These four strings are report-template.html's own fixed prose as
# it read before issue #147 tokenized any of it, so the change that makes a
# no-EV report true is proved not to have moved this one.
_EV_HOUSEHOLD_LITERALS = (
    ("section 0's free-win card",
     '<div class="card"><div class="big">~$1,221/yr</div><div class="lbl">Free win: '
     'fully super-off-peak EV charging (session-level, 100% compliance)</div></div>'),
    ("the Monday appendix's first instruction",
     "<h3>1 · Reprogram charging (this week, $0)</h3>"),
    ("the Monday appendix's success-metrics heading",
     '<h3>3 · Pre-registered success metrics for the EV fix '
     '<span class="pill y">modeled targets</span></h3>'),
    ("section 9's charging subsection",
     "<h3>EV charging report card</h3>"),
)


@case
def case_the_markup_around_the_no_ev_tokens_names_no_ev_either():
    """ISSUE #147 (Codex adversarial review). Issue #147's first pass made
    seven tokens render truthfully for a household whose intake says
    household.has_ev is false. The page stayed false anyway, because the
    FIXED MARKUP around them still asserted an EV -- a "/yr" welded onto a
    token that now renders a sentence, a card label announcing a free
    EV-charging win, a Monday instruction to reprogram a charger, a section-9
    report card for a car that is not there. No token-level case can see any
    of that, which is exactly how it shipped.

    So this case reads the RENDERED page. The no-EV household is the whole
    one -- behavior_rebuild.json's four not-applicable stubs and the
    package_results.json that prices scenario c beside them -- because a
    half-stubbed pair is a state the free-fix guard deliberately refuses, and
    refusing is not what this is about.

    SCOPED TO THE REGIONS THE ARTIFACTS DECIDE (section 0's cards, the Monday
    appendix's headings, section 9's headings) rather than to the whole
    document: the fixture swaps three ARTIFACTS and leaves
    private/household.yaml alone, so the rest of the page still carries
    household-sourced EV facts -- the plan really is called EV-TOU-5 -- which
    are not this defect and never were."""
    # Renders the real template, which carries household_yaml tokens
    # (UTILITY_NAME and friends), so this one genuinely cannot run without
    # the intake -- unlike the sweep above, which holds those tokens out and
    # keeps going. Same SkipCase convention the other archive-dependent
    # cases in this file use.
    _require_household()

    # 1. THE CONTROL. The EV household's four sites, unchanged to the
    #    character. Located by their own text, so putting the literal markup
    #    back does not break the control -- it is the no-EV half below that
    #    such a revert has to fail.
    published = _rendered_live_template()
    for what, literal in _EV_HOUSEHOLD_LITERALS:
        assert literal in published, (
            f"{what} no longer renders what report-template.html published before "
            f"issue #147 tokenized it:\n  {literal}\n"
            "Tokenizing a site is only allowed to change what a DIFFERENT household "
            "reads. If an artifact legitimately moved this household's figure, re-pin "
            "the literal here and say which regeneration moved it.")

    with _patched(rt, "_json", _no_ev_household()):
        sections = _rendered_sections()

    checked = 0

    # 2. SECTION 0'S CARDS. Six cards; none may tell a household with no EV
    #    about an EV, and every headline cell is a FIGURE, never a sentence
    #    wearing a unit -- "{{EV_FIX_SAVINGS_100}}/yr" published "not
    #    applicable to this household -- household.has_ev is false .../yr" in
    #    a <div class="big"> exactly that way.
    cards = _CARD_RE.findall(sections["s0"])
    assert len(cards) >= 5, f"only {len(cards)} card(s) found in section 0"
    for card in cards:
        checked += 1
        hit = _EV_VOCABULARY.search(card)
        assert not hit, (
            f"a section-0 card tells a household with no EV about an EV "
            f"({hit.group(0)!r}): {card!r}")
        big = re.search(r'<div class="big[^"]*">(.*?)</div>', card)
        assert big, card
        assert len(big.group(1)) <= 40 and "not applicable" not in big.group(1), (
            "a section-0 card's headline cell is a SENTENCE, not a figure -- the "
            f"template is decorating a token that refused: {big.group(1)!r}")
        lbl = re.search(r'<div class="lbl">(.*?)</div>', card)
        assert lbl, card
        # The cap is applied to the label THIS change owns, not to all six:
        # CLAUDE.md section 10 puts it on a section's LEAD sentence, and the
        # other five card labels are older markup that reads as a caption
        # (the best-plan card spends two asides on the scenarios it beat).
        if re.match(r"(Free win|No modeled saving|Costs money here):", lbl.group(1)):
            _assert_within_density_cap("the section-0 free-win card label",
                                       lbl.group(1), "a household with no EV")

    # 3. THE MONDAY APPENDIX'S HEADINGS. This is the one instruction list in
    #    the report, so a heading naming hardware the household does not own
    #    is an instruction that cannot be carried out.
    s15 = _H3_RE.findall(sections["s15"])
    assert len(s15) >= 3, f"only {len(s15)} h3(s) found in the Monday appendix"
    for h3 in s15:
        checked += 1
        hit = _EV_VOCABULARY.search(h3)
        assert not hit, (
            f"a 'What to do Monday' heading tells a household with no EV to act on "
            f"an EV ({hit.group(0)!r}): {h3!r}")
        _assert_within_density_cap("a Monday appendix heading",
                                   re.sub(r"<[^>]+>", "", h3),
                                   "a household with no EV")
    step1 = re.sub(r"<[^>]+>", "", s15[0])
    assert "flexible house load" in step1, (
        f"the first Monday instruction does not name the load this household can "
        f"actually move: {step1!r}")
    assert "(this week, $0)" in step1, (
        f"the first Monday instruction lost the fact that makes it FREE: {step1!r}")

    # 4. SECTION 9'S HEADINGS. The EV subsection's six tokens all render
    #    "not applicable" here, and the heading over them has to say so too
    #    rather than announcing a report card for a car that is not there.
    ev_headings = [h for h in _H3_RE.findall(sections["s9"])
                   if _EV_VOCABULARY.search(h)]
    assert len(ev_headings) == 1, (
        f"expected exactly one section-9 heading about charging, found "
        f"{len(ev_headings)}: {ev_headings}")
    checked += 1
    assert "not applicable" in ev_headings[0], (
        f"section 9 still announces a charging report card for a household with no "
        f"car to report on: {ev_headings[0]!r}")

    # 5. Nothing anywhere in the three regions is malformed.
    for region in (sections["s0"], sections["s15"], sections["s9"]):
        for label, pattern in _MALFORMED_RENDER:
            hit = pattern.search(region)
            assert not hit, f"a checked region rendered {label}: {hit.group(0)!r}"

    return (f"all {len(_EV_HOUSEHOLD_LITERALS)} artifact-driven markup sites render "
            f"their EV-household literal unchanged, and {checked} rendered element(s) "
            f"across sections 0, 9 and 15 name no EV on a household without one "
            f"(first Monday instruction: '{step1}')")


_UNIT_SUFFIX_RE = re.compile(r"^\s*(/yr|/mo|/kWh|/day|%|¢|×|x\b|kWh|kW\b|therms?\b|"
                             r"years?\b|yr\b|hours?\b|h\b)")
_SIGIL_PREFIX_RE = re.compile(r"[$~¢+\-±]\s*$")


@case
def case_no_live_markup_welds_a_unit_onto_a_token_that_can_state_it_does_not_apply():
    """THE CLASS, NOT THE THREE INSTANCES (issue #147, Codex adversarial
    review). "{{EV_FIX_SAVINGS_100}}/yr" was true markup right up to the
    moment that token learned to answer "not applicable to this household --
    household.has_ev is false ...", and then it published a sentence with an
    annual unit stuck on the end of it, inside the report's most prominent
    cell.

    The rule that generalises it: a template may not supply a unit or a sigil
    for a token that can render prose, because the template cannot know which
    of the two it got. The token owns its own unit -- issue #129's rule,
    applied to the seam between a token and the markup beside it -- and the
    tokens that can refuse are DISCOVERED here rather than listed, so one
    added next year is swept without editing this case.

    Discovery is by resolution, not by declaration: the two applicability
    fixtures this suite already carries (no EV, no gas) are driven, and every
    token whose value comes back saying it does not apply is a token no
    template line may decorate."""
    refusable, aborts = set(), {}

    def sweep(stub, label):
        with _patched(rt, "_json", stub):
            for name, spec in rt.TOKENS.items():
                if spec.get("kind") == "gap":
                    continue
                try:
                    value = rt.resolve_token(name)
                except SystemExit as e:
                    aborts.setdefault(name, f"{label}: {e}")
                    continue
                if "not applicable" in value:
                    refusable.add(name)

    sweep(_no_ev_household(), "no EV")

    def no_gas(doc):
        doc.clear()
        doc.update({"applicable": False, "reason": "household.has_gas is false"})

    for artifact in ("all_electric_endgame.json", "heat_pump_conversion.json"):
        sweep(_stub_for(artifact, no_gas), f"no gas ({artifact})")

    # The sweep must have FOUND something, or every assertion below passes on
    # an empty set -- the "guard reporting success while covering nothing"
    # failure this suite names elsewhere.
    assert len(refusable) >= 15, (
        f"only {len(refusable)} token(s) were found able to say they do not apply "
        f"({sorted(refusable)}) -- the discovery step broke, and this case is now "
        "checking nothing")

    text = _live_template_text()
    offences = []
    for m in rt._TOKEN_RE.finditer(text):
        name = m.group(1)
        if name not in refusable:
            continue
        after = text[m.end():m.end() + 12]
        before = text[max(0, m.start() - 4):m.start()]
        if _UNIT_SUFFIX_RE.match(after):
            offences.append((name, "a unit welded on after it", repr(after)))
        if _SIGIL_PREFIX_RE.search(before):
            offences.append((name, "a sigil in front of it", repr(before)))
    assert not offences, (
        "report-template.html decorates a token that can render 'not applicable to "
        "this household' as though it were always a number, so that household's "
        "report publishes a sentence wearing a unit: "
        + "; ".join(f"{n} has {why} ({ctx})" for n, why, ctx in offences)
        + ". Move the unit into the token, the way S0_FREE_WIN_CARD_FIGURE owns "
          "its '/yr'")
    note = ""
    if aborts:
        # NOT ASSERTED ON, and deliberately: a token that aborts under one of
        # these fixtures is a real finding of the same family (a leaf token
        # reading past a shape a generator legitimately writes), but it is a
        # DIFFERENT cause from the markup seam this case guards, and silently
        # folding one into the other is how a guard stops naming what it
        # caught. It is reported so the next reader sees it.
        note = (f"; {len(aborts)} token(s) abort outright under one of these "
                f"fixtures and need their own fix: {sorted(aborts)}")
    return (f"no live template line decorates any of the {len(refusable)} token(s) "
            f"that can state they do not apply{note}")


@case
def case_the_free_win_card_stops_calling_it_a_win_when_the_artifacts_say_it_is_not():
    """The card's label is a CLAIM ABOUT THE SIGN of the figure printed
    directly above it, so "Free win" over a modeled loss is the same class of
    falsehood as "EV charging" over a household with no car -- fixed prose
    contradicting the number beside it.

    Three states, the same three _free_fix_clause carries, driven by moving
    BOTH artifacts together: packages.LOW.savings_yr is literally
    round(scenarios[k].saved), and swapping one alone builds a pair no run
    produces and tests the drift guard instead of the card."""
    # Renders live template markup, which carries household_yaml tokens, so
    # it cannot run without the intake. Same convention as the sibling case.
    _require_household()

    scen = rt._json("behavior_rebuild.json")["scenarios"]
    got = {}

    def free_win_card():
        """Section 0's free-win card, located by the one thing that survives
        every branch: it is the card whose label states what the free
        behavior fix is worth."""
        cards = [c for c in _CARD_RE.findall(_rendered_sections()["s0"])
                 if re.search(r'<div class="lbl">(Free win|No modeled saving|'
                              r'Costs money here):', c)]
        assert len(cards) == 1, (
            f"expected exactly one free-win card in section 0, found {len(cards)}")
        return cards[0]

    def priced_at(amount):
        def behavior(doc):
            doc["scenarios"]["a"]["saved"] = float(amount)

        def package(doc):
            doc["packages"]["LOW"]["savings_yr"] = round(amount)
        return _stub_for_many({"behavior_rebuild.json": behavior,
                               "package_results.json": package})

    for label, amount, forbidden, wanted in (
            ("zero", 0.0, "Free win", "No modeled saving"),
            ("a loss", -812.0, "Free win", "Costs money here")):
        with _patched(rt, "_json", priced_at(amount)):
            got[label] = free_win_card()
        assert forbidden not in got[label], (
            f"section 0's card calls a free fix worth {amount}/yr a {forbidden!r}: "
            f"{got[label]!r}")
        assert wanted in got[label], (
            f"section 0's card does not state what {amount}/yr actually is: "
            f"{got[label]!r}")
        for name, pattern in _MALFORMED_RENDER:
            assert not pattern.search(got[label]), f"{label} rendered {name}: {got[label]}"

    # The positive control: the same card on the same household at a real
    # positive saving DOES sell it, or "never says Free win" would pass on a
    # card that never says anything.
    assert "Free win" in free_win_card(), (
        "the card withholds 'Free win' from a fix the artifacts price at "
        f"${round(scen['a']['saved']):,}/yr")

    def _cells(card):
        return (re.search(r'<div class="big[^"]*">(.*?)</div>', card).group(1),
                re.search(r'<div class="lbl">([^:]*):', card).group(1))

    return ("section 0's free-win card states a zero and a loss as themselves "
            f"({_cells(got['zero'])} / {_cells(got['a loss'])}) and still sells a "
            "real saving")


@case
def case_the_third_free_fix_naming_renders_a_heading_and_a_label_of_its_own():
    """_free_fix_move returns THREE names, and only two of them are reachable
    from the artifacts any generator writes today: analysis/package_results.py
    picks scenario "a" for a household with an EV and "c" for one without, so
    "EV-and-house-load" -- scenario c on a household that DOES have an EV --
    is a branch no case above ever renders.

    It is still a branch the markup depends on: the Monday appendix's first
    heading looks its imperative up by that name and REFUSES a name it has no
    instruction for, and CLAUDE.md section 10's density cap governs every
    branch rather than the one that renders today. A branch nothing exercises
    is where the next KeyError lives."""
    scen = rt._json("behavior_rebuild.json")["scenarios"]

    def prices_scenario_c(doc):
        low = doc["packages"]["LOW"]
        low["free_fix_scenario"] = "c"
        low["savings_yr"] = round(scen["c"]["saved"])

    with _patched(rt, "_json", _stub_for("package_results.json", prices_scenario_c)):
        heading = _renders("S15_STEP1_HEADING")
        label = _renders("S0_FREE_WIN_CARD_LABEL")
        short = _renders("FREE_FIX_SHORT_NAME")
        figure = _renders("S0_FREE_WIN_CARD_FIGURE")

    # It moves BOTH, so both are named: an instruction that mentioned only the
    # charger would leave the house-load half of the move unstated, and one
    # that mentioned only the load would drop the half this household is
    # already doing.
    assert "charging" in heading and "flexible house load" in heading, (
        f"the first Monday instruction does not name both halves of the move "
        f"packages.LOW prices: {heading!r}")
    assert "EV charging and flexible house load" in label, (
        f"section 0's card does not name both halves of the move: {label!r}")
    assert short == "EV-and-house-load", short
    assert re.fullmatch(r"~?-?\$[\d,]+/yr", figure), figure
    _assert_within_density_cap("S15_STEP1_HEADING", heading, "scenario c with an EV")
    _assert_within_density_cap("S0_FREE_WIN_CARD_LABEL", label, "scenario c with an EV")
    for name, value in (("S15_STEP1_HEADING", heading),
                        ("S0_FREE_WIN_CARD_LABEL", label),
                        ("S0_FREE_WIN_CARD_FIGURE", figure)):
        for what, pattern in _MALFORMED_RENDER:
            assert not pattern.search(value), f"{name} rendered {what}: {value}"
    return (f"the third free-fix naming renders '{heading}' / '{label}' "
            f"({figure}) instead of refusing a branch no committed artifact reaches")


def _night_floor_readers():
    """Every non-gap token whose resolution ACTUALLY READS
    data/quiet_night_floor.json, discovered by the loader's own record.

    Not read off TOKENS' `sources`, for the reason the poison sweep gives one
    section up: that list is prose, nothing consumes it, and a token that
    forgets to name an artifact is exactly the omission the case below exists
    to catch. rt._reads is populated inside rt._json, and _stub_for delegates
    to the real loader, so a stubbed resolution records the same way.

    A token this checkout cannot resolve (no private archive on CI) simply
    does not enter the enumeration; the case asserts a floor set separately so
    the sweep cannot silently collapse to nothing."""
    found = []
    for name, spec in rt.TOKENS.items():
        if spec.get("kind") == "gap":
            continue
        try:
            rt.resolve_token(name)
        except SystemExit:
            continue
        if rt._NIGHT_FLOOR_ARTIFACT in rt._reads:
            found.append(name)
    return sorted(found)


# THE FLOOR, NOT THE LIST. The case below enumerates its own tokens, so a
# token added later is swept without editing this file; these six are asserted
# to still be IN that enumeration so a refactor that stops discovering them
# fails loudly instead of passing on an empty sweep (the "a guard reporting
# success while covering nothing" failure this suite already names once).
# Removing a name here is only correct if that token genuinely stopped
# claiming a year off this artifact.
_ANNUAL_NIGHT_FLOOR_FLOOR_SET = frozenset((
    "NIGHT_FLOOR_ANNUAL_KWH", "NIGHT_FLOOR_ANNUAL_COST", "SEC9_TEASER",
    "PHANTOM_METHOD_DISCREPANCY", "NIGHT_FLOOR_SENSITIVITY_PER_100W",
    "NIGHT_FLOOR_PRICING_BASIS"))


@case
def case_a_corpus_that_is_not_a_year_never_publishes_a_figure_wearing_an_annual_unit():
    """ISSUE #132, PASS 3 AND CODEX PASS 1 FINDING 1. NIGHT_FLOOR_ANNUAL_KWH
    annualizes floor_kw x nights x 24 and labels it "/yr", while the artifact
    declares its own basis as the floor "applied as a constant across all 8,760
    hours of the year". nights_total is only the count of dated rows, so the
    two agree at 365 BY COINCIDENCE.

    The gate is tested at BOTH ENDS and against a scattered record, because
    each was a separate way to publish a false unit:
      * short  -- 200 nights understated the year and still said "/yr";
      * long   -- 730 nights rendered "18,046 kWh/yr", roughly two years of
                  energy wearing an annual unit, which the first version of
                  this gate let straight through (it tested only `>= 365`);
      * gappy  -- 365 dates scattered over three calendar years is not a year,
                  and a bare count cannot tell the difference, which is why
                  coverage is read from daily_series' own first and last date.

    ISSUE #140, ADVERSARIAL PASS 2, FINDING 2 ADDS THE OTHER TWO TOKENS THAT
    PRICE THIS LOAD. Half the rendering was coverage-aware and half was not:
    SEC9_TEASER read the price-map total and appended "/yr" unconditionally,
    and PHANTOM_METHOD_DISCREPANCY labelled both totals and their gap the same
    way -- so on a regenerated partial corpus the report published a correctly
    window-qualified kWh figure beside falsely annualized dollars IN THE SAME
    SENTENCE. All four tokens go through the same cases here rather than a
    parallel set, because the defect was precisely that the four did not share
    a mechanism.

    ISSUE #140, ADVERSARIAL PASS 3 STOPS NAMING TOKENS AT ALL. Fixing the
    four by name is how the fifth was missed: NIGHT_FLOOR_SENSITIVITY_PER_100W
    appended "/yr" to a savings RATE re-billed over the same interval series,
    and a sixth, NIGHT_FLOOR_PRICING_BASIS, claimed the year in a WORD rather
    than a unit ("leaving the annual figures conservative by about $86")
    beside two figures that had correctly stopped claiming it. Both were
    invisible to this case because it looped a hardcoded four.

    So the population is now DISCOVERED, twice over: _night_floor_readers()
    asks the loader which tokens actually read the artifact, and the annual
    ones are the subset whose complete render trips rt._ANNUAL_CLAIM -- the
    same expression the module's own structural guard uses. A token added
    later is swept without editing this file. The tokens that read the
    artifact and legitimately claim NO year (a kW median, a spread, a night
    count) are asserted too, in the other direction: they must never quietly
    acquire an annual unit.

    AND THE STRUCTURAL GUARD IS TESTED AS A GUARD, on a token deliberately
    written the wrong way and deliberately declaring no sources at all --
    which is what "a token added later cannot reintroduce this by omission"
    has to mean if it means anything.

    And the complete case must still render exactly what it renders today."""
    readers = _night_floor_readers()
    complete = {n: rt.resolve_token(n) for n in readers}
    annual = sorted(n for n in readers if rt._ANNUAL_CLAIM.search(complete[n]))
    quiet = [n for n in readers if n not in set(annual)]
    missing = _ANNUAL_NIGHT_FLOOR_FLOOR_SET - set(annual)
    assert not missing, (
        f"the enumeration no longer covers {sorted(missing)} -- either the sweep stopped "
        f"discovering them or they stopped claiming a year; it found {annual}")
    assert quiet, f"every night-floor reader claims a year, which cannot be right: {readers}"
    assert complete["NIGHT_FLOOR_ANNUAL_KWH"].endswith("kWh/yr"), complete

    def corpus(nights, step=1, hole_to=None):
        """A coherent stub: `nights` dates every `step` days, with nights_total
        agreeing with the series it is a count of. `hole_to` instead lays down
        `nights - 1` consecutive dates and then one final date at that offset,
        which is how you get a record that spans exactly a year while missing
        days inside it."""
        def edit(doc):
            first = dt.date(2025, 7, 24)
            if hole_to is None:
                offsets = [i * step for i in range(nights)]
            else:
                offsets = list(range(nights - 1)) + [hole_to]
            doc["night_floor"]["daily_series"] = [
                {"date": (first + dt.timedelta(days=o)).isoformat(),
                 "median_kw": doc["night_floor"]["median_kw"],
                 "excluded_high_demand": False}
                for o in offsets]
            doc["night_floor"]["nights_total"] = nights
            doc["night_floor"]["quiet_nights"] = nights
        return edit

    checked = {}
    for label, kwargs, expect in (
            ("short", dict(nights=200), "less than a full year"),
            ("short", dict(nights=90), "less than a full year"),
            ("long", dict(nights=730), "more than a full year"),
            ("long", dict(nights=400), "more than a full year"),
            # 365 dates every third day: the right COUNT, three calendar years
            # of window -- the case a bare count cannot see at all.
            ("scattered", dict(nights=365, step=3), "spanning more than a full year"),
            # 300 dates inside a 365-day window: the right SPAN, holes in it.
            ("gappy", dict(nights=300, hole_to=364), "gaps")):
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json",
                                             corpus(**kwargs))):
            for token in annual:
                text = _renders(token)
                checked[f"{token}@{label}:{kwargs}"] = text
                assert not rt._ANNUAL_CLAIM.search(text), (
                    f"{token} publishes an ANNUAL figure from a {label} corpus "
                    f"({kwargs}), which is not a year: {text}")
                assert expect in text, f"{token} did not say why ({expect!r}): {text}"
                for pat_label, pattern in _MALFORMED_RENDER:
                    assert not pattern.search(text), f"{token} rendered {pat_label}: {text}"
            # The other direction, on the tokens that read this artifact and
            # claim no year: they must still render, and must not pick one up.
            for token in quiet:
                text = _renders(token)
                checked[f"{token}@{label}:{kwargs}"] = text
                assert not rt._ANNUAL_CLAIM.search(text), (
                    f"{token} acquired an annual claim on a {label} corpus "
                    f"({kwargs}): {text}")
                for pat_label, pattern in _MALFORMED_RENDER:
                    assert not pattern.search(text), f"{token} rendered {pat_label}: {text}"

    # A leap year is still a year: the gate asks about coverage, not length.
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", corpus(nights=366))):
        leap = _renders("NIGHT_FLOOR_ANNUAL_KWH")
    assert leap.endswith("kWh/yr"), leap

    # The rate is the one the pricing used: a floor_kw_priced that no longer
    # matches median_kw beyond its own rounding means the kWh figure and the $
    # figure would rest on different floors, and that IS a refusal.
    def drifted(doc):
        doc["pricing"]["floor_kw_priced"] = doc["night_floor"]["median_kw"] + 0.5

    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", drifted)):
        try:
            rt.resolve_token("NIGHT_FLOOR_ANNUAL_KWH")
            raise AssertionError("a drifted floor_kw_priced was accepted")
        except SystemExit as e:
            assert "NIGHT_FLOOR_ANNUAL_KWH" in str(e), e

    # THE GUARD, AS A GUARD. A token written the way all six of the ones above
    # were originally written -- price map total, "/yr" appended, no coverage
    # call -- and declaring NO sources at all, so that nothing about this
    # depends on a declaration being remembered. On a real year it renders; on
    # a corpus that is not one it is refused, by name, pointing at the gate.
    probe = "NIGHT_FLOOR_UNGATED_ANNUAL_PROBE"
    assert probe not in rt.TOKENS, probe
    rt.TOKENS[probe] = dict(
        kind="derived", sources=[],
        get=lambda ctx: "${:,.0f}/yr".format(
            rt._night_floor_pricing()["method_a_price_map"]["total_usd"]))
    try:
        assert rt.resolve_token(probe).endswith("/yr"), rt.resolve_token(probe)
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json",
                                             corpus(nights=200))):
            try:
                rt.resolve_token(probe)
                raise AssertionError(
                    "the structural guard let an undeclared, ungated '/yr' through")
            except SystemExit as e:
                assert probe in str(e), e
                assert "less than a full year" in str(e), e
                assert "_night_floor_coverage" in str(e), e
    finally:
        del rt.TOKENS[probe]

    after = {n: rt.resolve_token(n) for n in complete}
    assert after == complete, f"the stubs leaked: {after} != {complete}"
    return (f"{len(checked)} renders across short / long / scattered / gappy corpora, over "
            f"the {len(annual)} discovered token(s) that claim a year off this artifact and "
            f"the {len(quiet)} that read it without claiming one, report their own window "
            f"instead of an annual claim; a real year still reads "
            f"{complete['NIGHT_FLOOR_ANNUAL_KWH']!r}, a drifted priced floor refuses, and "
            f"an undeclared ungated '/yr' is refused by the loader-observed guard")


# EVERY WAY daily_series CAN ARRIVE CARRYING NO DATE, as edits to the real
# committed series so that nothing else about the artifact moves. nights_total
# is deliberately left at its committed 365 in all of them: that is the whole
# defect -- a count that still says "a year" beside a record that no longer
# says which nights.
#
# Six rather than the three the issue names, because "wholly malformed" is a
# CLASS and this suite's own lesson is to sweep the shape, not the instance.
# absent / empty / null are the container going missing three ways; the last
# three are the container surviving with 365 rows whose dates do not parse --
# a bad string, a missing key, a non-string value -- which is the half of the
# class a check for "is the list there" would pass.
_UNDATED_SERIES = ("absent", "empty", "null", "unparsable date string",
                   "row with no date key", "non-string date")


def _undated(kind):
    """An edit that strips every readable date from the committed series."""
    def edit(doc):
        nf = doc["night_floor"]
        rows = list(nf.get("daily_series") or [])
        assert rows, "the committed artifact has no daily_series to strip"
        if kind == "absent":
            nf.pop("daily_series", None)
        elif kind == "empty":
            nf["daily_series"] = []
        elif kind == "null":
            nf["daily_series"] = None
        elif kind == "unparsable date string":
            nf["daily_series"] = [dict(r, date="2026-13-45") for r in rows]
        elif kind == "row with no date key":
            nf["daily_series"] = [{k: v for k, v in r.items() if k != "date"}
                                  for r in rows]
        elif kind == "non-string date":
            nf["daily_series"] = [dict(r, date=None) for r in rows]
        else:
            raise AssertionError(f"unknown undated shape: {kind}")
    return edit


@case
def case_a_corpus_with_no_dated_record_cannot_claim_a_year_off_its_count_alone():
    """ISSUE #174. The case above proves a WRONG window is caught. This one
    proves a MISSING window is, which was the hole underneath it.

    _night_floor_coverage read its window from night_floor.daily_series and
    fell back to nights_total alone when that series held no readable date --
    so an artifact that was half-regenerated, written by an older generator,
    or simply missing the key published "9,023 kWh/yr" off a bare count of
    365. That is the exact inference the function's own docstring says a count
    cannot support: 365 dates scattered across three years also count 365, and
    with no dates present there is nothing left that can tell the two apart.
    The fallback was not a weaker version of the span check; it was the span
    check not running.

    IT MATTERS MORE SINCE #140 because the branch is now structural. Six
    published figures -- the floor's energy, its two pricings, section 9's
    teaser, the sensitivity rate and the pricing-basis sentence -- take their
    annual unit from this one return value, and _forbid_unearned_annual_unit
    routes every future reader through it too. One permissive branch decided
    all of them.

    WHAT REPLACES IT IS NOT A REFUSAL, per #140's recorded design constraint:
    each of the six was written with a window branch, so each has an honest
    sentence to fall back on and must print it. The corpus is still reported
    at its real size; what it stops doing is calling that size a year.

    The population is DISCOVERED, exactly as the case above discovers it, so
    a seventh token that starts reading this artifact is swept here without
    editing this file -- and the six are asserted present so the sweep cannot
    quietly collapse to nothing."""
    readers = _night_floor_readers()
    complete = {n: rt.resolve_token(n) for n in readers}
    annual = sorted(n for n in readers if rt._ANNUAL_CLAIM.search(complete[n]))
    missing = _ANNUAL_NIGHT_FLOOR_FLOOR_SET - set(annual)
    assert not missing, (
        f"the enumeration no longer covers {sorted(missing)}; it found {annual}")

    # THE POSITIVE CONTROL, in this run and before any stub. The committed
    # artifact really does carry a contiguous dated year, so it must still
    # clear the gate and every discovered token must still claim its year --
    # otherwise a "not covered" below would only prove the instrument broke.
    covers, nights, why = rt._night_floor_coverage()
    assert (covers, why) == (True, ""), (covers, nights, why)
    assert nights in rt._FULL_YEAR_NIGHTS, nights
    assert complete["NIGHT_FLOOR_ANNUAL_KWH"].endswith("kWh/yr"), complete
    series = rt._night_floor()["daily_series"]
    dated = sorted(dt.date.fromisoformat(str(r["date"])) for r in series)
    assert len(set(dated)) == nights == (dated[-1] - dated[0]).days + 1, (
        f"the committed artifact stopped being a contiguous dated year: {len(dated)} "
        f"rows, {len(set(dated))} unique, {dated[0]}..{dated[-1]}")

    # EVERY SHAPE ANSWERED BEFORE ANY IS ASSERTED ON, so a reverted fix names
    # the whole class in one failure line instead of stopping at whichever
    # shape happens to be first in the tuple. This suite has paid twice for a
    # guard that reported one instance of a defect it was covering six of.
    coverage = {}
    for kind in _UNDATED_SERIES:
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _undated(kind))):
            coverage[kind] = rt._night_floor_coverage()
    accepted = sorted(k for k, (c, _n, _w) in coverage.items() if c)
    assert not accepted, (
        f"{len(accepted)} daily_series shape(s) carrying no dated record were accepted "
        f"as a full year on nights_total alone -- a count cannot tell one year from "
        f"three: {accepted}")
    unexplained = sorted(k for k, (_c, _n, w) in coverage.items() if not w)
    assert not unexplained, f"rejected without saying why: {unexplained}"

    checked = {}
    for kind in _UNDATED_SERIES:
        _covers, nights, why = coverage[kind]
        assert nights == 365, (kind, nights)
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _undated(kind))):
            for token in annual:
                text = _renders(token)
                checked[f"{token}@{kind}"] = text
                assert not rt._ANNUAL_CLAIM.search(text), (
                    f"{token} published an annual claim off a {kind} daily_series: {text}")
                # The window branch, not a refusal and not silence: the real
                # size of the corpus, and the reason it is not a year.
                assert f"{nights:,.0f} nights measured" in text, (
                    f"{token} dropped its annual claim without stating the window it "
                    f"does have ({kind}): {text}")
                assert why in text, (
                    f"{token} stated a window without the reason it is not a year "
                    f"({kind}): {text}")
                for pat_label, pattern in _MALFORMED_RENDER:
                    assert not pattern.search(text), f"{token} rendered {pat_label}: {text}"

    # A LEAP COUNT IS NOT A WINDOW EITHER. 366 was the other half of the
    # accepted range, so it needs its own stub or the fix could be a change to
    # one boundary rather than to the inference.
    def leap_but_undated(doc):
        _undated("absent")(doc)
        doc["night_floor"]["nights_total"] = 366

    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", leap_but_undated)):
        covers, nights, why = rt._night_floor_coverage()
        assert (covers, nights) == (False, 366), (covers, nights, why)
        assert why, why

    after = {n: rt.resolve_token(n) for n in complete}
    assert after == complete, f"the stubs leaked: {after} != {complete}"
    return (f"{len(checked)} renders across {len(_UNDATED_SERIES)} undated-series shapes "
            f"({', '.join(_UNDATED_SERIES)}) plus an undated leap count, over the "
            f"{len(annual)} discovered token(s) that claim a year off this artifact: none "
            f"claims one without a dated record, each states the window it does have, and "
            f"the committed artifact still reads "
            f"{complete['NIGHT_FLOOR_ANNUAL_KWH']!r}")


@case
def case_a_degradation_sentence_never_contradicts_its_own_numbers():
    """ISSUE #132, CODEX PASS 1, FINDINGS 2 AND 3. Two sentences whose
    conclusion WORD was fixed text sitting beside numbers it never compared.

    DEGRADATION_NAIVE_RANGE tested `hi <= 0` first, so an array whose three
    estimators all sat at zero published "0.0-0.0%/yr of decline" -- a
    direction attached to a magnitude of nothing, and the one reading a
    stable-array owner would most want to be right.

    DEGRADATION_WEATHER_CAVEAT asserted that clear-sky variation "cannot
    explain" the observed spread without comparing the two, so an artifact
    whose clear-sky spread was the larger of the pair published "varies only
    99.00% ... so geometry cannot explain the 14.0% spread".

    Every case below is a legitimate artifact state; the assertion each time is
    that the WORD agrees with the DIGITS printed next to it."""
    def degradation(**fields):
        def edit(doc):
            doc["degradation"].update(fields)
        return edit

    def trend(ols, cagr, theil):
        return degradation(ols_pct_per_yr=ols, cagr_pct_per_yr=cagr,
                           theil_sen_pct_per_yr=theil)

    got = {}
    # 1. Exact zero, and a trend too small to survive the printed rounding:
    #    neither may carry a direction word.
    for label, edit in (("all_zero", trend(0.0, 0.0, 0.0)),
                        ("negative_zero", trend(-0.0, 0.0, -0.0)),
                        ("rounds_to_zero", trend(-0.04, -0.02, -0.01)),
                        ("rounds_to_zero_up", trend(0.04, 0.02, 0.01))):
        with _patched(rt, "_json", _stub_for("gross_import_decomposition.json", edit)):
            text = _renders("DEGRADATION_NAIVE_RANGE")
        got[label] = text
        assert "decline" not in text and "gain" not in text, (
            f"a trend that prints as 0.0 was called a direction: {text}")
        assert "no measurable change" in text, text

    # 2. The directions themselves still read correctly on either side.
    for label, edit, word in (("real_decline", trend(-1.8, -1.3, -1.5), "decline"),
                              ("real_gain", trend(1.8, 1.3, 1.5), "gain")):
        with _patched(rt, "_json", _stub_for("gross_import_decomposition.json", edit)):
            text = _renders("DEGRADATION_NAIVE_RANGE")
        got[label] = text
        assert word in text, text
        other = "gain" if word == "decline" else "decline"
        assert other not in text, f"{label} also claimed {other}: {text}"

    # 3. Clear-sky variation at least as large as the observed spread: the
    #    sentence must stop claiming geometry cannot account for it.
    for label, cs, spread in (("clearsky_larger", 99.0, 14.0),
                              ("clearsky_equal", 14.0, 14.0)):
        with _patched(rt, "_json", _stub_for(
                "gross_import_decomposition.json",
                degradation(clearsky_annual_spread_pct=cs,
                            peak_to_trough_pct_2021_2025=spread))):
            text = _renders("DEGRADATION_WEATHER_CAVEAT")
        got[label] = text
        assert "cannot explain" not in text, (
            f"clear-sky varies by {cs}% against a {spread}% spread and the sentence "
            f"still says geometry cannot explain it: {text}")
        assert "varies only" not in text, text
        assert "could account for it" in text, text

    # ... and still says it where the comparison really does support it.
    with _patched(rt, "_json", _stub_for(
            "gross_import_decomposition.json",
            degradation(clearsky_annual_spread_pct=0.15,
                        peak_to_trough_pct_2021_2025=14.0))):
        got["clearsky_smaller"] = _renders("DEGRADATION_WEATHER_CAVEAT")
    assert "cannot explain" in got["clearsky_smaller"], got["clearsky_smaller"]

    # 4. The soiling clause's own boundary: an event exactly the size of the
    #    whole change is neither larger nor smaller than it.
    with _patched(rt, "_json", _stub_for(
            "gross_import_decomposition.json",
            degradation(single_event_soiling_swing_pct=5.2,
                        total_change_pct_2021_2025=-5.2))):
        got["swing_equals_change"] = _renders("DEGRADATION_WEATHER_CAVEAT")
    assert "exactly the size of" in got["swing_equals_change"], got["swing_equals_change"]
    assert "smaller than" not in got["swing_equals_change"], got["swing_equals_change"]

    for name, text in got.items():
        assert text.strip(), f"{name} rendered blank"
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"{name} rendered {label}: {text}"
    return (f"{len(got)} degradation sentences select their conclusion word from the "
            "comparison they describe, including at both zero boundaries")


@case
def case_the_install_cost_caveat_is_the_artifacts_own_words_not_a_paraphrase():
    """ISSUE #132, CODEX PASS 1's SWEEP. HEAT_PUMP_COST_BASIS asserted the
    study's example system was "larger than this household's own sizing" -- a
    comparison this module never makes and cannot make from what it reads. It
    happened to be true of today's artifact (a 4-ton example against a 3.0-ton
    sizing) and would have gone on reading as fact against any other.

    heat_pump_conversion.py states that comparison itself, tonnages and "not
    quantified" included, so the fix is to render its note rather than restate
    it -- and to refuse rather than publish a cost with no basis at all."""
    note = rt._json("heat_pump_conversion.json")["install_cost"]["note"]
    text = rt.resolve_token("HEAT_PUMP_COST_BASIS")
    assert note in text, f"the artifact's own note is not carried: {text}"
    assert "larger than this household" not in text, (
        f"the unchecked paraphrase is back: {text}")

    def blanked(doc):
        doc["install_cost"]["note"] = ""

    with _patched(rt, "_json", _stub_for("heat_pump_conversion.json", blanked)):
        try:
            rt.resolve_token("HEAT_PUMP_COST_BASIS")
            raise AssertionError("an install cost with no stated basis was published")
        except SystemExit as e:
            assert "HEAT_PUMP_COST_BASIS" in str(e) and "note" in str(e), e
    return ("HEAT_PUMP_COST_BASIS carries data/heat_pump_conversion.json's own "
            "install_cost.note verbatim and refuses when it is absent")


@case
def case_the_second_sweep_of_legitimate_generator_emissions():
    """ISSUE #132, /review FINDINGS 1-4 AND 8. The pass-2 sweep produced the
    right principle -- a value the generator writes ON PURPOSE is data, not an
    error -- and then was not run exhaustively. Five more sites had the same
    shape, four of them the identical null-payback defect the sweep had just
    fixed in tou_spread.

    heat_pump_conversion.payback_and_npv() returns {"payback_years": None,
    "note": "no positive annual savings on this basis -- no payback"}, and
    every payback in all_electric_endgame comes through it. Four tokens pushed
    that null into _quantities and aborted the WHOLE report for the household
    the conversion does not pay off on -- while HPWH_NET_SAVINGS beside them
    rendered its negative saving quite happily."""
    got = {}

    def endgame(edit):
        return _stub_for("all_electric_endgame.json", edit)

    def never_pay(node):
        node["payback_years"] = None
        node.pop("npv", None)          # the generator omits npv on this branch too

    # 1. All four payback tokens, each on its own null.
    def wh_never(doc):
        wh = doc["water_heater_conversion"]
        never_pay(wh["payback"][wh["headline_uef"]]["central_install"])
        for s in wh["water_heater_share_sensitivity"]["scenarios"].values():
            never_pay(s["payback"]["central_install"])
        never_pay(doc["sequencing_and_paybacks"]["complete_transition_payback"])

    with _patched(rt, "_json", endgame(wh_never)):
        for token in ("HPWH_PAYBACK", "HPWH_PAYBACK_SENSITIVITY",
                      "ELECTRIFICATION_COMBINED_PAYBACK"):
            got[token] = _renders(token)
            assert "never repays" in got[token], got[token]
    assert "$18,729" in got["ELECTRIFICATION_COMBINED_PAYBACK"], (
        "the install cost is still known when the payback is not")

    def hp_never(doc):
        for scenario in doc["payback"].values():
            never_pay(scenario["standalone"])

    with _patched(rt, "_json", _stub_for("heat_pump_conversion.json", hp_never)):
        got["HEAT_PUMP_PAYBACK"] = _renders("HEAT_PUMP_PAYBACK")
    assert "never repays" in got["HEAT_PUMP_PAYBACK"], got["HEAT_PUMP_PAYBACK"]

    # ... and a partial one: some scenarios repay, some never do.
    def hp_mixed(doc):
        keys = sorted(doc["payback"])
        never_pay(doc["payback"][keys[0]]["standalone"])

    with _patched(rt, "_json", _stub_for("heat_pump_conversion.json", hp_mixed)):
        got["HEAT_PUMP_PAYBACK_mixed"] = _renders("HEAT_PUMP_PAYBACK")
    assert "never repay" in got["HEAT_PUMP_PAYBACK_mixed"], got["HEAT_PUMP_PAYBACK_mixed"]

    # 2. soiling_analysis publishes no measured cleaning gain: the geometry
    #    half of the caveat stands, the soiling half is not determined.
    def no_gain(doc):
        doc["degradation"]["single_event_soiling_swing_pct"] = None

    with _patched(rt, "_json", _stub_for("gross_import_decomposition.json", no_gain)):
        got["DEGRADATION_WEATHER_CAVEAT"] = _renders("DEGRADATION_WEATHER_CAVEAT")
    assert "not determined" in got["DEGRADATION_WEATHER_CAVEAT"], \
        got["DEGRADATION_WEATHER_CAVEAT"]
    assert "cannot explain" in got["DEGRADATION_WEATHER_CAVEAT"], \
        got["DEGRADATION_WEATHER_CAVEAT"]

    # 3. service_headroom OMITS pv_ac_ceiling on a household with no PV --
    #    "a computation that does not apply is not the same as one that ran
    #    and found nothing", in the generator's own words.
    def no_pv(doc):
        doc["gross_reconstruction"].pop("pv_ac_ceiling", None)
        doc["gross_reconstruction"]["identity"] = \
            "gross = import (no on-site generation to net out)"

    with _patched(rt, "_json", _stub_for("service_headroom.json", no_pv)):
        for token in ("PV_PEAK_OBSERVED", "PV_PEAK_HEADROOM", "PV_PEAK_BASIS"):
            got[token] = _renders(token)
            assert "not applicable" in got[token], got[token]
            assert "no on-site generation" in got[token], got[token]

    # 4. One fitted slope cannot be publishable and not: the annual figure
    #    takes the same sign the slope it is multiplied out from takes.
    def cooling_negative(doc):
        doc["annual_cooling_kwh"] = -420
        doc["kwh_per_cdd65"] = -1.4

    with _patched(rt, "_json", _stub_for("weather_results.json", cooling_negative)):
        got["ANNUAL_COOLING_KWH"] = _renders("ANNUAL_COOLING_KWH")
        got["COOLING_KWH_PER_CDD"] = _renders("COOLING_KWH_PER_CDD")
    assert "-420" in got["ANNUAL_COOLING_KWH"], got["ANNUAL_COOLING_KWH"]
    assert "-1.4" in got["COOLING_KWH_PER_CDD"], got["COOLING_KWH_PER_CDD"]

    # 8. A measured peak ABOVE the stated ceiling is a contradiction, not a
    #    percentage. Gated on the archive, and ONLY this part: the ceiling it
    #    compares against is household.yaml's solar.kw_ac, while the four
    #    checks above need no private data and must keep running on CI.
    if rt.hh.PATH.is_file():
        real_csv = rt._csv_rows

        def over_ceiling(name):
            rows = real_csv(name)
            if name != "cleaning_study_peaks_2024.csv":
                return rows
            return [dict(r, peak_w="99000") for r in rows]

        with _patched(rt, "_csv_rows", over_ceiling):
            got["PEAK_POWER_MULTIYEAR"] = _renders("PEAK_POWER_MULTIYEAR")
        assert "ABOVE the inverter AC ceiling" in got["PEAK_POWER_MULTIYEAR"], \
            got["PEAK_POWER_MULTIYEAR"]
        assert "% of the inverter" not in got["PEAK_POWER_MULTIYEAR"], \
            got["PEAK_POWER_MULTIYEAR"]

    for name, text in got.items():
        assert text.strip(), f"{name} rendered blank"
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"{name} rendered {label}: {text}"
    return (f"{len(got)} render(s) across the five sites the pass-2 sweep missed: a null "
            "payback, an unmeasured cleaning gain, an omitted PV ceiling, a negative "
            "cooling fit and an above-nameplate peak are all answers, not aborts")


@case
def case_the_malformed_render_patterns_flag_exactly_the_shapes_they_name():
    """ISSUE #132. The sweep's whole contract is _MALFORMED_RENDER, and nothing
    tested the patterns themselves -- a lookaround loosened by one character
    would disable the guard while every case above still reported PASS, which
    is the shape six review rounds have been finding.

    So the patterns are pinned from both sides. The first list is what must
    still be caught, one entry per pattern including the shapes an earlier
    round actually shipped ("-$nan", "$-1,234", a bare "$" before a word). The
    second is what must NOT be caught, and it is short on purpose: the only
    entries are ones a real token renders, and the bracket-then-percent one is
    the exemption issue #132 added when SPREAD_TREND_WINTER began rendering
    tou_spread.py's own confidence intervals ("[1.68, 21.0]%/yr"). Written out here
    the exemption is a fact this case asserts rather than a lookbehind nobody
    reads."""
    def flagged(text):
        return [label for label, pattern in _MALFORMED_RENDER if pattern.search(text)]

    must_flag = {
        "$nan": "a non-finite numeral",
        "-$nan": "a non-finite numeral",
        "$inf/yr": "a non-finite numeral",
        "1.2 kW is infinity": "a non-finite numeral",
        "$-1,234": "a minus inside a sigil",
        "+$-3,000": "a minus inside a sigil",
        "$$4,200": "a doubled sigil",
        "12%%": "a doubled sigil",
        "8.4¢¢": "a doubled sigil",
        "~~$500": "a doubled sigil",
        "costs $ a year": "an empty numeric field",
        "a share of %": "an empty numeric field",
        "stored energy costs ¢": "an empty numeric field",
        # The bracket exemption must require a NUMBER, not merely a bracket:
        # an interval that formatted to nothing is the exact shape this
        # pattern exists for, and the first version of the exemption let all
        # three of these through (reviewer catch, issue #132).
        "95% CI []%/yr": "an empty numeric field",
        "[ ]%/yr": "an empty numeric field",
        "[nan]%/yr": "an empty numeric field",
        "[]¢": "an empty numeric field",
    }
    for text, label in must_flag.items():
        assert label in flagged(text), (
            f"{text!r} must be flagged as {label!r}; the patterns matched {flagged(text)}")

    must_not_flag = (
        # The real renders of tokens this repo publishes today.
        "not determined — the post-break slope is significant on its own interval "
        "([1.68, 21.0]%/yr) but not once that interval is widened",
        "$4,200 installed (quoted range $2,800–8,000)",
        "-$500/yr",
        "45.2 kWh/day",
        "97% of the inverter AC ceiling",
        "~8.4¢",
        "the information in the infrastructure is fine",
        "carried by a single step of $0.04844/kWh on 2025-06-14 (21.4% of the prior level)",
    )
    for text in must_not_flag:
        assert not flagged(text), f"{text!r} is a legitimate render but was flagged: " \
                                  f"{flagged(text)}"
    return (f"all {len(_MALFORMED_RENDER)} malformed-render patterns flag the "
            f"{len(must_flag)} shapes they name and none of the {len(must_not_flag)} "
            "legitimate renders, including a bracketed confidence interval's percent sign")


# --- the ten round-5 findings, one regression case each ---------------------
def _matrix_winning_plan():
    """The plan battery_plan_matrix.json prices cheapest in BOTH of its
    columns -- the household's own where the private archive is staged.

    The cases below stub this plan so section 4 is in its ORDINARY state --
    the household on the plan the matrix puts first, S4_ROW_CLASS reading
    "win" -- and whatever they then poison is the only thing that has moved.
    (Before issue #178 it was load-bearing rather than tidy: the three cells
    refused outright for any other plan, so a case stubbing one was
    exercising the chrome gate instead of the thing it came to test.)
    Computed from the artifact rather than skipped, so these cases run on a
    checkout with no private data -- which is the checkout
    .github/workflows/tests.yml uses."""
    plans = rt._bpm_plans()
    if rt.hh.PATH.is_file():
        return rt.hh1("household.plan")
    winners = [{p for p, v in plans.items()
                if v[column] == min(x[column] for x in plans.values())}
               for column, _phrase in rt._BPM_COLUMNS]
    both = sorted(set.intersection(*winners))
    assert both, (
        f"data/battery_plan_matrix.json has no plan cheapest in both columns "
        f"({[sorted(w) for w in winners]}); these cases cannot stub one")
    return both[0]


@case
def case_a_numeric_format_spec_refuses_a_value_that_is_not_a_number():
    """ROUND 5, FINDINGS 1 AND 2. packages.MID's battery-alone saving and
    battery_plan_matrix's battery_value cell had no finiteness guard anywhere:
    the first published "-$nan" (usd0_signed's `v >= 0` test is False for a
    nan, so the NEGATIVE branch ran and manufactured a minus on a non-number)
    and "$inf"; the second went out past a gate that had just finiteness-
    checked six neighbouring numbers and never looked at the cell it returns.

    A token declared with a numeric format spec is ASSERTING its field is a
    number, so resolve_token holds it to that for EVERY leaf token of every
    kind, rather than one exit per review round. Driven here on the two the
    review named, on a leaf of each remaining kind, and on the shape that made
    finding 1 worse than a bare "$nan"."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    plans = rt._json("battery_plan_matrix.json")["plans"]
    plan = _matrix_winning_plan()
    checked = {}
    with _stub_household({"household.plan": plan}):
        published = {n: rt.resolve_token(n)
                     for n in ("BATTERY_MARGINAL_SAVINGS", "BATTERY_VALUE_BEST_PLAN")}
        for bad in (float("nan"), float("inf"), float("-inf")):
            with _mid_battery_at(mid, mid["battery_alone_yr"], bad):
                try:
                    value = rt.resolve_token("BATTERY_MARGINAL_SAVINGS")
                    raise AssertionError(
                        f"BATTERY_MARGINAL_SAVINGS rendered {value!r} off a {bad} "
                        "battery-alone saving")
                except SystemExit as e:
                    assert "BATTERY_MARGINAL_SAVINGS" in str(e), e
            with _swapped(plans[plan], "battery_value", bad):
                try:
                    value = rt.resolve_token("BATTERY_VALUE_BEST_PLAN")
                    raise AssertionError(
                        f"BATTERY_VALUE_BEST_PLAN rendered {value!r} off a {bad} cell")
                except SystemExit as e:
                    assert "BATTERY_VALUE_BEST_PLAN" in str(e), e
                    assert "battery_value" in str(e), (
                        f"the refusal does not name the cell it is about: {e}")
            checked[str(bad)] = "refused"
        assert all(rt.resolve_token(n) == v for n, v in published.items()), (
            "the substituted figures leaked out of this case")

    # The formatter itself, at the value that made finding 1 worse than a
    # plain "$nan": the sign test used to select the negative branch.
    for fmt in ("usd0_signed", "usd0_tilde_signed", "usd0_plus"):
        try:
            out = rt.FORMATTERS[fmt](float("nan"))
            raise AssertionError(f"FORMATTERS[{fmt!r}](nan) rendered {out!r}")
        except SystemExit as e:
            assert "nan" in str(e), e

    # And the gate is not special-cased to currency: every numeric spec.
    for fmt in sorted(set(rt.FORMATTERS) - rt._NON_NUMERIC_FMTS):
        spec = {"kind": "cited_constant", "value": float("nan"), "fmt": fmt}
        try:
            out = rt.resolve_token("ZZZ_FABRICATED_NONFINITE_TOKEN", spec)
            raise AssertionError(f"a cited_constant of nan rendered {out!r} through {fmt}")
        except SystemExit as e:
            assert "ZZZ_FABRICATED_NONFINITE_TOKEN" in str(e), e
    return ("BATTERY_MARGINAL_SAVINGS and BATTERY_VALUE_BEST_PLAN fail closed naming "
            f"themselves on nan/inf/-inf, and all "
            f"{len(set(rt.FORMATTERS) - rt._NON_NUMERIC_FMTS)} numeric format specs "
            "refuse a non-number rather than rendering one")


@case
def case_an_unusable_package_cost_only_stops_the_figures_that_need_it():
    """ROUND 5, FINDING 4. Round 4 put the packages.MID.cost check at the top
    of _battery_alone, unconditionally, so a household whose artifact carries
    an unusable cost lost the ENTIRE report -- including the households where
    nothing downstream quotes a payback at all, because the battery does not
    repay and both verdicts read "never repays its own cost".

    The cost is the numerator of a payback, so it gates PAYBACKS. Driven both
    ways: with a payback in play it still refuses (the round-4 property, which
    must not regress), and with none in play the report renders."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    pk = rt._json("package_results.json")["packages"]
    mid, high_cost = pk["MID"], pk["HIGH"]["cost"]
    rendered, refused = {}, {}
    with _stub_plan(cheapest, provider):
        for bad in (float("nan"), 0, -14500):
            # 1. A battery that does not repay: no payback is printed anywhere,
            #    so an unusable cost is not a reason to withhold the report.
            #
            #    S7_VERDICT is included only where the HIGH-vs-MID expansion
            #    cost it computes off the same field is still usable. A nan MID
            #    cost makes that DIFFERENCE a nan too, and section 7 has
            #    refused on it since round 2 -- a separate, correct guard this
            #    case must not claim credit for either way.
            tokens = ("S0_VERDICT", "S7_VERDICT") if high_cost - bad > 0 else ("S0_VERDICT",)
            with _mid_battery_at(mid, -100, -400), _swapped(mid, "cost", bad):
                for token in tokens:
                    value = rt.resolve_token(token)
                    assert "repay" in value, value
                    rendered[f"{token} at cost {bad}"] = _assert_within_density_cap(
                        token, value, f"an unusable cost of {bad}")
                try:
                    card = rt.resolve_token("BATTERY_PAYBACK_RANGE")
                    raise AssertionError(
                        f"the section 0 card printed {card!r} for a battery that repays "
                        "in neither scenario")
                except SystemExit as e:
                    assert "BATTERY_PAYBACK_RANGE" in str(e), e

            # 2. A battery that DOES repay: a payback is about to be printed
            #    with no verified numerator behind it, and every caller refuses.
            with _swapped(mid, "cost", bad):
                for token in ("S0_VERDICT", "S7_VERDICT", "BATTERY_PAYBACK_RANGE"):
                    try:
                        value = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} quoted a payback off an unusable cost of {bad}: "
                            f"{value}")
                    except SystemExit as e:
                        assert token in str(e) and "cost" in str(e), e
                        refused[f"{token} at cost {bad}"] = True
    return (f"an unusable packages.MID.cost refuses every figure that needs it "
            f"({len(refused)} refusals) and withholds none that does not "
            f"({len(rendered)} sentences still render)")


@case
def case_the_evening_only_payback_is_checked_like_the_card_it_shares():
    """ROUND 5, FINDING 5. report-template.html renders BATTERY_PAYBACK_RANGE
    and BATTERY_PAYBACK_EVENING_ONLY into ONE section 0 card -- "Battery-alone
    payback with price-aware dispatch (8.4 yr evening-only)". Round 4 guarded
    the first and left the second a bare data_json read, so half the card was
    a checked quotient and half was whatever the artifact happened to say.

    analysis/package_results.py writes it as round(packages.MID.cost /
    battery_dispatch_policies.json's pw3.evening.save, 1), so all three
    numbers are committed and the derivation is checkable. Driven on a
    non-finite and non-positive payback, on a saving that cannot produce one,
    on an unusable cost, and on a pair that no longer divides."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    evening = rt._json("battery_dispatch_policies.json")["pw3"]["evening"]
    published = rt.resolve_token("BATTERY_PAYBACK_EVENING_ONLY")
    assert published == f"{mid['battery_alone_payback_evening_only_yr']:.1f} yr", published

    checked = []
    for node, key, bad in (
            (mid, "battery_alone_payback_evening_only_yr", float("nan")),
            (mid, "battery_alone_payback_evening_only_yr", float("inf")),
            (mid, "battery_alone_payback_evening_only_yr", 0),
            (mid, "battery_alone_payback_evening_only_yr", -8.4),
            (mid, "cost", float("nan")),
            (mid, "cost", 0),
            (evening, "save", float("nan")),
            (evening, "save", 0),
            (evening, "save", -1720),
            # ...and the pair that no longer divides: a real saving whose
            # committed payback is not cost/saving at all.
            (evening, "save", mid["cost"] / (mid["battery_alone_payback_evening_only_yr"] * 3)),
    ):
        with _swapped(node, key, bad):
            try:
                value = rt.resolve_token("BATTERY_PAYBACK_EVENING_ONLY")
                raise AssertionError(
                    f"the evening-only payback rendered {value!r} with {key} = {bad}")
            except SystemExit as e:
                assert "BATTERY_PAYBACK_EVENING_ONLY" in str(e), e
                checked.append(f"{key}={bad}")

    # It still renders on a household whose evening-only payback is simply
    # different, so this case cannot pass on a token that always refuses.
    doubled = mid["cost"] / (evening["save"] / 2)
    with _swapped(evening, "save", evening["save"] / 2), \
            _swapped(mid, "battery_alone_payback_evening_only_yr", round(doubled, 1)):
        value = rt.resolve_token("BATTERY_PAYBACK_EVENING_ONLY")
    assert value == f"{round(doubled, 1):.1f} yr", value
    assert rt.resolve_token("BATTERY_PAYBACK_EVENING_ONLY") == published, (
        "the substituted figures leaked out of this case")
    return (f"the evening-only half of the section 0 card reads {published!r}, refuses "
            f"{len(checked)} artifact states that cannot produce a payback "
            f"({', '.join(checked[:4])}, ...), and still renders {value!r} on a slower "
            "evening-only schedule")


@case
def case_section_five_refuses_a_non_finite_share_instead_of_crashing():
    """ROUND 5, FINDING 6. Round 4 swept this module for non-finite inputs and
    skipped section 5's comparison, because its branches read as a ROUNDING
    rather than a division. round(float('inf') * 100) does not return an
    infinity -- it raises OverflowError -- and OverflowError was not in
    resolve_token's caught tuple, so a non-finite share crashed the whole
    generator with a bare traceback instead of a named refusal.

    Both halves are asserted: the named refusal at the site, and
    resolve_token's own floor, which now catches ArithmeticError so a division
    or rounding anywhere in this module cannot escape as a raw traceback."""
    rd = rt._json("report_data.json")
    published = rt.resolve_token("S5_VERDICT")
    checked = []
    for node, key, bad in ((rd["periods_chart"]["import_share"], 2, float("inf")),
                           (rd["periods_chart"]["import_share"], 2, float("nan")),
                           (rd["periods_chart"]["import_share"], 2, float("-inf")),
                           (rd["onpeak"], "share_of_energy_cost", float("inf")),
                           (rd["onpeak"], "share_of_energy_cost", float("nan")),
                           (rd["onpeak"], "share_of_energy_cost", float("-inf"))):
        with _swapped(node, key, bad):
            try:
                value = rt.resolve_token("S5_VERDICT")
                raise AssertionError(f"S5_VERDICT rendered {value!r} off a {bad} share")
            except SystemExit as e:
                assert "S5_VERDICT" in str(e), e
                assert "share" in str(e), (
                    f"the refusal does not name which share was not a number: {e}")
                checked.append(f"{key}={bad}")

    # The floor beneath it: an ArithmeticError raised anywhere inside a
    # derived formula becomes this module's named refusal, not a traceback.
    def boom(ctx):
        return round(float("inf"))

    try:
        rt.resolve_token("ZZZ_FABRICATED_OVERFLOW_TOKEN",
                         {"kind": "derived", "get": boom, "fmt": None})
        raise AssertionError("an OverflowError escaped resolve_token")
    except SystemExit as e:
        assert "ZZZ_FABRICATED_OVERFLOW_TOKEN" in str(e) and "OverflowError" in str(e), e
    assert rt.resolve_token("S5_VERDICT") == published, (
        "the substituted shares leaked out of this case")
    return (f"S5_VERDICT names the share it cannot compare ({', '.join(checked)}) and "
            "resolve_token converts an OverflowError into a named refusal instead of a "
            "traceback")


@case
def case_negative_zero_is_zero_and_a_non_finite_npv_is_not_a_zero_gain():
    """ROUND 5, FINDINGS 7 AND 8, the two defects in the sign formatters
    themselves.

    FINDING 7: _usd0_signed tested `v >= 0`, which is True for -0.0, so a
    difference landing on the negative side of an exact zero took the
    non-negative branch and printed _usd0(-0.0) -- "$-0", the minus back
    inside the sigil, in the one formatter written to keep it out.

    FINDING 8: _usd0_plus ended in a bare `else` returning _usd0(0), and a nan
    reaches it because `nan > 0` and `nan < 0` are both False. An
    indeterminate net present value published as the confident figure "$0" is
    the opposite reading for someone deciding whether to buy."""
    assert rt.FORMATTERS["usd0_signed"](-0.0) == "$0", rt.FORMATTERS["usd0_signed"](-0.0)
    assert rt.FORMATTERS["usd0_signed"](0.0) == "$0"
    assert rt.FORMATTERS["usd0_tilde_signed"](-0.0) == "~$0"
    assert rt.FORMATTERS["usd0_plus"](-0.0) == "$0"
    # -0.0 must still be told apart from a real negative, including one small
    # enough to round to zero: that keeps its minus, OUTSIDE the sigil, which
    # is the whole distinction (-0.0 is zero; -0.4 is a loss).
    assert rt.FORMATTERS["usd0_signed"](-0.4) == "-$0", rt.FORMATTERS["usd0_signed"](-0.4)
    assert rt.FORMATTERS["usd0_signed"](-500) == "-$500"
    assert rt.FORMATTERS["usd0_plus"](-500) == "-$500"

    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            out = rt.FORMATTERS["usd0_plus"](bad)
            raise AssertionError(f"usd0_plus({bad}) rendered {out!r}")
        except SystemExit as e:
            assert "usd0_plus" in str(e), e

    # End to end, on the NPV token that calls _usd0_plus inline -- the route
    # resolve_token's own numeric gate does not cover, because the formula
    # returns an already-formatted string.
    rung = rt._json("battery_dispatch_policies.json")[
        "escalation_greedy_pw3_post_behavior"]["8%"]
    published = rt.resolve_token("NPV_AT_HISTORICAL_ESCALATION")
    with _swapped(rung, "npv10", float("nan")):
        try:
            value = rt.resolve_token("NPV_AT_HISTORICAL_ESCALATION")
            raise AssertionError(f"a nan 10-year NPV published as {value!r}")
        except SystemExit as e:
            assert "NPV_AT_HISTORICAL_ESCALATION" in str(e), e
    with _swapped(rung, "npv10", -8656):
        assert rt.resolve_token("NPV_AT_HISTORICAL_ESCALATION") == "-$8,656"
    assert rt.resolve_token("NPV_AT_HISTORICAL_ESCALATION") == published, (
        "the substituted NPV leaked out of this case")
    return ("-0.0 renders \"$0\" through all three signed formatters, a non-finite NPV "
            f"fails closed instead of publishing \"$0\", and a real loss reads -$8,656 "
            f"(published: {published})")


@case
def case_the_two_figures_for_the_batterys_own_saving_quote_one_scenario():
    """ROUND 5, FINDING 10. BATTERY_MARGINAL_SAVINGS read
    packages.MID.battery_alone_yr -- the battery on the UNSHIFTED baseline --
    while section 7's verdict had been re-based onto
    battery_alone_post_ev_fix_yr, the same battery after the free EV fix. Two
    figures for one purchase in one document: $2,328 and $2,238 on the
    committed archive, and a healthy saving beside a loss on the household
    round 4's own finding 1 introduced (+$2,328 before the fix, -$50 after).
    Round 4's sign gate used to catch that pair by aborting the report;
    removing it -- correctly, since the two are different scenarios -- left
    the mismatch with nothing behind it.

    This is a PAIRING defect, not a non-finite one, so the poison harness
    above cannot see it: both readings are perfectly well-formed strings. It
    is asserted as the property instead -- the token and the sentence move
    together, on every pair -- which is what the sign gate was standing in
    for and what CLAUDE.md section 3 requires of a re-based figure."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    mid = rt._json("package_results.json")["packages"]["MID"]
    live = (mid["battery_alone_yr"], mid["battery_alone_post_ev_fix_yr"])
    assert live[0] != live[1], (
        f"packages.MID's two battery-alone scenarios are both {live[0]}; this case "
        "cannot tell which one the token reads")
    seen = []
    with _stub_plan(cheapest, provider):
        for pre, post in ((live[0], live[1]), (2328, 2238), (500, 3000), (3000, 500)):
            with _mid_battery_at(mid, pre, post):
                token = rt.resolve_token("BATTERY_MARGINAL_SAVINGS")
                s7 = rt.resolve_token("S7_VERDICT")
            assert token == rt.FORMATTERS["usd0_signed"](post), (
                f"BATTERY_MARGINAL_SAVINGS reads {token!r} while the post-EV-fix "
                f"scenario section 7 is written on is {post}/yr")
            assert f"adds its own {token}/yr" in s7, (
                f"section 7 quotes a different figure for the same battery than "
                f"BATTERY_MARGINAL_SAVINGS does ({token!r}): {s7}")
            seen.append(f"{pre}/{post} -> {token}")
        # A losing battery: the token states the loss and the sentence agrees
        # it never repays, rather than the token selling a pre-fix saving.
        with _mid_battery_at(mid, 2328, -50):
            token = rt.resolve_token("BATTERY_MARGINAL_SAVINGS")
            s7 = rt.resolve_token("S7_VERDICT")
        assert token == "-$50", token
        assert "never repays its own cost" in s7, s7
        seen.append(f"2328/-50 -> {token}")
    return ("BATTERY_MARGINAL_SAVINGS and section 7's battery clause quote the same "
            f"post-EV-fix scenario on every pair ({'; '.join(seen)})")


# --- the round-6 findings, one regression case each -------------------------
@contextlib.contextmanager
def _csv_column_set(who, column, value, where=lambda row: True):
    """One committed CSV column (or the cells of it a predicate picks) set to
    `value` in report_tokens' in-memory cache, restored on the way out.
    Nothing on disk is touched -- data/ is a committed artifact."""
    rows = rt._csv_rows(who)
    original = [r[column] for r in rows]
    try:
        for row in rows:
            if where(row):
                row[column] = value
        yield rows
    finally:
        for row, was in zip(rows, original):
            row[column] = was


@case
def case_section_two_checks_the_production_it_prints_not_only_the_nameplate():
    """ROUND 6, FINDING 2. _s2_verdict validated solar.kw_dc through _figures
    and read the production total beside it unchecked -- so BOTH interpolated
    figures went past the finiteness gate, and a non-finite Total footer in
    data/enphase_daily_production.csv published "produced nan kWh at nan
    kWh/kW" while ANNUAL_PRODUCTION_KWH, the LEAF token over that same cell,
    failed closed on it. Two tokens, one cell, opposite behaviour.

    CAPACITY_FACTOR had the identical shape one file over -- kw_dc checked,
    the production quotient interpolated into an f-string as "~nan%" -- and is
    driven here too, because a defect found at one site is swept for rather
    than patched (CLAUDE.md section 8).

    Calibration, kept honest: a full generate_report run could not PUBLISH the
    nan, because it resolves every token and blocks the write on any non-gap
    failure. What was broken is the per-token contract -- one figure checked,
    the one beside it not -- and that is what is asserted."""
    stubs = _s2_household_inputs()
    footer = (lambda r: r["Date/Time"] == "Total")
    names = ("ANNUAL_PRODUCTION_KWH", "CAPACITY_FACTOR", "S2_VERDICT")
    with _stub_household(stubs):
        published = {n: rt.resolve_token(n) for n in names}
    assert "kWh/kW" in published["S2_VERDICT"], published["S2_VERDICT"]

    checked = []
    for bad in ("nan", "inf", "-inf"):
        with _csv_column_set("enphase_daily_production.csv",
                             "Energy Delivered (kWh)", bad, footer):
            for name in names:
                with _stub_household(stubs):
                    try:
                        value = rt.resolve_token(name)
                        raise AssertionError(
                            f"{name} rendered {value!r} off a {bad} production total")
                    except SystemExit as e:
                        assert name in str(e), e
                checked.append(f"{name}@{bad}")

    # A household that simply produced a different amount still gets its
    # sentence, so none of the three can pass by always refusing.
    with _csv_column_set("enphase_daily_production.csv",
                         "Energy Delivered (kWh)", "9000.00", footer), \
            _stub_household(stubs):
        moved = {n: rt.resolve_token(n) for n in names}
    assert moved["ANNUAL_PRODUCTION_KWH"] == "9,000", moved["ANNUAL_PRODUCTION_KWH"]
    assert "9,000 kWh" in moved["S2_VERDICT"], moved["S2_VERDICT"]
    assert moved["CAPACITY_FACTOR"] != published["CAPACITY_FACTOR"]
    with _stub_household(stubs):
        assert {n: rt.resolve_token(n) for n in names} == published, (
            "the substituted production total leaked out of this case")
    return (f"section 2's verdict and CAPACITY_FACTOR check the production they print, "
            f"not only the nameplate ({len(checked)} refusals, each naming its own "
            f"token), and all three still render on a 9,000 kWh year")


@case
def case_a_plan_total_that_models_below_zero_still_gets_a_report():
    """ROUND 6, FINDINGS 3 AND 5. Round 5 made _usd0 REFUSE a negative amount
    -- right, because "$-500" puts the minus inside the sigil -- but did not
    sweep the inline _usd0 calls that format an artifact-SIGNED annual bill.
    Those sites stopped rendering and started aborting.

    A plan total in data/plan_results.csv is a bill NET OF EXPORTS
    (BEST_PLAN_ANNUAL_CCA is declared usd0_signed for exactly that reason),
    and a solar-plus-battery house on the right tariff models below zero. Two
    populations were hit: section 3's verdict, in all three of its branches,
    and the DETAIL string of _best_plan's chrome gate -- which _claim builds
    eagerly on every call, so it fired even when the claim was supported. The
    result was a household losing the entire report to the formatting of a
    message that was never going to be raised.

    Same defect one artifact over, swept rather than reported: the same claim
    detail inside _best_plan_matrix_cell formats battery_plan_matrix.json's
    two modeled-bill columns, whose tokens are likewise declared signed."""
    provider, cheapest, priced = _plan_ranking_inputs()
    others = [r for r in priced if r["plan"] != cheapest]
    assert others, "this case needs a second priced plan"
    runner_up = others[0]["plan"]
    totals = {r["plan"]: r["total"] for r in priced}

    rendered = {}
    with _stub_plan(cheapest, provider):
        for label, prices, quoted in (
                ("sole cheapest", {cheapest: -5000.0, runner_up: -1200.0}, -5000.0),
                ("tied cheapest", {cheapest: -5000.0, runner_up: -5000.0}, -5000.0),
                ("beaten", {cheapest: -1200.0, runner_up: -5000.0}, -1200.0)):
            with _plan_repriced(provider, prices):
                s3 = rt.resolve_token("S3_VERDICT")
                # BEST_PLAN and its two annual cells, IN ALL THREE BRANCHES.
                # They used to be reachable only while this plan still won --
                # not because a beaten household has no plan name and no two
                # modeled bills, but because report-template.html asserted the
                # win in fixed chrome and _best_plan refused rather than fill
                # it. Issue #196 gave that chrome its own tokens, so the
                # beaten branch renders like the other two and the sweep this
                # case is FOR -- a bill below zero formatted with the minus
                # outside the sigil -- finally covers it.
                gated = {n: rt.resolve_token(n)
                         for n in ("BEST_PLAN", "BEST_PLAN_ANNUAL_CCA",
                                   "BEST_PLAN_ANNUAL_BUNDLED")}
            assert f"{quoted:,.0f}".replace("-", "-$") + "/yr" in s3, (
                f"section 3 does not state a modeled bill below zero on the {label} "
                f"branch: {s3}")
            assert "$-" not in s3, f"the minus went back inside the sigil: {s3}"
            rendered[f"S3 {label}"] = _assert_within_density_cap("S3_VERDICT", s3, label)
            assert gated["BEST_PLAN"] == cheapest, gated
            assert gated["BEST_PLAN_ANNUAL_CCA"].startswith("-$") or \
                gated["BEST_PLAN_ANNUAL_BUNDLED"].startswith("-$"), gated

    # The matrix's two modeled-bill columns, same sweep.
    plans = rt._json("battery_plan_matrix.json")["plans"]
    plan = _matrix_winning_plan()
    with _stub_plan(plan, provider), \
            _swapped(plans[plan], "no_battery", -4100.0), \
            _swapped(plans[plan], "with_battery", -6200.0):
        matrix = {n: rt.resolve_token(n)
                  for n in ("BEST_PLAN_NOBATT_MODELED", "BEST_PLAN_BATT_MODELED")}
    assert matrix["BEST_PLAN_NOBATT_MODELED"] == "-$4,100", matrix
    assert matrix["BEST_PLAN_BATT_MODELED"] == "-$6,200", matrix

    # Nothing leaked, and the published sentence is unchanged.
    with _stub_plan(cheapest, provider):
        live = rt.resolve_token("S3_VERDICT")
    assert f"{float(totals[cheapest]):,.0f}" in live.replace("$", ""), live
    return ("a plan total that models below zero renders as -$5,000/yr in all three of "
            f"section 3's branches ({', '.join(rendered)}) and in the battery-vs-plan "
            "matrix's two modeled-bill cells, instead of aborting the whole report "
            "inside a refusal message's own formatting")


@case
def case_a_battery_repaying_past_its_warranty_is_not_called_a_sound_buy():
    """ROUND 6, FINDING 6. Section 0's "is a sound optional buy" was selected
    by battery_alone_post_ev_fix_yr > 0 and nothing else, so a battery saving
    $50/yr against a ~$14,500 purchase read as a sound buy in the report's
    most prominent sentence, with a 290-year payback printed beside it.
    Nothing weighed magnitude or payback against cost; a sign test answers
    "does this repay at all", which is a different question from the one the
    word "sound" answers.

    The horizon is READ, not invented (CLAUDE.md section 0):
    data/uncertainty_results.json:meta.warranty_yr is what
    analysis/uncertainty_propagation.py's WARRANTY_YR writes, the term that
    module already reports the battery's headline probability against and the
    term data/battery_sizing_curve.json already stops sizing at.

    Driven on both sides of the boundary and ON it, because a threshold with
    only one side exercised is a threshold nobody has checked."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    mid = rt._json("package_results.json")["packages"]["MID"]
    cost, warranty = mid["cost"], rt._battery_warranty_years("PROBE")
    assert warranty == rt._json("uncertainty_results.json")["meta"]["warranty_yr"]
    widths, seen = {}, {}
    with _stub_plan(cheapest, provider):
        published = rt.resolve_token("S0_VERDICT")
        assert "sound optional buy" in published, published

        # A saving that repays only long past the warranted life.
        with _mid_battery_at(mid, 50.0, 50.0):
            slow = rt.resolve_token("S0_VERDICT")
        assert "sound optional buy" not in slow, (
            f"S0_VERDICT calls a battery repaying in {cost / 50:.0f} years a sound "
            f"optional buy: {slow}")
        assert f"past its {warranty:g}-year warranty" in slow, slow
        assert f"{cost / 50:.1f} years" in slow, slow
        widths["past warranty"] = _assert_within_density_cap(
            "S0_VERDICT", slow, "a battery repaying past its warranty")
        seen["past warranty"] = slow

        # The boundary itself: exactly at the warranty term is still sound,
        # a tenth of a year past it is not.
        for label, payback, sound in (("exactly at", warranty, True),
                                      ("just past", warranty + 0.1, False)):
            save = cost / payback
            with _mid_battery_at(mid, save, save):
                value = rt.resolve_token("S0_VERDICT")
            assert ("sound optional buy" in value) is sound, (
                f"a {payback}-year payback against a {warranty}-year warranty read "
                f"{'un' if sound else ''}sound: {value}")
            widths[label] = _assert_within_density_cap("S0_VERDICT", value, label)
            seen[label] = value

        # And it is the WHOLE printed range that has to clear the term, not
        # the friendlier end of it.
        fast, slow_save = cost / (warranty - 2), cost / (warranty + 5)
        with _mid_battery_at(mid, fast, slow_save):
            mixed = rt.resolve_token("S0_VERDICT")
        assert "sound optional buy" not in mixed, (
            f"S0_VERDICT calls the purchase sound while the top of the range it prints "
            f"is past the warranty: {mixed}")
        widths["range straddling"] = _assert_within_density_cap(
            "S0_VERDICT", mixed, "a range straddling the warranty term")

        # A warranty term the artifact cannot state is a refusal, not a
        # silently-assumed number.
        meta = rt._json("uncertainty_results.json")["meta"]
        for bad in (float("nan"), 0, -10):
            with _swapped(meta, "warranty_yr", bad):
                try:
                    value = rt.resolve_token("S0_VERDICT")
                    raise AssertionError(
                        f"S0_VERDICT judged the purchase against a {bad}-year warranty: "
                        f"{value}")
                except SystemExit as e:
                    assert "S0_VERDICT" in str(e) and "warrant" in str(e), e
        assert rt.resolve_token("S0_VERDICT") == published, (
            "the substituted battery figures leaked out of this case")
    return ("section 0 calls the battery a sound optional buy only where every payback "
            f"it prints lands inside the {warranty:g}-year warranted life "
            f"(at {warranty:g} yr: sound; at {warranty + 0.1:g} yr: not), and states the "
            "payback plainly otherwise: "
            + seen["past warranty"].split("and a ")[-1].rstrip("."))


@case
def case_the_expansion_tail_compares_two_paybacks_on_one_basis():
    """ROUND 6, FINDING 7. Section 7's sentence prints the first unit's
    payback rounded -- "(~6.5-yr payback)" -- and then compared an EXACT
    quotient against that rounded figure to choose its tail. Near the boundary
    the two sides were not the same quantity: an expansion repaying in 6.54 yr
    beside a printed 6.5 read "saves too little to match that" while both
    figures ON THE PAGE said 6.5, and 6.46 read "faster than that" on the same
    evidence. The tie branch, written precisely so an expansion repaying at
    the first unit's rate is not called faster, was reachable only on
    exactly-equal floats.

    Both sides now round to the tenth of a year the sentence publishes, so the
    comparison is the one a reader can check against the two numbers printed
    in front of them."""
    pk = rt._json("package_results.json")["packages"]
    exp_cost = pk["HIGH"]["cost"] - pk["MID"]["cost"]
    mid_payback = pk["MID"]["battery_alone_payback_post_fix_yr"]
    assert exp_cost > 0 and mid_payback > 0, (exp_cost, mid_payback)
    printed = round(mid_payback, 1)
    seen = {}
    for label, payback, expected in (
            ("0.04 yr slower — same printed figure", printed + 0.04,
             "pays back at the same rate"),
            ("0.04 yr faster — same printed figure", printed - 0.04,
             "pays back at the same rate"),
            ("exactly equal", printed, "pays back at the same rate"),
            ("0.3 yr slower", printed + 0.3, "saves too little to match that"),
            ("0.3 yr faster", printed - 0.3, "pays back faster than that")):
        with _swapped(pk["HIGH"], "marginal_vs_mid_yr", exp_cost / payback):
            value = rt.resolve_token("S7_VERDICT")
        assert expected in value, (
            f"an expansion repaying in {payback:.2f} yr against a printed "
            f"{printed:.1f}-yr first unit reads {value!r}, not {expected!r}")
        assert f"(~{printed:.1f}-yr payback)" in value, value
        _assert_within_density_cap("S7_VERDICT", value, label)
        seen[label] = expected
    return ("section 7's comparative tail rounds both paybacks to the tenth of a year it "
            f"prints, so an expansion within 0.04 yr of the first unit's {printed:.1f} "
            "ties instead of being ranked on digits the reader is never shown "
            f"({len(seen)} branches driven)")


@case
def case_a_verdict_published_in_the_wrong_section_fails_the_index_check():
    """ROUND 6, FINDING 8. _assert_verdict_matches_index asserted the rendered
    line appeared SOMEWHERE in index.html -- a membership test over the whole
    report. A verdict moved under a neighbouring heading, or duplicated into
    two sections, satisfied it exactly as well as one sitting where it
    belongs, and "section 7's conclusion is on the page somewhere" is not the
    claim the case exists to make.

    Driven on the real published document, mutated three ways in memory: the
    line moved out of its own section, deleted from it, and duplicated. Each
    must fail, and the unmutated document must pass -- otherwise the check
    would be rejecting everything rather than the right things."""
    index_html = (rt.ROOT / "index.html").read_text()
    name = "S7_VERDICT"     # artifact-only, so this runs without the archive
    assert not _needs_household(name), f"{name} is no longer archive-free"
    value = _assert_verdict_matches_index(name, index_html)
    line = f'<p class="verdict">{_htmllib.escape(value, quote=True)}</p>'
    assert index_html.count(line) == 1, line

    mutations = {
        # Moved: deleted from section 7, published under section 6 instead.
        "moved to another section": (
            index_html.replace(line, "", 1)
            .replace('<h2 id="s6">', line + '<h2 id="s6">', 1)),
        "deleted from its section": index_html.replace(line, "", 1),
        # Duplicated: still present where it belongs, and again elsewhere.
        "duplicated into another section": index_html.replace(
            '<h2 id="s6">', line + '<h2 id="s6">', 1),
    }
    caught = {}
    for label, mutated in mutations.items():
        assert mutated != index_html, f"the {label} mutation did not change anything"
        # The "it should have failed" assertion is raised OUTSIDE the except
        # clause on purpose: raising AssertionError inside a `try` whose
        # handler catches AssertionError swallows it, and the case then passes
        # on exactly the defect it was written to catch.
        rejection = None
        try:
            _assert_verdict_matches_index(name, mutated)
        except AssertionError as e:
            rejection = str(e).splitlines()[0]
            assert name in rejection, rejection
        assert rejection is not None, (
            f"{name} passed its index.html check with the published line {label}")
        caught[label] = rejection
    # The section slicer is what makes that possible, so pin its behaviour.
    section = _index_section(index_html, "s7")
    assert section.startswith('<h2 id="s7"'), section[:60]
    assert '<h2 id="s15"' not in section, "section 7's slice runs into the next section"
    assert line in section
    return (f"a verdict line moved, deleted or duplicated is caught by name "
            f"({len(caught)} mutations, each rejected), and _index_section slices "
            "section 7 at its own heading and the next one")


@case
def case_the_sweep_asserts_the_tokens_it_could_not_drive():
    """ROUND 6, FINDING 9. The poison harness's `unresolvable` bucket -- every
    token this checkout cannot resolve, and therefore cannot sweep -- was
    asserted on only inside `if rt.hh.PATH.is_file()`, where it is always
    empty. On the runner .github/workflows/tests.yml actually uses, which has
    no private archive and holds out 43 tokens, the bucket that holds every
    lost token was never examined. Coverage could shrink to a handful of
    tokens while the case reported the same success -- finding 1's shape,
    one bucket over.

    _unresolvable_gaps is now asserted on EVERY checkout, and a token may only
    drop out through the one door: its refusal names household.yaml AND it was
    observed reading a household path on the way to it."""
    def refuses(ctx):
        raise SystemExit("report_tokens: ZZZ_FABRICATED_SULKING_TOKEN is sulking")

    def refuses_by_name_only(ctx):
        raise SystemExit("report_tokens: ZZZ_FABRICATED_LIAR_TOKEN cannot read "
                         "private/household.yaml, it says, having never opened it")

    fabricated = {"ZZZ_FABRICATED_SULKING_TOKEN": {"kind": "derived", "get": refuses,
                                                   "fmt": None},
                  "ZZZ_FABRICATED_LIAR_TOKEN": {"kind": "derived",
                                                "get": refuses_by_name_only, "fmt": None}}
    sw = _run_poison_sweep(fabricated)
    assert set(sw.unresolvable) == set(fabricated), sorted(sw.unresolvable)
    gaps = _unresolvable_gaps(sw)
    assert set(gaps) == set(fabricated), (
        "a token that dropped out of the sweep for a reason other than the absent "
        f"private archive was not reported as a coverage gap: {gaps}")

    # And a genuinely household-blocked token IS allowed through the door --
    # otherwise this check would just reject everything on the CI runner.
    real = _run_poison_sweep()
    assert not _unresolvable_gaps(real), _unresolvable_gaps(real)
    if rt.hh.PATH.is_file():
        assert not real.unresolvable, sorted(real.unresolvable)
        note = "no token is held out on this staged archive"
    else:
        assert real.unresolvable, (
            "no token is held out without a private archive; this checkout is not the "
            "one the assertion is written for")
        note = (f"{len(real.unresolvable)} token(s) held out, every one proved to be "
                "household-blocked")
    return ("a token that leaves the sweep for any reason other than the absent private "
            f"archive is a named coverage gap on every checkout ({note})")


@case
def case_the_poison_harness_does_not_claim_findings_it_does_not_close():
    """ROUND 6, FINDING 4. The sweep's docstring claimed it closed round 5's
    findings 1, 2, 5, 6, 7 and 8. Its contract is on the SHAPE of the rendered
    string, so what it actually closes is the malformed-number class; findings
    4 and 10 are a too-broad refusal and a mismatched pair, and neither is
    visible to it -- both readings are well-formed strings.

    A docstring is a claim about the code like any other, so it is checked
    like one rather than trusted: the companion cases it now names must exist,
    and the words that were doing the overclaiming must be gone."""
    doc = case_no_token_publishes_a_malformed_number_from_any_poisoned_artifact_field.__doc__
    listed = {fn.__name__ for fn in CASES}
    named = re.findall(r"case_[a-z0-9_]+", doc)
    assert named, "the docstring no longer points at the cases that carry the rest"
    missing = [n for n in named if n not in listed]
    assert not missing, (
        f"the sweep's docstring names case(s) that do not exist: {missing}")
    assert "does NOT close" in doc, (
        "the docstring must state the classes this sweep cannot see, or it is back to "
        "claiming the whole review round")
    for overclaim in ("findings 1, 2, 5, 6, 7 and 8", "closes it for tokens"):
        assert overclaim not in doc or "does NOT close" in doc, doc
    # The three companion cases named are the ones that really carry those
    # findings, so the claim is true rather than merely present.
    for name in ("case_the_two_figures_for_the_batterys_own_saving_quote_one_scenario",
                 "case_an_unusable_package_cost_only_stops_the_figures_that_need_it"):
        assert name in named, f"the docstring no longer credits {name}"
    return (f"the poison sweep's docstring names the class it closes and the "
            f"{len(named)} case(s) that carry the classes it cannot see, all of which "
            "exist")


# ---------------------------------------------------------------------------
# ISSUE #133: THE RENDER-TIME SEAM GUARD.
#
# Every case above this one asks whether a token RESOLVES. None of them asks
# what the resolved value looks like once it is substituted into the template
# text that surrounds it -- and that is exactly where issue #129's defects
# lived. `~{{BATTERY_COST}}` resolves perfectly; it RENDERS "~~$14,500". The
# committed index.html never showed any of them because it was hand-authored
# rather than mechanically filled, so nothing in this repo caught them. Any
# external reproducer following the documented pipeline shipped all three.
#
# This guard substitutes every {{TOKEN}} occurrence into its own template line
# and inspects the RESULT for three defect classes:
#
#   1 DOUBLED SIGIL / DOUBLED UNIT -- the template supplies a sigil or a unit
#     the value already carries at that end:
#       "~{{BATTERY_COST}}"          -> "~~$14,500"
#       "{{SOILING_RATE_RANGE}}/month" -> "0.4-2.4%/month/month"
#   2 MISSING UNIT -- a bare-number value lands where neither it nor the prose
#     around it supplies a unit:
#       "covers {{SOLAR_COVERAGE_PCT}} of" -> "covers 55 of the home's ..."
#   3 ECHOED PHRASE -- the text immediately after a value repeats the value's
#     own tail, so one figure is printed twice:
#       "{{INVERTER_DESCRIPTION}} (= {{AC_CEILING_KW}} kW AC max)"
#       -> "... ~315 VA each = 9.45 kW AC max) (= 9.45 kW AC max)"
#
# GENERIC BY CONSTRUCTION, not a case per token. The occurrence list is parsed
# out of report-template.html, the values come from report_tokens' own
# resolver, and all three rules are properties of the (value, surrounding
# text) pair. A token added to the template tomorrow is checked tomorrow with
# no edit here. Every _SEAM_ALLOWLIST entry is one of class 2's dimensionless
# counts and years, and each names ONE occurrence; the case below asserts that
# every entry STILL HAS the seam it excuses and that no entry pardons more than
# the one occurrence it names -- so an exception whose seam has since been fixed
# cannot linger as a silent loophole, and one that was written for a different
# line cannot cover a real defect. No count of the entries is written down
# here: an earlier draft said "five" and the dict had six by the time anyone
# read it again. The live number is reported on every run by
# case_no_token_renders_a_broken_seam_in_its_own_template_context, which counts
# _SEAM_ALLOWLIST rather than restating it.
#
# WHAT THESE RULES DELIBERATELY DO NOT FLAG. Each of the three is heuristic,
# and the brief for this work is explicit that a conservative rule with a
# stated blind spot beats a clever one that cries wolf. Stated, therefore:
#
#   * Class 1 requires the template's sigil to be IMMEDIATELY adjacent (no
#     space) to the token. "~ {{X}}" with a value of "~$14,500" is not
#     flagged. Units are allowed one optional space ("{{X}} kWh/yr" against a
#     value ending "kWh/yr" IS flagged), because that spacing is normal
#     typography for a unit and never for a sigil.
#   * Class 1 matches units case-INsensitively ("{{X}} kWh" against a value
#     ending "KWH" is the same defect), but only on a WORD BOUNDARY at both
#     ends: the unit may not be the tail of a longer word in the value
#     ("1,234 kWh" does not end in the unit "h") nor the head of a longer word
#     in the template ("{{X}} hours later" does not supply the unit "h").
#     Without that boundary the short units cry wolf on ordinary prose.
#   * That boundary is a BLIND SPOT as well as a guard, and so is class 1's
#     strict adjacency for sigils. These misses are measured, not supposed: a
#     template that spells the unit out, inflects it, joins it with a hyphen or
#     writes the sigil as a word prints the same dimension twice and is NOT
#     flagged.
#         "{{X}} percent"        value "55%"        -> "55% percent"
#         "{{X}} dollars"        value "$14,500"    -> "$14,500 dollars"
#         "{{X}} kilowatt-hours" value "8,935 kWh"  -> "8,935 kWh kilowatt-hours"
#         "{{X}} yrs"            value "10.2 yr"    -> "10.2 yr yrs"
#         "{{X}}-year payback"   value "10.2 years" -> "10.2 years-year payback"
#         "{{X}}kWh"             value "9.45 kW"    -> "9.45 kWkWh"
#         "about {{X}}"          value "≈ 9.45 kW"  -> "about ≈ 9.45 kW"
#     Catching the first five needs a unit VOCABULARY -- plurals, hyphen joins,
#     spelled-out names, word forms of the sigils -- rather than a list of unit
#     spellings; the last two are the word boundary and the sigil adjacency
#     doing exactly what the bullets above describe. Widening either is what
#     makes the short units cry wolf, so these stay stated rather than caught.
#     The guards themselves are pinned by
#     case_the_false_positive_guards_inside_the_seam_rules_are_load_bearing.
#   * Class 1 compares like for like: the template's sigil against the SAME
#     character at the same end of the value. "%{{X}}" where X ends in "%" is
#     not a doubling and is not flagged.
#   * Class 1 reads the rendered line as text, markup included, so an HTML tag
#     between the sigil and the token ("~<b>{{X}}</b>") separates them and is
#     not flagged. Every seam this template actually has puts the sigil and
#     the token in the same text run, which is what the rule is written for.
#   * Class 2 fires when the value is a BARE NUMBER (digits, thousands
#     separators, optional decimals -- no sigil, no unit, no letters) and
#     NOTHING BESIDE IT SUPPLIES A UNIT. "Supplies a unit" is the whitelist,
#     and it is deliberately narrow: the text after the value has to BEGIN
#     with a member of _SEAM_UNITS at a word boundary (one optional leading
#     space and one optional leading hyphen, so "{{ANALYSIS_DAYS}}-day" and
#     "{{SYSTEM_SIZE_KW_DC}} kW" both read as unit-carrying). Anything else --
#     "self-consumed", "statements", "of the load" -- is not a unit, and the
#     figure is reported.
#     THE FUNCTION-WORD NARROWING THIS REPLACED WAS TOO TIGHT TO CATCH ITS OWN
#     DEFECT CLASS, measured rather than argued. #129's original defect line
#     renders "covers 55% of the home's ... load -- 40% self-consumed, 60%
#     exported". SOLAR_COVERAGE_PCT losing its "%" was caught, because "of" is
#     a function word. Its SIBLING on the same line, SELF_CONSUMED_SHARE,
#     losing its "%" the same way (a one-word fmt regression, "pct0" ->
#     "num0") was NOT, because "self-consumed" is a hyphenated participle that
#     no plausible function-word list contains. A guard that catches one half
#     of one line's defect is not a guard.
#     IT HAS FALSE POSITIVES, AND THEY ARE ORDINARY ENGLISH -- more of them
#     than the function-word form had. The rule cannot tell a figure that LOST
#     its unit from a number that legitimately has none: a year, a count, a
#     ratio. Every one of these is correct prose and every one is flagged:
#         "12 statements"                 a count, followed by a counted noun
#         "13 billing periods"            a count, followed by an adjective
#         "2 independent sources"         a count, followed by an adjective
#         "the 2024 cleaning"             a year, followed by a noun
#         "563 sessions were overnight"   a count, followed by a counted noun
#     Five bare-number occurrences in the live template are exactly this
#     shape, and each carries a _SEAM_ALLOWLIST entry below naming the
#     occurrence and why its number is dimensionless. Class 2 is therefore a
#     TRIPWIRE, not a proof: when it fires, READ THE RENDERED LINE. If the
#     number is genuinely dimensionless, the remedy is an allowlist entry --
#     not a quiet edit to the rule, and not a unit invented to silence it.
#     That the allowlist is keyed by (token, class, marker) is what keeps such
#     an entry from also blinding classes 1 and 3 for the same token, or the
#     SAME class at a different occurrence of it.
#   * Class 2 does NOT flag a bare number followed by text with no letters in
#     its next word -- punctuation, a symbol, or another figure ("{{X}} ×
#     335 W", "<td>{{X}}</td><td>17,373</td>", "{{X}} — cleaned Aug 12") --
#     nor one at end of line, nor one sitting alone in a table cell whose
#     column header carries the unit, nor one with a sigil immediately in
#     front of it, since the sigil is the unit. Those are the shapes where a
#     reader takes the unit from the column, the clause or the sigil rather
#     than from the next word, and flagging them is what would make this rule
#     unrunnable. It is a stated blind spot in the same breath: a figure that
#     lost its unit and happens to be followed by a bracket is missed.
#   * Class 2 READS THROUGH MARKUP, which is the opposite of class 1 above and
#     is deliberate: it strips tags out of the text after the value to find
#     "the next word", so it reaches into the next table cell. "<td>{{X}}</td>
#     <td>of the load</td>" with X="42" IS flagged missing-unit even though a
#     reader sees two separate cells. Class 1 treats the same markup as a
#     separator; class 2 treats it as whitespace. The asymmetry is the
#     conservative direction for each rule -- class 1 must not invent a
#     doubling across a tag, class 2 must not miss a lost unit because prose
#     was wrapped in <b> -- but it means a class-2 hit can point at a word
#     that never renders beside the number. READ THE RENDERED LINE, as above.
#   * Class 2 HAS A SECOND TEST, evaluated FIRST, that does not read the
#     seam's prose at all: THE TOKEN'S DECLARED DIMENSION. Every leaf token in
#     report_tokens.py names a format spec -- usd0, pct1, cents1, num0, year --
#     and the money, percent and cents specs are the ones whose OUTPUT carries
#     a dimension sigil. A token declared with one of those, rendering a value
#     with no "$" (or "%", or "¢") anywhere in it and none supplied
#     immediately beside it by the template, has lost the dimension its own
#     format declares, and is reported REGARDLESS of what follows it and
#     regardless of the value's shape.
#     THE PROSE TEST ALONE COULD NOT SEE THIS, and that is measured rather
#     than argued. Most money figures in this template are printed
#     "{{X}}/yr" or "~{{X}}/mo", and "/yr" and "/mo" are members of
#     _SEAM_UNITS -- so a formatter regression from usd0 to num0, the exact
#     shape of #129's pct0 -> num0, renders "<b>3,282/yr electric</b>" and the
#     unit whitelist above answers "something beside it gives it a unit" and
#     stays quiet. It does: the wrong one. A period is not a price. Simulating
#     that regression at every occurrence of a dimension-declaring token in
#     the shipped template caught 8 of 43 occurrences before this test existed
#     and 43 of 43 after it -- the 35 it could not see included every $.../yr
#     and $.../mo figure in the report. The sweep is not a note about the
#     past: case_a_declared_money_or_percent_token_that_loses_its_sigil_is_
#     reported re-runs it on every checkout and fails if the ratio slips.
#     THE DIMENSION TABLE IS DERIVED, NOT DECLARED. _seam_fmt_dimensions()
#     calls every numeric formatter in report_tokens.FORMATTERS on one probe
#     number and reads the sigil out of what it prints, so a money format
#     added tomorrow is checked tomorrow with no edit here and no list of
#     token names exists to fall behind. What a derivation cannot see is a
#     classification DISAPPEARING -- a formatter deleted, a probe that stops
#     rendering, a table that quietly comes back empty -- which is the same
#     hole _SEAM_VOCABULARY_FLOOR exists for, closed the same way by
#     _SEAM_FMT_DIMENSION_FLOOR.
#     ITS BLIND SPOT WAS CLOSED IN ISSUE #163, and the shape of the hole is
#     worth keeping because the fix is shaped around it. A token that formats
#     ITSELF -- kind="derived" or "cited_constant" with fmt=None, building its
#     own string by calling _usd3 or _cents1 inside its own lambda -- declared
#     no format for this test to read, so its dimension was invisible here
#     however plainly a reader saw it, and 18 live tokens whose whole value is
#     one sigil-carrying figure sat in that state.
#     A DERIVATION COULD NOT HAVE CLOSED IT. Reading the dimension off the
#     value gets BILLED_GENERATION_RATES wrong in one direction (three "$"
#     figures in one clause, and it is prose) and DAYBAND_ONPEAK_PRICE wrong
#     in the other ("61-87¢" typed by hand, and it is one figure). So the
#     token declares, in one of three ways, and _seam_declared_dimension reads
#     the first two:
#       * `fmt` -- the registry formats the value and the sigil is not typed
#         at the token site at all. Preferred, and where an existing formatter
#         fits, taken: nine of those 18 moved to one.
#       * `dim="$"` -- the token formats itself and says what the figure is
#         measured in. For the decorations, ranges and tolerances no single
#         formatter call produces, and for the tokens that publish a figure on
#         one branch and a sentence on the other (states_no_figure).
#       * `phrase=True` -- the value is language, and the question does not
#         apply. Read here only to keep the two apart.
#     case_every_sigil_carrying_token_declares_what_it_is holds the whole
#     population to declaring one of the three, and the undeclared count in
#     case_a_declared_money_or_percent_token_that_loses_its_sigil_is_reported
#     is still computed from the registry rather than written down -- it now
#     reads 0, and is asserted at 0 rather than merely reported.
#   * Class 3 requires the echo to contain a DIGIT, to be at least
#     _SEAM_MIN_ECHO characters long, and to sit within _SEAM_ECHO_GAP
#     characters of the value -- after trailing closing punctuation is
#     stripped off the value, because the echoed figure is routinely followed
#     by the bracket that closes the clause carrying it (the pre-#134
#     INVERTER_DESCRIPTION ended "... ≈ 9.45 kW AC max)", and the shipped
#     template repeats everything but that final paren).
#     It reads BOTH SIDES of the seam. The template's copy of the figure can
#     sit after the token or in front of it, and after c79cc06 moved the AC
#     ceiling off INVERTER_DESCRIPTION and onto the template, in front is the
#     likelier direction: a future edit that restores the ceiling token-side
#     while the template's copy leads the line prints it twice with the sides
#     swapped. "A token whose value already contains the figure the template
#     prints beside it" is direction-agnostic, so the rule is too.
#     A short repeated number is not flagged, and neither is a figure repeated
#     in a DIFFERENT CONTAINER. The live example of the latter is the lifetime
#     table, which prints the first year's value in both the annual and the
#     cumulative cell (report-template.html:553) -- and be precise about WHICH
#     rule spares it, because there are two and only one of them is load-
#     bearing. Today's FIRST_YEAR_VALUE happens to be 7 characters, one under
#     _SEAM_MIN_ECHO, so the length floor would exclude it anyway; that is an
#     accident of this household's figure and it stops being true the moment
#     the first year's value reaches five digits. What actually spares the row,
#     at any length, is that the two cells are two CONTAINERS: the echo rule
#     stops at the </td>, so the second cell is never in the comparison and no
#     threshold is consulted. A character count could not have said that -- the
#     </td> closer is optional in valid HTML5, and an author who omits it does
#     not thereby print the figure once.
#     case_the_lifetime_tables_cumulative_cell_is_not_an_echo pins the row with
#     a value LONGER than the floor as well as today's, so neither rule can be
#     mistaken for the other and a future threshold change cannot start
#     flagging the row in silence.
#   * Class 3 BLIND SPOT, the accepted cost of the trim above: trailing
#     punctuation is stripped BEFORE the length floor is applied, so a value
#     that clears _SEAM_MIN_ECHO only with its punctuation attached falls
#     under the floor once trimmed and its echo is missed. "1.23 kW.," echoed
#     immediately (gap 0) is 9 characters and is NOT flagged, because the core
#     the rule compares is the 7-character "1.23 kW". The band is narrow --
#     values whose core is 8 or more characters are unaffected -- and no live
#     token's value lands in it today, but a shorter figure carrying two
#     closers would. Widening the floor to catch it costs false positives on
#     every short repeated number, which is the trade the floor exists to
#     make.
#   * Class 3 searches a WINDOW of 120 characters on each side of the value:
#     the 120 after it and the 120 before. Nothing further out is looked for,
#     and no repeated run longer than 120 characters can be matched at all.
#     That costs nothing a defect needs -- _SEAM_ECHO_GAP already requires the
#     repeat to sit within 6 characters of the value -- and it keeps the search
#     off the rest of a long table row. It does mean the value's OWN LENGTH
#     stops mattering past the window, which is why the rule tries both ends of
#     the value against each side: a long value's tail is all the suffix half
#     can ever look for, so a template that repeats the OPENING of a long value
#     is caught only by the head half of the run pair (core[:length]). Values
#     longer than the window are ordinary in this report --
#     case_the_echo_rule_reads_both_ends_of_the_value counts them on the run.
#   * ALL THREE RULES READ THE TEXT A READER SEES, NOT THE SOURCE. The rules
#     compare a rendered document, and the two things that stand between a
#     serialized HTML line and the text on the page are TAGS and ENTITIES.
#     Both are taken out first, in that order.
#     TAGS, by what they do to the reader rather than by how long they are.
#     _SEAM_INLINE_TAGS (b, i, span, a, br, ...) do not change the container:
#     they are removed with ZERO WIDTH and the text closes up behind them, so
#     "12</b>,345.6" is one figure and a <br> between two printings of the
#     same figure is still two printings. _SEAM_CONTAINER_TAGS (td, th, tr, p,
#     div, li, h1-h6, table, details, ...) do change it, and class 3 does not
#     compare ACROSS one at all -- the next cell, paragraph or list item is a
#     different run of text however close it sits in the file. Both sets are
#     the HTML VOCABULARY, not this template's inventory: every text-level
#     element is inline whether or not the template uses one. An element in
#     NEITHER set is not guessed at -- the guard raises SeamTagUnclassified,
#     names the tag and both sets, and stops, because a default in either
#     direction has a victim (a "container" default hid `<mark>`, `<u>` and
#     `<time>`, all three of which the previous rule caught). Comments,
#     doctypes and other constructs with no element name print no characters,
#     so they are zero width and take no lookup. See the policy comment on
#     _SEAM_INLINE_TAGS for why length was rejected as the test.
#     Class 2 is the one asymmetry, and it is deliberate: it reads THROUGH a
#     container boundary (collapsed to one space) because the unit that saves
#     a bare number is routinely in the next cell or the column header, so
#     stopping at the wall would invent a missing unit rather than prevent an
#     invented echo. Each rule takes whichever view keeps it quiet.
#     ENTITIES, decoded after the tags come out. "&#36;{{X}}" supplies a "$",
#     "{{X}}&#37;" supplies a "%", "&mdash;" is ONE character of gap and not
#     seven, and "{{X}} &times; 335 W" is a figure followed by a multiplication
#     sign rather than by a word called "times". Attribute text is gone with
#     the tags either way: '<td data-sort="12,345.6 kWh">{{X}}' with
#     X = "12,345.6 kWh" prints the figure ONCE and is not flagged.
#     THE ORDER IS LOAD-BEARING. Decoding first would turn a template's own
#     "&lt;p&gt;" -- prose ABOUT a tag, which a reader sees as three visible
#     characters -- into a tag the mask then eats.
#   * All three rules are scoped to ONE TEMPLATE LINE. This template puts one
#     element per line, so a seam always has both of its sides on the same
#     line; a value echoed across a line break is not looked for.
#   * Values are substituted ESCAPED, the way the generator writes them.
#     generate_report.render() substitutes html.escape(value, quote=True), so
#     that is what _seam_render substitutes too, and the rules then decode both
#     sides together -- the value and the window around it -- so the comparison
#     happens in one alphabet. Thirteen live token values change under escaping
#     (ampersands and apostrophes), so this is the shipped path, not a
#     hypothetical one. Since the decode undoes the escape for ordinary text,
#     the shape where escaping still changes the answer is narrow and named:
#     a value that literally SPELLS an entity. That is the probe
#     case_the_seam_guard_compares_the_values_the_generator_writes drives.
#
# CI. This runs with NO private archive. Where private/household.yaml is
# absent, the household-sourced tokens are resolved against the committed
# household.example.yaml instead (see _seam_values), so EVERY non-gap token is
# checked on the merge-guarding runner rather than only the ones that read
# data/ alone. The KNOWN_GAPS tokens are the only ones skipped, and the case
# asserts that set BY NAME rather than by a count -- counts of this inventory
# have gone stale here before. The two paths are not identical and this guard
# does not claim they are: see _seam_stand_in_household for the one shape
# difference measured between them.
# ---------------------------------------------------------------------------
_SEAM_TOKEN_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Sigils a template can supply on either side of a value. Strict adjacency.
_SEAM_SIGILS = ("~", "$", "%", "≈", "+", "−", "-")

# Unit suffixes a template can supply behind a value; one optional space
# allowed, matched case-insensitively and only on a word boundary at both ends
# (see _seam_doubled). Matched longest-first so "kWh/yr" wins over "kWh".
#
# The TIME units are here because this report is full of duration figures --
# paybacks, endurance, cleaning intervals, lifetimes -- and "{{PAYBACK_YEARS}}
# years" against a value already ending "years" is the most plausible seam a
# prose pass can introduce, ahead of anything the energy units cover. The word
# boundary is what makes the one-letter "h" safe to list: without it, any value
# ending "kWh" followed by the word "hours" would be flagged.
#
# "cycles/day" is here for the same reason the time units are: it is a unit
# this template really does supply behind a bare figure
# ("{{CYCLES_PER_DAY}} cycles/day"), spelled as a noun phrase rather than an
# abbreviation. Listing it is what keeps that occurrence out of
# _SEAM_ALLOWLIST -- an allowlist entry would have had to claim the number is
# dimensionless, which it is not; it is a rate whose unit is written out.
_SEAM_UNITS = ("cycles/day", "kWh/kW/yr", "kWh/yr", "per month", "per year",
               "annually", "/month", "months", "/year", "years", "hours",
               "month", "hour", "days", "year", "kWh", "/kWh", "MWh", "/day",
               "/mo", "/yr", "yrs", "day", "kW", "yr", "%", "¢", "°", "h")

_SEAM_BARE_NUMBER_RE = re.compile(r"\A\d[\d,]*(?:\.\d+)?\Z")

# TWO numbers joined by a dash and nothing else -- "61-87", "2,103.58-2,455.64",
# "0.45-2.4". A range is still ONE figure in ONE dimension: the token that
# publishes "61-87¢" is quoting a single price that moves, not a sentence
# carrying two unrelated quantities, and report_tokens' own `dim` docstring
# names a range as a thing `dim` exists for. So the phrase audit has to read it
# the same way it reads a lone number, or "phrase=True" on a range is a hole of
# exactly the shape a unit suffix is.
#
# EXACTLY ONE separator, which is what keeps a date out. "2025-06-14" carries
# two hyphens and does not match; "2026-08" would, and no token in this report
# publishes a bare year-month wearing a money or percent sign. Both dash
# characters this report really uses are accepted -- the en dash a formatter
# writes and the hyphen a hand-typed range like "61-87¢" uses.
_SEAM_RANGE_NUMBER_RE = re.compile(
    r"\A\d[\d,]*(?:\.\d+)?[-–—]\d[\d,]*(?:\.\d+)?\Z")

# The marks that say how a figure was arrived at rather than what it measures,
# stripped before a value is asked whether it is one figure. "~" and "+" come
# from _SEAM_SIGILS' own reading of the same distinction; "±" is here for the
# tolerance shape report_tokens names in the same breath as the range
# ("±2%"), and the space is here because a figure does not stop being one
# figure for having been spaced.
_SEAM_FIGURE_DECORATIONS = "~+± "

# The sigils that are a DIMENSION rather than a decoration. A subset of
# _SEAM_SIGILS on purpose: "~", "+" and "-" say how a figure was arrived at,
# not what it measures, and a value that keeps its tilde while losing its
# dollar sign has still lost its dimension. "¢" is here as well as "$"
# because _cents1 is a currency formatter too and a rate that loses its cent
# sign is the same defect one decimal place down.
_SEAM_DIMENSION_SIGILS = ("$", "%", "¢")

# The number every formatter is asked to render so its dimension can be read
# off the output. Positive and finite, because the currency formatters that
# carry no sign REFUSE a negative and every formatter refuses a non-number
# (report_tokens' two formatter preconditions); the exact magnitude is
# irrelevant -- only which sigils appear.
_SEAM_FMT_PROBE = 1234.0

# The format specs that MUST still classify as a dimension, and as which one.
#
# Committed separately from _seam_fmt_dimensions() for the reason
# _SEAM_VOCABULARY_FLOOR is committed separately from the rules that read
# _SEAM_UNITS: derivation is what keeps the table current, and it is exactly
# what cannot notice the table getting shorter. A classification that vanishes
# -- a formatter deleted, a probe call that starts raising and is skipped, a
# sigil dropped from _SEAM_DIMENSION_SIGILS -- takes with it every check that
# depended on it, and the suite goes green by having stopped asking.
#
# A SUPERSET needs no edit here, which is the whole point: a money format
# added to report_tokens.FORMATTERS tomorrow is derived, checked and swept
# tomorrow without this dict being touched. What fails is an entry below no
# longer classifying the way it says, so narrowing the guard becomes a
# deliberate two-place edit with a reviewer's name on it.
_SEAM_FMT_DIMENSION_FLOOR = {
    "usd0": "$", "usd0_tilde": "$", "usd0_signed": "$",
    "usd0_tilde_signed": "$", "usd0_plus": "$", "usd2": "$", "usd3": "$",
    "pct0": "%", "pct1": "%", "pct0_frac": "%",
    "cents1": "¢",
}


def _seam_fmt_dimensions():
    """{format-spec name: the dimension sigil its output carries}, DERIVED by
    running every numeric formatter in report_tokens.FORMATTERS on one probe
    number and reading the sigils out of what it prints.

    Derived rather than written down so that a money or percent format
    registered tomorrow is a dimension tomorrow, with no edit here and no list
    of token names to fall behind -- the same principle the rules themselves
    follow when they read _SEAM_UNITS instead of naming tokens. The floor
    above is what a derivation cannot supply.

    _NON_NUMERIC_FMTS (None and "raw") are skipped: they are str()
    passthroughs for tokens that build their own string, so whatever sigil
    comes out belongs to the value, not to the format.

    A formatter that REFUSES the probe fails here by name instead of being
    skipped. Skipping is how a table like this empties itself quietly: the
    formatter still exists, still puts a "$" on every figure it renders, and
    is no longer classified as currency by anything. Same for a formatter that
    prints TWO different dimension sigils -- there is no honest single answer,
    so the ambiguity is raised rather than resolved by tuple order."""
    out = {}
    for name, fn in rt.FORMATTERS.items():
        if name in rt._NON_NUMERIC_FMTS:
            continue
        try:
            rendered = fn(_SEAM_FMT_PROBE)
        except BaseException as e:                     # noqa: BLE001 -- reported
            raise AssertionError(
                f"report_tokens.FORMATTERS[{name!r}] cannot render the probe "
                f"{_SEAM_FMT_PROBE!r} ({type(e).__name__}: {e}); the seam guard reads "
                "each format's dimension off exactly this call, and a formatter it "
                "cannot call is a format whose money or percent sign nothing checks. "
                "Either the probe is no longer a value every formatter accepts -- "
                "change _SEAM_FMT_PROBE -- or the formatter's preconditions moved")
        found = [s for s in _SEAM_DIMENSION_SIGILS if s in rendered]
        assert len(found) <= 1, (
            f"report_tokens.FORMATTERS[{name!r}] renders {rendered!r}, which carries "
            f"the dimension sigils {found}; a format spec measures ONE dimension, and "
            "this test cannot say which of them a value that lost one has lost")
        if found:
            out[name] = found[0]
    return out


# Derived ONCE, at import, for the reason _seam_excuse gives for validating
# allowlist keys in a case rather than inside the scan: a table this rule
# cannot build is a fact about report_tokens.FORMATTERS, and it should be
# reported by name before any case runs rather than as an exception out of
# whichever case happened to be first. The dedicated case below re-derives it
# and holds the result to _SEAM_FMT_DIMENSION_FLOOR.
_SEAM_FMT_DIMENSIONS = _seam_fmt_dimensions()

# An HTML tag, with QUOTED ATTRIBUTES honored: a '>' inside "..." or '...' is
# attribute text and does not end the tag. The naive `<[^>]*>` ends the tag at
# the first '>' it sees, which leaves the rest of the attribute -- and the
# closing quote and bracket -- in the "visible" text ahead of the real next
# word, so a missing unit behind such an attribute is invisible to class 2 and
# attribute text is readable as printed by class 3.
# `<span title="comparison > baseline"> of the load</span>` is the shape.
# No live tag in report-template.html has a '>' inside a quoted attribute
# today (measured: the naive and the honoring regex agree on every tag in the
# live markup), so this is a blind spot closed before it was reached rather
# than a bug fixed after it bit.
_SEAM_TAG_RE = re.compile(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>""")

# The element name a tag opens or closes, lower-cased by _seam_tag_kind.
_SEAM_TAG_NAME_RE = re.compile(r"</?\s*([A-Za-z][A-Za-z0-9]*)")

# CONSTRUCTS THAT ARE NOT ELEMENTS AND PRINT NOTHING: an HTML comment
# `<!-- ... -->`, a doctype `<!DOCTYPE html>`, a CDATA section (which an HTML
# parser treats as a bogus comment outside foreign content), an XML processing
# instruction `<?xml ... ?>`. They carry no element name, so there is nothing
# to classify and nothing to look up -- and they are NOT sent down the refusal
# path, because the answer is already known: a reader sees NO CHARACTERS where
# one sits. `12<!-- note -->,345.6` renders "12,345.6", one figure. They are
# therefore ZERO WIDTH, kind "invisible", and the text closes up behind them
# exactly as it does behind an inline element. The kind is named separately
# from "inline" because the two are zero width for different reasons: an
# inline element WRAPS visible text, an invisible construct CONTRIBUTES none.
# `case_an_invisible_construct_is_zero_width_not_a_boundary` pins it.
_SEAM_INVISIBLE_RE = re.compile(r"\A<[!?]")

# HOW A TAG IS CLASSIFIED, AND WHY IT IS NOT BY LENGTH.
#
# THE RULE IS NOT HOW MANY CHARACTERS A TAG OCCUPIES, IT IS WHETHER A READER IS
# STILL READING THE SAME RUN OF TEXT ON THE OTHER SIDE OF IT. The seam rules
# compare what a reader sees, and a reader never sees a tag: `12</b>,345.6` is
# one figure to them and `</td><td>` is the wall between two cells. Counting
# the tag's source characters answers neither question -- it only says how much
# markup the author happened to type, which is why the two designs that tried
# it were rejected. Blanking a tag to its own length makes `12</b>,345.6` look
# four characters apart when a reader sees none, and it makes the wall between
# two cells exactly as wide as the author's optional `</td>` closer, which
# valid HTML5 lets them omit.
#
# INLINE FORMATTING does not change the container. The run of text continues
# straight through it, so these are masked to ZERO WIDTH and the text on either
# side is joined: a figure split by `<b>` is one figure, and a repeat separated
# by a `<br>` is still a repeat.
#
# `br` IS DELIBERATELY INLINE HERE, and it is the case that decides between
# this design and a fixed-width barrier. It ends a LINE; it does not end the
# run of text the reader is reading, and a template that prints the same figure
# on two lines of one paragraph prints it twice. A barrier wide enough to keep
# the next table cell out of range would go permanently blind to exactly that
# defect. `case_a_line_break_does_not_end_the_run_a_reader_is_reading` pins it.
#
# THE SET IS THE HTML SPEC'S PHRASING VOCABULARY, NOT A LIST OF THE TAGS THIS
# TEMPLATE HAPPENS TO USE. An earlier revision of this table listed twelve
# names and let everything else fall to a default. The twelve were the ones
# the live template carried, so the classification tracked one household's
# markup instead of what a reader sees, and `<mark>`, `<u>` and `<time>` --
# ordinary text-level elements, all three caught by the previous design --
# went silently unread. Every text-level element below is inline whether or
# not report-template.html has ever contained it, because a reader reads
# straight through all of them.
_SEAM_INLINE_TAGS = frozenset((
    # Text-level semantics, the whole group: HTML's own definition of markup
    # that formats a run of text without interrupting it.
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn",
    "em", "i", "kbd", "mark", "q", "rp", "rt", "ruby", "s", "samp", "small",
    "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
    # Edits. Phrasing when their content is, and both render in line: struck
    # text and inserted text are still the run of text the reader is in.
    "del", "ins",
    # Phrasing that prints its own text INSIDE the surrounding run rather
    # than inside a widget of its own.
    "label", "output",
    # Replaced content and the wrappers around it. `img` prints a box rather
    # than text and the words on either side of it are one run; `picture`,
    # `source` and `track` print nothing of their own at all.
    "img", "picture", "source", "track",
))

# CONTAINER BOUNDARIES do change it. Two values in different cells, paragraphs
# or list items are not one run of text however few characters of markup the
# author put between them, so the echo rule does not compare ACROSS one at all
# -- not "at a distance", not at all. That is what spares the lifetime table's
# cumulative column (report-template.html:553, which prints FIRST_YEAR_VALUE in
# both the annual and the cumulative cell) no matter how long that value grows,
# and it does so without a second threshold to tune.
#
# LIKE THE INLINE SET, THIS IS A VOCABULARY AND NOT AN INVENTORY. It names
# every element that ends the run of text a reader is in, in four groups:
# markup that opens a new block, markup that opens a widget with its own
# label, markup that embeds something that is not this document's text, and
# markup whose contents are never printed at all. The last group is the one
# worth stating out loud: a reader sees nothing of a <script> or a <title>,
# so joining the text on either side of one would splice source that is not
# on the page into a run that is. A boundary is the right answer there, not
# an approximation of one.
_SEAM_CONTAINER_TAGS = frozenset((
    # Tabular data. Two cells are two runs, and so are two rows.
    "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot",
    "tr", "td", "th",
    # Grouping content: paragraphs, lists, quotes, rules.
    "p", "div", "hr", "pre", "blockquote", "ol", "ul", "menu", "li",
    "dl", "dt", "dd", "figure", "figcaption",
    # Sections and headings.
    "html", "body", "main", "article", "section", "nav", "aside",
    "header", "footer", "hgroup", "search", "address",
    "h1", "h2", "h3", "h4", "h5", "h6",
    # Interactive containers.
    "details", "summary", "dialog",
    # Form controls. Each one prints its own text inside its own widget: the
    # caption on a button is not the sentence beside the button. `label` and
    # `output` are the exceptions and are inline above.
    "form", "fieldset", "legend", "input", "button", "select", "datalist",
    "optgroup", "option", "textarea", "progress", "meter",
    # Embedded content. Whatever text is inside is the embedded document's,
    # or a fallback that is printed only when the embed fails.
    "iframe", "embed", "object", "video", "audio", "canvas", "map", "area",
    "svg", "math",
    # Content that is never printed. Splicing across one of these would join
    # two visible runs through text that is not on the page.
    "head", "title", "base", "link", "meta", "style", "script",
    "noscript", "template", "slot",
))


class SeamTagUnclassified(AssertionError):
    """The seam guard met an element name that is in neither tag set.

    An AssertionError subclass so the suite runner reports it as the case
    failure it is, and a named class so the case that drives the refusal can
    tell it apart from an ordinary assertion inside the same fixture."""


# THERE IS NO DEFAULT. AN UNCLASSIFIED ELEMENT REFUSES.
#
# The previous revision defaulted to "container" and called that the quiet
# answer. It was not quiet, it was silently WRONG, and the measurement is the
# argument: `_seam_echo("12,345.6 kWh", "", "<mark>12,345.6 kWh</mark>")`
# reported the duplicate before that default existed -- the source `<mark>`
# fits inside _SEAM_ECHO_GAP, so the old character-counting rule compared
# straight through it -- and returned None after. `<u>` and `<time>` moved the
# same way. Three ordinary text-level elements went from CAUGHT to MISSED, so
# the "stated blind spot" the default bought was in fact a regression against
# markup a prose pass introduces without thinking about it.
#
# The lesson is not that the other default is better. It is that BOTH defaults
# are guesses, and each guess has a victim:
#   * "container" hides a real echo behind any element this table has not
#     heard of, which is what just happened.
#   * "inline" joins two runs of text a reader sees separately and reports a
#     figure that is printed once -- the false alarm this guard cannot
#     survive, because a guard that refuses a legitimate state is how a
#     household stops being able to publish its report.
# Neither cost is worth paying for an answer nobody has looked up. So the
# guard does not answer: it raises SeamTagUnclassified, names the tag, names
# both sets, and stops. This is a TEST-ONLY guard, so the whole price of a
# refusal is a red CI run and one word of classification by the author who
# introduced the element -- not a household losing its report. That asymmetry
# is the entire reason failing closed is affordable here.
#
# Constructs with no element name are NOT sent down this path: a comment, a
# doctype, a CDATA section and a processing instruction print no characters,
# so their kind is known without a lookup and _SEAM_INVISIBLE_RE answers
# "invisible" for them above.
#
# case_an_unclassified_element_refuses_instead_of_guessing_its_kind drives the
# refusal, and case_the_live_template_classifies_every_tag_it_scans keeps the
# shipped template out of it.


def _seam_tag_kind(tag):
    """"inline", "container" or "invisible" for one matched HTML construct;
    raises SeamTagUnclassified for an element name in neither set.

    See _SEAM_INLINE_TAGS above for the policy: a tag is classified by whether
    a reader is still reading the same run of text on the other side of it, not
    by how many characters it occupies. "inline" and "invisible" are both ZERO
    WIDTH; they differ in why, and _SEAM_INVISIBLE_RE says which."""
    if _SEAM_INVISIBLE_RE.match(tag):
        return "invisible"
    m = _SEAM_TAG_NAME_RE.match(tag)
    if m is None:
        raise SeamTagUnclassified(
            f"the seam guard cannot read {tag!r}: it carries no element name and is "
            "not a comment, doctype, CDATA section or processing instruction either, "
            "so there is nothing to classify and no reason to assume it prints "
            "nothing. Either _SEAM_TAG_RE has started matching something that is not "
            "markup, or the template contains a construct this guard has never seen. "
            "Fix whichever it is; the guard will not guess whether a reader sees "
            "characters here")
    name = m.group(1).lower()
    if name in _SEAM_INLINE_TAGS:
        return "inline"
    if name in _SEAM_CONTAINER_TAGS:
        return "container"
    raise SeamTagUnclassified(
        f"the seam guard has no classification for the element {name!r} (in "
        f"{tag!r}), and it will not guess one. Add the name to exactly ONE of "
        "_SEAM_INLINE_TAGS (a reader reads straight through it, so it is removed at "
        "zero width and the text closes up behind it: b, span, mark, time, ...) or "
        "_SEAM_CONTAINER_TAGS (it ends the run of text, so the echo rule does not "
        "compare across it at all: td, p, li, script, ...). Guessing either way has a "
        "victim -- 'container' hides a real duplicated figure behind the element, "
        "'inline' reports a figure that is printed once -- so this refuses instead. "
        "It is a test-only guard: the cost of this failure is one word of "
        "classification, not a report that cannot be published")


# Characters trimmed off the ends of the "next word" before it is quoted back
# in a class-2 message. Not a rule input: the word is TESTED for letters
# before it is trimmed.
_SEAM_WORD_TRIM = ".,;:!?)(\"'"

_SEAM_MIN_ECHO = 8      # chars; shorter repeats are coincidence, not an echo
_SEAM_ECHO_GAP = 6      # chars allowed between a value's end and its echo

# Closing punctuation stripped off a value's end before class 3 looks for the
# value's tail in the text after it. A value that closes a bracket or a
# sentence ("... ≈ 9.45 kW AC max)") echoes everything EXCEPT that last
# character, so a suffix match anchored on the value's true final character
# finds nothing -- which is how the pre-#134 INVERTER_DESCRIPTION slipped past
# this rule when it was rendered into the template as it ships today.
_SEAM_ECHO_TRIM = ")]}.,;:"

# The three defect classes, in the order _seam_defects evaluates them. Named
# once so the allowlist's key check and the reports below cannot drift from the
# labels the rules actually emit.
_SEAM_CLASSES = ("doubled-sigil", "missing-unit", "echoed-phrase")

# (token, defect class, template-line marker) -> reason.
#
# ONE ENTRY EXCUSES ONE OCCURRENCE. Keyed by the token alone, an entry
# suppresses every class for that token forever, so an excuse for a
# legitimately dimensionless number would also blind it to a doubled sigil and
# to an echoed figure -- two defects issue #129 actually shipped. Keyed by
# (token, class), it still suppresses that class at EVERY occurrence of the
# token: this template prints {{BILL_COUNT}} on two different lines, and one
# entry for the count that is genuinely dimensionless would silently pardon a
# real lost unit on the other line, with the stale check below still green
# because the first occurrence keeps matching. Measured on exactly that pair,
# not supposed.
#
# The MARKER closes it. It is a literal substring of the report-template.html
# LINE carrying the occurrence -- the raw template line, never the rendered
# one, so no household figure is ever committed to this file (CLAUDE.md
# section 4) and the marker does not drift with the archive. An occurrence is
# excused only if its own line contains the marker, and the stale check
# refuses a marker that matches MORE THAN ONE line (an over-broad or empty
# marker is a token-wide pardon wearing a disguise) and reports an entry whose
# marker matches NO seam as stale.
#
# ONE STATED LIMIT: a marker cannot separate two occurrences of the SAME token
# on the SAME line. Several template lines print a token twice -- the lifetime
# table's row (report-template.html:553, FIRST_YEAR_VALUE in both the annual
# and the cumulative cell) is the one this file has reason to name elsewhere --
# and none of them is allowlisted, so an entry for one would excuse both.
# Line numbers are the next granularity down and they do not help here either.
# No list of those lines is written down: the previous one named five lines by
# number, every number was stale, and nothing failed. The live count is
# asserted instead, one entry to one occurrence, by
# case_no_token_renders_a_broken_seam_in_its_own_template_context.
#
# Every entry below is a bare number that is legitimately DIMENSIONLESS -- a
# count or a year -- reported by class 2 because the word after it is an
# ordinary English noun or adjective rather than a unit. Each names the token,
# the line it excuses and why that number has no unit to lose.
_SEAM_ALLOWLIST = {
    ("BILL_COUNT", "missing-unit", "-day audit of {{BILL_COUNT}} statements"):
        "a COUNT of statements, not a measurement: '12 statements' is the "
        "number of PDFs the audit read. There is no unit a statement count "
        "could have lost.",
    ("PRODUCTION_SOURCE_COUNT", "missing-unit",
     "{{PRODUCTION_SOURCE_COUNT}} independent sources"):
        "a COUNT of monitoring feeds ('2 independent sources'). Dimensionless "
        "by construction -- the figure it qualifies, the agreement percentage, "
        "carries its own sign two tokens later.",
    ("BILL_COUNT", "missing-unit", "All {{BILL_COUNT}} detailed electric statements"):
        "the same statement COUNT at its second occurrence, on the bills "
        "section's opening line. A separate entry on purpose: the two "
        "occurrences are excused one at a time, so a lost unit at either one "
        "is still reported at the other.",
    ("BILLING_PERIOD_COUNT", "missing-unit",
     "({{BILLING_PERIOD_COUNT}} billing periods) were parsed"):
        "a COUNT of billing periods. One statement PDF can carry two periods, "
        "which is why this count differs from BILL_COUNT and why neither is a "
        "quantity with a unit.",
    ("BILLING_PERIOD_COUNT", "missing-unit",
     "statements ({{BILLING_PERIOD_COUNT}} billing periods):"):
        "the same billing-period COUNT at its second occurrence, on the bottom "
        "line's anchoring sentence. A separate entry on purpose: the two "
        "occurrences are excused one at a time.",
    ("CLEANING_YEAR", "missing-unit", "did the {{CLEANING_YEAR}} cleaning actually work"):
        "a calendar YEAR used as an adjective ('the 2024 cleaning'). A year "
        "has no unit; the token is a date part, not a measurement.",
}

# The committed entries, frozen at import. The allowlist case below MUTATES
# _SEAM_ALLOWLIST to drive the contract its live entries do not reach, and a
# case that leaves an entry behind hands every later case a silent pardon.
_SEAM_ALLOWLIST_SHIPPED = tuple(_SEAM_ALLOWLIST)


@contextlib.contextmanager
def _seam_allowlist_cleared():
    """_SEAM_ALLOWLIST emptied for the duration, then restored exactly.

    The allowlist case drives synthetic templates whose `values` dict knows
    nothing of the live tokens, and _seam_stale_allowlist rightly refuses a
    key naming a token the run cannot resolve. Emptying the dict is what lets
    that guard stay strict while the synthetic fixtures still exercise it; the
    case asserts the restore against _SEAM_ALLOWLIST_SHIPPED afterwards."""
    shipped = dict(_SEAM_ALLOWLIST)
    _SEAM_ALLOWLIST.clear()
    try:
        yield
    finally:
        _SEAM_ALLOWLIST.clear()
        _SEAM_ALLOWLIST.update(shipped)


def _seam_render(line, values):
    """(rendered_line, [(token, start, end), ...]) -- the line with every
    resolvable token replaced by its value AS THE GENERATOR WRITES IT, and
    where each value landed.

    The substituted text is html.escape(value, quote=True), which is what
    generate_report.render() puts in the document (its `_sub`). Escaping
    rewrites a value's INTERNAL text -- "PG&E 2025" becomes "PG&amp;E 2025" --
    so comparing the raw value against an escaped template hides an echoed
    figure. case_the_seam_guard_compares_the_values_the_generator_writes pins
    the two against each other rather than trusting this comment.

    A token with no value (a KNOWN_GAPS entry) is left as its literal
    {{NAME}}, which no rule below can mistake for a figure or a unit."""
    out, spans, pos = [], [], 0
    for m in _SEAM_TOKEN_RE.finditer(line):
        value = values.get(m.group(1))
        if value is None:
            continue
        # Every rule below reads the value as text. A non-str here is a
        # resolver returning a raw number, and it used to surface as a bare
        # TypeError from inside this helper, naming neither the token nor the
        # rule that wanted a string.
        assert isinstance(value, str), (
            f"token {m.group(1)} resolved to a {type(value).__name__} "
            f"({value!r}), not the rendered string the seam rules read; a "
            "token's value reaches the template as text, so format it here")
        written = _htmllib.escape(value, quote=True)
        out.append(line[pos:m.start()])
        start = sum(len(chunk) for chunk in out)
        out.append(written)
        spans.append((m.group(1), start, start + len(written)))
        pos = m.end()
    out.append(line[pos:])
    return "".join(out), spans


def _seam_doubled(value, head, tail):
    """Class 1: the sigil or unit on one side of the seam that the value
    already carries at that end -- or None.

    Both sides are read as a READER sees them (_seam_visible_before /
    _seam_visible_after) rather than as the file spells them, so an inline
    `<b>` between the value and the sigil hides nothing and a template that
    writes its sigil as an entity -- `&#36;{{X}}` -- supplies a "$" here just
    as plainly as if it had typed one. The value is decoded for the same
    reason: the two sides have to be compared in one alphabet."""
    value = _seam_unescape(value)
    head, tail = _seam_visible_before(head), _seam_visible_after(tail)
    if head and head[-1] in _SEAM_SIGILS and value.startswith(head[-1]):
        return f"template sigil {head[-1]!r} before a value already starting with it"
    for sigil in _SEAM_SIGILS:
        if tail.startswith(sigil) and value.endswith(sigil):
            return f"template sigil {sigil!r} after a value already ending with it"
    after = tail[1:] if tail[:1] == " " else tail
    low_after, low_value = after.lower(), value.lower()
    for unit in sorted(_SEAM_UNITS, key=len, reverse=True):
        low_unit = unit.lower()
        if not (low_after.startswith(low_unit) and low_value.endswith(low_unit)):
            continue
        # Word boundary at both ends, or the short units fire on ordinary
        # prose: "1,234 kWh" does not END in the unit "h" (a letter precedes
        # it) and "hours later" does not SUPPLY the unit "h" (a letter follows
        # it). Digits and punctuation are not boundaries to defend against --
        # "2.4%" legitimately ends in "%" with a digit in front of it.
        if after[len(unit):len(unit) + 1].isalpha():
            continue
        if value[:len(value) - len(unit)][-1:].isalpha():
            continue
        return f"template unit {unit!r} after a value already ending with it"
    return None


def _seam_unit_prefix(text):
    """The member of _SEAM_UNITS that `text` BEGINS with, at a word boundary --
    or None.

    One optional leading hyphen is allowed, because a unit joined to its figure
    with a hyphen is still that figure's unit: "{{ANALYSIS_DAYS}}-day audit"
    renders "365-day audit", and the day is what the 365 counts. `text` is
    expected already left-stripped.

    The word boundary is the same guard _seam_doubled applies at its template
    end, and for the same reason: without it the one-letter "h" would read
    "hours later" as supplying a unit -- which here would be a MISS rather than
    a false alarm, since this rule uses the unit list to stay QUIET. The two
    are deliberately separate functions: _seam_doubled must not allow the
    hyphen (a hyphen between a value and a unit is not a doubling), and this
    one must."""
    body = text[1:] if text[:1] == "-" else text
    low = body.lower()
    for unit in sorted(_SEAM_UNITS, key=len, reverse=True):
        if low.startswith(unit.lower()) and not body[len(unit):len(unit) + 1].isalpha():
            return unit
    return None


def _seam_unit_suffix(text):
    """The member of _SEAM_UNITS that `text` ENDS with, at a word boundary --
    or None. The mirror of _seam_unit_prefix, reading the value's end instead
    of the template's.

    Same list, same boundary rule, for the same reason both of those exist:
    the units this report really writes are already enumerated and already
    held load-bearing by case_every_member_of_the_seam_constants_is_load_
    bearing, so a second hand-rolled list would be one more thing to fall
    behind _SEAM_UNITS. Longest match first, so "kWh/yr" is taken whole rather
    than leaving a "kWh" behind, and the boundary is checked on the LEFT here
    (a letter before the unit means the unit is the tail of a word): "control
    years" ends in the unit "years", "the fresh" does not end in the unit "h".

    No leading-hyphen allowance, unlike _seam_unit_prefix: a hyphen inside the
    value is part of the value, and eating one here would turn the range
    "61-87" into the number "61"."""
    low = text.lower()
    for unit in sorted(_SEAM_UNITS, key=len, reverse=True):
        if not low.endswith(unit.lower()):
            continue
        if text[:len(text) - len(unit)][-1:].isalpha():
            continue
        return unit
    return None


def _seam_reduces_to_one_figure(value):
    """True when `value` is ONE figure in ONE dimension and nothing else.

    THE SHAPE TEST THE PHRASE AUDIT ASKS, and what makes it a test rather than
    a regex is the normalisation in front of it. A figure in this report
    arrives wearing up to three layers that are not part of what it measures:
    an approximation or tolerance mark ("~", "+", "±"), a dimension sigil
    ("$", "%", "¢"), and a unit suffix ("/yr", "/month", "/kWh"). Asking
    _SEAM_BARE_NUMBER_RE before those come off answers a question about
    decoration rather than about content -- "~$1,221/yr" reduced only for its
    sigils leaves "1,221/yr", which is not a bare number, so the audit used to
    accept the commonest figure shape in the whole report as language. That is
    a hole the width of a unit suffix, and it fits every "/yr" and "/mo"
    figure here.

    THE OTHER DIRECTION IS THE ONE THAT COSTS MORE. Called on real prose this
    must stay quiet, or the audit becomes a false alarm on the sentences it
    exists to leave alone -- so the units come off ONE END, the value has to
    reduce to a number (or a range) with NOTHING left over, and a value with
    so much as one other word in it survives every layer of stripping:
    "$10-51/yr across the 0.45-2.4%/month soiling bracket" still carries
    "across ... soiling bracket" when the last suffix is gone. A sentence is
    not reachable from here.

    The unit loop runs to a fixed bound rather than `while`: a compound the
    list spells in two pieces ("2.4%/month" -> "/month", then "%") needs more
    than one pass, and a bound that cannot exceed the number of distinct units
    keeps a pathological value from spinning."""
    core = value.strip()
    for _ in range(len(_SEAM_UNITS)):
        unit = _seam_unit_suffix(core)
        if unit is None:
            break
        core = core[:len(core) - len(unit)].rstrip()
    core = "".join(c for c in core
                   if c not in _SEAM_DIMENSION_SIGILS
                   and c not in _SEAM_FIGURE_DECORATIONS)
    return bool(_SEAM_BARE_NUMBER_RE.match(core)
                or _SEAM_RANGE_NUMBER_RE.match(core))


def _seam_phrase_offences(values, marked):
    """[(token, value)] for every name in `marked` whose value carries a
    dimension sigil and still reduces to one figure -- the phrase audit's
    whole finding, factored out so a mutation case can drive it on a
    declaration this repo does not ship."""
    return [(name, values[name]) for name in marked
            if any(s in values[name] for s in _SEAM_DIMENSION_SIGILS)
            and _seam_reduces_to_one_figure(values[name])]


def _seam_declared_dimension(name, fmt, value):
    """The dimension sigil a token's figure is measured in, and where that
    answer came from, as (sigil, source) -- or (None, None).

    TWO PLACES DECLARE IT, and they are asked in this order (issue #163).

      1. THE FORMAT SPEC, read by probing report_tokens.FORMATTERS. A token
         whose `fmt` names a money, percent or cents format has its sigil put
         on by the registry, so the declaration is as strong as the formatter.
      2. THE TOKEN'S OWN `dim`, for a value no single formatter call can
         produce -- a decorated figure ("~18.7%"), a figure carrying a period
         the formatter does not own ("$233/yr"), a range ("61-87¢"), a
         tolerance ("±2%") -- and for the tokens that publish a figure on one
         branch and a sentence on the other.

    report_tokens._tok refuses a token that declares both, so the two can
    never disagree and the order is a fall-through, not a precedence rule.

    THE ONE EXEMPTION IS ON THE `dim` PATH ONLY, and it is why the value is an
    argument here. Several `dim` tokens render a figure only where the
    household has the thing being priced; on the other branch they render this
    module's own words for having no figure to state, and
    report_tokens.states_no_figure is report_tokens answering for its own
    output. A `fmt` token needs no such exemption: resolve_token's finiteness
    gate stands in front of every formatter, so a formatted value is always a
    figure.

    A token this scan cannot name -- a synthetic fixture's invented token --
    is in neither place and declares nothing, which leaves this half of class
    2 inert exactly as it should be."""
    derived = _SEAM_FMT_DIMENSIONS.get(fmt)
    if derived is not None:
        return derived, repr(fmt)
    declared = rt.declared_dimension(name)
    if declared is not None and not rt.states_no_figure(value):
        return declared, f"dim={declared!r}"
    return None, None


def _seam_missing_unit(value, head, tail, fmt=None, name=None):
    """Class 2: a value that lost the dimension its token declares, or a bare
    number that nothing beside it gives a unit to -- or None.

    A bare number followed by a WORD THAT IS NOT A UNIT is reported. The
    earlier form of this rule reported only a bare number followed by a
    function word, which is too tight to catch its own defect class: on issue
    #129's own line it caught SOLAR_COVERAGE_PCT (followed by "of") and missed
    SELF_CONSUMED_SHARE (followed by "self-consumed") losing its percent sign
    the same way. See the block comment above for the whitelist, the
    exemptions, and the false positives the widening costs.

    `fmt` is the token's DECLARED format spec and `name` is the token itself,
    both as report_tokens.TOKENS records them, and both None for a token that
    declares none or that this scan cannot name (a synthetic fixture's
    invented token). They are what the first test below reads, through
    _seam_declared_dimension, and they are passed in rather than looked up so
    this rule stays a function of its arguments and the module tables, like
    the other two.

    THE FIRST TEST IS NOT ABOUT THE PROSE, and it comes first because the
    prose test cannot answer it. A "usd0" value rendering "3,282" beside the
    template's "/yr" satisfies the unit whitelist below and is still a bill
    with no currency on it -- 23 of the report's 43 money and percent
    occurrences were exactly that shape, "/yr" or "/mo" answering for a
    missing "$". The declared dimension is the only thing on either side of
    the seam that knows a price from a period, so it is asked first, and its
    answer is not overridable by what follows.

    It is deliberately NOT limited to the bare-number shape the rest of the
    rule is scoped to. A formatter regression on a signed money token renders
    "-500", which is not a bare number and never reaches the whitelist below,
    and it has lost its dollar sign just as completely. A correctly formatted
    value cannot trip this test -- its formatter puts the sigil there
    unconditionally -- so widening past the shape guard costs no false
    positives and catches the sign case for free.

    Both sides are read as a reader sees them: entities decoded and inline
    formatting removed. That is what keeps "{{X}} &times; 335 W" quiet -- the
    reader's next "word" there is the multiplication sign, which carries no
    letters and is therefore not a word claiming to be a unit, while the raw
    source spells it "&times;" and looks like one."""
    value = _seam_unescape(value)
    dimension, source = _seam_declared_dimension(name, fmt, value)
    if dimension is not None and dimension not in value \
            and _seam_visible_before(head)[-1:] != dimension \
            and _seam_visible_after(tail)[:1] != dimension:
        return (f"a {source} value renders {value!r}, which carries no {dimension!r} and "
                f"has none immediately beside it; that declaration says the figure is "
                f"measured in {dimension!r}, so this one lost its dimension -- whatever "
                "unit follows it belongs to something else")
    if not _SEAM_BARE_NUMBER_RE.match(value):
        return None
    # A sigil in front of a bare number IS its unit ("$14,500", "~2"), and the
    # template may have written it as an entity or put an inline tag between
    # the two, so the head is read the way a reader reads it.
    if _seam_visible_before(head)[-1:] in _SEAM_SIGILS:
        return None
    # Read THROUGH markup, as the block comment describes: container
    # boundaries become whitespace so the next word can be in the next table
    # cell, inline formatting vanishes, and entities are decoded.
    text = _seam_visible_through(tail).lstrip()
    if not text:
        return None
    if _seam_unit_prefix(text) is not None:
        return None
    nxt = text.split()[0]
    # No letters in the next word means punctuation, a symbol or another
    # figure -- a column header, a clause or a sigil is carrying the unit, and
    # flagging those is what would make this rule unrunnable.
    if not any(c.isalpha() for c in nxt):
        return None
    return (f"bare number followed by {nxt.strip(_SEAM_WORD_TRIM)!r}, which is not a "
            "unit, so nothing beside the figure gives it one")


def _seam_unescape(text):
    """`text` with HTML entities decoded -- what a reader sees, not what the
    file spells.

    ALWAYS CALLED AFTER THE TAGS ARE OUT, never before. Decoding first turns a
    template's own `&lt;p&gt;` -- prose ABOUT a tag, which a reader sees as the
    four characters "<p>" -- into a tag that the mask then eats, and it lets a
    value's escaped "&amp;lt;" become markup in a document that contains none.
    Tags are markup and entities are text; the markup comes out first."""
    return _htmllib.unescape(text)


def _seam_visible_before(head):
    """The run of text a reader is reading immediately BEFORE the value:
    everything after the last CONTAINER boundary in `head`, with the inline
    formatting inside it removed and the entities decoded.

    Truncating at the container is the whole of the fix for reading across a
    cell wall: text in the previous <td>, <p> or <li> is not text a reader is
    still reading, so no rule may compare against it."""
    start = 0
    for m in _SEAM_TAG_RE.finditer(head):
        if _seam_tag_kind(m.group(0)) == "container":
            start = m.end()
    # Every construct left in the remainder is zero width by construction --
    # inline or invisible, since an unclassified one would already have
    # raised in the loop above -- so this removes exactly those.
    return _seam_unescape(_SEAM_TAG_RE.sub("", head[start:]))


def _seam_visible_after(tail):
    """The run of text a reader is reading immediately AFTER the value: `tail`
    up to the first CONTAINER boundary, with the inline formatting inside it
    removed and the entities decoded. The mirror of _seam_visible_before."""
    end = len(tail)
    for m in _SEAM_TAG_RE.finditer(tail):
        if _seam_tag_kind(m.group(0)) == "container":
            end = m.start()
            break
    return _seam_unescape(_SEAM_TAG_RE.sub("", tail[:end]))


def _seam_visible_through(text):
    """`text` read THROUGH its container boundaries: inline tags removed, every
    container boundary collapsed to one space, entities decoded, nothing cut.

    CLASS 2 ONLY, and the asymmetry with the two helpers above is deliberate
    rather than an oversight. Class 3 must not compare across a container
    because doing so INVENTS an echo; class 2 reads across one because the
    thing it is looking for -- a unit somewhere beside the figure -- is
    routinely in the next cell or in the column header, and refusing to look
    there would INVENT a missing unit. Both directions are the same trade: the
    view that keeps the rule quiet is the one each rule takes."""
    return _seam_unescape(_SEAM_TAG_RE.sub(
        lambda m: " " if _seam_tag_kind(m.group(0)) == "container" else "", text))


def _seam_echo(value, head, tail):
    """Class 3: a run of the value's own text repeated by the template
    immediately BESIDE it, on either side -- or None.

    Trailing closing punctuation is stripped off the value first
    (_SEAM_ECHO_TRIM). The echoed text is the FIGURE, not the bracket that
    closes the clause around it, so requiring the repeat to reach the value's
    literal last character misses exactly the shape issue #129 shipped:
    INVERTER_DESCRIPTION ended '... ≈ 9.45 kW AC max)' and the template line
    repeated '≈ 9.45 kW AC max' without the paren.

    BOTH SIDES, and both ends of the value against each. #129's defect was the
    template's copy sitting AFTER the token, but c79cc06 moved the AC ceiling
    onto the template ahead of nothing -- restore it token-side tomorrow while
    the template's copy leads the line and the same figure prints twice with
    the sides swapped, which a tail-only rule cannot see. The gap is measured
    from the near edge of the value in both directions.

    BOTH WINDOWS ARE THE TEXT A READER SEES, per _seam_visible_before /
    _seam_visible_after: attribute text is gone, inline formatting is gone,
    entities are decoded, and the window STOPS at the nearest container
    boundary. Three consequences, each of them the point:
      * Attribute text is markup, not print. `<td data-sort="12,345.6 kWh">`
        beside {{X}} = "12,345.6 kWh" shows the figure once.
      * Inline formatting is not a distance. `12</b>,345.6` is one figure to a
        reader, so a template that splits its copy of the value with a <b> --
        or separates two printings with a <br> -- is still printing it twice.
      * A container boundary is not a distance either; it is the end of the
        comparison. The next cell, paragraph or list item is not this run of
        text, so _SEAM_ECHO_GAP is never even consulted across one. That is
        what spares the lifetime table's cumulative column, and it spares it
        for however long FIRST_YEAR_VALUE grows -- no character count involved.
    _SEAM_ECHO_GAP therefore measures a gap a reader can actually see, which is
    what it always claimed to measure."""
    core = _seam_unescape(value).rstrip(_SEAM_ECHO_TRIM)
    seen_head, seen_tail = _seam_visible_before(head), _seam_visible_after(tail)
    for side, window in (("later", seen_tail[:120]), ("earlier", seen_head[-120:])):
        for length in range(len(core), _SEAM_MIN_ECHO - 1, -1):
            # Either end of the value: the template can repeat the figure the
            # value opens with as readily as the one it closes with.
            for run in (core[-length:], core[:length]):
                if not any(c.isdigit() for c in run):
                    continue
                if side == "later":
                    at = window.find(run)
                    gap = at
                else:
                    at = window.rfind(run)
                    gap = len(window) - (at + len(run))
                if at >= 0 and 0 <= gap <= _SEAM_ECHO_GAP:
                    return (f"the value's own text {run!r} is printed again "
                            f"{gap} char(s) {side}")
    return None


def _seam_scan(template_text, values):
    """Every seam in a filled template, allowlist NOT applied, as
    [(token, class, why, rendered_context, template_line), ...].

    The ONE piece of module state it does not read is _SEAM_ALLOWLIST: no
    entry can suppress a hit here, which is what lets _seam_stale_allowlist
    prove an excused occurrence still has the seam its entry excuses. It does
    read the rule tables and thresholds those rules are built from --
    _SEAM_CLASSES here, and _SEAM_SIGILS, _SEAM_UNITS, _SEAM_BARE_NUMBER_RE,
    _SEAM_FMT_DIMENSIONS, _SEAM_TAG_RE, _SEAM_INLINE_TAGS,
    _SEAM_CONTAINER_TAGS, _SEAM_INVISIBLE_RE, _SEAM_ECHO_TRIM, _SEAM_MIN_ECHO and
    _SEAM_ECHO_GAP inside the three rules -- plus report_tokens.TOKENS, for
    the one thing about a seam that is not visible in the rendered line: what
    dimension the token declares the figure is measured in, through its format
    spec or through its own `dim` -- so identical arguments answer differently once one of
    those is mutated. Measured, not assumed: dropping "cycles/day" from
    _SEAM_UNITS turns a clean "{{CYCLES_PER_DAY}} cycles/day" fragment into
    one missing-unit hit. That is not a defect to fix; it is what the probe
    cases below rely on when they drive the rules off those same tables.

    The TEMPLATE LINE is carried alongside each hit because that is what an
    allowlist entry's marker is matched against -- the raw line, not the
    rendered one, so an excuse never depends on a household figure."""
    found = []
    for line in template_text.splitlines():
        rendered, spans = _seam_render(line, values)
        for name, start, end in spans:
            value, head, tail = rendered[start:end], rendered[:start], rendered[end:]
            # The token's DECLARED format, which class 2 reads to learn what
            # dimension the figure is supposed to carry. Looked up here rather
            # than inside the rule so the rule stays a function of its
            # arguments; .get twice because a synthetic fixture's invented
            # token is not in the registry and declares nothing, which leaves
            # that half of class 2 inert exactly as it should be. The NAME
            # goes with it because the format is only the first of the two
            # places a dimension can be declared -- see
            # _seam_declared_dimension.
            fmt = rt.TOKENS.get(name, {}).get("fmt")
            for label, why in zip(_SEAM_CLASSES,
                                  (_seam_doubled(value, head, tail),
                                   _seam_missing_unit(value, head, tail, fmt, name),
                                   _seam_echo(value, head, tail))):
                if why:
                    found.append((name, label, why,
                                  rendered[max(0, start - 40):end + 40], line))
    return found


def _seam_excuse(name, label, line):
    """The _SEAM_ALLOWLIST key excusing a (token, class) seam on `line`, or
    None.

    A malformed key matches NOTHING here rather than raising: key shapes are
    validated in _seam_stale_allowlist, which fails by name and explains what
    the entry does not do. A malformed key that raised from inside the scan
    would surface as an exception in whatever case happened to run first."""
    for key in _SEAM_ALLOWLIST:
        if not (isinstance(key, tuple) and len(key) == 3):
            continue
        token, cls, marker = key
        if (token, cls) == (name, label) and isinstance(marker, str) and marker \
                and marker in line:
            return key
    return None


def _seam_defects(template_text, values, apply_allowlist=True):
    """Every seam defect in a filled template, as
    [(token, class, why, rendered_context), ...].

    NOT pure: with apply_allowlist=True (the default) it also reads the
    module-level _SEAM_ALLOWLIST, so the same arguments answer differently
    once an entry is added -- which is exactly what the allowlist case below
    exercises by mutating that dict around a call. The rest of what it reads
    is its arguments plus the rule tables listed on _seam_scan above -- never
    a file and never a household figure, which is what lets the negative case
    drive it with the pre-issue-129 text.

    apply_allowlist=False reports the excused seams too -- that is how
    _seam_stale_allowlist proves an entry still has the seam it excuses."""
    return [(name, label, why, context)
            for name, label, why, context, line in _seam_scan(template_text, values)
            if not (apply_allowlist and _seam_excuse(name, label, line))]


def _seam_stale_allowlist(template_text, values):
    """The _SEAM_ALLOWLIST entries that excuse NO seam in this filled template,
    as [((token, class, marker), reason), ...].

    One helper, driven by the live case against report-template.html and by
    the allowlist case against a synthetic fragment, so the comparison that
    decides staleness is exercised in both directions on every run rather than
    only claimed to be.

    A MALFORMED key fails here by name instead of being reported as stale. A
    bare string, a tuple of the wrong length, a class name that is not one of
    _SEAM_CLASSES, a token this run does not resolve, or an empty marker can
    never excuse an occurrence, so the plain difference would call every one of
    them "an excuse whose seam has since been fixed -- delete the entry",
    sending the reader to look for a fix that was never made when the truth is
    that the entry never suppressed anything in the first place.

    A marker matching MORE THAN ONE template line fails here too, and that one
    is not a typo check: an over-broad marker is a token-wide pardon wearing
    the disguise of an occurrence-level one, which is the loophole the marker
    exists to close."""
    lines = template_text.splitlines()
    for key in _SEAM_ALLOWLIST:
        assert isinstance(key, tuple) and len(key) == 3, (
            f"_SEAM_ALLOWLIST key {key!r} is not a (token, class, marker) triple; the "
            "allowlist is keyed by the TRIPLE so that one excuse cannot blind a token "
            "to every defect class, nor the same class at every occurrence of it, and "
            "a key of another shape suppresses nothing at all")
        name, label, marker = key
        assert label in _SEAM_CLASSES, (
            f"_SEAM_ALLOWLIST key {key!r} names the defect class {label!r}, which is "
            f"not one of {list(_SEAM_CLASSES)}; it can never match a reported seam")
        assert name in values, (
            f"_SEAM_ALLOWLIST key {key!r} names the token {name!r}, which this run "
            "does not resolve (a typo, a renamed token, or a KNOWN_GAPS token that is "
            "never rendered); it can never match a reported seam")
        assert isinstance(marker, str) and marker, (
            f"_SEAM_ALLOWLIST key {key!r} carries an empty or non-string marker; the "
            "marker is the template line the entry excuses, and an empty one excuses "
            "every line, which is the token-wide pardon this key shape exists to stop")
        carrying = [line for line in lines if marker in line]
        assert len(carrying) <= 1, (
            f"_SEAM_ALLOWLIST key {key!r} has a marker matching {len(carrying)} template "
            "lines; an entry excuses ONE occurrence, so the marker has to name one line. "
            "Lengthen it until it does -- a marker matching several lines pardons the "
            "same class at every one of them, which is what keying by (token, class) "
            "alone already did")
    hits = _seam_scan(template_text, values)
    return [(key, why) for key, why in _SEAM_ALLOWLIST.items()
            if not any(_seam_excuse(name, label, line) == key
                       for name, label, _w, _c, line in hits)]


class _seam_stand_in_household:
    """private/household.yaml stood in for by the COMMITTED
    household.example.yaml, so the seam guard checks EVERY non-gap token on a
    runner that has no private archive instead of only the ones that read
    data/ alone.

    Three of the example file's placeholder answers have to agree with
    committed artifacts or the token they feed refuses to render (correctly --
    a plan the plan table never priced, a cleaning date the cleaning study
    never covered). Each is taken FROM the artifact rather than written here:
    the rate plan and the generation provider off data/plan_results.csv, the
    cleaning date off data/cleaning_study_daily.csv. Nothing else is touched,
    nothing is written to disk, and the household's own answers are never read
    on this path -- CLAUDE.md section 4 keeps them out of this committed file.

    WHAT THIS PATH DOES AND DOES NOT REPRODUCE. Most of a rendered value's
    shape -- its sigils, its unit, its bare-numberness, its thousands
    separators -- comes from the token's formatter and formula, which are the
    same code on both paths, so a seam that depends only on those is caught
    here exactly as it is against the real archive. THE SIGN IS NOT SUCH A
    SHAPE, and that was measured rather than supposed: CLEANING_EFFECT_PCT was
    observed rendering with OPPOSITE SIGNS on the two paths, because the effect
    is computed around whatever cleaning date it is given and this class hands
    it the MEDIAN day of the cleaning study rather than a real one. Neither
    rendering is quoted here: both are household figures that move with the
    archive and with data/cleaning_study_daily.csv, and a figure written into
    this file as a literal goes stale silently while still reading like a
    fact. "-" is a member of _SEAM_SIGILS. A template that put a minus sign in
    front of such a token would therefore be flagged on one path and not the
    other. Residual risk, stated: CI and a staged checkout can disagree about
    a sign-adjacent seam, in either direction. The class-1 rule is what would
    disagree; classes 2 and 3 read digits, not signs.

    Coherence between tokens is enforced rather than assumed -- see the
    provider patch in __enter__."""

    def __enter__(self):
        import yaml
        node = yaml.safe_load((rt.ROOT / "household.example.yaml").read_text())
        rows = rt._csv_rows("plan_results.csv")
        assert rows, "data/plan_results.csv is empty; the stand-in has no plan to use"
        self.provider = rows[0]["provider"]
        priced = [r["plan"] for r in rows if r["provider"] == self.provider]
        assert priced, f"data/plan_results.csv prices no plan for {self.provider!r}"
        node["household"]["plan"] = priced[0]
        days = sorted(dt.datetime.strptime(r["date"], "%Y%m%d").date()
                      for r in rt._csv_rows("cleaning_study_daily.csv"))
        assert (days[-1] - days[0]).days >= 60, (
            "data/cleaning_study_daily.csv spans fewer than 60 days, so no stand-in "
            "cleaning date has a 30-day window on both sides of it")
        node["cleaning_history"] = [{"date": days[len(days) // 2], "cost_usd": 150}]
        # The provenance answers (issue #135). household.example.yaml leaves the
        # two review fields null ON PURPOSE -- "nobody reviewed this" is the
        # honest default for a reproduction, and REVIEW_TOOL_1/2 refuse to
        # render a name that would claim otherwise. That refusal is correct and
        # is tested elsewhere; here it would stop the seam guard before it
        # checked a single seam. This path exercises RENDERING, not provenance
        # policy, so it answers as a household that did have both reviews.
        node["provenance"] = {
            "generation_tool": "Stand-In Generator (v0)",
            "review_tool_independent": "Stand-In Independent Reviewer (v0)",
            "review_tool_adversarial": "Stand-In Adversarial Reviewer (v0)",
        }
        self.old_cache, self.old_path = rt.hh._cache, rt.hh.PATH
        self.old_provider = rt._generation_provider_short
        rt.hh._cache = node
        rt.hh.PATH = rt.ROOT / "household.example.yaml"

        # The plan ranking looks the household's plan up by PROVIDER in
        # data/plan_results.csv, so the stand-in has to answer with the
        # provider that artifact actually prices; the example file's
        # placeholder CCA name yields a different acronym.
        #
        # Patching the module global alone is NOT enough, and the two-token
        # contradiction it produced is the reason this is spelled out: a spec
        # registered as get=_generation_provider_short captured the ORIGINAL
        # function object at import, so GENERATION_PROVIDER_SHORT kept
        # answering "ECE" (from the example file) while GENERATION_PROVIDER,
        # which reaches the global through a lambda, answered "... (CEA)"
        # (from the artifact). Two tokens that print the same provider printed
        # two different ones. So every spec still holding the original
        # function is repointed too -- found by identity, so a token
        # registered against it tomorrow is patched tomorrow with no edit
        # here. The long name stays the example file's placeholder, because
        # data/plan_results.csv commits the acronym and nothing else: the
        # stand-in reads "Example Community Energy (CEA)", which is a
        # placeholder company wearing the artifact's acronym rather than a
        # real provider's name. That is as coherent as a committed stand-in
        # can be, and it is the relation the report depends on -- the two
        # tokens print ONE acronym. _assert_provider_coherent fails the
        # context manager rather than letting them print two.
        stand_in = lambda ctx: self.provider          # noqa: E731
        self.old_specs = [(spec, spec["get"]) for spec in rt.TOKENS.values()
                          if spec.get("get") is self.old_provider]
        rt._generation_provider_short = stand_in
        for spec, _old in self.old_specs:
            spec["get"] = stand_in
        try:
            self._assert_provider_coherent()
        except BaseException:
            # __exit__ never runs for a context manager whose __enter__ raised,
            # so an incoherent stand-in must not leave the module patched. It
            # FAILS here rather than letting the guard render a report in which
            # two provider tokens disagree.
            self.__exit__(None, None, None)
            raise
        return self

    def _assert_provider_coherent(self):
        short = rt.resolve_token("GENERATION_PROVIDER_SHORT",
                                 rt.TOKENS["GENERATION_PROVIDER_SHORT"])
        long = rt.resolve_token("GENERATION_PROVIDER", rt.TOKENS["GENERATION_PROVIDER"])
        assert short == self.provider, (
            f"the stand-in household renders GENERATION_PROVIDER_SHORT as {short!r} "
            f"but ranks plans as {self.provider!r}; the patch no longer reaches every "
            "consumer of the provider acronym")
        assert long.endswith(f"({short})"), (
            f"the stand-in household renders GENERATION_PROVIDER as {long!r}, which "
            f"does not carry the acronym {short!r} that GENERATION_PROVIDER_SHORT "
            "prints; the two would contradict each other in the report")

    def __exit__(self, *exc):
        rt.hh._cache, rt.hh.PATH = self.old_cache, self.old_path
        rt._generation_provider_short = self.old_provider
        for spec, old in self.old_specs:
            spec["get"] = old


def _seam_values():
    """{token: rendered value} for every non-gap token, plus the names skipped.

    Resolved against the real archive where it is staged and against the
    committed stand-in otherwise, so the same tokens are checked in both
    places -- the set, not a count of it, is what the case asserts."""
    def resolve():
        out = {}
        for name, spec in rt.TOKENS.items():
            if spec.get("kind") == "gap":
                continue
            out[name] = rt.resolve_token(name, spec)
        return out

    if rt.hh.PATH.is_file():
        values = resolve()
    else:
        with _seam_stand_in_household():
            values = resolve()
    return values, set(rt.KNOWN_GAPS)


def _seam_report(defects):
    return "\n  ".join(
        f"{name} [{label}]: {why}\n      rendered: ...{context}..."
        for name, label, why, context in defects)


@case
def case_no_token_renders_a_broken_seam_in_its_own_template_context():
    """ISSUE #133. Every {{TOKEN}} in report-template.html, substituted into
    the line it really sits on, with the rendered result checked for a doubled
    sigil or unit, a bare number that lost its unit, and a figure echoed twice
    across the seam. See the block comment above for the three rules and for
    what they deliberately do not flag.

    Runs without the private archive: see _seam_stand_in_household."""
    values, gaps = _seam_values()
    template = rt.TEMPLATE.read_text()
    defects = _seam_defects(template, values)
    assert not defects, (
        f"{len(defects)} token/template seam(s) render wrong once the token resolves:\n  "
        + _seam_report(defects))

    # The guard has to have LOOKED at something. Coverage is asserted against
    # the template's own token inventory, so a parser that silently stopped
    # finding occurrences fails here rather than reporting a clean sweep.
    live, comment_only = rt.template_tokens()
    seen = {name for line in template.splitlines()
            for name, _s, _e in _seam_render(line, values)[1]}
    expected = (live | comment_only) - gaps
    assert seen == expected, (
        "the seam guard did not render every token the template carries -- "
        f"missed {sorted(expected - seen)}, invented {sorted(seen - expected)}")
    assert gaps == {n for n, s in rt.TOKENS.items() if s.get("kind") == "gap"}, (
        "the skipped set is no longer exactly KNOWN_GAPS")
    # The floor is DERIVED from the token inventory, not written down: every
    # token the guard is meant to cover contributes at least one occurrence,
    # so a parser that lost occurrences lands below it. A hard number here --
    # the count of occurrences, or the share of them inside <!-- TODO -->
    # blocks -- would fail on an ordinary prose pass and blame the parser, so
    # both are computed here and reported rather than committed as literals.
    occurrences = sum(len(_seam_render(line, values)[1]) for line in template.splitlines())
    assert occurrences >= len(expected), (
        f"only {occurrences} token occurrences were rendered for {len(expected)} "
        "tokens, so some token was rendered fewer than once; the occurrence parser broke")
    in_live_markup = sum(len(_seam_render(line, values)[1])
                         for line in _seam_comment_mask(template).splitlines())
    # STRICTLY fewer, and the strictness is the whole assertion: masking only
    # ever replaces '[^\n]' with a space, so the masked token set is provably a
    # SUBSET and "<=" is a tautology that an identity mask passes. What can be
    # observed is that the mask actually removed something, i.e. that this
    # template still puts at least one token inside a comment span.
    assert in_live_markup < occurrences, (
        f"all {occurrences} token occurrences survive the comment mask, so none of "
        "them is inside a comment span. Either report-template.html no longer "
        "carries a {{TOKEN}} inside a <!-- TODO --> block -- in which case say so "
        "here and in case_the_shipped_line_fixture_decides_liveness_by_comment_span, "
        "which asserts the same thing from the other end -- or _seam_comment_mask "
        "stopped masking, which is exactly where an identity mask lands")

    stale = _seam_stale_allowlist(template, values)
    assert not stale, (
        f"_SEAM_ALLOWLIST excuses occurrence(s) that no longer have that seam: "
        f"{stale} -- delete the entry rather than leaving a loophole behind")

    # THE ALLOWLIST'S OWN ACCOUNTING, one entry to one occurrence. The stale
    # check proves every entry excuses at least one seam and the assertion
    # above proves every seam is excused by something; neither proves the
    # mapping is ONE TO ONE, and the gap between them is the marker's stated
    # limit -- a marker cannot separate two occurrences of the same token on
    # the same line, so one entry could quietly pardon two. Counted here, so
    # if the template ever grows that shape it fails rather than passing with
    # a hidden second pardon.
    excused = [_seam_excuse(name, label, line)
               for name, label, _w, _c, line in _seam_scan(template, values)]
    assert all(excused), (
        f"the seam scan reports {excused.count(None)} unexcused seam(s) the defect "
        "list did not; _seam_defects and _seam_scan disagree")
    assert len(excused) == len(set(excused)) == len(_SEAM_ALLOWLIST), (
        f"{len(excused)} excused occurrence(s) share {len(set(excused))} allowlist "
        f"entr(ies) out of {len(_SEAM_ALLOWLIST)} committed; one entry is pardoning "
        "more than one occurrence (the same token twice on one line) or an entry "
        "excuses nothing")
    # THE BARE-NUMBER DISPOSITION, derived rather than written down. A bare
    # number is the shape the prose half of class 2 exists for, and every one
    # of them leaves _seam_missing_unit by exactly one of its exits: a
    # declared dimension the value no longer carries, a sigil in front,
    # nothing but markup behind it to the end of the line, a unit behind it, a
    # letterless next word, or a report -- which must then be allowlisted,
    # since the assertion at the top of this case says nothing is left
    # unexcused. The branch chain below is that function's
    # exits IN ITS OWN ORDER, and each occurrence increments exactly one
    # bucket, so the buckets are a partition by construction and the totals
    # cannot be mis-stated the way a hand-counted split can be. The chain is
    # not trusted to mirror the rule either: every occurrence asserts that the
    # bucket it landed in agrees with what _seam_missing_unit actually
    # returned, so a chain that drifts from the function fails here rather
    # than quietly reporting a wrong split.
    #
    # "lost its declared dimension" leads the chain because it leads the rule,
    # and it is a REPORTING bucket rather than a quiet one: a money token
    # rendering a bare number is a defect no matter what follows it, which is
    # the hole "carrying a unit" used to swallow. It stays at zero on the
    # shipped template -- every dimension-declaring token renders its sigil --
    # and case_a_declared_money_or_percent_token_that_loses_its_sigil_is_
    # reported is where it is driven off zero on purpose.
    quiet = ("carrying a unit", "sigil-fronted", "at end of line",
             "before a letterless word")
    disposition = dict.fromkeys(("lost its declared dimension",) + quiet
                                + ("allowlisted", "reported"), 0)
    bare = 0
    for line in template.splitlines():
        rendered, spans = _seam_render(line, values)
        for name, start, end in spans:
            value, head, tail = rendered[start:end], rendered[:start], rendered[end:]
            if not _SEAM_BARE_NUMBER_RE.match(value):
                continue
            bare += 1
            fmt = rt.TOKENS.get(name, {}).get("fmt")
            dimension, _source = _seam_declared_dimension(name, fmt, value)
            text = _SEAM_TAG_RE.sub(" ", tail).lstrip()
            if dimension is not None and dimension not in value \
                    and head[-1:] != dimension and tail[:1] != dimension:
                bucket = "lost its declared dimension"
            elif head[-1:] in _SEAM_SIGILS:
                bucket = "sigil-fronted"
            elif not text:
                bucket = "at end of line"
            elif _seam_unit_prefix(text) is not None:
                bucket = "carrying a unit"
            elif not any(c.isalpha() for c in text.split()[0]):
                bucket = "before a letterless word"
            elif _seam_excuse(name, "missing-unit", line):
                bucket = "allowlisted"
            else:
                bucket = "reported"
            disposition[bucket] += 1
            why = _seam_missing_unit(value, head, tail, fmt, name)
            assert (why is None) == (bucket in quiet), (
                f"the bare-number disposition puts {name} on template line "
                f"{line.strip()[:60]!r} in the {bucket!r} bucket, but "
                f"_seam_missing_unit said {why!r}; the branch chain here no longer "
                "mirrors that rule's exits")
    assert sum(disposition.values()) == bare, (
        f"the disposition buckets {disposition} sum to {sum(disposition.values())}, not "
        f"the {bare} bare-number occurrence(s) counted; a bare number fell into more "
        "than one bucket or into none")
    assert disposition["reported"] == 0, (
        f"{disposition['reported']} bare number(s) lost a unit and are not allowlisted, "
        "which the defect assertion at the top of this case should already have said")
    assert disposition["lost its declared dimension"] == 0, (
        f"{disposition['lost its declared dimension']} token(s) declared with a money "
        "or percent format render a bare number with no sigil on it or beside it; the "
        "defect assertion at the top of this case should already have said so")
    split = ", ".join(f"{n} {label}" for label, n in disposition.items())
    return (f"{occurrences} token occurrences across {len(seen)} tokens render clean at "
            f"their template seams ({in_live_markup} in live markup, "
            f"{occurrences - in_live_markup} inside comment spans; {bare} of them bare "
            f"numbers -- {split}; {len(gaps)} KNOWN_GAPS tokens skipped, "
            f"{len(_SEAM_ALLOWLIST)} occurrence(s) allowlisted"
            + (" -- resolved against the real archive)" if rt.hh.PATH.is_file()
               else " -- resolved against the committed stand-in household)"))


@case
def case_provenance_fields_refuse_anything_that_is_not_a_tool_name():
    """Issue #135. These three values are published as the names of the tools
    that produced and checked the report, so anything that merely stringifies
    is a false claim rather than a formatting problem.

    `review_tool_independent: false` is the case that matters: it is a
    realistic way to write "nobody reviewed it", YAML parses it as a boolean,
    and a truthiness check passes it straight through to "the data, methodology,
    and conclusions were then independently reviewed with False". Lists and
    mappings render just as readably and just as wrongly. null, and only null,
    means nobody."""
    _require_household()
    real = rt.hh._cache
    base = dict(rt.hh._load())
    try:
        for field, token in (("generation_tool", "GENERATION_TOOL"),
                             ("review_tool_independent", "REVIEW_TOOL_1"),
                             ("review_tool_adversarial", "REVIEW_TOOL_2")):
            for bad in (False, True, 0, 5, ["a name"], {"tool": "a name"}):
                prov = {"generation_tool": "A Generator",
                        "review_tool_independent": "An Independent Reviewer",
                        "review_tool_adversarial": "An Adversarial Reviewer"}
                prov[field] = bad
                base["provenance"] = prov
                rt.hh._cache = base
                try:
                    rendered = rt.resolve_token(token)
                except SystemExit:
                    continue
                raise AssertionError(
                    f"provenance.{field} = {bad!r} rendered {token} as "
                    f"{rendered!r} instead of refusing -- the report would "
                    "publish that as the name of a tool")
        # POSITIVE CONTROL: a real name still renders, so the check above is
        # rejecting the shape and not simply refusing everything.
        base["provenance"] = {"generation_tool": "A Generator",
                              "review_tool_independent": "An Independent Reviewer",
                              "review_tool_adversarial": "An Adversarial Reviewer"}
        rt.hh._cache = base
        assert rt.resolve_token("REVIEW_TOOL_1") == "An Independent Reviewer"
        # null is the one non-string that is allowed, and it refuses to RENDER
        # rather than refusing to parse -- a different, documented path.
        base["provenance"]["review_tool_independent"] = None
        rt.hh._cache = base
        try:
            rt.resolve_token("REVIEW_TOOL_1")
            raise AssertionError("a null review field rendered a name")
        except rt.ProvenanceUnanswered as e:
            # The dedicated type matters, not just the message: resolve_all()
            # uses it to skip this token instead of losing every other one.
            assert "not answered" in str(e), str(e)
    finally:
        rt.hh._cache = real
    return ("the three provenance fields refuse booleans, numbers, lists and "
            "mappings, and take null to mean nobody")


@case
def case_the_shipped_provenance_placeholder_is_refused():
    """A reproduction that copies household.example.yaml and forgets this one
    field must not publish "generated with REPLACE ME".

    The placeholder is READ OFF the example file rather than written here, so
    changing that file without changing the refusal set fails this case instead
    of silently reopening the hole. Quoted no-review words are checked with it:
    "false" is a string, so the type check cannot see it, and it would render
    as the name of a reviewer."""
    _require_household()
    import yaml
    node = yaml.safe_load((rt.ROOT / "household.example.yaml").read_text())
    shipped = node["provenance"]["generation_tool"]
    assert isinstance(shipped, str) and shipped.strip(), (
        "household.example.yaml's generation_tool is no longer a placeholder string; "
        "this case assumes the example ships one for a reproducer to replace")
    assert shipped.strip().casefold() in rt._PROVENANCE_NON_ANSWERS, (
        f"household.example.yaml ships {shipped!r} but report_tokens.py would accept "
        "it as a tool name -- a reproduction that copied the file unedited would "
        "publish it")

    real = rt.hh._cache
    base = dict(rt.hh._load())
    try:
        for bad in (shipped, "false", "None", " tbd ", "n/a", "TODO"):
            base["provenance"] = {"generation_tool": bad,
                                  "review_tool_independent": "An Independent Reviewer",
                                  "review_tool_adversarial": "An Adversarial Reviewer"}
            rt.hh._cache = base
            try:
                rendered = rt.resolve_token("GENERATION_TOOL")
            except SystemExit:
                continue
            raise AssertionError(
                f"provenance.generation_tool = {bad!r} rendered as {rendered!r} "
                "instead of refusing")
        # POSITIVE CONTROL: a real name still renders, so this is rejecting
        # placeholders rather than refusing everything.
        base["provenance"]["generation_tool"] = "Claude Code (Opus 5)"
        rt.hh._cache = base
        assert rt.resolve_token("GENERATION_TOOL") == "Claude Code (Opus 5)"
    finally:
        rt.hh._cache = real
    return (f"the shipped placeholder {shipped!r} and quoted no-review words are refused, "
            "while a real tool name renders")


@case
def case_an_unanswered_review_does_not_take_resolve_all_down():
    """Issue #135, found by /review. resolve_all() is all-or-nothing, and null
    review fields are the DOCUMENTED default -- household.example.yaml ships
    them, CLAUDE.md section 11 recommends them, the README step says to leave
    them. Refusing to render them is right; letting that refusal abort the bulk
    resolve lost the household all 218 tokens over the one sentence it had
    answered correctly.

    That is the shape this file already records twice, and the fix for the
    generate_report caller did not cover this one -- a patch, not a sweep.

    The split this pins: NOT ANSWERED is skipped like a gap; a MISTAKE still
    fails the whole resolve, because a placeholder or a boolean is something to
    fix rather than a state to render around."""
    _require_household()
    real = rt.hh._cache
    base = dict(rt.hh._load())
    filled = {"generation_tool": "A Generator",
              "review_tool_independent": "An Independent Reviewer",
              "review_tool_adversarial": "An Adversarial Reviewer"}
    try:
        base["provenance"] = dict(filled)
        rt.hh._cache = base
        full = len(rt.resolve_all())
        assert full > 100, f"resolve_all returned only {full} tokens with everything filled"

        for label, prov in (
            ("both review fields null",
             {**filled, "review_tool_independent": None, "review_tool_adversarial": None}),
            ("review keys omitted entirely", {"generation_tool": filled["generation_tool"]}),
        ):
            base["provenance"] = prov
            rt.hh._cache = base
            out = rt.resolve_all()
            assert len(out) == full - 2, (
                f"with {label}, resolve_all returned {len(out)} of {full} tokens -- an "
                "unanswered provenance field must cost only its own token")
            assert "REVIEW_TOOL_1" not in out and "REVIEW_TOOL_2" not in out, (
                "an unanswered review field rendered a value instead of being skipped")

        # POSITIVE CONTROL: a MISTAKE must still take the resolve down, or this
        # case would pass against a version that simply swallowed everything.
        for label, prov in (
            ("the shipped placeholder", {**filled, "generation_tool": "REPLACE ME"}),
            ("a boolean review name", {**filled, "review_tool_independent": False}),
        ):
            base["provenance"] = prov
            rt.hh._cache = base
            try:
                rt.resolve_all()
            except SystemExit:
                continue
            raise AssertionError(
                f"resolve_all succeeded with {label}; a malformed provenance value must "
                "fail the run, not be skipped like an unanswered one")
    finally:
        rt.hh._cache = real
    return ("an unanswered review field costs only its own token, while a placeholder "
            "or a malformed one still fails the whole resolve")


@case
def case_the_seam_guard_checks_the_same_tokens_without_the_private_archive():
    """The case above resolves against private/household.yaml where it is
    staged, and .github/workflows/tests.yml runs this suite where it is not.
    A guard that quietly checks the data/-only tokens on the runner that
    actually guards merges is the failure mode this file has already recorded twice
    (the SEC9 and verdict round-trip cases), so the archive-less path is
    driven HERE, on every checkout, and asserted to cover the same set.

    Skipped only where the archive is absent, because then the case above IS
    this case."""
    _require_household()
    with _seam_stand_in_household():
        stand_in = {name: rt.resolve_token(name, spec)
                    for name, spec in rt.TOKENS.items() if spec.get("kind") != "gap"}
    real, gaps = _seam_values()
    assert set(stand_in) == set(real), (
        "the stand-in household resolves a different token set than the real archive: "
        f"missing {sorted(set(real) - set(stand_in))}, extra {sorted(set(stand_in) - set(real))}")
    defects = _seam_defects(rt.TEMPLATE.read_text(), stand_in)
    assert not defects, (
        f"{len(defects)} seam(s) render wrong under the committed stand-in household, so "
        "CI would fail where the staged archive passes:\n  " + _seam_report(defects))
    return (f"the archive-less path resolves the same {len(stand_in)} non-gap tokens and "
            f"renders every seam clean ({len(gaps)} gaps skipped)")


def _seam_plain_number_fmt():
    """The name of a report_tokens format spec that renders the probe as a
    BARE NUMBER -- no dimension sigil, no unit, nothing but digits.

    This is the regression the sweep below simulates: issue #129's defect was
    one word in a token declaration, pct0 -> num0, and the money half of the
    same slip is usd0 -> num0. Chosen by probing FORMATTERS rather than named,
    for the reason the dimension table is derived rather than named -- and
    picked deterministically (sorted first) so the sweep's numbers are the
    same on every run and in every checkout."""
    plain = sorted(name for name, fn in rt.FORMATTERS.items()
                   if name not in rt._NON_NUMERIC_FMTS
                   and _SEAM_BARE_NUMBER_RE.match(fn(_SEAM_FMT_PROBE)))
    assert plain, (
        "no format spec in report_tokens.FORMATTERS renders a bare number any more, so "
        "the sigil-dropping regression this case exists to simulate cannot be built. "
        "That is a claim about report_tokens, not about this test: say which spec "
        "replaced num0 and probe it here")
    return plain[0]


def _seam_regressed_values(plain):
    """{token: its value re-rendered through `plain`} for every token whose
    DECLARED format carries a dimension.

    The mutation is applied where the defect really happens -- to the token's
    format spec, re-resolved through report_tokens' own resolver -- rather
    than by editing the rendered string here. A hand-edited string proves the
    rule fires on a shape someone typed; this proves it fires on what
    report_tokens actually publishes when a declaration slips.

    Resolved through the same two paths as _seam_values, so the sweep covers
    the same tokens on a runner with no private archive."""
    def resolve():
        return {name: rt.resolve_token(name, dict(spec, fmt=plain))
                for name, spec in rt.TOKENS.items()
                if spec.get("kind") != "gap" and spec.get("fmt") in _SEAM_FMT_DIMENSIONS}

    if rt.hh.PATH.is_file():
        return resolve()
    with _seam_stand_in_household():
        return resolve()


@case
def case_a_declared_money_or_percent_token_that_loses_its_sigil_is_reported():
    """The hole class 2 shipped with, closed and measured: a money token whose
    format regresses to a plain number renders "3,282/yr", and "/yr" is a
    member of _SEAM_UNITS, so the prose whitelist answered "something beside
    it gives it a unit" and the suite stayed green on a bill with no currency
    on it. It is the same one-word slip as issue #129's pct0 -> num0, one
    dimension over, and issue #133's own title names it.

    THE SWEEP IS THE ASSERTION, not an anecdote. Every occurrence in
    report-template.html of a token whose declared format carries a dimension
    is re-resolved with that format swapped for a bare-number one, and the
    guard must report it. The prose-only rule is run against the same
    occurrences in the same loop, so the before and after numbers come from
    one measurement rather than from two claims -- and the case fails if the
    prose rule ever turns out to have caught them all along, which is what a
    sweep that proves nothing would look like.

    Two things it deliberately does NOT do. It does not name a token: the rule
    reads report_tokens.TOKENS[...]["fmt"] and the dimension table is derived
    from FORMATTERS, so a money token added tomorrow is swept tomorrow with no
    edit here -- that is the property case_the_seam_rules_are_generic_not_a_
    case_per_token asserts for the other rules. And it does not touch the
    allowlist: an entry excusing one of these would fail the sweep, which is
    correct. A count that is genuinely dimensionless does not get declared
    usd0.

    THE BLIND SPOT IT USED TO COUNT IS NOW SWEPT AND ASSERTED (issue #163). A
    token that formats itself declares its dimension with `dim` instead of a
    format spec, so step 2b sweeps those occurrences the only way their defect
    can be simulated -- by taking the sigil back out of the value -- and step
    3's count of tokens declaring NOTHING is asserted at zero rather than
    reported. Both numbers are still computed from the registry, so they track
    it rather than going stale. See the block comment above."""
    values, _gaps = _seam_values()
    template = rt.TEMPLATE.read_text()

    # 1. The derivation still says what the floor says it says, and the floor
    #    is what a derivation cannot check about itself.
    assert _seam_fmt_dimensions() == _SEAM_FMT_DIMENSIONS, (
        "report_tokens.FORMATTERS classifies differently now than it did at import; "
        "the seam guard's dimension table is stale for this run")
    for fmt, sigil in _SEAM_FMT_DIMENSION_FLOOR.items():
        assert _SEAM_FMT_DIMENSIONS.get(fmt) == sigil, (
            f"format spec {fmt!r} no longer classifies as {sigil!r} (it is now "
            f"{_SEAM_FMT_DIMENSIONS.get(fmt)!r}), so every token declared with it can "
            "drop its sigil unseen. Narrowing this guard is allowed and hiding it is "
            f"not: drop {fmt!r} from _SEAM_FMT_DIMENSION_FLOOR in the same commit, "
            "where a reviewer reads it")
    # ...and the table discriminates: a plain-number format is not a dimension,
    # or the rule would fire on every figure in the report.
    plain = _seam_plain_number_fmt()
    assert plain not in _SEAM_FMT_DIMENSIONS, (
        f"the bare-number format {plain!r} classifies as the dimension "
        f"{_SEAM_FMT_DIMENSIONS[plain]!r}; the derivation is reading a sigil that is "
        "not there and every token in the report is about to be reported")

    # 2. The sweep. One token is regressed at a time, against the real
    #    template line it sits on, through _seam_defects -- so this exercises
    #    the whole path, including _seam_scan's registry lookup, and not just
    #    the rule in isolation.
    regressed = _seam_regressed_values(plain)
    assert regressed, (
        "no token in report_tokens.TOKENS declares a money, percent or cents format, "
        "so this sweep checks nothing. That would be a real change to the registry, "
        "not a passing test")
    swept = caught_before = 0
    unit_fronted = []
    for line in template.splitlines():
        rendered, spans = _seam_render(line, values)
        for name, start, end in spans:
            if name not in regressed:
                continue
            swept += 1
            value, head, tail = rendered[start:end], rendered[:start], rendered[end:]
            fmt = rt.TOKENS[name]["fmt"]
            # The value the report would really publish after the slip.
            broken = regressed[name]
            hits = _seam_defects(line, dict(values, **{name: broken}))
            assert any(n == name and c == "missing-unit" for n, c, _w, _x in hits), (
                f"{name} declares {fmt!r} and would publish {broken!r} on template line "
                f"{line.strip()[:70]!r} if that format slipped to {plain!r}, and the "
                f"seam guard reports {[(n, c) for n, c, _w, _x in hits] or 'nothing'}. "
                "A figure with no currency or percent sign on it is the defect issue "
                "#133 is named for")
            # The prose-only rule, on the same occurrence, in the same loop:
            # this is the "before" number, measured rather than remembered.
            b_head, b_tail = rendered[:start], rendered[end:]
            if _seam_missing_unit(broken, b_head, b_tail) is not None:
                caught_before += 1
            else:
                text = _SEAM_TAG_RE.sub(" ", b_tail).lstrip()
                if _seam_unit_prefix(text) is not None:
                    unit_fronted.append((name, fmt, _seam_unit_prefix(text)))
    assert caught_before < swept, (
        f"the prose-only half of class 2 already caught all {swept} sigil-dropping "
        "regressions, so the declared-dimension test is not what is catching them and "
        "this case proves nothing about it")
    assert unit_fronted, (
        "not one of the regressions the prose rule missed was missed BECAUSE a unit "
        "follows the figure, which is the specific hole this test closes "
        "(\"{{X}}/yr\" answering for a missing \"$\"). Either report-template.html "
        "stopped printing money figures with a period behind them, or _SEAM_UNITS "
        "stopped containing '/yr' -- say which, here")

    # 2b. THE OTHER HALF OF THE SWEEP, and the half this case did not have
    #     (issue #163). A token that formats ITSELF declares its dimension
    #     with `dim` rather than through a format spec, so there is no format
    #     to slip -- the regression is the lambda dropping the sigil it types
    #     by hand, and that is what is simulated here: the declared dimension
    #     is taken back out of the value the token really publishes, and the
    #     guard must report the occurrence.
    #
    #     Mutating the STRING rather than the declaration is a real weakening
    #     against 2's method and it is the only mutation available, because
    #     the sigil is not in a declaration to mutate; that is the whole
    #     reason `dim` exists. What it still proves is the thing that was
    #     previously proven for nothing at all: the seam this class of token
    #     sits on now has a rule reading it.
    dim_swept, dim_tokens = 0, set()
    for line in template.splitlines():
        rendered, spans = _seam_render(line, values)
        for name, start, end in spans:
            dim = rt.declared_dimension(name)
            if dim is None or dim not in values[name]:
                continue
            dim_swept += 1
            dim_tokens.add(name)
            broken = values[name].replace(dim, "")
            hits = _seam_defects(line, dict(values, **{name: broken}))
            assert any(n == name and c == "missing-unit" for n, c, _w, _x in hits), (
                f"{name} declares dim={dim!r} and would publish {broken!r} on template "
                f"line {line.strip()[:70]!r} if the lambda that types that sigil by "
                f"hand dropped it, and the seam guard reports "
                f"{[(n, c) for n, c, _w, _x in hits] or 'nothing'}")
    assert dim_tokens, (
        "no token in report_tokens.TOKENS declares a `dim`, so this half of the sweep "
        "checks nothing. Either every self-formatting figure learned a real format "
        "spec -- say so here and delete this half -- or the declarations were dropped")

    # 3. The blind spot this case used to COUNT is now an assertion (issue
    #    #163). The counting formula is unchanged -- a token in the template
    #    whose value is ONE figure wearing a dimension sigil -- and what
    #    changed is that such a token now has two ways to say so and no way to
    #    stay silent: a format spec the registry renders through, or its own
    #    `dim`. `phrase` is read here as well, so a value that is a SENTENCE
    #    carrying figures is excluded by declaration rather than by shape;
    #    without that the count could only be driven to zero by pretending
    #    every verdict line is a figure.
    seen = {name for line in template.splitlines()
            for name, _s, _e in _seam_render(line, values)[1]}
    undeclared = sorted(
        name for name in seen
        if rt.TOKENS.get(name, {}).get("fmt") not in _SEAM_FMT_DIMENSIONS
        and rt.declared_dimension(name) is None
        and not rt.is_phrase(name)
        and any(s in values[name] for s in _SEAM_DIMENSION_SIGILS)
        and _SEAM_BARE_NUMBER_RE.match(
            "".join(c for c in values[name]
                    if c not in _SEAM_DIMENSION_SIGILS and c not in "~+ ")))
    assert not undeclared, (
        f"{len(undeclared)} single-figure token(s) in the template format themselves "
        f"and declare no dimension for this rule to read: {undeclared}. Each publishes "
        "one figure wearing a money, percent or cents sign, and a regression inside its "
        "own lambda is caught by nothing. Declare a `fmt` the registry can render it "
        "through, or a `dim` saying which dimension the figure is measured in")
    return (f"all {swept} occurrence(s) of a token declaring a money, percent or cents "
            f"format are caught when that format slips to {plain!r}; the prose-only rule "
            f"caught {caught_before} of them, and {len(unit_fronted)} of the "
            f"{swept - caught_before} it missed were missed because a unit "
            f"({sorted({u for _n, _f, u in unit_fronted})}) sits where the sigil should "
            f"be. {len(_SEAM_FMT_DIMENSIONS)} format spec(s) classify as a dimension, "
            f"{len(_SEAM_FMT_DIMENSION_FLOOR)} of them held to the committed floor; "
            f"{dim_swept} occurrence(s) of the {len(dim_tokens)} self-formatting token(s) "
            f"declaring a `dim` are caught the same way when the sigil is taken out of "
            f"the value; {len(undeclared)} single-figure token(s) in the template format "
            "themselves and declare no dimension for this to read")


@case
def case_every_sigil_carrying_token_declares_what_it_is():
    """ISSUE #163. The seam guard's dimension test can only ask a question the
    token answers, and before this there were three states a token could be in
    and only two the guard could tell apart: a declared format (readable), a
    sentence that happens to carry figures (nothing to read, and nothing to
    read is correct), and a single figure whose lambda types its own sigil
    (nothing to read, and nothing to read is a hole). The third looked exactly
    like the second.

    THE RULE THAT SEPARATES THEM IS A DECLARATION, NOT A SHAPE TEST, and the
    two live tokens that make shape testing impossible are worth naming:
    BILLED_GENERATION_RATES builds THREE "$" figures into one clause, and
    DAYBAND_ONPEAK_PRICE writes "61-87¢" without calling a formatter at all --
    a range, one dimension, one figure by any reading a guard could act on.
    No inspection of the string tells those two apart. The token says which it
    is.

    SCOPED TO WHERE THE QUESTION IS ASKED. The dimension test fires on a value
    carrying a "$", "%" or "¢", so those are the tokens held to declaring
    something; a date, a script path or a kWh figure declares neither and is
    not swept. Discovered from the live template and this run's resolved
    values, never from a list -- a token added tomorrow whose value wears a
    sigil is swept tomorrow, and the failure names it."""
    values, _gaps = _seam_values()
    seen = {name for line in rt.TEMPLATE.read_text().splitlines()
            for name, _s, _e in _seam_render(line, values)[1]}
    carrying = sorted(name for name in seen
                      if any(s in values[name] for s in _SEAM_DIMENSION_SIGILS))
    assert len(carrying) >= 40, (
        f"only {len(carrying)} live token(s) render a value carrying a dimension sigil "
        f"({carrying}); this template is full of money and percent figures, so the "
        "discovery step broke and this case is now checking nothing")
    by_state = {"a format spec": [], "its own dim": [], "phrase": [], "nothing": []}
    for name in carrying:
        fmt = rt.TOKENS.get(name, {}).get("fmt")
        if fmt in _SEAM_FMT_DIMENSIONS:
            by_state["a format spec"].append(name)
        elif rt.declared_dimension(name) is not None:
            by_state["its own dim"].append(name)
        elif rt.is_phrase(name):
            by_state["phrase"].append(name)
        else:
            by_state["nothing"].append(name)
    assert not by_state["nothing"], (
        f"{len(by_state['nothing'])} live token(s) publish a value wearing a money, "
        f"percent or cents sign and declare nothing about it: {by_state['nothing']}. "
        "The seam guard cannot tell whether each is a figure whose sigil it should be "
        "checking or a sentence the question does not apply to. Declare one of the "
        "three: a `fmt` the registry renders the figure through (preferred), a `dim` "
        "for a figure the token formats itself, or phrase=True for language")
    for state in ("a format spec", "its own dim", "phrase"):
        assert by_state[state], (
            f"not one live sigil-carrying token declares {state}, so this case is "
            f"asserting a partition with an empty part. If that is a real change to "
            "report_tokens, say which declaration replaced it")
    return ("every one of the " + str(len(carrying)) + " live token(s) whose value wears "
            "a dimension sigil declares what it is: "
            + ", ".join(f"{len(v)} by {k}" for k, v in by_state.items() if k != "nothing"))


@case
def case_a_phrase_marker_cannot_be_used_to_hide_a_single_figure():
    """The marker above has one abuse and this is it: phrase=True on a token
    whose value really is one figure silences the dimension test for exactly
    the token it was written for, and the suite stays green by having stopped
    asking. That is the same failure the allowlist is held away from in
    _seam_stale_allowlist, one declaration over.

    So the two are checked against each other. `phrase` says the value is
    language; _seam_reduces_to_one_figure says whether the value reduces to a
    single figure once its unit suffix, its dimension sigil and its
    approximation or tolerance marks come off. A token where those two
    disagree is either mismarked or has changed what it publishes, and the
    message says which way to resolve it.

    THE STRIPPING IS WHAT MAKES THE AUDIT WORTH RUNNING. Read for sigils
    alone, it saw "~$1,221/yr" as "1,221/yr", called that not-a-number and
    passed -- so phrase=True switched the dimension check off for the single
    commonest value shape in this report, and every "/yr" and "/mo" figure was
    one word away from being unchecked with the suite still green. A range
    ("61-87¢") and a tolerance ("±2%") escaped the same way. The mutation case
    below drives all three shapes off live tokens.

    THE SHAPE TEST IS SOUND HERE AND NOT IN THE GUARD, and the difference is
    the direction of the error. Used to CLASSIFY, it gets
    BILLED_GENERATION_RATES wrong and the guard silently mis-scopes. Used to
    AUDIT a declaration a human wrote, its false positives are loud: it can
    only complain about a token whose value is literally one number in one
    dimension, which is a claim a reader can settle in one look."""
    values, _gaps = _seam_values()
    marked = sorted(name for name, spec in rt.TOKENS.items()
                    if spec.get("phrase") and name in values)
    assert marked, ("no token declares phrase=True, so this case checks nothing; "
                    "report_tokens dropped the marker")
    offences = _seam_phrase_offences(values, marked)
    assert not offences, (
        "phrase=True is declared on token(s) whose value is one figure and nothing "
        "else, which turns the marker into a way of not being checked: "
        + "; ".join(f"{n} renders {v!r}" for n, v in offences)
        + ". Give each a `fmt` the registry renders it through, or a `dim`")
    return (f"none of the {len(marked)} phrase-marked token(s) publishes a value that "
            "reduces to a single figure")


# The decorated shapes a single figure wears in this report, one live token
# each, with the layer that used to carry the figure past the audit named.
# LIVE TOKENS RATHER THAN SYNTHETIC STRINGS: a mutation case built on strings
# I typed proves the regex matches strings I typed. These are the values the
# report actually publishes, so a formatter that starts writing "1221 /yr" or
# an en dash that becomes a word takes the probe with it and says so here.
_SEAM_PHRASE_ABUSE_SHAPES = (
    ("S0_FREE_WIN_CARD_FIGURE", "a '/yr' unit behind an approximated money figure"),
    ("HPWH_NET_SAVINGS", "a '/yr' unit behind a plain money figure"),
    ("SOILING_RATE_RANGE", "a '/month' unit behind a percent range"),
    ("NEM_GRANDFATHER_VALUE_RANGE", "a money range with no unit at all"),
    ("DAYBAND_ONPEAK_PRICE", "a hand-written cents range"),
    ("PRODUCTION_AGREEMENT_PCT", "a tolerance mark on a percent"),
)


@case
def case_the_phrase_audit_catches_a_decorated_figure_marked_as_language():
    """THE MUTATION THE CASE ABOVE EXISTS TO FAIL ON, run rather than argued.

    An anti-abuse guard is only worth the abuse it actually stops, and this
    one shipped for a while stopping less than it read as stopping: its shape
    test asked _SEAM_BARE_NUMBER_RE after stripping sigils and nothing else,
    so any figure wearing a unit suffix, written as a range, or carrying a
    tolerance mark could be declared phrase=True and the whole suite stayed
    green. Flipping S0_FREE_WIN_CARD_FIGURE from dim="$" to phrase=True --
    which switches off the dimension check on a headline saving -- was a
    180/180 pass.

    So every decorated shape this report really publishes is flipped here, one
    live token at a time, and the audit above must report each one BY NAME.
    Patching rt.TOKENS rather than calling the helper directly is what makes
    this a test of the case and not of the regex: the discovery step, the
    sigil filter and the message all run exactly as they would on a real
    mis-declaration.

    THE OTHER HALF IS THE FALSE ALARM, and it is checked in the same breath:
    with nothing patched, the case passes. A stripping rule loose enough to
    call one of the live phrase tokens a figure would fail there rather than
    here, which is the direction that matters -- an audit that cries wolf on
    real prose is worse than the hole it closed."""
    values, _gaps = _seam_values()
    audit = case_a_phrase_marker_cannot_be_used_to_hide_a_single_figure
    audit()  # the unmutated registry, for the false alarm.

    missed, caught = [], []
    for name, shape in _SEAM_PHRASE_ABUSE_SHAPES:
        spec = rt.TOKENS.get(name)
        assert spec is not None and name in values, (
            f"{name} is no longer a live token, so the {shape} it stands for is not "
            "being driven. Name the token that publishes that shape now, or say why "
            "the report stopped publishing it")
        assert spec.get("dim") and not spec.get("phrase"), (
            f"{name} no longer declares a `dim` of its own, so flipping it to "
            f"phrase=True mutates nothing. Re-point this shape ({shape}) at a token "
            "that does")
        assert any(s in values[name] for s in _SEAM_DIMENSION_SIGILS), (
            f"{name} renders {values[name]!r} here, which wears no dimension sigil, so "
            f"the audit would not look at it and this probe ({shape}) tests nothing "
            "against these inputs. Name a token that publishes the shape on this "
            "household, or say why the report no longer publishes it")
        mutated = dict(rt.TOKENS)
        mutated[name] = {k: v for k, v in spec.items() if k != "dim"}
        mutated[name]["phrase"] = True
        with _patched(rt, "TOKENS", mutated):
            try:
                audit()
            except AssertionError as exc:
                assert name in str(exc), (
                    f"the audit failed under {name} flipped to phrase=True but does "
                    f"not name it: {exc}")
                caught.append((name, shape, str(exc).splitlines()[0]))
                continue
        missed.append((name, values[name], shape))
    assert not missed, (
        "phrase=True hides a value that is one figure and nothing else, and the audit "
        "passes anyway: "
        + "; ".join(f"{n} renders {v!r} ({s})" for n, v, s in missed)
        + ". _seam_reduces_to_one_figure stopped taking one of the layers off -- the "
        "unit suffix, the dimension sigil, or the approximation/tolerance mark -- so "
        "the marker is once again a way of not being checked")
    return (f"the phrase audit reports each of the {len(caught)} decorated figure "
            "shape(s) when a live token declaring it is flipped to phrase=True ("
            + ", ".join(f"{n}: {s}" for n, s, _m in caught)
            + "), and passes with nothing flipped")


@case
def case_a_dim_token_that_states_it_has_no_figure_is_not_reported():
    """The false positive `dim` would otherwise ship with, driven rather than
    argued.

    Several tokens that declare a dimension publish a figure only where the
    household has the thing being priced -- EV_FIX_SAVINGS_100 renders
    "~$1,221" here and "not applicable to this household -- household.has_ev
    is false ..." for a house with no car. A declaration that said "this value
    carries a $" without qualification would report that sentence as a figure
    that lost its dollar sign, in every report for every household the section
    does not apply to.

    report_tokens.states_no_figure is the qualification, and it is report_
    tokens answering for ITS OWN two refusal wordings rather than the guard
    guessing from the shape of a string. Both directions are driven here: the
    refusal is quiet, and the same token with the sigil taken out of a real
    figure is still reported -- or the exemption would be a way to disable the
    rule by writing prose."""
    refusing = {name for name, spec in rt.TOKENS.items()
                if spec.get("dim") and spec.get("kind") != "gap"}
    assert refusing, "no token declares a dim; this case checks nothing"

    saying_nothing = []
    with _patched(rt, "_json", _no_ev_household()):
        for name in sorted(refusing):
            try:
                value = rt.resolve_token(name)
            except SystemExit:
                continue
            if rt.states_no_figure(value):
                saying_nothing.append((name, value))
    assert saying_nothing, (
        "not one dim-declaring token states it has no figure under the no-EV fixture, "
        "so the exemption this case covers is unreachable and untested. Say which "
        "fixture now drives it")

    for name, value in saying_nothing:
        dim = rt.declared_dimension(name)
        assert dim not in value, (
            f"{name}'s refusal {value!r} already carries its {dim!r}, so this case "
            "cannot tell the exemption from the ordinary pass")
        assert _seam_missing_unit(value, "<p>", "</p>", None, name) is None, (
            f"the seam guard reports {name}'s own 'no figure here' sentence {value!r} "
            f"as a figure that lost its {dim!r}; report_tokens.states_no_figure no "
            "longer recognises this module's refusal wording")
        # The other direction: the exemption must not be reachable by a value
        # that IS a figure.
        assert _seam_missing_unit("1,221", "<p>", "/yr</p>", None, name) is not None, (
            f"a bare '1,221' published by {name}, which declares dim={dim!r}, is not "
            "reported; the exemption is swallowing real figures too")
    return (f"{len(saying_nothing)} dim-declaring token(s) state they have no figure "
            f"under the no-EV fixture and are not reported for it "
            f"({sorted(n for n, _v in saying_nothing)}), while a bare number published "
            "by the same token still is")


def _seam_comment_mask(text):
    """`text` with every <!-- ... --> span blanked to spaces, newlines kept.

    Liveness is a property of the SPAN, not of the line. This template is full
    of multi-line <!-- TODO --> blocks whose interior lines carry no '<!--' of
    their own, and of live lines that CARRY an inline '<!--' opener -- whether
    it runs to the end of the line or opens and closes mid-line with live
    tokens still to come after it -- so the obvious per-line test ('<!--' not
    in line) is wrong in both directions: it reads a comment's interior as live
    markup and a live line as commented. Masking to spaces rather than deleting
    keeps every line's length and column positions, so a masked line can be
    zipped against the real one.

    An unterminated '<!--' is masked to the end of the text, which is how a
    browser reads it.

    TWO WAYS THIS DIFFERS FROM A BROWSER, both stated rather than fixed. (1) A
    '<!--' inside a quoted attribute or a JS string is an opener to this regex
    and is not one to a parser, so the mask would run on to the next '-->' or
    to EOF and blank live markup with it. That direction is the safe one: it
    OVER-masks, so _seam_template_line finds no live line for a token and
    refuses by assertion rather than quietly driving a fixture against the
    wrong line. No such string exists in this template today. (2) Only '\\n' is
    preserved, but str.splitlines() also splits on '\\r', '\\v' and '\\f', so
    one of those inside a comment span would blank a line break and leave the
    masked copy with fewer lines than the real one -- desynchronising the zip
    in _seam_template_line. This template contains none, and
    case_the_shipped_line_fixture_decides_liveness_by_comment_span compares the
    two line counts on every run, so the desynchronisation fails there rather
    than mislabelling a line."""
    return re.sub(r"<!--.*?(?:-->|\Z)",
                  lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def _seam_template_line(token, text=None):
    """The one report-template.html line carrying {{token}} in LIVE markup --
    outside every comment SPAN, per _seam_comment_mask -- so a fixture can be
    driven against the template as it ships.

    The line is returned exactly as it ships, comment text included: liveness
    is decided on the masked copy, but the fixture wants the real line. `text`
    is the template by default and is a parameter only so the shapes this
    filter has to get right can be driven directly."""
    text = rt.TEMPLATE.read_text() if text is None else text
    needle = "{{%s}}" % token
    lines = [line for line, masked in zip(text.splitlines(),
                                          _seam_comment_mask(text).splitlines())
             if needle in masked]
    assert len(lines) == 1, (
        f"report-template.html carries {{{{{token}}}}} on {len(lines)} live "
        "line(s); this fixture needs exactly one")
    return lines[0]


def _pre_129_seams(values):
    """The seams issue #129 names, each rebuilt as (label, template fragment,
    value overrides, expected token, expected class).

    Fragment 4 is the template text VERBATIM as it stood BEFORE commit c79cc06
    (PR #134) fixed it (report-template.html:299). Fragments 1-3 are ABRIDGED
    from the pre-c79cc06 text of the lines their comments cite (366 and 411,
    then 312): each keeps the seam it names and shortens the markup around it
    to the element that carries it. All four are historical constants either
    way -- unlike the live template, they cannot drift out from under this
    case. The
    two token-side reverts are rebuilt from the CURRENT resolved values rather
    than written as literals, so no household figure is committed here and the
    fixture follows the archive it runs against.

    Fragment 5 is deliberately the OTHER combination, and quoting the historic
    fragment is not a substitute for it: #129's third defect was fixed on the
    TOKEN side, so reverting the token alone -- against report-template.html
    exactly as it ships -- reproduces the shipped defect through the one path
    an external reproducer can still take (a stale or hand-edited
    report_tokens.py against the current template). It is not a variant of
    fragment 4: the pre-#134 fragment closed the echoed clause with ')' and
    the shipped line does not, which is the difference between an echo the
    rule can find anchored at the value's last character and one it can only
    find after trimming that character off.

    This is the "verified to FAIL against the pre-#129 template" acceptance
    criterion, kept as a permanent case rather than a one-time manual revert:
    a rule deleted or weakened later stops failing here."""
    ac = values["AC_CEILING_KW"]
    inverter = values["INVERTER_DESCRIPTION"]
    coverage = values["SOLAR_COVERAGE_PCT"]
    assert coverage.endswith("%"), (
        f"SOLAR_COVERAGE_PCT no longer renders a percent sign ({coverage!r}); this "
        "fixture reverts that sign to rebuild issue #129's missing-unit seam")
    # c79cc06's token-side half, reverted: the clause the token used to close
    # with, rebuilt from the value it renders today rather than quoted, so no
    # household figure lands in this file. Pre-#134 the token read
    # "N × MODEL (~V VA each ≈ K kW AC max)"; today it reads
    # "N × MODEL, ~V VA each" and the template supplies the ceiling.
    assert ", ~" in inverter, (
        f"INVERTER_DESCRIPTION no longer reads '..., ~V VA each' ({inverter!r}), so "
        "this fixture cannot rebuild the pre-#134 value it reverts to")
    pre_134_inverter = inverter.replace(", ~", " (~", 1) + f" ≈ {ac} kW AC max)"
    return [
        # report-template.html:366 and :411 -- the template's own tilde in
        # front of a value that already carries one.
        ("~{{BATTERY_COST}} (template line 366)",
         "<tr class=\"win\"><td>{{BATTERY_MODEL}}</td><td>~{{BATTERY_COST}}</td></tr>",
         {}, "BATTERY_COST", "doubled-sigil"),
        # The suffix half of the same class, found by Codex's adversarial pass
        # on #129: the token owns the unit and the template repeated it.
        ("{{SOILING_RATE_RANGE}}/month",
         "<p><!-- TODO: bracket the soiling rate honestly "
         "({{SOILING_RATE_RANGE}}/month); cite {{SOILING_SCRIPT}}. --></p>",
         {}, "SOILING_RATE_RANGE", "doubled-sigil"),
        # report-template.html:312 -- a percentage rendered as a bare count.
        # Pre-fix SOLAR_COVERAGE_PCT was fmt="num0", i.e. today's value
        # without its sign.
        ("covers {{SOLAR_COVERAGE_PCT}} of",
         "<li><b>Where it goes:</b> covers {{SOLAR_COVERAGE_PCT}} of the home's "
         "{{ANNUAL_LOAD_KWH}} kWh/yr load</li>",
         {"SOLAR_COVERAGE_PCT": coverage.rstrip("%")},
         "SOLAR_COVERAGE_PCT", "missing-unit"),
        # report-template.html:299 -- the AC ceiling printed twice, because
        # INVERTER_DESCRIPTION used to close with the same clause.
        ("{{INVERTER_DESCRIPTION}} (= {{AC_CEILING_KW}} kW AC max)",
         "<li><b>Inverters:</b> {{INVERTER_DESCRIPTION}} "
         "(≈ {{AC_CEILING_KW}} kW AC max)</li>",
         {"INVERTER_DESCRIPTION": f"{inverter} ≈ {ac} kW AC max)"},
         "INVERTER_DESCRIPTION", "echoed-phrase"),
        # The same defect reached the other way: report-template.html EXACTLY
        # as it ships, with only the token reverted to its pre-#134 value. The
        # shipped line prints "≈ {{AC_CEILING_KW}} kW AC max" with no closing
        # paren, so the echo stops one character short of the value's end --
        # the case the suffix match missed until _SEAM_ECHO_TRIM.
        ("shipped template line + pre-#134 INVERTER_DESCRIPTION",
         _seam_template_line("INVERTER_DESCRIPTION"),
         {"INVERTER_DESCRIPTION": pre_134_inverter},
         "INVERTER_DESCRIPTION", "echoed-phrase"),
    ]


@case
def case_the_seam_guard_fires_on_every_defect_issue_129_named():
    """ISSUE #133's second acceptance criterion, as a permanent case: the
    guard is driven against the pre-#129 template text (and the pre-#129 token
    values, for the two that were fixed token-side) and must report EACH seam,
    by token and by class. A rule that is quietly weakened later stops failing
    here and the case fails instead.

    WHAT THIS CASE DOES NOT PIN, because measuring it is the only way to know:
    these five fixtures reach exactly the constant members they happen to use
    -- "~" of _SEAM_SIGILS, "/month" of _SEAM_UNITS, ")" of _SEAM_ECHO_TRIM --
    and every OTHER member could be deleted with this case still green. Those
    are pinned one at a time by
    case_every_member_of_the_seam_constants_is_load_bearing below, which probes
    each member of each constant and holds the constants to a committed
    vocabulary floor. Both cases are needed and
    neither substitutes for the other: this one pins the DEFECTS issue #129
    actually shipped, that one pins the CONSTANTS the rules are made of.

    The last fixture reverts the TOKEN against the template as it ships, which
    is the combination a historic template fragment cannot exercise: see
    _pre_129_seams.

    Each fixture is also checked to be clean once the fix is put back, so the
    case cannot pass by flagging everything -- and for the shipped-line
    fixture that check IS the live template rendering clean with the real
    token value."""
    values, _gaps = _seam_values()
    fixtures = _pre_129_seams(values)
    classes = {cls for _l, _f, _o, _t, cls in fixtures}
    assert classes == {"doubled-sigil", "missing-unit", "echoed-phrase"}, (
        f"the pre-#129 fixtures no longer exercise all three defect classes: {classes}")

    caught = []
    for label, fragment, overrides, token, cls in fixtures:
        broken = dict(values, **overrides)
        hits = _seam_defects(fragment, broken)
        assert any(name == token and got == cls for name, got, _w, _c in hits), (
            f"the seam guard did NOT report the pre-#129 defect {label}: expected "
            f"{token} [{cls}], got {[(n, c) for n, c, _w, _x in hits] or 'nothing'}\n"
            f"  rendered: {_seam_render(fragment, broken)[0]!r}")
        caught.append(f"{token} [{cls}] via {label}")

        # And the fixed shape is clean, so the rule discriminates rather than
        # firing on everything it is handed.
        fixed_fragment = fragment.replace("~{{BATTERY_COST}}", "{{BATTERY_COST}}") \
                                 .replace("{{SOILING_RATE_RANGE}}/month",
                                          "{{SOILING_RATE_RANGE}}") \
                                 .replace(" (≈ {{AC_CEILING_KW}} kW AC max)",
                                          " ≈ {{AC_CEILING_KW}} kW AC max")
        assert not _seam_defects(fixed_fragment, values), (
            f"the guard still flags {label} after the shipped fix is applied, so it is "
            f"not discriminating: {_seam_defects(fixed_fragment, values)}")
    return ("the guard reports every pre-issue-129 seam and none of the fixed ones: "
            + ", ".join(caught))


@case
def case_the_seam_rules_are_generic_not_a_case_per_token():
    """ISSUE #133's third acceptance criterion, checked mechanically rather
    than asserted: a token nobody has heard of, dropped into a template
    fragment with a broken seam, is caught with no edit to this file. If the
    rules were ever rewritten as a table of known token names this fails."""
    invented = {"A_TOKEN_THAT_DOES_NOT_EXIST_YET": "~$9,999",
                "ANOTHER_INVENTED_TOKEN": "42",
                "A_THIRD_INVENTED_TOKEN": "1.5%/month"}
    probes = [
        ("<p>costs ~{{A_TOKEN_THAT_DOES_NOT_EXIST_YET}} installed</p>",
         "A_TOKEN_THAT_DOES_NOT_EXIST_YET", "doubled-sigil"),
        ("<p>covers {{ANOTHER_INVENTED_TOKEN}} of the load</p>",
         "ANOTHER_INVENTED_TOKEN", "missing-unit"),
        # The half of class 2 the function-word form could not see: the word
        # after the figure is a hyphenated participle, exactly as it is on
        # issue #129's own line ("... -- 40% self-consumed, 60% exported").
        ("<p>{{ANOTHER_INVENTED_TOKEN}} self-consumed, the rest exported</p>",
         "ANOTHER_INVENTED_TOKEN", "missing-unit"),
        ("<p>soiling {{A_THIRD_INVENTED_TOKEN}}/month</p>",
         "A_THIRD_INVENTED_TOKEN", "doubled-sigil"),
    ]
    for fragment, token, cls in probes:
        hits = _seam_defects(fragment, invented)
        assert any(n == token and c == cls for n, c, _w, _x in hits), (
            f"an unknown token's {cls} seam went unreported: {fragment!r} -> {hits}")
    for name in invented:
        assert name not in rt.TOKENS, f"{name} is a real token; pick a name that is not"
    # And the same values in a well-formed context are clean.
    clean = ("<p>costs {{A_TOKEN_THAT_DOES_NOT_EXIST_YET}} installed, "
             "{{ANOTHER_INVENTED_TOKEN}} kWh served, soiling {{A_THIRD_INVENTED_TOKEN}}</p>")
    assert not _seam_defects(clean, invented), _seam_defects(clean, invented)
    return (f"{len(probes)} invented token(s) with broken seams are caught by name, and "
            "the same values in a correct context are not -- the rules read the render, "
            "not a token list")


_SEAM_PROBE_TOKEN = "A_TOKEN_THAT_DOES_NOT_EXIST_YET"

# The vocabulary the three seam constants MUST still carry.
#
# Committed separately from the constants themselves, because deletion is the
# ONE weakening a generated probe cannot see: a probe built off _SEAM_UNITS
# loses its subject at the same instant the unit does, so the case goes green
# by having stopped asking. Measured, not assumed -- dropping any of $ % ≈ + −
# - from _SEAM_SIGILS, or any unit but "/month", or any _SEAM_ECHO_TRIM
# character but ")", left all of this suite green before this floor existed.
#
# A SUPERSET is fine and needs no edit here: a unit added tomorrow is probed
# tomorrow by _seam_member_probes and this floor never mentions it. What fails
# is a member DISAPPEARING, which is the edit that quietly narrows what the
# guard can see. That is not forbidden -- a unit may turn out to cry wolf --
# but it becomes a deliberate two-place edit with a reviewer's name on it
# instead of one character deleted from a tuple.
_SEAM_VOCABULARY_FLOOR = {
    "_SEAM_SIGILS": ("~", "$", "%", "≈", "+", "−", "-"),
    "_SEAM_UNITS": ("cycles/day", "kWh/kW/yr", "kWh/yr", "per month", "per year",
                    "annually", "/month", "months", "/year", "years", "hours",
                    "month", "hour", "days", "year", "kWh", "/kWh", "MWh", "/day",
                    "/mo", "/yr", "yrs", "day", "kW", "yr", "%", "¢", "°", "h"),
    "_SEAM_ECHO_TRIM": tuple(")]}.,;:"),
}


def _seam_member_probes():
    """One synthetic probe per member of _SEAM_SIGILS, _SEAM_UNITS and
    _SEAM_ECHO_TRIM, as
    [(constant, member, label, broken, clean, value, class), ...].

    The CONSTANT is carried alongside the member because the two lists
    overlap: "%" is a member of both _SEAM_SIGILS and _SEAM_UNITS, and it
    reaches the rule down two different paths ("%{{X}}" is answered by the
    sigil branch's strict adjacency, "{{X}} %" only by the unit branch). Keyed
    by the member alone, the %-as-unit probe could be deleted with the
    coverage check still satisfied by the %-as-sigil probes -- measured, not
    supposed. Keyed by the pair, every member of every constant has to be
    probed as that constant's member.

    Built OFF THE CONSTANTS, in the same spirit as the rules themselves, so a
    sigil or a unit added tomorrow is probed tomorrow with no edit here. That
    is also this builder's one blind spot: a member DELETED from a constant
    takes its own probe with it, which is why _SEAM_VOCABULARY_FLOOR exists
    alongside these probes rather than instead of them."""
    tok = _SEAM_PROBE_TOKEN
    probes = []
    for sigil in _SEAM_SIGILS:
        # Both directions, because the head and the tail of a doubled sigil are
        # two separate branches of _seam_doubled and deleting either one has to
        # fail somewhere.
        probes.append(("_SEAM_SIGILS", sigil,
                       f"sigil {sigil!r} in front of a value that starts with it",
                       "<p>cost %s{{%s}} today</p>" % (sigil, tok),
                       "<p>cost {{%s}} today</p>" % tok,
                       sigil + "1,234", "doubled-sigil"))
        probes.append(("_SEAM_SIGILS", sigil,
                       f"sigil {sigil!r} behind a value that ends with it",
                       "<p>cost {{%s}}%s today</p>" % (tok, sigil),
                       "<p>cost {{%s}} today</p>" % tok,
                       "1,234" + sigil, "doubled-sigil"))
    for unit in _SEAM_UNITS:
        # A "/..." unit is written flush against the figure, everything else
        # behind a space -- and that space is load-bearing for this probe, not
        # cosmetic: "%" is on BOTH constant lists, and the sigil rule demands
        # strict adjacency, so only the space keeps the sigil rule from
        # answering a probe that is asking about the unit rule.
        sep = "" if unit.startswith("/") else " "
        probes.append(("_SEAM_UNITS", unit,
                       f"unit {unit!r} behind a value that ends with it",
                       "<p>uses {{%s}}%s%s today</p>" % (tok, sep, unit),
                       "<p>uses {{%s}} today</p>" % tok,
                       "1,234" + sep + unit, "doubled-sigil"))
    for closer in _SEAM_ECHO_TRIM:
        # The value closes its clause with `closer` and the template prints the
        # same figure immediately after. Only the trim can find that echo:
        # every suffix of the UNtrimmed value ends in `closer`, and the
        # template's copy does not carry it.
        probes.append(("_SEAM_ECHO_TRIM", closer,
                       f"echo findable only once {closer!r} is trimmed",
                       "<p>{{%s}} 1,234.56 kW</p>" % tok,
                       "<p>{{%s}} and nothing else</p>" % tok,
                       "the ceiling is 1,234.56 kW" + closer, "echoed-phrase"))
    return probes


@case
def case_every_member_of_the_seam_constants_is_load_bearing():
    """Every member of _SEAM_SIGILS, _SEAM_UNITS and _SEAM_ECHO_TRIM carries
    weight: it is still in the constant, and the rule that reads the constant
    still acts on it.

    The case above claims a weakened rule "stops failing here". Measured, that
    was true of exactly the three members its five historical fixtures happen
    to use -- every other member could be deleted, one at a time, with this
    whole suite green. A guard whose vocabulary can be shortened without
    argument is a guard that reports clean because it stopped looking.

    TWO MECHANISMS, because one cannot do both jobs:
      * A member DELETED from a constant is caught by _SEAM_VOCABULARY_FLOOR,
        which is the committed record of what the constants must carry. Probes
        generated off the constants cannot catch a deletion -- they lose their
        subject in the same edit -- and pretending otherwise is how a
        generative test ends up asserting nothing.
      * A member NEUTERED -- left in the constant while the rule quietly stops
        honoring it, which is what a "small tidy-up" inside _seam_doubled or
        _seam_echo looks like -- is caught by the probes, one per member,
        generated FROM the constants so a member added tomorrow is probed
        tomorrow with no edit here.

    Each probe is paired with the same value in a context that does NOT supply
    the member, which must be clean -- otherwise a rule that fired on
    everything would pass this case."""
    probes = _seam_member_probes()
    assert _SEAM_PROBE_TOKEN not in rt.TOKENS, (
        f"{_SEAM_PROBE_TOKEN} is a real token now; pick a name that is not")
    # Keyed by (constant, member), never by the member alone: "%" is on BOTH
    # _SEAM_SIGILS and _SEAM_UNITS, and a union would let its unit probe be
    # deleted while its sigil probes kept the coverage check happy -- two
    # different branches of _seam_doubled, one of them then unprobed. See
    # _seam_member_probes.
    covered = {(constant, member)
               for constant, member, _l, _b, _c, _v, _cls in probes}
    for constant, name in ((_SEAM_SIGILS, "_SEAM_SIGILS"),
                           (_SEAM_UNITS, "_SEAM_UNITS"),
                           (_SEAM_ECHO_TRIM, "_SEAM_ECHO_TRIM")):
        missing = [m for m in constant if (name, m) not in covered]
        assert not missing, (
            f"{name} member(s) {missing} have no probe OF THEIR OWN CONSTANT, so the "
            "rule could stop honoring them with this suite green; the probe builder no "
            "longer reads the constants")
        gone = [m for m in _SEAM_VOCABULARY_FLOOR[name] if m not in constant]
        assert not gone, (
            f"{name} no longer carries {gone}, so every seam that needs {gone} to be "
            "seen is now invisible to this guard. Narrowing the vocabulary is allowed "
            f"and hiding it is not: say why in _SEAM_VOCABULARY_FLOOR[{name!r}] and "
            "drop it there too, in the same commit, where a reviewer reads it")

    for _constant, member, label, broken, clean, value, cls in probes:
        values = {_SEAM_PROBE_TOKEN: value}
        hits = _seam_defects(broken, values)
        assert any(n == _SEAM_PROBE_TOKEN and c == cls for n, c, _w, _x in hits), (
            f"nothing catches {label}, so {member!r} carries no weight: "
            f"{broken!r} with value {value!r} reported "
            f"{[(n, c) for n, c, _w, _x in hits] or 'nothing'}")
        assert not _seam_defects(clean, values), (
            f"the same value {value!r} is flagged in a context that does not repeat "
            f"{member!r} ({clean!r}), so the probe proves nothing: "
            f"{_seam_defects(clean, values)}")
    floor = sum(len(v) for v in _SEAM_VOCABULARY_FLOOR.values())
    pairs = len(_SEAM_SIGILS) + len(_SEAM_UNITS) + len(_SEAM_ECHO_TRIM)
    return (f"{len(probes)} probes cover all {pairs} (constant, member) pairs across "
            f"_SEAM_SIGILS ({len(_SEAM_SIGILS)}), _SEAM_UNITS ({len(_SEAM_UNITS)}) and "
            f"_SEAM_ECHO_TRIM ({len(_SEAM_ECHO_TRIM)}); each fires on the member's own "
            f"seam and stays quiet on the same value without it, and all {floor} "
            "committed vocabulary members are still there to be read")


@case
def case_the_false_positive_guards_inside_the_seam_rules_are_load_bearing():
    """The guards that keep the rules QUIET carry weight too, and they were as
    unpinned as the constants above were: _seam_doubled's two word-boundary
    checks and _seam_missing_unit's preceding-sigil exemption could each be
    deleted, one at a time, with this whole suite green. Nothing in the live
    template or in the historical fixtures asks a rule to stay SILENT for those
    reasons, so nothing noticed.

    That is the same argument
    case_every_member_of_the_seam_constants_is_load_bearing makes about the
    vocabulary, carried through to the rules: an unpinned guard is a guard a
    tidy-up deletes, and deleting one of these does not break anything a
    reviewer would see. It makes a conservative rule fire on ordinary prose --
    which is how a tripwire gets switched off for good, because the fix that
    follows a wolf-crying guard is to stop running it.

    Each guard is driven in ISOLATION: for every assertion below, only one of
    the three can be the reason the rule answers None, so deleting either word
    boundary alone fails here. The corresponding blind spots -- these same
    guards refusing a real doubling -- are listed in the block comment above
    and are not re-argued here."""
    # Template side. "kW" is only the HEAD of the longer unit the template
    # really supplies, and "9.45 kW" beside "kWh/yr" is two different units
    # rather than one printed twice. The VALUE's own boundary is clean here (a
    # space precedes "kW"), so only the template-side check can keep this
    # quiet.
    assert _seam_doubled("9.45 kW", "", " kWh/yr of output") is None, (
        "the template-side word boundary is gone: a template word that merely BEGINS "
        "with a unit's letters is now read as supplying that unit, so every value "
        "ending in a short unit cries wolf on the next ordinary word")
    # Value side. "3,500 kWh" does not END in the unit "h" -- its unit is kWh
    # -- so a template that really does supply "h" is not repeating anything.
    # The TEMPLATE's boundary is clean here ("/" follows the "h"), so only the
    # value-side check can keep this quiet.
    assert _seam_doubled("3,500 kWh", "", " h/day") is None, (
        "the value-side word boundary is gone: a value whose unit merely ENDS with a "
        "shorter unit's letters is now read as ending in that shorter unit, and "
        "every kWh figure in the report becomes an 'h' waiting to be doubled")
    # Both at once -- the shape the block comment argues from.
    assert _seam_doubled("3,500 kWh", "", " hours today") is None, (
        "'3,500 kWh' followed by the ordinary word 'hours' is reported as a doubled "
        "unit; one of the two word boundaries in _seam_doubled is gone")
    # ...and the rule still reports the doubling the boundaries are NOT about,
    # so this case cannot be satisfied by a rule that answers None to
    # everything.
    real = _seam_doubled("3,500 kWh", "", " kWh/yr")
    assert real and "'kWh'" in real, (
        f"a template unit the value really does end with went unreported: {real!r}")

    # Class 2: a sigil immediately in front of a bare number IS its unit, so
    # the function word after it is not evidence of anything.
    for head, sigil in (("<li>the pack costs $", "$"), ("<li>about ~", "~")):
        assert _seam_missing_unit("14,500", head, " of the total") is None, (
            f"a bare number carrying the sigil {sigil!r} in front of it is reported as "
            "having lost its unit; _seam_missing_unit's preceding-sigil exemption is "
            "gone and every '$14,500 of the total' in the report is now a defect")
    # The control: the same number and the same following words, with nothing
    # supplying a unit, is still reported.
    bare = _seam_missing_unit("14,500", "<li>the pack costs ", " of the total")
    assert bare and "'of'" in bare, (
        f"the missing-unit rule stopped reporting a genuinely bare number: {bare!r}")
    return ("the two word boundaries in _seam_doubled and the preceding-sigil "
            "exemption in _seam_missing_unit are each pinned in isolation, and the "
            "real doubling and the real missing unit are still reported")


@case
def case_the_unit_match_folds_case_on_all_three_of_its_strings():
    """_seam_doubled folds case on THREE strings -- the template's text after
    the token, the value, and the unit spelling itself -- and every one of the
    three was unpinned: any single .lower() could be deleted with this whole
    suite green, while "{{X}} kWh" against a value already ending "KWH" went
    invisible. Same class of hole as the constants and the false-positive
    guards pinned above, and it is real rather than theoretical: unit case is
    not consistent across this report's sources ("kWh", "KWH", "kwh" all
    appear in utility and monitoring exports), so a value carrying a
    differently-cased unit is an ordinary thing for a formatter to produce.

    Driven so that each assertion has exactly ONE fold that can save it: the
    first writes the template's unit in lower case (so only the value's fold
    and the unit's own can bring the two together), the second writes the
    VALUE's unit in lower case (so only the template's fold and the unit's own
    can). Deleting any one of the three fails at least one of them."""
    value_side = _seam_doubled("3,500 KWH", "", " kwh/yr")
    assert value_side and "'kWh'" in value_side, (
        "a value ending in an upper-case unit is no longer read as ending in that "
        f"unit: {value_side!r} -- value.lower() or unit.lower() is gone from "
        "_seam_doubled, and every differently-cased unit in the report is now a "
        "doubling nobody can see")
    template_side = _seam_doubled("3,500 kwh", "", " KWH/yr")
    assert template_side and "'kWh'" in template_side, (
        "a template supplying an upper-case unit no longer counts as supplying it: "
        f"{template_side!r} -- after.lower() or unit.lower() is gone from _seam_doubled")
    # Controls: the same case-folding must not make the rule fire on prose that
    # supplies no unit at all, or the assertions above would pass on a rule
    # that reports everything.
    assert _seam_doubled("3,500 KWH", "", " today</p>") is None, (
        "an upper-case value followed by an ordinary word is reported as a doubled unit")
    assert _seam_doubled("3,500 kwh", "", " of the load") is None, (
        "a lower-case value followed by an ordinary word is reported as a doubled unit")
    return ("all three case folds in _seam_doubled are pinned in isolation -- the "
            "template's text, the value, and the unit spelling -- and none of them "
            "makes the rule fire on prose that supplies no unit")


@case
def case_the_seam_rules_read_markup_the_way_a_reader_does():
    """Two markup blind spots, closed and pinned.

    ONE, a '>' inside a QUOTED ATTRIBUTE does not end a tag. The naive
    `<[^>]*>` stops there and leaves the rest of the attribute -- plus its
    closing quote and bracket -- sitting in the text where the next visible
    word should be, so class 2 reads 'baseline">' as the word after the figure:
    a real missing unit is hidden behind a title attribute, and a line that
    DOES supply its unit is reported because the attribute's tail got there
    first. Both directions are driven below.

    TWO, attribute text is not PRINTED. A value repeated in a sort key --
    `<td data-sort="12,345.6 kWh">{{X}}</td>` -- appears once to a reader, and
    class 3 read it as twice. The windows now carry only what a reader sees,
    which the third assertion pins from the other side: the cell wall in the
    lifetime table is a CONTAINER boundary and the echo rule stops there, so
    the next cell's copy of a figure is out of the comparison entirely rather
    than merely far enough away.

    Neither shape exists in report-template.html today -- no live tag has a
    '>' inside a quoted attribute, and the template carries no data-*
    attribute at all -- so these are blind spots closed before they were
    reached, and this case is what keeps them closed."""
    attr = '<span title="comparison > baseline"> of the load</span></p>'
    hit = _seam_missing_unit("42", "<p>covers ", attr)
    assert hit and "'of'" in hit, (
        f"the word after the figure was read out of a title attribute: {hit!r} -- "
        "_SEAM_TAG_RE stopped honoring quoted attributes, so the visible next word is "
        "whatever the attribute happens to end with")
    quiet = '<span title="a > b"> kWh</span> imported</p>'
    assert _seam_missing_unit("3,500", "<p>", quiet) is None, (
        "a figure whose unit the template really does supply is reported missing one, "
        "because a quoted '>' left attribute text standing between the two")

    assert _seam_echo("12,345.6 kWh", '<td data-sort="12,345.6 kWh">', "</td>") is None, (
        "a value repeated in a sort ATTRIBUTE is reported as printed twice; attribute "
        "text is markup, and a reader sees the figure once")
    assert _seam_echo("12,345.6 kWh", "", "</td><td>12,345.6 kWh</td>") is None, (
        "the next table cell's copy of a figure is reported as an echo. The cell wall "
        "is a CONTAINER boundary, and the echo rule does not compare across one at "
        "all -- not at a distance, not at all -- so this is _seam_visible_after "
        "reading past a </td> or a <td>, either because the tag is no longer "
        "classified as a container or because the window stopped being truncated "
        "there. It is NOT about how many characters '</td><td>' occupies: the author "
        "may omit the optional </td> and valid HTML5 still means one cell to a reader")
    # ...and the visible repeat, on both sides, is still reported -- so the
    # masking narrowed the rule to markup rather than switching it off.
    after = _seam_echo("12,345.6 kWh", "<td>", " — 12,345.6 kWh</td>")
    assert after and "later" in after, f"a visible repeat after the value was lost: {after!r}"
    before = _seam_echo("12,345.6 kWh", "<td>12,345.6 kWh — ", "</td>")
    assert before and "earlier" in before, (
        f"a visible repeat ahead of the value was lost: {before!r}")
    return ("a '>' inside a quoted attribute no longer ends a tag for either rule, "
            "attribute text is no longer read as printed by the echo rule, the next "
            "cell's copy of a figure is out of the comparison, and a visible repeat on "
            "either side is still reported")


@case
def case_a_tag_is_classified_by_the_container_it_changes_not_by_its_length():
    """ISSUE #156. The rules read the text a READER sees, and the test applied
    to a tag is whether the reader is still reading the same run of text on the
    other side of it -- never how many characters the tag occupies.

    THE DEFECT THIS CLOSES. Markup that splits a figure hid an exact visible
    duplicate: `{{X}} <b>12</b>,345.6 kWh` with X = "12,345.6 kWh" prints the
    same figure twice on the page and was reported by nothing, because the
    source text between the two copies is not the source text of the value.
    The same line with the markup AROUND the echo rather than through it was
    caught, which is the tell: the rule was reading the serialization.

    THE OTHER HALF is that a container boundary is not a small distance, it is
    the end of the comparison. Text in the next <td>, <p> or <li> is not the
    run of text the reader is in, so no gap threshold is consulted across one.

    WHY NOT A CHARACTER COUNT, measured rather than argued. Blanking each tag
    to its own width makes the two designs below indistinguishable from this
    one on the shipped template and wrong on two shapes it does not carry yet:
    `</td><td>` is 9 characters only while the author writes the OPTIONAL
    </td> closer -- omit it, still valid HTML5, and the wall is 4 characters
    and the next cell is inside _SEAM_ECHO_GAP. Both source widths are pinned
    below so the argument cannot rot into an assertion."""
    # 1. INLINE FORMATTING IS ZERO WIDTH -- the figure split through a <b>.
    split = _seam_echo("12,345.6 kWh", "", " <b>12</b>,345.6 kWh</p>")
    assert split and "later" in split, (
        "a figure the template repeats WITH INLINE MARKUP THROUGH IT went unreported: "
        f"{split!r} -- '12</b>,345.6' is one figure to a reader, so _SEAM_INLINE_TAGS "
        "is no longer removed at zero width and the rule is back to reading the "
        "serialized source instead of the page")
    # ...and the same shape with the markup AROUND the echo, which the rule
    # caught before this change and must still catch.
    around = _seam_echo("12,345.6 kWh", "", " <b>12,345.6 kWh</b></p>")
    assert around and "later" in around, (
        f"a figure repeated inside inline markup went unreported: {around!r}")
    # The control: inline masking must not make the rule fire on a DIFFERENT
    # figure that happens to be split the same way.
    assert _seam_echo("12,345.6 kWh", "", " <b>12</b>,999.9 kWh</p>") is None, (
        "a different figure beside the value is reported as an echo of it")

    # 2. A CONTAINER BOUNDARY ENDS THE COMPARISON, at any width. Both spellings
    # of the cell wall, and both sides of the seam.
    for tail in ("</td><td>12,345.6 kWh</td>",      # with the optional closer
                 "<td>12,345.6 kWh"):               # without it -- valid HTML5
        assert _seam_echo("12,345.6 kWh", "", tail) is None, (
            f"the next table cell's copy of a figure is reported as an echo ({tail!r}); "
            "a cell wall is a container boundary and the echo rule does not compare "
            "across one")
    assert _seam_echo("12,345.6 kWh", "<td>12,345.6 kWh</td><td>", "") is None, (
        "the PREVIOUS cell's copy of a figure is reported as an echo; the head window "
        "is no longer cut back to the last container boundary")
    assert len("</td><td>") == 9 and len("<td>") == 4 and _SEAM_ECHO_GAP == 6, (
        f"the two spellings of a cell wall are {len('</td><td>')} and {len('<td>')} "
        f"source characters against a gap threshold of {_SEAM_ECHO_GAP}; the point of "
        "this case is that one of them is inside the threshold and the classification "
        "spares the row anyway, so if these numbers move re-argue it rather than "
        "editing them")
    # The positive control for the container half: the same repeat INSIDE one
    # cell is still reported, so this is a narrowed rule and not a silenced one.
    inside = _seam_echo("12,345.6 kWh", "<td>", " — 12,345.6 kWh</td>")
    assert inside and "later" in inside, (
        f"a repeat inside the SAME cell went unreported: {inside!r}")

    # 3. THE OTHER TWO RULES READ THE SAME WAY. Class 1 sees a unit through an
    # inline tag and does not see one across a cell wall; class 2's
    # preceding-sigil exemption survives an inline tag between the two.
    through = _seam_doubled("3,500 kWh", "", "</b> kWh/yr of output")
    assert through and "'kWh'" in through, (
        f"a doubled unit hidden behind an inline </b> went unreported: {through!r}")
    assert _seam_doubled("3,500 kWh", "", "</td><td>kWh</td>") is None, (
        "the next column's unit is reported as a doubling of the value's own; class 1 "
        "is reading across a container boundary")
    assert _seam_missing_unit("14,500", "<p>the pack costs $<b>", " of the total") is None, (
        "a bare number whose sigil sits behind an inline <b> is reported as having "
        "lost its unit; class 2's head is no longer read the way a reader reads it")
    return ("a tag is classified by whether the reader is still in the same run of text "
            f"({len(_SEAM_INLINE_TAGS)} inline names removed at zero width, "
            f"{len(_SEAM_CONTAINER_TAGS)} container names ending the comparison), the "
            "figure split through a <b> is reported, both spellings of a cell wall are "
            "spared, and the repeat inside one cell still fires")


@case
def case_a_line_break_does_not_end_the_run_a_reader_is_reading():
    """`br` IS INLINE, and it is the case that decides between classifying a
    tag by its container and treating block markup as a fixed-width barrier.

    A barrier design keeps the next table cell out of range by declaring block
    tags wider than _SEAM_ECHO_GAP. It has to put `<br>` somewhere, and `<br>`
    is 4 source characters -- exactly as many as `</p>`. Called a barrier, it
    goes permanently blind to a template that prints the same figure on two
    lines of ONE paragraph, which is a real defect and a common one in a report
    full of stacked figures. Called inline, the run of text continues through
    it and the repeat is reported, while `</p>` still ends the comparison.

    That is the whole argument for classifying by CONTAINER rather than by
    width: the two tags are the same size and mean opposite things."""
    assert "br" in _SEAM_INLINE_TAGS, (
        "`br` is no longer inline, so two printings of one figure separated by a line "
        "break inside a single paragraph are no longer compared. A <br> ends a LINE, "
        "not the run of text a reader is reading -- see the policy comment on "
        "_SEAM_INLINE_TAGS")
    assert len("<br>") == len("</p>") == 4, (
        "the two tags this case contrasts are no longer the same source width, which "
        "is the fact that makes a width-based classification impossible to state")
    broken = _seam_echo("12,345.6 kWh", "", "<br>12,345.6 kWh</p>")
    assert broken and "later" in broken, (
        f"a figure printed again after a <br> went unreported: {broken!r} -- the run "
        "of text continues through a line break, so this is the same figure twice")
    assert _seam_echo("12,345.6 kWh", "", "</p><p>12,345.6 kWh</p>") is None, (
        "the same figure in the NEXT paragraph is reported as an echo, so `p` has "
        "stopped being a container boundary; a width rule cannot tell these two "
        "fixtures apart and the classification has to")
    return ("<br> and </p> are the same 4 source characters and are classified "
            "oppositely: the repeat across the line break is reported, the repeat "
            "across the paragraph boundary is not")


@case
def case_ordinary_text_level_markup_does_not_hide_an_echo():
    """THE THREE SHAPES A DEFAULT REGRESSED, pinned by name.

    `<mark>`, `<u>` and `<time>` are ordinary text-level elements. A revision
    of this guard that classified only the twelve names the live template
    happened to carry, and defaulted the rest to "container", turned all three
    from CAUGHT to MISSED -- measured on
    `_seam_echo("12,345.6 kWh", "", "<TAG>12,345.6 kWh</TAG>")`, which reported
    the duplicate before that revision and returned None after. The three are
    named here because they are the shapes that actually regressed.

    They are not enough on their own: a set built by listing the three known
    failures passes this case and stays wrong about the next element nobody
    thought of. So the case also drives text-level elements NONE of the
    reported failures named -- `kbd`, `var`, `data`, `dfn`, `s`, `q`, `bdi`,
    `cite`, `samp`, `wbr` -- and the two edit elements. The claim under test is
    that the inline set is HTML's phrasing vocabulary rather than an inventory
    of this household's markup."""
    regressed = ("mark", "u", "time")
    # Not in the twelve names the old set listed, and not among the three
    # above either: these are what stops the set from being a list of the
    # failures somebody happened to report.
    beyond = ("kbd", "var", "data", "dfn", "s", "q", "bdi", "cite", "samp",
              "wbr", "del", "ins", "label", "output")
    for name in regressed + beyond:
        assert _seam_tag_kind(f"<{name}>") == "inline", (
            f"`{name}` is no longer inline. A reader reads straight through a "
            f"<{name}>, so the text on either side of it is one run; classifying it "
            "as a boundary hides a figure the template prints twice, which is "
            "exactly the regression this case exists to keep closed")
        echoed = _seam_echo("12,345.6 kWh", "", f"<{name}>12,345.6 kWh</{name}>")
        assert echoed and "later" in echoed, (
            f"a figure printed twice with a <{name}> between the two copies went "
            f"unreported: {echoed!r}. The reader sees '12,345.6 kWh12,345.6 kWh'")
    # The control, on the same fixture shape: a CONTAINER between the two
    # copies still ends the comparison, so this is a widened inline set and
    # not a rule that has started reporting every repeat it sees.
    assert _seam_echo("12,345.6 kWh", "", "</p><p>12,345.6 kWh</p>") is None, (
        "the same figure in the NEXT paragraph is now reported as an echo, so the "
        "inline set has swallowed a container name")
    # And the sets stay disjoint: a name in both would make the answer depend
    # on which lookup runs first.
    both = _SEAM_INLINE_TAGS & _SEAM_CONTAINER_TAGS
    assert not both, (
        f"{sorted(both)} is in BOTH tag sets, so its kind depends on the order of two "
        "lookups rather than on what a reader sees")
    return (f"{len(regressed + beyond)} text-level elements are inline and report the "
            "figure they sit between -- including the three (<mark>, <u>, <time>) a "
            "container default regressed -- while a paragraph boundary still ends the "
            "comparison")


@case
def case_an_unclassified_element_refuses_instead_of_guessing_its_kind():
    """THERE IS NO DEFAULT TAG KIND, AND THAT IS THE POINT.

    Both defaults are guesses and each guess has a victim. "container" hides a
    real duplicated figure behind any element the table has not heard of --
    which is what happened to `<mark>`, `<u>` and `<time>`, pinned in the case
    above. "inline" joins two runs of text a reader sees separately and reports
    a figure that is printed once, the false alarm this repo has been bitten by
    repeatedly: a guard that refuses a legitimate state is how a household
    stops being able to publish its report.

    So the guard refuses. It raises SeamTagUnclassified, names the element and
    names both sets, and the author who introduced the element classifies it.
    That is affordable ONLY because this is a test-only guard: the cost of the
    refusal is a red CI run and one word, not a report that cannot be built,
    and the message has to say so or the next reader will read it as the
    second kind of failure."""
    global _SEAM_INLINE_TAGS               # rebound and restored below
    probe = "flurb"
    assert (probe not in _SEAM_INLINE_TAGS
            and probe not in _SEAM_CONTAINER_TAGS), (
        f"`{probe}` is classified now, so it is no longer a probe for the refusal; "
        "pick an element name that is in neither set")
    try:
        got = _seam_tag_kind(f"<{probe} class='x'>")
    except SeamTagUnclassified as exc:
        message = str(exc)
    else:
        raise AssertionError(
            f"an unclassified element was silently answered {got!r} instead of "
            "refusing. A default in either direction is a guess: 'container' hides a "
            "real echo, 'inline' invents one")
    assert probe in message, (
        f"the refusal does not name the element it could not classify: {message!r} -- "
        "the author cannot act on it without reading the traceback")
    for named in ("_SEAM_INLINE_TAGS", "_SEAM_CONTAINER_TAGS"):
        assert named in message, (
            f"the refusal does not name {named}, so it says what went wrong without "
            f"saying where the one-word fix goes: {message!r}")
    assert "test-only" in message, (
        "the refusal does not say it is a TEST-ONLY guard, so it reads like a report "
        f"that cannot be published rather than a word of classification: {message!r}")

    # The refusal reaches the rules, not just the classifier: an unclassified
    # element anywhere in either window stops the comparison loudly.
    for head, tail in (("", f"<{probe}>12,345.6 kWh</{probe}>"),
                       (f"<{probe}>12,345.6 kWh ", "")):
        try:
            _seam_echo("12,345.6 kWh", head, tail)
        except SeamTagUnclassified:
            pass
        else:
            raise AssertionError(
                f"the echo rule answered for a window containing <{probe}> instead of "
                f"refusing (head={head!r}, tail={tail!r}); the refusal is trapped in "
                "the classifier and never reaches the rule that reads it")

    # ...and classifying it really is one word, in either direction. Both are
    # driven, because a refusal is only affordable if BOTH remedies work.
    shipped = _SEAM_INLINE_TAGS
    try:
        _SEAM_INLINE_TAGS = shipped | {probe}
        adopted = _seam_echo("12,345.6 kWh", "", f"<{probe}>12,345.6 kWh</{probe}>")
    finally:
        _SEAM_INLINE_TAGS = shipped
    assert adopted and "later" in adopted, (
        f"naming the element in _SEAM_INLINE_TAGS did not make its echo visible: "
        f"{adopted!r} -- the refusal is only affordable because the fix is one word")
    assert _SEAM_INLINE_TAGS is shipped, "the inline tag set was not restored"
    return ("an unclassified element raises SeamTagUnclassified from the classifier "
            "and from the echo rule, naming the element, both tag sets and the fact "
            "that this is a test-only guard; naming it inline closes the refusal")


@case
def case_an_invisible_construct_is_zero_width_not_a_boundary():
    """A COMMENT IS NOT AN ELEMENT AND IT IS NOT A BOUNDARY EITHER.

    `<!-- ... -->`, `<!DOCTYPE html>`, `<![CDATA[...]]>` and `<?xml ... ?>`
    carry no element name, so there is nothing for the two tag sets to answer
    and the refusal above would have nothing to tell the author to classify.
    They do not need one: a reader sees NO CHARACTERS where any of them sits.
    `12<!-- note -->,345.6` renders "12,345.6", one figure, so the honest
    answer is ZERO WIDTH -- kind "invisible" -- and the text closes up behind
    it exactly as behind a <b>.

    That is a decision about the reader, not a convenience. Calling a comment
    a boundary would let a template hide a duplicated figure behind a comment
    the reader cannot see, which is the same class of miss the container
    default made. Calling it inline would be the right WIDTH under the wrong
    name: an inline element wraps visible text and a comment contributes none.

    The doctype is classified for completeness rather than because the choice
    is reachable -- nothing can precede a doctype, so no run of text is ever
    joined across one. CDATA is a bogus comment to an HTML parser outside
    foreign content, which is where this template's markup lives."""
    for construct in ("<!-- a comment -->", "<!DOCTYPE html>",
                      "<![CDATA[ x ]]>", "<?xml version='1.0'?>"):
        assert _seam_tag_kind(construct) == "invisible", (
            f"{construct!r} is no longer classified 'invisible'. It carries no element "
            "name, so it is neither a lookup nor a refusal -- it prints no characters, "
            "and the only question is whether the guard agrees")
    split = _seam_echo("12,345.6 kWh", "", " 12<!-- keep in sync -->,345.6 kWh</p>")
    assert split and "later" in split, (
        f"a figure the template repeats WITH A COMMENT THROUGH IT went unreported: "
        f"{split!r} -- '12<!-- ... -->,345.6' renders as one figure, so a comment is "
        "being given a width the reader never sees")
    behind = _seam_echo("12,345.6 kWh", "", "<!-- the same figure again -->12,345.6 kWh")
    assert behind and "later" in behind, (
        f"a figure printed twice with a comment between the copies went unreported: "
        f"{behind!r} -- a comment is not a container, so it cannot end the comparison")
    # The control: the comment is zero width, not a licence to read anywhere.
    # A container boundary INSIDE the comment span still does not apply, and a
    # container boundary after it still does.
    assert _seam_echo("12,345.6 kWh", "", "<!-- x --></td><td>12,345.6 kWh") is None, (
        "the next cell's copy of a figure is reported as an echo once a comment "
        "precedes the cell wall; the wall is still a container boundary")
    assert _seam_missing_unit("3,500", "<p>", "<!-- unit --> kWh imported</p>") is None, (
        "the unit behind a comment is reported missing; class 2 reads the printed "
        "text, and a comment prints nothing")
    return ("a comment, doctype, CDATA section and processing instruction are zero "
            "width rather than boundaries -- the figure split through a comment and "
            "the figure repeated behind one are both reported, and a cell wall after "
            "a comment still ends the comparison")


# Element names that appear in report-template.html but are NOT markup: a
# placeholder inside a JavaScript comment, spelled `<season>` because that is
# how the artifact's key is written (data/tou_spread.json's
# delivery_spread.<season>.series). _SEAM_TAG_RE matches it, so the refusal
# would fire on it -- but only if a {{TOKEN}} ever lands on the same line,
# which is the condition the case below asserts is false. Adding a token to
# that line is a red CI run naming `season`, not a wrong answer.
_SEAM_TEMPLATE_NON_ELEMENTS = frozenset(("season",))


@case
def case_the_live_template_classifies_every_tag_it_scans():
    """THE REFUSAL MUST NOT FIRE ON THE SHIPPED TEMPLATE. A guard that fails
    closed is only usable if the live input is fully classified, so the
    template's own tag inventory is checked here rather than discovered when
    some other case walks a token line.

    Two claims, and the second is the one that stops this from being a
    tautology. First: every element name report-template.html contains is in
    one of the two sets, apart from the named non-elements above. Second:
    each of those named non-elements sits only on lines that carry no
    {{TOKEN}}, so the seam rules never reach it -- which is why the exception
    is safe rather than a hole punched in the check.

    THE INVENTORY IS TAKEN LINE BY LINE, because that is how the rules read
    the template (`_seam_render` is called per line) and the two answers are
    not the same. Read as one string, the multi-line `<!-- ... -->` block
    around line 700 swallows the `<season>` placeholder inside the script's
    comment and it never surfaces as a tag at all; read line by line -- the
    way the rules do -- it is a tag name in neither set. A whole-file scan
    would report a clean inventory the rules do not actually see."""
    template = rt.TEMPLATE.read_text()
    names = {m.group(1).lower()
             for line in template.splitlines()
             for tag in _SEAM_TAG_RE.findall(line)
             for m in [_SEAM_TAG_NAME_RE.match(tag)] if m}
    assert len(names) > 20, (
        f"only {len(names)} element names were found in report-template.html; the tag "
        "inventory is not being read, so this case would pass on an empty set")
    unclassified = sorted(names - _SEAM_INLINE_TAGS - _SEAM_CONTAINER_TAGS)
    assert unclassified == sorted(_SEAM_TEMPLATE_NON_ELEMENTS), (
        f"report-template.html carries element name(s) the seam guard cannot classify: "
        f"{unclassified}. Every one of them raises SeamTagUnclassified the moment a "
        "{{TOKEN}} shares its line. Add each to _SEAM_INLINE_TAGS or "
        "_SEAM_CONTAINER_TAGS -- or, if it is not markup at all, to "
        "_SEAM_TEMPLATE_NON_ELEMENTS with the reason")
    # The exception is only safe while these names stay off token lines.
    for name in _SEAM_TEMPLATE_NON_ELEMENTS:
        on_token_lines = [n for n, line in enumerate(template.splitlines(), 1)
                          if re.search(rf"</?\s*{re.escape(name)}\b", line)
                          and _SEAM_TOKEN_RE.search(line)]
        assert not on_token_lines, (
            f"`{name}` is excused as a non-element, but it now shares line(s) "
            f"{on_token_lines} with a {{{{TOKEN}}}}, so the seam rules DO reach it and "
            "the guard refuses. Classify it or move it off the token's line")
    # The positive control: the classifier really is exercised over the live
    # markup, and answers for every name it found.
    kinds = {_seam_tag_kind(f"<{name}>")
             for name in names - _SEAM_TEMPLATE_NON_ELEMENTS}
    assert kinds <= {"inline", "container"} and len(kinds) == 2, (
        f"the live template's tags classify as {sorted(kinds)}; both kinds should be "
        "present, and an 'invisible' answer here would mean _SEAM_TAG_NAME_RE matched "
        "a construct that has no element name")
    return (f"all {len(names)} element names in report-template.html classify "
            f"({len(names & _SEAM_INLINE_TAGS)} inline, "
            f"{len(names & _SEAM_CONTAINER_TAGS)} container), and the "
            f"{len(_SEAM_TEMPLATE_NON_ELEMENTS)} excused non-element(s) "
            f"({', '.join(sorted(_SEAM_TEMPLATE_NON_ELEMENTS))}) share no line with a "
            "token, so the refusal cannot fire on the shipped template")


@case
def case_the_seam_rules_read_entities_the_way_a_reader_does():
    """ISSUE #156. An HTML entity is TEXT, and the rules compared source
    spellings, so every one of the three classes could be evaded -- or made to
    cry wolf -- by writing an ordinary character as an entity.

    Four shapes, all reproduced before the fix:
      * `cost &#36;{{X}}` with X = "$14,500" prints "cost $$14,500" and the
        doubled-sigil rule saw a ';' in front of the value.
      * `{{X}}&#176;` and `{{X}}&#37;` are the same defect for a unit and for
        a percent sign; the literal '°' and '%' spellings were caught, so the
        rule's blind spot was the spelling and not the shape.
      * `{{X}} &times; 335 W` with X = "30" was a FALSE POSITIVE: the reader's
        next character there is a multiplication sign, and class 2 read the
        source as a word called "times" that is not a unit.
      * `{{X}}&mdash;12,345.6 kWh` hid an echo by costing 7 SOURCE characters
        of gap against a threshold of 6, for a dash the reader sees as one.

    THE ORDER IS THE DESIGN. Tags come out first and entities are decoded
    second, so that a template's own `&lt;td&gt;` -- prose ABOUT a tag, which a
    reader sees as four visible characters -- is never turned into markup that
    the tag pass then eats. The last assertion drives exactly that."""
    tok = _SEAM_PROBE_TOKEN
    # 1. A sigil written as an entity is a sigil.
    entity_sigil = _seam_doubled("$14,500", "<p>cost &#36;", "</p>")
    assert entity_sigil and "'$'" in entity_sigil, (
        f"a template sigil spelled '&#36;' in front of a value that already carries it "
        f"went unreported: {entity_sigil!r} -- a reader sees '$$14,500'")
    for value, entity, sign in (("72°", "&#176;", "'°'"), ("55%", "&#37;", "'%'")):
        hit = _seam_doubled(value, "<p>", entity + "</p>")
        assert hit and sign in hit, (
            f"a template {sign} spelled {entity!r} behind a value already ending in it "
            f"went unreported: {hit!r} -- the literal spelling is caught, so this is "
            "the entity and not the shape")
    # 2. The echo the entity spelling hid, end to end through _seam_render so
    # the value is escaped exactly as the generator writes it.
    fragment = "<p>{{%s}} (PG&#38;E 12,345.6)</p>" % tok
    values = {tok: "PG&E 12,345.6"}
    hits = _seam_defects(fragment, values)
    assert any(c == "echoed-phrase" for _n, c, _w, _x in hits), (
        f"a figure the template repeats with an entity inside it went unreported: "
        f"{hits} -- rendered {_seam_render(fragment, values)[0]!r}")
    # 3. The false positive: an entity is not a word.
    assert _seam_missing_unit("30", "<p>", " &times; 335 W</p>") is None, (
        "'{{X}} &times; 335 W' is reported as a bare number followed by a word that is "
        "not a unit; the reader's next character is '×', which carries no letters and "
        "claims to be nothing")
    real_word = _seam_missing_unit("30", "<p>", " times 335 W</p>")
    assert real_word and "'times'" in real_word, (
        f"the same figure followed by the actual WORD 'times' went unreported: "
        f"{real_word!r} -- without this control the assertion above passes on a rule "
        "that reports nothing")
    # 4. An entity costs its reader ONE character of gap, not its source width.
    assert len("&mdash;") == 7 > _SEAM_ECHO_GAP, (
        "'&mdash;' no longer exceeds _SEAM_ECHO_GAP in source characters, so this "
        "fixture no longer measures what the decode buys")
    dashed = _seam_echo("12,345.6 kWh", "", "&mdash;12,345.6 kWh</p>")
    assert dashed and "later" in dashed, (
        f"a figure repeated one em dash away went unreported: {dashed!r} -- the dash is "
        "7 characters of source and one character to a reader, and the gap threshold "
        "measures what the reader sees")
    # 5. THE ORDER. The template prints '<td>' as visible prose by escaping it.
    # Decode-first would turn that back into a container boundary and cut the
    # window at it, losing the echo six characters further on.
    prose = _seam_echo("12,345.6 kWh", "", " &lt;td&gt; 12,345.6 kWh</p>")
    assert prose and "later" in prose, (
        f"an echo behind a template's ESCAPED '<td>' went unreported: {prose!r} -- "
        "entities are being decoded before the tags are masked, so prose about a tag "
        "became a tag and the mask ate the text after it")
    return ("a sigil, a degree sign and a percent sign spelled as entities are read as "
            "the characters a reader sees, '&times;' is no longer a word that is not a "
            "unit, an em dash costs one character of gap rather than seven, and the "
            "tags come out before the entities are decoded")


@case
def case_the_lifetime_tables_cumulative_cell_is_not_an_echo():
    """THE ONE LIVE REPEAT IN report-template.html, pinned so that a future
    threshold change cannot start flagging it in silence.

    The lifetime table prints the first year's value in both the annual and the
    cumulative cell -- one figure, two columns, exactly what a cumulative column
    is for on its first row. It is the only place the shipped template prints
    one token's value twice on a line, and it is the false positive every
    tightening of class 3 lands on first.

    TWO RULES COULD SPARE IT AND ONLY ONE OF THEM IS LOAD-BEARING. Today's
    FIRST_YEAR_VALUE is short enough that _SEAM_MIN_ECHO excludes it before the
    echo search runs at all -- an accident of this household's arithmetic that
    stops being true the moment the first year's value reaches five digits.
    What spares the row at ANY length is that the two cells are two containers.
    So the row is driven here with today's value AND with longer ones that
    clear the floor, and a case that passed only because of the floor fails.

    The line number is pinned too. Two comments in this file name
    report-template.html:553 as this row; the last pair of comments to name a
    line number here named five of them and every one was stale."""
    text = rt.TEMPLATE.read_text()
    row = _seam_template_line("FIRST_YEAR_VALUE", text)
    lineno = text.splitlines().index(row) + 1
    assert lineno == 553, (
        f"the lifetime table's repeated-value row is report-template.html:{lineno}, "
        "not 553. Update the two comments in this file that name that line -- the "
        "class 3 paragraph in the block comment and the _SEAM_INLINE_TAGS policy "
        "comment -- in the same edit, which is what this assertion exists to force")
    assert row.count("{{FIRST_YEAR_VALUE}}") == 2, (
        f"report-template.html:{lineno} no longer prints FIRST_YEAR_VALUE twice "
        f"({row!r}); if the cumulative column is gone, this case and the comments that "
        "cite the row are describing a template that no longer exists")

    others = {"FIRST_FULL_YEAR": "2019", "FIRST_YEAR_PRODUCTION_KWH": "8,935"}
    short, long_ = "~$5,600", ("~$15,600", "~$125,600.00")
    assert len(short.rstrip(_SEAM_ECHO_TRIM)) < _SEAM_MIN_ECHO, (
        f"{short!r} now clears _SEAM_MIN_ECHO, so it is no longer the short probe this "
        "case contrasts the long ones against")
    for probe in (short,) + long_:
        values = dict(others, FIRST_YEAR_VALUE=probe)
        hits = [(c, w) for n, c, w, _x in _seam_defects(row, values)
                if n == "FIRST_YEAR_VALUE"]
        assert not hits, (
            f"the lifetime table's cumulative cell is reported as an echo of the annual "
            f"cell with FIRST_YEAR_VALUE = {probe!r}: {hits}. The two cells are two "
            "CONTAINERS and the echo rule does not compare across one; if a threshold "
            "moved, that is not why this row is quiet and the fix is not to move it "
            "back")
        if probe in long_:
            assert len(probe.rstrip(_SEAM_ECHO_TRIM)) >= _SEAM_MIN_ECHO, (
                f"{probe!r} is under _SEAM_MIN_ECHO, so the assertion above passed on "
                "the length floor and proves nothing about the container boundary")
    # The positive control, on the same row: move the second printing INSIDE
    # the annual cell and it is an echo again, so the quiet above is the cell
    # wall and not a rule that stopped looking.
    two_cells = "<td>{{FIRST_YEAR_VALUE}}</td><td>{{FIRST_YEAR_VALUE}}</td>"
    one_cell = "<td>{{FIRST_YEAR_VALUE}} {{FIRST_YEAR_VALUE}}</td>"
    same_cell = row.replace(two_cells, one_cell, 1)
    assert same_cell != row, (
        f"the row does not carry {two_cells!r}, so the substitution that moves the two "
        "printings into one cell matched nothing and the control below would run "
        f"against the unmodified row and prove nothing; the row reads {row!r}")
    assert one_cell in same_cell and two_cells not in same_cell, (
        f"the one-cell control is not the shape it claims to be: {same_cell!r}")
    for probe in long_:
        values = dict(others, FIRST_YEAR_VALUE=probe)
        echoed = [c for n, c, _w, _x in _seam_defects(same_cell, values)
                  if n == "FIRST_YEAR_VALUE"]
        assert "echoed-phrase" in echoed, (
            f"the same figure printed twice INSIDE one cell with FIRST_YEAR_VALUE = "
            f"{probe!r} is not reported ({echoed or 'nothing'}); class 3 is not spared "
            "by the container boundary here, it has stopped working")
    return (f"report-template.html:{lineno}'s cumulative cell is quiet at "
            f"{len((short,) + long_)} value lengths -- including "
            f"{max(long_, key=len)!r}, well clear of _SEAM_MIN_ECHO = "
            f"{_SEAM_MIN_ECHO} -- because a cell wall ends the comparison, and the "
            "same repeat inside one cell is still reported")


@case
def case_the_seam_guard_compares_the_values_the_generator_writes():
    """The guard renders what generate_report.render() renders: the value
    html.escape()d with quote=True.

    Escaping is neutral for class 1 -- it never touches a leading or trailing
    sigil -- and the block comment used to generalise that into "escaping
    cannot hide a seam", which is FALSE for class 3, the one rule that compares
    a value's INTERNAL text. Thirteen live token values change under escaping,
    so this is the shipped path.

    The rules now DECODE entities before they compare, which narrows what the
    escaping itself is still buying and does not remove it. A plain "&" in a
    value and a template's "&amp;" spelling of it meet in the middle after the
    decode, so "PG&E 2025" -- the probe this case used to carry -- no longer
    tells an escaped comparison from a raw one. A value that literally SPELLS
    an entity still does, and that is the probe below; the comment beside it
    says why it is the only shape left.

    The escaping is checked AGAINST THE GENERATOR rather than restated here:
    generate_report.render() is driven over the real template with one probe
    value carrying every character html.escape(quote=True) touches, and
    _seam_render is required to produce the same substitution."""
    import generate_report as gr

    probe_token = "UTILITY_NAME"
    probe = """PG&E "2025" <x> it's"""
    assert probe_token in rt.TOKENS, f"{probe_token} is no longer a token"
    generated, _missing = gr.render(rt.TEMPLATE.read_text(), {}, {probe_token: probe})
    written = _htmllib.escape(probe, quote=True)
    assert written in generated and probe not in generated, (
        "generate_report.render() no longer writes html.escape(value, quote=True) into "
        "the document; the seam guard is now comparing text the published page does not "
        "contain. Re-read its _sub and follow it here")
    rendered, spans = _seam_render("<p>{{%s}}</p>" % probe_token, {probe_token: probe})
    assert rendered == "<p>%s</p>" % written, (
        f"_seam_render substitutes {rendered!r}, not what the generator writes "
        f"({written!r})")
    name, start, end = spans[0]
    assert (name, rendered[start:end]) == (probe_token, written), (
        f"the span the rules read is {rendered[start:end]!r}, not the written value")

    # The echo the unescaped comparison could not see, both halves.
    #
    # WHY THIS PROBE AND NOT "PG&E 2025". The rules now decode entities before
    # they compare (mask the tags, then unescape), so a plain "&" in a value
    # and the template's "&amp;" spelling of it arrive in the same alphabet
    # whether the value was escaped on the way in or not: "PG&E 2025" no longer
    # separates the escaped comparison from the raw one, and a control built on
    # it would pass on a guard that had stopped escaping. What still separates
    # them is a value that ITSELF SPELLS AN ENTITY. html.escape turns its "&"
    # into "&amp;", so the decode hands the rule the value's own text back --
    # while the same value fed in raw decodes into a DIFFERENT character, and
    # the template's copy of what a reader sees is then something the rule
    # cannot find. It is the one shape where escaping still changes the answer,
    # which is why it is the probe.
    echoed = {"A_TOKEN_THAT_DOES_NOT_EXIST_YET": "&mdash; 12,345.6 kWh"}
    fragment = "<p>{{A_TOKEN_THAT_DOES_NOT_EXIST_YET}} &amp;mdash; 12,345.6 kWh</p>"
    hits = _seam_defects(fragment, echoed)
    assert any(n == "A_TOKEN_THAT_DOES_NOT_EXIST_YET" and c == "echoed-phrase"
               for n, c, _w, _x in hits), (
        "a figure the template prints beside a token whose value renders to the same "
        f"escaped text went unreported: {hits} -- rendered "
        f"{_seam_render(fragment, echoed)[0]!r}")
    assert _seam_echo("&mdash; 12,345.6 kWh", "", " &amp;mdash; 12,345.6 kWh</p>") is None, (
        "the unescaped value already matches the escaped template text, so this case "
        "is no longer measuring what the escaping buys; pick a probe whose escaping "
        "actually changes it -- since the rules decode entities, that now means a "
        "value that literally spells one, not merely a value carrying an '&'")
    return ("the guard substitutes html.escape(value, quote=True), checked against "
            "generate_report.render() on the real template rather than restated, and "
            "the echoed figure that only the escaped comparison can see is reported")


@case
def case_the_echo_rule_reads_both_ends_of_the_value():
    """Class 3 tries the value's OPENING as well as its tail against each side
    of the seam, and the opening half was unpinned: `core[:length]` could be
    deleted with this whole suite green, because every echo fixture in this
    file repeats a value's TAIL.

    It is real capability and not symmetry for its own sake. A template that
    prints a figure the value LEADS with -- "{{X}} (12,345.6 kWh)" where X is
    "12,345.6 kWh recovered over ..." -- repeats it just as visibly as one that
    trails it, and the suffix half cannot see that at any length.

    It also decides what is findable at all in a long value. The echo search
    reads a 120-character window on each side of the seam (see the block
    comment), so the runs the suffix half can look for are the value's last 120
    characters and nothing else. Values longer than the window are ordinary in
    this report -- the return line below counts them on the run rather than
    committing a number here -- and for every one of them, an echo of the
    value's opening is reachable ONLY through the head half."""
    # The plain shape, both sides of the seam. The repeated run is the value's
    # opening; no suffix of the value appears anywhere in either line.
    value = "12,345.6 kWh recovered over the study window"
    after = _seam_echo(value, "", " (12,345.6 kWh)")
    assert after and after.startswith("the value's own text '12,345.6 kWh"), (
        f"the template's copy of a figure the value OPENS with went unreported when it "
        f"sat after the token: {after!r}")
    before = _seam_echo(value, "<li>12,345.6 kWh — ", "")
    assert before and "earlier" in before, (
        f"the same copy went unreported when it sat ahead of the token: {before!r}")
    # Neither fires without the repeat, so the assertions above are about the
    # echo and not about the value.
    assert _seam_echo(value, "<li>the array ", " and nothing else</li>") is None, (
        "the same value is reported as echoed in a line that prints it once")

    # A value longer than the 120-character window, with its OPENING repeated
    # beside it: the suffix half is looking at the value's tail, which appears
    # nowhere, so this is the head half or nothing.
    opening = "9.45 kW AC across 30 modules,"
    long_value = opening + " metered at the inverters and reconciled against the " \
                           "billing export month by month for the whole study year"
    assert len(long_value) > 120, (
        f"the long-value fixture is only {len(long_value)} characters; it has to "
        "exceed the 120-character echo window to test what that window excludes")
    long_hit = _seam_echo(long_value, "", f" ({opening})")
    assert long_hit and long_hit.startswith(f"the value's own text {opening!r}"), (
        "a long value whose OPENING the template reprints beside it went unreported: "
        f"{long_hit!r} -- past the 120-character window the suffix half can only ever "
        "look for the value's tail, so the head half is the only one that can see this")

    values, _gaps = _seam_values()
    over = sum(1 for v in values.values() if len(v) > 120)
    longest = max(len(v) for v in values.values())
    return ("the echo rule reads the value's opening as well as its tail, on both "
            f"sides of the seam and past the 120-character window: {over} of "
            f"{len(values)} rendered token values are longer than that window "
            f"(longest {longest} characters)")


@case
def case_the_echo_rule_reads_the_side_of_the_seam_the_template_prints_first():
    """The template's copy of a figure can sit in FRONT of the token, and after
    commit c79cc06 that is the likelier direction: the AC ceiling now lives in
    the template rather than in INVERTER_DESCRIPTION, so an edit restoring it
    token-side prints it twice with the sides swapped. A tail-only class 3
    reports nothing on that line.

    Driven against the shipped template line with the two halves reordered --
    derived from the line rather than quoted, so it cannot drift out from under
    the case -- and against the same line with the token's CURRENT value, which
    must stay clean."""
    values, _gaps = _seam_values()
    ac, inverter = values["AC_CEILING_KW"], values["INVERTER_DESCRIPTION"]
    assert ", ~" in inverter, (
        f"INVERTER_DESCRIPTION no longer reads '..., ~V VA each' ({inverter!r}), so "
        "this case cannot rebuild the pre-#134 value it reverts to")
    pre_134 = inverter.replace(", ~", " (~", 1) + f" ≈ {ac} kW AC max)"

    shipped = _seam_template_line("INVERTER_DESCRIPTION")
    m = re.search(r"\{\{INVERTER_DESCRIPTION\}\}(\s*≈\s*\{\{AC_CEILING_KW\}\}[^<]*)", shipped)
    assert m, (
        "report-template.html no longer prints the AC ceiling right after "
        f"{{{{INVERTER_DESCRIPTION}}}} ({shipped!r}); this case reorders that clause")
    mirrored = shipped.replace(m.group(0), f"{m.group(1).strip()} — {{{{INVERTER_DESCRIPTION}}}}")
    assert "{{INVERTER_DESCRIPTION}}" in mirrored and "{{AC_CEILING_KW}}" in mirrored, (
        f"the reordered line lost a token: {mirrored!r}")

    broken = dict(values, INVERTER_DESCRIPTION=pre_134)
    hits = _seam_defects(mirrored, broken)
    assert any(n == "INVERTER_DESCRIPTION" and c == "echoed-phrase"
               for n, c, _w, _x in hits), (
        "the guard did not report the AC ceiling printed AHEAD of a token whose value "
        f"already ends with it: got {[(n, c) for n, c, _w, _x in hits] or 'nothing'}\n"
        f"  rendered: {_seam_render(mirrored, broken)[0]!r}")
    # The same reordering with today's value is correct prose and must be clean,
    # and the shipped order with the reverted value fires as well -- the rule is
    # about the repeat, not about which side it lands on.
    assert not _seam_defects(mirrored, values), (
        "the reordered line is flagged with the token's current value, which prints the "
        f"ceiling once: {_seam_defects(mirrored, values)}")
    assert any(n == "INVERTER_DESCRIPTION" and c == "echoed-phrase"
               for n, c, _w, _x in _seam_defects(shipped, broken)), (
        "the shipped order stopped reporting the reverted token, so the two-sided rule "
        "traded one direction for the other")
    return ("the echo rule reports the figure printed ahead of the token as well as "
            "behind it, and neither direction fires on the line as it ships")


@case
def case_the_shipped_line_fixture_decides_liveness_by_comment_span():
    """_seam_template_line decides LIVENESS from comment spans, not from
    whether the line happens to contain the characters '<!--'.

    Both failure directions are real in this template, and both are asserted to
    still be real here rather than assumed: it carries multi-line <!-- TODO -->
    blocks whose interior token lines contain no '<!--' of their own (a
    per-line test reads them as live markup) and live lines that CARRY an
    inline '<!--' opener (a per-line test throws them away) -- the filter below
    counts both the openers that run to end of line and the comments that open
    and close mid-line with live tokens after them. Today the one
    fixture that calls this helper wants a token that appears exactly once in
    the whole file, so neither mistake changes the answer -- which is precisely
    how a fixture ends up silently driven against a TODO comment while claiming
    to use the template as it ships."""
    tpl = rt.TEMPLATE.read_text()
    pairs = list(zip(tpl.splitlines(), _seam_comment_mask(tpl).splitlines()))
    assert len(pairs) == len(tpl.splitlines()), "the mask dropped or added a line"
    interior = [line for line, masked in pairs
                if _SEAM_TOKEN_RE.search(line) and "<!--" not in line
                and not _SEAM_TOKEN_RE.search(masked)]
    inline = [line for line, masked in pairs
              if _SEAM_TOKEN_RE.search(masked) and "<!--" in line]
    assert interior, (
        "no comment-interior token line in report-template.html, so half of what this "
        "case guards is no longer reachable; re-read the helper before deleting it")
    assert inline, (
        "no live token line carrying an inline '<!--' in report-template.html, so the "
        "other half is no longer reachable")

    tok = _SEAM_PROBE_TOKEN
    live = "<li>ships {{%s}} here</li>" % tok
    todo = ("<!-- TODO: fill this in\n"
            "     using {{%s}} and friends\n"
            "     -->" % tok)
    inline_live = "<li>ships {{%s}} here</li><!-- TODO: and the rest" % tok

    def refuses(text, why):
        try:
            got = _seam_template_line(tok, text)
        except AssertionError:
            return
        raise AssertionError(f"{why}: _seam_template_line returned {got!r}")

    # A token that exists ONLY inside a multi-line comment is not a live line.
    refuses(todo, "a token that lives only inside a <!-- TODO --> block was "
                  "accepted as the shipped line")
    # The comment copy comes FIRST: "the first line that matches" answers with it.
    assert _seam_template_line(tok, todo + "\n" + live) == live, (
        "the commented occurrence won over the live one, so the fixture would be "
        "driven against a TODO block")
    # A live line may carry an inline opener; the token before it is still live.
    assert _seam_template_line(tok, inline_live + "\n     more prose -->") == inline_live, (
        "a live line was thrown away because it ends with a comment opener")
    # And the "exactly one" contract still holds for two genuinely live lines.
    refuses(live + "\n" + live, "two live lines were reported as one")

    shipped = _seam_template_line("INVERTER_DESCRIPTION")
    assert shipped in tpl.splitlines() and "<!--" not in shipped, (
        f"the shipped INVERTER_DESCRIPTION line is not a plain live line: {shipped!r}")
    return (f"liveness is read off comment spans: {len(interior)} comment-interior token "
            f"line(s) and {len(inline)} live line(s) with an inline opener are classified "
            "the way a browser reads them, and the first-match shortcut is rejected")


@case
def case_the_seam_allowlist_excuses_a_seam_and_cannot_go_stale():
    """ISSUE #133's fourth acceptance criterion. The live entries below are all
    one class on one shape (a dimensionless count or year), so the rest of the
    escape hatch's contract is driven here on synthetic templates -- and an
    unexercised escape hatch is exactly the loophole that manufactures false
    confidence. Four halves: an entry suppresses the seam it names, an entry
    does NOT suppress the same token's OTHER defect classes, an entry does NOT
    suppress the SAME class at another OCCURRENCE of the same token, and an
    entry whose seam has since been fixed is reported stale by
    _seam_stale_allowlist -- the same call, not a re-derived copy of it, that
    the live case above makes against report-template.html. Inverting that
    comparison fails this case.

    THE THIRD HALF IS WHY THE KEY CARRIES A MARKER. Keyed by (token, class)
    alone, an entry excusing one occurrence pardons every other occurrence of
    that token in the same class, and the stale check stays green because the
    first occurrence keeps matching -- so a real lost unit ships behind an
    excuse written for a different line. That is not hypothetical: this
    template prints {{BILL_COUNT}} on two lines, both bare counts, and the
    fixture below is that shape."""
    with _seam_allowlist_cleared():
        token = "A_TOKEN_THAT_DOES_NOT_EXIST_YET"
        fragment = "<p>costs ~{{%s}} installed</p>" % token
        values = {token: "~$9,999"}
        assert _seam_defects(fragment, values), "the fixture no longer has a seam to excuse"

        key = (token, "doubled-sigil", "costs ~{{%s}} installed" % token)
        _SEAM_ALLOWLIST[key] = "synthetic fixture, this case only"
        try:
            assert not _seam_defects(fragment, values), (
                "an allowlisted seam was still reported, so the allowlist does nothing")
            assert _seam_defects(fragment, values, apply_allowlist=False), (
                "apply_allowlist=False must still see the excused seam, or the stale-entry "
                "check in the live case can never find anything")
            # Not a blanket pardon: the same token's other classes still report.
            other = "<p>covers {{%s}} of the load</p>" % token
            assert any(n == token and c == "missing-unit"
                       for n, c, _w, _x in _seam_defects(other, {token: "42"})), (
                "an entry excusing one class also suppressed another class for the same "
                "token, so one excuse blinds the token to every defect #129 shipped")
            # Live: the entry is NOT stale while its own seam is present...
            assert not _seam_stale_allowlist(fragment, values), (
                "an entry whose seam is present was reported stale")
            # ...and IS stale against a template where that seam has been fixed.
            fixed = "<p>costs {{%s}} installed</p>" % token
            stale = _seam_stale_allowlist(fixed, values)
            assert [k for k, _why in stale] == [key], (
                f"a fixed seam left its excuse un-flagged: {stale}")
        finally:
            del _SEAM_ALLOWLIST[key]

        # THE OCCURRENCE HALF, on the shape the live allowlist actually has: one
        # token, one class, two lines. The excused line goes quiet; the OTHER line
        # keeps reporting, and the excuse is not stale because its own line still
        # has the seam.
        two_lines = ("<p>{{%s}} of 13 periods</p>\n"
                     "<p>covers {{%s}} of the load</p>" % (token, token))
        counts = {token: "12"}
        both = _seam_defects(two_lines, counts)
        assert len(both) == 2 and {c for _n, c, _w, _x in both} == {"missing-unit"}, (
            f"the two-occurrence fixture no longer has two missing-unit seams: {both}")
        occurrence = (token, "missing-unit", "of 13 periods")
        _SEAM_ALLOWLIST[occurrence] = "synthetic fixture, this case only"
        try:
            left = _seam_defects(two_lines, counts)
            assert [(n, c) for n, c, _w, _x in left] == [(token, "missing-unit")], (
                "an entry naming ONE occurrence suppressed the same token's seam at the "
                f"OTHER occurrence too, so it is a token-wide pardon: {left}")
            assert "of the load" in left[0][3], (
                f"the surviving report is not the un-excused occurrence: {left}")
            assert not _seam_stale_allowlist(two_lines, counts), (
                "an occurrence-level entry whose own line still has the seam was reported "
                "stale")
            # ...and once ITS line is fixed, it is stale even though the other
            # occurrence still has the same (token, class) seam. This is the check
            # a (token, class) key cannot make: the other occurrence would keep it
            # green forever.
            one_fixed = ("<p>{{%s}} periods of 13</p>\n"
                         "<p>covers {{%s}} of the load</p>" % (token, token))
            stale = _seam_stale_allowlist(one_fixed, counts)
            assert [k for k, _why in stale] == [occurrence], (
                "an entry whose own occurrence was fixed stayed green because ANOTHER "
                f"occurrence of the same token still has the seam: {stale}")
        finally:
            del _SEAM_ALLOWLIST[occurrence]

        # A MALFORMED key is a fifth half, and it is the one that misdirects: a
        # key that is not a (token, class, marker) triple, or that names a class or
        # a token nothing can report, or whose marker names no single line, never
        # excuses an occurrence -- so a plain difference calls it stale and the
        # live case tells the reader to "delete the entry -- it excuses a seam that
        # has since been fixed". The entry never excused anything. Each shape fails
        # by name instead. The last two are the marker's own loopholes: an empty
        # marker matches every line and an over-broad one matches several, and both
        # are the token-wide pardon this key shape exists to stop.
        two_line_fragment = fragment + "\n" + fragment.replace("costs", "prices")
        malformed = [
            (token, "not a (token, class, marker) triple"),
            ((token,), "not a (token, class, marker) triple"),
            ((token, "doubled-sigil"), "not a (token, class, marker) triple"),
            ((token, "doubled-sigil", "m", "and one more"),
             "not a (token, class, marker) triple"),
            ((token, "doubled_sigil", "costs"), "not one of"),
            ((token, "wrong-class", "costs"), "not one of"),
            (("A_TOKEN_NOBODY_RESOLVES", "doubled-sigil", "costs"), "does not resolve"),
            ((token, "doubled-sigil", ""), "empty or non-string marker"),
            ((token, "doubled-sigil", None), "empty or non-string marker"),
            ((token, "doubled-sigil", "installed"), "matching 2 template lines"),
        ]
        for bad, expected in malformed:
            _SEAM_ALLOWLIST[bad] = "malformed, this case only"
            try:
                try:
                    _seam_stale_allowlist(two_line_fragment, values)
                except AssertionError as exc:
                    assert expected in str(exc), (
                        f"a malformed allowlist key {bad!r} failed with a message that does "
                        f"not say why ({expected!r} missing): {exc}")
                else:
                    raise AssertionError(
                        f"the malformed allowlist key {bad!r} was accepted; it suppresses "
                        "nothing and would be reported as a seam that has since been fixed")
            finally:
                del _SEAM_ALLOWLIST[bad]

    assert set(_SEAM_ALLOWLIST) == set(_SEAM_ALLOWLIST_SHIPPED), (
        "this case left _SEAM_ALLOWLIST different from the committed one: added "
        f"{sorted(set(_SEAM_ALLOWLIST) - set(_SEAM_ALLOWLIST_SHIPPED))}, removed "
        f"{sorted(set(_SEAM_ALLOWLIST_SHIPPED) - set(_SEAM_ALLOWLIST))}")
    return ("the seam allowlist suppresses only the (token, class, marker) occurrence it "
            "names, leaves that token's other classes AND its other occurrences "
            "reporting, is flagged stale by the live check once its own line is fixed "
            f"even while another occurrence still has the seam, and rejects "
            f"{len(malformed)} malformed key shapes by name rather than calling them "
            f"stale ({len(_SEAM_ALLOWLIST_SHIPPED)} committed entries on the live "
            "template)")


# ===========================================================================
# ISSUE #140, /review: the always-on floor's sensitivity ladder, its scope
# claim, and the two public documents that publish its figures.
# ===========================================================================
def _ladder_rungs():
    """The reductions data/quiet_night_floor.json's sensitivity ladder was
    actually re-billed at, smallest first."""
    steps = rt._json("quiet_night_floor.json")["sensitivity_per_100w"]["steps"]
    return sorted(s["reduction_w"] for s in steps)


def _rebuild_marginal_range(sens):
    """sensitivity_per_100w.marginal_range recomputed off `steps`, exactly the
    way quiet_night_floor.sensitivity_per_100w() writes it -- including its
    `or steps[:1]` fallback, so a floor smaller than one rung still publishes
    a reachable block instead of dropping the key.

    Re-implemented here rather than imported because quiet_night_floor.py
    needs the private archive to run. It is a mechanical restatement of a
    min/max over one column, not of the defect: the axis stays reduction_w
    throughout."""
    steps = sens["steps"]

    def span(rows):
        lo = min(rows, key=lambda s: s["marginal_usd_per_100w"])
        hi = max(rows, key=lambda s: s["marginal_usd_per_100w"])
        return {"min_usd": lo["marginal_usd_per_100w"],
                "min_at_reduction_w": lo["reduction_w"],
                "max_usd": hi["marginal_usd_per_100w"],
                "max_at_reduction_w": hi["reduction_w"]}

    reachable = [s for s in steps if not s["exceeds_measured_floor"]] or steps[:1]
    rng = sens.setdefault("marginal_range", {})
    rng["reachable"] = dict(span(reachable),
                            through_reduction_w=reachable[-1]["reduction_w"])
    rng["full_ladder"] = span(steps)


def _floor_at(median_kw, reduction_w=None):
    """A stub edit putting this household's measured floor at `median_kw`,
    with the rest of the artifact made SELF-CONSISTENT the way the corrected
    quiet_night_floor.sensitivity_per_100w() makes it (issue #173):
    measured_floor_w is the floor in whole watts, usd_per_100w_at_current_floor
    prices the ladder's SMALLEST rung -- the first 100 W off the floor as it
    stands -- at that rung's own marginal, every step's exceeds_measured_floor
    is recomputed as reduction_w > measured_floor_w, and both halves of
    marginal_range are rebuilt off the steps.

    THE VERSION THIS REPLACED WROTE THE DEFECT INTO THE FIXTURE (issue #173).
    It rounded `median_kw` onto the ladder, clamped the result into [smallest
    rung, largest rung] and wrote it to a `floor_w_used` field -- a floor
    LEVEL laid onto an axis that counts watts REMOVED, so a 1,030 W floor was
    "self-consistently" quoted the tenth 100 W (the rate for a household that
    has already stripped 900 W) instead of the first. The field is gone from
    the artifact and the mapping is gone from here; what a stub sets is the
    floor, and what the ladder answers with is always its first rung.

    `reduction_w` overrides which removal the artifact says it priced, with
    value_usd kept honest to that rung, which is how an artifact that prices a
    deeper removal than the first one is built. It is named for the axis the
    ladder is indexed on -- watts REMOVED -- and never for a resulting floor
    level, because naming it for the level is the whole of the defect."""
    def edit(doc):
        sens = doc["sensitivity_per_100w"]
        steps = sens["steps"]
        floor_w = int(round(median_kw * 1000))
        doc["night_floor"]["median_kw"] = median_kw
        for row in doc["night_floor"].get("daily_series") or []:
            if row.get("median_kw") is not None:
                row["median_kw"] = median_kw
        doc["pricing"]["floor_kw_priced"] = round(median_kw, 4)
        sens["measured_floor_w"] = floor_w
        for s in steps:
            s["exceeds_measured_floor"] = bool(s["reduction_w"] > floor_w)
        w = min(s["reduction_w"] for s in steps) if reduction_w is None else reduction_w
        at = sens["usd_per_100w_at_current_floor"]
        at["reduction_w"] = w
        marginal = next((s["marginal_usd_per_100w"] for s in steps
                         if s["reduction_w"] == w), None)
        if marginal is not None:
            at["value_usd"] = marginal
        _rebuild_marginal_range(sens)
    return edit


def _sweep_every_token():
    """({token: rendered}, {token: why it refused}) over every non-gap token.

    THE WHOLE SET, and BaseException, both deliberately. resolve_token signals
    refusal with SystemExit, and generate_report.resolve_tokens_with_gaps()
    folds any refusal into `failures` -- which stops the run. So "this token
    still renders" is not the claim that matters for a household whose floor
    sits outside the ladder; "this household still gets a report" is, and only
    a sweep of the whole set can make it."""
    rendered, refused = {}, {}
    for name, spec in rt.TOKENS.items():
        if spec.get("kind") == "gap":
            continue
        try:
            rendered[name] = rt.resolve_token(name)
        except BaseException as exc:            # noqa: BLE001 - SystemExit is the refusal
            refused[name] = f"{type(exc).__name__}: {exc}"
    return rendered, refused


def _resolve_every_token():
    """{token: rendered} for every non-gap token, or an AssertionError naming
    the ones that could not be resolved. Needs the private archive: without
    household.yaml the household-sourced tokens refuse for a reason that has
    nothing to do with the case calling this."""
    rendered, refused = _sweep_every_token()
    assert not refused, (
        f"{len(refused)} token(s) refused, so this household gets no report at all: "
        + "; ".join(f"{n} -- {why}" for n, why in sorted(refused.items())))
    return rendered


def _assert_no_new_refusals(baseline_refused, now_refused, what):
    """The archive-independent form of "this household still gets a report".

    A case that sweeps the whole token set and demands ZERO refusals can only
    run where private/household.yaml is staged, so it SKIPS in CI -- and a
    guard that skips where bad merges are caught is not a guard. The claim is
    made against a baseline swept in the same environment instead: whatever a
    household on the winning path can resolve here, a household this case has
    moved to second must resolve too. Where the archive IS staged the baseline
    is the complete set, so this is exactly "the full token set resolves";
    where it is not, the 30-odd household-sourced tokens are refused
    identically on both sides and every token this issue is about is still
    swept."""
    new = {n: why for n, why in now_refused.items() if n not in baseline_refused}
    assert not new, (
        f"{len(new)} token(s) refuse for {what} that resolve for the same household on "
        "the winning path, so it gets no report at all: "
        + "; ".join(f"{n} -- {why}" for n, why in sorted(new.items())))
    healed = sorted(set(baseline_refused) - set(now_refused))
    assert not healed, (
        f"{healed} refused on the winning path but resolved for {what}; the two sweeps "
        "are not comparable, so this case cannot say what it changed")


@case
def case_a_floor_smaller_than_the_ladders_first_rung_still_gets_a_whole_report():
    """ISSUE #173, and ISSUE #140, /review FINDING 1 BEFORE IT. The claim both
    make is the same one -- a household whose floor is an unusual size still
    gets a report -- and only the second one states it on the axis the ladder
    is actually indexed on.

    WHAT THIS CASE USED TO ASSERT, AND WHY IT CANNOT BE RESTORED. It fed the
    token a floor "outside the ladder" and demanded a sentence naming the
    ladder's top or bottom END, because the token then read a floor_w_used
    field, rounded the measured floor onto the rungs, clamped it into [first
    rung, last rung] and reported the clamped rung as the step "nearest the
    measured floor". Every one of those comparisons puts a floor LEVEL on an
    axis that counts watts REMOVED. They are both watts and neither is the
    other: a 1,030 W floor does not mean the household should be quoted the
    rung that removes 1,030 W (that rung is the tenth 100 W, priced for a
    household that already stripped 900), and a floor bigger than the last
    rung is not a reading at the ladder's top end -- it is a floor with more
    rungs available than were re-billed. So the wording that case pinned was
    the wrong figure wearing the right shape, and the clamp/nearness branches
    it tested are DELETED rather than adjusted. Restoring any of it under the
    banner of an older test would reintroduce the axis error itself.

    WHAT REPLACED IT is the same protection stated correctly. The rate at a
    floor is the ladder's FIRST rung whatever the floor measures, so no floor
    is outside the ladder any more and no floor is refused for its size. What
    an unusual floor changes is only how much of the ladder the metered load
    can supply, and that has exactly one honest edge: a floor smaller than the
    first rung, where even the first 100 W asks for more than the floor holds
    and _split_floor gives back only what was there. Three floors, and the
    sweep is over EVERY token rather than this one, because a refusal stops
    the whole run (generate_report.resolve_tokens_with_gaps folds it into
    `failures`):
      0.03 kW -- below the first rung: renders, and says the smallest step
                 asks for more than the floor holds;
      1.40 kW -- above the last rung: renders, and reports the first rung off
                 that floor with every re-billed rung reachable;
      1.03 kW -- this household: renders exactly what it renders today.
    Every watt figure in the assertions is read off the artifact's own ladder,
    so nothing here restates the generator's constants either."""
    _require_household()
    rungs = _ladder_rungs()
    lowest, highest = rungs[0], rungs[-1]
    live = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")

    # BELOW the first rung: the rate is real, the wording degrades, and the
    # report still ships.
    small_kw = (lowest - 70) / 1000.0
    small_w = lowest - 70
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _floor_at(small_kw))):
        small = _resolve_every_token()["NIGHT_FLOOR_SENSITIVITY_PER_100W"]
    for phrase in (f"the ladder's smallest step, {lowest:,.0f} W, which already asks for "
                   f"more than the {small_w:,.0f} W this floor holds",
                   "so the re-billing gave back only what was there",
                   "a rate at the ladder's smallest step"):
        assert phrase in small, (
            f"a {small_w:,.0f} W floor cannot supply the ladder's {lowest:,.0f} W first "
            f"rung, so the sentence must say so ({phrase!r}): {small}")
    assert f"the first {lowest:,.0f} W off the {small_w:,.0f} W floor as measured" not in small, (
        f"a rung the floor cannot fill was published as watts actually taken off it: {small}")

    # ABOVE the last rung: nothing degrades. The floor holds every rung that
    # was re-billed, so the rate is the first rung and the spread runs the
    # whole ladder -- a floor with more to give than the ladder priced, which
    # is not the same thing as a reading at the ladder's top end.
    big_kw = (highest + 200) / 1000.0
    big_w = highest + 200
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _floor_at(big_kw))):
        big = _resolve_every_token()["NIGHT_FLOOR_SENSITIVITY_PER_100W"]
    for phrase in (f"the first {lowest:,.0f} W off the {big_w:,.0f} W floor as measured",
                   "a rate at this floor",
                   f"across the first {highest:,.0f} W, as deep as a {big_w:,.0f} W "
                   "floor reaches"):
        assert phrase in big, (
            f"a {big_w:,.0f} W floor holds every re-billed rung, so the sentence must "
            f"price the first one off it ({phrase!r}): {big}")

    for where, text in (("below the first rung", small), ("above the last rung", big)):
        assert "nearest the measured floor" not in text, (
            f"the floor {where} was quoted a step 'nearest the measured floor' -- the "
            f"deleted comparison of a removal against a level: {text}")
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"the {where} rendering is {label}: {text}"

    # This household is untouched: same sentence as today.
    inside = rt._json("quiet_night_floor.json")["night_floor"]["median_kw"]
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _floor_at(inside))):
        same = _resolve_every_token()["NIGHT_FLOOR_SENSITIVITY_PER_100W"]
    assert same == live, f"the in-range rendering moved:\n  was {live!r}\n  now {same!r}"
    assert f"the first {lowest:,.0f} W off the" in same, same

    after = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return (f"a {small_w:,.0f} W floor below the ladder's {lowest:,.0f} W first rung and a "
            f"{big_w:,.0f} W floor above its {highest:,.0f} W last one both resolve the "
            f"ENTIRE token set ({len(rt.TOKENS)} entries); the small one says the first "
            "rung asks for more than it holds, the large one prices that first rung with "
            f"every rung reachable, and this household still renders {live[:38]!r}...")


@case
def case_the_sensitivity_ladder_refuses_a_shape_it_cannot_be_read_off():
    """ISSUE #140, /review FINDING 4. Two degenerate ladders reached prose
    through arithmetic instead of through a refusal.

    An EMPTY steps list raised ValueError out of min()/max(), which
    resolve_token's catch-all reports as a generic "failed to resolve token
    ... ValueError" -- not the named refusal every other guard in this module
    produces, and not something a maintainer can route.

    A DUPLICATE reduction_w was worse, because it did not fail at all: the
    marginals are collected into a dict keyed f"step_{reduction_w}_w", so two
    rungs at one reduction collapse to one key and the LAST one wins. The
    published spread narrows silently -- and the spread is the whole point of
    that clause, which exists to stop a reader multiplying one rate by any
    amount removed.

    The duplicate here is built to narrow the spread measurably: the rung
    carrying the ladder's HIGHEST marginal is relabelled onto its neighbour,
    so the collapse would drop the top of the published range."""
    _require_household()
    live = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")

    def empty(doc):
        doc["sensitivity_per_100w"]["steps"] = []

    def collide(doc):
        steps = doc["sensitivity_per_100w"]["steps"]
        top = max(steps, key=lambda s: s["marginal_usd_per_100w"])
        neighbour = min((s for s in steps if s is not top),
                        key=lambda s: abs(s["reduction_w"] - top["reduction_w"]))
        top["reduction_w"] = neighbour["reduction_w"]

    steps = rt._json("quiet_night_floor.json")["sensitivity_per_100w"]["steps"]
    widest = max(s["marginal_usd_per_100w"] for s in steps)
    assert f"${widest:,.0f}" in live, (
        f"the live sentence does not publish the ladder's highest marginal (${widest:,.0f}), "
        f"so collapsing it would not be observable and this case cannot test it: {live}")

    refusals = {}
    for label, edit, must_say in (
            ("empty", empty, "steps is empty"),
            ("duplicate", collide, "repeats reduction_w")):
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", edit)):
            try:
                text = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
            except SystemExit as exc:
                refusals[label] = str(exc)
            else:
                raise AssertionError(
                    f"a {label} sensitivity ladder was published anyway: {text}")
        assert "NIGHT_FLOOR_SENSITIVITY_PER_100W" in refusals[label], refusals[label]
        assert must_say in refusals[label], (
            f"the {label} refusal does not say what is wrong ({must_say!r}): "
            f"{refusals[label]}")
        for generic in ("ValueError", "IndexError", "KeyError", "Traceback"):
            assert generic not in refusals[label], (
                f"the {label} ladder reached a generic {generic} instead of this module's "
                f"own named refusal: {refusals[label]}")

    after = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return ("an empty sensitivity ladder and two rungs sharing one reduction_w are both "
            "refused by name rather than raising a bare ValueError or silently narrowing "
            f"the published ${widest:,.0f} top of the spread")


@case
def case_the_sensitivity_ladder_refuses_an_artifact_that_contradicts_its_own_rungs():
    """ISSUE #173. The four guards that replaced the clamp/nearness pair, each
    provoked by the artifact state it exists to catch. All four stay on ONE
    axis -- reduction_w, watts REMOVED from the floor -- which is the whole
    correction: the guard they replaced compared a removal against a level,
    made the wrong rung the passing one, and refused the corrected artifact.

    The five edits below, and what each would publish unguarded:
      * value_usd read off a DIFFERENT rung than the reduction_w beside it --
        the sentence would quote a second copy of the ladder instead of the
        ladder, which is how a stale hand-edit survives a regeneration;
      * a reduction_w that is not the ladder's smallest rung -- the defect
        itself, in its own words: a deeper rung is the rate for a household
        that has ALREADY stripped the earlier ones, so publishing it as "the
        rate at the current floor" prices watts that are not there yet;
      * an exceeds_measured_floor flag that disagrees with reduction_w >
        measured_floor_w -- the flags decide which rungs may be quoted as a
        spread, so a wrong one silently moves an end of the published range;
      * a marginal_range half that does not match a recomputation from steps
        -- reachable and full_ladder both, since the token prints one of them
        and the choice between them depends on the other.

    Written the way this module's other refusal cases are: the message has to
    NAME what is wrong, and a generic ValueError/KeyError is a failure even
    when it stops the run, because a maintainer cannot route it."""
    _require_household()
    live = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    sens = rt._json("quiet_night_floor.json")["sensitivity_per_100w"]
    rungs = _ladder_rungs()
    lowest, deepest = rungs[0], rungs[-1]
    inside = rt._json("quiet_night_floor.json")["night_floor"]["median_kw"]
    by_rung = {s["reduction_w"]: s["marginal_usd_per_100w"] for s in sens["steps"]}
    assert by_rung[deepest] != by_rung[lowest], (
        f"the ladder's first ({lowest} W) and last ({deepest} W) rungs carry the same "
        f"marginal (${by_rung[lowest]:,.2f}), so neither the mispriced-rate edit nor the "
        "deeper-rung edit below would change anything and this case cannot test them")

    def value_off_another_rung(doc):
        at = doc["sensitivity_per_100w"]["usd_per_100w_at_current_floor"]
        at["value_usd"] = by_rung[deepest]

    def flag_disagrees_with_the_floor(doc):
        first = min(doc["sensitivity_per_100w"]["steps"],
                    key=lambda s: s["reduction_w"])
        first["exceeds_measured_floor"] = not first["exceeds_measured_floor"]

    def reachable_span_narrowed(doc):
        rch = doc["sensitivity_per_100w"]["marginal_range"]["reachable"]
        rch["min_usd"] = rch["max_usd"]

    def reachable_reach_overstated(doc):
        doc["sensitivity_per_100w"]["marginal_range"]["reachable"][
            "through_reduction_w"] = deepest

    def full_span_narrowed(doc):
        full = doc["sensitivity_per_100w"]["marginal_range"]["full_ladder"]
        full["min_usd"] = full["max_usd"]

    refusals = {}
    for label, edit, must_say in (
            ("a rate that is not the rung's own marginal", value_off_another_rung,
             "the sentence must quote the ladder, not a second copy of it"),
            ("a rate priced at a deeper removal than the first",
             _floor_at(inside, reduction_w=deepest),
             f"the ladder's smallest rung is {lowest:,.0f} W"),
            ("a flag that disagrees with the floor it names", flag_disagrees_with_the_floor,
             "the steps flag"),
            ("a reachable spread narrower than its own rungs", reachable_span_narrowed,
             "marginal_range.reachable publishes"),
            ("a reachable spread reaching past the floor", reachable_reach_overstated,
             "marginal_range.reachable publishes"),
            ("a full-ladder spread narrower than its own rungs", full_span_narrowed,
             "marginal_range.full_ladder publishes")):
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", edit)):
            try:
                text = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
            except SystemExit as exc:
                refusals[label] = str(exc)
            else:
                raise AssertionError(f"{label} was published anyway: {text}")
        assert "NIGHT_FLOOR_SENSITIVITY_PER_100W" in refusals[label], refusals[label]
        assert must_say in refusals[label], (
            f"the refusal for {label} does not say what is wrong ({must_say!r}): "
            f"{refusals[label]}")
        for generic in ("ValueError", "IndexError", "KeyError", "TypeError", "Traceback"):
            assert generic not in refusals[label], (
                f"{label} reached a generic {generic} instead of this module's own named "
                f"refusal: {refusals[label]}")

    after = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return (f"{len(refusals)} artifact states that contradict the ladder they are read "
            f"off -- a rate off another rung, a rate priced at the {deepest:,.0f} W "
            f"removal instead of the {lowest:,.0f} W one, a flipped "
            "exceeds_measured_floor flag, and three wrong marginal_range fields -- are "
            "each refused by name rather than published")


@case
def case_the_sensitivity_spread_carries_the_window_at_its_own_endpoints():
    """ISSUE #140, /review FINDING 5. On a partial corpus this sentence
    published the rate with its window attached -- "about $323 across the 200
    nights measured, spanning less than a full year" -- and then published the
    two ends of the ladder's spread BARE, with no window on them at all.

    Both endpoints are sums over exactly the same window as the rate, and the
    docstring justified leaving them bare by saying the window is stated
    "immediately before them". It is not: on that render the window clause
    sits about forty words and a complete clause earlier, with the step and
    the multiplier caveat in between. Nor can _ANNUAL_CLAIM catch it -- those
    figures carry no "/yr" -- so this is the defect the structural guard
    exists for, at the one exit its regex cannot see.

    THE PAIR THIS CHECKS IS THE REACHABLE ONE (issue #173). The sentence
    publishes the spread across the rungs at or below the measured floor,
    because the rungs above it are clamped by _split_floor and their diluted
    marginals would blame the tariff for the model's own truncation. So the
    endpoints are read off the steps this floor can reach, not off every step
    in the ladder.

    Asserted STRUCTURALLY, not by matching the whole sentence: the window has
    to appear within a short distance AFTER the endpoint pair, which is what
    "qualified where they appear" means and what a re-word that drops the
    qualifier again would fail."""
    _require_household()
    live = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    nights = 200

    def short_corpus(doc):
        first = dt.date(2025, 7, 24)
        doc["night_floor"]["daily_series"] = [
            {"date": (first + dt.timedelta(days=i)).isoformat(),
             "median_kw": doc["night_floor"]["median_kw"],
             "excluded_high_demand": False}
            for i in range(nights)]
        doc["night_floor"]["nights_total"] = nights
        doc["night_floor"]["quiet_nights"] = nights

    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", short_corpus)):
        text = _renders("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert not rt._ANNUAL_CLAIM.search(text), text
    steps = rt._json("quiet_night_floor.json")["sensitivity_per_100w"]["steps"]
    marginals = [s["marginal_usd_per_100w"] for s in steps
                 if not s["exceeds_measured_floor"]]
    pair = f"${min(marginals):,.0f} to ${max(marginals):,.0f}"
    at = text.find(pair)
    assert at >= 0, f"the sentence no longer publishes the ladder's two ends ({pair}): {text}"
    tail = text[at + len(pair):]
    m = re.search(rf"those same {nights:,.0f} nights", tail)
    assert m, (
        f"the ladder's endpoints ({pair}) are published with no window attached to them, "
        f"on a corpus of {nights} nights that is not a year: {text}")
    assert m.start() <= 40, (
        f"the window sits {m.start()} characters after the endpoints it qualifies, far "
        f"enough for a reader to take {pair} as a per-year rate: {text}")
    after = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert after == live, f"the stub leaked: {after!r} != {live!r}"
    return (f"on a {nights}-night corpus the ladder's two ends ({pair}) carry the window "
            f"{m.start()} characters later, in the clause that publishes them")


@case
def case_the_published_scope_claim_tracks_the_statement_it_compresses():
    """ISSUE #140, /review FINDING 3. PHANTOM_METHOD_DISCREPANCY required
    pricing.reconciliation.scope_of_agreement to be present and non-blank, and
    then published its OWN hardcoded scope sentence. Nothing compared the two.

    So DELETION was guarded and DRIFT was not -- and drift is the likelier
    failure. If the two methods stop drawing their rates from the same module,
    or stop starting from the identical allocation, or the agreement stops
    being limited to the netting treatment, the presence check still passes
    and the report keeps publishing a scope claim the artifact no longer
    makes. The docstring's promise that it "refuses to render an agreement
    claim at all if that statement is ever dropped" was true of deletion and
    read as covering both.

    Each published clause is now pinned to the term in the artifact's own
    statement that carries it, and each is knocked out in turn -- a check that
    only ever fires on all three at once is one check wearing three names."""
    _require_household()
    live = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    scope = (rt._json("quiet_night_floor.json")["pricing"]["reconciliation"]
             ["scope_of_agreement"])
    assert rt._SCOPE_CLAUSES, "the clause table is empty, so this case checks nothing"

    drifted = {}
    for clause, term in rt._SCOPE_CLAUSES:
        assert clause in live, (
            f"the clause table names {clause!r}, which the published sentence does not "
            f"contain -- the table has drifted from the prose it guards: {live}")
        assert term.lower() in scope.lower(), (
            f"the committed artifact's scope statement no longer contains {term!r}, so "
            "this case is asserting against a statement that has already drifted")

        def lose(doc, term=term):
            rec = doc["pricing"]["reconciliation"]
            rec["scope_of_agreement"] = re.sub(
                re.escape(term), "[the same thing, said another way]",
                rec["scope_of_agreement"], flags=re.I)

        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", lose)):
            try:
                text = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
            except SystemExit as exc:
                drifted[term] = str(exc)
            else:
                raise AssertionError(
                    f"the artifact's scope statement stopped saying {term!r} and the "
                    f"report published the clause resting on it anyway: {text}")
        assert "PHANTOM_METHOD_DISCREPANCY" in drifted[term], drifted[term]
        assert clause in drifted[term], (
            f"the refusal must name the clause that lost its support ({clause!r}): "
            f"{drifted[term]}")
        assert "scope_of_agreement" in drifted[term], drifted[term]

    after = rt.resolve_token("PHANTOM_METHOD_DISCREPANCY")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return (f"each of the {len(rt._SCOPE_CLAUSES)} published scope clauses refuses by name "
            f"when the artifact's own statement stops carrying the term it rests on "
            f"({', '.join(sorted(drifted))})")


@case
def case_the_glossary_states_the_floor_figures_these_tokens_publish():
    """ISSUE #140, /review FINDING 2. GLOSSARY.md is linked BY NAME from
    index.html's reader's-guide block, so it is part of the published report
    for a reader who follows the link -- and its phantom-load entry still
    carried a floor cost from deep_results.json:phantom (rounded to
    "~$1,800/yr gross"), plus a "only part of that is realistically
    recoverable" framing nothing in this archive meters. CLAUDE.md section 3
    requires a changed figure to be replaced in EVERY instance.

    Checked as a sweep rather than as a literal: every dollar figure in the
    entry must be one of the two the live artifact prices this load at, so a
    stale figure of any shape fails here rather than only the one that was
    found."""
    entry = [line for line in (rt.ROOT / "GLOSSARY.md").read_text().splitlines()
             if line.startswith("**Phantom load")]
    assert len(entry) == 1, f"expected exactly one phantom-load glossary entry, got {entry}"
    entry = entry[0]
    doc = rt._json("quiet_night_floor.json")
    nf, pr = doc["night_floor"], doc["pricing"]
    live = {f"${pr['method_a_price_map']['total_usd']:,.0f}",
            f"${pr['method_b_rebill']['total_usd']:,.0f}"}
    for value in live | {f"{nf['median_kw']:,.2f} kW"}:
        assert value in entry, (
            f"the glossary's phantom-load entry does not state {value!r}, which is what "
            f"data/quiet_night_floor.json prices this load at: {entry}")
    published = set(re.findall(r"\$[\d,]+", entry))
    stale = published - live
    assert not stale, (
        f"the glossary's phantom-load entry states {sorted(stale)}, which is not one of "
        f"the live pricings {sorted(live)}: {entry}")
    text = (rt.ROOT / "GLOSSARY.md").read_text()
    assert "realistically recoverable" not in text, (
        "GLOSSARY.md still says part of the floor is 'realistically recoverable' -- "
        "nothing in this archive meters which appliance behind the floor could be "
        "switched off (CLAUDE.md section 0)")
    return (f"the glossary's phantom-load entry states the live floor ({nf['median_kw']:,.2f} "
            f"kW) and both live pricings {sorted(live)}, carries no other dollar figure, "
            "and no longer claims a recoverable share")


@case
def case_section_0_states_the_exclusion_rate_beside_the_floor():
    """ISSUE #140, /review FINDING 6. Section 0 published the floor and its
    annual cost off a bare night count ("43 quiet nights"), which reads the
    way the retired "44 EV-free nights" did. The artifact asks for the other
    half in as many words -- night_floor.selection_caveat says to "report the
    exclusion rate alongside the floor figure rather than treating the kept
    ~11.8% as the whole story" -- and NIGHT_FLOOR_SAMPLE already computes it,
    so section 13 states it and section 0 did not.

    Both halves of the token's own value are asserted, and the section 0
    density cap with them: the exclusion rate goes in as its own short
    sentence rather than as more clauses on a lead that would then blow the
    35-word / one-aside cap CLAUDE.md section 10 puts on the basic tier."""
    _require_household()
    html = (rt.ROOT / "index.html").read_text()
    m = re.search(r"<li><b>Always-on baseload.*?</li>", html, re.S)
    assert m, "index.html has no section 0 always-on-baseload item"
    item = m.group(0)
    nf = rt._json("quiet_night_floor.json")["night_floor"]
    sample = rt.resolve_token("NIGHT_FLOOR_SAMPLE")
    count = f"{nf['quiet_nights']:,.0f} of {nf['nights_total']:,.0f} nights"
    excluded = f"{(nf['nights_total'] - nf['quiet_nights']) / nf['nights_total'] * 100:.1f}%"
    assert count in sample and excluded in sample, (
        f"NIGHT_FLOOR_SAMPLE no longer carries the count and the exclusion rate this case "
        f"requires section 0 to publish: {sample!r}")
    assert count in item, (
        f"section 0 does not state the sample the floor is measured on ({count!r}): {item}")
    assert excluded in item, (
        f"section 0 publishes the floor and its cost without the exclusion rate "
        f"({excluded!r}) the artifact's own selection_caveat asks for beside them: {item}")

    # The density cap, on the sentences this item leads with.
    plain = re.sub(r"<[^>]+>", "", item)
    lead, rest, sentences = None, plain, []
    while rest.strip() and len(sentences) < 3:
        end = re.search(r"\.(?=\s|\Z)", rest)
        sentence = rest[:end.end()] if end else rest
        sentences.append(sentence.strip())
        if not end:
            break
        rest = rest[end.end():]
    over = [(s, len(s.split()), s.count("(") + s.count("—"))
            for s in sentences if len(s.split()) > 35 or s.count("(") + s.count("—") > 1]
    assert not over, (
        "section 0's always-on item leads with a sentence past the basic-tier density cap "
        f"(35 words, 1 aside): {over}")
    lead = sentences[0]
    return (f"section 0 states {count!r} AND the {excluded} exclusion rate "
            f"NIGHT_FLOOR_SAMPLE computes, and its first {len(sentences)} sentences stay "
            f"inside the density cap (lead: {len(lead.split())} words, "
            f"{lead.count('(') + lead.count(chr(8212))} asides)")


# ---------------------------------------------------------------------------
# THE CHECK BEHIND report_tokens.is_attribute_only.
#
# The flag is a promise a token makes about ITSELF -- "my value lands in an
# HTML attribute, not in a sentence" -- and generate_report.build_scope_values
# acts on it by keeping the token out of every prose block's LLM scope. A
# promise nothing verifies is the whole objection to declaring this rather
# than deriving it, so it is verified here, from report-template.html, BOTH
# ways: a flagged token that turns up in running text fails, and so does an
# unflagged one that only ever appears inside a tag.
#
# WHAT THE DETECTOR BELOW RECOGNISES, exactly:
#   masked   inside <!-- --> or inside a <script>/<style> BODY. Neither is
#            markup text and neither is an attribute; a token there (the five
#            CHART_TITLE_* and the three *_IMPORT_SHARE_PCT ones live in the
#            chart config) is out of this case's jurisdiction and must not be
#            flagged, since it is prose the reader sees on a chart.
#   attribute inside a <tag ...> in the unmasked remainder.
#   text     everywhere else in the unmasked remainder.
# Masking the script body FIRST is the load-bearing step. The chart config
# contains `c.p1DataIndex<=D.spBreak.summer`, and to a scan that just looks
# for "< ... >" that `<` opens a tag which stays open for hundreds of
# characters -- long enough to swallow {{CHART_TITLE_SPREAD}} and report it
# as an attribute value. That false positive is not hypothetical: it is what
# the naive version of this detector says about this template today.
#
# WHAT IT DOES NOT RECOGNISE, and how it says so instead of guessing: a
# document whose angle brackets do not all belong to a comment, a script or
# style body, or a tag it matched. Every unmasked `<` and `>` must be part of
# a tag this case's own regex matched; a literal `>` in body text, an
# unbalanced `<` in an attribute value, or a masking regex outrun by a new
# construct leaves residue, and the residue assertion fails FIRST, by
# position and context. So the detector never silently reclassifies a token
# on a template it has stopped understanding.
# ---------------------------------------------------------------------------
_MASKED_RE = re.compile(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_TAG_RE = re.compile(r"<[a-zA-Z/!][^<>]*>")


def _template_token_positions():
    """{token_name: {"attribute" | "text", ...}} for every LIVE occurrence in
    report-template.html, plus the stray-angle-bracket residue list."""
    text = rt.TEMPLATE.read_text()
    masked = [m.span() for m in _MASKED_RE.finditer(text)]

    # Blank the masked spans (newlines kept so offsets and line numbers in a
    # failure message still line up with the file) so the tag scan below
    # cannot see a comparison operator inside JS as the start of a tag.
    buf = list(text)
    for start, end in masked:
        for i in range(start, end):
            if buf[i] != "\n":
                buf[i] = " "
    scan = "".join(buf)

    tags = [m.span() for m in _TAG_RE.finditer(scan)]
    covered = bytearray(len(scan))
    for start, end in tags:
        covered[start:end] = b"\x01" * (end - start)
    residue = [(i, scan[max(0, i - 60):i + 60])
               for i, ch in enumerate(scan) if ch in "<>" and not covered[i]]

    def in_span(spans, pos):
        return any(s <= pos < e for s, e in spans)

    positions = {}
    for m in rt._TOKEN_RE.finditer(text):
        name = m.group(1)
        if not name or in_span(masked, m.start()):
            continue
        where = "attribute" if in_span(tags, m.start()) else "text"
        positions.setdefault(name, set()).add(where)
    return positions, residue


@case
def case_attribute_only_flags_match_where_the_template_puts_each_token():
    """report_tokens' attribute_only flag agrees with report-template.html,
    in both directions, so the flag cannot drift from the markup it
    describes. Needs nothing but the template and TOKENS -- no archive.

    A flagged token is one generate_report.build_scope_values keeps out of
    every prose block's LLM scope, so a wrong flag is not cosmetic: flag a
    token that appears in a sentence and the model loses a value it is
    supposed to cite; leave one unflagged and a CSS class name is offered as
    citable prose ("the matrix rates this plan win.")."""
    positions, residue = _template_token_positions()
    assert not residue, (
        "this case's detector no longer understands report-template.html: "
        f"{len(residue)} angle bracket(s) outside every comment, <script>/<style> body "
        f"and matched tag, first at offset {residue[0][0]} in {residue[0][1]!r}. Fix the "
        "masking before trusting any attribute/text verdict it gives")

    derived = {n for n, where in positions.items() if where == {"attribute"}}
    declared = {n for n in rt.TOKENS if rt.is_attribute_only(n)}
    assert declared == derived, (
        f"attribute_only is declared for {sorted(declared)} but report-template.html puts "
        f"{sorted(derived)} inside a tag and nowhere else. Declared-not-derived "
        f"{sorted(declared - derived)} would be silently withheld from prose that can see "
        f"it in the page; derived-not-declared {sorted(derived - declared)} is a markup "
        "value being offered to an LLM as a sentence it may write")
    mixed = {n for n, where in positions.items() if len(where) > 1}
    assert not mixed, (
        f"{sorted(mixed)} appear BOTH inside a tag and in running text, so one flag cannot "
        "describe them; split the token before flagging either use")
    return (f"attribute_only is declared for exactly the {len(derived)} token(s) "
            f"report-template.html puts only inside a tag ({sorted(derived)}), over "
            f"{len(positions)} live tokens outside its comments, <script> and <style>")


# ---------------------------------------------------------------------------
# THE SOILING ARTIFACT'S SECOND SHAPE (ISSUES #167, #170, #171, #137, #168).
#
# analysis/soiling_analysis.py writes sanity_check_2024_cleaning and
# annual_economics.scenario_B_2024_cleaning_evidence in TWO shapes: the
# numeric one this household gets, and a `status` string for any household
# whose cleaning_history does not contain the dated event the gain was
# measured on. The second shape is the ORDINARY outcome of the reproduction
# path README documents, not a broken artifact -- so the whole token set has
# to survive it, and the cases below sweep rather than spot-check, because a
# case that resolves one token cannot tell you the report still generates.
# ---------------------------------------------------------------------------
_STATUS_SANITY_CHECK = (
    "not determined — cleaning_history has no entry for 2024-08-12, the only event "
    "with a measured diff-in-diff effect; other cleanings have no measured gain to "
    "sanity-check against")
_STATUS_SCENARIO_B = (
    "not determined — requires the measured 2024-08-12 cleaning event (see "
    "sanity_check_2024_cleaning)")


def _status_shaped(doc):
    """soiling_results.json as soiling_analysis.py writes it for a household
    with no measured cleaning: a status string in place of BOTH numeric
    blocks."""
    doc["sanity_check_2024_cleaning"] = {"status": _STATUS_SANITY_CHECK}
    doc["annual_economics"]["scenario_B_2024_cleaning_evidence"] = \
        {"status": _STATUS_SCENARIO_B}


def _resolution_failures():
    """{token: what went wrong} for every non-gap token that will not render.

    Catches BaseException, not SystemExit or AssertionError: the whole point
    is that NOTHING escapes, and a token family that started raising KeyError
    past resolve_token's own conversion would satisfy a narrower check while
    still taking the report down -- which is exactly the defect #167 and #170
    name."""
    out = {}
    for name, spec in rt.TOKENS.items():
        if spec.get("kind") == "gap":
            continue
        try:
            rt.resolve_token(name, spec)
        except BaseException as e:                # noqa: BLE001 - that is the assertion
            out[name] = f"{type(e).__name__}: {e}"
    return out


class _example_household:
    """household.example.yaml standing in for private/household.yaml, with one
    cleaning_history written into it -- so a case about the STUDY CSV needs no
    private archive. Nothing is written to disk; report_tokens' view of the
    household is restored on the way out, including when the body raises."""

    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        import yaml
        node = yaml.safe_load((rt.ROOT / "household.example.yaml").read_text())
        node["cleaning_history"] = [dict(e) for e in self.entries]
        self.old = (rt.hh._cache, rt.hh.PATH)
        rt.hh._cache, rt.hh.PATH = node, rt.ROOT / "household.example.yaml"
        return node

    def __exit__(self, *exc):
        rt.hh._cache, rt.hh.PATH = self.old
        return False


@case
def case_a_status_shaped_soiling_artifact_leaves_every_token_resolving():
    """ISSUES #167 AND #170, which are one defect at two exits.

    With a status-shaped soiling_results.json, exactly two tokens used to hard-
    fail: SEC12_TEASER subscripted known_cleaning_gain_pct and
    SOILING_RATE_RANGE subscripted scenario B's rate_pct_per_month. Either one
    alone takes resolve_all() down, so a household without the measured
    cleaning got NO REPORT AT ALL -- which is the outcome issue #138's
    state-aware CLEANING_EFFECT_PCT was written to prevent, defeated by the
    two tokens beside it.

    Swept, not spot-checked (issue #170's own lesson): the assertion is that
    the set of tokens that fail is UNCHANGED by the artifact's shape, so a
    third token growing the same assumption fails this case."""
    _require_household()
    baseline = _resolution_failures()
    with _patched(rt, "_json", _stub_for("soiling_results.json", _status_shaped)):
        variant = _resolution_failures()
        teaser = _renders("SEC12_TEASER")
        bracket = _renders("SOILING_RATE_RANGE")
        claim = _renders("CLEANING_EFFECT_CLAIM")
    assert set(variant) == set(baseline), (
        "a status-shaped soiling_results.json changed which tokens resolve at all: "
        + "; ".join(f"{t}: {variant.get(t, 'now resolves')}"
                    for t in sorted(set(variant) ^ set(baseline))))
    for name, text in (("SEC12_TEASER", teaser), ("SOILING_RATE_RANGE", bracket),
                       ("CLEANING_EFFECT_CLAIM", claim)):
        assert rt._NOT_DETERMINED_VERDICT in text.lower(), (
            f"{name} rendered {text!r} against an artifact that states no measured "
            "cleaning gain -- it must say so, not state a figure")
        assert "11.8" not in text, (
            f"{name} rendered {text!r}, carrying this household's measured gain into a "
            "household the artifact says it was not measured for")
    assert _STATUS_SANITY_CHECK.split("— ", 1)[1][:40] in teaser, (
        f"SEC12_TEASER rendered {teaser!r} without soiling_analysis.py's own reason -- "
        "the generator owns why it measured nothing")
    assert "0.45" in bracket, (
        f"SOILING_RATE_RANGE rendered {bracket!r}, dropping scenario A's rate, which the "
        "same artifact still states")
    return (f"a status-shaped soiling_results.json leaves all "
            f"{len(rt.TOKENS) - len(variant)} resolvable tokens resolving; the teaser "
            f"renders {teaser[:48]!r}... and the bracket {bracket[:48]!r}...")


@case
def case_the_cleaning_heading_pill_follows_the_determination_state():
    """ISSUE #168: §12's h3 carried a fixed green `measured` pill, so a
    household whose gain comes back "not determined" was handed a stamp saying
    the absent value was measured -- CLAUDE.md §9's "precision must match
    evidence density", broken by markup rather than by a number.

    Renders the template's actual h3 line in BOTH states and checks the whole
    line, not the tokens in isolation: the defect was in how the fixed wording
    and the varying value fit together, which only the assembled heading
    shows."""
    _require_household()
    line, = [ln for ln in (rt.TEMPLATE.read_text()).splitlines()
             if "{{CLEANING_EFFECT_CLAIM}}" in ln]
    names = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", line)))

    def heading():
        return re.sub(r"\{\{([A-Z0-9_]+)\}\}",
                      lambda m: rt.resolve_token(m.group(1)), line)

    def pill(text):
        cls, label = re.search(r'<span class="pill ([a-z]+)">([^<]*)</span>',
                               text).groups()
        return cls, label

    determined = heading()
    # BOTH ways the gain can be undetermined, since the pill has to follow the
    # state and not one route to it: the artifact's own status shape, and a
    # cleaning_history that simply records a different event.
    headings = {}
    with _patched(rt, "_json", _stub_for("soiling_results.json", _status_shaped)):
        headings["the artifact's own not-determined status"] = heading()
    with _example_household([{"date": "2023-01-01", "cost_usd": 150}]):
        headings["a history without the measured cleaning"] = heading()

    assert pill(determined) == ("g", "measured · single event"), (
        f"the determined heading lost its measured pill: {determined}")
    assert "difference-in-differences" in determined, (
        f"the determined heading no longer names the method: {determined}")
    for why, text in headings.items():
        cls, label = pill(text)
        assert (cls, label) == ("r", "not determined"), (
            f"with {why}, an undetermined cleaning gain is labelled "
            f"{label!r} in a {cls!r} pill: {text}")
        assert "difference-in-differences" not in text, (
            f"with {why}, the heading still describes the absent value as a "
            f"difference-in-differences estimate: {text}")
        assert "production gain not determined —" in text, (
            f"with {why}, the heading does not read as a sentence: {text}")
        assert "11.8" not in text, text

    # Every class the pill can reach is one report-template.html's own <style>
    # block paints -- the guard an attribute value needs, the same one
    # S3_ROW_CLASS carries. An unstyled pill publishes as unmarked text.
    style = rt.TEMPLATE.read_text()
    for text in (determined, *headings.values()):
        cls, _label = pill(text)
        assert re.search(rf"\.pill\.{cls}\b", style), (
            f"the pill class {cls!r} is not painted by report-template.html's <style>")
    return (f"§12's h3 ({len(names)} tokens) renders 'measured · single event' with the "
            f"gain and a red 'not determined' pill without it, across {len(headings)} "
            "routes to the undetermined state, claiming no difference-in-differences "
            "method in either")


@case
def case_a_degenerate_cleaning_window_is_a_named_refusal_not_a_bare_error():
    """ISSUE #171: the named zero/nan refusal issue #131 added to the cleaning
    windows went away with the quotient it guarded, and CLEANED_RATIO divided
    unguarded -- a bare ZeroDivisionError instead of a refusal naming the
    artifact and the window.

    All THREE consumers of _cleaning_window_medians are checked, not just the
    one that divides: the two that publish a median render it under a
    `measured` pill, where a nan is a nonsense figure with an evidence stamp
    on it. Needs no private archive -- household.example.yaml supplies the
    cleaning date and the study CSV is synthetic."""
    consumers = ("CLEANED_PRE_MEDIAN", "CLEANED_POST_MEDIAN", "CLEANED_RATIO")
    clean = dt.date(2023, 1, 1)                      # household.example.yaml's own

    def study(pre_kwh, post_kwh):
        rows = []
        for k in range(1, 31):
            for day, kwh in ((clean - dt.timedelta(days=k), pre_kwh),
                             (clean + dt.timedelta(days=k), post_kwh)):
                rows.append({"date": day.strftime("%Y%m%d"), "generated_kwh": str(kwh)})
        return lambda name: rows if name == "cleaning_study_daily.csv" else real(name)

    real = rt._csv_rows
    with _example_household([{"date": clean.isoformat(), "cost_usd": 150}]):
        # A positive control first: the same synthetic window with a real
        # production level resolves, so a refusal below is the DEGENERACY and
        # not the fixture.
        with _patched(rt, "_csv_rows", study(50.0, 55.0)):
            healthy = {t: rt.resolve_token(t) for t in consumers}
        refusals = {}
        for label, (pre_kwh, post_kwh) in (("a zero pre-window median", (0.0, 55.0)),
                                           ("a nan pre-window median", ("nan", 55.0)),
                                           ("a nan post-window median", (50.0, "nan"))):
            with _patched(rt, "_csv_rows", study(pre_kwh, post_kwh)):
                for token in consumers:
                    try:
                        rendered = rt.resolve_token(token)
                    except SystemExit as e:
                        refusals[(label, token)] = str(e)
                        continue
                    raise AssertionError(
                        f"{token} rendered {rendered!r} on {label} -- a production level "
                        "a gain cannot be measured against must be refused by name")

    assert healthy["CLEANED_RATIO"] == "1.10", healthy
    for (label, token), message in refusals.items():
        assert token in message, f"{label}: the refusal does not name {token}: {message}"
        assert "cleaning_study_daily.csv" in message, (
            f"{label}: the refusal does not name the artifact: {message}")
        assert "30-day windows" in message and str(clean) in message, (
            f"{label}: the refusal does not name the window: {message}")
    return (f"{len(refusals)} named refusals across {len(consumers)} consumers of the "
            "cleaning windows (zero and non-finite, either side), each naming the token, "
            f"data/cleaning_study_daily.csv and the window; the healthy control renders "
            f"{healthy['CLEANED_RATIO']}")


@case
def case_the_soiling_bracket_matches_the_published_report():
    """ISSUE #137: SOILING_RATE_RANGE rendered "0.4–2.4%/month" while index.html
    and TECHNICAL.md both state the same bracket as 0.45 -- one decimal
    published a LOWER soiling rate than data/soiling_results.json's scenario-A
    0.449%/month measures. Both ends are pinned here against the artifact and
    against the published paragraph, so the two cannot drift apart again."""
    _require_household()
    econ = rt._json("soiling_results.json")["annual_economics"]
    lo = econ["scenario_A_this_years_evidence"]["rate_pct_per_month"]
    hi = econ["scenario_B_2024_cleaning_evidence"]["rate_pct_per_month"]
    rendered = rt.resolve_token("SOILING_RATE_RANGE")
    for value in (lo, hi):
        assert float(f"{value:.2f}") == round(value, 2), value
    expected = f"{rt._trimmed(min(lo, hi))}–{rt._trimmed(max(lo, hi))}%/month"
    assert rendered == expected, (
        f"SOILING_RATE_RANGE rendered {rendered!r} against the artifact's own "
        f"{expected!r} (scenario A {lo}, scenario B {hi})")
    published = (rt.ROOT / "index.html").read_text()
    # The paragraph opens on the bracket sentence the template emits from this
    # token (issue #217); the study it rests on follows inside the paragraph.
    para = re.search(r"<p>The defensible soiling rate here is the full bracket.*?</p>",
                     published, re.S)
    assert para, "§12's soiling-range paragraph not found in index.html"
    para = para.group(0)
    assert f"~{expected}" in para, (
        f"§12 does not state the soiling bracket as ~{expected}; the token and the "
        f"published report must agree (CLAUDE.md §9, artifact-prose diff): {para[-320:]}")
    assert f"~{min(lo, hi):.1f}–" not in para, (
        f"§12 still carries the one-decimal low end {min(lo, hi):.1f}, which understates "
        f"the artifact's {min(lo, hi)}%/month")
    return (f"SOILING_RATE_RANGE = {rendered}, matching soiling_results.json's scenario "
            f"rates ({lo} / {hi}) and index.html's published bracket")


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
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except SystemExit as exc:
            # THE ONE FAILURE THIS RUNNER USED TO SWALLOW THE NAME OF.
            # report_tokens fails closed with SystemExit for every refusal --
            # a missing private/household.yaml, a token it cannot source, a
            # non-finite cell -- and SystemExit inherits from BaseException,
            # not Exception. So a case that resolved a token outside a try
            # walked straight past `except Exception` and out of this loop:
            # no FAIL line, no case name, just report_tokens' own message and
            # exit 1, which reads like the SUITE failed rather than one case.
            # That is what hid an ungated household-dependent case in CI, and
            # it cost a no-archive reproduction to find a name this loop had
            # in hand the whole time.
            #
            # NOT swallowed -- named and re-raised, exactly like the clauses
            # above. The `raise SystemExit(1)` in those clauses is raised FROM
            # a handler, not from fn(), so it is not caught here and still
            # exits 1 without being re-reported as a case failure.
            print(f"FAIL {fn.__name__}\n     SystemExit: {exc}")
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
