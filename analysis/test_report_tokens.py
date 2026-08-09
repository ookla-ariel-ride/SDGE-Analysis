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
    the private archive is staged, clearly synthetic stand-ins otherwise.

    Same contract as _s2_household_inputs -- never the real answers as
    literals in a committed file (CLAUDE.md section 4), and the stub exists so
    the guard runs UNGATED on the archive-less runner CI uses rather than
    skipping there."""
    if rt.hh.PATH.is_file():
        return {}
    return {"household.utility": "Example Power",
            "household.cca": "Example Community Energy — Example Product"}


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
    and the recommendation around it would be false.

    Stubbed household plan, so this keeps running ungated now that S0 checks
    the tariff ranking too -- the substituted values are the household's own
    where the archive is staged."""
    mid = rt._json("package_results.json")["packages"]["MID"]
    provider, cheapest, _rows = _plan_ranking_inputs()
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
def case_s0_verdict_refuses_the_tariff_claim_when_another_plan_prices_lower():
    """Section 0 opens "the rate plan is right" -- the same claim section 3
    makes, and section 3 is the only one that used to check it. If a
    regeneration ever made another plan cheapest, S3 would fail closed while
    the report's most prominent sentence kept recommending the losing tariff.

    Both failure modes are driven, not read off the code: another plan priced
    lower, and an exact tie (at a tie the household's plan is not THE cheapest
    plan, it is one of two). Each is asserted for S0 and S3 together, which is
    what proves the two go through the same ranking rather than two
    implementations that can drift apart. Ungated: the household plan and
    provider are stubbed, so this runs in CI as well as here."""
    provider, cheapest, priced = _plan_ranking_inputs()
    index_html = (rt.ROOT / "index.html").read_text()

    # Positive control first, or "refuses" could pass on a token that refuses
    # everything -- and it doubles as the ungated round-trip check for the S0
    # line, which the verbatim case above can no longer make without the
    # private archive now that S0 declares household sources.
    with _stub_plan(cheapest, provider):
        published = _assert_verdict_matches_index("S0_VERDICT", index_html)
    assert "the rate plan is right" in published, (
        f"S0_VERDICT no longer carries the tariff claim this case guards: {published}")

    runner_up = min((r for r in priced if r["plan"] != cheapest),
                    key=lambda r: float(r["total"]))
    cheapest_total = next(r["total"] for r in priced if r["plan"] == cheapest)
    gap = float(runner_up["total"]) - float(cheapest_total)
    results = {}

    # 1. Another plan really is cheaper: the household sits on the runner-up.
    for token in ("S0_VERDICT", "S3_VERDICT"):
        with _stub_plan(runner_up["plan"], provider):
            try:
                value = rt.resolve_token(token)
                raise AssertionError(
                    f"{token} called {runner_up['plan']} the right plan while "
                    f"{cheapest} prices ${gap:,.2f} lower: {value}")
            except SystemExit as e:
                assert token in str(e), e
                assert cheapest in str(e), (
                    f"{token}'s refusal does not name the plan that actually won: {e}")
        results[f"{token} vs a cheaper plan"] = "refused"

    # 2. An exact tie: the runner-up is repriced to the cheapest total, so the
    #    household's plan is joint-cheapest and "the cheapest plan" is false.
    for token in ("S0_VERDICT", "S3_VERDICT"):
        with _swapped(runner_up, "total", cheapest_total), \
             _stub_plan(cheapest, provider):
            try:
                value = rt.resolve_token(token)
                raise AssertionError(
                    f"{token} called {cheapest} THE cheapest plan while "
                    f"{runner_up['plan']} ties it at ${float(cheapest_total):,.2f}: {value}")
            except SystemExit as e:
                assert token in str(e), e
        results[f"{token} at a tie"] = "refused"

    assert float(runner_up["total"]) != float(cheapest_total), (
        "the substituted plan total leaked out of this case")
    with _stub_plan(cheapest, provider):
        assert rt.resolve_token("S0_VERDICT") == published, (
            "S0_VERDICT no longer renders its published line after the substitutions")
    return (f"S0_VERDICT and S3_VERDICT both fail closed when {runner_up['plan']} prices "
            f"below or ties {cheapest} on the {provider} column, and S0's published line "
            "round-trips into index.html ungated")


