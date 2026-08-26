#!/usr/bin/env python3
"""Tests for generate_report.py (issue #39 part 5) and the extended
report-consistency checks against a FULLY GENERATED index.generated.html
(issue #39 part 6). A sibling of test_report_consistency.py rather than an
extension of it: that file reads the real index.html eagerly at import time
and this suite needs a completely different (generated, mocked-LLM) file, so
sharing a module would mean two incompatible fixtures under one HTML global.

NO TEST IN THIS FILE MAKES A REAL NETWORK CALL. Every case either calls
find_fragment_violations()/prose_lint.lint() directly (pure functions), or
calls generate_report.run() with a FAKE llm_call it supplies -- llm_providers
itself still poisons urllib.request.urlopen at import time (see
test_llm_providers.py), so even a bug that bypassed the fake would fail loud
rather than reaching a socket. Real egress preflight (llm_providers.preflight,
including its gitleaks scan) DOES run in most cases here -- it's a pure/local
check, not a network call -- and is skipped only where gitleaks isn't
installed.

Run from the repo root:  ./.venv/bin/python analysis/test_generate_report.py
"""
import ast
import csv
import json
import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import generate_report as gr   # noqa: E402
import llm_providers as lp     # noqa: E402
import report_blocks as rb     # noqa: E402
import report_tokens as rt     # noqa: E402
# Cross-suite import, the convention test_battery_plan_matrix set with
# test_scripts_runnable: the plan-repricing fixtures live beside the token
# cases that prove them, and a copy here would reprice slightly differently.
import test_report_tokens as trt  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    pass


def _require_gitleaks():
    if shutil.which("gitleaks") is None:
        raise SkipCase("needs the gitleaks binary (brew install gitleaks), not installed here")


def _require_household():
    if not rt.hh.PATH.is_file():
        raise SkipCase(f"needs private/household.yaml ({rt.hh.PATH}), which this checkout "
                       "does not have")


# A universally safe fragment: no digits, no {{TOKEN}}, clears prose_lint and
# the numeral guard for ANY of the 105 blocks regardless of instruction text.
SAFE_FRAGMENT = ("This part of the report explains the finding using only the values "
                 "already shown above, staying within the analysis's own evidence.")


def make_fake_call(text=SAFE_FRAGMENT, finish_reason="end_turn", calls=None):
    calls = calls if calls is not None else []

    def fake_call(provider, model, system, user, max_tokens, env=None):
        calls.append({"provider": provider, "model": model, "system": system, "user": user})
        return {"text": text, "finish_reason": finish_reason,
               "usage": {"input_tokens": 10, "output_tokens": 5}}

    fake_call.calls = calls
    return fake_call


# Stand-in answers for gap tokens whose slot constrains the answer's shape.
# Modelled on what index.html actually publishes, so the fixture exercises the
# accepting path a real operator reaches -- not a value invented to clear a regex.
_GAP_ANSWER_FIXTURES = {
    "UTILITY_TOOL_BEST_PLAN_FIGURE": "$4,519.65",
    "UTILITY_TOOL_BEST_PLAN_VERDICT": "\u2713",
}


def _all_human_answers():
    """Fabricated operator-supplied answers covering every human-classified
    block AND every live KNOWN_GAPS token EXCEPT the provenance review
    clause, which this module refuses to accept an override for at all (see
    case_review_clause_override_attempt_is_ignored below)."""
    # No <p> wrapper: a human answer replaces a TODO comment's own slot the
    # same way a prose fragment does (typically already inside a <p>/<li>/
    # <td> in the template), and <p> is not on the HTML-structure guard's
    # inline-tag allowlist (see find_html_structure_violations) -- these
    # fixtures must pass that gate exactly like a real operator answer would.
    answers = {bid: f"<b>Operator-supplied</b> answer for {bid} (fabricated for this test)."
              for bid in rb.HUMAN_REASONS}
    # A gap answer lands in fixed markup that already carries half the sentence,
    # so some tokens declare a SHAPE the answer has to satisfy
    # (report_tokens.GAP_ANSWER_CONTRACTS). A generic placeholder does not, and
    # generate_report now puts every token answer through that contract, so a
    # fixture of placeholders would exercise the refusal path instead of the run
    # it is here to test.
    for name in sorted(rb.LIVE_GAP_TOKENS):
        answers[f"TOKEN:{name}"] = _GAP_ANSWER_FIXTURES.get(
            name, f"(operator-supplied placeholder for {name})")
    # And the fixture proves itself rather than being trusted: a contract added
    # later without a fixture beside it fails HERE, naming the token, instead of
    # surfacing as an unrelated run failure three cases along.
    for name in sorted(rb.LIVE_GAP_TOKENS):
        try:
            rt.validate_gap_answer(name, answers[f"TOKEN:{name}"])
        except SystemExit as e:
            raise AssertionError(
                f"the stand-in answer for gap token {name} does not satisfy its own "
                f"contract, so these fixtures no longer model a real operator answer -- "
                f"add one to _GAP_ANSWER_FIXTURES: {e}") from None
    return answers


# ---------------------------------------------------------------------------
# The numeral guard.
# ---------------------------------------------------------------------------
@case
def case_numeral_guard_rejects_the_issues_own_fabricated_example():
    violations = gr.find_fragment_violations("The battery saves $2,900/yr under this policy.")
    assert violations, "expected the fabricated 'saves $2,900/yr' fragment to be rejected"
    return f"'saves $2,900/yr' is rejected: {violations}"


@case
def case_numeral_guard_accepts_valid_token_and_section_references():
    text = "See §3 for the plan comparison; {{BEST_PLAN}} wins by {{PLAN_MARGIN_VS_RUNNER_UP}}."
    assert gr.find_fragment_violations(text) == [], gr.find_fragment_violations(text)
    return "a fragment using only {{TOKEN}} and §N references passes cleanly"


@case
def case_numeral_guard_rejects_an_unknown_token_reference():
    violations = gr.find_fragment_violations("{{TOTALLY_MADE_UP_TOKEN}} explains it.")
    assert any("unknown token" in v for v in violations), violations
    return "a reference to a token not in report_tokens.TOKENS is rejected"


@case
def case_numeral_guard_rejects_a_digit_inside_an_unknown_token_name():
    violations = gr.find_fragment_violations("{{BOGUS_TOKEN_2900}} saved the day.")
    assert violations, violations
    return "a bogus token name containing digits is still rejected (as an unknown token)"


# ---------------------------------------------------------------------------
# Adversarial review finding 2: a decimal-digit scan alone misses an invented
# quantity spelled out in words. One case per construction named in the
# finding, plus the false-positive case it specifically asked to check.
# ---------------------------------------------------------------------------
@case
def case_numeral_guard_rejects_a_spelled_out_fraction():
    violations = gr.find_fragment_violations(
        "roughly two-thirds of imports occur on-peak")
    assert violations, violations
    return "'roughly two-thirds' is rejected (spelled-out fraction + vague quantifier)"


@case
def case_numeral_guard_rejects_a_spelled_out_percent_phrase():
    violations = gr.find_fragment_violations("about forty percent of production is exported")
    assert violations, violations
    return "'forty percent' is rejected (spelled-out cardinal number)"


@case
def case_numeral_guard_rejects_vague_quantifiers():
    for text in ("the battery serves most of the on-peak imports",
                "several bill facts were checked",
                "the majority of savings come from behavior change"):
        violations = gr.find_fragment_violations(text)
        assert violations, (text, violations)
    return "vague quantifiers ('most of', 'several', 'majority') are rejected"


# ---------------------------------------------------------------------------
# Adversarial review pass 2, finding 1: "most"/"many" gated on a following
# "of" missed the equally-natural ungated phrasing entirely, and multiplier
# words ("doubles", "triples", "twice") and several more vague-quantifier
# words weren't covered at all. One case per exact phrase the reviewer used.
# ---------------------------------------------------------------------------
@case
def case_numeral_guard_rejects_most_many_without_a_following_of():
    for text in ("most on-peak imports happen in the evening",
                "many households export more than they import"):
        violations = gr.find_fragment_violations(text)
        assert violations, (text, violations)
    return "'most'/'many' are rejected even without a following 'of'"


@case
def case_numeral_guard_rejects_multiplier_words():
    for text in ("the battery doubles the savings",
                "this triples the effective capacity",
                "twice as much power is exported"):
        violations = gr.find_fragment_violations(text)
        assert violations, (text, violations)
    return "multiplier words ('doubles', 'triples', 'twice') are rejected"


@case
def case_numeral_guard_rejects_additional_vague_quantifier_words():
    for text in ("a handful of days drove the annual total",
                "a tiny fraction of exports are curtailed",
                "the bulk of savings come from behavior change",
                "countless factors affect the outcome"):
        violations = gr.find_fragment_violations(text)
        assert violations, (text, violations)
    return "'handful', 'fraction', 'bulk', and 'countless' are all rejected"


