#!/usr/bin/env python3
"""The orchestrator for issue #39 part 5: fills report-template.html from the
committed artifacts and a reader's own paid LLM key, and writes
index.generated.html -- never index.html.

PIPELINE
  1. Parse the template (report_blocks.parse_todo_blocks) and validate the
     classification map covers it exactly (report_blocks.validate_classification).
  2. Resolve every token in report_tokens.TOKENS, separating real values from
     KNOWN_GAPS "gap" tokens report_tokens.py cannot source at all.
  3. Override three tokens that live in CLAUDE.md section 11's provenance
     sentence -- see PROVENANCE OVERRIDES below. This is the one deliberate
     departure from a literal reading of report_tokens.py's own values, and
     it is load-bearing for this module's privacy/honesty guarantees.
  4. For every TODO block, by its report_blocks.CLASSIFICATION:
       - "data": call its report_blocks.DATA_BUILDERS function. No model
         call, no numeral guard (the digits are read straight from a
         committed artifact by report_blocks.py's own code, the same trust
         boundary report_tokens.py's "derived" tokens already carry).
       - "human": splice a caller-supplied answer if one was given (via
         --human-answers), a literal string this script never invents;
         otherwise the block is left unresolved and reported.
       - "prose": build a per-block request (that block's own TODO text plus
         ONLY the token values in its report_blocks.scope_tokens_for_block
         scope -- never the surrounding HTML), run it through
         llm_providers.preflight(), then llm_providers.call(). The returned
         fragment is checked by the numeral guard (find_fragment_violations);
         a violation gets ONE corrective retry, then the block hard-fails.
         Successful fragments are cached under private/report_cache/ keyed by
         a hash of the block id, PROMPT_VERSION, its exact scoped token
         values, its own TODO text, the provider and the model -- so a
         second run with nothing changed makes zero new calls, and changing
         one artifact value invalidates only the blocks whose scope included
         the token that moved.
  5. If any block, or any LIVE KNOWN_GAPS token (report_blocks.LIVE_GAP_TOKENS)
     with no --human-answers override, is left unresolved, NOTHING is
     written: every failure is collected and reported, and the run exits
     non-zero. Never a partial file.
  6. Otherwise: fill the <script> block's `const D` chart-data placeholders
     (see CHART DATA below) with the same literal-substring-and-assert
     technique the rest of this module uses, splice every TODO block's
     fragment into the template at its exact comment span, then run ONE
     global {{TOKEN}} substitution pass over the whole document (this also
     resolves any {{TOKEN}} a data-builder's own output referenced, e.g.
     {{PEAK_WINDOW}} inside the price-map table). Stage the result to a temp
     file and hand it to analysis.publish.
     promote_set() as "index.generated.html" -- the same crash-consistent,
     single-writer promotion battery_plan_matrix.py and friends use for
     data/*.json. index.html is never touched.

PROVENANCE OVERRIDES (CLAUDE.md section 11 / issue #39's own explicit design)
  report_tokens.py's GENERATION_TOOL/REVIEW_TOOL_1/REVIEW_TOOL_2 are
  cited_constant tokens hardcoded to "Claude Cowork (Fable 5)" / "Claude Code
  (Fable 5)" / "Codex (GPT-5.6 Sol)" -- the literal values CLAUDE.md section
  11 requires for THIS repo's own hand-curated index.html. Using them
  verbatim in a FORK's generated report would be false on two counts: it
  would claim a tool this run never used, and it would assert an
  independent + adversarial review that never happened. This module
  overrides all three, always, for its own output only (report_tokens.py
  itself is untouched, so index.html's provenance sentence is unaffected):
    - GENERATION_TOOL -> "{provider} ({model})", the ACTUAL provider/model
      this run used.
    - REVIEW_TOOL_1 and REVIEW_TOOL_2 -> REVIEW_DISCLAIMER, a fixed,
      digit-free, non-claiming string. This is intentionally NEVER
      overridable, by --human-answers or anything else: the issue's own
      text names "the script never emits any review-claim text under any
      circumstance, full stop" as the simplest correct design, and that is
      what test_generate_report.py asserts. A human who has genuinely had
      this report reviewed edits that one sentence by hand afterward; this
      script will not put words about a review into their mouth.

CHART DATA
  report-template.html's <script> block declares `const D = {...}`, whose
  chart-series fields are placeholder JS comments (e.g.
  `hourlyS_imp:[/* 24 hourly values -- summer avg-kW grid import */]`), plus
  two similarly-commented array literals inline in the §5 periods chart's own
  config. These are not {{TOKEN}} references or <!-- TODO --> blocks -- a
  different fill-in shape report_blocks.py's 105-block classification does
  not cover -- but they are exactly the "data" class the issue describes
  ("table rows... chart series, the const D block... day-band stamps"), and
  a generated report with empty charts would not be a usable deliverable.
  fill_chart_data() replaces each placeholder with real values from
  data/report_data.json, data/battery_dispatch_policies.json,
  data/carbon_fullyear_results.json and data/tou_spread.json, using the
  SAME derivations test_report_consistency.py's existing cases already
  check index.html's hand-written arrays against (bat_now_S = hourly_S.imp
  rounded to 2dp, spLabels/spSummer/spWinter/spBreak built by merging both
  seasons' series exactly as case_spread_chart_series_match_their_artifact
  reads them back) -- so the same test file's existing idioms apply
  unchanged to the generated output. Every replacement is an EXACT literal
  substring match, asserted present before replacing, so a template edit
  that changes the placeholder wording fails closed instead of silently
  leaving an empty array.

NUMERAL GUARD
  A prose fragment is rejected if it contains a digit that is not inside
  EITHER a {{TOKEN}} reference naming a real report_tokens.TOKENS entry, OR
  a "§N" section reference. NUMERAL_LITERAL_ALLOWLIST is an additional,
  committed set of exact literal substrings exempted from the digit scan; it
  starts EMPTY and stays that way unless a genuinely unavoidable case turns
  up (none has) -- CLAUDE.md section 0 requires every figure trace to a
  script, and a token is that trace, so this repo does not pre-populate the
  allowlist "just in case."
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm_providers as lp   # noqa: E402
import prose_lint             # noqa: E402
import publish               # noqa: E402
import report_blocks as rb   # noqa: E402
import report_tokens as rt   # noqa: E402

ROOT = rt.ROOT
DEFAULT_CACHE_DIR = ROOT / "private" / "report_cache"
DEFAULT_MANIFEST_PATH = ROOT / "private" / "report_generation_manifest.json"

PROMPT_VERSION = "generate_report/v1"
MAX_TOKENS = 700

REVIEW_DISCLAIMER = ("no independent or adversarial review is recorded for this "
                     "generation run -- confirm and edit this sentence by hand "
                     "before publishing if a real review has happened")

# Normal (non-truncated, non-error) finish reasons per vendor's own vocabulary
# -- anything else is treated the same as a numeral-guard failure: one retry,
# then a hard fail for that block. Never spliced in as a partial fragment.
NORMAL_FINISH = {
    "anthropic": {"end_turn", "stop_sequence"},
    "openai": {"stop"},
    "google": {"STOP"},
}

SYSTEM_PROMPT = (
    "You are filling in exactly ONE numbered slot of a pre-built energy analysis "
    "report template. You will be given that slot's own original authoring "
    "instruction and a list of ALREADY-COMPUTED values you may cite. Rules, "
    "enforced mechanically after you answer:\n"
    "1. Write ONLY the replacement HTML fragment for this one slot -- no "
    "surrounding HTML, no other slot's content, no <script> or <style>.\n"
    "2. Never write a bare digit. Every quantity must be written as "
    "{{TOKEN_NAME}} using one of the token names you were given verbatim -- "
    "never invent a token name, never paraphrase a number into words. A "
    "reference to a report section is written as §N (e.g. §3), which is "
    "allowed and is not a token.\n"
    "3. If the instruction asks for a fact you were not given a token for, "
    "write around it in qualitative language rather than inventing a figure.\n"
    "4. Avoid rule-of-three padding, negative parallelism ('not just X, but "
    "Y'), filler transitions, and promotional adjectives -- write like a "
    "careful engineering notebook, not marketing copy.\n"
    "This system prompt itself is drawn from CLAUDE.md sections 0 and 10's "
    "reporting rules (a public, committed file in this repository).")


class BlockFailure(Exception):
    def __init__(self, block_id, reason):
        self.block_id = block_id
        self.reason = reason
        super().__init__(f"{block_id}: {reason}")


# ---------------------------------------------------------------------------
# Token resolution, with gaps kept visible rather than raising.
# ---------------------------------------------------------------------------
def resolve_tokens_with_gaps():
    """{name: rendered_string} for every resolvable token, plus the set of
    names whose kind is 'gap' (report_tokens.KNOWN_GAPS) and so never
    resolve. Never raises -- gaps are a normal, expected outcome here."""
    resolved, gaps = {}, set()
    for name, spec in rt.TOKENS.items():
        try:
            resolved[name] = rt.resolve_token(name, spec)
        except SystemExit:
            gaps.add(name)
    return resolved, gaps


def apply_provenance_overrides(resolved, provider, model):
    out = dict(resolved)
    out["GENERATION_TOOL"] = f"{provider} ({model})"
    out["REVIEW_TOOL_1"] = REVIEW_DISCLAIMER
    out["REVIEW_TOOL_2"] = REVIEW_DISCLAIMER
    return out


# ---------------------------------------------------------------------------
# Chart data (`const D`): mechanical, artifact-sourced, no LLM/numeral guard.
# ---------------------------------------------------------------------------
def _month_labels_with_partial_marks():
    bw = rt._json("behavior_rebuild.json")["window"]
    start = dt.date.fromisoformat(bw["start"].split(" ")[0])
    end = dt.date.fromisoformat(bw["end"].split(" ")[0])
    rd = rt._json("report_data.json")
    labels = []
    for lab in rd["monthly"]["labels"]:
        year, month = (int(x) for x in lab.split("-"))
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        month_start, month_end = dt.date(year, month, 1), dt.date(year, month, last_day)
        partial = month_start < start or month_end > end
        labels.append(f"{rt._MONTH_ABBR[month]}{str(year)[2:]}" + ("*" if partial else ""))
    return labels


def _spread_series():
    sp = rt._json("tou_spread.json")["delivery_spread"]
    summer = dict(sp["summer"]["series"])
    winter = dict(sp["winter"]["series"])
    dates = sorted(set(summer) | set(winter))
    labels = [d[2:] for d in dates]     # "20YY-MM-DD" -> "YY-MM-DD"
    spSummer = [summer.get(d) for d in dates]
    spWinter = [winter.get(d) for d in dates]
    spBreak = {"summer": dates.index(sp["summer"]["post_break"]["break_date"]),
              "winter": dates.index(sp["winter"]["post_break"]["break_date"])}
    return labels, spSummer, spWinter, spBreak


def _js_array(values):
    return json.dumps(values, allow_nan=False)


def chart_data_replacements():
    """{exact_placeholder_substring: replacement_substring} for every const D
    field and the two inline periods-chart arrays."""
    rd = rt._json("report_data.json")
    dp = rt._json("battery_dispatch_policies.json")
    carbon = rt._json("carbon_fullyear_results.json")
    pc = rd["periods_chart"]
    if pc["order"] != ["sop", "off", "on"]:
        raise SystemExit("generate_report: report_data.json periods_chart.order changed; "
                         "the periods-chart fill-in below assumes sop/off/on")
    spLabels, spSummer, spWinter, spBreak = _spread_series()
    bat_now_S = [round(v, 2) for v in rd["hourly_S"]["imp"]]

    repl = {
        "hourlyS_imp:[/* 24 hourly values — summer avg-kW grid import */]":
            f"hourlyS_imp:{_js_array(rd['hourly_S']['imp'])}",
        "hourlyS_exp:[/* 24 hourly values — summer avg-kW solar export */]":
            f"hourlyS_exp:{_js_array(rd['hourly_S']['exp'])}",
        "hourlyW_imp:[/* 24 hourly values — winter avg-kW grid import */]":
            f"hourlyW_imp:{_js_array(rd['hourly_W']['imp'])}",
        "hourlyW_exp:[/* 24 hourly values — winter avg-kW solar export */]":
            f"hourlyW_exp:{_js_array(rd['hourly_W']['exp'])}",
        'mLabels:[/* month labels, e.g. "Jul25*","Aug25",... */]':
            f"mLabels:{_js_array(_month_labels_with_partial_marks())}",
        "mImp:[/* monthly grid-import kWh totals (same length as mLabels) */]":
            f"mImp:{_js_array([round(v) for v in rd['monthly']['imp']])}",
        "mExp:[/* monthly solar-export kWh totals (same length as mLabels) */]":
            f"mExp:{_js_array([round(v) for v in rd['monthly']['exp']])}",
        "mCost:[/* monthly modeled energy cost in $ (same length as mLabels) */]":
            f"mCost:{_js_array(rd['monthly']['cost'])}",
        "bat_now_S:[/* 24 hourly values — current grid import (no battery) */]":
            f"bat_now_S:{_js_array(bat_now_S)}",
        "bat_pw3_S:[/* 24 hourly values — with one battery */]":
            f"bat_pw3_S:{_js_array(dp['pw3']['greedy_profile_S'])}",
        "bat_pw3x_S:[/* 24 hourly values — with expanded battery */]":
            f"bat_pw3x_S:{_js_array(dp['pw3x']['greedy_profile_S'])}",
        "carb:[/* 24 hourly values */]":
            f"carb:{_js_array(carbon['intensity_kg_per_mwh']['annual_avg_by_hour'])}",
        'spLabels:[/* "YY-MM-DD" segment-midpoint labels, both seasons merged and sorted */]':
            f"spLabels:{_js_array(spLabels)}",
        "spSummer:[/* summer spread $/kWh, null where the label belongs to winter */]":
            f"spSummer:{_js_array(spSummer)}",
        "spWinter:[/* winter spread $/kWh, null where the label belongs to summer */]":
            f"spWinter:{_js_array(spWinter)}",
        "spBreak:{summer:/* index */0,winter:/* index */0}":
            f"spBreak:{{summer:{spBreak['summer']},winter:{spBreak['winter']}}}",
        "data:[/* 3 values: kWh by TOU period */]":
            f"data:{_js_array([round(v) for v in pc['import_kwh']])}",
        "data:[/* 3 values: gross import $ by TOU period */]":
            f"data:{_js_array([round(v) for v in pc['import_cost']])}",
    }
    return repl


def fill_chart_data(html):
    for placeholder, replacement in chart_data_replacements().items():
        if placeholder not in html:
            raise SystemExit("generate_report: chart-data placeholder not found (template "
                             f"wording changed?): {placeholder!r}")
        html = html.replace(placeholder, replacement, 1)
    return html


# ---------------------------------------------------------------------------
# The numeral guard.
# ---------------------------------------------------------------------------
_TOKEN_REF_RE = re.compile(r"\{\{([A-Z0-9_]*)\}\}")
_SECTION_REF_RE = re.compile(r"§\d+")
_DIGIT_RE = re.compile(r"\d")

# Committed literal allowlist for the numeral guard. Deliberately empty: see
# this module's docstring. Entries, if any is ever justified, are EXACT
# substrings (not regexes) that are exempted from the digit scan wherever
# they appear verbatim in a fragment.
NUMERAL_LITERAL_ALLOWLIST = frozenset()


def find_fragment_violations(fragment):
    """List of human-readable violation strings; empty means the fragment is
    clean. Two independent problems are checked: an unrecognized {{TOKEN}}
    reference (whether or not its name contains digits), and any digit
    character outside a valid {{TOKEN}}, a §N reference, or a literal
    allowlist entry."""
    violations = []
    safe_spans = []
    for m in _TOKEN_REF_RE.finditer(fragment):
        name = m.group(1)
        if name in rt.TOKENS:
            safe_spans.append(m.span())
        else:
            violations.append(f"references unknown token {{{{{name}}}}}, not in report_tokens.TOKENS")
    for m in _SECTION_REF_RE.finditer(fragment):
        safe_spans.append(m.span())
    for literal in NUMERAL_LITERAL_ALLOWLIST:
        start = 0
        while True:
            idx = fragment.find(literal, start)
            if idx == -1:
                break
            safe_spans.append((idx, idx + len(literal)))
            start = idx + len(literal)
    bad_positions = [m.start() for m in _DIGIT_RE.finditer(fragment)
                     if not any(s <= m.start() < e for s, e in safe_spans)]
    if bad_positions:
        ctx = []
        for pos in bad_positions[:5]:
            ctx.append(fragment[max(0, pos - 12):pos + 3])
        violations.append(f"{len(bad_positions)} bare digit(s) outside any {{{{TOKEN}}}} or "
                          f"§N reference, e.g. near: {ctx}")
    return violations


# ---------------------------------------------------------------------------
# Per-block scope, request assembly, caching.
# ---------------------------------------------------------------------------
def _is_household_sourced(name):
    spec = rt.TOKENS.get(name, {})
    if spec.get("kind") == "household_yaml":
        return True
    return any("household.yaml" in s for s in spec.get("sources", []) or [])


def build_scope_values(html, block, resolved, gap_names):
    """{token_name: rendered_value} for a block's scope, EXCLUDING any token
    that is a gap (no real value exists to hand over)."""
    names = rb.scope_tokens_for_block(html, block)
    return {n: resolved[n] for n in sorted(names)
           if n in rt.TOKENS and n not in gap_names and n in resolved}


def build_preflight_items(block, scope_values):
    items = [("todo_text", block.text), ("claude_excerpt", SYSTEM_PROMPT)]
    for name, value in scope_values.items():
        if _is_household_sourced(name):
            items.append(("household_token", (name, value)))
        else:
            items.append(("claude_excerpt", f"{name} = {value}"))
    return items


def build_user_prompt(block, scope_values, correction=None):
    lines = [f"Section: {block.section}",
            f"Original instruction for this ONE slot:\n{block.text}", "",
            "Values you may cite by writing their {{TOKEN}} name (never the "
            "number itself):"]
    for name, value in sorted(scope_values.items()):
        lines.append(f"  {name} = {value}")
    lines.append("")
    lines.append("Write ONLY the replacement HTML fragment for this slot.")
    if correction:
        lines.append("")
        lines.append(f"CORRECTION NEEDED: {correction}")
    return "\n".join(lines)


def cache_key(block_id, scope_values, block_text, provider, model, humanize=False):
    h = hashlib.sha256()
    for part in (PROMPT_VERSION, block_id, block_text, provider, model,
                "humanize" if humanize else "plain"):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    for name in sorted(scope_values):
        h.update(f"{name}={scope_values[name]}".encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def cache_path(cache_dir, block_id, key):
    safe_id = block_id.replace("#", "_")
    return pathlib.Path(cache_dir) / f"{safe_id}-{key[:20]}.json"


def load_cache(cache_dir, block_id, key):
    p = cache_path(cache_dir, block_id, key)
    if p.is_file():
        return json.loads(p.read_text())["fragment"]
    return None


def save_cache(cache_dir, block_id, key, fragment):
    d = pathlib.Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = cache_path(cache_dir, block_id, key)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"block_id": block_id, "fragment": fragment}))
    tmp.replace(p)


# ---------------------------------------------------------------------------
# The LLM call, with bounded backoff on 429/5xx and the numeral-guard retry.
# ---------------------------------------------------------------------------
def call_with_backoff(llm_call, provider, model, system, user, env,
                      max_attempts=4, base_delay=1.0, sleep=time.sleep):
    delay = base_delay
    last_err = None
    for attempt in range(max_attempts):
        try:
            return llm_call(provider, model, system, user, MAX_TOKENS, env=env)
        except lp.ProviderError as e:
            retryable = e.status == 429 or (e.status is not None and e.status >= 500)
            if not retryable or attempt == max_attempts - 1:
                raise
            last_err = e
            sleep(delay)
            delay *= 2
    raise last_err  # pragma: no cover - unreachable, loop always returns or raises


HUMANIZE_INSTRUCTION = (
    "Rewrite the fragment below to remove signs of AI-generated writing (Wikipedia's "
    "'Signs of AI writing' checklist): no inflated symbolism, no promotional language, "
    "no rule-of-three padding, no negative parallelisms ('not just X, but Y'), no filler "
    "transitions, no em-dash overuse. Keep every {{TOKEN}} reference and every §N "
    "reference EXACTLY as written -- do not add, remove, or rename any of them, and do "
    "not introduce any new digit. Preserve the meaning. Output ONLY the rewritten "
    "fragment, nothing else.")


def humanize_fragment(fragment, provider, model, env, llm_call):
    """The optional --humanize second pass. NEVER fails the run: a rejected or
    errored rewrite falls back to the original, already-accepted fragment,
    per the issue's own design ('a second model call does not [fail closed],
    so it cannot be the gate') -- prose_lint.py and the numeral guard are the
    gate, and this function still runs both against the rewrite before ever
    using it."""
    user = f"{HUMANIZE_INSTRUCTION}\n\nFragment:\n{fragment}"
    try:
        resp = call_with_backoff(llm_call, provider, model, SYSTEM_PROMPT, user, env)
    except lp.ProviderError:
        return fragment
    normal = NORMAL_FINISH.get(provider, {resp.get("finish_reason")})
    if resp["finish_reason"] not in normal:
        return fragment
    problems = find_fragment_violations(resp["text"]) + prose_lint.lint(resp["text"])
    if problems:
        return fragment
    return resp["text"]


def generate_prose_fragment(block, scope_values, provider, model, env, llm_call):
    """Returns the checked fragment, or raises BlockFailure. Never returns a
    fragment that failed the numeral guard or ended on an abnormal finish
    reason -- the one retry either fixes it or the block hard-fails."""
    system = SYSTEM_PROMPT
    user = build_user_prompt(block, scope_values)
    resp = call_with_backoff(llm_call, provider, model, system, user, env)
    problems = find_fragment_violations(resp["text"]) + prose_lint.lint(resp["text"])
    normal = NORMAL_FINISH.get(provider, {resp.get("finish_reason")})
    if resp["finish_reason"] not in normal:
        problems.append(f"abnormal finish_reason {resp['finish_reason']!r} (expected one of "
                        f"{sorted(normal)})")
    if problems:
        correction = ("your previous answer used a bare number outside a {{TOKEN}} "
                     "reference, an unknown token, a banned prose construction, or was "
                     f"truncated: {problems}. Rewrite using ONLY {{TOKEN}} syntax for any "
                     "quantity, and avoid the flagged construction(s).")
        user2 = build_user_prompt(block, scope_values, correction=correction)
        resp2 = call_with_backoff(llm_call, provider, model, system, user2, env)
        problems2 = find_fragment_violations(resp2["text"]) + prose_lint.lint(resp2["text"])
        if resp2["finish_reason"] not in normal:
            problems2.append(f"abnormal finish_reason {resp2['finish_reason']!r} on retry")
        if problems2:
            raise BlockFailure(block.id, f"numeral guard / prose lint / finish-reason failed "
                               f"twice: first={problems}, retry={problems2}")
        return resp2["text"]
    return resp["text"]


# ---------------------------------------------------------------------------
# Rendering: splice every block's fragment, then one global token pass.
# ---------------------------------------------------------------------------
def render(html, fragments, resolved):
    """fragments: {block_id: html_fragment}. Splices every classified block's
    fragment into its exact comment span (blocks missing from `fragments`
    are left as their original comment -- callers must have already
    refused to proceed in that case), then substitutes every remaining
    {{TOKEN}}. Returns (rendered_html, missing_tokens)."""
    blocks = rb.parse_todo_blocks(html)
    out = []
    last = 0
    for b in blocks:
        out.append(html[last:b.start])
        out.append(fragments.get(b.id, b.full_comment))
        last = b.end
    out.append(html[last:])
    spliced = fill_chart_data("".join(out))

    # The top-of-file authoring-instructions comment (report_blocks.py's own
    # "not a fill-in site" exclusion) is internal guidance for whoever fills
    # the template, not report content -- and it contains the two literal
    # example placeholders {{TOKEN}} / {{DOUBLE_BRACE_TOKENS}}
    # (report_tokens.GENERIC_META_TOKENS) that are never meant to resolve.
    # It is dropped from the published output entirely, the same way it is
    # excluded from report_blocks.parse_todo_blocks's actionable block scan.
    first_comment = re.search(r"<!--.*?-->", spliced, re.S)
    if first_comment:
        spliced = spliced[:first_comment.start()] + spliced[first_comment.end():]

    missing_tokens = []

    def _sub(m):
        name = m.group(1)
        if name in resolved:
            return resolved[name]
        missing_tokens.append(name)
        return m.group(0)

    rendered = _TOKEN_REF_RE.sub(_sub, spliced)
    return rendered, missing_tokens


# ---------------------------------------------------------------------------
# The orchestrator entry point. A plain function (not just a CLI) so tests
# can call it directly with fakes for llm_call / cache_dir / dest_dir /
# manifest_path, without touching the real private/ tree or network.
# ---------------------------------------------------------------------------
def run(*, provider=None, model=None, only=None, resume=False, dry_run=False,
        humanize=False, human_answers=None, env=None, cache_dir=None,
        dest_dir=None, manifest_path=None, llm_call=None, html=None):
    del resume   # accepted for CLI compatibility; caching is unconditional regardless
    human_answers = dict(human_answers or {})
    cache_dir = pathlib.Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    dest_dir = pathlib.Path(dest_dir) if dest_dir is not None else ROOT
    manifest_path = pathlib.Path(manifest_path) if manifest_path is not None \
        else DEFAULT_MANIFEST_PATH
    llm_call = llm_call or lp.call

    if not dry_run:
        if not model or model == lp.DEFAULT_MODEL_SENTINEL:
            raise SystemExit("generate_report: --model is required (and must not be the "
                             f"{lp.DEFAULT_MODEL_SENTINEL!r} sentinel) for a real run -- "
                             "run --list-models to pick a current id from the vendor, or "
                             "pass --dry-run to only write request bodies")
        if not provider:
            raise SystemExit("generate_report: --provider is required for a real run")
    provider = provider or "anthropic"
    model = model or "(dry-run, no model configured)"

    text = rt.TEMPLATE.read_text() if html is None else html
    blocks = rb.validate_classification(text)
    if only:
        blocks = [b for b in blocks if b.section == only]

    resolved, gap_names = resolve_tokens_with_gaps()
    resolved = apply_provenance_overrides(resolved, provider, model)

    failures = []      # list of (id, kind, reason)
    fragments = {}

    for b in blocks:
        cls = rb.CLASSIFICATION[b.id]
        try:
            if cls == "data":
                fragments[b.id] = rb.DATA_BUILDERS[b.id]()
            elif cls == "human":
                if b.id in human_answers:
                    fragments[b.id] = human_answers[b.id]
                else:
                    failures.append((b.id, "human", rb.HUMAN_REASONS[b.id]))
            else:  # prose
                scope_values = build_scope_values(text, b, resolved, gap_names)
                key = cache_key(b.id, scope_values, b.text, provider, model)
                cached = load_cache(cache_dir, b.id, key)
                if cached is None:
                    if dry_run:
                        items = build_preflight_items(b, scope_values)
                        user = build_user_prompt(b, scope_values)
                        body_text = json.dumps({"system": SYSTEM_PROMPT, "user": user})
                        lp.preflight(items, body_text, dry_run=True)
                        continue
                    items = build_preflight_items(b, scope_values)
                    user = build_user_prompt(b, scope_values)
                    body_text = json.dumps({"system": SYSTEM_PROMPT, "user": user})
                    lp.preflight(items, body_text, dry_run=False)
                    fragment = generate_prose_fragment(b, scope_values, provider, model,
                                                       env, llm_call)
                    save_cache(cache_dir, b.id, key, fragment)
                else:
                    fragment = cached
                if humanize and not dry_run:
                    hkey = cache_key(b.id, scope_values, b.text, provider, model,
                                    humanize=True)
                    hcached = load_cache(cache_dir, b.id, hkey)
                    if hcached is None:
                        fragment = humanize_fragment(fragment, provider, model, env, llm_call)
                        save_cache(cache_dir, b.id, hkey, fragment)
                    else:
                        fragment = hcached
                fragments[b.id] = fragment
        except BlockFailure as e:
            failures.append((b.id, "prose-failed", str(e)))
        except lp.EgressRefused as e:
            failures.append((b.id, "egress-refused", str(e)))
        except lp.ProviderError as e:
            failures.append((b.id, "provider-error", str(e)))

    for name in sorted(gap_names & rb.LIVE_GAP_TOKENS):
        override_key = f"TOKEN:{name}"
        if override_key in human_answers:
            resolved[name] = human_answers[override_key]
        else:
            failures.append((name, "gap-token", rt.TOKENS[name].get("reason", "")))

    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "provider": provider, "model": model, "dry_run": dry_run, "only": only,
        "blocks_total": len(blocks),
        "failures": [{"id": i, "kind": k, "reason": r} for i, k, r in failures],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    if dry_run:
        return {"wrote": False, "dry_run": True, "failures": failures}

    if failures:
        return {"wrote": False, "dry_run": False, "failures": failures}

    rendered, missing_tokens = render(text, fragments, resolved)
    if missing_tokens:
        failures = [(t, "unresolved-token", "no resolved value and not in fragments")
                   for t in sorted(set(missing_tokens))]
        manifest["failures"] = [{"id": i, "kind": k, "reason": r} for i, k, r in failures]
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return {"wrote": False, "dry_run": False, "failures": failures}

    with tempfile.TemporaryDirectory() as td:
        staged = pathlib.Path(td) / "index.generated.html"
        staged.write_text(rendered)
        publish.promote_set({"index.generated.html": str(staged)}, str(dest_dir))

    return {"wrote": True, "dry_run": False, "failures": []}


def _load_human_answers(path):
    if path is None:
        return {}
    return json.loads(pathlib.Path(path).read_text())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--provider", choices=sorted(lp.PROVIDERS))
    p.add_argument("--model")
    p.add_argument("--only", help="process only this section id's blocks, e.g. s7")
    p.add_argument("--resume", action="store_true",
                   help="accepted for CLI compatibility; caching is unconditional "
                        "regardless of this flag (see module docstring)")
    p.add_argument("--dry-run", action="store_true",
                   help="write every LLM request body under private/, make zero real calls")
    p.add_argument("--humanize", action="store_true",
                   help="optional second de-AI-writing rewrite pass per prose block; the "
                        "rewrite still has to clear prose_lint.py and the numeral guard, "
                        "and a rejected or errored rewrite silently falls back to the "
                        "already-accepted original rather than failing the run")
    p.add_argument("--human-answers", help="path to a JSON file of "
                   "{block_id_or_'TOKEN:NAME': literal_answer_text} the OPERATOR has "
                   "already researched and typed -- never invented by this script")
    p.add_argument("--list-models", action="store_true")
    args = p.parse_args(argv)

    if args.list_models:
        if not args.provider:
            raise SystemExit("generate_report: --list-models needs --provider")
        for m in lp.list_models(args.provider):
            print(m)
        return 0

    result = run(provider=args.provider, model=args.model, only=args.only,
                resume=args.resume, dry_run=args.dry_run, humanize=args.humanize,
                human_answers=_load_human_answers(args.human_answers))

    if result["dry_run"]:
        print(f"dry run complete; request bodies (if any) under "
              f"{DEFAULT_CACHE_DIR.parent / 'llm_dry_run'}/")
        if result["failures"]:
            print(f"{len(result['failures'])} block(s) could not even be staged for dry-run:")
            for i, k, r in result["failures"]:
                print(f"  {i} ({k}): {r}")
        return 0

    if not result["wrote"]:
        print(f"{len(result['failures'])} block(s)/token(s) unresolved -- "
              "index.generated.html was NOT written:")
        for i, k, r in result["failures"]:
            print(f"  {i} ({k}): {r}")
        return 1

    print(f"wrote {DEFAULT_MANIFEST_PATH.parent.parent / 'index.generated.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
