#!/usr/bin/env python3
"""The TODO-block classification map for issue #39 part 5: parses every
actionable ``<!-- TODO ... -->`` block out of report-template.html and
declares, for each one, whether it is filled by an LLM (``prose``), filled
mechanically from a committed artifact with no model call (``data``), or left
as a blocking item for a human (``human``). analysis/generate_report.py is
the orchestrator that consumes this map; this module owns the map itself
plus the mechanical row-builders the ``data`` blocks need.

WHY A SEPARATE MODULE FROM report_tokens.py
  report_tokens.py already solved "parse report-template.html for every
  {{TOKEN}}"; TODO blocks are a different shape (HTML comments, not
  double-brace tokens) so they get their own parser here, but this module
  reuses report_tokens.py's cached artifact loaders (_json/_csv_rows/hh1)
  and its ROOT/TEMPLATE/rates-module handles rather than re-declaring them.

PARSING
  parse_todo_blocks() finds every <!-- ...TODO... --> comment in the
  template EXCEPT the very first one (the top-of-file authoring
  instructions, which mentions the word TODO in passing but is not a
  fill-in site -- see report-template.html's own header comment). Each
  block gets a stable id of the form "<section>#<n>" where <section> is the
  nearest preceding <h2 id="..."> (or "top" for the source/season/study-description blocks that sit
  before s0, in the header/meta/day-band/purpose area) and <n> counts
  occurrences within that section in document order. This id is stable
  under any edit that doesn't reorder or add/remove TODO blocks, which is
  exactly the same stability report_tokens.py's own token names have.

CLASSIFICATION
  CLASSIFICATION maps every block id to "prose" / "data" / "human". A block
  is:
    - "data" when its own instruction is a mechanical "one <tr> per X" loop
      over a committed artifact (DATA_BUILDERS has a matching row-builder),
      or when the position is a VESTIGIAL leftover -- a TODO comment that
      sits immediately after a {{TOKEN}} in the same element that already
      fully renders what the TODO asks for (report_tokens.py's
      DATA_SOURCES_SUMMARY, DAYBAND_SEASON_LABEL, SEC9_TEASER, SEC12_TEASER,
      SEC13_TEASER, DATA_SOURCES_DETAIL all have this shape) -- these
      resolve to "" (the comment is simply dropped).
    - "human" when satisfying it truthfully needs a fact THIS REPO DOES NOT
      HOLD -- not in data/, not in private/household.yaml, not in
      analysis/*.py: a hardware price quote, an incentive program's current
      status. Inventing one would violate CLAUDE.md section 0.

      THE TEST IS THE EVIDENCE, NOT THE TOKEN INVENTORY (issue #132). "No
      report_tokens.py entry" is NOT a reason to mark a block human, and
      eleven blocks were wrongly human for exactly that reason: a committed
      artifact answered each of them and only the TOKENS entry carrying the
      answer was missing, which is a thing to write rather than a blocker.
      Every human block therefore also declares a checkable blocker in
      HUMAN_BLOCKERS -- a KNOWN_GAPS token, or a named outside fact -- and
      test_report_blocks.py fails the moment one of those stops holding.
    - "prose" otherwise: the block can be answered using only tokens
      already in report_tokens.TOKENS plus qualitative, non-numeric
      language -- the numeral guard in generate_report.py is what actually
      enforces "no invented digit," not this classification, so a prose
      block is free to omit a sub-point its TODO text asks for if no token
      backs it, rather than inventing a figure.

  This classification is a judgment call in a real number of places (see
  the PR description for the full per-block reasoning); it is deliberately
  conservative -- three deep-dive research blocks in section 9 and several
  more elsewhere are "human" specifically because report_tokens.py's own
  KNOWN_GAPS or a plain absence of any matching TOKENS entry means writing
  them mechanically-safely would require inventing a number.

  validate_classification() re-parses the template fresh and asserts
  CLASSIFICATION's key set is EXACTLY the parsed block id set (not a
  superset, not a subset) -- generate_report.py calls this before doing any
  work, so an edited template that adds, removes, or reorders a TODO block
  fails closed by naming the exact block, rather than silently mis-mapping
  ids.

LIVE GAP TOKENS
  Some of report_tokens.py's KNOWN_GAPS tokens appear in LIVE template
  markup -- static prose OUTSIDE any TODO block, not just inside worked
  examples -- so they always fail resolve_token() regardless of how any
  TODO block is classified. generate_report.py treats these exactly like a
  "human" block: LIVE_GAP_TOKENS names them so the orchestrator can require
  an explicit human-supplied override or refuse to publish, rather than
  crashing on the first resolve_token() call.

  WHICH ones, and HOW MANY of each set, are deliberately not written down
  here: both counts have gone stale already (a gap token left KNOWN_GAPS,
  and a template edit moved another out of live markup), and a maintainer
  reads a typed count instead of re-deriving it. LIVE_GAP_TOKENS below is
  computed from the template at import time, len(report_tokens.KNOWN_GAPS)
  is the other count, and main() prints the live set.
"""
import datetime as dt
import html as _html
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as R                # noqa: E402
import report_tokens as rt       # noqa: E402

ROOT = rt.ROOT
TEMPLATE = rt.TEMPLATE

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_H2_RE = re.compile(r'<h2 id="([^"]+)"')
_TOKEN_REF_RE = re.compile(r"\{\{([A-Z0-9_]*)\}\}")


class Block:
    __slots__ = ("id", "section", "line", "start", "end", "text", "full_comment")

    def __init__(self, id_, section, line, start, end, text, full_comment):
        self.id = id_
        self.section = section
        self.line = line
        self.start = start
        self.end = end
        self.text = text
        self.full_comment = full_comment

    def __repr__(self):  # pragma: no cover - debug aid only
        return f"Block({self.id!r}, section={self.section!r}, line={self.line})"


def _sections(html):
    return [(m.start(), m.group(1)) for m in _H2_RE.finditer(html)]


def _section_for(sections, pos):
    cur = None
    for spos, sid in sections:
        if spos <= pos:
            cur = sid
        else:
            break
    return cur


def parse_todo_blocks(html=None):
    """Every actionable TODO block, in document order, each tagged with a
    stable "<section>#<n>" id. Excludes the top-of-file instructions
    comment (the first HTML comment in the file), which is not a fill-in
    site -- see this module's docstring."""
    text = TEMPLATE.read_text() if html is None else html
    spans = [m.span() for m in _COMMENT_RE.finditer(text)]
    if not spans:
        raise SystemExit("report_blocks: no HTML comments found in the template at all -- "
                          "the top-of-file instructions comment should always be present")
    first = spans[0]
    sections = _sections(text)
    counts = {}
    blocks = []
    for s, e in spans:
        if (s, e) == first:
            continue
        comment = text[s:e]
        if "TODO" not in comment:
            continue
        sec = _section_for(sections, s) or "top"
        counts[sec] = counts.get(sec, 0) + 1
        bid = f"{sec}#{counts[sec]}"
        line = text.count("\n", 0, s) + 1
        inner = comment[len("<!--"):-len("-->")].strip()
        blocks.append(Block(bid, sec, line, s, e, inner, comment))
    return blocks


def validate_classification(html=None):
    """Fail closed (SystemExit, naming the exact block(s)) unless
    CLASSIFICATION's key set is exactly the freshly-parsed block id set."""
    blocks = parse_todo_blocks(html)
    parsed_ids = {b.id for b in blocks}
    declared_ids = set(CLASSIFICATION)
    missing = parsed_ids - declared_ids
    extra = declared_ids - parsed_ids
    if missing:
        detail = ", ".join(f"{b.id} (line {b.line}: {b.text[:70]!r})"
                            for b in blocks if b.id in missing)
        raise SystemExit(f"report_blocks: {len(missing)} TODO block(s) in the template have "
                          f"no CLASSIFICATION entry: {detail}")
    if extra:
        raise SystemExit(f"report_blocks: CLASSIFICATION names {len(extra)} block id(s) that "
                          f"no longer exist in the template: {sorted(extra)}")
    bad_kind = {bid: k for bid, k in CLASSIFICATION.items() if k not in ("prose", "data", "human")}
    if bad_kind:
        raise SystemExit(f"report_blocks: CLASSIFICATION has invalid kind(s): {bad_kind}")
    missing_reasons = {bid for bid, k in CLASSIFICATION.items()
                        if k == "human" and bid not in HUMAN_REASONS}
    if missing_reasons:
        raise SystemExit(f"report_blocks: human-classified block(s) with no HUMAN_REASONS "
                          f"entry: {sorted(missing_reasons)}")
    # A prose reason a reader can nod at is not a justification a test can
    # check (issue #132). Every human block also declares WHAT blocks it, in
    # exactly one of the two checkable forms.
    missing_blockers = {bid for bid, k in CLASSIFICATION.items()
                         if k == "human" and bid not in HUMAN_BLOCKERS}
    if missing_blockers:
        raise SystemExit(f"report_blocks: human-classified block(s) with no HUMAN_BLOCKERS "
                          f"entry naming a checkable blocker: {sorted(missing_blockers)}")
    bad_blockers = {bid: sorted(b) for bid, b in HUMAN_BLOCKERS.items()
                     if bool(b.get("gap_tokens")) == bool(b.get("outside_fact"))
                     or set(b) - {"gap_tokens", "outside_fact"}}
    if bad_blockers:
        raise SystemExit(
            "report_blocks: every HUMAN_BLOCKERS entry declares exactly one of "
            f"gap_tokens / outside_fact and nothing else; these do not: {bad_blockers}")
    missing_builders = {bid for bid, k in CLASSIFICATION.items()
                         if k == "data" and bid not in DATA_BUILDERS}
    if missing_builders:
        raise SystemExit(f"report_blocks: data-classified block(s) with no DATA_BUILDERS "
                          f"entry: {sorted(missing_builders)}")
    return blocks


