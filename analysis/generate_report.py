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
         scope, less gaps and less any token whose value is markup rather
         than language -- see build_scope_values -- and never the
         surrounding HTML), run it through
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
from html import escape as _html_escape  # `html` is already this module's own
                                          # convention for "the document text"
                                          # (render(html, ...), fill_chart_data(html),
                                          # run(..., html=None)) -- imported this way
                                          # to avoid shadowing every one of those.
from html.parser import HTMLParser as _HTMLParser

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
    "surrounding HTML, no other slot's content. The ONLY HTML tags you may "
    "use are <b> <i> <em> <strong> <code> <sup> <sub>, and <a href=\"#sN\"> "
    "for an internal section link -- no other tag, no attribute other than "
    "that one href, no style attribute, no event-handler attribute. Every "
    "tag you open must be closed.\n"
    "2. Never write a bare digit, and never spell a quantity out in words "
    "either (no 'roughly two-thirds', no 'about forty percent', no 'the "
    "majority', no 'several'). Every quantity must be written as "
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
# The three tokens apply_provenance_overrides ALWAYS replaces. Named once so the
# override and the egress label below cannot drift apart.
PROVENANCE_OVERRIDDEN = ("GENERATION_TOOL", "REVIEW_TOOL_1", "REVIEW_TOOL_2")


def resolve_tokens_with_gaps():
    """{name: rendered_string} for every resolvable token; the set of names
    whose kind is LITERALLY 'gap' (report_tokens.KNOWN_GAPS) and so never
    resolve BY DESIGN; and resolve_failures, a list of (name, message) for
    any OTHER token whose resolution raised SystemExit for a REAL reason --
    a missing or malformed data/*.json or *.csv, a broken household.yaml
    path, a future report_tokens.py regression. Only kind == "gap" gets
    silently folded into `gaps` -- adversarial review found an earlier
    version of this function caught every SystemExit and treated it as an
    expected gap regardless of the token's actual kind, which would let a
    real artifact failure disappear silently instead of blocking the run.
    Never raises itself: the caller (run()) folds resolve_failures into its
    own failures list, so a broken token is reported and blocks the write
    exactly like a failed block, with the run never claiming success while
    quietly missing required data."""
    resolved, gaps, resolve_failures = {}, set(), []
    for name, spec in rt.TOKENS.items():
        # The provenance three are NOT resolved here, because this module
        # replaces all three a moment later and their household values are
        # irrelevant to what it publishes. Resolving them anyway made the
        # DOCUMENTED no-review configuration abort the run: household.example
        # .yaml leaves both review fields null on purpose, report_tokens.py
        # refuses to render a name that would claim a review happened, and
        # those refusals landed in resolve_failures -- which run() folds into
        # its own failures, so the report never wrote. The overrides that make
        # the question moot ran afterwards and could not un-record them.
        # Skipping is the fix rather than swallowing the error: an override is
        # coming, so there is no question to answer here (issue #135, found by
        # adversarial review).
        if name in PROVENANCE_OVERRIDDEN:
            continue
        try:
            resolved[name] = rt.resolve_token(name, spec)
        except SystemExit as e:
            if spec.get("kind") == "gap":
                gaps.add(name)
            else:
                resolve_failures.append((name, str(e)))
    return resolved, gaps, resolve_failures


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