@case
def case_numeral_guard_does_not_flag_an_ordinal_used_as_a_rank_not_a_quantity():
    """The reviewer's own named false-positive risk: 'third' as an ordinal
    referring to LOW/MID/HIGH (not a fraction) should not trip the guard.
    Distinguished from a real fraction claim by requiring an article/number
    word before, or 'of'/'the' after -- see _WORD_NUMBER_PATTERNS."""
    text = "the third package option pencils better than the second"
    assert gr.find_fragment_violations(text) == [], gr.find_fragment_violations(text)
    return "'the third package option' (an ordinal rank, not a fraction) is not flagged"


# ---------------------------------------------------------------------------
# Adversarial review pass 3, finding 1: broadening "most" to match standalone
# (pass 2's own fix, for "most on-peak imports...") false-positived on the
# ordinary superlative-adjective construction ("the most efficient policy"),
# which states no quantity at all. "most" is graded (superlative of "many"/
# adjectives) in a way none of this list's other vague quantifiers are, so it
# alone needs a narrower rule: exempt only when immediately preceded by "the"
# (the superlative frame), which still catches the bare "most X"/"most of X"
# quantifier sense pass 2 fixed.
# ---------------------------------------------------------------------------
@case
def case_numeral_guard_does_not_flag_the_most_as_a_superlative_adjective():
    for text in ("the most efficient dispatch policy is greedy",
                "the most cost-effective option is the smaller battery",
                "this is the most affordable package available"):
        assert gr.find_fragment_violations(text) == [], (text, gr.find_fragment_violations(text))
    return "'the most efficient/cost-effective/affordable ...' (superlative, not a quantity) is not flagged"


@case
def case_numeral_guard_still_flags_most_as_a_quantifier():
    """The fix above must not regress pass 2's own case: 'most' with no
    preceding 'the' is still overwhelmingly the 'majority of' quantifier
    sense, and must still be caught."""
    for text in ("most on-peak imports happen in the evening",
                "most of the savings come from behavior change"):
        violations = gr.find_fragment_violations(text)
        assert violations, (text, violations)
    return "'most on-peak imports...' / 'most of the savings...' (the quantifier sense) are still rejected"


@case
def case_numeral_guard_still_accepts_clean_prose_with_no_quantity_words():
    text = ("{{BEST_PLAN}} stays cheapest with or without a battery; see §3 for the "
           "full comparison and no one disputes the ranking.")
    assert gr.find_fragment_violations(text) == [], gr.find_fragment_violations(text)
    return "ordinary prose (including 'no one', an indefinite pronoun) is not flagged"


# ---------------------------------------------------------------------------
# Codex review finding 3: a {{TOKEN}} reference must be inside the
# ORIGINATING BLOCK's own scope, not just any real token in the whole
# 143-token system.
# ---------------------------------------------------------------------------
@case
def case_numeral_guard_rejects_a_real_token_outside_the_blocks_own_scope():
    violations = gr.find_fragment_violations(
        "{{BEST_PLAN}} vs {{CLEANING_PRICE}}", allowed_tokens={"BEST_PLAN"})
    assert any("outside this block's own scope" in v for v in violations), violations
    assert not any("unknown token" in v for v in violations), (
        "CLEANING_PRICE is a real token -- it must be flagged as out-of-scope, not unknown")
    return "a real token outside the block's own scope is rejected, distinctly from an unknown one"


@case
def case_numeral_guard_accepts_a_token_that_is_in_the_blocks_own_scope():
    violations = gr.find_fragment_violations(
        "{{BEST_PLAN}} vs {{CLEANING_PRICE}}", allowed_tokens={"BEST_PLAN", "CLEANING_PRICE"})
    assert violations == [], violations
    return "both tokens are accepted when both are in the block's own scope"


@case
def case_numeral_guard_with_no_allowed_tokens_argument_accepts_any_real_token():
    """Backward-compatible default: allowed_tokens=None (the default) accepts
    any real report_tokens.TOKENS name, for standalone/generic callers that
    aren't validating one specific block's scoped output."""
    violations = gr.find_fragment_violations("{{BEST_PLAN}} vs {{CLEANING_PRICE}}")
    assert violations == [], violations
    return "with no allowed_tokens argument, any real token is accepted (unscoped mode)"


# ---------------------------------------------------------------------------
# Codex review finding 2 (the most important one): a mechanical HTML-
# structure gate. Neither the numeral guard nor prose_lint.py looked at HTML
# structure at all, so "<script>...</script>" (zero digits, no banned
# phrase) passed both cleanly. One case per example the review named,
# plus the allowlisted-tags-are-fine positive control.
# ---------------------------------------------------------------------------
@case
def case_html_guard_rejects_a_script_tag():
    violations = gr.find_fragment_violations("<script>alert(document.domain)</script>")
    assert any("disallowed HTML tag <script>" in v for v in violations), violations
    return "<script>...</script> is rejected"


@case
def case_html_guard_rejects_an_event_handler_attribute():
    violations = gr.find_fragment_violations('<b onerror="alert(1)">bold</b>')
    assert any("event-handler attribute" in v for v in violations), violations
    return "an onerror= attribute is rejected"


@case
def case_html_guard_rejects_a_javascript_href():
    violations = gr.find_fragment_violations('<a href="javascript:alert(1)">click</a>')
    assert any("URL scheme" in v or "internal section anchor" in v for v in violations), violations
    return "a javascript: href is rejected"


@case
def case_html_guard_rejects_an_external_href():
    violations = gr.find_fragment_violations('<a href="https://evil.example.com">click</a>')
    assert any("internal section anchor" in v for v in violations), violations
    return "an external https:// href is rejected -- only a bare #sN internal anchor is allowed"


@case
def case_html_guard_rejects_an_unbalanced_tag():
    violations = gr.find_fragment_violations("<b>bold with no closing tag")
    assert any("unclosed tag" in v or "unbalanced" in v for v in violations), violations
    return "an unclosed <b> with no matching </b> is rejected"


@case
def case_html_guard_rejects_improperly_nested_tags():
    violations = gr.find_fragment_violations("<b><i>mismatched</b></i>")
    assert any("unbalanced or improperly nested" in v for v in violations), violations
    return "improperly nested <b><i>...</b></i> is rejected"


@case
def case_html_guard_rejects_a_disallowed_tag_even_when_otherwise_inert():
    for fragment in ('<div>a div</div>', '<span class="pill">a span</span>',
                     '<img src="x">', '<svg onload="alert(1)"></svg>'):
        violations = gr.find_fragment_violations(fragment)
        assert violations, fragment
    return "div, span, img, and svg are all rejected -- not on the inline allowlist"


@case
def case_html_guard_accepts_the_allowlisted_tags_used_normally():
    text = ('<b>Plan: stay on {{BEST_PLAN}}</b>, <i>emphasis</i>, <em>emphasis</em>, '
           '<strong>strong</strong>, <code>rates.py</code>, <sub>sub</sub> and '
           '<sup>sup</sup>, and see <a href="#s3">§3</a>.')
    violations = gr.find_fragment_violations(text, allowed_tokens={"BEST_PLAN"})
    assert violations == [], violations
    return "b/i/em/strong/code/sub/sup and an internal <a href='#sN'> are all accepted together"


@case
def case_html_guard_runs_as_part_of_find_fragment_violations_not_a_separate_step():
    """Wired into find_fragment_violations() itself (not a separate check
    callers have to remember to add), so it participates in the existing
    retry-once-then-hard-fail flow automatically."""
    block = rb.parse_todo_blocks()[3]
    responses = iter([
        {"text": "<script>alert(1)</script>", "finish_reason": "end_turn", "usage": {}},
        {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}},
    ])

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return next(responses)

    _require_gitleaks()
    frag = gr.generate_prose_fragment(block, {}, "anthropic", "m", None, fake_call)
    assert frag == SAFE_FRAGMENT
    return "a <script> tag on the first attempt triggers the retry, which then succeeds"


@case
def case_stale_cache_entry_with_disallowed_markup_is_revalidated_on_read():
    """A cache entry written before this fix existed (or corrupted on disk)
    must never be trusted just because its hash key still matches --
    load_and_validate_cache() re-runs the full current guard on every read
    and treats a failure as a cache MISS, not a crash."""
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td)
        key = gr.cache_key("s0#1", {}, "some block text", "anthropic", "m")
        gr.save_cache(cache_dir, "s0#1", key, "<script>alert(1)</script>")
        # A naive load_cache() would return the poisoned fragment as-is.
        assert gr.load_cache(cache_dir, "s0#1", key) == "<script>alert(1)</script>"
        # load_and_validate_cache() must refuse to trust it.
        result = gr.load_and_validate_cache(cache_dir, "s0#1", key, allowed_tokens=set())
        assert result is None, result
    return "a stale/tampered cache entry containing disallowed markup is treated as a cache miss"


@case
def case_stale_cache_entry_referencing_an_out_of_scope_token_is_revalidated_on_read():
    """Same mechanism, for finding 3: a cache entry written before the
    per-block scope restriction existed could reference a real token that
    is no longer (or never was) in this block's own scope."""
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td)
        key = gr.cache_key("s0#1", {}, "some block text", "anthropic", "m")
        gr.save_cache(cache_dir, "s0#1", key, "{{CLEANING_PRICE}} is cited here.")
        result = gr.load_and_validate_cache(cache_dir, "s0#1", key,
                                            allowed_tokens={"BEST_PLAN"})
        assert result is None, result
    return "a stale cache entry citing a real but out-of-scope token is treated as a cache miss"


