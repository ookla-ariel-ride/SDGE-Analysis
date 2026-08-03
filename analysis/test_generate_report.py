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
import json
import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import generate_report as gr   # noqa: E402
import llm_providers as lp     # noqa: E402
import report_blocks as rb     # noqa: E402
import report_tokens as rt     # noqa: E402

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


def _all_human_answers():
    """Fabricated operator-supplied answers covering every human-classified
    block AND every live KNOWN_GAPS token EXCEPT the provenance review
    clause, which this module refuses to accept an override for at all (see
    case_review_clause_override_attempt_is_ignored below)."""
    answers = {bid: f"<p>Operator-supplied answer for {bid} (fabricated for this test).</p>"
              for bid in rb.HUMAN_REASONS}
    for name in sorted(rb.LIVE_GAP_TOKENS):
        answers[f"TOKEN:{name}"] = f"(operator-supplied placeholder for {name})"
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


@case
def case_numeral_guard_still_accepts_clean_prose_with_no_quantity_words():
    text = ("{{BEST_PLAN}} stays cheapest with or without a battery; see §3 for the "
           "full comparison and no one disputes the ranking.")
    assert gr.find_fragment_violations(text) == [], gr.find_fragment_violations(text)
    return "ordinary prose (including 'no one', an indefinite pronoun) is not flagged"


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
    resolved, gaps = gr.resolve_tokens_with_gaps()
    resolved = gr.apply_provenance_overrides(resolved, "openai", "gpt-9-fabricated")
    assert resolved["GENERATION_TOOL"] == "openai (gpt-9-fabricated)"
    return "GENERATION_TOOL reflects the actual provider/model passed to this run"


@case
def case_review_tools_are_always_the_disclaimer_never_the_hardcoded_report_tokens_values():
    resolved, gaps = gr.resolve_tokens_with_gaps()
    original_review_1 = resolved["REVIEW_TOOL_1"]
    overridden = gr.apply_provenance_overrides(resolved, "anthropic", "claude-fabricated")
    assert overridden["REVIEW_TOOL_1"] == gr.REVIEW_DISCLAIMER
    assert overridden["REVIEW_TOOL_2"] == gr.REVIEW_DISCLAIMER
    assert overridden["REVIEW_TOOL_1"] != original_review_1, (
        "report_tokens.py's own hardcoded REVIEW_TOOL_1 must never survive into "
        "generate_report.py's output")
    assert "Claude Code" not in overridden["REVIEW_TOOL_1"]
    assert "Codex" not in overridden["REVIEW_TOOL_2"]
    return "REVIEW_TOOL_1/2 are always the fixed disclaimer, never report_tokens.py's values"


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
    called at least once somewhere"."""
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
                  only="s0", humanize=True)
        finally:
            gr.lp.preflight = old_preflight

    n_s0_prose = sum(1 for bid, k in rb.CLASSIFICATION.items()
                     if k == "prose" and bid.startswith("s0#"))
    # 1 extra real call for the forced retry on the very first block processed,
    # plus one humanize call per block.
    expected = n_s0_prose + 1 + n_s0_prose
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
    """--only restricts which blocks are PROCESSED (and cached), not what the
    final render requires -- a partial run like this one is expected to leave
    render()'s global token pass unable to resolve the OTHER blocks' own
    comment-only example tokens, so wrote stays False. That's the correct
    "never write a partial file" behavior; this case only exercises the call
    count, via the same run() the full-pipeline cases use elsewhere."""
    _require_gitleaks()
    _require_household()
    with tempfile.TemporaryDirectory() as td:
        cache_dir = pathlib.Path(td) / "cache"
        dest_dir = pathlib.Path(td) / "dest"
        dest_dir.mkdir()
        manifest_path = pathlib.Path(td) / "manifest.json"
        calls = []
        fake = make_fake_call(calls=calls)
        n_s0_prose = sum(1 for bid, k in rb.CLASSIFICATION.items()
                         if k == "prose" and bid.startswith("s0#"))

        _run_full(cache_dir, dest_dir, manifest_path, fake, only="s0", humanize=True)
        assert len(calls) == 2 * n_s0_prose, (len(calls), n_s0_prose)

        _run_full(cache_dir, dest_dir, manifest_path, fake, only="s0", humanize=True)
        assert len(calls) == 2 * n_s0_prose, "a warm-cache humanize re-run made new calls"
    return (f"--humanize makes 2 calls per prose block when cold ({2 * n_s0_prose} for "
           f"{n_s0_prose} s0 blocks) and zero new calls on a warm-cache re-run")


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
            rt.TOKENS["CLEANING_PRICE"] = dict(original_spec, get=lambda ctx: "$999")
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


@case
def case_review_clause_override_attempt_is_ignored():
    """The provenance review clause can NEVER be filled -- not even via
    --human-answers. A caller sneaking in a "TOKEN:REVIEW_TOOL_1" answer
    must have it silently ignored (apply_provenance_overrides always wins)."""
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
        assert gr.REVIEW_DISCLAIMER in html
    return "a sneaked-in human-answers override for the review clause is ignored; the disclaimer wins"


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
    html = _generated_html()
    assert "anthropic (claude-fabricated-for-tests)" in html
    assert gr.REVIEW_DISCLAIMER in html
    return "the generated provenance sentence names the actual provider/model and the disclaimer"


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
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
