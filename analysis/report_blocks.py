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
  nearest preceding <h2 id="..."> (or "top" for the three blocks that sit
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
    - "human" when satisfying it truthfully needs a fact this repo's
      pipeline has never measured: a hardware price quote, incentive/rebate
      program status, or a genuinely new statistic (a measured peak, a
      regression coefficient, a degradation rate) with no TOKENS entry
      anywhere -- inventing one would violate CLAUDE.md section 0. This
      also covers every block that names a KNOWN_GAPS token by cheatsheet
      / report_tokens.py's own admission it cannot source.
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
  Three of report_tokens.py's five KNOWN_GAPS tokens (UTILITY_TOOL_BEST_
  PLAN_FIGURE, EXPANSION_PAYBACK_YEARS, ELECTRIFICATION_VERDICT_SHORT)
  appear in LIVE template markup -- static prose OUTSIDE any TODO block,
  not just inside worked examples -- so they always fail resolve_token()
  regardless of how any TODO block is classified. generate_report.py treats
  these exactly like a "human" block: LIVE_GAP_TOKENS names them so the
  orchestrator can require an explicit human-supplied override or refuse to
  publish, rather than crashing on the first resolve_token() call.
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
    missing_builders = {bid for bid, k in CLASSIFICATION.items()
                         if k == "data" and bid not in DATA_BUILDERS}
    if missing_builders:
        raise SystemExit(f"report_blocks: data-classified block(s) with no DATA_BUILDERS "
                          f"entry: {sorted(missing_builders)}")
    return blocks