# ---------------------------------------------------------------------------
# THE CLASSIFICATION MAP. One entry per parsed block id (verified by
# validate_classification() / test_report_blocks.py against a fresh parse).
# ---------------------------------------------------------------------------
CLASSIFICATION = {
    "balcony-solar#3": "data",  # matched chart source tables

    # --- header / meta / day-band / purpose (before s0) -------------------
    "top#1": "data",    # sources summary -- already fully rendered by {{DATA_SOURCES_SUMMARY}}
    "top#2": "data",    # day-band season caption -- already rendered by {{DAYBAND_SEASON_LABEL}}
    "top#3": "data",    # study description already lives in fixed markup

    # --- s0 Bottom line: opening decision, bill dates, supporting findings --
    "s0#1": "prose", "s0#2": "prose", "s0#3": "prose", "s0#4": "prose",
    "s0#5": "prose", "s0#6": "prose", "s0#7": "prose",
    "s0#8": "prose", "s0#9": "prose", "s0#10": "prose", "s0#11": "prose",

    # --- s1 The data ---------------------------------------------------
    "s1#1": "prose", "s1#2": "prose",

    # --- s2 Your solar system today --------------------------------------
    "s2#1": "prose",   # gateway/metering configuration -- DATA_SOURCES_SUMMARY/DETAIL cover it
    "s2#2": "prose",   # monitoring platforms in use
    "s2#3": "prose",   # daily range -- DAILY_PRODUCTION_MEAN/BEST/WORST, enphase_daily_production.csv
    "s2#4": "prose",   # output health -- DEGRADATION_NAIVE_RANGE; the per-panel ask is retired
                        # from the TODO because nothing committed records per-module output
    # s2#5 (key architectural fact) retired (issue #180): the paragraph is now the
    # literal {{S2_EXPORT_TIMING_NOTE}} token, not a TODO comment, so report_blocks.py
    # no longer parses a block there and there is nothing left for CLASSIFICATION to
    # classify.

    # --- s3 Rate plan comparison ------------------------------------------
    "s3#1": "prose",   # pricing methodology, qualitative
    "s3#2": "data",    # one <tr> per remaining eligible plan -- plan_results.csv
    "s3#3": "prose",   # footnotes
    "s3#4": "prose",   # structural reason -- ONPEAK/SOP_IMPORT_SHARE_PCT etc.
    "s3#5": "prose",   # generation-provider CCA note

    # --- s4 Battery x plan --------------------------------------------
    "s4#1": "prose",
    "s4#2": "data",    # one <tr> per alternative plan tested -- battery_plan_matrix.json
    "s4#3": "prose",

    # --- s5 Usage profile ---------------------------------------------
    "s5#1": "prose", "s5#2": "prose", "s5#3": "prose", "s5#4": "prose",
    "s5#5": "prose", "s5#6": "prose",

    # --- s6 Battery hardware -------------------------------------------
    "s6#2": "human",   # framing incl. current incentive status -- INCENTIVE_STATUS is a KNOWN_GAP
    "s6#3": "data",    # one <tr> per alternative config -- battery_sim.json
    "s6#4": "prose",   # STORED_KWH_COST_SOLAR/GRID tokens exist
    "s6#5": "prose",   # deliberate exclusions, qualitative
    "s6#6": "data",    # one <tr> per additional dispatch policy -- battery_dispatch_policies.json
    "s6#7": "prose",   # simulation assumptions, cites DISPATCH_SCRIPT
    "s6#8": "data",    # one <tr> per config -- backup_endurance.json
    "s6#9": "human",   # "get quotes" pricing footnote -- hardware price quotes
    "s6#10": "prose",   # controller and sizing supporting explanation
    "s6#11": "prose",  # partial-season program scope and enrollment limits
    "s6#12": "prose",  # program and resilience supporting explanation
    "balcony-solar#1": "data",    # checked portable/joint paragraph or table
    "balcony-solar#4": "data",    # checked portable/joint paragraph or table
    "balcony-solar#5": "data",    # checked portable/joint paragraph or table
    "balcony-solar#6": "data",    # checked portable/joint paragraph or table
    "balcony-solar#7": "data",    # checked portable/joint paragraph or table
    "balcony-solar#8": "data",    # checked portable/joint paragraph or table
    "balcony-solar#2": "data",    # checked portable/joint paragraph or table
    "balcony-solar#9": "data",    # checked portable/joint paragraph or table
    "balcony-solar#10": "data",    # checked portable/joint paragraph or table
    "balcony-solar#11": "data",    # checked portable/joint paragraph or table
    "balcony-solar#12": "data",    # checked portable/joint paragraph or table
    "balcony-solar#13": "data",    # checked portable/joint paragraph or table
    "balcony-solar#14": "data",    # checked standalone capacity-sweep basis
    "balcony-solar#15": "data",    # checked capacity increments

    "balcony-solar#16": "data",  # checked decision-support view
    "balcony-solar#17": "data",  # checked decision-support view
    "balcony-solar#18": "data",  # checked decision-support view
    "balcony-solar#19": "data",  # checked decision-support view
    "balcony-solar#20": "data",  # checked decision-support view
    "balcony-solar#21": "data",  # checked decision-support view
    "balcony-solar#22": "data",  # checked decision-support view
    "balcony-solar#23": "data",  # checked decision-support view
    "balcony-solar#24": "data",  # checked decision-support view
    "balcony-solar#25": "data",  # checked decision-support view
    "balcony-solar#26": "data",  # checked decision-support view

    # --- s7 The decision: three packages ---------------------------------
    "s7#1": "prose", "s7#2": "prose", "s7#3": "prose", "s7#4": "prose",
    "s7#5": "prose", "s7#6": "prose", "s7#7": "prose", "s7#8": "prose",
    "s7#9": "prose", "s7#10": "prose", "s7#11": "prose",

    "s6#1": "data",  # common-assumption decision comparison

    # --- s15 What to do Monday --------------------------------------------
    "s15#1": "data",   # content-only appendix intro -- authoring note, nothing to render
    "s15#2": "prose",  # CHEAP_WINDOW token exists
    "s15#3": "prose",  # pre-battery checklist, qualitative
    "s15#4": "data",   # the metric-name cell -- a fixed label for METRIC_NOW/METRIC_TARGET
    "s15#5": "prose",  # re-run triggers
    "s15#6": "prose",  # paired "When" cell

    # --- s8 Array upgrades -------------------------------------------
    "s8#1": "human",   # asks for EXPANSION_PAYBACK_YEARS (gap -- the
                        # marginal-kW yield is derived now, issue #190, and the
                        # live markup publishes it through the MARGINAL_KW_*
                        # tokens; the retrofit $/W is still not collected).
                        # Named in the block's own TODO text as a figure that
                        # must NOT be supplied, which is where its scope picks
                        # the token up.

    # --- s9 Deeper analyses ------------------------------------------
    "s9#1": "data",    # teaser -- already rendered by {{SEC9_TEASER}}
    "s9#2": "prose",   # degradation -- ARRAY_EFFICIENCY_SERIES / DEGRADATION_NAIVE_RANGE /
                        # DEGRADATION_WEATHER_CAVEAT, gross_import_decomposition.json
    "s9#3": "prose",   # clipping -- PV_PEAK_OBSERVED / PV_PEAK_HEADROOM /
                        # PEAK_POWER_MULTIYEAR, service_headroom.json + the 2024 peaks CSV
    "s9#4": "prose",   # cooling regression -- the seven weather_results.json tokens
    "s9#5": "prose",   # EV report card -- EV_SESSION_COUNT/ANNUAL_KWH/AVG_SESSION_KWH/
                        # WINDOW_DECOMPOSITION/SOP_COMPLIANCE_PCT, behavior_rebuild.json
    "s9#6": "prose",   # wildcard plan -- WILDCARD_PLAN + BEST_PLAN tokens, qualitative verdict
    "s5#8": "prose",   # phantom honesty note, qualitative

    # --- s10 Bills, gas & electrification ----------------------------
    "s10#1": "prose",  # bill facts -- narrative rows, not a uniform artifact loop
    "s10#2": "prose",  # ACTUAL_ANNUAL_BILL / MODELED_ANNUAL_AT_CURRENT_RATES tokens exist
    "s10#3": "prose",  # ACTUAL_ANNUAL_GAS_BILL token exists
    "s10#4": "prose",  # electrification math -- all_electric_endgame.json +
                        # heat_pump_conversion.json now cost BOTH appliances. The section's
                        # own {{ELECTRIFICATION_VERDICT_SHORT}} HEADING token is still a
                        # live gap needing a human override; the block below it is not.
    "s10#5": "prose",  # gas fixed vs volumetric, qualitative

    # --- s11 Lifetime value --------------------------------------------
    "s11#1": "prose",  # method-split line, qualitative
    "s11#2": "data",   # one <tr> per year -- lifetime_payback.json
    "s11#3": "prose",  # rounding note
    "s11#4": "prose",  # crossover/blended-rate narrative, PAYBACK_HEADLINE/SOLAR_ANNUAL_VALUE

    # --- s12 Panel cleaning & soiling ------------------------------------
    "s12#1": "data",   # teaser -- already rendered by {{SEC12_TEASER}}
    "s12#2": "data",   # one <tr> per control year -- cleaning_study_daily.csv
    "s12#3": "prose",  # diff-in-diff result -- CLEANED_RATIO/CLEANING_EFFECT_PCT tokens
    "s12#4": "prose",  # soiling reconciliation -- SOILING_RATE_RANGE/SOILING_SCRIPT tokens
    "s12#5": "prose",  # cadence -- CLEANING_BEST_MONTH / CLEANING_SINGLE_VALUE_RANGE /
                        # CLEANING_SECOND_MARGINAL_RANGE, extra_results.json:cleaning

    # --- s13 Carbon, NEM, escalation & price map -------------------------
    "s13#1": "data",   # teaser -- already rendered by {{SEC13_TEASER}}
    "s13#2": "prose",  # carbon narrative -- CARBON_SAMPLE_DESCRIPTION token
    "s13#3": "prose",  # NEM re-bill narrative -- NEM_GRANDFATHER_VALUE_RANGE token
    "s13#4": "data",   # one <tr> per escalation scenario -- battery_dispatch_policies.json
    "s13#5": "prose",  # base-case assumptions, qualitative
    "s13#6": "prose",  # ladder-vs-spread framing -- ESCALATION_HISTORICAL token
    "s13#7": "prose",  # spread chart caption, qualitative
    "s13#8": "prose",  # per-season spread trend -- SPREAD_TREND_SUMMER/WINTER carry
                        # tou_spread.json's own verdict, reason and corpus, including
                        # "not determined", which is the answer on this corpus
    "s13#9": "prose",  # battery on the measured spread -- BATTERY_ON_MEASURED_SPREAD /
                        # SPREAD_BATTERY_SEED_SAVING, tou_spread.json:battery
    "s13#10": "prose", # scope caveat, qualitative
    "s5#7": "prose", # night floor -- the seven NIGHT_FLOOR_* tokens, quiet_night_floor.json
    "s13#11": "data",  # one <tr> per season x TOU-period -- analysis/rates.py
    "s13#12": "prose", # rate-table provenance, qualitative

    # --- s14 Methodology -------------------------------------------------
    "s14#1": "prose", "s14#2": "prose", "s14#3": "prose", "s14#4": "prose",
    "s14#5": "prose", "s14#6": "prose", "s14#7": "prose", "s14#8": "prose",
    "s14#9": "prose", "s14#10": "prose", "s14#11": "prose", "s14#12": "prose",
    "s14#13": "prose", "s14#14": "prose",
    "s14#15": "data",  # data sources full inventory -- already rendered by {{DATA_SOURCES_DETAIL}}
}