# ---------------------------------------------------------------------------
# Word-number / fraction / vague-quantifier detection (adversarial review
# finding 2): a decimal-digit scan alone misses an invented quantity spelled
# out in words ("roughly two-thirds of imports", "about forty percent") --
# an LLM told "never write a bare digit" can trivially comply while still
# stating an unbacked number. These patterns are additional, INDEPENDENT
# violations (not a replacement for the digit scan above); {{TOKEN}}/§N spans
# do not exempt them, since a token's rendered STRING is never itself matched
# against these word patterns (find_fragment_violations only scans the raw
# fragment text a model wrote, not resolved token values).
#
# Design choices, spelled out because both are judgment calls:
#   - "one" is DELIBERATELY EXCLUDED from the cardinal-word list. It is
#     overwhelmingly an indefinite pronoun/article in this report's own
#     reference voice ("one option", "no one", "someone", "one of the three
#     packages") rather than a quantity, and including it produced far more
#     false positives than caught fabrications. This is an accepted residual
#     gap: "saves one hundred dollars" is still caught (via "hundred"), a
#     bare "saves one dollar" would not be. "two" and up stay in the list --
#     they are overwhelmingly real quantities when they appear in this
#     report's prose ("two EVs" is exactly the kind of invented countable
#     fact OTHER_MAJOR_LOADS should back instead).
#   - Fraction words that double as ordinals ("third", "quarter", "fourth", ...)
#     are flagged only in a QUANTITY-shaped context (preceded by an article/
#     number word, or followed by "of"/"the") -- "the third package option"
#     (an ordinal referring to LOW/MID/HIGH) is not flagged; "roughly a third
#     of imports" and "two-thirds of production" are. "half" has no common
#     non-quantity reading in this report's prose, so it is flagged bare.
#   - Vague quantifiers ("roughly", "majority", "several", "a few", "most",
#     "many", "much", "handful", "fraction", "bulk", "countless", ...) are
#     treated as HARD violations, not soft warnings, matching this repo's
#     consistent fail-closed bias elsewhere (KNOWN_GAPS, human-block
#     blocking, egress preflight's fail-safe-on-no-gitleaks) -- "most of the
#     on-peak imports" is exactly the kind of claim ONPEAK_IMPORT_SHARE_PCT
#     should express as a token instead. This trades a higher false-positive/
#     retry rate for never silently publishing an unbacked quantity claim.
#     "most"/"many" match STANDALONE, not gated on a following "of" -- a
#     second adversarial review pass found that dropping "of" ("most on-peak
#     imports happen in the evening") defeated the original of-gated pattern
#     entirely, and that construction is at least as natural as the gated
#     one, not just an adversarial trick.
#   - Multiplier words ("doubles", "triples", "twice") are their own class:
#     "the battery doubles the savings" and "twice as much power is
#     exported" state an invented multiplicative fact exactly like a
#     spelled-out number would.
_CARDINAL_WORDS = ("two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                   "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
                   "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
                   "hundred|thousand|million|billion")
_FRACTION_ORDINAL_WORDS = "third|quarter|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
_VAGUE_QUANTIFIER_WORDS = ("roughly|approximately|nearly|majority|several|dozens|numerous|"
                          "many|much|handful|fraction|bulk|countless")
_MULTIPLIER_WORDS = "quadruple(?:s|d)?|triple(?:s|d)?|double(?:s|d)?|twice"
# "most" is graded ("most" is the superlative of "many"/adjectives), so it has
# a genuine second reading this list's other words don't: "the most efficient
# dispatch policy" / "the most affordable package" state an ordinary
# superlative comparison, not an invented quantity -- a third adversarial
# review pass found "most"'s standalone match false-positiving on exactly
# this construction. "the most ADJECTIVE" is that comparison; bare "most"
# with no preceding "the" (or "most" followed directly by "of") is
# overwhelmingly the "majority of" quantifier sense this guard exists to
# catch ("most on-peak imports happen...", "most of the savings"). Excluding
# "most" only when immediately preceded by "the" keeps both real cases
# working: "the most efficient..." is exempt, "most imports..." still flags.
_MOST_QUANTIFIER_RE = re.compile(r"(?<!\bthe )\bmost\b", re.I)

_WORD_NUMBER_PATTERNS = [
    (re.compile(r"\b(" + _CARDINAL_WORDS + r")\b", re.I), "spelled-out cardinal number"),
    (re.compile(r"\bhalf\b", re.I), "spelled-out fraction ('half')"),
    (re.compile(r"\b(a|an|one|" + _CARDINAL_WORDS + r")[\s-]+(" + _FRACTION_ORDINAL_WORDS
               + r")s?\b", re.I), "spelled-out fraction"),
    (re.compile(r"\b(" + _FRACTION_ORDINAL_WORDS + r")s?\s+(of|the)\b", re.I),
     "spelled-out fraction"),
    (re.compile(r"\b(" + _VAGUE_QUANTIFIER_WORDS + r")\b", re.I), "vague quantifier"),
    (_MOST_QUANTIFIER_RE, "vague quantifier ('most', not the superlative 'the most ADJ')"),
    (re.compile(r"\ba\s+few\b", re.I), "vague quantifier ('a few')"),
    (re.compile(r"\b(" + _MULTIPLIER_WORDS + r")\b", re.I), "multiplier word"),
]


def _word_number_violations(fragment):
    violations = []
    for pattern, label in _WORD_NUMBER_PATTERNS:
        m = pattern.search(fragment)
        if m:
            violations.append(f"{label}: {m.group(0)!r}")
    return violations