@case
def case_a_clean_valid_cache_entry_is_still_accepted():
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td)
        key = gr.cache_key("s0#1", {}, "some block text", "anthropic", "m")
        gr.save_cache(cache_dir, "s0#1", key, SAFE_FRAGMENT)
        result = gr.load_and_validate_cache(cache_dir, "s0#1", key, allowed_tokens=set())
        assert result == SAFE_FRAGMENT
    return "a clean cache entry still passes revalidation and is used normally"


# ---------------------------------------------------------------------------
# Retry-once-then-hard-fail.
# ---------------------------------------------------------------------------
@case
def case_a_bad_fragment_followed_by_a_good_one_succeeds_via_retry():
    responses = iter([
        {"text": "saves $2,900/yr", "finish_reason": "end_turn", "usage": {}},
        {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}},
    ])

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return next(responses)

    block = rb.parse_todo_blocks()[3]   # s0#1, an ordinary prose block
    _require_gitleaks()
    frag = gr.generate_prose_fragment(block, {}, "anthropic", "m", None, fake_call)
    assert frag == SAFE_FRAGMENT
    return "a numeral-guard failure on attempt 1 is corrected by the single retry"


@case
def case_two_bad_fragments_in_a_row_hard_fail_the_block():
    _require_gitleaks()

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return {"text": "saves $2,900/yr", "finish_reason": "end_turn", "usage": {}}

    block = rb.parse_todo_blocks()[3]
    try:
        gr.generate_prose_fragment(block, {}, "anthropic", "m", None, fake_call)
        raise AssertionError("expected BlockFailure after two bad fragments")
    except gr.BlockFailure as e:
        assert "s0#1" in str(e)
    return "two numeral-guard failures in a row hard-fail the block, never splicing either"


@case
def case_abnormal_finish_reason_triggers_the_retry_path():
    responses = iter([
        {"text": SAFE_FRAGMENT, "finish_reason": "max_tokens", "usage": {}},
        {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}},
    ])

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return next(responses)

    block = rb.parse_todo_blocks()[3]
    _require_gitleaks()
    frag = gr.generate_prose_fragment(block, {}, "anthropic", "m", None, fake_call)
    assert frag == SAFE_FRAGMENT
    return "a truncated (max_tokens) finish_reason triggers the retry, which then succeeds"


@case
def case_prose_lint_violation_triggers_the_retry_path():
    responses = iter([
        {"text": "This cutting-edge battery is a game-changer.", "finish_reason": "end_turn",
        "usage": {}},
        {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}},
    ])

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return next(responses)

    block = rb.parse_todo_blocks()[3]
    _require_gitleaks()
    frag = gr.generate_prose_fragment(block, {}, "anthropic", "m", None, fake_call)
    assert frag == SAFE_FRAGMENT
    return "a prose_lint violation (promotional language) triggers the retry, which then succeeds"


# ---------------------------------------------------------------------------
# --humanize: an optional second pass that never fails the run.
# ---------------------------------------------------------------------------
_HUMANIZE_TEST_BLOCK = rb.parse_todo_blocks()[3]   # s0#1, an ordinary prose block


@case
def case_humanize_uses_the_clean_rewrite_when_it_passes_both_gates():
    _require_gitleaks()

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return {"text": "A rewritten, still-clean version of the fragment.",
               "finish_reason": "end_turn", "usage": {}}

    out = gr.humanize_fragment(_HUMANIZE_TEST_BLOCK, {}, SAFE_FRAGMENT, "anthropic", "m",
                               None, fake_call)
    assert out == "A rewritten, still-clean version of the fragment."
    return "a clean rewrite that clears both gates is used in place of the original"


@case
def case_humanize_falls_back_to_the_original_on_a_numeral_guard_violation():
    _require_gitleaks()

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return {"text": "This saves $2,900/yr.", "finish_reason": "end_turn", "usage": {}}

    out = gr.humanize_fragment(_HUMANIZE_TEST_BLOCK, {}, SAFE_FRAGMENT, "anthropic", "m",
                               None, fake_call)
    assert out == SAFE_FRAGMENT, out
    return "a rewrite that fails the numeral guard falls back to the original fragment"


@case
def case_humanize_falls_back_to_the_original_on_a_prose_lint_violation():
    _require_gitleaks()

    def fake_call(provider, model, system, user, max_tokens, env=None):
        return {"text": "This cutting-edge, seamless improvement is a game-changer.",
               "finish_reason": "end_turn", "usage": {}}

    out = gr.humanize_fragment(_HUMANIZE_TEST_BLOCK, {}, SAFE_FRAGMENT, "anthropic", "m",
                               None, fake_call)
    assert out == SAFE_FRAGMENT, out
    return "a rewrite that fails prose_lint falls back to the original fragment"


@case
def case_humanize_never_raises_and_never_aborts_the_whole_run():
    """The issue's own framing: 'a second model call does not [fail closed], so
    it cannot be the gate' -- humanize_fragment must not be able to raise
    BlockFailure or propagate an unhandled exception at all, whether the
    rewrite call fails fast (a non-retryable 400) or not."""
    _require_gitleaks()

    def fake_call(provider, model, system, user, max_tokens, env=None):
        raise lp.ProviderError("anthropic", 400, "bad request")

    out = gr.humanize_fragment(_HUMANIZE_TEST_BLOCK, {}, SAFE_FRAGMENT, "anthropic", "m",
                               None, fake_call)
    assert out == SAFE_FRAGMENT
    return "even a non-retryable ProviderError from the rewrite call never propagates"


# ---------------------------------------------------------------------------
# Bounded backoff on 429/5xx; a non-retryable error raises immediately.
# ---------------------------------------------------------------------------
@case
def case_backoff_retries_a_429_then_succeeds():
    attempts = {"n": 0}
    sleeps = []

    def fake_call(provider, model, system, user, max_tokens, env=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise lp.ProviderError("anthropic", 429, "rate limited")
        return {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}}

    out = gr.call_with_backoff(fake_call, "anthropic", "m", "sys", "user", None,
                               sleep=sleeps.append)
    assert out["text"] == SAFE_FRAGMENT
    assert attempts["n"] == 3, attempts
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0], sleeps
    return f"a 429 retried twice with growing backoff ({sleeps}) before succeeding"


@case
def case_non_retryable_error_raises_immediately_with_no_backoff():
    attempts = {"n": 0}

    def fake_call(provider, model, system, user, max_tokens, env=None):
        attempts["n"] += 1
        raise lp.ProviderError("anthropic", 400, "bad request")

    try:
        gr.call_with_backoff(fake_call, "anthropic", "m", "sys", "user", None, sleep=lambda s: None)
        raise AssertionError("expected a 400 to propagate immediately")
    except lp.ProviderError as e:
        assert e.status == 400
    assert attempts["n"] == 1, "a non-retryable error must not be retried at all"
    return "a 400 (not 429/5xx) propagates on the first attempt with zero retries"


# ---------------------------------------------------------------------------
# Provenance overrides -- the most load-bearing behavior in this module.
# ---------------------------------------------------------------------------
@case
def case_generation_tool_reflects_the_actual_provider_and_model_used():
    resolved, gaps, resolve_failures = gr.resolve_tokens_with_gaps()
    resolved = gr.apply_provenance_overrides(resolved, "openai", "gpt-9-fabricated")
    assert resolved["GENERATION_TOOL"] == "openai (gpt-9-fabricated)"
    return "GENERATION_TOOL reflects the actual provider/model passed to this run"


@case
def case_review_tools_are_always_the_disclaimer_never_the_hardcoded_report_tokens_values():
    resolved, gaps, resolve_failures = gr.resolve_tokens_with_gaps()
    # STRONGER THAN "the override wins" (issue #135): the provenance tokens are
    # not resolved on this path at all, so there is no household value here for
    # an override to have to beat. That also means a household which recorded
    # no review -- the documented default, both fields null, which report_tokens
    # .py refuses to render -- cannot put a blocking failure in this list.
    for name in gr.PROVENANCE_OVERRIDDEN:
        assert name not in resolved, (
            f"{name} was resolved before its override; a household with no review "
            "recorded would then fail the whole run")
        assert name not in {n for n, _ in resolve_failures}, (
            f"{name} recorded a resolution failure, which run() would treat as "
            "blocking even though the value is about to be replaced")
    overridden = gr.apply_provenance_overrides(resolved, "anthropic", "claude-fabricated")
    assert overridden["REVIEW_TOOL_1"] == gr.REVIEW_DISCLAIMER
    assert overridden["REVIEW_TOOL_2"] == gr.REVIEW_DISCLAIMER
    assert "Claude Code" not in overridden["REVIEW_TOOL_1"]
    assert "Codex" not in overridden["REVIEW_TOOL_2"]
    return ("REVIEW_TOOL_1/2 are never resolved on this path and are always the fixed "
            "disclaimer, so no household provenance value can reach the output")