# ---------------------------------------------------------------------------
# THE CLASSIFICATION MAP. 105 entries, one per parsed block id (verified by
# validate_classification() / test_report_blocks.py against a fresh parse).
# ---------------------------------------------------------------------------
CLASSIFICATION = {
    # --- header / meta / day-band / purpose (before s0) -------------------
    "top#1": "data",    # sources summary -- already fully rendered by {{DATA_SOURCES_SUMMARY}}
    "top#2": "data",    # day-band season caption -- already rendered by {{DAYBAND_SEASON_LABEL}}
    "top#3": "data",    # "adapt the question list" -- static list outside any TODO splice point

    # --- s0 Bottom line (7 items, all reference-voice prose with tokens) --
    "s0#1": "prose", "s0#2": "prose", "s0#3": "prose", "s0#4": "prose",
    "s0#5": "prose", "s0#6": "prose", "s0#7": "prose",

    # --- s1 The data ---------------------------------------------------
    "s1#1": "prose",

    # --- s2 Your solar system today --------------------------------------
    "s2#1": "prose",   # gateway/metering configuration -- DATA_SOURCES_SUMMARY/DETAIL cover it
    "s2#2": "prose",   # monitoring platforms in use
    "s2#3": "human",   # daily production range (mean/best/worst) -- no TOKENS entry
    "s2#4": "human",   # degradation / laggard-panel health signals -- no TOKENS entry
    "s2#5": "prose",   # key architectural fact -- SOLAR_COVERAGE_PCT/SELF_CONSUMED_SHARE etc.

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
    "s6#1": "human",   # framing incl. current incentive status -- INCENTIVE_STATUS is a KNOWN_GAP
    "s6#2": "data",    # one <tr> per alternative config -- battery_sim.json
    "s6#3": "prose",   # STORED_KWH_COST_SOLAR/GRID tokens exist
    "s6#4": "prose",   # deliberate exclusions, qualitative
    "s6#5": "data",    # one <tr> per additional dispatch policy -- battery_dispatch_policies.json
    "s6#6": "prose",   # simulation assumptions, cites DISPATCH_SCRIPT
    "s6#7": "data",    # one <tr> per config -- backup_endurance.json
    "s6#8": "human",   # "get quotes" pricing footnote -- hardware price quotes

    # --- s7 The decision: three packages ---------------------------------
    "s7#1": "prose", "s7#2": "prose", "s7#3": "prose", "s7#4": "prose",
    "s7#5": "prose", "s7#6": "prose", "s7#7": "prose", "s7#8": "prose",
    "s7#9": "prose", "s7#10": "prose", "s7#11": "prose",

    # --- s15 What to do Monday --------------------------------------------
    "s15#1": "data",   # content-only appendix intro -- authoring note, nothing to render
    "s15#2": "prose",  # CHEAP_WINDOW token exists
    "s15#3": "prose",  # pre-battery checklist, qualitative
    "s15#4": "data",   # the metric-name cell -- a fixed label for METRIC_NOW/METRIC_TARGET
    "s15#5": "prose",  # re-run triggers
    "s15#6": "prose",  # paired "When" cell

    # --- s8 Array upgrades -------------------------------------------
    "s8#1": "human",   # mixes MARGINAL_EXPORT_VALUE (gap), repowering health evidence,
                        # and measured-peak-vs-AC-ceiling clipping -- none sourced

    # --- s9 Deeper analyses ------------------------------------------
    "s9#1": "data",    # teaser -- already rendered by {{SEC9_TEASER}}
    "s9#2": "human",   # array degradation %/yr bracket -- no TOKENS entry
    "s9#3": "human",   # inverter clipping vs measured peak kW -- no TOKENS entry
    "s9#4": "human",   # weather-normalized cooling regression -- no TOKENS entry
    "s9#5": "human",   # EV report card (sessions/yr, kWh/yr, compliance %) -- no TOKENS entry
    "s9#6": "prose",   # wildcard plan -- WILDCARD_PLAN + BEST_PLAN tokens, qualitative verdict
    "s9#7": "prose",   # phantom honesty note, qualitative

    # --- s10 Bills, gas & electrification ----------------------------
    "s10#1": "prose",  # bill facts -- narrative rows, not a uniform artifact loop
    "s10#2": "prose",  # ACTUAL_ANNUAL_BILL / MODELED_ANNUAL_AT_CURRENT_RATES tokens exist
    "s10#3": "prose",  # ACTUAL_ANNUAL_GAS_BILL token exists
    "s10#4": "human",  # electrification math -- exactly ELECTRIFICATION_VERDICT_SHORT's gap
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
    "s12#5": "human",  # optimal cadence/month + shop-below price threshold -- no TOKENS entry

    # --- s13 Carbon, NEM, escalation & price map -------------------------
    "s13#1": "data",   # teaser -- already rendered by {{SEC13_TEASER}}
    "s13#2": "prose",  # carbon narrative -- CARBON_SAMPLE_DESCRIPTION token
    "s13#3": "prose",  # NEM re-bill narrative -- NEM_GRANDFATHER_VALUE_RANGE token
    "s13#4": "data",   # one <tr> per escalation scenario -- battery_dispatch_policies.json
    "s13#5": "prose",  # base-case assumptions, qualitative
    "s13#6": "prose",  # ladder-vs-spread framing -- ESCALATION_HISTORICAL token
    "s13#7": "prose",  # spread chart caption, qualitative
    "s13#8": "human",  # per-season escalation trend/CI/r^2 -- a new regression, no token
    "s13#9": "human",  # battery re-run on the measured spread -- a new payback/NPV, no token
    "s13#10": "prose", # scope caveat, qualitative
    "s13#11": "human", # phantom baseload decomposition (median/p10/p90) -- no TOKENS entry
    "s13#12": "data",  # one <tr> per season x TOU-period -- analysis/rates.py
    "s13#13": "prose", # rate-table provenance, qualitative

    # --- s14 Methodology -------------------------------------------------
    "s14#1": "prose", "s14#2": "prose", "s14#3": "prose", "s14#4": "prose",
    "s14#5": "prose", "s14#6": "prose", "s14#7": "prose", "s14#8": "prose",
    "s14#9": "prose", "s14#10": "prose", "s14#11": "prose", "s14#12": "prose",
    "s14#13": "prose", "s14#14": "prose",
    "s14#15": "data",  # data sources full inventory -- already rendered by {{DATA_SOURCES_DETAIL}}
}

