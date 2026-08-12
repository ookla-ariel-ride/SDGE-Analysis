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
import contextlib
import copy
import datetime as dt
import html as _htmllib
import pathlib
import re
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
    dp = rt._json("deep_results.json")["phantom"]
    assert f"${dp['annual_cost_at_blend']:,}" not in rendered, (
        f"the reconciliation cites deep_results' superseded flat-blend cost: {rendered}")
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
    history, and deep_results.json's is generated but priced at a hardcoded
    flat $0.20/kWh against an hour-weighted all-in import rate of about
    $0.375/kWh (issue #172). So both halves are pinned to the artifact AND to
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
    assert f"${dr['phantom']['annual_cost_at_blend']:,}" not in teaser, (
        "teaser cites deep_results' flat-$0.20/kWh cost, which issue #172 shows is about "
        f"half the real price of that energy -- got: {teaser}")
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
    sentence, so both now word themselves off the sign. The Yes/No prefix and
    the widens/narrows verb are deliberately NOT touched: issue #141 holds
    that prefix answers section 4's heading question backwards, and this case
    asserts only that the words around it stop claiming a lead that is not
    there.

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
                ("tie", plans[best]["no_battery"], "$0", f"ties {runner_up}"),
                ("beaten", plans[best]["no_battery"] - 500, "-$500",
                 f"trails {runner_up} by $500/yr")):
            with _swapped(plans[runner_up], "no_battery", beaten):
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
# `winners`, and the three tokens that gate on it render inside section 4's
# fixed class="win" row -- so the runner-up would have been published as the
# winner. The cases below hold both halves: identity is exact, sizes are
# hedged, and the household the matrix ranks second is refused by name rather
# than promoted.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _matrix_priced(plans, cells):
    """Substitute a whole synthetic matrix -- {plan: (no_battery, with_battery)}
    -- into the cached artifact, restoring every cell on the way out.

    battery_value is DERIVED here rather than passed, for _mid_battery_swaps'
    reason: battery_plan_matrix.py writes it as round(no_b - with_b) off the
    same two bills, so a case that moves a column and leaves battery_value
    behind is not describing a household, it is describing an artifact no run
    of the generator could produce."""
    with contextlib.ExitStack() as stack:
        for plan, (no_b, with_b) in cells.items():
            stack.enter_context(_swapped(plans[plan], "no_battery", no_b))
            stack.enter_context(_swapped(plans[plan], "with_battery", with_b))
            stack.enter_context(_swapped(plans[plan], "battery_value", no_b - with_b))
        yield


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
    cell is a whole number of dollars. If a future generator keeps the cents,
    this case fails and both constants have to be re-derived rather than
    silently applied to values that no longer need them."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    fractional = [f"{p}.{k} = {v!r}" for p, row in sorted(plans.items())
                  for k, v in sorted(row.items()) if float(v) != int(v)]
    assert not fractional, (
        "data/battery_plan_matrix.json no longer rounds every cell to whole dollars ("
        + ", ".join(fractional) + "), so report_tokens._BPM_TIE_USD is derived from a "
        "rounding the generator has stopped applying")
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
    ranking change. It may not say "No" either, because with a battery there
    is no single cheapest plan to hand the reader. And the size clause has to
    hedge the $1 lead it cannot size -- "leads by $1/yr" is a precision two
    rounded cells do not carry."""
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
        for wrong in ("Yes", "No"):
            assert not value.startswith(wrong), (
                f"S4_VERDICT_SHORT answers {wrong!r} where {best} is cheapest without a "
                f"battery and TIES {rival} with one -- a tie is not a ranking change, and "
                f"it is not a plan recommendation either: {value}")
        assert f"leads {rival} by under {rt._usd0(1 + rt._BPM_TIE_USD)}/yr" in value, (
            f"S4_VERDICT_SHORT does not name the holder of the $1 lead while bounding a "
            f"size two rounded cells cannot carry: {value}")
        assert "$1/yr" not in value, (
            f"S4_VERDICT_SHORT quotes a $1/yr lead off two cells that could be a cent "
            f"apart: {value}")
        # The tie renders the rest of section 4: joint-cheapest is cheapest, so
        # the win row's gate must let a tied household through.
        with _matrix_priced(plans, cells):
            for token in _MATRIX_PLAN_TOKENS:
                assert rt.resolve_token(token).strip(), (
                    f"{token} refused a matrix in which {best} is cheapest without a "
                    f"battery and tied with one")
        words = _assert_within_density_cap("S4_VERDICT_SHORT", value, "a lead into a tie")
        for token, was in published.items():
            assert rt.resolve_token(token) == was, (
                f"the synthetic matrix leaked out of this case ({token})")
    return (f"a $1 stored gap ranks ({sorted(no_batt)} alone cheapest) and equal cells tie "
            f"({sorted(with_batt)}), so the heading calls neither a change of plan "
            f"({value!r}, {words}w)")


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


@case
def case_a_household_the_matrix_ranks_second_is_refused_by_name():
    """THE REVIEWER'S REGRESSION CASE, and the cost of getting it right.

    A household whose plan stores $101 against a rival's $100 is on the
    second-cheapest plan -- the rounding is monotone, so the rival's bill was
    strictly lower. It is not in `winners`, and section 4's three win-row
    tokens refuse, which stops the whole report. A $1.00 band on "cheapest"
    made that household render, and what it rendered was a `class="win"` row
    holding a plan the artifact ranks second: the report would have called
    the runner-up the winner. Dodging a refusal by publishing a false one is
    the trade this case exists to refuse, so it asserts the refusal instead.

    What the refusal has to be is HONEST AND WELL-NAMED. It says the
    household is not on the plan that column prices cheapest, quotes both
    cells so the reader can see the ranking, and names the template markup
    that cannot express "second" -- which is where the real fix belongs, and
    it is not in this module.

    Both directions are driven, in both columns. A dollar the OTHER way is
    the same rounding and the same $1, and it must resolve every token: the
    refusal has to be about the ranking, not about closeness.

    The sweep also shows the blast radius is exactly the win-row family --
    every other token still renders -- so whoever makes the template
    conditional knows precisely what it has to cover."""
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
        for column, phrase in rt._BPM_COLUMNS:
            for label, offset in (("a dollar below", -1), ("a dollar above", 1)):
                moved = dict(zip(("no_battery", "with_battery"),
                                 (plans[rival]["no_battery"], plans[rival]["with_battery"])))
                # offset -1 puts the RIVAL a dollar under this household, which
                # makes the household second; +1 leaves the household ahead.
                moved[column] = plans[cheapest][column] + offset
                with _matrix_priced(plans, {rival: (moved["no_battery"],
                                                    moved["with_battery"])}):
                    refused, rendered = {}, {}
                    for name, spec in rt.TOKENS.items():
                        if spec.get("kind") == "gap":
                            continue
                        try:
                            rendered[name] = rt.resolve_token(name)
                        except BaseException as exc:   # noqa: BLE001 - refusal is SystemExit
                            refused[name] = str(exc)
                    cheapest_set = rt._bpm_cheapest("S4_VERDICT_SHORT", column)
                if offset > 0:
                    assert cheapest_set == {cheapest}, (
                        f"a rival priced $1 ABOVE this household did not leave it alone "
                        f"cheapest in the {column} column: {sorted(cheapest_set)}")
                    assert not refused, (
                        f"{len(refused)} token(s) refused for a household the matrix "
                        f"still ranks first {phrase}: "
                        + "; ".join(f"{n} -- {w}" for n, w in sorted(refused.items())))
                    seen[f"{column} {label}"] = "whole report"
                    continue
                assert cheapest_set == {rival}, (
                    f"a rival priced $1 BELOW this household is the cheaper bill -- "
                    f"round() is monotone -- but the {column} column's cheapest set is "
                    f"{sorted(cheapest_set)}")
                assert set(refused) == set(_MATRIX_PLAN_TOKENS), (
                    f"the win-row family is not what refuses for a household the matrix "
                    f"ranks second {phrase}: refused {sorted(refused)}, expected "
                    f"{sorted(_MATRIX_PLAN_TOKENS)}")
                for name, why in refused.items():
                    for fragment in (
                            "not on the plan that column prices cheapest",
                            f"prices {cheapest} at",
                            f"against {rival} at",
                            'class="win" row'):
                        assert fragment in why, (
                            f"{name}'s refusal does not name the situation ({fragment!r} "
                            f"missing), so a reader cannot tell that this household is on "
                            f"the plan the matrix ranks second: {why}")
                    assert f"{rt._usd0(rt._BPM_TIE_USD)}/yr of" not in why, (
                        f"{name}'s refusal sizes the gap it cannot size: {why}")
                # This module's OWN sentence about the same cells keeps
                # rendering: it can say a plan trails, which is exactly what
                # the template's win row cannot.
                assert "trails" in rendered["S4_VERDICT_SHORT"], (
                    f"S4_VERDICT_SHORT does not say {cheapest} trails while the matrix "
                    f"prices {rival} below it {phrase}: {rendered['S4_VERDICT_SHORT']}")
                seen[f"{column} {label}"] = f"{len(refused)} refused"
        assert _resolve_every_token() == published, (
            "the substituted matrix cell leaked out of this case")
    return ("a household the matrix ranks second is refused by name -- "
            f"{sorted(_MATRIX_PLAN_TOKENS)} only, out of {len(published)} tokens, in both "
            "columns, and a household a dollar the other way still gets a whole report ("
            + ", ".join(f"{k}: {v}" for k, v in seen.items()) + ")")


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


# The template chrome that ASSERTS the household's plan wins. Named here so
# the cases below fail if the chrome is ever made conditional (at which point
# the gates it justifies can go) as loudly as if a gate regressed.
_PLAN_CHROME = ("Best plan in every scenario", 'Why {{BEST_PLAN}} wins:')
# The two families are gated on DIFFERENT artifacts because they are rendered
# from different artifacts -- see
# case_section_4s_win_row_is_gated_on_the_matrix_its_cells_come_from.
_CSV_PLAN_TOKENS = ("BEST_PLAN", "BEST_PLAN_ANNUAL_CCA", "BEST_PLAN_ANNUAL_BUNDLED")
_MATRIX_PLAN_TOKENS = ("BEST_PLAN_NOBATT_MODELED", "BEST_PLAN_BATT_MODELED",
                       "BATTERY_VALUE_BEST_PLAN")
_BEST_PLAN_TOKENS = _CSV_PLAN_TOKENS + _MATRIX_PLAN_TOKENS


@case
def case_best_plan_family_fails_closed_on_chrome_it_cannot_make_true():
    """FINDING 2 (round two). _plan_ranking no longer requires the household's
    plan to be cheapest, but BEST_PLAN was a bare household.plan passthrough
    and report-template.html still asserts the ranking as FIXED CHROME: a
    section 0 card reading "Best plan in every scenario", `class="win"` rows
    carrying {{BEST_PLAN}} in sections 3 and 4, and the line "Why
    {{BEST_PLAN}} wins:". So a losing household got section 3's own inverted
    verdict ("... is not the cheapest plan for this house") printed beside a
    card calling the same plan the best in every scenario.

    The template is not this module's to edit, and no value rendered into
    those slots makes that page true -- so the family is state 3 there,
    failing closed with a message that NAMES the chrome as the reason. This
    deliberately re-refuses a case round one asked to invert; the difference
    is that round one refused sentences this module OWNS (which now invert,
    and are driven in the plan-verdict case above), while these tokens feed
    sentences it does not own.

    THE FAMILY IS THE THREE TOKENS RENDERED FROM data/plan_results.csv.
    Section 4's three matrix cells used to be gated here too, off a CSV that
    does not price a battery at all; they are gated on their own artifact now
    and driven in the case below. This case asserts they are NOT taken down
    by a CSV-only change, which is the other half of gating the right artifact.

    A TIE renders: a plan tying for cheapest is a cheapest plan, so the card
    and the win rows are true of it.

    The chrome literals are asserted present in report-template.html, so if
    that file is ever made conditional this case fails and the gate comes out
    rather than quietly outliving its reason."""
    template = rt.TEMPLATE.read_text()
    for literal in _PLAN_CHROME:
        assert literal in template, (
            f"report-template.html no longer carries {literal!r}; if the plan chrome is "
            "now conditional, _best_plan's gate has outlived its reason -- delete both")
    win_rows = [ln for ln in template.splitlines()
                if 'class="win"' in ln and "{{BEST_PLAN}}" in ln]
    assert len(win_rows) >= 2, (
        f"report-template.html no longer marks the household's plan rows as winners "
        f"({len(win_rows)} found); re-derive whether this gate is still needed")

    provider, cheapest, priced = _plan_ranking_inputs()
    runner_up = min((r for r in priced if r["plan"] != cheapest),
                    key=lambda r: float(r["total"]))
    cheapest_total = next(r["total"] for r in priced if r["plan"] == cheapest)

    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in _BEST_PLAN_TOKENS}
        for token, value in published.items():
            assert value.strip(), f"{token} resolved blank on the winning household"

        # 1. Beaten in the CSV: the three CSV-rendered tokens fail closed,
        #    naming the chrome. The household stays on the plan the matrix
        #    prices, so the refusal can only be the ranking gate and not a
        #    missing artifact row.
        with _swapped(runner_up, "total", str(float(cheapest_total) - 500)):
            for token in _CSV_PLAN_TOKENS:
                try:
                    value = rt.resolve_token(token)
                    raise AssertionError(
                        f"{token} rendered {value!r} into report-template.html's "
                        f"'Best plan in every scenario' chrome while {runner_up['plan']} "
                        f"prices $500/yr below {cheapest}")
                except SystemExit as e:
                    assert token in str(e), e
                    assert "best plan" in str(e), (
                        f"{token}'s refusal does not name the claim it cannot make: {e}")
                    assert "report-template.html" in str(e) and _PLAN_CHROME[0] in str(e), (
                        f"{token}'s refusal does not name the chrome that makes the page "
                        f"unrenderable, so a reader cannot tell what to fix: {e}")
            # ... and the matrix cells, which this CSV does not rank, are
            # untouched by it.
            for token in _MATRIX_PLAN_TOKENS:
                assert rt.resolve_token(token) == published[token], (
                    f"{token} refused on a data/plan_results.csv change, but its value "
                    "is a data/battery_plan_matrix.json cell that CSV does not price")

        # 2. An exact tie still renders: a joint-cheapest plan IS a best plan.
        with _swapped(runner_up, "total", cheapest_total):
            for token in _BEST_PLAN_TOKENS:
                assert rt.resolve_token(token) == published[token], (
                    f"{token} refused a household tying for cheapest, which the chrome "
                    "describes truthfully")

        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted plan total leaked out of this case ({token})")
    return (f"the {len(_CSV_PLAN_TOKENS)} plan_results.csv-rendered tokens fail closed "
            f"naming report-template.html's own plan chrome when {cheapest} stops winning "
            f"there, the {len(_MATRIX_PLAN_TOKENS)} matrix cells are untouched by it, and "
            "all six still render on a tie")


@case
def case_section_4s_win_row_is_gated_on_the_matrix_its_cells_come_from():
    """ROUND 4, FINDING 3. Section 4's `class="win"` row renders three cells,
    all three out of data/battery_plan_matrix.json -- and the gate in front of
    them ranked data/plan_results.csv instead.

    The two artifacts are not interchangeable. battery_plan_matrix.py asserts
    its no_battery column against plan_results.csv's CEA column to within
    $1.00, so they agree about THAT column for the three plans the matrix
    prices; plan_results.csv has no battery column at all, so nothing in it
    constrains with_battery or battery_value.

    The reproduction: move a rival plan's matrix with_battery below this
    household's and plan_results.csv does not change by a cent. The old gate
    passed, and section 4 rendered "trails by $500/yr with one" from
    S4_VERDICT_SHORT -- which reads the matrix, correctly -- directly above a
    row marked as the winner.

    Both columns are driven, because the win row spans both and section 4's
    heading question is exactly whether the answer survives the battery. The
    two SIBLING sentences that read the same matrix (S4_VERDICT_SHORT and
    PLAN_MARGIN_VS_RUNNER_UP) must keep rendering throughout: they are this
    module's own, they word themselves off the sign, and taking them down was
    never the fix."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    provider, cheapest, _priced = _plan_ranking_inputs()
    with _stub_plan(cheapest, provider):
        assert cheapest in plans, (
            f"the CSV's cheapest plan {cheapest!r} is not priced in "
            f"battery_plan_matrix.json ({sorted(plans)}); this case cannot drive the gate")
        published = {t: rt.resolve_token(t) for t in _MATRIX_PLAN_TOKENS}
        best = plans[cheapest]
        rivals = [p for p in plans if p != cheapest]
        assert rivals, "the matrix prices only one plan; there is no rival to promote"
        rival = min(rivals, key=lambda p: plans[p]["no_battery"])

        checked = []
        for column in ("no_battery", "with_battery"):
            # Undercut the household's plan in ONE matrix column at a time,
            # leaving data/plan_results.csv untouched.
            with _swapped(plans[rival], column, best[column] - 500):
                for token in _MATRIX_PLAN_TOKENS:
                    try:
                        value = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} rendered {value!r} into section 4's class=\"win\" "
                            f"row while battery_plan_matrix.json prices {rival} $500/yr "
                            f"below {cheapest} in its {column} column")
                    except SystemExit as e:
                        assert token in str(e), e
                        assert "best plan" in str(e) and column in str(e), (
                            f"{token}'s refusal does not name the matrix column that "
                            f"made the win row false: {e}")
                        assert "battery_plan_matrix.json" in str(e), (
                            f"{token}'s refusal names no artifact a reader could go "
                            f"and check: {e}")
                # The sibling sentences off the same artifact keep rendering.
                for sibling in ("S4_VERDICT_SHORT", "PLAN_MARGIN_VS_RUNNER_UP"):
                    assert rt.resolve_token(sibling).strip(), (
                        f"{sibling} was taken down by the matrix gate; it words itself "
                        "off the sign and must render for a household the matrix does "
                        "not put first")
                checked.append(column)

            # A TIE in that column renders: joint-cheapest is cheapest.
            with _swapped(plans[rival], column, best[column]):
                for token in _MATRIX_PLAN_TOKENS:
                    assert rt.resolve_token(token) == published[token], (
                        f"{token} refused a plan tying for cheapest in the {column} "
                        "column, which the win row describes truthfully")

        for token, value in published.items():
            assert rt.resolve_token(token) == value, (
                f"the substituted matrix cell leaked out of this case ({token})")
    return (f"the {len(_MATRIX_PLAN_TOKENS)} section 4 win-row cells fail closed when "
            f"battery_plan_matrix.json stops putting {cheapest} first in either of its "
            f"{checked} columns, tie included, while S4_VERDICT_SHORT and "
            "PLAN_MARGIN_VS_RUNNER_UP keep rendering")


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
                ("ties with a battery", plans[best]["with_battery"], "ties with one")):
            with _swapped(plans[runner_up], "with_battery", with_battery):
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
                                  ("tied", plans[best]["no_battery"])):
            with _swapped(plans[runner_up], "no_battery", no_battery):
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
            # The three comparisons the previous sweep did not reach (issue
            # #131 review round 4, findings 4 and 5, plus the section 4 win
            # row's own new gate). Named the same way, in the same place, so
            # "swept" means the whole module and not the arms that were easy
            # to reach from a verdict token.
            ("S0_VERDICT", pk["MID"], "battery_alone_post_ev_fix_yr",
             "whether the battery repays its own cost", cheapest),
            ("BEST_PLAN_BATT_MODELED", plans[best], "with_battery",
             "cheapest with one battery", best),
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
    "PEAK_WINDOW", "RATES_EFFECTIVE_DATE", "RECOMMENDED_PACKAGE_SUMMARY",
    "REPORT_DATE", "S14_VERDICT", "SUMMER_ONPEAK_EXPORT_RATE",
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

    # 4. behavior_rebuild.py's _not_applicable stub, for a household with no EV.
    def no_ev(doc):
        doc["detection"] = {"not_applicable": True,
                            "reason": "household.has_ev is false (intake applicability flag)"}

    with _patched(rt, "_json", _stub_for("behavior_rebuild.json", no_ev)):
        for token in ("EV_SESSION_COUNT", "EV_ANNUAL_KWH", "EV_AVG_SESSION_KWH",
                      "EV_WINDOW_DECOMPOSITION", "EV_SOP_COMPLIANCE_PCT",
                      "EV_DETECTION_BASIS"):
            got[f"no_ev_{token}"] = _renders(token)
            assert "not applicable" in got[f"no_ev_{token}"], got[f"no_ev_{token}"]
            assert "has_ev" in got[f"no_ev_{token}"], got[f"no_ev_{token}"]

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
                 "median_kw": 1.0, "excluded_high_demand": False}
                for o in offsets]
            doc["night_floor"]["nights_total"] = nights
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

    _best_plan_matrix_cell gates its three tokens on the household being on
    that plan (section 4's class="win" row asserts it in fixed markup), so a
    case that stubs any other plan is exercising the chrome gate rather than
    the thing it came to test. Computed from the artifact rather than skipped,
    so these cases run on a checkout with no private data -- which is the
    checkout .github/workflows/tests.yml uses."""
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

    @contextlib.contextmanager
    def _repriced(prices):
        """plan_results.csv's total column, repriced for this household's own
        generation provider. Every plan named goes below zero."""
        rows = rt._csv_rows("plan_results.csv")
        original = [r["total"] for r in rows]
        try:
            for row in rows:
                if row["provider"] == provider and row["plan"] in prices:
                    row["total"] = f"{prices[row['plan']]:.2f}"
            yield
        finally:
            for row, was in zip(rows, original):
                row["total"] = was

    rendered = {}
    with _stub_plan(cheapest, provider):
        for label, prices, quoted in (
                ("sole cheapest", {cheapest: -5000.0, runner_up: -1200.0}, -5000.0),
                ("tied cheapest", {cheapest: -5000.0, runner_up: -5000.0}, -5000.0),
                ("beaten", {cheapest: -1200.0, runner_up: -5000.0}, -1200.0)):
            with _repriced(prices):
                s3 = rt.resolve_token("S3_VERDICT")
                # BEST_PLAN and its two annual cells go through _best_plan's
                # chrome gate, whose DETAIL string _claim builds on every call
                # -- the site that aborted the report while its own claim was
                # supported. Only reachable while this plan still wins.
                gated = ({n: rt.resolve_token(n)
                          for n in ("BEST_PLAN", "BEST_PLAN_ANNUAL_CCA",
                                    "BEST_PLAN_ANNUAL_BUNDLED")}
                         if label != "beaten" else {})
            assert f"{quoted:,.0f}".replace("-", "-$") + "/yr" in s3, (
                f"section 3 does not state a modeled bill below zero on the {label} "
                f"branch: {s3}")
            assert "$-" not in s3, f"the minus went back inside the sigil: {s3}"
            rendered[f"S3 {label}"] = _assert_within_density_cap("S3_VERDICT", s3, label)
            if gated:
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
# no edit here. _SEAM_ALLOWLIST carries five entries today, all of them class
# 2's dimensionless counts and years, each naming ONE occurrence; the case
# below asserts that every entry STILL HAS the seam it excuses and that no
# entry pardons more than the one occurrence it names -- so an exception whose
# seam has since been fixed cannot linger as a silent loophole, and one that
# was written for a different line cannot cover a real defect.
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
#     ITS BLIND SPOT, stated and counted rather than supposed: a token that
#     formats ITSELF -- kind="derived" or "cited_constant" with fmt=None,
#     building its own string by calling _usd3 or _cents1 inside its own
#     lambda -- declares no format for this test to read, so its dimension is
#     invisible here however plainly a reader sees it. The live template
#     carries such tokens whose whole value is one sigil-carrying figure
#     (SUMMER_ONPEAK_IMPORT_RATE is the shape: kind="derived", fmt=None,
#     get=lambda ctx: _usd3(...)), and a regression inside one of those
#     lambdas is caught by nothing here. The count is reported by the case
#     rather than written down, so it tracks the registry instead of going
#     stale. The remedy for any one of them is to declare its fmt.
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
#     further down the same line. The live example of the latter is the
#     lifetime table, which prints the first year's value in both the annual
#     and the cumulative column (report-template.html:538) -- but be precise
#     about WHICH rule spares it: today's FIRST_YEAR_VALUE is 7 characters, so
#     _SEAM_MIN_ECHO excludes it before the gap rule is ever consulted. The
#     gap between the two cells is len("</td><td>") == 9 against a threshold
#     of 6, a margin of three characters. Omit the optional </td> closers --
#     valid HTML5 -- and the gap falls to len("<td>") == 4, and a cumulative
#     column repeating a value >= _SEAM_MIN_ECHO long WOULD false-positive.
#     That is a tripwire, not a proof, on the same terms as class 2.
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
#   * Class 3 reads its two windows with every HTML TAG MASKED TO SPACES of
#     the same length, so attribute text -- which a reader never sees -- is
#     not mistaken for a printed figure. '<td data-sort="12,345.6 kWh">{{X}}'
#     with X = "12,345.6 kWh" prints the figure ONCE and is not flagged; the
#     sort key is markup. Masking to EQUAL-LENGTH spaces rather than deleting
#     is what keeps _SEAM_ECHO_GAP measuring the distance a reader sees: the
#     lifetime table's "</td><td>" is still 9 characters against a threshold
#     of 6, exactly as the paragraph above computes it.
#   * All three rules are scoped to ONE TEMPLATE LINE. This template puts one
#     element per line, so a seam always has both of its sides on the same
#     line; a value echoed across a line break is not looked for.
#   * Values are compared ESCAPED, the way the generator writes them.
#     generate_report.render() substitutes html.escape(value, quote=True), so
#     that is what _seam_render substitutes too. Escaping is neutral for class
#     1 -- it never changes a leading or trailing sigil -- but NOT for class 3,
#     which compares INTERNAL text: a template printing "PG&amp;E 2025" beside
#     a token whose value is "PG&E 2025" renders the figure twice, and an
#     unescaped comparison sees two different strings and reports nothing.
#     Thirteen live token values change under escaping (ampersands and
#     apostrophes), so this is the shipped path, not a hypothetical one.
#
# CI. This runs with NO private archive. Where private/household.yaml is
# absent, the household-sourced tokens are resolved against the committed
# household.example.yaml instead (see _seam_values), so all 206 non-gap tokens
# are checked on the merge-guarding runner rather than the 162 that read only
# data/. The five KNOWN_GAPS tokens are the only ones skipped, and the case
# asserts that set by name. The two paths are not identical and this guard
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
# on the SAME line. Five template lines print a token twice (line 234, 325,
# 529, 538, 655); none of them is allowlisted, and an entry for one would
# excuse both. Line numbers are the next granularity down and they do not
# help here either.
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
     "({{BILLING_PERIOD_COUNT}} billing periods)"):
        "a COUNT of billing periods. One statement PDF can carry two periods, "
        "which is why this count differs from BILL_COUNT and why neither is a "
        "quantity with a unit.",
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

    A token with no value (the KNOWN_GAPS five) is left as its literal
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
    already carries at that end -- or None."""
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


def _seam_missing_unit(value, head, tail, fmt=None):
    """Class 2: a value that lost the dimension its format declares, or a bare
    number that nothing beside it gives a unit to -- or None.

    A bare number followed by a WORD THAT IS NOT A UNIT is reported. The
    earlier form of this rule reported only a bare number followed by a
    function word, which is too tight to catch its own defect class: on issue
    #129's own line it caught SOLAR_COVERAGE_PCT (followed by "of") and missed
    SELF_CONSUMED_SHARE (followed by "self-consumed") losing its percent sign
    the same way. See the block comment above for the whitelist, the
    exemptions, and the false positives the widening costs.

    `fmt` is the token's DECLARED format spec, as report_tokens.TOKENS records
    it, and None for a token that declares none or that this scan cannot name
    (a synthetic fixture's invented token). It is what the first test below
    reads, and it is passed in rather than looked up so this rule stays a
    function of its arguments and the module tables, like the other two.

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
    positives and catches the sign case for free."""
    dimension = _SEAM_FMT_DIMENSIONS.get(fmt)
    if dimension is not None and dimension not in value \
            and head[-1:] != dimension and tail[:1] != dimension:
        return (f"a {fmt!r} value renders {value!r}, which carries no {dimension!r} and "
                f"has none immediately beside it; that format DECLARES the figure is "
                f"measured in {dimension!r}, so this one lost its dimension -- whatever "
                "unit follows it belongs to something else")
    if not _SEAM_BARE_NUMBER_RE.match(value):
        return None
    # A sigil in front of a bare number IS its unit ("$14,500", "~2").
    if head[-1:] in _SEAM_SIGILS:
        return None
    # Read THROUGH markup, as the block comment describes: tags become
    # whitespace so the next word can be in the next table cell.
    text = _SEAM_TAG_RE.sub(" ", tail).lstrip()
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


def _seam_mask_tags(text):
    """`text` with every HTML tag blanked to spaces OF THE SAME LENGTH.

    Same trick as _seam_comment_mask, and for the same reason: every column
    position in the masked copy is the position it had in the real text, so a
    distance measured on the mask is a distance in the document."""
    return _SEAM_TAG_RE.sub(lambda m: " " * len(m.group(0)), text)


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

    Both windows have their HTML TAGS MASKED TO EQUAL-LENGTH SPACES first.
    Attribute text is markup, not print: `<td data-sort="12,345.6 kWh">{{X}}`
    with X = "12,345.6 kWh" shows the figure once, and reading the sort key as
    a second printing is a false positive on a common table idiom. Masking to
    the SAME LENGTH rather than deleting is what leaves _SEAM_ECHO_GAP
    measuring the distance a reader sees -- the lifetime table's "</td><td>"
    is 9 characters masked or not."""
    core = value.rstrip(_SEAM_ECHO_TRIM)
    seen_head, seen_tail = _seam_mask_tags(head), _seam_mask_tags(tail)
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
    _SEAM_FMT_DIMENSIONS, _SEAM_TAG_RE, _SEAM_ECHO_TRIM, _SEAM_MIN_ECHO and
    _SEAM_ECHO_GAP inside the three rules -- plus report_tokens.TOKENS, for
    the one thing about a seam that is not visible in the rendered line: what
    dimension the token's declared format says the figure is measured in
    -- so identical arguments answer differently once one of
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
            # that half of class 2 inert exactly as it should be.
            fmt = rt.TOKENS.get(name, {}).get("fmt")
            for label, why in zip(_SEAM_CLASSES,
                                  (_seam_doubled(value, head, tail),
                                   _seam_missing_unit(value, head, tail, fmt),
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
    household.example.yaml, so the seam guard checks all 206 non-gap tokens on
    a runner that has no private archive instead of the 162 that read only
    data/.

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
    committed stand-in otherwise, so the same 206 tokens are checked in both
    places."""
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
            dimension = _SEAM_FMT_DIMENSIONS.get(fmt)
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
            why = _seam_missing_unit(value, head, tail, fmt)
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
def case_the_seam_guard_checks_the_same_tokens_without_the_private_archive():
    """The case above resolves against private/household.yaml where it is
    staged, and .github/workflows/tests.yml runs this suite where it is not.
    A guard that quietly checks 162 of 206 tokens on the runner that actually
    guards merges is the failure mode this file has already recorded twice
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

    THE BLIND SPOT IS COUNTED HERE TOO, in the return line rather than as a
    literal, because a token that formats itself (fmt=None, calling _usd3 or
    _cents1 inside its own lambda) declares no dimension for any of this to
    read. See the block comment above."""
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

    # 3. The stated blind spot, counted from the registry so it tracks it.
    seen = {name for line in template.splitlines()
            for name, _s, _e in _seam_render(line, values)[1]}
    self_formatting = sorted(
        name for name in seen
        if rt.TOKENS.get(name, {}).get("fmt") not in _SEAM_FMT_DIMENSIONS
        and any(s in values[name] for s in _SEAM_DIMENSION_SIGILS)
        and _SEAM_BARE_NUMBER_RE.match(
            "".join(c for c in values[name]
                    if c not in _SEAM_DIMENSION_SIGILS and c not in "~+ ")))
    return (f"all {swept} occurrence(s) of a token declaring a money, percent or cents "
            f"format are caught when that format slips to {plain!r}; the prose-only rule "
            f"caught {caught_before} of them, and {len(unit_fronted)} of the "
            f"{swept - caught_before} it missed were missed because a unit "
            f"({sorted({u for _n, _f, u in unit_fronted})}) sits where the sigil should "
            f"be. {len(_SEAM_FMT_DIMENSIONS)} format spec(s) classify as a dimension, "
            f"{len(_SEAM_FMT_DIMENSION_FLOOR)} of them held to the committed floor; "
            f"{len(self_formatting)} single-figure token(s) in the template format "
            "themselves and declare no dimension for this to read")


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
    class 3 read it as twice. The windows are now masked, and masked to
    EQUAL-LENGTH spaces rather than deleted, which the third assertion pins
    from the other side: deleting tags instead would collapse the lifetime
    table's "</td><td>" from 9 characters to nothing and false-positive on
    every cumulative column that repeats the year's own figure.

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
        "the next table cell's copy of a figure is reported as an echo: the tag mask "
        "is deleting tags instead of blanking them to the same length, so "
        "_SEAM_ECHO_GAP now measures a distance no reader sees")
    # ...and the visible repeat, on both sides, is still reported -- so the
    # masking narrowed the rule to markup rather than switching it off.
    after = _seam_echo("12,345.6 kWh", "<td>", " — 12,345.6 kWh</td>")
    assert after and "later" in after, f"a visible repeat after the value was lost: {after!r}"
    before = _seam_echo("12,345.6 kWh", "<td>12,345.6 kWh — ", "</td>")
    assert before and "earlier" in before, (
        f"a visible repeat ahead of the value was lost: {before!r}")
    return ("a '>' inside a quoted attribute no longer ends a tag for either rule, "
            "attribute text is no longer read as printed by the echo rule, the tag mask "
            "preserves the distances _SEAM_ECHO_GAP measures, and a visible repeat on "
            "either side is still reported")