# ---------------------------------------------------------------------------
# HTML-structure validation (Codex review, finding 2): the numeral guard and
# prose_lint.py check invented QUANTITIES and WRITING STYLE only -- neither
# looks at HTML structure at all, so "<script>alert(1)</script>" or a tag
# with an onerror= attribute has zero digits and no banned phrase, passes
# both checks cleanly, and would be spliced straight into a page meant to be
# published on GitHub Pages. This is the mechanical gate for that: stdlib
# html.parser.HTMLParser only (this repo's zero-new-dependencies rule
# applies here too -- no bleach, no new package), a small allowlist of the
# INERT INLINE tags this report's own prose actually uses (checked directly
# against index.html/report-template.html's real usage: <b>, <i>, <em>,
# <code> appear throughout prose; <strong>/<sup>/<sub> do not appear today
# but are the same trust class and cost nothing to allow; <a> is used only
# for internal #sN section links in prose, never an external URL or a
# javascript:/data: scheme), a narrow per-tag attribute allowlist (nothing
# but href, and only on <a>), and a same-document-anchor-only rule for that
# href. Anything else -- <script>, <style>, <iframe>, <object>, <embed>,
# <form>, <img>, <svg>, <div>, <span>, any on* event-handler attribute on
# ANY tag, any style attribute, any non-#sN href, or an unbalanced/
# improperly-nested tag -- is rejected.
# ---------------------------------------------------------------------------
ALLOWED_INLINE_TAGS = {
    "b": frozenset(), "i": frozenset(), "em": frozenset(), "strong": frozenset(),
    "code": frozenset(), "sup": frozenset(), "sub": frozenset(),
    "a": frozenset({"href"}),
}
_INTERNAL_ANCHOR_HREF_RE = re.compile(r"^#s\d+$")
# Same shape, NOT anchored -- used by find_fragment_violations to exempt an
# internal anchor's digit(s) from the numeral guard wherever the substring
# appears (e.g. inside href="#s3"). Digits INSIDE the section id are not an
# invented quantity, and find_html_structure_violations already constrains
# where the anchored, whole-value form may legally appear at all.
_INTERNAL_ANCHOR_HREF_RE_LOOSE = re.compile(r"#s\d+")
_URL_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.\-]*):")