# ---------------------------------------------------------------------------
# WHY EACH REMAINING BLOCK IS HUMAN -- AND THE TEST THAT IS ACTUALLY BEING
# APPLIED (issue #132).
#
# Eleven of the fourteen entries this map used to hold are gone, because they
# were never true. Eight of them justified themselves with the words "no
# report_tokens.py entry" -- a test of THIS PIPELINE'S TOKEN INVENTORY, not of
# the evidence. A committed artifact answered every one of those eight
# questions; what was missing was a TOKENS entry to carry the answer, which is
# a thing to write, not a reason to block. Stated that way the map could only
# rot: every generator added after it was written left a stale "human" behind
# it, and nothing in the test suite could tell the difference between "no
# evidence exists" and "nobody wired the evidence up".
#
# The test each surviving entry states, and which the AC5 case in
# test_report_blocks.py CHECKS rather than reads:
#
#   THE FACT IS NOT IN THIS REPO AT ALL. Not in data/, not in
#   private/household.yaml, not in analysis/*.py. Every such block names its
#   blocker in HUMAN_BLOCKERS below -- either a report_tokens.KNOWN_GAPS token
#   (checkable: if the token leaves KNOWN_GAPS, the justification is void and
#   the case fails naming this block) or an outside fact this repo does not
#   collect (a price quote), named explicitly so the exemption is written down
#   rather than inferred from a token that happens to resolve.
#
# Reasons here name the ARTIFACT or the GAP TOKEN whose absence blocks the
# block. "No token exists" is not a reason and must not reappear.
# ---------------------------------------------------------------------------
HUMAN_REASONS = {
    "s6#2": "blocked on {{INCENTIVE_STATUS}}, a report_tokens.KNOWN_GAPS token. The "
            "CALIFORNIA side of this question is now committed research -- "
            "data/heat_pump_conversion.json:incentives dates SGIP's ratepayer-budget "
            "close, and data/dsgs_vpp_backtest.json:finding_2026_enrollment_eligibility "
            "reads the CEC's own DSGS guidelines -- so the blocker is narrower than it "
            "was: no committed artifact records the CURRENT FEDERAL residential storage "
            "credit's status, and an incentive figure without an as-of date is not "
            "something this report may publish",
    "s6#9": "blocked on an INSTALLED PRICE QUOTE and the cross-ecosystem cost/warranty "
            "deltas that go with it. No data/*.json or *.csv in this repo carries a "
            "quoted installed price for any battery -- data/battery_sim.json prices "
            "capacity and savings, never a bid -- and a quote is a fact about a local "
            "market on a date, which this pipeline has no way to measure",
    "s8#1": "blocked on {{EXPANSION_PAYBACK_YEARS}}, a report_tokens.KNOWN_GAPS token. "
            "One half of that payback is derived: what one more kW would earn is "
            "data/marginal_capacity_value.json's interval-level counterfactual re-billed "
            "through the NEM engine (issue #190), published by {{MARGINAL_KW_VALUE_YR}}. "
            "The other half is not: retrofit dollars per watt is a fact about a local "
            "installer market on a date, which nothing committed here measures. "
            "(The block's other two asks ARE artifact-backed now: what the year's "
            "exports are worth is bounded by {{EXPORT_VALUE_SURPLUS_BOUND}} and "
            "{{EXPORT_VALUE_NETTING_BOUND}}, the two NEM 2.0 settlement treatments "
            "of the committed hour-of-day export profile, and the clipping evidence "
            "is s9#3's.)",
}

# ---------------------------------------------------------------------------
# THE MACHINE-CHECKABLE HALF OF EACH HUMAN REASON. Prose above for a reader;
# this for the AC5 case, which can only check a fact.
#
# gap_tokens : report_tokens.KNOWN_GAPS token names whose absence blocks the
#              block. Checked three ways: still declared, still kind="gap",
#              and actually inside this block's own scope -- so a justification
#              cannot cite a gap belonging to some other section.
# outside_fact: for a block blocked by something this repo does not collect at
#              all. There is no token to fail, so the case cannot detect the
#              justification going stale, and the exemption is written down
#              here with what would end it rather than inferred from silence.
# ---------------------------------------------------------------------------
HUMAN_BLOCKERS = {
    "s6#2": {"gap_tokens": ("INCENTIVE_STATUS",)},
    "s6#9": {"outside_fact": "an installed price quote for a specific battery from a "
                             "specific installer on a specific date; ends when a "
                             "committed artifact records one"},
    "s8#1": {"gap_tokens": ("EXPANSION_PAYBACK_YEARS",)},
}

# The report_tokens.py KNOWN_GAPS tokens that appear in LIVE template markup
# (outside any TODO comment) fail resolve_token() regardless of any TODO
# block's classification. Which ones, and how many, is computed from the
# template itself rather than typed here -- a typed count is the thing a
# maintainer trusts instead of re-deriving, and both halves of the one that
# used to head this comment ("three of five") went stale.
def _live_gap_tokens(html=None):
    live, _ = rt.template_tokens(html)
    gaps = {n for n, s in rt.TOKENS.items() if s.get("kind") == "gap"}
    return frozenset(live & gaps)


LIVE_GAP_TOKENS = _live_gap_tokens()