@case
def case_the_seam_guard_compares_the_values_the_generator_writes():
    """The guard renders what generate_report.render() renders: the value
    html.escape()d with quote=True.

    Escaping is neutral for class 1 -- it never touches a leading or trailing
    sigil -- and the block comment used to generalise that into "escaping
    cannot hide a seam", which is FALSE for class 3, the one rule that compares
    a value's INTERNAL text. A template printing "PG&amp;E 2025" beside a
    token whose value is "PG&E 2025" prints the figure twice in the published
    page, and an unescaped comparison sees two unequal strings and reports
    nothing. Thirteen live token values change under escaping, so this is the
    shipped path.

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
    echoed = {"A_TOKEN_THAT_DOES_NOT_EXIST_YET": "PG&E 2025"}
    fragment = "<p>{{A_TOKEN_THAT_DOES_NOT_EXIST_YET}} PG&amp;E 2025</p>"
    hits = _seam_defects(fragment, echoed)
    assert any(n == "A_TOKEN_THAT_DOES_NOT_EXIST_YET" and c == "echoed-phrase"
               for n, c, _w, _x in hits), (
        "a figure the template prints beside a token whose value renders to the same "
        f"escaped text went unreported: {hits} -- rendered "
        f"{_seam_render(fragment, echoed)[0]!r}")
    assert _seam_echo("PG&E 2025", "", " PG&amp;E 2025</p>") is None, (
        "the unescaped value already matches the escaped template text, so this case "
        "is no longer measuring what the escaping buys; pick a probe whose escaping "
        "actually changes it")
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


def _floor_at(median_kw, floor_w_used=None):
    """A stub edit putting this household's measured floor at `median_kw`,
    with the rest of the artifact made SELF-CONSISTENT the way
    quiet_night_floor.py itself makes it: the step is the floor rounded onto
    the ladder and then CLAMPED into [smallest rung, largest rung], and the
    published rate is that step's own marginal.

    The clamp is re-implemented here rather than imported because
    quiet_night_floor.py needs the private archive to run -- and because the
    defect under test is precisely that report_tokens.py never read it.
    `floor_w_used` overrides the clamp, which is how an artifact that
    contradicts its own ladder is built."""
    def edit(doc):
        sens = doc["sensitivity_per_100w"]
        rungs = sorted(s["reduction_w"] for s in sens["steps"])
        step = rungs[0] if len(rungs) < 2 else rungs[1] - rungs[0]
        w = int(round(median_kw * 1000 / step)) * step
        w = min(max(w, rungs[0]), rungs[-1]) if floor_w_used is None else floor_w_used
        doc["night_floor"]["median_kw"] = median_kw
        doc["pricing"]["floor_kw_priced"] = round(median_kw, 4)
        sens["usd_per_100w_at_current_floor"]["floor_w_used"] = w
        marginal = next((s["marginal_usd_per_100w"] for s in sens["steps"]
                         if s["reduction_w"] == w), None)
        if marginal is not None:
            sens["usd_per_100w_at_current_floor"]["value_usd"] = marginal
    return edit


def _resolve_every_token():
    """{token: rendered} for every non-gap token, or an AssertionError naming
    the ones that could not be resolved.

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
    assert not refused, (
        f"{len(refused)} token(s) refused, so this household gets no report at all: "
        + "; ".join(f"{n} -- {why}" for n, why in sorted(refused.items())))
    return rendered