class _StructureViolation(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class _FragmentHTMLValidator(_HTMLParser):
    """Raises _StructureViolation from within a handler on the FIRST problem
    found -- this is a validator, not an enumerator of every issue, so it
    short-circuits like a parser error would. `stack` tracks open tags so
    find_html_structure_violations can additionally check it is EMPTY once
    parsing finishes (an unclosed <b> at the end of a fragment raises
    nothing DURING parsing -- HTMLParser is lenient about missing close
    tags -- so the emptiness check after the fact is what actually catches
    it)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def _check_tag(self, tag, attrs):
        if tag not in ALLOWED_INLINE_TAGS:
            raise _StructureViolation(f"disallowed HTML tag <{tag}>")
        allowed_attrs = ALLOWED_INLINE_TAGS[tag]
        for name, value in attrs:
            lname = (name or "").lower()
            if lname.startswith("on"):
                raise _StructureViolation(
                    f"<{tag}> carries an event-handler attribute {name!r}")
            if lname == "style":
                raise _StructureViolation(f"<{tag}> carries a style attribute")
            if lname not in allowed_attrs:
                raise _StructureViolation(f"<{tag}> carries a disallowed attribute {name!r}")
            if lname == "href":
                v = value or ""
                if not _INTERNAL_ANCHOR_HREF_RE.match(v):
                    raise _StructureViolation(
                        f"<a href={value!r}> is not a bare internal section anchor "
                        "(must match ^#s\\d+$)")
                if _URL_SCHEME_RE.match(v):
                    raise _StructureViolation(
                        f"<a href={value!r}> carries a URL scheme, not a bare anchor")

    def handle_starttag(self, tag, attrs):
        self._check_tag(tag, attrs)
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self._check_tag(tag, attrs)
        # self-closed (e.g. <a href="#s3"/>) -- nothing left to balance later.

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            raise _StructureViolation(
                f"unbalanced or improperly nested </{tag}> (open tag stack: {self.stack})")
        self.stack.pop()


def find_html_structure_violations(fragment):
    """List of human-readable violation strings; empty means the fragment's
    HTML structure is clean. See the module-level comment above
    ALLOWED_INLINE_TAGS for exactly what is and is not allowed."""
    validator = _FragmentHTMLValidator()
    try:
        validator.feed(fragment)
        validator.close()
    except _StructureViolation as e:
        return [f"HTML structure violation: {e.message}"]
    if validator.stack:
        return [f"HTML structure violation: unclosed tag(s) at end of fragment: "
               f"{validator.stack}"]
    return []


def find_fragment_violations(fragment, allowed_tokens=None):
    """List of human-readable violation strings; empty means the fragment is
    clean. Checks, all independent: an unrecognized {{TOKEN}} reference
    (whether or not its name contains digits); a reference to a token that
    IS real but outside `allowed_tokens` (see below); any digit character
    outside a valid {{TOKEN}}, a §N reference, or a literal allowlist entry;
    any spelled-out number, fraction, or vague quantifier word (see
    _WORD_NUMBER_PATTERNS above) -- a decimal-digit scan alone cannot catch
    "roughly two-thirds" or "about forty percent"; and any HTML-structure
    violation (see find_html_structure_violations) -- a disallowed tag, a
    disallowed attribute, an event-handler or style attribute, a non-#sN
    href, or an unbalanced/improperly-nested tag.

    allowed_tokens: an optional iterable of token names the ORIGINATING
    BLOCK was actually shown (its own report_blocks.scope_tokens_for_block
    scope) -- when given, a {{TOKEN}} reference to a real report_tokens.
    TOKENS name that is NOT in this set is rejected exactly like a reference
    to a nonexistent token. This is what keeps the per-block sandbox
    (adversarial review: "the model is handed one TODO block's own scope and
    nothing else") true of the OUTPUT, not just the prompt -- without it, a
    fragment could cite any of the 143 tokens in the whole system, not just
    the ones its own block was given, and it would still resolve in the
    final render. None (the default) accepts any real token -- used by
    generic/standalone callers (tests, prose_lint-adjacent tooling) that
    aren't validating one specific block's own scoped output."""
    violations = []
    safe_spans = []
    allowed = set(allowed_tokens) if allowed_tokens is not None else None
    for m in _TOKEN_REF_RE.finditer(fragment):
        name = m.group(1)
        if name not in rt.TOKENS:
            violations.append(f"references unknown token {{{{{name}}}}}, not in report_tokens.TOKENS")
        elif allowed is not None and name not in allowed:
            violations.append(f"references token {{{{{name}}}}}, a real token but outside "
                              "this block's own scope")
        else:
            safe_spans.append(m.span())
    for m in _SECTION_REF_RE.finditer(fragment):
        safe_spans.append(m.span())
    for m in _INTERNAL_ANCHOR_HREF_RE_LOOSE.finditer(fragment):
        # the digit(s) inside an internal section anchor like #s3 are not an
        # invented quantity -- they're a section id, and find_html_structure_
        # violations() already independently constrains WHERE this exact
        # substring may legally appear (only inside <a href="#sN">, nothing
        # else) via _INTERNAL_ANCHOR_HREF_RE's anchored, whole-value match.
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
    violations.extend(_word_number_violations(fragment))
    violations.extend(find_html_structure_violations(fragment))
    return violations


# ---------------------------------------------------------------------------
# Per-block scope, request assembly, caching.
# ---------------------------------------------------------------------------
def _is_household_sourced(name):
    """Which egress label build_preflight_items puts on one scoped value.
    Only ever asked about a name build_scope_values already kept, so a token
    excluded there (a gap, or an attribute-only one) is never labelled at
    all rather than being labelled and then withheld -- the question "is this
    household-sourced" stays a fact about the token, and whether it travels
    stays one decision, made upstream."""
    # The provenance three are household-sourced in report_tokens.py (issue
    # #135 moved them out of hard-coded constants), but this module overrides
    # all three before anything leaves it: GENERATION_TOOL becomes the run's
    # ACTUAL provider/model and the review names become a non-claiming
    # disclaimer. Labelling them household_token would send the egress guard to
    # re-resolve them against household.yaml and refuse the override as a
    # tampered value -- checking the wrong claim. Their in-run value is this
    # run's own fact, not a household one, so they travel as caller-asserted
    # literal text, which is exactly what they are.
    if name in PROVENANCE_OVERRIDDEN:
        return False
    spec = rt.TOKENS.get(name, {})
    if spec.get("kind") == "household_yaml":
        return True
    return any("household.yaml" in s for s in spec.get("sources", []) or [])


def build_scope_values(html, block, resolved, gap_names):
    """{token_name: rendered_value} for a block's scope, EXCLUDING two kinds
    of token: a gap (no real value exists to hand over), and one declared
    report_tokens.is_attribute_only (a value that is markup rather than
    language -- a CSS class, an id -- which report_blocks.scope_tokens_for_block
    picks up like any other token live in the section, because it reads the
    section's token inventory and nothing about where in the markup each one
    lands).

    THIS IS THE ONE CHOKE POINT, which is why the exclusion is here and not
    in scope_tokens_for_block. Everything downstream that decides what an
    LLM sees, keeps, or may cite is fed from this one dict: build_user_prompt
    lists it under "Values you may cite", build_preflight_items labels each
    entry for the egress gate, cache_key hashes it, and both
    find_fragment_violations calls take `scope_values.keys()` as the set of
    token names a returned fragment is allowed to name. Drop a token here
    and it is out of all five at once; drop it in report_blocks and every
    other reader of that function inherits a decision about prose it has no
    stake in.

    See report_tokens.is_attribute_only for what the flag promises and the
    test that holds it to the template."""
    names = rb.scope_tokens_for_block(html, block)
    return {n: resolved[n] for n in sorted(names)
           if n in rt.TOKENS and n not in gap_names and n in resolved
           and not rt.is_attribute_only(n)}


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


def load_and_validate_cache(cache_dir, block_id, key, allowed_tokens):
    """load_cache(), but REVALIDATED against today's rules before being
    trusted (Codex review, finding 2): a cache entry written before the
    HTML-structure validator, the word-number patterns, or the per-block
    scope restriction existed -- or one corrupted on disk -- must never be
    spliced into the document just because its hash key still happens to
    match. Runs the exact same checks a fresh fragment has to pass
    (find_fragment_violations against THIS block's own current scope, plus
    prose_lint.lint); a fragment that fails either is treated as a cache
    MISS (returns None) rather than a crash -- the caller regenerates it
    fresh, exactly like a cold cache, so a stale entry costs one extra call
    rather than corrupting the output or aborting the run."""
    fragment = load_cache(cache_dir, block_id, key)
    if fragment is None:
        return None
    problems = (find_fragment_violations(fragment, allowed_tokens=allowed_tokens)
               + prose_lint.lint(fragment))
    if problems:
        return None
    return fragment


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


def preflighted_call(llm_call, provider, model, system, user, env, items):
    """THE single chokepoint for every real call that sends a REQUEST BODY --
    i.e. every prose-generation attempt (first try and corrective retry) and
    every --humanize rewrite: always runs llm_providers.preflight() against
    the EXACT body about to be sent, then (only if that succeeds) makes the
    call via call_with_backoff(). No other function building a request body
    may call llm_call or call_with_backoff -- test_generate_report.py's AST
    guard (case_preflighted_call_is_the_only_real_call_site) enforces this
    the same way llm_providers.py's own _post_json chokepoint is enforced,
    after an adversarial review found the corrective-retry and --humanize
    call sites bypassing preflight() entirely.

    NOT covered, intentionally: --list-models (main()'s own branch calls
    llm_providers.list_models() directly). That is a bodyless GET against a
    vendor's model-list endpoint -- there is no request body for preflight()
    to scan or allowlist-check, so routing it through this chokepoint would
    be a no-op wrapper, not a safety property. This function's guarantee is
    "every real call that sends a body is preflighted," not "every network
    call this module ever makes.\""""
    body_text = json.dumps({"system": system, "user": user})
    lp.preflight(items, body_text, dry_run=False)
    return call_with_backoff(llm_call, provider, model, system, user, env)


HUMANIZE_INSTRUCTION = (
    "Rewrite the fragment below to remove signs of AI-generated writing (Wikipedia's "
    "'Signs of AI writing' checklist): no inflated symbolism, no promotional language, "
    "no rule-of-three padding, no negative parallelisms ('not just X, but Y'), no filler "
    "transitions, no em-dash overuse. Keep every {{TOKEN}} reference and every §N "
    "reference EXACTLY as written -- do not add, remove, or rename any of them, and do "
    "not introduce any new digit. Preserve the meaning. Output ONLY the rewritten "
    "fragment, nothing else.")


def humanize_fragment(block, scope_values, fragment, provider, model, env, llm_call):
    """The optional --humanize second pass. NEVER fails the run: a rejected or
    errored rewrite falls back to the original, already-accepted fragment,
    per the issue's own design ('a second model call does not [fail closed],
    so it cannot be the gate') -- prose_lint.py and the numeral guard are the
    gate, and this function still runs both against the rewrite before ever
    using it. Egress: this is a REAL call, so it goes through
    preflighted_call() like any other -- the instruction text is a fixed
    claude_excerpt, the fragment being rewritten (this run's own already-
    accepted, already-scanned output) is sent as todo_text, and the same
    scope_values that justified the fragment travel with it so preflight can
    re-verify any household_token still present."""
    user = f"{HUMANIZE_INSTRUCTION}\n\nFragment:\n{fragment}"
    items = build_preflight_items(block, scope_values) + [
        ("claude_excerpt", HUMANIZE_INSTRUCTION), ("todo_text", fragment)]
    try:
        resp = preflighted_call(llm_call, provider, model, SYSTEM_PROMPT, user, env, items)
    except (lp.ProviderError, lp.EgressRefused):
        return fragment
    normal = NORMAL_FINISH.get(provider, {resp.get("finish_reason")})
    if resp["finish_reason"] not in normal:
        return fragment
    problems = (find_fragment_violations(resp["text"], allowed_tokens=scope_values.keys())
               + prose_lint.lint(resp["text"]))
    if problems:
        return fragment
    return resp["text"]


def generate_prose_fragment(block, scope_values, provider, model, env, llm_call):
    """Returns the checked fragment, or raises BlockFailure. Never returns a
    fragment that failed the numeral guard or ended on an abnormal finish
    reason -- the one retry either fixes it or the block hard-fails. BOTH
    attempts are real calls and go through preflighted_call(); the retry's
    correction text (which can embed excerpts of the model's own rejected
    prior output, via `problems`) is sent as its own todo_text item so it is
    gitleaks-scanned and allowlist-checked exactly like the first attempt's
    body, never assumed safe because it is "just a correction"."""
    system = SYSTEM_PROMPT
    user = build_user_prompt(block, scope_values)
    items = build_preflight_items(block, scope_values)
    resp = preflighted_call(llm_call, provider, model, system, user, env, items)
    problems = (find_fragment_violations(resp["text"], allowed_tokens=scope_values.keys())
               + prose_lint.lint(resp["text"]))
    normal = NORMAL_FINISH.get(provider, {resp.get("finish_reason")})
    if resp["finish_reason"] not in normal:
        problems.append(f"abnormal finish_reason {resp['finish_reason']!r} (expected one of "
                        f"{sorted(normal)})")
    if problems:
        correction = ("your previous answer used a bare number or a spelled-out number/"
                     "fraction/vague quantifier outside a {{TOKEN}} reference, an unknown "
                     "or out-of-scope token, disallowed HTML markup, a banned prose "
                     f"construction, or was truncated: {problems}. Rewrite using ONLY "
                     "{{TOKEN}} syntax for any quantity, only the allowed HTML tags, and "
                     "avoid the flagged construction(s).")
        user2 = build_user_prompt(block, scope_values, correction=correction)
        items2 = build_preflight_items(block, scope_values) + [("todo_text", correction)]
        resp2 = preflighted_call(llm_call, provider, model, system, user2, env, items2)
        problems2 = (find_fragment_violations(resp2["text"], allowed_tokens=scope_values.keys())
                    + prose_lint.lint(resp2["text"]))
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
# ---------------------------------------------------------------------------
# The provenance sentence (Codex review pass 3, finding 1). report-template.
# html:641 is FIXED prose, shared verbatim with this repo's own hand-
# authored index.html, and it must never be edited by this PR (verified
# byte-identical throughout). That sentence's surrounding GRAMMAR --
# "were then independently reviewed... and adversarially reviewed...", "was
# subsequently re-worked... to incorporate the findings of both reviews" --
# independently asserts a review-and-rework process happened, regardless of
# what REVIEW_TOOL_1/REVIEW_TOOL_2 resolve to. apply_provenance_overrides()
# swapping those two tokens for REVIEW_DISCLAIMER neutralizes the TOOL NAMES
# but not the fixed clauses around them -- a generated report's rendered
# sentence ended up simultaneously naming a disclaimer and asserting (via
# that fixed grammar) that a review took place, which for a fork's own run
# it did not. The only fix that doesn't touch the template is to replace the
# WHOLE fixed sentence, verbatim-matched first so a future template edit
# fails closed here instead of silently leaving a false claim in place --
# the same literal-substring-find-and-replace technique fill_chart_data()
# already uses for the const D placeholders, applied here instead of via
# {{TOKEN}} substitution because there is no honest way to fill
# REVIEW_TOOL_1/REVIEW_TOOL_2 that keeps the surrounding clauses true.
# ---------------------------------------------------------------------------
PROVENANCE_SENTENCE_LITERAL = (
    "<b>How this report was produced:</b> generated with <b>{{GENERATION_TOOL}}</b>; the "
    "data, methodology, and conclusions were then independently reviewed with "
    "<b>{{REVIEW_TOOL_1}}</b> and adversarially reviewed with <b>{{REVIEW_TOOL_2}}</b>; the "
    "analysis was subsequently re-worked in {{GENERATION_TOOL}} to incorporate the findings "
    "of both reviews.")
PROVENANCE_SENTENCE_REPLACEMENT = (
    "<b>How this report was produced:</b> generated with <b>{{GENERATION_TOOL}}</b>, filling "
    "this template directly from the committed data artifacts via "
    "<code>analysis/generate_report.py</code>; no independent or adversarial review of this "
    "specific run has been performed.")


def rewrite_provenance_sentence(html):
    """Replace report-template.html's fixed CLAUDE.md section 11 provenance
    sentence with an honest one for a generated report -- see the module
    comment above PROVENANCE_SENTENCE_LITERAL. Fails closed (SystemExit,
    naming the problem) if the literal sentence is not found verbatim,
    rather than silently leaving the false review claim in place or
    guessing at a replacement in the wrong spot."""
    if PROVENANCE_SENTENCE_LITERAL not in html:
        raise SystemExit(
            "generate_report: the CLAUDE.md section 11 provenance sentence was not found "
            "verbatim in the template (report-template.html's wording changed?) -- refusing "
            "to guess at a replacement that might leave a false review claim in the output")
    return html.replace(PROVENANCE_SENTENCE_LITERAL, PROVENANCE_SENTENCE_REPLACEMENT, 1)


def render(html, fragments, resolved):
    """fragments: {block_id: html_fragment}. Splices every classified block's
    fragment into its exact comment span (blocks missing from `fragments`
    are left as their original comment -- callers must have already
    refused to proceed in that case), rewrites the fixed provenance sentence
    to an honest one (rewrite_provenance_sentence), then substitutes every
    remaining {{TOKEN}}. Returns (rendered_html, missing_tokens)."""
    blocks = rb.parse_todo_blocks(html)
    out = []
    last = 0
    for b in blocks:
        out.append(html[last:b.start])
        out.append(fragments.get(b.id, b.full_comment))
        last = b.end
    out.append(html[last:])
    spliced = fill_chart_data("".join(out))
    spliced = rewrite_provenance_sentence(spliced)

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
            # HTML-escaped (adversarial review finding 4): a resolved token
            # value is substituted directly into the document with no prior
            # sanitization otherwise, so a value containing <, >, &, ', or "
            # would corrupt the markup of a page meant to be published on
            # GitHub Pages. Verified no currently-declared report_tokens.TOKENS
            # value intentionally carries raw HTML (none does today -- they
            # are all short formatted numbers/dates/names). A handful of
            # {{TOKEN}} references sit inside single-quoted JS STRING
            # literals rather than HTML text (the five chart-title tokens,
            # the periods-chart labels, a couple of dataset labels) -- for
            # those, escaping is still the safer default: an unescaped quote
            # or ampersand in a future token value would corrupt the JS
            # syntax outright (prematurely closing the string), whereas an
            # escaped one only shows as a literal entity, which is a display
            # quirk, not a syntax break. `fill_chart_data()` runs BEFORE this
            # substitution and writes its own `const D` array values via
            # `json.dumps` from raw artifact data (never through `resolved`
            # or this `_sub` pass), so the two never double-process the same
            # value -- proven by case_generated_chart_arrays_match_their_
            # committed_artifacts in test_generate_report.py, which is
            # unaffected by this change.
            return _html_escape(resolved[name], quote=True)
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
        if only:
            # Codex review (also filed as issue #66): a --only run leaves
            # every OTHER block's raw <!-- TODO --> comment untouched in the
            # template, and render() splices fragments.get(b.id, b.full_
            # comment) for every block in the FULL document -- an out-of-
            # scope block's literal comment lands in the output. Today that
            # is always caught anyway, but only because two KNOWN_GAPS
            # tokens happen to sit in two different sections and no single
            # --only value spans both, which is an accident of the current
            # template, not a designed guarantee. --only is for previewing
            # or debugging ONE section's prompts and cache behavior; it must
            # never be combined with a real (non-dry-run) write, so this
            # fails closed rather than depending on that accident holding.
            raise SystemExit(
                f"generate_report: --only {only!r} may only be combined with --dry-run -- "
                "a section-scoped run leaves every OTHER section's TODO blocks unfilled, "
                "and a real run must never publish a file with unrendered TODO comments "
                "left in it (see issue #66). Drop --only for a real run, or add --dry-run "
                "to preview just this section's request bodies.")
    provider = provider or "anthropic"
    model = model or "(dry-run, no model configured)"

    text = rt.TEMPLATE.read_text() if html is None else html
    blocks = rb.validate_classification(text)
    if only:
        blocks = [b for b in blocks if b.section == only]

    resolved, gap_names, resolve_failures = resolve_tokens_with_gaps()
    resolved = apply_provenance_overrides(resolved, provider, model)

    failures = []      # list of (id, kind, reason)
    fragments = {}

    # A token that failed to resolve for a REAL reason (not a documented
    # KNOWN_GAPS entry) blocks the run exactly like a failed block -- it is
    # reported by name up front, before any LLM call is made, since it may
    # be the root cause of several blocks' content being wrong or missing.
    for name, message in resolve_failures:
        failures.append((name, "token-resolution-failed", message))

    for b in blocks:
        cls = rb.CLASSIFICATION[b.id]
        try:
            if cls == "data":
                fragments[b.id] = rb.DATA_BUILDERS[b.id]()
            elif cls == "human":
                if b.id in human_answers:
                    answer = human_answers[b.id]
                    # Codex review (pass 2), finding 2: a human-supplied
                    # answer is expected and allowed to state real,
                    # researched figures (unlike LLM prose, which is
                    # distrusted specifically for INVENTING numbers) -- so it
                    # is never run through the numeral guard or the
                    # word-number guard. But it is still spliced verbatim
                    # into a document meant to be published, so it MUST
                    # still pass the same mechanical HTML-structure gate an
                    # LLM fragment does: a typo'd, copy-pasted, or genuinely
                    # untrusted --human-answers file could otherwise inject
                    # <script>, an event-handler attribute, an external
                    # link, or unbalanced markup with nothing to catch it.
                    structure_problems = find_html_structure_violations(answer)
                    if structure_problems:
                        failures.append((b.id, "human-answer-invalid-html",
                                        "; ".join(structure_problems)))
                    else:
                        fragments[b.id] = answer
                else:
                    failures.append((b.id, "human", rb.HUMAN_REASONS[b.id]))
            else:  # prose
                scope_values = build_scope_values(text, b, resolved, gap_names)
                key = cache_key(b.id, scope_values, b.text, provider, model)
                cached = load_and_validate_cache(cache_dir, b.id, key, scope_values.keys())
                if cached is None:
                    if dry_run:
                        # dry-run previews the FIRST attempt's body only (there is
                        # no retry to simulate without a real response to react
                        # to); still goes through the same preflight() gate.
                        items = build_preflight_items(b, scope_values)
                        user = build_user_prompt(b, scope_values)
                        body_text = json.dumps({"system": SYSTEM_PROMPT, "user": user})
                        lp.preflight(items, body_text, dry_run=True)
                        continue
                    fragment = generate_prose_fragment(b, scope_values, provider, model,
                                                       env, llm_call)
                    save_cache(cache_dir, b.id, key, fragment)
                else:
                    fragment = cached
                if humanize and not dry_run:
                    hkey = cache_key(b.id, scope_values, b.text, provider, model,
                                    humanize=True)
                    hcached = load_and_validate_cache(cache_dir, b.id, hkey, scope_values.keys())
                    if hcached is None:
                        fragment = humanize_fragment(b, scope_values, fragment, provider,
                                                     model, env, llm_call)
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
            # THROUGH THE CONTRACT, never straight into `resolved`. A human answer
            # lands in fixed markup that already carries half the sentence -- section
            # 3's cell reads `{{FIGURE}} - "Your Best Plan" {{VERDICT}}` -- so an empty
            # or shapeless answer does not render as "unanswered", it renders as the
            # surrounding words asserting themselves. That is the assertion issue #196
            # removed, restored by a blank line in an answers file. Block answers have
            # been validated all along (find_html_structure_violations); token answers
            # were spliced unasked.
            try:
                resolved[name] = rt.validate_gap_answer(name, human_answers[override_key])
            except SystemExit as e:
                failures.append((name, "gap-token", str(e)))
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
    p.add_argument("--only", help="process only this section id's blocks, e.g. s7 -- for "
                   "previewing/debugging one section's prompts only; must be combined "
                   "with --dry-run (see issue #66: a real run must never publish a file "
                   "with every OTHER section's TODO blocks left unrendered)")
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