# ---------------------------------------------------------------------------
# Codex review finding 1: resolve_tokens_with_gaps() used to catch EVERY
# SystemExit from token resolution and silently fold it into `gaps`,
# regardless of whether that token's own kind was actually "gap" -- a real
# failure (a missing/malformed artifact, a broken path) would disappear into
# the same bucket as a documented, intentional KNOWN_GAPS entry instead of
# blocking the run.
# ---------------------------------------------------------------------------
@case
def case_resolve_tokens_with_gaps_treats_a_non_gap_failure_as_a_real_failure():
    original_spec = dict(rt.TOKENS["CLEANING_PRICE"])
    assert original_spec.get("kind") != "gap", (
        "fixture assumption broken: CLEANING_PRICE must not already be a documented gap")
    try:
        rt.TOKENS["CLEANING_PRICE"] = dict(
            original_spec, kind="data_json",
            file="ZZZ_this_artifact_does_not_exist_anywhere.json", path=("x",))
        resolved, gaps, resolve_failures = gr.resolve_tokens_with_gaps()
    finally:
        rt.TOKENS["CLEANING_PRICE"] = original_spec
    assert "CLEANING_PRICE" not in gaps, (
        "a real resolution failure (missing artifact) was silently folded into `gaps`, "
        "the exact bug the fix addresses")
    assert "CLEANING_PRICE" not in resolved
    failure_names = [name for name, _ in resolve_failures]
    assert "CLEANING_PRICE" in failure_names, resolve_failures
    return "a non-gap token pointing at a missing artifact is reported as a real failure, not a gap"


@case
def case_resolve_tokens_with_gaps_still_treats_a_real_gap_as_a_gap():
    resolved, gaps, resolve_failures = gr.resolve_tokens_with_gaps()
    assert "INCENTIVE_STATUS" in gaps, gaps
    assert not any(name == "INCENTIVE_STATUS" for name, _ in resolve_failures), resolve_failures
    return "a genuine KNOWN_GAPS token (kind == 'gap') is still folded into `gaps`, not failures"


@case
def case_a_real_token_resolution_failure_blocks_the_full_run():
    _require_gitleaks()
    _require_household()
    original_spec = dict(rt.TOKENS["CLEANING_PRICE"])
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        try:
            rt.TOKENS["CLEANING_PRICE"] = dict(
                original_spec, kind="data_json",
                file="ZZZ_this_artifact_does_not_exist_anywhere.json", path=("x",))
            r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
        finally:
            rt.TOKENS["CLEANING_PRICE"] = original_spec
    assert not r["wrote"], "the run wrote a file despite a real token resolution failure"
    assert any(f[0] == "CLEANING_PRICE" and f[1] == "token-resolution-failed"
              for f in r["failures"]), r["failures"]
    return "a broken (non-gap) token blocks the entire run and is reported by name"


# ---------------------------------------------------------------------------
# Codex review (pass 2), finding 1 / issue #66: a --only run leaves every
# OTHER section's blocks unfilled, and render() splices the full,
# unfiltered template -- an out-of-scope block's raw <!-- TODO --> comment
# would land in the output. The guarantee that this never actually happened
# held only by accident (two KNOWN_GAPS tokens sitting in two different
# sections). Fix: --only may only be combined with --dry-run; a real
# (non-dry-run) run with --only is refused outright.
# ---------------------------------------------------------------------------
@case
def case_only_combined_with_dry_run_still_works_exactly_as_before():
    _require_gitleaks()
    result = gr.run(provider="anthropic", model="claude-fabricated-for-tests",
                    dry_run=True, only="s0", human_answers=_all_human_answers())
    assert result["dry_run"] is True
    assert not result["wrote"]
    return "--only combined with --dry-run is unaffected -- still just previews request bodies"


@case
def case_only_without_dry_run_is_refused_naming_both_flags():
    try:
        gr.run(provider="anthropic", model="claude-fabricated-for-tests", only="s0")
        raise AssertionError("run() accepted --only without --dry-run for a real run")
    except SystemExit as e:
        assert "--only" in str(e), e
        assert "--dry-run" in str(e), e
        assert "s0" in str(e), e
    return "--only without --dry-run is refused, naming both --only and --dry-run"


@case
def case_only_without_dry_run_is_refused_even_with_no_model_or_provider_given():
    """The --only guard must fire from the same 'this is a real run' branch
    as the --model/--provider checks, not bypass them or be bypassed by
    them -- confirms the check doesn't accidentally depend on model/provider
    having already been validated first."""
    try:
        gr.run(only="s7")
        raise AssertionError("run() accepted --only with no model, provider, or --dry-run")
    except SystemExit as e:
        assert "--model" in str(e) or "--only" in str(e), e
    return "an --only-only call (no provider/model/dry-run) still fails closed, one way or the other"


@case
def case_a_full_run_writes_with_no_review_recorded():
    """Issue #135, found by adversarial review. household.example.yaml leaves
    both provenance review fields null ON PURPOSE -- "nobody reviewed this" is
    the honest answer for a reproduction, and report_tokens.py refuses to
    render a name that would claim otherwise.

    That refusal must not reach this module's failure list. Every provenance
    token is overridden here, so their household values never reach the page;
    resolving them anyway recorded two blocking failures and the report did not
    write at all -- the documented default configuration broke the documented
    generation path."""
    _require_gitleaks()
    _require_household()
    import yaml
    node = yaml.safe_load((rt.ROOT / "household.example.yaml").read_text())
    assert node.get("provenance", {}).get("review_tool_independent") is None, (
        "household.example.yaml no longer ships a null review_tool_independent, so "
        "this case is not exercising the documented no-review default any more")

    real = rt.hh._cache
    hh_now = dict(rt.hh._load())
    hh_now["provenance"] = {"generation_tool": "Stand-In Generator (v0)",
                            "review_tool_independent": None,
                            "review_tool_adversarial": None}
    rt.hh._cache = hh_now
    try:
        for name in ("REVIEW_TOOL_1", "REVIEW_TOOL_2"):
            try:
                rt.resolve_token(name)
                raise AssertionError(
                    f"{name} resolved with a null review field, so this case is no "
                    "longer forcing the condition it exists to test")
            except SystemExit:
                pass
        with tempfile.TemporaryDirectory() as td:
            cache_dir = pathlib.Path(td) / "cache"
            dest_dir = pathlib.Path(td) / "dest"
            dest_dir.mkdir()
            manifest_path = pathlib.Path(td) / "manifest.json"
            r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
            assert r["wrote"], (
                "a run with no review recorded did not write; the provenance tokens "
                f"must not be resolved before they are overridden: {r['failures']}")
            assert r["failures"] == [], r["failures"]
            html_out = (dest_dir / "index.generated.html").read_text()
            assert "no independent or adversarial review" in html_out, (
                "the generated report does not state that no review happened")
            assert "{{" not in html_out
    finally:
        rt.hh._cache = real
    return ("a full run with both review fields null writes successfully and states "
            "that no independent or adversarial review was performed")


@case
def case_full_run_processes_every_single_block_before_reporting_wrote_true():
    """Codex's own regression test: a full (non --only) run where every
    in-scope block succeeds must return wrote=True only when EVERY block in
    the template was actually processed -- checked against report_blocks.
    CLASSIFICATION's full block count (independent of the current
    prose/data/human mix), and against the manifest's own blocks_total and
    empty failures list, not just the boolean."""
    _require_gitleaks()
    _require_household()
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
        assert r["wrote"], r["failures"]
        assert r["failures"] == []
        manifest = json.loads(manifest_path.read_text())
        assert manifest["blocks_total"] == len(rb.CLASSIFICATION), (
            manifest["blocks_total"], len(rb.CLASSIFICATION))
        assert manifest["failures"] == []
        html_out = (dest_dir / "index.generated.html").read_text()
        assert "TODO" not in html_out
        assert "{{" not in html_out
    return (f"a full run processes all {len(rb.CLASSIFICATION)} template blocks and reports "
           "wrote=True only once every one of them succeeded")