@case
def case_a_floor_outside_the_sensitivity_ladder_still_gets_a_whole_report():
    """ISSUE #140, /review FINDING 1. NIGHT_FLOOR_SENSITIVITY_PER_100W checked
    the published step against the measured floor with a bare 50 W tolerance
    and REFUSED past it -- and generate_report.resolve_tokens_with_gaps folds
    a refusal into `failures`, which stops the whole run.

    But quiet_night_floor.sensitivity_per_100w() never computes a step for an
    arbitrary floor: it rounds the floor onto its ladder and then CLAMPS the
    result into [STEP_W, MAX_REDUCTION_W], bounds whose own comment says they
    bracket THIS household's ~1.0-1.1 kW floor. So a 1.40 kW floor pinned to
    the 1,200 W end missed by 200 W, a 0.03 kW floor pinned to the 100 W end
    missed by 70, and every household outside roughly 50-1,250 W got no
    report. Third time this project has shipped a guard that compares two
    artifact fields without reading the generator that writes both, which is
    what report_tokens.py's own _require_derived preamble is about.

    Three floors, and the sweep is over EVERY token rather than this one:
      1.40 kW -- above the ladder, renders the rate at its top end;
      0.03 kW -- below it, renders the rate at its bottom end;
      1.03 kW -- inside it, renders exactly what it renders today.
    The bounds in the assertions are read off the artifact's own ladder, so
    nothing here restates the generator's constants either."""
    _require_household()
    rungs = _ladder_rungs()
    lowest, highest = rungs[0], rungs[-1]
    live = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    got = {}
    for median, end_w, side, where in (
            ((highest + 200) / 1000.0, highest, "top", "above"),
            (lowest / 1000.0 - 0.07, lowest, "bottom", "below")):
        with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _floor_at(median))):
            rendered = _resolve_every_token()
            text = rendered["NIGHT_FLOOR_SENSITIVITY_PER_100W"]
            got[median] = text
        for phrase in (f"{end_w:,.0f} W step at the {side} of the re-billed ladder",
                       f"({median * 1000:,.0f} W) sits {where} the "
                       f"{lowest:,.0f}–{highest:,.0f} W range",
                       "a rate at that end of the ladder"):
            assert phrase in text, (
                f"a {median} kW floor is {where} the ladder, so the sentence must say so "
                f"({phrase!r}) rather than claim a step nearest it -- got: {text}")
        assert "nearest the measured floor" not in text, (
            f"a clamped step was published as the step 'nearest the measured floor': {text}")
        for label, pattern in _MALFORMED_RENDER:
            assert not pattern.search(text), f"the clamped rendering is {label}: {text}"

    # The household inside the ladder is untouched: same sentence as today.
    inside = rt._json("quiet_night_floor.json")["night_floor"]["median_kw"]
    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", _floor_at(inside))):
        same = _resolve_every_token()["NIGHT_FLOOR_SENSITIVITY_PER_100W"]
    assert same == live, f"the in-range rendering moved:\n  was {live!r}\n  now {same!r}"
    assert "nearest the measured floor" in same, same

    # What is STILL refused: an artifact that contradicts its own ladder -- a
    # floor outside the range whose step is not the end the clamp produces.
    with _patched(rt, "_json", _stub_for(
            "quiet_night_floor.json",
            _floor_at((highest + 200) / 1000.0, floor_w_used=lowest))):
        try:
            text = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
        except SystemExit as exc:
            assert "NIGHT_FLOOR_SENSITIVITY_PER_100W" in str(exc), exc
            assert f"{lowest:,.0f} W step" in str(exc), exc
        else:
            raise AssertionError(
                "a floor above the ladder whose rate was read off the ladder's BOTTOM "
                f"rung was published anyway: {text}")

    after = rt.resolve_token("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert after == live, f"the stubs leaked: {after!r} != {live!r}"
    return (f"a floor above the {lowest}-{highest} W ladder and one below it both resolve "
            f"the ENTIRE token set ({len(rt.TOKENS)} entries) and say the rate is the one "
            f"at the ladder's end; a floor inside it still renders {live[:38]!r}...; and an "
            "artifact whose step is not the clamp's own endpoint is still refused by name")


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
def case_the_sensitivity_spread_carries_the_window_at_its_own_endpoints():
    """ISSUE #140, /review FINDING 5. On a partial corpus this sentence read
    "about $289 across the 200 nights measured, less than a full year, for
    every 100 W taken off it, read off the 1,000 W step ... the same ladder's
    marginal runs from $249 to $323 per 100 W across the range it was
    re-billed over".

    Both endpoints are sums over exactly the same window as the rate, and the
    docstring justified leaving them bare by saying the window is stated
    "immediately before them". It is not: on that render the window clause
    sits about forty words and a complete clause earlier. Nor can
    _ANNUAL_CLAIM catch it -- those figures carry no "/yr" -- so this is the
    defect the structural guard exists for, at the one exit its regex cannot
    see.

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
             "median_kw": 1.0, "excluded_high_demand": False}
            for i in range(nights)]
        doc["night_floor"]["nights_total"] = nights

    with _patched(rt, "_json", _stub_for("quiet_night_floor.json", short_corpus)):
        text = _renders("NIGHT_FLOOR_SENSITIVITY_PER_100W")
    assert not rt._ANNUAL_CLAIM.search(text), text
    steps = rt._json("quiet_night_floor.json")["sensitivity_per_100w"]["steps"]
    marginals = [s["marginal_usd_per_100w"] for s in steps]
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
