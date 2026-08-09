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
    tie_marginal = 1000.0
    tie_payback = exp_cost / tie_marginal
    with _swapped(pk["HIGH"], "marginal_vs_mid_yr", tie_marginal), \
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
    end where the sentence does."""
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

    published = [p for p in re.findall(r'<p class="verdict">(.*?)</p>', index_html, re.S)
                 if p.startswith(_htmllib.escape(fragments[0], quote=True))]
    assert len(published) == 1, (
        f"{len(published)} published verdict lines open with {fragments[0]!r}; "
        f"{name} cannot be matched to one")
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
            assert f"repays its extra cost in {exp_cost / marginal:.0f} years" in s7, (
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
        for bad in (0, -400):
            with _swapped(scenario, "saved", bad), _swapped(low, "savings_yr", bad):
                for token in tokens:
                    value = rt.resolve_token(token)
                    assert "adds no modeled saving" in value, (
                        f"{token} does not say plainly that a {bad}/yr EV-charging shift "
                        f"has nothing to capture: {value}")
                    assert value != published[token], (
                        f"{token} renders its published sentence at a {bad}/yr free "
                        f"fix: {value}")
                    for sold in ("captures the free savings", "whatever you buy"):
                        assert sold not in value, (
                            f"{token} still sells the free move at {bad}/yr ({sold!r}): "
                            f"{value}")
                    widths[f"{token} at {bad}"] = _assert_within_density_cap(
                        token, value, f"the no-saving branch at {bad}")

        # 2. The two artifacts disagreeing about the sign is still a refusal.
        for node, key, other in ((scenario, "saved", "package_results"),
                                 (low, "savings_yr", "behavior_rebuild")):
            for bad in (0, -400):
                with _swapped(node, key, bad):
                    for token in tokens:
                        try:
                            rendered = rt.resolve_token(token)
                            raise AssertionError(
                                f"{token} rendered while {key} is {bad}/yr and {other} "
                                f"still reports a positive saving: {rendered}")
                        except SystemExit as e:
                            assert token in str(e), e

        for token in tokens:
            assert rt.resolve_token(token) == published[token], (
                f"the substituted free-fix saving leaked out of this case ({token})")
    return ("S0_VERDICT, S7_VERDICT and S15_VERDICT invert to 'adds no modeled saving' "
            "when both artifacts put the free fix at or below zero, and still fail "
            "closed when only one does (live: "
            + ", ".join(f"{k.split(':')[0]} ${v:,.0f}" for k, v in live.items())
            + "; " + ", ".join(f"{k} {v}w" for k, v in widths.items()) + ")")


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