# ---------------------------------------------------------------------------
# AC (adversarial review finding 1): every REAL call to an LLM -- the first
# attempt, the corrective retry, and the --humanize pass -- must be preceded
# by preflight(). Before the fix, generate_prose_fragment's retry and
# humanize_fragment each called the LLM directly with no preflight() call
# anywhere in either function, so a corrective-retry prompt (which can embed
# excerpts of the model's own prior, rejected output) or a humanize prompt
# went out completely unscanned. preflighted_call() is now the ONE function
# in this module allowed to call llm_call/call_with_backoff for a real
# request; this is checked two ways: an AST guard (structural: no other
# function constructs that call), and a live call-count invariant across a
# full run with both a retry and --humanize in play (behavioral: the fix
# actually fires as many times as it should, not just "exists somewhere").
# ---------------------------------------------------------------------------
class _RealCallFinder(ast.NodeVisitor):
    """Finds every call to llm_call(...) or call_with_backoff(...), tagged
    with the enclosing function name, anywhere in generate_report.py."""
    def __init__(self):
        self.current = None
        self.hits = {}

    def visit_FunctionDef(self, node):
        prev = self.current
        self.current = node.name
        self.generic_visit(node)
        self.current = prev

    def visit_Call(self, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in ("llm_call", "call_with_backoff"):
            self.hits.setdefault(self.current, []).append(node.lineno)
        self.generic_visit(node)


@case
def case_preflighted_call_is_the_only_real_call_site():
    src = pathlib.Path(gr.__file__).read_text()
    finder = _RealCallFinder()
    finder.visit(ast.parse(src))
    # call_with_backoff itself calls llm_call -- that's the retry-loop
    # primitive, not a second unguarded call site. preflighted_call calling
    # call_with_backoff is the one sanctioned real-call site. Every other
    # function touching either name is an offender.
    allowed = {"call_with_backoff", "preflighted_call"}
    offenders = {fn: lines for fn, lines in finder.hits.items() if fn not in allowed}
    assert finder.hits, "no llm_call/call_with_backoff call found at all -- guard is broken"
    assert not offenders, f"real-call sites outside preflighted_call: {offenders}"
    return "llm_call/call_with_backoff are only ever invoked via preflighted_call"


@case
def case_preflight_call_count_matches_llm_call_count_with_a_retry_and_humanize_in_play():
    """Full-run, behavioral version of the invariant above: wraps the REAL
    lp.preflight (still doing the real gitleaks scan -- this is not mocked
    out) with a counter, and drives a scenario that forces exactly one
    corrective retry (the very first llm_call response fails the numeral
    guard) plus a --humanize pass on every block, then asserts the preflight
    call count equals the llm_call count exactly, not just "preflight was
    called at least once somewhere". Runs the FULL block set, not --only:
    since issue #66's fix, --only may only be combined with --dry-run (a
    real run leaves every out-of-scope block's raw TODO comment in the
    document otherwise), and this test needs a REAL run to exercise
    preflight()/llm_call() at all."""
    _require_gitleaks()
    _require_household()
    calls = []
    preflight_calls = []
    real_preflight = lp.preflight

    def counting_preflight(items, body_text, dry_run=False):
        preflight_calls.append(1)
        return real_preflight(items, body_text, dry_run=dry_run)

    first_call_done = {"flag": False}

    def fake_call(provider, model, system, user, max_tokens, env=None):
        calls.append(1)
        if not first_call_done["flag"]:
            first_call_done["flag"] = True
            return {"text": "saves $2,900/yr", "finish_reason": "end_turn", "usage": {}}
        return {"text": SAFE_FRAGMENT, "finish_reason": "end_turn", "usage": {}}

    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        old_preflight = gr.lp.preflight
        gr.lp.preflight = counting_preflight
        try:
            gr.run(provider="anthropic", model="claude-fabricated-for-tests",
                  human_answers=_all_human_answers(), env={}, cache_dir=cache_dir,
                  dest_dir=dest_dir, manifest_path=manifest_path, llm_call=fake_call,
                  humanize=True)
        finally:
            gr.lp.preflight = old_preflight

    n_prose = sum(1 for v in rb.CLASSIFICATION.values() if v == "prose")
    # 1 extra real call for the forced retry on the very first block processed,
    # plus one humanize call per block.
    expected = n_prose + 1 + n_prose
    assert len(calls) == expected, (len(calls), expected)
    assert len(preflight_calls) == len(calls), (
        f"preflight() was called {len(preflight_calls)} times but llm_call was called "
        f"{len(calls)} times -- every real call must be preceded by exactly one preflight()")
    return (f"preflight() was called exactly once per real llm_call ({len(calls)} calls, "
           "including the forced retry and every --humanize pass)")


# ---------------------------------------------------------------------------
# Full pipeline: cache warm/cold behavior, invalidation, provenance in the
# generated output, index.html untouched, and the consistency extensions.
# ---------------------------------------------------------------------------
def _run_full(cache_dir, dest_dir, manifest_path, llm_call, human_answers=None,
              only=None, humanize=False):
    return gr.run(provider="anthropic", model="claude-fabricated-for-tests",
                 human_answers=human_answers or _all_human_answers(),
                 env={}, cache_dir=cache_dir, dest_dir=dest_dir,
                 manifest_path=manifest_path, llm_call=llm_call, only=only,
                 humanize=humanize)


@case
def case_humanize_doubles_calls_when_cold_and_reuses_the_cache_when_warm():
    """Runs the FULL block set, not --only: since issue #66's fix, --only
    may only be combined with --dry-run, and this case needs a real
    (non-dry-run) run to exercise actual llm_call invocations and their
    caching. (An earlier version of this test used --only "s0" without
    --dry-run to keep the case fast, and its docstring incorrectly credited
    render()'s token-substitution pass with a partial-write guarantee that
    did not actually exist for that scenario -- see issue #66, filed from
    adversarial review pass 3. That mechanism, and the misleading docstring
    claim about it, are both gone now: --only without --dry-run is refused
    outright, so there is no partial-write scenario left to reason about
    here at all.)"""
    _require_gitleaks()
    _require_household()
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        calls = []
        fake = make_fake_call(calls=calls)
        n_prose = sum(1 for v in rb.CLASSIFICATION.values() if v == "prose")

        r1 = _run_full(cache_dir, dest_dir, manifest_path, fake, humanize=True)
        assert r1["wrote"], r1["failures"]
        assert len(calls) == 2 * n_prose, (len(calls), n_prose)

        r2 = _run_full(cache_dir, dest_dir, manifest_path, fake, humanize=True)
        assert r2["wrote"], r2["failures"]
        assert len(calls) == 2 * n_prose, "a warm-cache humanize re-run made new calls"
    return (f"--humanize makes 2 calls per prose block when cold ({2 * n_prose} for "
           f"{n_prose} prose blocks) and zero new calls on a warm-cache re-run")


@case
def case_full_run_writes_the_generated_file_and_second_run_makes_zero_new_calls():
    _require_gitleaks()
    _require_household()
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        calls = []
        fake = make_fake_call(calls=calls)

        r1 = _run_full(cache_dir, dest_dir, manifest_path, fake)
        assert r1["wrote"], r1["failures"]
        n_prose = sum(1 for v in rb.CLASSIFICATION.values() if v == "prose")
        assert len(calls) == n_prose, (len(calls), n_prose)
        first_bytes = (dest_dir / "index.generated.html").read_bytes()

        r2 = _run_full(cache_dir, dest_dir, manifest_path, fake)
        assert r2["wrote"], r2["failures"]
        assert len(calls) == n_prose, ("a warm-cache re-run made new LLM calls", len(calls))
        second_bytes = (dest_dir / "index.generated.html").read_bytes()
        assert first_bytes == second_bytes, "warm-cache re-run did not reproduce byte-identically"
    return (f"a full run makes exactly {n_prose} calls (one per prose block); a second, "
           "warm-cache run makes zero new calls and reproduces byte-identically")


@case
def case_changing_one_token_regenerates_only_the_blocks_in_its_scope():
    _require_gitleaks()
    _require_household()
    html = rt.TEMPLATE.read_text()
    affected = {b.id for b in rb.parse_todo_blocks(html)
               if rb.CLASSIFICATION[b.id] == "prose"
               and "CLEANING_PRICE" in rb.scope_tokens_for_block(html, b)}
    assert affected, "fixture assumption broken: no prose block scopes CLEANING_PRICE"

    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        calls = []
        fake = make_fake_call(calls=calls)

        r1 = _run_full(cache_dir, dest_dir, manifest_path, fake)
        assert r1["wrote"], r1["failures"]
        n_prose = sum(1 for v in rb.CLASSIFICATION.values() if v == "prose")
        assert len(calls) == n_prose

        original_spec = dict(rt.TOKENS["CLEANING_PRICE"])
        try:
            # The NUMBER, not "$999": CLEANING_PRICE declares fmt="usd0" (issue
            # #163), so the registry puts the sigil on and resolve_token's
            # finiteness gate refuses a pre-formatted string. This fixture only
            # needs the token's value to CHANGE, so what it hands over is
            # whatever the token's own declaration accepts.
            rt.TOKENS["CLEANING_PRICE"] = dict(original_spec, get=lambda ctx: 999)
            r2 = _run_full(cache_dir, dest_dir, manifest_path, fake)
        finally:
            rt.TOKENS["CLEANING_PRICE"] = original_spec
        assert r2["wrote"], r2["failures"]
        new_calls = len(calls) - n_prose
        assert new_calls == len(affected), (new_calls, affected)
    return (f"changing CLEANING_PRICE's resolved value triggered exactly {len(affected)} "
           f"new call(s), matching the block(s) whose scope named it: {sorted(affected)}")


@case
def case_no_human_answer_for_a_gap_token_refuses_to_write():
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    some_gap = sorted(rb.LIVE_GAP_TOKENS)[0]
    del answers[f"TOKEN:{some_gap}"]
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert not r["wrote"]
        assert any(f[0] == some_gap for f in r["failures"]), r["failures"]
        assert not (dest_dir / "index.generated.html").exists()
    return f"a missing human answer for the live gap token {some_gap} refuses to write anything"


@case
def case_no_human_answer_for_a_human_block_refuses_to_write():
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    del answers["s6#8"]   # hardware pricing footnote
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert not r["wrote"]
        assert any(f[0] == "s6#8" for f in r["failures"]), r["failures"]
        assert not (dest_dir / "index.generated.html").exists()
    return "a missing human answer for a human-classified block refuses to write anything"


# ---------------------------------------------------------------------------
# Codex review (pass 2), finding 2: a human-supplied --human-answers fragment
# went straight into `fragments[b.id]` with NO validation at all -- unlike
# LLM output, which now passes through find_html_structure_violations(). A
# malformed or careless --human-answers file (a typo, a copy-paste accident,
# or genuinely untrusted input) could inject <script>, an event-handler
# attribute, or unbalanced markup with nothing to catch it. Fix: run every
# human answer through the SAME HTML-structure validator (never the numeral
# guard -- a human answer is expected to state real, researched figures).
# ---------------------------------------------------------------------------
@case
def case_human_answer_containing_a_script_tag_is_rejected_by_name():
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    answers["s6#8"] = "<script>alert(document.domain)</script>"
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert not r["wrote"], "a <script>-carrying human answer was accepted"
        matching = [f for f in r["failures"] if f[0] == "s6#8"]
        assert matching, r["failures"]
        assert matching[0][1] == "human-answer-invalid-html", matching
        assert "script" in matching[0][2].lower(), matching
        assert not (dest_dir / "index.generated.html").exists()
    return "a human answer containing <script>...</script> is rejected, naming the block"


@case
def case_human_answer_with_an_event_handler_attribute_is_rejected():
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    answers["s6#8"] = '<b onerror="alert(1)">Tesla Powerwall 3 quoted at $12,500 installed</b>'
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert not r["wrote"]
        assert any(f[0] == "s6#8" and f[1] == "human-answer-invalid-html" for f in r["failures"]), (
            r["failures"])
    return "a human answer with an onerror= attribute is rejected, even carrying a real quote"


@case
def case_human_answer_with_real_numbers_and_allowlisted_tags_is_accepted():
    """The point of NOT running human answers through the numeral guard:
    a human is expected to write down a real, researched figure exactly
    like this -- '$2,900/yr' here is not invented, it is the operator's own
    reported quote, which the numeral guard would otherwise have rejected
    as a bare digit."""
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    answers["s6#8"] = ("<b>Tesla Powerwall 3</b> quoted at $12,500 installed by two local "
                       "installers as of this quote; <i>see</i> <a href=\"#s6\">§6</a> for "
                       "the arbitrage math.")
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert r["wrote"], r["failures"]
        html_out = (dest_dir / "index.generated.html").read_text()
        assert "$12,500" in html_out
        assert "quoted at" in html_out
    return ("a human answer using real dollar figures and only allowlisted inline tags is "
           "accepted, numbers and all")


@case
def case_find_html_structure_violations_is_the_gate_not_the_numeral_guard():
    """Direct unit check of the design choice itself: a human answer with a
    bare digit is NOT what find_html_structure_violations checks -- only
    the LLM path's find_fragment_violations() cares about that."""
    answer = "This actually costs $2,900/yr, quoted directly by the installer."
    assert gr.find_html_structure_violations(answer) == []
    return "find_html_structure_violations() does not care about bare digits at all"


@case
def case_review_clause_override_attempt_is_ignored():
    """The provenance review clause can NEVER be filled -- not even via
    --human-answers. A caller sneaking in a "TOKEN:REVIEW_TOOL_1" answer
    must have it silently ignored: apply_provenance_overrides() always wins
    on the token values, AND (since the Codex pass-3 fix) the whole fixed
    sentence those tokens used to sit in is replaced wholesale before the
    token-substitution pass ever runs, so there is no {{REVIEW_TOOL_1}}/
    {{REVIEW_TOOL_2}} placeholder left in the document for a sneaked-in
    value to fill even in principle."""
    _require_gitleaks()
    _require_household()
    answers = _all_human_answers()
    answers["TOKEN:REVIEW_TOOL_1"] = "Claude Code (Fable 5)"
    answers["TOKEN:REVIEW_TOOL_2"] = "Codex (GPT-5.6 Sol)"
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call(),
                     human_answers=answers)
        assert r["wrote"], r["failures"]
        html = (dest_dir / "index.generated.html").read_text()
        assert "Claude Code (Fable 5)" not in html
        assert "Codex (GPT-5.6 Sol)" not in html
        # PROVENANCE_SENTENCE_REPLACEMENT itself still contains the literal,
        # unsubstituted {{GENERATION_TOOL}} placeholder -- by the time it
        # reaches the FINAL rendered output that token has already been
        # substituted with the real provider/model, so check for a fixed
        # substring of the replacement that doesn't include the token.
        assert "no independent or adversarial review of this specific run has been performed" \
            in html
    return "a sneaked-in human-answers override for the review clause is ignored"