@case
def case_plan_lead_tokens_refuse_a_lead_the_matrix_does_not_show():
    """The same shape one artifact over. S4_VERDICT_SHORT says the battery
    "widens EV-TOU-5's lead over" the runner-up and PLAN_MARGIN_VS_RUNNER_UP
    publishes that lead as a dollar figure; both take the difference without
    checking its sign. A runner-up that priced at or below the household's
    plan would print a negative lead and a battery that "widens" it, so the
    shared _runner_up() helper refuses instead.

    Ranked on battery_plan_matrix.json's own no-battery column -- the numbers
    these two sentences actually quote -- not on plan_results.csv."""
    plans = rt._json("battery_plan_matrix.json")["plans"]
    ordered = sorted(plans, key=lambda k: plans[k]["no_battery"])
    best, runner_up = ordered[0], ordered[1]
    provider = (rt._generation_provider_short(rt.CTX) if rt.hh.PATH.is_file()
                else "CEA")
    with _stub_plan(best, provider):
        lead = plans[runner_up]["no_battery"] - plans[best]["no_battery"]
        assert lead > 0, f"data/battery_plan_matrix.json shows no lead for {best}"
        assert rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP") == rt._usd0(lead)
        for beaten in (plans[best]["no_battery"], plans[best]["no_battery"] - 500):
            with _swapped(plans[runner_up], "no_battery", beaten):
                for token in ("PLAN_MARGIN_VS_RUNNER_UP", "S4_VERDICT_SHORT"):
                    try:
                        value = rt.resolve_token(token)
                        raise AssertionError(
                            f"{token} published a lead over {runner_up} while "
                            f"{runner_up} prices ${beaten:,} against {best}'s "
                            f"${plans[best]['no_battery']:,}: {value}")
                    except SystemExit as e:
                        assert token in str(e), e
        assert rt.resolve_token("PLAN_MARGIN_VS_RUNNER_UP") == rt._usd0(lead), (
            "the substituted no-battery total leaked out of this case")
    return (f"PLAN_MARGIN_VS_RUNNER_UP and S4_VERDICT_SHORT refuse to call the gap to "
            f"{runner_up} a lead when it ties or beats {best} (live lead ${lead:,})")


@case
def case_free_fix_verdicts_refuse_to_call_a_non_saving_a_saving():
    """Three sentences sell the same free move: section 0 ("saves a modeled
    $X/yr whatever you buy"), section 7 ("is worth a modeled $X/yr") and the
    Monday appendix ("captures the free savings", which quotes no figure at
    all and so can never be caught by reading the rendered line). At X <= 0
    the shift saves nothing: the first two would print a negative figure
    straight after the word "saves", and the third would send the reader
    after a loss.

    Driven from BOTH artifacts the three sentences quote -- behavior_rebuild's
    scenarios.a.saved and package_results' packages.LOW.savings_yr -- because
    the guard they share checks both, so a regression that re-pointed one
    sentence at the other artifact still cannot slip a non-saving through.
    Ungated (the plan answers S0 needs are stubbed)."""
    provider, cheapest, _priced = _plan_ranking_inputs()
    scenario = rt._json("behavior_rebuild.json")["scenarios"]["a"]
    low = rt._json("package_results.json")["packages"]["LOW"]
    live = {"behavior_rebuild:scenarios.a.saved": scenario["saved"],
            "package_results:LOW.savings_yr": low["savings_yr"]}
    for label, value in live.items():
        assert value > 0, (
            f"data/{label} is already {value}/yr; the free-fix sentences in sections "
            "0, 7 and 15 are no longer true and should already be failing closed")

    tokens = ("S0_VERDICT", "S7_VERDICT", "S15_VERDICT")
    with _stub_plan(cheapest, provider):
        published = {t: rt.resolve_token(t) for t in tokens}
        for node, key in ((scenario, "saved"), (low, "savings_yr")):
            for bad in (0, -400):
                with _swapped(node, key, bad):
                    for token in tokens:
                        try:
                            rendered = rt.resolve_token(token)
                            raise AssertionError(
                                f"{token} sold the free EV-charging fix while "
                                f"{key} is {bad}/yr: {rendered}")
                        except SystemExit as e:
                            assert token in str(e), e
        for token in tokens:
            assert rt.resolve_token(token) == published[token], (
                f"the substituted free-fix saving leaked out of this case ({token})")
    return ("S0_VERDICT, S7_VERDICT and S15_VERDICT all fail closed at a zero or "
            "negative free-fix saving in either artifact (live: "
            + ", ".join(f"{k.split(':')[0]} ${v:,.0f}" for k, v in live.items()) + ")")


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
    exported = rt._json("report_data.json")["totals"]["exp"]
    assert f"{production:,.0f} kWh" in value, (
        f"S2_VERDICT dropped the measured production total: {value}")
    assert "kWh/kW" in value, f"S2_VERDICT dropped the measured specific yield: {value}"
    assert f"{round(exported / production * 100)}% of that output exports at midday" \
        in value, f"S2_VERDICT dropped the export-timing conclusion: {value}"
    _assert_within_density_cap("S2_VERDICT", value, "the published branch")
    return ("S2_VERDICT keeps the measured yield and the export-timing conclusion and "
            "passes no health verdict")


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