# ---------------------------------------------------------------------------
# Per-block scoping: which resolved token VALUES a prose block's LLM call may
# be handed as context. Scope = every token that appears LIVE anywhere inside
# that block's own <h2> section, union every token named inside the block's
# own TODO text. This is what lets generate_report.py's cache invalidate only
# the blocks whose scope actually changed when one artifact value changes.
# ---------------------------------------------------------------------------
def _section_span(html, section_id):
    sections = _sections(html)
    start = None
    for i, (pos, sid) in enumerate(sections):
        if sid == section_id:
            start = pos
            end = sections[i + 1][0] if i + 1 < len(sections) else len(html)
            return start, end
    if section_id == "top":
        first_h2 = sections[0][0] if sections else len(html)
        return 0, first_h2
    raise SystemExit(f"report_blocks: section {section_id!r} not found in the template")


def tokens_live_in_section(html, section_id):
    start, end = _section_span(html, section_id)
    live, _ = rt.template_tokens(html[start:end])
    return live


def tokens_mentioned(text):
    return {m for m in _TOKEN_REF_RE.findall(text) if m}


def scope_tokens_for_block(html, block):
    return tokens_live_in_section(html, block.section) | tokens_mentioned(block.text)


# ---------------------------------------------------------------------------
# DATA-CLASS ROW BUILDERS. Each returns the literal HTML to splice into the
# block's slot -- either "" (vestigial comment, nothing to add), a fixed
# mechanical label, or one <tr> per artifact row. None of these makes an LLM
# call or needs the numeral guard: every digit here comes straight from a
# committed data/*.json or data/*.csv file or analysis/rates.py, exactly like
# report_tokens.py's own "derived" token formulas. Output may itself contain
# {{TOKEN}} references (e.g. {{PEAK_WINDOW}}) -- these are resolved in the
# SAME final global substitution pass generate_report.py runs over the whole
# rendered document, so a data builder never needs to duplicate a token's own
# formula.
# ---------------------------------------------------------------------------
def _usd0(v):
    return f"${v:,.0f}"


def _usd0_signed(v):
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def _usd3(v):
    return f"${v:,.3f}"


def _num1(v):
    return f"{v:,.1f}"


def _num2(v):
    return f"{v:,.2f}"


def _esc(value):
    """HTML-escape one ARTIFACT-DERIVED free-text cell value (a plan name, a
    battery config name, an escalation-rate key) before splicing it into a
    row. Mirrors generate_report.py's render()-time html.escape() on
    {{TOKEN}} values -- an adversarial review found this module's own row
    builders had no equivalent, even though they build raw HTML via f-string
    interpolation exactly like render()'s substitution does. Applied ONLY to
    values that come from a committed data/*.json or *.csv file; hardcoded
    Python string-literal labels this module declares itself (e.g.
    _DISPATCH_POLICY_LABELS, _ENDURANCE_LABELS, _SEASON_LABEL/_PERIOD_LABEL)
    are trusted source code, not runtime data, and are NOT escaped here --
    _ENDURANCE_LABELS specifically contains an intentional literal
    "&times;" HTML entity that blanket-escaping would corrupt into
    "&amp;times;". Numeric values formatted by _usd0/_usd3/_num1/_num2 are
    plain digit/currency strings that cannot contain an HTML metacharacter,
    so they are left as-is too (escaping them would be a harmless no-op, not
    a bug, but this keeps the "why is this one escaped" reasoning legible)."""
    return _html.escape(str(value), quote=True)


def _empty():
    return ""


def _s15_metric_label():
    return "On-peak share of grid imports (%)"


def _s3_plan_rows():
    rows = rt._csv_rows("plan_results.csv")
    current_plan = rt.hh1("household.plan")
    by_plan = {}
    for r in rows:
        by_plan.setdefault(r["plan"], {})[r["provider"]] = r
    if current_plan not in by_plan:
        raise SystemExit(f"report_blocks: household plan {current_plan!r} not present in "
                          "data/plan_results.csv")
    current_cea = float(by_plan[current_plan]["CEA"]["total"])
    others = sorted((p for p in by_plan if p != current_plan),
                     key=lambda p: float(by_plan[p]["CEA"]["total"]))
    out = []
    for p in others:
        cea = float(by_plan[p]["CEA"]["total"])
        sdge = float(by_plan[p]["SDGE"]["total"])
        out.append(f"<tr><td>{_esc(p)}</td><td>{_usd0(cea)}</td><td>{_usd0(sdge)}</td>"
                   f"<td>{_usd0_signed(cea - current_cea)}</td><td>&mdash;</td></tr>")
    return "\n".join(out)


def _s4_battery_plan_rows():
    plans = rt._json("battery_plan_matrix.json")["plans"]
    current_plan = rt.hh1("household.plan")
    # Ordered on the cents, not the whole-dollar cell (issue #177): two rivals
    # that round to the same dollar would otherwise take the JSON's key order,
    # which is not a ranking. The cells printed stay the whole-dollar ones.
    # Through rt._bpm_column_cents rather than a bare key read, so a matrix
    # with no _cents fields (one from before #177) or a dollar cell that
    # disagrees with its cents twin gets the same named refusal here as at
    # the ranked tokens. A bare KeyError would escape generate_report's data
    # builders as a traceback, before the manifest is written, and the
    # "regenerate with battery_plan_matrix.py" remedy would never print.
    rivals = {p: plans[p] for p in plans if p != current_plan}
    cents = rt._bpm_column_cents(
        "S4 battery-plan rows", "no_battery", rivals,
        "which order section 4's rival rows take in its no_battery column")
    others = sorted(rivals, key=lambda p: cents[p])
    out = []
    for p in others:
        d = plans[p]
        out.append(f"<tr><td>{_esc(p)}</td><td>{_usd0(d['no_battery'])}</td>"
                   f"<td>{_usd0(d['with_battery'])}</td>"
                   f"<td>{_usd0(d['battery_value'])}/yr</td></tr>")
    return "\n".join(out)


def _s6_arbitrage_rows():
    rows = rt._json("battery_sim.json")
    out = []
    for r in rows:
        if r["config"] == "1x Tesla Powerwall 3":
            continue
        out.append(f"<tr><td>{_esc(r['config'])}</td>"
                   f"<td>{_num1(r['usable_kwh'])} / {_num1(r['power_kw'])}</td>"
                   f"<td>{_usd0(r['net_annual_savings'])}</td><td>&mdash;</td></tr>")
    return "\n".join(out)


_DISPATCH_POLICY_LABELS = {"evening": "Evening-only", "twowin": "Two-window"}


def _s6_dispatch_rows():
    dp = rt._json("battery_dispatch_policies.json")
    out = []
    for key, label in _DISPATCH_POLICY_LABELS.items():
        p3, p3x = dp["pw3"][key], dp["pw3x"][key]
        out.append(
            f"<tr><td>{label}</td>"
            f"<td><b>{_usd0(p3['save'])}/yr</b> &middot; {p3['kwh_served']:,} kWh &middot; "
            f"{_num2(p3['cycles_per_day'])} cycles/day</td>"
            f"<td><b>{_usd0(p3x['save'])}/yr</b> &middot; {p3x['kwh_served']:,} kWh</td></tr>")
    return "\n".join(out)


_ENDURANCE_LABELS = {"IQ 5P": "1&times; Enphase IQ 5P",
                     "IQ 10C": "1&times; Enphase IQ 10C",
                     "PW3+Exp": "PW3 + Expansion"}


def _s6_endurance_rows():
    be = rt._json("backup_endurance.json")
    out = []
    for key, label in _ENDURANCE_LABELS.items():
        t1, t2 = be[f"{key}|t1"], be[f"{key}|t2"]
        out.append(f"<tr><td>{label}</td>"
                   f"<td><b>{t1['median_h']} h (p10 {t1['p10_h']} h)</b></td>"
                   f"<td>{t2['median_h']} h (p10 {t2['p10_h']} h)</td></tr>")
    return "\n".join(out)