@case
def case_index_html_is_byte_unchanged_across_a_full_run():
    _require_gitleaks()
    _require_household()
    real_index = rt.ROOT / "index.html"
    if not real_index.is_file():
        raise SkipCase("this checkout has no index.html to protect")
    original_bytes = real_index.read_bytes()
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        shutil.copy(real_index, dest_dir / "index.html")
        manifest_path = pathlib.Path(td) / "manifest.json"
        r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
        assert r["wrote"], r["failures"]
        assert (dest_dir / "index.generated.html").is_file()
        assert (dest_dir / "index.html").read_bytes() == original_bytes, (
            "index.html in the promotion directory was modified by a run that must only "
            "ever touch index.generated.html")
    assert real_index.read_bytes() == original_bytes, "the REAL index.html changed on disk"
    return "index.html is byte-unchanged (both the promotion-dir copy and the real file)"


@case
def case_a_second_ranked_household_renders_packages_and_plans_on_one_stated_footing():
    """ISSUE #200's acceptance case: the FULL template rendered for a
    household ranked second, with the package figures and the plan comparison
    on a stated common footing.

    The fixture keeps the real household and the real artifacts and reprices
    exactly ONE rival -- a plan mid_package_on_plans DOES price -- $1 below
    the household's plan in report_tokens' parsed plan_results rows
    (trt._plan_repriced; generate_report resolves tokens through the same
    module object, so the substitution is visible to gr.run and restored on
    the way out). That is the household issue #196 chromed and issue #200
    prices for.

    THE COMMON FOOTING is asserted MECHANICALLY, not as words alone: the
    ranking (data/plan_results.csv, analyze_norelief.py's published-table
    rate engine) and the switch pricing (battery_plan_matrix.json, its own
    published-table engine) are ONE basis family, tied out by the matrix
    generator's own fail-closed assert (each plan's no_battery within $1.00
    of the CSV's CEA total, then rounded). This case re-asserts that tie-out
    on the winner's row, checks the arithmetic identity behind "baseline =
    the same plan's no-package year" (package_save = no_battery -
    package_bill, up to independent roundings), and then requires the
    rendered section-7 sentence to NAME that basis where the figure is
    quoted: "published-table rates" and "battery×plan matrix". The figure
    itself is read HERE off mid_package_on_plans (never hardcoded) with its
    baseline named in the same sentence: the same plan's no-package year,
    one rate vintage. And sections 0 and 3 of the same rendered file state
    the same standing, so the plan comparison and the package figures
    cannot tell two stories."""
    _require_gitleaks()
    _require_household()
    provider, cheapest, priced = trt._plan_ranking_inputs()
    own = float(next(r["total"] for r in priced if r["plan"] == cheapest))
    mid = rt._json("battery_plan_matrix.json")["mid_package_on_plans"]
    rival = next((r["plan"] for r in sorted(priced, key=lambda r: float(r["total"]))
                  if r["plan"] != cheapest and r["plan"] in mid["plans"]), None)
    assert rival is not None, (
        f"no rival data/plan_results.csv prices for {provider!r} is also in "
        f"mid_package_on_plans ({sorted(mid['plans'])}); this case needs one to "
        "put a priced winner ahead of the household")
    save = mid["plans"][rival]["package_save"]

    # (0) THE ARTIFACT RELATIONSHIP THE SENTENCE RESTS ON, asserted before a
    # single line is rendered. battery_plan_matrix.py fail-closed asserts each
    # plan's no_battery within $1.00 of plan_results.csv's CEA total (its ref
    # reads the provider == "CEA" rows) BEFORE rounding, then stores
    # round(no_b) -- one rounding, adding at most $0.50 -- so the stored cell
    # can sit at most $1.50 from the CSV total; $2.00 bounds that with margin.
    # (Not $3.00: that figure belongs to test_report_consistency's comparison
    # of two MARGINS, where two such cells each contribute, and is too loose
    # for a single level.) Read the COMMITTED csv, not trt's parsed rows: the
    # repricing fixture below edits rt's PARSED rows only, and the matrix was
    # built against the committed totals -- the committed file is the side
    # the tie-out actually ran against.
    #
    # DESIGN DECISION, stated so nobody "fixes" it: the fixture's CSV
    # repricing is deliberately COUNTERFACTUAL -- it exists to force the
    # trails branch, not to describe a real ranking -- so no assertion here
    # is about the synthetic repriced total, and the matrix is NOT repriced
    # to match it. The production guarantee that ranking and matrix agree is
    # the GENERATOR's own fail-closed $1 tie-out (mutation-tested in
    # test_battery_plan_matrix.py); what this assertion pins is
    # committed-CSV-to-committed-matrix coherence at render time.
    committed = list(csv.DictReader(
        (rt.ROOT / "data" / "plan_results.csv").read_text().splitlines()))
    csv_total = float(next(r["total"] for r in committed
                           if r["provider"] == "CEA" and r["plan"] == rival))
    no_batt = rt._json("battery_plan_matrix.json")["plans"][rival]["no_battery"]
    assert abs(no_batt - csv_total) <= 2.0, (
        f"the ranking and the switch pricing are NOT on one footing: "
        f"battery_plan_matrix.json plans[{rival!r}].no_battery (${no_batt:,}) is "
        f"${abs(no_batt - csv_total):,.2f} from plan_results.csv's CEA total "
        f"(${csv_total:,.2f}) -- past the generator's own $1.00 pre-rounding "
        "tie-out plus its one $0.50 rounding, so the two artifacts no longer "
        "reconcile")
    # ... and the identity that makes "baseline = the same plan's no-package
    # year" checkable: package_save = no_battery - package_bill, up to $1
    # because the three fields are rounded independently by the generator.
    pkg_bill = mid["plans"][rival]["package_bill"]
    assert abs(save - (no_batt - pkg_bill)) <= 1, (
        f"mid_package_on_plans.plans[{rival!r}].package_save (${save:,}) is not "
        f"plans[{rival!r}].no_battery - package_bill (${no_batt:,} - ${pkg_bill:,} "
        f"= ${no_batt - pkg_bill:,}) -- the quoted save is not measured against "
        "the same plan's no-package year")

    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        with trt._plan_repriced(provider, {cheapest: own, rival: own - 1}):
            r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
            assert r["wrote"], (
                f"a household ranked second must still get a whole report; the run "
                f"refused: {r['failures']}")
            html = (dest_dir / "index.generated.html").read_text()

    # (1) Section 7's opening paragraph carries the trails footing, priced
    # clause included -- located as the one paragraph the fixed markup pins.
    start = html.index(f"All packages keep <b>{cheapest}</b>")
    para = html[start:html.index("</p>", start)]
    assert "the plan this house is on, not the cheapest one" in para, para
    assert f"{rival} prices lower in the rate plan comparison above" in para, para
    assert "none of the savings below includes switching to it" in para, para

    # (2) The quoted figure IS the artifact's package_save for the repriced
    # winner, formatted the way report_tokens formats dollars.
    assert f"Re-billed end-to-end on {rival}" in para, para
    assert f"saves ${save:,.0f}/yr" in para, (
        f"section 7 does not quote mid_package_on_plans.plans[{rival!r}]"
        f".package_save (${save:,.0f}/yr): {para!r}")
    # ... with the baseline stated in the same breath: same plan, no-package
    # year, one rate vintage. That is the stated common footing.
    assert ("no-package" in para and "same plan" in para
            and "one rate vintage" in para), para
    # ... and the footing's PROVENANCE stated where the figure is quoted, not
    # implied: the sentence (_s7_switch_pricing) must name the basis family
    # ("published-table rates" -- the same family plan_results.csv is priced
    # on) and the artifact ("battery×plan matrix") the reader can check it in.
    assert "published-table rates" in para, (
        f"section 7 quotes the switch figure without naming its rate basis "
        f"(expected 'published-table rates'): {para!r}")
    assert "battery×plan matrix" in para, (
        f"section 7 quotes the switch figure without naming the battery×plan "
        f"matrix it is read from: {para!r}")

    # (3) Sections 0 and 3 of the same file state the same standing.
    assert "a cheaper rate plan exists" in html, (
        "section 0 does not tell the second-ranked household a cheaper plan exists")
    assert f"{cheapest} is not the cheapest plan for this house" in html, (
        "section 3 does not report the trails standing section 7 is footed on")

    # (4) A complete render: nothing unsubstituted survives.
    assert "{{" not in html, "a {{TOKEN}} survived substitution"
    assert "TODO" not in html, "a literal TODO string survived generation"
    return (f"the full template renders for a household repriced second behind {rival}; "
            f"section 7 quotes the MID package at ${save:,.0f}/yr re-billed on that "
            "plan with its baseline named, sections 0 and 3 state the same standing, "
            "and no {{ or TODO survives")