HUMAN_REASONS = {
    "s2#3": "daily production range (mean/best/worst) has no report_tokens.py entry -- "
            "a new measured statistic, not a token substitution",
    "s2#4": "degradation / laggard-panel health-check results have no report_tokens.py "
            "entry -- asserting a specific check outcome with no source would invent a fact",
    "s6#1": "references {{INCENTIVE_STATUS}}, a KNOWN_GAPS token in report_tokens.py: "
            "federal ITC / CA SGIP program status is a live research fact this pipeline "
            "has never measured (issue #39's own stated default: incentive status is human)",
    "s6#8": "asks the reader to 'get quotes' -- a hardware price quote (issue #39's own "
            "stated default: hardware prices as quoted are human)",
    "s8#1": "mixes MARGINAL_EXPORT_VALUE (a KNOWN_GAPS token), repowering health evidence, "
            "and a measured worst-case peak kW vs the AC ceiling -- none of the three has a "
            "report_tokens.py entry",
    "s9#2": "a multi-year degradation %/yr bracket is a new statistic with no "
            "report_tokens.py entry",
    "s9#3": "the measured worst-case peak power (to compare against AC_CEILING_KW) has no "
            "report_tokens.py entry -- only the ceiling itself is tokenized, not the "
            "measured peak",
    "s9#4": "a weather-normalized cooling regression (base load, kWh/CDD, $ sensitivity) "
            "is a new statistical result with no report_tokens.py entry",
    "s9#5": "the EV report card asks for sessions/yr, kWh/yr, and compliance % broken out "
            "by window; only the two compliance-savings dollar figures are tokenized "
            "(EV_FIX_SAVINGS_100/80) -- the session counts and percentages have no entry",
    "s10#4": "this is exactly report_tokens.py's ELECTRIFICATION_VERDICT_SHORT gap: "
             "heat-pump space heating has a committed install-cost basis since issue "
             "#1 (data/heat_pump_conversion.json), HPWH still does not, so an "
             "appliance-by-appliance ROI verdict needing both still cannot be made "
             "honestly from what is committed",
    "s12#5": "an optimal single-cleaning month and a shop-below price threshold are new "
             "figures with no report_tokens.py entry",
    "s13#8": "a per-season escalation trend (%/yr, 95% CI, r-squared, rate-level count) is "
             "a new regression result computed on the fly, not backed by a committed token "
             "-- and tou_spread.py's own stated rule allows the honest answer to be 'not "
             "determined', which a token substitution cannot express",
    "s13#9": "a battery re-run on the MEASURED spread (vs the uniform escalation ladder) "
             "is a new payback/NPV figure with no report_tokens.py entry",
    "s13#11": "phantom baseload's median/p10/p90 kW and kWh/yr and $/yr breakdown has no "
              "standalone report_tokens.py entry (only bundled inside SEC9_TEASER's fixed "
              "sentence, not exposed as reusable tokens)",
}

# Three of report_tokens.py's five KNOWN_GAPS tokens appear in LIVE template
# markup (outside any TODO comment), so they fail resolve_token() regardless
# of any TODO block's classification. Computed from the template itself, not
# hardcoded, so a template edit that removes one is caught rather than silently
# leaving a stale entry here.
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
    others = sorted((p for p in plans if p != current_plan),
                     key=lambda p: plans[p]["no_battery"])
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
        out.append(f"<tr><td>{yr} (no cleaning)</td><td>{pre_m:.1f}</td>"
                   f"<td>{post_m:.1f}</td><td>{round(post_m / pre_m, 3)}</td></tr>")
    return "\n".join(out)


def _s13_escalation_rows():
    ladder = rt._json("battery_dispatch_policies.json")["escalation_greedy_pw3_post_behavior"]
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


DATA_BUILDERS = {
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
    "s6#2": _s6_arbitrage_rows,
    "s6#5": _s6_dispatch_rows,
    "s6#7": _s6_endurance_rows,
    "s11#2": _s11_year_rows,
    "s12#2": _s12_control_year_rows,
    "s13#4": _s13_escalation_rows,
    "s13#12": _s13_pricemap_rows,
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