def _s11_year_rows():
    lp_data = rt._json("lifetime_payback.json")
    years = lp_data["years"][1:]           # index 0 is already shown in the template
    crossover_years = {lp_data["crossover"]["gross"]["year"],
                       lp_data["crossover"]["net_itc"]["year"]}
    out = []
    for y in years:
        cls = ' class="win"' if y["year"] in crossover_years else ""
        val_rounded = round(y["value_usd"] / 100) * 100
        cum_rounded = round(y["cumulative_usd"] / 100) * 100
        out.append(f"<tr{cls}><td>{y['year']}</td><td>{y['production_kwh']:,}</td>"
                   f"<td>~{_usd0(val_rounded)}</td><td>{_usd0(cum_rounded)}</td></tr>")
    return "\n".join(out)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _s12_control_year_rows():
    entry = rt._cleaning_entry()
    cleaning_date = rt._as_date(entry["date"])
    rows = rt._csv_rows("cleaning_study_daily.csv")
    parsed = [(dt.datetime.strptime(r["date"], "%Y%m%d"), float(r["generated_kwh"]))
             for r in rows]
    other_years = sorted({d.year for d, _ in parsed} - {cleaning_date.year})
    out = []
    for yr in other_years:
        center = dt.datetime(yr, cleaning_date.month, cleaning_date.day)
        pre = [v for d, v in parsed if center - dt.timedelta(days=30) <= d < center]
        post = [v for d, v in parsed if center < d <= center + dt.timedelta(days=30)]
        if not pre or not post:
            continue    # incomplete calendar window this control year -- skip, don't fabricate
        pre_m, post_m = _median(pre), _median(post)
        # :.3f, not a bare round(..., 3): round() drops a trailing zero
        # (round(0.9403, 3) == 0.94, printed "0.94"), so this column and
        # CLEANED_RATIO's own cell above it -- both the SAME post/pre ratio
        # statistic -- printed at two different, unstable precisions
        # (issue #276's sweep).
        out.append(f"<tr><td>{yr} (no cleaning)</td><td>{pre_m:.1f}</td>"
                   f"<td>{post_m:.1f}</td><td>{post_m / pre_m:.3f}</td></tr>")
    return "\n".join(out)


def _s13_escalation_rows():
    ladder = rt._json("battery_dispatch_policies.json")["escalation_published_pw3_post_behavior"]
    order = [k for k in ("3%", "5%", "12%") if k in ladder]
    out = []
    for k in order:
        r = ladder[k]
        sign = "+" if r["npv10"] >= 0 else "-"
        out.append(f"<tr><td>{_esc(k)}/yr</td><td>{r['payback']:.1f} yr</td>"
                   f"<td>{sign}${abs(r['npv10']):,}</td></tr>")
    return "\n".join(out)


_SEASON_LABEL = {"S": "Summer", "W": "Winter"}
_PERIOD_LABEL = {"on": "on-peak", "off": "off-peak", "sop": "super-off-peak"}
# (S, on) is already shown in the template's own first row.
_PRICEMAP_COMBOS = [("S", "off"), ("S", "sop"), ("W", "on"), ("W", "off"), ("W", "sop")]


def _s13_pricemap_rows():
    out = []
    for season, period in _PRICEMAP_COMBOS:
        label = f"{_SEASON_LABEL[season]} &middot; {_PERIOD_LABEL[period]}"
        if period == "on":
            label += " {{PEAK_WINDOW}}"
        imp = R.allin(season, period)
        exp = R.credit(season, period)
        out.append(f"<tr><td>{label}</td><td>{_usd3(imp)}</td><td>{_usd3(exp)}</td></tr>")
    return "\n".join(out)


# Deterministic portable/joint tables. Every figure comes from a checked artifact row.
_BALCONY_TREATMENTS = (("credited", "Hypothetical credit"), ("zero_credit", "No extra credit"))
_BALCONY_HOMES = ("none", "13.5", "27")


def _balcony_money(value, decimals=0):
    rt._balcony_number(value, "table amount")
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def _balcony_range(values):
    for value in values:
        rt._balcony_number(value, "controller-priority result")
    low, high = min(values), max(values)
    return (_balcony_money(low) if round(low) == round(high) else
            f"{_balcony_money(low)} to {_balcony_money(high)}")


def _balcony_home_label(home):
    if home == "none":
        return "No home storage"
    row = rt._balcony_row(rt._balcony_baseline_id(home))
    capacity = rt._balcony_number(row["batteries"][0]["capacity_kwh"], "home capacity")
    return f"{capacity:g} kWh home storage"


def _balcony_priorities(home):
    return ("home_first",) if home == "none" else ("home_first", "small_first")


def _balcony_summary_rows(cap="0.8"):
    rows = []
    if cap != "0.8":
        example = rt._balcony_row(rt._balcony_full_id("none", cap, "credited"))
        ac = rt._balcony_number(example["shared_new_pv_battery_ac_kw"], "higher shared cap")
        dc = rt._balcony_number(example["pv_dc_kw"], "higher PV size")
        rows.append(f'<tr><td colspan="4">Sensitivity: {ac:g} kW shared AC output; {dc:g} kW DC panels</td></tr>')
    for home in _BALCONY_HOMES:
        for treatment, label in _BALCONY_TREATMENTS:
            ids = [rt._balcony_full_id(home, cap, treatment, order) for order in _balcony_priorities(home)]
            totals = [rt._balcony_row(sid)["annual_savings_usd"] for sid in ids]
            increments = [rt._balcony_delta(rt._balcony_baseline_id(home), sid)[0] for sid in ids]
            rows.append(f'<tr data-scenarios="{_esc(",".join(ids))}" data-baseline="{_esc(rt._balcony_baseline_id(home) or "post_behavior")}"><td>{_balcony_home_label(home)}</td><td>{label}</td><td>{_balcony_range(increments)}</td><td>{_balcony_range(totals)}</td></tr>')
    return "\n".join(rows)


def _balcony_cost_rows():
    rows = []
    for home in _BALCONY_HOMES:
        for treatment, label in _BALCONY_TREATMENTS:
            sid = rt._balcony_full_id(home, "0.8", treatment)
            entry = rt._balcony_cost(sid)
            economics = entry["economics"]
            example = entry["cost_sensitivities"][1]
            cost = rt._balcony_number(example["installed_cost_usd"], "example complete cost")
            payback = example["simple_payback_years"]
            saving = entry["incremental_savings_usd"]
            if saving > 0:
                rt._balcony_number(payback, "example payback")
                if abs(payback - cost / saving) > 0.000002:
                    raise SystemExit("report_blocks: balcony example payback disagrees with the matched increment")
                illustration = f"{_balcony_money(cost)} / {payback:.1f} yr"
            elif payback is None:
                illustration = "No positive payback"
            else:
                raise SystemExit("report_blocks: balcony nonpositive increment has a positive payback")
            rows.append(f'<tr data-scenario="{_esc(sid)}"><td>{_balcony_home_label(home)}</td><td>{label}</td><td>{_balcony_money(economics["five_year"]["break_even_installed_usd"])}</td><td>{_balcony_money(economics["ten_year"]["break_even_installed_usd"])}</td><td>{illustration}</td></tr>')
    return "\n".join(rows)


def _balcony_options_rows():
    selections = (("Panels only", "pv_0.8kw_credited", "pv_0.8kw_zero_credit"),
                  ("Portable battery only; matched output cap", "battery_power_0.8kw", "battery_power_0.8kw"),
                  ("Panels + portable battery", "combined_0.8kw_credited", "combined_0.8kw_zero_credit"))
    rows = []
    for label, credited, uncredited in selections:
        values = [rt._balcony_row(sid)["annual_savings_usd"] for sid in (credited, uncredited)]
        rows.append(f'<tr data-scenarios="{_esc(credited + "," + uncredited)}"><td>{label}</td><td>{_balcony_money(values[0])}</td><td>{_balcony_money(values[1])}</td></tr>')
    return "\n".join(rows)


def _balcony_order_rows():
    rows = []
    for home in ("13.5", "27"):
        for cap in ("0.8", "1.2"):
            for treatment, label in _BALCONY_TREATMENTS:
                full = [rt._balcony_full_id(home, cap, treatment, order) for order in _balcony_priorities(home)]
                pv = rt._balcony_pv_id(home, cap, treatment)
                standalone = rt._balcony_full_id("none", cap, treatment)
                # Matched no-PV controller controls retain the balcony output cap.
                controls = ([f"home_{home}kwh_small_{cap}kw_{order}" for order in _balcony_priorities(home)]
                            if home == "27" or cap == "0.8" else ["powerwall_then_small", "small_then_powerwall"])
                increments = ([rt._balcony_delta(standalone, sid)[0] for sid in full],
                              [rt._balcony_delta(pv, sid)[0] for sid in full],
                              [rt._balcony_delta(before, after)[0] for before, after in zip(controls, full)])
                cap_value = rt._balcony_number(rt._balcony_row(full[0])["shared_new_pv_battery_ac_kw"], "shared cap")
                cells = "".join(f"<td>{_balcony_range(values)}</td>" for values in increments)
                rows.append(f'<tr data-scenarios="{_esc(",".join(full))}"><td>{_balcony_home_label(home)}</td><td>{cap_value:g} kW</td><td>{label}</td>{cells}</tr>')
    return "\n".join(rows)


def _balcony_component_rows():
    rows = []
    for home in _BALCONY_HOMES:
        for treatment, label in _BALCONY_TREATMENTS:
            sid = rt._balcony_full_id(home, "0.8", treatment)
            delta, components = rt._balcony_delta(rt._balcony_baseline_id(home), sid)
            if abs(components["fixed_usd"]) > 0.00001:
                raise SystemExit("report_blocks: balcony dispatch changed the fixed daily charge")
            cells = "".join(f"<td>{_balcony_money(components[key], 2)}</td>" for key in rt._BALCONY_COMPONENTS)
            rows.append(f'<tr data-scenario="{_esc(sid)}"><td>{_balcony_home_label(home)}</td><td>{label}</td>{cells}<td>{_balcony_money(delta, 2)}</td></tr>')
    return "\n".join(rows)


