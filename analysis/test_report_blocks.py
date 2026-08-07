#!/usr/bin/env python3
"""Tests for report_blocks.py (issue #39 part 5): the TODO-block
classification map and its mechanical data-class row builders.

Follows this repo's established CASES/@case/main() convention (see e.g.
test_report_tokens.py, test_llm_providers.py).

Run from the repo root:  ./.venv/bin/python analysis/test_report_blocks.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import report_blocks as rb   # noqa: E402
import report_tokens as rt   # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """issue #102: this file had NO private-data gate at all -- the one case
    that touches the real archive raised household.py's own fail-closed
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
def case_electrification_block_matches_its_known_gap_token():
    assert rb.CLASSIFICATION["s10#4"] == "human"
    assert "ELECTRIFICATION_VERDICT_SHORT" in rb.HUMAN_REASONS["s10#4"]
    return "the electrification block (s10#4) is classified human, matching its KNOWN_GAPS token"


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
