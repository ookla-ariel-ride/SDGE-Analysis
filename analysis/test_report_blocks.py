#!/usr/bin/env python3
"""Tests for report_blocks.py (issue #39 part 5): the TODO-block
classification map and its mechanical data-class row builders.

Follows this repo's established CASES/@case/main() convention (see e.g.
test_report_tokens.py, test_llm_providers.py).

Run from the repo root:  ./.venv/bin/python analysis/test_report_blocks.py
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import report_blocks as rb   # noqa: E402
import report_tokens as rt   # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """issue #102: this file had NO private-data gate at all -- the cases
    that touch the real archive raised household.py's own fail-closed
    SystemExit on a checkout without private/household.yaml (a CI runner,
    matching test_report_tokens.py's own _require_household() convention),
    which main()'s `except Exception` cannot catch (SystemExit is a
    BaseException), aborting the whole file before any later case could run
    or a clean SKIP could print."""


def _require_household():
    if not rt.hh.PATH.is_file():
        raise SkipCase(f"needs private/household.yaml ({rt.hh.PATH}), which this "
                       "checkout does not have")


# ---------------------------------------------------------------------------
# AC: every one of the (currently 105) TODO blocks the template actually
# contains is classified, and the classification covers the template EXACTLY
# -- re-parsed fresh, not against a hardcoded count.
# ---------------------------------------------------------------------------
@case
def case_classification_covers_a_fresh_parse_exactly():
    blocks = rb.validate_classification()
    parsed_ids = {b.id for b in blocks}
    assert parsed_ids == set(rb.CLASSIFICATION), (
        parsed_ids ^ set(rb.CLASSIFICATION))
    return f"CLASSIFICATION covers all {len(blocks)} freshly-parsed TODO blocks exactly"


@case
def case_classification_excludes_the_top_of_file_instructions_block():
    html = rt.TEMPLATE.read_text()
    import re
    first_comment = re.search(r"<!--.*?-->", html, re.S)
    assert first_comment, "template has no HTML comments at all"
    assert "TODO" in first_comment.group(0), (
        "fixture assumption broken: the top-of-file comment no longer mentions TODO")
    blocks = rb.parse_todo_blocks(html)
    assert not any(b.start == first_comment.start() for b in blocks), (
        "the top-of-file instructions comment was parsed as an actionable TODO block")
    return "the top-of-file instructions comment is excluded from the parsed block set"


@case
def case_every_block_id_is_unique():
    blocks = rb.parse_todo_blocks()
    ids = [b.id for b in blocks]
    assert len(ids) == len(set(ids)), f"duplicate block ids: " \
        f"{sorted({i for i in ids if ids.count(i) > 1})}"
    return f"all {len(ids)} parsed block ids are unique"


@case
def case_fails_closed_on_a_synthetic_unclassified_block():
    fake_html = (
        '<h2 id="s0">Bottom line</h2>\n'
        '<!-- TODO: this block does not exist in the real CLASSIFICATION map -->\n')
    try:
        rb.validate_classification(fake_html)
        raise AssertionError("validate_classification accepted an unclassified block")
    except SystemExit as e:
        assert "s0#1" in str(e), e
    return "validate_classification() exits naming a block with no CLASSIFICATION entry"


@case
def case_fails_closed_on_a_classification_entry_for_a_removed_block():
    # A template with zero TODO blocks: every declared CLASSIFICATION id is
    # now "extra" and must be named.
    fake_html = ("<!-- top-of-file instructions comment, no TODO word, just a stand-in -->\n"
                "<h2 id=\"s0\">Bottom line</h2>\n<p>no TODO comments here at all</p>\n")
    try:
        rb.validate_classification(fake_html)
        raise AssertionError("validate_classification accepted a template with no TODO "
                             "blocks at all without naming the now-stale CLASSIFICATION")
    except SystemExit as e:
        assert "no longer exist" in str(e), e
    return "validate_classification() exits naming stale CLASSIFICATION entries"


@case
def case_every_classification_value_is_one_of_the_three_kinds():
    bad = {bid: k for bid, k in rb.CLASSIFICATION.items() if k not in ("prose", "data", "human")}
    assert not bad, bad
    return "every CLASSIFICATION value is prose, data, or human"


@case
def case_every_human_block_has_a_reason():
    human_ids = {bid for bid, k in rb.CLASSIFICATION.items() if k == "human"}
    assert human_ids, "no human-classified blocks at all -- fixture assumption broken"
    missing = human_ids - set(rb.HUMAN_REASONS)
    assert not missing, f"human blocks with no HUMAN_REASONS entry: {sorted(missing)}"
    extra = set(rb.HUMAN_REASONS) - human_ids
    assert not extra, f"HUMAN_REASONS names non-human-classified blocks: {sorted(extra)}"
    return f"all {len(human_ids)} human-classified blocks have a stated reason"


@case
def case_every_data_block_has_a_builder():
    data_ids = {bid for bid, k in rb.CLASSIFICATION.items() if k == "data"}
    assert data_ids, "no data-classified blocks at all -- fixture assumption broken"
    missing = data_ids - set(rb.DATA_BUILDERS)
    assert not missing, f"data blocks with no DATA_BUILDERS entry: {sorted(missing)}"
    extra = set(rb.DATA_BUILDERS) - data_ids
    assert not extra, f"DATA_BUILDERS names non-data-classified blocks: {sorted(extra)}"
    return f"all {len(data_ids)} data-classified blocks have a builder function"


# ---------------------------------------------------------------------------
# AC (issue's own default read): hardware price quotes, incentive/rebate
# status, and the provenance review claim are human by default.
# ---------------------------------------------------------------------------
@case
def case_incentive_status_block_is_human():
    assert rb.CLASSIFICATION["s6#1"] == "human"
    assert "INCENTIVE_STATUS" in rb.HUMAN_REASONS["s6#1"]
    return "the incentive-status framing block (s6#1) is classified human"


@case
def case_hardware_pricing_block_is_human():
    assert rb.CLASSIFICATION["s6#8"] == "human"
    return "the 'get quotes' hardware-pricing block (s6#8) is classified human"


@case
def case_the_electrification_gap_blocks_a_heading_not_the_block_under_it():
    """ISSUE #132. s10#4 used to be human "because" the section's heading
    carries {{ELECTRIFICATION_VERDICT_SHORT}}, a KNOWN_GAPS token. Those are
    two different things and conflating them cost the block its evidence: the
    HEADING's one-line "which appliance pencils" verdict needs both appliances
    costed on one basis and stays a gap, while the BLOCK below it asks for the
    appliance-by-appliance math, which data/all_electric_endgame.json and
    data/heat_pump_conversion.json both carry.

    So the two are asserted apart -- the gap token still fails, the block no
    longer inherits its failure."""
    assert rb.CLASSIFICATION["s10#4"] == "prose", (
        "s10#4 asks for the electrification math, which is now artifact-backed")
    assert "s10#4" not in rb.HUMAN_REASONS
    assert rt.TOKENS["ELECTRIFICATION_VERDICT_SHORT"]["kind"] == "gap", (
        "fixture assumption broken: the heading token is no longer a declared gap")
    assert "ELECTRIFICATION_VERDICT_SHORT" in rb.LIVE_GAP_TOKENS, (
        "the heading token must still be a LIVE gap needing a human override")
    return ("the electrification HEADING token is still a live gap; the block under it "
            "(s10#4) is prose, backed by all_electric_endgame.json / heat_pump_conversion.json")


# ---------------------------------------------------------------------------
# AC: KNOWN_GAPS tokens appearing in LIVE (non-comment) template markup are
# identified -- these fail resolve_token() regardless of TODO classification.
# ---------------------------------------------------------------------------
@case
def case_live_gap_tokens_are_a_subset_of_known_gaps():
    gap_tokens = {n for n, s in rt.TOKENS.items() if s.get("kind") == "gap"}
    assert rb.LIVE_GAP_TOKENS <= gap_tokens
    assert rb.LIVE_GAP_TOKENS, "expected at least one live gap token in the real template"
    live, _ = rt.template_tokens()
    assert rb.LIVE_GAP_TOKENS <= live
    return f"LIVE_GAP_TOKENS ({sorted(rb.LIVE_GAP_TOKENS)}) are gap tokens appearing live"


# ---------------------------------------------------------------------------
# AC: every data builder actually runs against the real committed archive and
# returns HTML (row builders return at least one <tr>; vestigial/label
# builders may legitimately return "" or a short fixed string).
# ---------------------------------------------------------------------------
_ROW_BUILDER_IDS = {"s3#2", "s4#2", "s6#2", "s6#5", "s6#7", "s11#2", "s12#2",
                    "s13#4", "s13#12"}


@case
def case_row_builders_produce_at_least_one_row_against_the_real_archive():
    _require_household()
    for bid in sorted(_ROW_BUILDER_IDS):
        out = rb.DATA_BUILDERS[bid]()
        assert "<tr>" in out, f"{bid}: no <tr> in builder output: {out!r}"
    return f"all {len(_ROW_BUILDER_IDS)} row-builder blocks produce real table rows"


# ---------------------------------------------------------------------------
# AC (adversarial review pass 2, finding 2): a row builder's ARTIFACT-DERIVED
# free-text cell (a plan name, a battery-config name) must be HTML-escaped,
# matching the same principle generate_report.py's render() already applies
# to {{TOKEN}} substitution. Not exploitable with today's committed data
# (real plan/config names never contain markup) -- this fabricates an evil
# value via monkeypatching report_tokens.py's own cached loaders (the same
# functions report_blocks.py's row builders call) rather than editing a
# committed artifact, and restores the real loader afterward either way.
# ---------------------------------------------------------------------------
@case
def case_row_builders_escape_artifact_derived_free_text():
    evil = "<script>alert(1)</script> & Co"
    original_json = rt._json

    def fake_json(name):
        if name == "battery_sim.json":
            real = original_json(name)
            fabricated = [dict(r, config=evil) for r in real
                         if r["config"] != "1x Tesla Powerwall 3"]
            return fabricated[:1]
        return original_json(name)

    rt._json = fake_json
    try:
        out = rb.DATA_BUILDERS["s6#2"]()
    finally:
        rt._json = original_json
    assert "<script>alert(1)</script>" not in out, out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out, out
    assert "&amp; Co" in out, out
    return "s6#2's row builder (battery_sim.json config names) HTML-escapes a fabricated evil value"


@case
def case_hardcoded_endurance_labels_are_not_double_escaped():
    """_ENDURANCE_LABELS is a hardcoded Python dict (trusted source, not
    artifact data) that intentionally contains a literal '&times;' entity --
    _esc() must never be applied to it, or blanket-escaping would corrupt it
    into '&amp;times;'."""
    out = rb.DATA_BUILDERS["s6#7"]()
    assert "&times;" in out, out
    assert "&amp;times;" not in out, out
    return "the hardcoded '&times;' entity in _ENDURANCE_LABELS survives un-double-escaped"


@case
def case_vestigial_blocks_resolve_to_empty_string():
    vestigial = {"top#1", "top#2", "top#3", "s9#1", "s12#1", "s13#1", "s15#1", "s14#15"}
    for bid in sorted(vestigial):
        assert rb.DATA_BUILDERS[bid]() == "", f"{bid}: expected empty, got {rb.DATA_BUILDERS[bid]()!r}"
    return f"all {len(vestigial)} vestigial data blocks resolve to the empty string"


@case
def case_s15_metric_label_is_a_fixed_non_empty_string():
    label = rb.DATA_BUILDERS["s15#4"]()
    assert label and "{{" not in label and not any(c.isdigit() for c in label)
    return f"the Monday-appendix metric label resolves to a fixed digit-free string: {label!r}"


@case
def case_price_map_rows_cover_the_five_remaining_season_period_combos():
    out = rb.DATA_BUILDERS["s13#12"]()
    assert out.count("<tr>") == 5, out
    assert "{{PEAK_WINDOW}}" in out, "the winter on-peak row should reference {{PEAK_WINDOW}}"
    return "the price-map table's five remaining rows are present, including the token reference"


@case
def case_plan_and_battery_plan_rows_exclude_the_current_best_plan():
    _require_household()
    current = rt.hh1("household.plan")
    s3_rows = rb.DATA_BUILDERS["s3#2"]()
    s4_rows = rb.DATA_BUILDERS["s4#2"]()
    assert f">{current}<" not in s3_rows.replace(" ✓ current", ""), (
        f"s3 plan table's extra rows should exclude the current plan {current!r}")
    assert f">{current}<" not in s4_rows, (
        f"s4 battery-plan table's extra rows should exclude the current plan {current!r}")
    return f"the s3/s4 additional-row tables both exclude the current plan ({current!r})"


@case
def case_scope_tokens_for_block_include_tokens_named_in_its_own_text():
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    b = blocks["s0#1"]
    assert "BEST_PLAN" in b.text, "fixture assumption broken: s0#1 no longer mentions BEST_PLAN"
    scope = rb.scope_tokens_for_block(html, b)
    assert "BEST_PLAN" in scope
    return "scope_tokens_for_block includes tokens named inside the block's own TODO text"


@case
def case_scope_tokens_for_block_include_tokens_live_in_the_same_section():
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    b = blocks["s0#3"]   # baseload item -- mentions no token itself
    scope = rb.scope_tokens_for_block(html, b)
    # s0's live markup elsewhere (the cards, the rec box) uses BEST_PLAN,
    # ACTUAL_ANNUAL_BILL etc. -- section-wide scoping should surface them even
    # though s0#3's own text names none.
    assert "ACTUAL_ANNUAL_BILL" in scope, scope
    return "scope_tokens_for_block also includes tokens live elsewhere in the same section"


# ---------------------------------------------------------------------------
# ISSUE #132, AC5. THE MAP MUST NOT BE ABLE TO ROT AGAIN.
#
# Eleven blocks sat marked "human" for reasons that had stopped being true,
# and nothing could tell: the justifications were prose, and prose does not
# fail. Eight of them said "no report_tokens.py entry", which tests this
# pipeline's own token inventory rather than the evidence -- so every artifact
# added after the map was written left a stale entry behind it.
#
# The three cases below turn each surviving justification into a fact:
#   * the words that caused the rot cannot come back;
#   * a cited gap token must still BE a gap, and must belong to the block that
#     cites it;
#   * a human block must actually have something in its own scope that fails,
#     unless it declares an outside fact -- and that declaration is a named
#     entry, not an inference from silence.
# ---------------------------------------------------------------------------
_TOKEN_INVENTORY_EXCUSE = "report_tokens.py entry"


@case
def case_no_human_reason_rests_on_the_token_inventory():
    """AC5, the systematic cause. "No report_tokens.py entry" is a statement
    about what this module's author has written, not about what the household's
    archive holds -- and a block whose evidence exists but is unwired is a
    token to write, never a blocker. Eight of fourteen reasons said it."""
    offenders = {bid: why for bid, why in rb.HUMAN_REASONS.items()
                 if _TOKEN_INVENTORY_EXCUSE in why}
    assert not offenders, (
        f"{len(offenders)} HUMAN_REASONS entry/entries justify themselves by this "
        f"pipeline's own token inventory rather than by the evidence: {sorted(offenders)}. "
        "State which committed artifact or KNOWN_GAPS token is missing; if an artifact "
        "answers the block, write the token and reclassify it instead.")
    return (f"none of the {len(rb.HUMAN_REASONS)} human reasons rests on "
            f"{_TOKEN_INVENTORY_EXCUSE!r}")


@case
def case_every_cited_gap_token_is_still_a_gap_in_this_blocks_own_scope():
    """AC5, signal (b). A human block justified by a KNOWN_GAPS token stops
    being justified the moment that token gets a source. Checked three ways so
    none of them can be satisfied by accident: the token is declared, it is
    still kind="gap", and it is in THIS block's own scope -- a justification
    cannot borrow a gap from another section."""
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    checked = 0
    for bid, blocker in sorted(rb.HUMAN_BLOCKERS.items()):
        for token in blocker.get("gap_tokens", ()):
            spec = rt.TOKENS.get(token)
            assert spec is not None, (
                f"{bid} is human because of {{{{{token}}}}}, which is not a "
                "report_tokens.TOKENS entry at all")
            assert spec.get("kind") == "gap", (
                f"{bid} is human because {{{{{token}}}}} has no committed source, but that "
                f"token is now kind={spec.get('kind')!r} -- it resolves, so the "
                "justification no longer holds. Reclassify the block or name a real "
                "blocker.")
            assert token in rb.scope_tokens_for_block(html, blocks[bid]), (
                f"{bid} cites {{{{{token}}}}} as its blocker, but that token is not in the "
                "block's own scope -- a block cannot be blocked by a gap it never sees")
            assert token in rb.HUMAN_REASONS[bid], (
                f"{bid}'s HUMAN_REASONS prose does not name its own declared blocker "
                f"{token}, so the two can drift apart unnoticed")
            checked += 1
    assert checked, "no human block cites a gap token -- fixture assumption broken"
    return f"all {checked} cited gap token(s) are still gaps inside their own block's scope"


@case
def case_a_human_block_whose_scope_fully_resolves_must_declare_an_outside_fact():
    """AC5, signal (a). If every token a human block can see resolves, the
    "this repo cannot source it" claim has nothing left holding it up -- either
    the block should be prose, or its blocker is something no token represents.

    s6#8 is the legitimate second case and is the reason this is not simply
    "every human block must have a failing token": it asks for an installed
    price quote, which no token could ever fail on because no token exists for
    a fact this repo does not collect. That is declared in HUMAN_BLOCKERS as an
    outside_fact -- named, with what would end it -- rather than allowed
    through by a test that quietly tolerates the case."""
    _require_household()
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    unjustified, by_failure = [], {}
    for bid, kind in sorted(rb.CLASSIFICATION.items()):
        if kind != "human":
            continue
        blocker = rb.HUMAN_BLOCKERS[bid]
        if blocker.get("outside_fact"):
            continue
        failing = []
        for token in sorted(rb.scope_tokens_for_block(html, blocks[bid])):
            if token not in rt.TOKENS:
                continue
            try:
                rt.resolve_token(token, rt.TOKENS[token])
            except SystemExit:
                failing.append(token)
        if failing:
            by_failure[bid] = failing
        else:
            unjustified.append(bid)
    assert not unjustified, (
        f"human block(s) {unjustified} have no failing token in their own scope and declare "
        "no outside_fact -- every figure they can see resolves, so nothing supports leaving "
        "them blocked. Reclassify them prose, or name the outside fact in HUMAN_BLOCKERS.")
    outside = sorted(b for b, v in rb.HUMAN_BLOCKERS.items() if v.get("outside_fact"))
    return (f"every human block with no outside_fact has a failing scope token "
            f"({ {k: v for k, v in by_failure.items()} }); {outside} are blocked on a fact "
            "this repo does not collect and say so")


@case
def case_human_blockers_covers_exactly_the_human_blocks():
    human_ids = {bid for bid, k in rb.CLASSIFICATION.items() if k == "human"}
    assert set(rb.HUMAN_BLOCKERS) == human_ids, (
        f"HUMAN_BLOCKERS and the human blocks disagree: "
        f"{set(rb.HUMAN_BLOCKERS) ^ human_ids}")
    return f"HUMAN_BLOCKERS covers exactly the {len(human_ids)} human-classified blocks"


# ---------------------------------------------------------------------------
# ISSUE #132, AC2. Every block that stopped being human must be FILLABLE: the
# figures its TODO now names have to be in that block's own scope (which is
# what generate_report.py hands the prose pass, and what its output validator
# restricts a fragment to) and every one of them has to resolve.
#
# This is the precondition, asserted mechanically; the fill itself needs a
# model and is not run here. Naming the tokens per block rather than deriving
# them from the template is deliberate -- a template edit that drops a token
# mention would otherwise silently shrink the scope and pass.
# ---------------------------------------------------------------------------
_RECLASSIFIED_FIGURES = {
    "s2#3": ("DAILY_PRODUCTION_MEAN", "DAILY_PRODUCTION_BEST", "DAILY_PRODUCTION_WORST"),
    "s2#4": ("DEGRADATION_NAIVE_RANGE",),
    "s9#2": ("ARRAY_EFFICIENCY_SERIES", "DEGRADATION_NAIVE_RANGE",
             "DEGRADATION_WEATHER_CAVEAT"),
    "s9#3": ("PV_PEAK_OBSERVED", "PV_PEAK_HEADROOM", "PEAK_POWER_MULTIYEAR",
             "AC_CEILING_KW", "PV_PEAK_BASIS"),
    "s9#4": ("COOLING_BASE_LOAD", "COOLING_KWH_PER_CDD", "COOLING_REGRESSION_R2",
             "ANNUAL_COOLING_KWH", "COOLING_SENSITIVITY_PER_100_CDD",
             "PRECOOL_SHIFT_VALUE", "SETPOINT_VALUE"),
    "s9#5": ("EV_SESSION_COUNT", "EV_ANNUAL_KWH", "EV_AVG_SESSION_KWH",
             "EV_WINDOW_DECOMPOSITION", "EV_SOP_COMPLIANCE_PCT",
             "EV_FIX_SAVINGS_100", "EV_FIX_SAVINGS_80", "EV_DETECTION_BASIS"),
    "s10#4": ("ELECTRIFICATION_SEQUENCE", "HPWH_INSTALL_COST", "HPWH_NET_SAVINGS",
              "HPWH_PAYBACK", "HEAT_PUMP_INSTALL_COST", "HEAT_PUMP_PAYBACK",
              "ELECTRIFICATION_COMBINED_PAYBACK", "ELECTRIFICATION_INCENTIVES",
              "HPWH_SHARE_CAVEAT", "HPWH_PAYBACK_SENSITIVITY", "HPWH_SAVINGS_BOUND",
              "HEAT_PUMP_COST_BASIS", "HPWH_COST_BASIS",
              "HEAT_PUMP_MARGINAL_INSTALL_COST", "HEAT_PUMP_MARGINAL_PAYBACK",
              "ELECTRIFICATION_METER_REMOVAL_CAVEAT"),
    "s12#5": ("CLEANING_BEST_MONTH", "CLEANING_SINGLE_VALUE_RANGE",
              "CLEANING_SECOND_MARGINAL_RANGE", "CLEANING_PRICE",
              "MIDDAY_MARGINAL_VALUE_RANGE"),
    "s13#8": ("SPREAD_TREND_SUMMER", "SPREAD_TREND_WINTER"),
    "s13#9": ("BATTERY_ON_MEASURED_SPREAD", "SPREAD_BATTERY_SEED_SAVING",
              "PAYBACK_AT_HISTORICAL_ESCALATION", "NPV_AT_HISTORICAL_ESCALATION"),
    "s13#11": ("NIGHT_FLOOR_MEDIAN", "NIGHT_FLOOR_SPREAD", "NIGHT_FLOOR_SAMPLE",
               "NIGHT_FLOOR_ANNUAL_KWH", "NIGHT_FLOOR_ANNUAL_COST",
               "NIGHT_FLOOR_CYCLING", "NIGHT_FLOOR_SEASONALITY",
               "NIGHT_FLOOR_PRICING_BASIS", "PHANTOM_METHOD_DISCREPANCY"),
}

# ---------------------------------------------------------------------------
# ISSUE #132, ADVERSARIAL REVIEW PASS 1. A FIGURE MAY NOT BE IN SCOPE WITHOUT
# THE QUALIFIER THAT ARTIFACT SAYS IS NEEDED TO READ IT.
#
# The first version of s10#4's tokens put a 30.8-year water-heater payback and
# a "cost-effective" appliance order into the block's scope and left behind the
# caveat sitting in the same artifact object: that every one of those figures
# is the pure 100%-water-heater computation, NOT VERIFIED against this
# household's actual gas appliance mix. A prose block sees its scoped token
# values and its own TODO text and nothing else, so the omission did not make
# the block cautious -- it made the block UNABLE to be cautious, and the
# figures would have published as household fact (CLAUDE.md section 0).
#
# The pairs below are the fix as a property: each names a figure token and the
# qualifier token that must share its scope. They are pairs and not a flat
# list because the failure mode is asymmetric -- a qualifier alone is harmless,
# a figure alone is the defect -- and naming the pair makes the failure message
# say which figure went unqualified.
# ---------------------------------------------------------------------------
_FIGURE_NEEDS_QUALIFIER = {
    "s10#4": [
        # Every water-heater figure inherits water_heater_conversion's own
        # not_verified_caveat, and the sequence inherits it too by derivation
        # (sequencing_and_paybacks.not_verified_caveat says so in as many words).
        ("HPWH_PAYBACK", "HPWH_SHARE_CAVEAT"),
        ("HPWH_PAYBACK", "HPWH_PAYBACK_SENSITIVITY"),
        ("HPWH_NET_SAVINGS", "HPWH_SHARE_CAVEAT"),
        ("HPWH_NET_SAVINGS", "HPWH_SAVINGS_BOUND"),
        ("ELECTRIFICATION_COMBINED_PAYBACK", "HPWH_SHARE_CAVEAT"),
        # install_cost.note prices an example system larger than this house's.
        ("HEAT_PUMP_INSTALL_COST", "HEAT_PUMP_COST_BASIS"),
        # ... and its water-heater companion, which the artifact's own note
        # exists to contrast with (issue #132, Codex pass 3).
        ("HPWH_INSTALL_COST", "HPWH_COST_BASIS"),
        # The whole-transition payback is not a confirmed meter removal.
        ("ELECTRIFICATION_COMBINED_PAYBACK", "ELECTRIFICATION_METER_REMOVAL_CAVEAT"),
    ],
    # The three maxima measure different things (an hourly mean, a lower bound,
    # a five-minute sample); corroboration_reading and
    # why_not_the_observed_maximum are what make them readable as a clipping
    # answer rather than three comparable numbers.
    "s9#3": [("PV_PEAK_OBSERVED", "PV_PEAK_BASIS"),
             ("PV_PEAK_HEADROOM", "PV_PEAK_BASIS")],
    # A session count is the output of a three-threshold rule, and a second
    # committed detector reaches a different number on the same series.
    "s9#5": [("EV_SESSION_COUNT", "EV_DETECTION_BASIS"),
             ("EV_ANNUAL_KWH", "EV_DETECTION_BASIS")],
    # Both annual figures extend a four-hour measurement across 8,760 hours,
    # which quiet_night_floor.py's own confidence_labels call modeled.
    "s13#11": [("NIGHT_FLOOR_ANNUAL_KWH", "NIGHT_FLOOR_PRICING_BASIS"),
               ("NIGHT_FLOOR_ANNUAL_COST", "NIGHT_FLOOR_PRICING_BASIS"),
               # Section 9 publishes a different figure for this same load, and
               # CLAUDE.md section 0 forbids carrying both silently.
               ("NIGHT_FLOOR_ANNUAL_KWH", "PHANTOM_METHOD_DISCREPANCY"),
               ("NIGHT_FLOOR_ANNUAL_COST", "PHANTOM_METHOD_DISCREPANCY")],
}


@case
def case_the_cleaning_caveat_compares_against_a_range_not_a_summer_import_rate():
    """ISSUE #132, CODEX PASS 2. s12#5's caveat compared the cadence model's
    pricing against {{SUPER_OFF_PEAK_RATE}} and called it "what a marginal
    midday kWh earns on today's tariff". That token is rates.allin("S", "sop")
    -- the SUMMER SUPER-OFF-PEAK IMPORT rate -- so the instruction told the
    model to state as fact a single figure that is wrong for every exported
    kWh (which earns the export credit, not an import it never offsets) and
    silent about winter.

    The conclusion was right and had to survive: the cadence model prices
    recovered kWh above ANY of those readings, so the upper-bound framing
    holds. What changed is the comparator.

    Four properties, so neither half can regress alone: the block sees the
    range, the range really does span both sides and both seasons, the old
    single-rate comparator is gone from this block, and the upper-bound
    instruction is still there."""
    _require_household()
    html = rt.TEMPLATE.read_text()
    block = {b.id: b for b in rb.parse_todo_blocks(html)}["s12#5"]
    scope = rb.scope_tokens_for_block(html, block)

    assert "MIDDAY_MARGINAL_VALUE_RANGE" in scope, (
        "s12#5 cannot cite the midday-value range it is told to compare against")
    rendered = rt.resolve_token("MIDDAY_MARGINAL_VALUE_RANGE")
    assert rendered.strip(), "the midday-value range resolved to nothing"

    # It must be a RANGE, and must not be the bare summer import rate the old
    # comparator used -- both ends are checked against rates.py itself.
    summer_import = rt._cents1(rt.R.allin("S", "sop"))
    # Numeric min/max, THEN formatted: sorting the rendered strings compares
    # "12.5¢" against "7.6¢" lexicographically and picks the wrong ends.
    cells = [rt.R.credit(s, "sop") for s in ("S", "W")] + \
            [rt.R.allin(s, "sop") for s in ("S", "W")]
    assert "–" in rendered, f"the comparator is not a range: {rendered}"
    assert rendered.split("/kWh")[0].strip() != summer_import, (
        f"the comparator collapsed back to the summer import rate: {rendered}")
    for end in (rt._cents1(min(cells)), rt._cents1(max(cells))):
        assert end in rendered, (
            f"the range does not span both sides and both seasons -- {end} missing "
            f"from {rendered!r} (cells: {[rt._cents1(c) for c in cells]})")

    # The old comparator must not be back in THIS block's own instruction.
    named = rb.tokens_mentioned(block.text)
    assert "SUPER_OFF_PEAK_RATE" not in named, (
        "s12#5's TODO names SUPER_OFF_PEAK_RATE again -- a summer-only IMPORT rate "
        "cannot stand for what a marginal midday kWh earns")
    assert "MIDDAY_MARGINAL_VALUE_RANGE" in named, (
        "s12#5's TODO no longer names the range it must compare against")

    # And the caveat itself survives.
    assert "upper bound" in block.text, (
        "s12#5's TODO lost the upper-bound caveat, which the corrected comparator "
        "still supports")
    return (f"s12#5 compares against {rendered!r} -- a both-seasons, both-sides range "
            "from rates.py -- and keeps its upper-bound caveat")


# ---------------------------------------------------------------------------
# ISSUE #132, CODEX PASS 3. THE PAIR LIST CANNOT BE THE ONLY GUARD.
#
# case_no_figure_reaches_a_blocks_scope_without_its_qualifier below carries
# hand-listed pairs, and it PASSED while HPWH_INSTALL_COST sat in s10#4's scope
# with no qualifier -- because nobody added that pair. A hand-maintained list
# of what needs guarding always lags the code it guards; that is the same shape
# as the HUMAN_REASONS rot this whole issue exists to fix, one level up.
#
# So the CANDIDATES are derived, and the list becomes a ledger of exemptions.
# The derivation, which needs no declaration to be right:
#
#   1. For every token in a block's scope, poison each numeric leaf of every
#      artifact it opens and see whether its render moves. That yields the set
#      of artifact OBJECTS the block actually reads numbers out of -- observed,
#      not declared, for the reason report_tokens' `sources` prose is not
#      trusted by the poison sweep either.
#   2. In each such object, any long string under a qualifier-shaped key
#      (note / caveat / basis / not_verified / not_determined / sensitivity /
#      reading / why_not) is a CANDIDATE: the artifact put a condition for
#      reading its own numbers right beside them.
#   3. Blank that string. If no token in the block's scope changes its outcome,
#      nothing in this block can tell the reader about it.
#
# WHAT DEFEATS FULL DERIVATION, stated plainly because it is the reason the
# ledger still exists: a qualifier can be discharged two ways, and only one is
# observable. A token can RENDER the artifact's string (blanking it moves the
# render -- detected, and needs no entry), or it can COMPUTE the same content
# from the artifact's own numbers (DEGRADATION_WEATHER_CAVEAT rebuilds
# clearsky_note's argument out of clearsky_annual_spread_pct and
# peak_to_trough_pct; NIGHT_FLOOR_SAMPLE rebuilds selection_caveat's exclusion
# rate out of quiet_nights and nights_total). The second never touches the
# string, so blanking it proves nothing. Both are legitimate. The derivation
# therefore produces CANDIDATES, not verdicts.
#
# What that buys anyway, and it is the part that matters: the DEFAULT is now
# "must be accounted for". A new artifact field, a new token or a new block
# raises a new candidate, and the case fails until someone writes down which
# bucket it is in. The ledger can no longer fall behind silently -- it can only
# fall behind loudly.
# ---------------------------------------------------------------------------
_QUALIFIER_KEY = re.compile(
    r"caveat|note|basis|not_verified|not_determined|sensitivity|reading|why_not", re.I)
_QUALIFIER_MIN_CHARS = 40


def _token_outcome(name):
    try:
        return ("ok", rt.resolve_token(name, rt.TOKENS[name]))
    except SystemExit as e:
        return ("exit", str(e))
    except BaseException as e:                                   # noqa: BLE001
        return ("raised", type(e).__name__)


def _json_leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _json_leaves(value, path + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _json_leaves(value, path + (i,))
    else:
        yield path, node


def _parent_of(doc, path):
    node = doc
    for key in path[:-1]:
        node = node[key]
    return node, path[-1]


class _record_json_reads:
    def __init__(self, seen):
        self.seen = seen

    def __enter__(self):
        self.real = rt._json

        def json_(name):
            self.seen.add(name)
            return self.real(name)

        rt._json = json_
        return self

    def __exit__(self, *exc):
        rt._json = self.real


def _derive_qualifier_candidates(html, blocks, block_ids):
    """[(block, artifact, object path, qualifier key, covered)] -- see the
    block comment above for what each step establishes."""
    out = []
    for bid in block_ids:
        scope = sorted(t for t in rb.scope_tokens_for_block(html, blocks[bid])
                       if t in rt.TOKENS)
        baseline = {n: _token_outcome(n) for n in scope}
        live = [n for n in scope if baseline[n][0] == "ok"]
        touched = {}
        for name in live:
            seen = set()
            with _record_json_reads(seen):
                _token_outcome(name)
            for who in sorted(seen):
                doc = rt._json(who)
                for path, value in _json_leaves(doc):
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        continue
                    parent, key = _parent_of(doc, path)
                    original = parent[key]
                    parent[key] = float("nan")
                    try:
                        moved = _token_outcome(name) != baseline[name]
                    finally:
                        parent[key] = original
                    if moved:
                        # EVERY ANCESTOR, not just the immediate parent. A
                        # qualifier is routinely written one level up from the
                        # number it qualifies -- install_cost.note sits beside
                        # install_cost.central_usd, but water_heater_conversion's
                        # not_verified_caveat sits a level above the payback it
                        # governs. `path[:-1]` alone made every one of those
                        # invisible, so the mechanism built to replace the hand
                        # list had the hand list's own blind spot (issue #132,
                        # /review finding 9).
                        for depth in range(len(path)):
                            touched.setdefault(who, set()).add(path[:depth])
        for who, objects in sorted(touched.items()):
            doc = rt._json(who)
            for objpath in sorted(objects):
                node = doc
                for key in objpath:
                    node = node[key]
                if not isinstance(node, dict):
                    continue
                for key, value in sorted(node.items()):
                    if not (isinstance(value, str) and _QUALIFIER_KEY.search(key)
                            and len(value) >= _QUALIFIER_MIN_CHARS):
                        continue
                    original = node[key]
                    node[key] = ""
                    try:
                        covered = any(_token_outcome(n) != baseline[n] for n in scope)
                    finally:
                        node[key] = original
                    out.append((bid, who, ".".join(str(k) for k in objpath) or "(root)",
                                key, covered))
    return out


# Every derived candidate the blanking test cannot discharge, and why. A
# `todo_phrase` is checked against the block's own TODO text, so an entry
# claiming the instruction carries the qualification is held to it.
_QUALIFIER_ACCOUNTED = {
    ("s2#4", "gross_import_decomposition.json", "degradation", "clearsky_note"):
        ("the basic-tier line states the weather-corrected rate is not determined and "
         "points at section 9, where the full caveat is rendered",
         "not determined"),
    ("s2#4", "gross_import_decomposition.json", "degradation", "single_event_soiling_basis"):
        ("same: the soiling confounder is section 9's workup, named from here",
         "not determined"),
    ("s9#2", "gross_import_decomposition.json", "degradation", "clearsky_note"):
        ("DEGRADATION_WEATHER_CAVEAT COMPUTES this note's argument from "
         "clearsky_annual_spread_pct and peak_to_trough_pct_2021_2025 rather than "
         "quoting it", None),
    ("s9#2", "gross_import_decomposition.json", "degradation", "single_event_soiling_basis"):
        ("DEGRADATION_WEATHER_CAVEAT computes the same event's size from "
         "single_event_soiling_swing_pct", None),
    ("s10#4", "all_electric_endgame.json",
     "sequencing_and_paybacks.complete_transition_payback", "electric_interaction_note"):
        ("documents a correction the generator has ALREADY applied inside the figure; "
         "it is provenance for the arithmetic, not a condition for reading the result",
         None),
    ("s10#4", "all_electric_endgame.json",
     "sequencing_and_paybacks.complete_transition_payback", "tier_interaction_note"):
        ("same: an applied correction, not an unmet condition", None),
    ("s10#4", "all_electric_endgame.json", "sequencing_and_paybacks.share_robustness",
     "basis"): ("ELECTRIFICATION_SEQUENCE computes this check's own result from "
                "robust_across_named_scenarios and crossover_water_heater_share", None),
    ("s10#4", "all_electric_endgame.json", "sequencing_and_paybacks.share_robustness",
     "crossover_note"): ("ELECTRIFICATION_SEQUENCE renders the crossover share itself",
                         None),
    ("s10#4", "all_electric_endgame.json", "water_heater_conversion", "gas_wh_uef_basis"):
        ("the assumed gas water-heater efficiency; HPWH_SHARE_CAVEAT and "
         "HPWH_SAVINGS_BOUND already carry the two conditions that decide whether the "
         "payback is readable, and the UEF sits inside both", None),
    ("s13#8", "tou_spread.json", "delivery_spread.summer", "ci_basis"):
        ("names which of the two fits gates the verdict; SPREAD_TREND_SUMMER reads that "
         "fit's own escalation_ci95_pct_yr, so the token obeys it rather than needing to "
         "quote it", None),
    ("s13#8", "tou_spread.json", "delivery_spread.winter", "ci_basis"):
        ("same, for SPREAD_TREND_WINTER", None),
    ("s13#11", "quiet_night_floor.json", "night_floor", "selection_caveat"):
        ("NIGHT_FLOOR_SAMPLE computes this caveat's exclusion rate from quiet_nights and "
         "nights_total, and the TODO requires it be kept in view", "exclusion rate"),
    ("s13#11", "quiet_night_floor.json", "pricing", "floor_kw_basis"):
        ("NIGHT_FLOOR_PRICING_BASIS states the constant-across-the-year method this "
         "field describes, from confidence_labels.pricing", None),
    ("s13#11", "quiet_night_floor.json", "pricing.floor_assumption_violations", "note"):
        ("NIGHT_FLOOR_PRICING_BASIS computes this note's direction and magnitude from "
         "usd_dropped_at_export_rate", None),
    # --- raised by the ancestor walk (issue #132, /review finding 9) --------
    ("s10#4", "all_electric_endgame.json", "(root)", "basis"):
        ("states how the two conversions were computed -- provenance for the "
         "arithmetic, not a condition on reading its result", None),
    ("s10#4", "all_electric_endgame.json", "sequencing_and_paybacks", "basis"):
        ("states how the ORDER was chosen; ELECTRIFICATION_SEQUENCE carries that "
         "order's own robustness and crossover share", None),
    ("s10#4", "all_electric_endgame.json", "sequencing_and_paybacks",
     "not_verified_caveat"):
        ("the sequencing copy of water_heater_conversion.not_verified_caveat; "
         "HPWH_SHARE_CAVEAT states it and ELECTRIFICATION_SEQUENCE repeats it inline "
         "('on an unverified water-heater-share assumption')", None),
    ("s10#4", "all_electric_endgame.json", "water_heater_conversion."
     "water_heater_share_sensitivity", "basis"):
        ("HPWH_SHARE_CAVEAT states this basis's own conclusion -- illustrative "
         "scenarios, not a proven bound, true share could be lower still", None),
    ("s10#4", "heat_pump_conversion.json", "(root)", "basis"):
        ("states how the furnace therms were isolated and re-billed -- method "
         "provenance, not a condition on the payback", None),
    ("s9#3", "service_headroom.json", "(root)", "caveat"):
        ("scopes the SERVICE-CAPACITY verdicts to a licensed electrician's permit "
         "calculation; s9#3 publishes measured PV power against the inverter "
         "nameplate, not a capacity verdict", None),
    ("s9#3", "service_headroom.json", "gross_reconstruction.pv_ac_ceiling", "basis"):
        ("names the nameplate as the ceiling's source; AC_CEILING_KW is that same "
         "intake field and PV_PEAK_BASIS states the corroboration's standing", None),
    ("s9#3", "service_headroom.json", "gross_reconstruction.pv_ac_ceiling",
     "why_not_the_observed_maximum"):
        ("PV_PEAK_BASIS states this argument in its own closing clause -- a "
         "quarter-hour can carry more than a quarter of the best full hour", None),
    ("s9#2", "behavior_rebuild.json", "(root)", "note"):
        ("governs the model's ABSOLUTE bill figures ('report DELTAS'); the section 9 "
         "tokens publish session counts and kWh, not bills", None),
    ("s9#3", "behavior_rebuild.json", "(root)", "note"): ("same", None),
    ("s9#4", "behavior_rebuild.json", "(root)", "note"): ("same", None),
    ("s9#5", "behavior_rebuild.json", "(root)", "note"): ("same", None),
    # --- issue #140: the always-on floor, now read from ONE artifact ---------
    # SEC9_TEASER used to open data/deep_results.json for its phantom figures
    # and now opens data/quiet_night_floor.json instead, because that is the
    # only pricing of this load with a committed generator (extra_results'
    # phantom has none; deep_results' prices the energy at a hardcoded flat
    # $0.20/kWh against an hour-weighted all-in import rate of about
    # $0.375/kWh -- issue #172). The teaser is LIVE in section 9's <summary>,
    # so its reads land in the scope of every s9 block, and these four blocks
    # -- degradation, clipping, cooling, the EV report card -- inherit two
    # qualifiers belonging to a figure none of them publishes.
    #
    # ACCOUNTED, not pre-existing, and the difference is the point: this
    # change is what re-pointed the teaser, so the debt is this change's own
    # and is written down where a reason is required rather than parked in the
    # exemption dict below. What discharges it is that the floor's own block
    # (s9#7, the honesty note) carries BOTH qualifiers in its scope -- its
    # TODO names NIGHT_FLOOR_SAMPLE, which rebuilds selection_caveat's
    # exclusion rate, and NIGHT_FLOOR_PRICING_BASIS, which states
    # floor_kw_basis' constant-across-the-year method -- so the section does
    # state them where it develops the figure. What these four blocks see is a
    # one-line summary of a subsection they do not write.
    ("s9#2", "quiet_night_floor.json", "night_floor", "selection_caveat"):
        ("SEC9_TEASER's floor figure, not this block's: the section states the exclusion "
         "rate in s9#7, whose scope carries NIGHT_FLOOR_SAMPLE", None),
    ("s9#2", "quiet_night_floor.json", "pricing", "floor_kw_basis"):
        ("same figure, same block: s9#7's scope carries NIGHT_FLOOR_PRICING_BASIS, which "
         "states the constant-across-the-year method this field describes", None),
    ("s9#3", "quiet_night_floor.json", "night_floor", "selection_caveat"): ("same", None),
    ("s9#3", "quiet_night_floor.json", "pricing", "floor_kw_basis"): ("same", None),
    ("s9#4", "quiet_night_floor.json", "night_floor", "selection_caveat"): ("same", None),
    ("s9#4", "quiet_night_floor.json", "pricing", "floor_kw_basis"): ("same", None),
    ("s9#5", "quiet_night_floor.json", "night_floor", "selection_caveat"): ("same", None),
    ("s9#5", "quiet_night_floor.json", "pricing", "floor_kw_basis"): ("same", None),
    # --- issue #140: the sensitivity ladder NIGHT_FLOOR_SENSITIVITY_PER_100W
    # publishes. All three are this change's own debt, so all three are here.
    ("s13#11", "quiet_night_floor.json", "sensitivity_per_100w", "basis"):
        ("names the engine and step size the ladder was re-billed with (method b, 100 W "
         "steps) -- provenance for the arithmetic, not a condition on reading the rate; "
         "NIGHT_FLOOR_ANNUAL_COST and PHANTOM_METHOD_DISCREPANCY already publish what "
         "method b is and what it agrees with", None),
    ("s13#11", "quiet_night_floor.json", "sensitivity_per_100w",
     "linearity_note"):
        ("NIGHT_FLOOR_SENSITIVITY_PER_100W recomputes this note's argument from the "
         "ladder's own marginal_usd_per_100w column and prints the spread, so the "
         "curvature is stated as a range rather than quoted as a note", None),
    ("s13#11", "quiet_night_floor.json",
     "sensitivity_per_100w.usd_per_100w_at_current_floor", "note"):
        ("NIGHT_FLOOR_SENSITIVITY_PER_100W states this note's own conclusion -- the rate "
         "is read off the step nearest the measured floor, not computed at the "
         "household's exact wattage -- and computes which step that was", None),
}

# Candidates whose FIGURE comes from a token this PR did not add. Out of scope
# here by the same rule that left EV_FIX_SAVINGS_* alone (issue #147); listed
# so they are visibly excluded rather than quietly absent.
# Keyed PER BLOCK, not per artifact: the same object can carry a pre-existing
# token's figures in one section and this PR's in another. quiet_night_floor's
# night_floor is exactly that -- section 2 reads it only through the
# pre-existing S2_VERDICT, section 13 reads it through the NIGHT_FLOOR_* tokens
# this PR added, and an artifact-keyed entry silently exempted both.
_QUALIFIER_PRE_EXISTING = {
    # The four ("s9#N", "deep_results.json", "phantom") entries that sat here
    # are GONE, not moved (issue #140). SEC9_TEASER no longer opens that
    # artifact, so the derivation stops raising them; and the candidates that
    # replaced them belong to a token this change itself re-pointed, which
    # makes them this change's debt rather than inherited debt. They are
    # written down with a reason in _QUALIFIER_ACCOUNTED above instead.
    ("s2#3", "quiet_night_floor.json", "night_floor"): ("S2_VERDICT",),
    ("s2#4", "quiet_night_floor.json", "night_floor"): ("S2_VERDICT",),
    ("s13#8", "battery_dispatch_policies.json", "(root)"):
        ("PAYBACK_AT_HISTORICAL_ESCALATION", "NPV_AT_HISTORICAL_ESCALATION"),
    ("s13#9", "battery_dispatch_policies.json", "(root)"):
        ("PAYBACK_AT_HISTORICAL_ESCALATION", "NPV_AT_HISTORICAL_ESCALATION"),
    ("s13#11", "battery_dispatch_policies.json", "(root)"):
        ("PAYBACK_AT_HISTORICAL_ESCALATION", "NPV_AT_HISTORICAL_ESCALATION"),
    ("s13#8", "carbon_fullyear_results.json", "(root)"): ("SEC13_TEASER",),
    ("s13#9", "carbon_fullyear_results.json", "(root)"): ("SEC13_TEASER",),
    ("s13#11", "carbon_fullyear_results.json", "(root)"): ("SEC13_TEASER",),
    ("s13#8", "nem3_grandfathering.json", "grandfathering_value_range_usd_per_yr"):
        ("NEM_GRANDFATHER_VALUE_RANGE",),
    ("s13#9", "nem3_grandfathering.json", "grandfathering_value_range_usd_per_yr"):
        ("NEM_GRANDFATHER_VALUE_RANGE",),
    ("s13#11", "nem3_grandfathering.json", "grandfathering_value_range_usd_per_yr"):
        ("NEM_GRANDFATHER_VALUE_RANGE",),
    ("s10#4", "cca_bundled_counterfactual.json",
     "direction_a_cca_repriced_at_bundled"): ("S10_VERDICT",),
}


@case
def case_each_instruction_names_the_branch_the_recommendation_it_makes_needs():
    """ISSUE #132, /review FINDINGS 6, 7 AND 10. Three TODOs whose instruction
    and whose scoped figures pointed at different things.

    s10#4 concluded "the decision belongs at the moment the existing appliance
    dies" with only the STANDALONE furnace figures in scope ($14,529 / 167.2
    yr). heat_pump_conversion's marginal_over_ac_replacement branch ($4,098 /
    47.2 yr) is the one that prices replacing at failure, and it was not
    tokenized at all -- so the instruction argued for waiting while showing the
    number for not waiting.

    s9#3 called a 60-row cleaning-study export "the closest approach anywhere
    in the committed record", a superlative over a record that token never
    opens.

    s9#5 put the compliance share and the two savings figures in one sentence,
    inviting prose that reads them as one basis when the savings come from
    re-billing whole shifted sessions."""
    _require_household()
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    checked = {}

    s10 = blocks["s10#4"].text
    for token in ("HEAT_PUMP_MARGINAL_INSTALL_COST", "HEAT_PUMP_MARGINAL_PAYBACK"):
        assert token in rb.tokens_mentioned(s10), (
            f"s10#4 recommends replace-on-failure but cannot cite {token}, the branch "
            "that prices it")
        checked[token] = rt.resolve_token(token)
    assert "replace-on-failure" in s10, "s10#4 lost the replace-on-failure recommendation"
    assert "do not support the recommendation with the standalone figures" in s10, (
        "s10#4 no longer forbids pricing replace-on-failure with the standalone branch")

    s9_3 = blocks["s9#3"].text
    assert "anywhere in the committed record" not in s9_3, (
        "s9#3 claims a record-wide maximum again from a token that reads one "
        "60-row cleaning-study export")
    assert "NOT a record-wide maximum" in s9_3, (
        "s9#3 no longer tells the writer the peak is not a record-wide maximum")
    checked["PEAK_POWER_MULTIYEAR"] = rt.resolve_token("PEAK_POWER_MULTIYEAR")
    assert "sample" in checked["PEAK_POWER_MULTIYEAR"], (
        "the peak token no longer names the window it covers")

    s9_5 = blocks["s9#5"].text
    assert "SEPARATE sentence" in s9_5 and "do not present them as one basis" in s9_5, (
        "s9#5 no longer separates the compliance share from the re-billed savings")
    return (f"s10#4 prices replace-on-failure with the marginal branch "
            f"({checked['HEAT_PUMP_MARGINAL_INSTALL_COST']} / "
            f"{checked['HEAT_PUMP_MARGINAL_PAYBACK']}), s9#3 drops the record-wide "
            "superlative, and s9#5 splits the two EV bases")


@case
def case_every_derived_figure_qualifier_candidate_is_accounted_for():
    _require_household()
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    candidates = _derive_qualifier_candidates(html, blocks, sorted(_RECLASSIFIED_FIGURES))
    assert candidates, "the derivation found no candidates at all -- it has broken"

    unaccounted, stale = [], set(_QUALIFIER_ACCOUNTED)
    covered = pre_existing = 0
    for bid, who, objpath, key, is_covered in candidates:
        if is_covered:
            covered += 1
            continue
        if (bid, who, objpath) in _QUALIFIER_PRE_EXISTING:
            pre_existing += 1
            continue
        entry = _QUALIFIER_ACCOUNTED.get((bid, who, objpath, key))
        if entry is None:
            unaccounted.append(f"{bid}: data/{who}:{objpath}.{key} qualifies a figure this "
                               "block reads, and nothing in its scope can state it")
            continue
        stale.discard((bid, who, objpath, key))
        reason, phrase = entry
        assert reason.strip(), f"{bid}/{key}: an empty reason is not an account"
        if phrase:
            assert phrase in blocks[bid].text, (
                f"{bid} claims its TODO carries the qualification for {key} via "
                f"{phrase!r}, but the TODO no longer says it")
    assert not unaccounted, (
        f"{len(unaccounted)} derived qualifier candidate(s) are unaccounted for. Each is a "
        "condition the artifact states beside a number this block reads, that no token in "
        "the block's scope can tell the reader. Add the qualifier token, or record why it "
        "is discharged in _QUALIFIER_ACCOUNTED:\n  " + "\n  ".join(unaccounted))
    assert not stale, (
        f"_QUALIFIER_ACCOUNTED has entries the derivation no longer raises: {sorted(stale)}")
    raised = {(bid, who, objpath) for bid, who, objpath, _k, cov in candidates if not cov}
    stale_pre = set(_QUALIFIER_PRE_EXISTING) - raised
    assert not stale_pre, (
        "_QUALIFIER_PRE_EXISTING has entries the derivation no longer raises: "
        f"{sorted(stale_pre)}")
    return (f"{len(candidates)} qualifier candidates derived across "
            f"{len(_RECLASSIFIED_FIGURES)} blocks: {covered} discharged by a token that "
            f"reads them, {pre_existing} belong to pre-existing figures, "
            f"{len(_QUALIFIER_ACCOUNTED)} accounted for by name")


@case
def case_no_figure_reaches_a_blocks_scope_without_its_qualifier():
    _require_household()
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    unqualified, checked = [], 0
    for bid, pairs in sorted(_FIGURE_NEEDS_QUALIFIER.items()):
        scope = rb.scope_tokens_for_block(html, blocks[bid])
        for figure, qualifier in pairs:
            assert figure in scope, (
                f"{bid}: fixture assumption broken -- {figure} is no longer in scope")
            if qualifier not in scope:
                unqualified.append(f"{bid}: {figure} is in scope but {qualifier} is not")
                continue
            value = rt.resolve_token(qualifier, rt.TOKENS[qualifier])
            assert value.strip(), f"{bid}: {qualifier} resolved to nothing"
            checked += 1
    assert not unqualified, (
        f"{len(unqualified)} figure(s) can be cited without the qualifier their own "
        "artifact states as the condition for reading them, so the prose pass could not "
        "disclose the limitation even if it tried:\n  " + "\n  ".join(unqualified))
    return (f"all {checked} figure/qualifier pair(s) share a scope across "
            f"{len(_FIGURE_NEEDS_QUALIFIER)} blocks, and every qualifier resolves")


@case
def case_every_reclassified_block_can_see_and_resolve_its_own_figures():
    _require_household()
    html = rt.TEMPLATE.read_text()
    blocks = {b.id: b for b in rb.parse_todo_blocks(html)}
    total = 0
    for bid, tokens in sorted(_RECLASSIFIED_FIGURES.items()):
        assert rb.CLASSIFICATION[bid] == "prose", (
            f"{bid} is listed as reclassified but is {rb.CLASSIFICATION[bid]!r}")
        scope = rb.scope_tokens_for_block(html, blocks[bid])
        missing = [t for t in tokens if t not in scope]
        assert not missing, (
            f"{bid} cannot cite {missing} -- they are neither live in section "
            f"{blocks[bid].section} nor named in the block's own TODO text, so "
            "generate_report.py would reject a fragment that used them")
        for token in tokens:
            value = rt.resolve_token(token, rt.TOKENS[token])
            assert value.strip(), f"{bid}: {token} resolved to nothing"
            total += 1
    return (f"all {total} figures across the {len(_RECLASSIFIED_FIGURES)} reclassified "
            "blocks are in their own block's scope and resolve against the real archive")


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran = skipped = 0
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
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