def _balcony_ladder_rows():
    rows = []
    heading_costs = [rt._balcony_number(rung["installed_cost_usd"], "assumed cost heading")
                     for rung in rt._balcony_cost("combined_0.8kw_credited")["cost_sensitivities"]]
    for home in _BALCONY_HOMES:
        for treatment, label in _BALCONY_TREATMENTS:
            for kind in ("pv", "combined"):
                sid = (rt._balcony_pv_id(home, "0.8", treatment) if kind == "pv" else
                       rt._balcony_full_id(home, "0.8", treatment))
                entry = rt._balcony_cost(sid)
                saving = entry["incremental_savings_usd"]
                rungs = entry["cost_sensitivities"]
                if len(rungs) != 4:
                    raise SystemExit("report_blocks: balcony complete-cost ladder needs four assumed costs")
                if [rung["installed_cost_usd"] for rung in rungs] != heading_costs:
                    raise SystemExit("report_blocks: balcony assumed costs disagree with the table headings")
                cells = []
                for rung in rungs:
                    cost = rt._balcony_number(rung["installed_cost_usd"], "assumed complete incremental cost")
                    payback = rung["simple_payback_years"]
                    if saving > 0:
                        rt._balcony_number(payback, "assumed-cost payback")
                        if abs(payback - cost / saving) > 0.000002:
                            raise SystemExit("report_blocks: balcony assumed-cost payback disagrees with its increment")
                        cells.append(f"<td>{payback:.1f} yr</td>")
                    elif payback is not None:
                        raise SystemExit("report_blocks: balcony nonpositive increment has a positive payback")
                    else:
                        cells.append("<td>No positive payback</td>")
                equipment = "Panels only" if kind == "pv" else "Panels + portable battery"
                economics = entry["economics"]
                rows.append(f'<tr data-scenario="{_esc(sid)}"><td>{_balcony_home_label(home)}; {equipment}</td><td>{label}</td><td>{_balcony_money(economics["five_year"]["break_even_installed_usd"])}</td><td>{_balcony_money(economics["ten_year"]["break_even_installed_usd"])}</td>{"".join(cells)}</tr>')
    return "\n".join(rows)


def _balcony_ladder_headings():
    entry = rt._balcony_cost("combined_0.8kw_credited")
    return "".join(f'<th>{_balcony_money(rt._balcony_number(row["installed_cost_usd"], "assumed complete cost"))} assumed cost</th>' for row in entry["cost_sensitivities"])


def _balcony_capacity_basis():
    reference = rt._balcony_row("battery_3.84kwh")["batteries"][0]
    charge = rt._balcony_number(reference["charge_kw"], "capacity-sweep charge power")
    discharge = rt._balcony_number(reference["discharge_kw"], "capacity-sweep discharge power")
    for sid in ("battery_1.92kwh", "battery_3.84kwh", "battery_5.76kwh", "battery_7.68kwh", "battery_11.52kwh"):
        row = rt._balcony_row(sid)
        if len(row["batteries"]) != 1 or row["pv_dc_kw"] != 0:
            raise SystemExit("report_blocks: balcony capacity sweep needs standalone storage without added PV")
        pack = row["batteries"][0]
        if pack["charge_kw"] != charge or pack["discharge_kw"] != discharge:
            raise SystemExit("report_blocks: balcony capacity sweep cannot claim fixed power after a power change")
    return f"Charge power is {charge:g} kW and discharge power is {discharge:g} kW."


def _balcony_capacity_rows():
    rows = []
    study = rt._json(rt._BALCONY_ARTIFACT)
    for before, after in (("battery_1.92kwh", "battery_3.84kwh"),
                          ("battery_3.84kwh", "battery_5.76kwh"),
                          ("battery_5.76kwh", "battery_7.68kwh"),
                          ("battery_7.68kwh", "battery_11.52kwh")):
        pairs = [row for row in study["marginal_comparisons"] if row["before"] == before and row["after"] == after]
        if len(pairs) != 1:
            raise SystemExit("report_blocks: balcony capacity increment needs exactly one matched pair")
        entry = pairs[0]
        delta, _ = rt._balcony_delta(before, after)
        if abs(delta - rt._balcony_number(entry["incremental_savings_usd"], "capacity increment")) > 0.00001:
            raise SystemExit("report_blocks: balcony capacity increment disagrees with complete bills")
        sizes = [rt._balcony_number(rt._balcony_row(sid)["batteries"][0]["capacity_kwh"], "capacity") for sid in (before, after)]
        economics = entry["economics"]
        ceilings = [rt._balcony_number(economics[key]["break_even_installed_usd"], "capacity ceiling") for key in ("five_year", "ten_year")]
        rows.append(f'<tr data-baseline="{_esc(before)}" data-scenario="{_esc(after)}"><td>{sizes[0]:g} to {sizes[1]:g} kWh</td><td>{_balcony_money(delta)}</td><td>{_balcony_money(ceilings[0])}</td><td>{_balcony_money(ceilings[1])}</td></tr>')
    return "\n".join(rows)


def _depth_quote_priority():
    candidates = {c['id']: c for c in rt._balcony_decision_support()['candidate_allowances']}
    dpu, ultra = candidates['dpu_two_battery'], candidates['ultrax_base']
    return _depth_paragraphs(
        f"Complete installed costs remain unknown. For bill savings, compare a supported storage quote before buying added panels. The generic two-battery Delta Pro Ultra case saves {_balcony_money(dpu['annual_savings_usd'])}/yr. Its advertised equipment-only cost is {_balcony_money(dpu['equipment_price_usd'])}. "
        f"The higher-output Ultra X-shaped case saves {_balcony_money(ultra['annual_savings_usd'])}/yr. Its advertised equipment-only cost is {_balcony_money(ultra['equipment_price_usd'])}. "
        "Their nearly equal savings give little bill-savings reason to pay for the extra output under this schedule.",
        "The Delta Pro Ultra connection and measured eligible load decide whether that comparison applies. Request a complete supported-storage quote if whole-meter access and these controls are feasible. "
        "If only a selected appliance is accessible, measure that appliance first. Stream's small budget and unavailable stock make it a separate feed-in case; it is not the generic combined system.")


_DEPTH_LABELS = {
    "pv_0.8kw_credited": "Panels only; hypothetical extra credit",
    "pv_0.8kw_zero_credit": "Panels only; no extra credit",
    "battery_power_0.8kw": "Portable battery only; 0.8 kW output",
    "combined_0.8kw_credited": "Panels + portable battery; hypothetical extra credit",
    "combined_0.8kw_zero_credit": "Panels + portable battery; no extra credit",
    "large_13.5kwh_11.5kw": "Home storage; 13.5 kWh",
    "large_27kwh_11.5kw": "Home storage; 27 kWh",
    "home_13.5kwh_combined_0.8kw_zero_credit_home_first": "Home 13.5 kWh + Panels + portable battery; no extra credit",
}
_DEPTH_CANDIDATES = {
    "stream_default": "Stream Ultra; default feed-in",
    "stream_conditional": "Stream Ultra; conditional feed-in",
    "dp3_base": "Delta Pro 3; equipment only",
    "dpu_base": "Delta Pro Ultra; equipment only",
    "dpu_shp2_bundle": "Delta Pro Ultra + Smart Home Panel 2",
    "dpu_two_battery": "Delta Pro Ultra; two batteries, without Smart Home Panel",
    "ultrax_base": "Delta Pro Ultra X; equipment only",
    "ultrax_shp3_bundle": "Delta Pro Ultra X + Smart Home Panel 3",
}


def _depth_paragraphs(*texts):
    return '\n'.join('<p>' + _esc(text) + '</p>' for text in texts)


def _depth_common_rows():
    d = rt._balcony_decision_support()
    rows = []
    for family in d['common_options']:
        for c in family['cases']:
            sid = c['scenario_id']
            track = ('Home storage' if family['family'].startswith('home_') else
                     'Portable storage; connection unproved' if sid == 'battery_power_0.8kw' else
                     'Added PV; qualification unproved')
            cells = (track, _DEPTH_LABELS[sid], _balcony_money(c['annual_savings_usd']),
                     _balcony_money(c['five_year']['total_affordable_initial_cost_usd']),
                     _balcony_money(c['ten_year']['total_affordable_initial_cost_usd']))
            rows.append(f'<tr data-scenario="{_esc(sid)}" data-baseline="post_behavior">' + ''.join('<td>'+_esc(v)+'</td>' for v in cells) + '</tr>')
    return '\n'.join(rows)


def _depth_evening_intro():
    e = rt._balcony_decision_support()['evening_output_example']
    return _depth_paragraphs(
        f"{e['output_kw']:g} kW is instantaneous power. Held from 4pm to midnight for {e['end_hour']-e['start_hour']:g} hours, it delivers {e['delivered_kwh']:g} kWh. "
        f"That includes {e['period_delivered_kwh']['on']:g} kWh in on-peak and {e['period_delivered_kwh']['off']:g} kWh in off-peak. "
        "The table gives illustrative frozen June 2026 tariff arithmetic.")


