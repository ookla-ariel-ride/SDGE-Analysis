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
    dollar figure uses), and the phantom figures must track deep_results,
    whose 3-5am method matches section 9's own framing. Section 9's phantom
    sentence cites no artifact, and the body's phantom NUMBERS come from a
    third artifact (extra_results.json) -- that unresolved three-way split
    is issue #140, deliberately not settled here. A future change that
    re-points either half at a different artifact fails here.

    Deliberately NOT gated on _require_household(): SEC9_TEASER reads only
    committed public artifacts, so this case must run in CI too -- gating it
    would let a reversion merge green (Codex adversarial review, issue #130)."""
    teaser = rt.resolve_token("SEC9_TEASER")
    br = rt._json("behavior_rebuild.json")["detection"]
    dr = rt._json("deep_results.json")

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

    assert f"{dr['phantom']['annual_kwh']:,} kWh/yr" in teaser, (
        f"phantom kWh must track deep_results, which section 9's body cites -- got: {teaser}")
    assert f"${dr['phantom']['annual_cost_at_blend']:,}/yr" in teaser, (
        f"phantom cost must track deep_results, which section 9's body cites -- got: {teaser}")
    return (f"SEC9_TEASER cites behavior_rebuild's {sessions} sessions (not "
            f"deep_results' {stale}) and deep_results' phantom figures; the "
            "phantom three-way split is tracked in issue #140")


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


def _assert_verdict_matches_index(name, index_html):
    """The published line must equal the token AS RENDERED -- i.e. after the
    same html.escape(value, quote=True) generate_report.py applies. Comparing
    the raw value instead would have been wrong in both directions: "&" is
    fine (it escapes to "&amp;", which is exactly what index.html carries),
    while "'" is not (it escapes to "&#x27;"). Escaping here reproduces the
    render contract rather than approximating it."""
    value = rt.resolve_token(name)
    assert value.startswith("In one sentence: "), (
        f"{name} must open with the same stem the other section verdicts use, got: {value!r}")
    assert not (set("<>") & set(value)), (
        f"{name} contains markup characters; these tokens carry plain text only: {value!r}")
    rendered = _htmllib.escape(value, quote=True)
    assert f'<p class="verdict">{rendered}</p>' in index_html, (
        f"{name} does not round-trip into index.html:\n  token    : {value!r}\n"
        f"  rendered : {rendered!r}")
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
def case_section_verdict_guards_refuse_to_publish_a_false_claim():
    """Two of these sentences make a claim that would be FALSE if the
    artifacts moved, so their formulas fail closed instead of rendering it
    (CLAUDE.md section 0). Proven by driving each guard, not by reading it:
    S1 claims every billed TOU bucket rebuilds, S3 claims the household's own
    plan is the cheapest priced. Both must raise SystemExit naming the token."""
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
        rt.resolve_token("S3_VERDICT")
        raise AssertionError("S3_VERDICT claimed the household plan was cheapest while "
                             f"{victim['plan']} priced lower")
    except SystemExit as e:
        assert "S3_VERDICT" in str(e), e
    finally:
        victim["total"] = original
    return ("S1_VERDICT and S3_VERDICT both raise SystemExit naming themselves rather "
            "than publishing a claim their artifacts no longer support")


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
    assert "buys endurance, not savings" in published, published
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

    assert rt.resolve_token("S7_VERDICT") == published, (
        "the substituted marginal saving leaked out of this case")
    return ("S7_VERDICT reads endurance / faster / never-repays across marginal "
            f"+{real}, +{quick:.0f}, 0 and -400 per year, each inside the density cap "
            f"({', '.join(f'{k} {v}w' for k, v in widths.items())})")


@case
def case_s7_verdict_refuses_a_battery_payback_it_cannot_honestly_quote():
    """The same sentence quotes MID's own payback as a fact and calls HIGH an
    expansion. A non-positive or infinite payback, or a HIGH package that
    costs no more than MID, makes one of those false -- refuse, do not print
    a "~-6.5-yr payback"."""
    pk = rt._json("package_results.json")["packages"]
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
    return ("S7_VERDICT fails closed on a zero, negative or infinite battery payback and "
            "on a HIGH package with no extra cost to repay")


@case
def case_s0_verdict_refuses_to_call_a_losing_battery_a_sound_buy():
    """Section 0's headline quotes both battery-alone paybacks as a RANGE
    under "a sound optional buy". Both are cost/annual-saving quotients, so a
    battery that saved nothing would print "0.0-", "inf-" or "-3.0-year" there
    and the recommendation around it would be false."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    for key in ("battery_alone_payback_yr", "battery_alone_payback_post_fix_yr"):
        real = mid[key]
        assert real > 0, f"data/package_results.json:{key} is already {real}"
        for bad in (0, -3.0, float("inf")):
            with _swapped(mid, key, bad):
                try:
                    value = rt.resolve_token("S0_VERDICT")
                    raise AssertionError(f"S0_VERDICT called the battery a sound optional "
                                         f"buy at a {bad}-year payback: {value}")
                except SystemExit as e:
                    assert "S0_VERDICT" in str(e), e
    return ("S0_VERDICT fails closed on a zero, negative or infinite battery-alone "
            "payback at either end of the range it publishes")


@case
def case_s5_verdict_refuses_the_timing_claim_when_the_shares_stop_diverging():
    """"takes X% of imported kWh but Y% of the import energy cost, so timing
    sets this bill" is a comparison the sentence never states. It holds only
    while Y > X; at Y <= X the same words assert the opposite of the data."""
    rd = rt._json("report_data.json")
    pc = rd["periods_chart"]
    kwh_share = pc["import_share"][pc["order"].index("on")]
    real = rd["onpeak"]["share_of_energy_cost"]
    assert real > kwh_share, (
        f"data/report_data.json already puts the on-peak cost share ({real}) at or below "
        f"its kWh share ({kwh_share}); S5_VERDICT's claim is no longer true")
    for bad in (kwh_share, kwh_share / 2):
        with _swapped(rd["onpeak"], "share_of_energy_cost", bad):
            try:
                value = rt.resolve_token("S5_VERDICT")
                raise AssertionError(f"S5_VERDICT claimed timing sets this bill with the "
                                     f"on-peak cost share at {bad} against a kWh share of "
                                     f"{kwh_share}: {value}")
            except SystemExit as e:
                assert "S5_VERDICT" in str(e), e
    return ("S5_VERDICT fails closed when the on-peak cost share stops exceeding the "
            f"kWh share (live: {real} vs {kwh_share})")


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
def case_s6_verdict_compares_marginal_values_and_stays_true_at_every_sign():
    """S6's closing clause is the other shape: a comparison of two DIFFERENCES
    ("worth more than"), not a quotient. Ordering survives either difference
    going negative, so it needs no guard -- asserted here across the sign
    grid rather than by reading the code, since the neighbouring S7 defect
    looked equally harmless."""
    dp = rt._json("battery_dispatch_policies.json")
    grid = [(1000, 900, 1050), (1000, 900, 1200), (1000, 1100, 1050),
            (1000, 1100, 900), (1000, 900, 800)]
    for greedy, evening, expanded in grid:
        with _swapped(dp["pw3"]["greedy"], "save", greedy), \
             _swapped(dp["pw3"]["evening"], "save", evening), \
             _swapped(dp["pw3x"]["greedy"], "save", expanded):
            value = rt.resolve_token("S6_VERDICT")
        policy_gap, capacity_gap = greedy - evening, expanded - greedy
        expected = ("so the dispatch settings are worth more than a bigger pack"
                    if policy_gap > capacity_gap else
                    "so a bigger pack is worth more than the dispatch settings")
        assert expected in value, (
            f"S6_VERDICT ranks the wrong side at policy gap {policy_gap} vs capacity gap "
            f"{capacity_gap}: {value}")
    return (f"S6_VERDICT's 'worth more than' clause tracks the larger marginal gain across "
            f"{len(grid)} sign combinations, including both gaps negative")


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