# ---------------------------------------------------------------------------
# Extended consistency checks against the fully generated file (issue #39
# part 6) -- run once, share the rendered HTML across several assertions.
# ---------------------------------------------------------------------------
_GENERATED_CACHE = {}


def _generated_html():
    if "html" not in _GENERATED_CACHE:
        _require_gitleaks()
        _require_household()
        with tempfile.TemporaryDirectory() as td:
            cache_dir = pathlib.Path(td) / "cache"
            dest_dir = pathlib.Path(td) / "dest"
            dest_dir.mkdir()
            manifest_path = pathlib.Path(td) / "manifest.json"
            r = _run_full(cache_dir, dest_dir, manifest_path, make_fake_call())
            assert r["wrote"], r["failures"]
            _GENERATED_CACHE["html"] = (dest_dir / "index.generated.html").read_text()
    return _GENERATED_CACHE["html"]


@case
def case_generated_output_has_zero_surviving_double_brace_or_todo():
    html = _generated_html()
    assert "{{" not in html, "a {{TOKEN}} survived substitution"
    assert "TODO" not in html, "a literal TODO string survived generation"
    return "the generated file has zero surviving {{ or TODO substrings"


@case
def case_generated_output_preserves_the_advanced_tier_and_its_four_mitigations():
    html = _generated_html()
    assert '<details id="advanced" class="advanced">' in html
    assert "function openHashTarget" in html
    assert "const lazyChart=" in html and "runLazyCharts" in html
    assert "addEventListener('beforeprint'" in html
    assert "tier-closed" in html
    return "the advanced <details> tier and its four required JS mitigations all survive"


@case
def case_generated_output_preserves_the_day_band_and_four_s5_canvases():
    html = _generated_html()
    assert 'class="dayband"' in html
    for cid in ("hourly", "battery", "monthly", "periods"):
        assert f'<canvas id="{cid}"' in html, f"missing canvas#{cid}"
    return "the day-band markup and all four §5 canvases survive generation"


@case
def case_generated_chart_arrays_match_their_committed_artifacts():
    html = _generated_html()
    rd = json.loads((rt.ROOT / "data" / "report_data.json").read_text())

    def array(name):
        m = re.search(re.escape(name) + r":\s*(\[[^\]]*\])", html)
        assert m, f"{name} not found in generated output"
        return json.loads(m.group(1))

    got = array("hourlyS_imp")
    want = rd["hourly_S"]["imp"]
    assert len(got) == len(want) and all(abs(a - b) < 1e-6 for a, b in zip(got, want)), (
        "hourlyS_imp in the generated file does not match report_data.json")
    return "the generated file's chart arrays (e.g. hourlyS_imp) match their committed artifact"


@case
def case_generated_output_contains_only_report_tokens_style_provenance():
    """Since the Codex pass-3 fix, the whole fixed review-claim sentence is
    replaced (rewrite_provenance_sentence) rather than just its two tool-
    name tokens -- REVIEW_DISCLAIMER (still set by apply_provenance_
    overrides, as defense in depth) no longer appears in the output at all,
    because the sentence that used to carry it is gone."""
    html = _generated_html()
    assert "anthropic (claude-fabricated-for-tests)" in html
    assert gr.REVIEW_DISCLAIMER not in html, (
        "the old two-token-only fix's placeholder text survived -- the whole sentence "
        "should have been replaced instead")
    return "the generated provenance sentence names the actual provider/model"


# ---------------------------------------------------------------------------
# Codex review (pass 3), finding 1: swapping only REVIEW_TOOL_1/REVIEW_TOOL_2
# for a disclaimer did not neutralize the FIXED SURROUNDING GRAMMAR ("were
# then independently reviewed... and adversarially reviewed...", "was
# subsequently re-worked... to incorporate the findings of both reviews"),
# which independently asserted a review-and-rework process regardless of
# what the two tokens said. The prior test only checked the two hardcoded
# tool names were absent -- never the sentence's actual MEANING, which is
# exactly the gap Codex found.
# ---------------------------------------------------------------------------
_REVIEW_CLAIM_PHRASES = [
    "were then independently reviewed",
    "adversarially reviewed",
    "to incorporate the findings of both reviews",
    "independently reviewed with",
]


@case
def case_generated_output_never_claims_a_review_or_rework_occurred():
    html = _generated_html()
    for phrase in _REVIEW_CLAIM_PHRASES:
        assert phrase not in html, (
            f"the generated report claims a review process via {phrase!r}, which for this "
            "run never happened")
    assert "no independent or adversarial review of this specific run has been performed" \
        in html
    return "the generated report's provenance sentence makes no review/rework claim at all"


@case
def case_rewrite_provenance_sentence_replaces_the_literal_template_text():
    template_text = rt.TEMPLATE.read_text()
    assert gr.PROVENANCE_SENTENCE_LITERAL in template_text, (
        "fixture assumption broken: the literal sentence text no longer matches "
        "report-template.html verbatim")
    out = gr.rewrite_provenance_sentence(template_text)
    assert gr.PROVENANCE_SENTENCE_LITERAL not in out
    assert gr.PROVENANCE_SENTENCE_REPLACEMENT in out
    return "rewrite_provenance_sentence() replaces the exact literal sentence from the template"