def _depth_evening_rows():
    e = rt._balcony_decision_support()['evening_output_example']
    rows = []
    for name, season in e['seasons'].items():
        values = (name.capitalize(),) + tuple(_balcony_money(season[key],2) for key in (
            'gross_avoided_import_usd_per_day','grid_charge_cost_usd_per_day','net_before_standby_and_equipment_usd_per_day'))
        rows.append('<tr>'+''.join('<td>'+_esc(v)+'</td>' for v in values)+'</tr>')
    return '\n'.join(rows)


def _depth_evening_limits():
    e = rt._balcony_decision_support()['evening_output_example']
    repeat = [_balcony_money(s['thirty_day_repeat']['net_before_standby_and_equipment_usd'],2) for s in e['seasons'].values()]
    return _depth_paragraphs(
        f"At {e['round_trip_efficiency']*100:g}% round-trip efficiency, grid recharge needs {e['grid_charge_kwh']:.2f} kWh. "
        f"A full starting battery needs {e['nominal_capacity_kwh']:.2f} kWh nominal capacity to reach {e['reserve_fraction']*100:g}% reserve under symmetric losses, before standby. "
        f"Thirty identical days repeat to {repeat[0]} in summer or {repeat[1]} in winter. This is not an annual forecast.",
        "These values need the full output to replace eligible grid imports in every quarter-hour, a full starting battery, and no outage. "
        "Charging power and duration feasibility remain unestablished. Monthly NEM netting, rooftop generation and home storage can reduce marginal value. "
        "Solar charging has forgone export value. This example excludes standby and all equipment/ownership costs; it proves no product eligibility.")


def _depth_evening():
    return (_depth_evening_intro() + '\n<table id="balcony-evening-results">\n'
            '<thead><tr><th>Season</th><th>Avoided purchases/day</th><th>Grid recharge/day</th><th>Net/day before standby and equipment</th></tr></thead>\n'
            '<tbody>\n' + _depth_evening_rows() + '\n</tbody>\n</table>\n' + _depth_evening_limits())


def _depth_purchase_rules():
    d = rt._balcony_decision_support()
    panels = [r for r in d['purchase_increments'] if r['before'] == 'battery_power_0.8kw']
    portable = [r for r in d['purchase_increments'] if r['before'] == 'home_27kwh_pv_0.8kw_credited']
    expansion = d['ownership_stresses']['dpu_battery_increment']
    return _depth_paragraphs(
        f"Price panels against their own saving. After matched {rt._balcony_row(panels[0]['before'])['batteries'][0]['discharge_kw']:g} kW portable output, "
        f"panels add {_balcony_money(panels[0]['annual_savings_usd'])}/yr with hypothetical extra credit or {_balcony_money(panels[1]['annual_savings_usd'])}/yr without it. "
        f"Their five-/ten-year extra-cost ceilings are {_balcony_money(panels[0]['five_year']['total_affordable_initial_cost_usd'])}/{_balcony_money(panels[0]['ten_year']['total_affordable_initial_cost_usd'])} "
        f"or {_balcony_money(panels[1]['five_year']['total_affordable_initial_cost_usd'])}/{_balcony_money(panels[1]['ten_year']['total_affordable_initial_cost_usd'])}, respectively. "
        "The supported export treatment and measured placement yield decide which panel budget applies. "
        "The AC-coupled proxy clips production before storage. These budgets exclude direct-DC clipping recovery. Exact equipment topology remains unproved.",
        f"After {rt._balcony_row(portable[0]['before'])['batteries'][0]['capacity_kwh']:g} kWh home storage plus credited PV, portable storage adds only "
        f"{_balcony_range([r['annual_savings_usd'] for r in portable])}/yr. Its own ten-year extra-cost ceiling is "
        f"{_balcony_range([r['ten_year']['total_affordable_initial_cost_usd'] for r in portable])}. "
        "Controller priority sets this range; it is not uncertainty bounds. Price each addition separately; standalone savings cannot be added.",
        f"A different increment can support more cost: the generic Delta Pro Ultra second battery adds {_balcony_money(expansion['annual_savings_usd'])}/yr. "
        f"Its advertised {_balcony_money(expansion['equipment_price_usd'])} increment leaves {_balcony_money(expansion['five_year']['remaining_allowance_usd'])} at five years "
        f"or {_balcony_money(expansion['ten_year']['remaining_allowance_usd'])} at ten years before other costs. "
        "This holds the same generic connection and power assumptions; it is not an installed expansion quote.")


def _depth_operating_rows():
    rows = []
    for c in rt._balcony_decision_support()['operating_cases']:
        sid = c['scenario_id']
        values = (_DEPTH_LABELS[sid], _balcony_money(c['annual_savings_usd']),
                  _balcony_money(c['seasons']['summer']['annual_savings_usd']),
                  _balcony_money(c['seasons']['winter']['annual_savings_usd']), f"{c['physical']['peak_import_kw']:.2f} kW")
        rows.append(f'<tr data-scenario="{_esc(sid)}">'+''.join('<td>'+_esc(v)+'</td>' for v in values)+'</tr>')
    return '\n'.join(rows)


def _depth_operating_prose():
    d = rt._balcony_decision_support()
    c = d['operating_cases'][0]; b = c['battery']; p = c['physical']
    combined = next(c for c in d['operating_cases'] if c['scenario_id'] == 'combined_0.8kw_zero_credit')
    cp = combined['physical']; cb = combined['battery']
    return _depth_paragraphs(
        f"The matched portable-only case buys {b['grid_charge_kwh']:,.0f} kWh of grid charging and diverts {b['existing_solar_charge_kwh']:,.0f} kWh of existing solar. "
        f"It delivers {b['served_kwh']:,.0f} kWh, loses {b['loss_kwh']:,.0f} kWh in conversion and uses {p['standby_kwh']:.1f} kWh in standby. "
        f"Annual imports are {p['imports_kwh']:,.0f} kWh and physical/credited exports are {p['exports_kwh']:,.0f} kWh. Delivered energy is not dollar saving.",
        f"With panels and no extra credit, the battery charges from {cb['grid_charge_kwh']:,.0f} grid, {cb['existing_solar_charge_kwh']:,.0f} existing-solar and "
        f"{cb['new_solar_charge_kwh']:,.0f} new-solar kWh. It delivers {cb['served_kwh']:,.0f} kWh, loses {cb['loss_kwh']:,.0f} kWh and uses {cp['standby_kwh']:.1f} kWh in standby. "
        f"Imports are {cp['imports_kwh']:,.0f} kWh; physical exports are {cp['exports_kwh']:,.0f} kWh, but only {cp['credited_exports_kwh']:,.0f} kWh receive modeled credit.",
        f"Cheap-hour charging does not cut the annual peak. The no-hardware peak is {p['baseline_peak_import_kw']:.2f} kW. "
        "The table shows each hardware peak and tariff-season contribution. Summer and winter together cover the observation year, not weather uncertainty.")


def _depth_equipment_rows():
    rows = []
    for c in rt._balcony_decision_support()['candidate_allowances']:
        b = c['modeled_batteries'][0]
        label = _DEPTH_CANDIDATES[c['id']]
        warranty = f"{c['warranty_years']:g} yr " + ('unit' if c['id'].startswith('stream') else 'inverter/batteries')
        values = (f"{b['capacity_kwh']:g} kWh; {b['discharge_kw']:g}/{b['charge_kw']:g} kW", _balcony_money(c['equipment_price_usd']),
                  _balcony_money(c['annual_savings_usd']), _balcony_money(c['five_year']['remaining_allowance_usd']),
                  _balcony_money(c['ten_year']['remaining_allowance_usd']), warranty, 'Available' if c['available'] else 'Unavailable')
        rows.append(f'<tr data-scenario="{_esc(c["scenario_id"])}" data-candidate="{_esc(c["id"])}"><td><a href="{_esc(c["source_url"])}">{_esc(label)}</a></td>' + ''.join('<td>'+_esc(v)+'</td>' for v in values)+'</tr>')
    return '\n'.join(rows)


def _depth_ownership_rows():
    d = rt._balcony_decision_support(); rows = []
    for candidate in ('stream_default','dp3_base','dpu_base','ultrax_base'):
        for annual, service in ((0,0),(50,500),(100,1500)):
            selected = [r for r in d['ownership_stresses']['cases'] if r['candidate_id']==candidate and r['assumptions']['annual_cost_usd']==annual and r['assumptions']['service_cost_usd']==service]
            if len(selected) != 2 or {r['years'] for r in selected} != {5,10}: raise SystemExit('report_blocks: balcony ownership ladder needs matched horizons')
            selected.sort(key=lambda r:r['years'])
            values = (_DEPTH_CANDIDATES[candidate], _balcony_money(annual), _balcony_money(service)) + tuple(_balcony_money(r['remaining_allowance_usd']) for r in selected)
            rows.append(f'<tr data-candidate="{candidate}">'+''.join('<td>'+_esc(v)+'</td>' for v in values)+'</tr>')
    return '\n'.join(rows)


def _depth_monthly_rows():
    cases = rt._balcony_decision_support()['operating_cases']
    rows = []
    for month in cases[0]['monthly_savings_usd']:
        rows.append('<tr><td>'+month+'</td>'+''.join('<td>'+_balcony_money(c['monthly_savings_usd'][month],2)+'</td>' for c in cases)+'</tr>')
    rows.append('<tr><th>Annual sum</th>'+''.join('<td>'+_balcony_money(c['annual_savings_usd'],2)+'</td>' for c in cases)+'</tr>')
    return '\n'.join(rows)


def _depth_sensitivity():
    d = rt._balcony_decision_support()
    saving = lambda sid: rt._balcony_row(sid)['annual_savings_usd']
    charging = [saving('battery_charging_'+kind) for kind in ('solar','grid','mixed')]
    reserve = [saving(sid) for sid in d['existing_sensitivity_ids']['reserve']]
    standby = [saving(sid) for sid in d['existing_sensitivity_ids']['standby']]
    study = rt._json(rt._BALCONY_ARTIFACT)
    nbt = study['present_2026_nbt_sensitivity']
    if nbt['economics'] is not None or nbt['payable_bill_established'] is not False or nbt['settlement_credit_limits_applied'] is not False:
        raise SystemExit('report_blocks: balcony NBT remains before settlement with no economics')
    def nbt_range(sid):
        values = [rt._balcony_number(c['scenarios'][sid]['annual_hardware_signed_ledger_delta_usd'], 'NBT signed delta') for c in nbt['cases'].values()]
        return _balcony_range(values)
    runtime_case = rt._balcony_row('battery_power_0.8kw')
    runtime = runtime_case['conditional_backup_runtime'][0][1]
    capacity = runtime_case['batteries'][0]['capacity_kwh']
    return _depth_paragraphs(
        f"For the separate generic 3.84 kWh/1.2 kW storage case, solar-only/grid-only/mixed charging saves "
        + '/'.join(_balcony_money(v) for v in charging) + "/yr. Grid-only beats this simple mixed policy because charging priorities and forgone export credits matter. "
        "It does not prove that grid-only is best for every controller or connection.",
        f"For that same standalone case, reserve from 10% to 30% changes saving from {_balcony_money(reserve[0])} to {_balcony_money(reserve[-1])}/yr. "
        f"Standby from 0 W to 30 W changes it from {_balcony_money(standby[0])} to {_balcony_money(standby[-1])}/yr. "
        "These are separate one-variable stresses; they do not describe combined downside or joint-system results. Measure actual losses and controls before a product quote.",
        f"Present-NBT repricing gives the matched portable-only row {nbt_range('battery_power_0.8kw')} and credited PV-only {nbt_range('pv_0.8kw_credited')} "
        "in annual signed ledger deltas before settlement. It reprices the whole account's unchanged flows, including existing rooftop exports. "
        "It does not optimize NBT dispatch or establish payable-bill savings, an NBT price ceiling, NPV or payback. NEM retained with no extra credit is a different case.",
        f"The {capacity:g} kWh portable runtime example gives {runtime['runtime_from_full_hours']:.2f} hours at a constant {runtime['constant_load_w']:g} W from full charge to reserve. "
        f"The preserved reserve supplies {runtime['runtime_from_preserved_reserve_hours']:.2f} hours if outage control releases it. "
        "It includes assumed standby and conversion loss with zero rooftop recharge. These are arithmetic runtimes, not measured appliance or household outage coverage. "
        "Supported outlets, transfer design and measured startup demand decide which loads work.")


def _balcony_chart_tables():
    """Static default source values keep the chart meaning available without JS."""
    from generate_report import balcony_chart_projection
    projection = balcony_chart_projection()
    selection = projection["home_storage"]["0"]
    keys = ("baseline", "panels", "portable", "combined")
    names = ("Starting equipment", "Add panels", "Add portable battery", "Add both")
    profiles = [projection["profiles"][selection["hourly"][k]]["seasons"]["S"] for k in keys]
    def table(id_, caption, headings, rows):
        return (f'<table id="{id_}"><caption>{_esc(caption)}</caption>\n<thead><tr>'
                + ''.join('<th scope="col">' + _esc(h) + '</th>' for h in headings)
                + '</tr></thead><tbody>\n'
                + '\n'.join('<tr>' + ''.join('<td>' + _esc(v) + '</td>' for v in row) + '</tr>' for row in rows)
                + '\n</tbody></table>')
    hourly = [[f"{hour}:00"] + [f"{p[key][hour]:.3f}" for p in profiles
              for key in ("imports_avg_kw", "physical_exports_avg_kw")] for hour in range(24)]
    purchases = selection["purchases"]["combined"]
    treatments = ("zero_credit", "credited")
    monthly = [[month["label"]] + [_balcony_money(purchases[t]["monthly_savings_usd"][i], 2)
               for t in treatments] for i, month in enumerate(projection["months"])]
    costs = []
    for i, point in enumerate(purchases["zero_credit"]["cost_sensitivities"]):
        values = [_balcony_money(point["installed_cost_usd"], 2)]
        for treatment in treatments:
            years = purchases[treatment]["cost_sensitivities"][i]["simple_payback_years"]
            values.append("No simple payback" if years is None else f"{years:.2f} yr")
        costs.append(values)
    return '\n'.join((
        table("balcony-hourly-source", "Hourly physical flows, average kW", ["Local hour"] +
              [name + " " + flow for name in names for flow in ("imports", "exports")], hourly),
        table("balcony-monthly-source", "Additional monthly bill savings, dollars", ["Month", "No extra-export credit", "Hypothetical extra-export credit"], monthly),
        table("balcony-payback-source", "Conditional simple payback, years", ["Assumed complete incremental cost", "No extra-export credit", "Hypothetical extra-export credit"], costs)))


DATA_BUILDERS = {
    "balcony-solar#3": _balcony_chart_tables,

    "balcony-solar#16": _depth_quote_priority,
    "balcony-solar#17": _depth_purchase_rules,
    "balcony-solar#18": _depth_evening_intro,
    "balcony-solar#19": _depth_evening_rows,
    "balcony-solar#20": _depth_evening_limits,
    "balcony-solar#21": _depth_operating_prose,
    "balcony-solar#22": _depth_operating_rows,
    "balcony-solar#23": _depth_equipment_rows,
    "balcony-solar#24": _depth_ownership_rows,
    "balcony-solar#25": _depth_monthly_rows,
    "balcony-solar#26": _depth_sensitivity,

    "s6#1": _depth_common_rows,
    "balcony-solar#1": lambda: _esc(rt._balcony_configuration(None)),
    "balcony-solar#4": _balcony_summary_rows,
    "balcony-solar#5": lambda: _esc(rt._balcony_generic_baselines(None)),
    "balcony-solar#6": _balcony_cost_rows,
    "balcony-solar#7": lambda: _esc(rt._balcony_economic_assumptions(None)),
    "balcony-solar#8": lambda: _esc(rt._balcony_stream_cases(None)),
    "balcony-solar#2": _balcony_options_rows,
    "balcony-solar#9": lambda: _balcony_summary_rows("1.2"),
    "balcony-solar#10": _balcony_order_rows,
    "balcony-solar#11": _balcony_component_rows,
    "balcony-solar#12": _balcony_ladder_headings,
    "balcony-solar#13": _balcony_ladder_rows,
    "balcony-solar#14": _balcony_capacity_basis,
    "balcony-solar#15": _balcony_capacity_rows,
    "top#1": _empty,
    "top#2": _empty,
    "top#3": _empty,
    "s15#1": _empty,
    "s15#4": _s15_metric_label,
    "s9#1": _empty,
    "s12#1": _empty,
    "s13#1": _empty,
    "s14#15": _empty,
    "s3#2": _s3_plan_rows,
    "s4#2": _s4_battery_plan_rows,
    "s6#3": _s6_arbitrage_rows,
    "s6#6": _s6_dispatch_rows,
    "s6#8": _s6_endurance_rows,
    "s11#2": _s11_year_rows,
    "s12#2": _s12_control_year_rows,
    "s13#4": _s13_escalation_rows,
    "s13#11": _s13_pricemap_rows,
}


def main():  # pragma: no cover - manual smoke test only
    blocks = validate_classification()
    counts = {"prose": 0, "data": 0, "human": 0}
    for b in blocks:
        counts[CLASSIFICATION[b.id]] += 1
    print(f"{len(blocks)} TODO blocks classified: {counts}")
    print(f"live KNOWN_GAPS tokens needing the same human treatment: {sorted(LIVE_GAP_TOKENS)}")


if __name__ == "__main__":  # pragma: no cover
    main()