@case
def case_rewrite_provenance_sentence_fails_closed_if_the_literal_is_missing():
    try:
        gr.rewrite_provenance_sentence("<p>no provenance sentence anywhere in here</p>")
        raise AssertionError("rewrite_provenance_sentence accepted text with no match at all")
    except SystemExit as e:
        assert "provenance sentence" in str(e), e
    return "rewrite_provenance_sentence() fails closed when the literal sentence isn't found"


# ---------------------------------------------------------------------------
# Adversarial review finding 4: render()'s token substitution had no
# HTML-escaping at all.
# ---------------------------------------------------------------------------
@case
def case_render_html_escapes_a_token_value_containing_markup():
    """Uses the real template (fill_chart_data() needs its real const D
    placeholders to exist) with one fabricated evil value injected for a
    real, live-in-HTML-text token (BEST_PLAN appears directly in plain
    markup, e.g. inside a .card div). Every other token is deliberately left
    unresolved -- irrelevant to this narrow check, which only cares whether
    the ONE substituted value comes out escaped."""
    template_text = rt.TEMPLATE.read_text()
    evil = "<script>alert(1)</script> & \"quoted\" 'single'"
    rendered, missing = gr.render(template_text, fragments={}, resolved={"BEST_PLAN": evil})
    assert "<script>alert(1)</script>" not in rendered, "a raw <script> tag survived unescaped"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered, rendered[:4000]
    assert "&amp;" in rendered
    assert "&quot;quoted&quot;" in rendered
    assert "&#x27;single&#x27;" in rendered
    return "render() HTML-escapes a resolved token value before substituting it"


@case
def case_fill_chart_data_and_render_do_not_double_process_the_same_values():
    """fill_chart_data() writes const D's array values straight from
    artifact JSON via json.dumps, never through the `resolved` token map or
    render()'s _sub() -- so HTML-escaping tokens cannot also mangle chart
    data, and chart data cannot leak back through the token substitution
    pass. Confirmed structurally (fill_chart_data takes no `resolved`
    argument at all) and behaviorally (the full generated file's chart
    arrays still match their artifact byte-for-byte after HTML-escaping was
    added -- see case_generated_chart_arrays_match_their_committed_artifacts)."""
    import inspect
    sig = inspect.signature(gr.fill_chart_data)
    assert list(sig.parameters) == ["html"], (
        "fill_chart_data must only ever see the document text, never the resolved "
        "token map, or it could re-process a value _sub() already escaped")
    return "fill_chart_data() has no access to the resolved token map at all"


# ---------------------------------------------------------------------------
# ATTRIBUTE-ONLY TOKENS ARE NOT PROSE. report_blocks.scope_tokens_for_block
# reads a block's <h2> section for the tokens LIVE in it and knows nothing
# about where in the markup each one lands, so a token that only ever fills
# an HTML attribute arrives in scope beside the figures. build_scope_values
# is the one place that decides, and these three cases hold it to that from
# the three directions it matters: what the model is offered, what a returned
# fragment may cite, and what the egress gate is asked to label.
#
# NO ARCHIVE NEEDED, deliberately -- the question is which NAMES survive the
# filter, not what they resolve to, so `resolved` is stubbed and every case
# below runs in a bare checkout where a bad merge would be caught.
# ---------------------------------------------------------------------------
def _stub_resolved():
    """A resolved-map stand-in: every non-gap token present with a value that
    carries no digit (so the numeral guard's own rules never confuse a scope
    question) and no gap token at all, exactly as resolve_tokens_with_gaps
    splits them."""
    gap_names = {n for n, spec in rt.TOKENS.items() if spec.get("kind") == "gap"}
    resolved = {n: "stub value" for n in rt.TOKENS if n not in gap_names}
    return resolved, gap_names


@case
def case_an_attribute_only_token_is_in_a_sections_tokens_but_never_in_its_scope():
    html = rt.TEMPLATE.read_text()
    resolved, gap_names = _stub_resolved()
    attribute_only = {n for n in rt.TOKENS if rt.is_attribute_only(n)}
    assert attribute_only, (
        "no token declares attribute_only, so this case and the two below prove nothing; "
        "if the flag has been retired, retire them with it")

    in_raw_scope, in_built_scope, unexplained = set(), set(), {}
    for b in rb.parse_todo_blocks(html):
        raw = rb.scope_tokens_for_block(html, b)
        built = set(gr.build_scope_values(html, b, resolved, gap_names))
        in_raw_scope |= raw & attribute_only
        in_built_scope |= built & attribute_only
        # Everything build_scope_values dropped must be explained by one of
        # its three documented reasons -- not in TOKENS, a gap, or
        # attribute-only. Any other name leaving is this change reaching
        # further than it was meant to.
        dropped = (raw & set(rt.TOKENS)) - gap_names - built
        if dropped - attribute_only:
            unexplained[b.id] = sorted(dropped - attribute_only)
    assert not unexplained, (
        f"build_scope_values dropped token(s) no rule of its own accounts for: {unexplained}")
    assert in_raw_scope == attribute_only, (
        f"only {sorted(in_raw_scope)} of the attribute-only token(s) {sorted(attribute_only)} "
        "is live in any block's section, so the exclusion is untested for the rest")
    assert not in_built_scope, (
        f"attribute-only token(s) {sorted(in_built_scope)} are still in a block's LLM scope")
    return (f"{sorted(attribute_only)} is live in a section and so reaches "
            f"scope_tokens_for_block, but is in no block's LLM scope; nothing else was "
            "dropped from any of the 105 blocks")


@case
def case_the_numeral_guard_rejects_a_fragment_citing_an_attribute_only_token():
    """The guard accepts any REAL token that is in the originating block's
    scope, and a CSS class name carries no digit for the word-number or
    numeral rules to object to -- so scope membership is the only thing
    standing between "{{S4_ROW_CLASS}}" and a published sentence reading
    "the matrix rates this plan win." Both sides are run here: the same
    fragment against the same block's scope with the token restored (accepted)
    and as build_scope_values now builds it (rejected)."""
    html = rt.TEMPLATE.read_text()
    resolved, gap_names = _stub_resolved()
    attribute_only = sorted(n for n in rt.TOKENS if rt.is_attribute_only(n))
    blocks = [b for b in rb.parse_todo_blocks(html)
             if rb.CLASSIFICATION[b.id] == "prose"
             and rb.scope_tokens_for_block(html, b) & set(attribute_only)]
    assert blocks, ("fixture assumption broken: no PROSE block's section carries an "
                    "attribute-only token, so no fragment could have cited one")
    checked = []
    for b in blocks:
        scope = set(gr.build_scope_values(html, b, resolved, gap_names))
        for name in attribute_only:
            fragment = f"The matrix rates this plan {{{{{name}}}}}."
            was = gr.find_fragment_violations(fragment, allowed_tokens=scope | {name})
            now = gr.find_fragment_violations(fragment, allowed_tokens=scope)
            assert was == [], (
                f"fixture assumption broken: with {name} in scope the guard already "
                f"rejected the fragment for another reason ({was}), so this case would "
                "pass whether or not the exclusion works")
            assert now, (f"{b.id}: the guard still accepts a fragment citing {name}")
            checked.append((b.id, name))
    return (f"a fragment citing an attribute-only token is accepted when the token is in "
            f"scope and rejected once build_scope_values removes it, across "
            f"{len(checked)} block/token pair(s): {checked}")


@case
def case_an_attribute_only_token_reaches_neither_the_prompt_nor_the_egress_gate():
    """The two other consumers of a block's scope. build_user_prompt lists
    every scoped value under "Values you may cite by writing their {{TOKEN}}
    name"; build_preflight_items labels each one for llm_providers.preflight,
    and _is_household_sourced answers True for S4_ROW_CLASS (its class is
    decided partly by household.plan), which would route a CSS class name
    through the household-token gate. Excluded upstream, it reaches neither."""
    html = rt.TEMPLATE.read_text()
    resolved, gap_names = _stub_resolved()
    attribute_only = [n for n in rt.TOKENS if rt.is_attribute_only(n)]
    offered, labelled = [], []
    for b in rb.parse_todo_blocks(html):
        if rb.CLASSIFICATION[b.id] != "prose":
            continue
        scope = gr.build_scope_values(html, b, resolved, gap_names)
        prompt = gr.build_user_prompt(b, scope)
        items = gr.build_preflight_items(b, scope)
        for name in attribute_only:
            if f"  {name} = " in prompt:
                offered.append((b.id, name))
            if any(kind == "household_token" and value[0] == name for kind, value in items):
                labelled.append((b.id, name))
    assert not offered, f"attribute-only token(s) still offered as citable prose: {offered}"
    assert not labelled, (
        f"attribute-only token(s) still sent to the egress gate as household values: "
        f"{labelled}")
    assert any(gr._is_household_sourced(n) for n in attribute_only), (
        "fixture assumption broken: no attribute-only token is household-sourced, so the "
        "preflight half of this case proves nothing")
    return (f"no prose block offers {attribute_only} in its user prompt or hands it to "
            "preflight as a household_token, though _is_household_sourced still calls it "
            "household-sourced")


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
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
