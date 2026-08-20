#!/usr/bin/env python3
"""The mechanical half of report generation (issue #39, part 1): resolve every
{{TOKEN}} in report-template.html to a real value, with no model involved.

WHAT THIS DOES
  1. Parses report-template.html itself (never a hand-transcribed list) to find
     every token, split into LIVE (outside any <!-- --> comment) and
     COMMENT-ONLY (appears only inside a <!-- TODO ... --> block, as a worked
     example of a value a later LLM prose pass may reference). Two comment-only
     strings are generic instructional text, not real tokens, and are excluded:
     TOKEN and DOUBLE_BRACE_TOKENS.
  2. Declares TOKENS: a committed, explicit map of token name -> source record
     (which of data_json / data_csv / rates_module / household_yaml /
     cited_constant / derived, the exact path/key/column, and a format spec).
  3. Resolves the whole map against the real committed archive: data/*.json,
     data/*.csv, analysis/rates.py, and private/household.yaml (public-ok
     fields only, enforced at every resolution -- see _hh_value below).

FAIL-CLOSED, WITH ONE NAMED EXCEPTION CLASS
  Every token that CAN be sourced from something committed in this repo IS
  sourced, and resolve_token() raises SystemExit naming the token and what was
  tried if its source ever goes missing. A small number of live template
  tokens have NO committed source anywhere in this repo today (not in data/,
  not in household.yaml, not in analysis/*.py, not in TECHNICAL.md) -- these
  are listed in KNOWN_GAPS with the reason, kept deliberately small, and their
  TOKENS entries have kind="gap": calling resolve_token on one of them raises
  SystemExit naming the gap and the reason, which IS the fail-closed behavior
  (it never silently returns an empty or invented string). resolve_all(...)
  defaults to raising if the gap set ever changes shape unexpectedly, and
  omits them from its returned map unless include_gaps=True is passed.

PRIVACY
  Every household_yaml-sourced token is checked against
  analysis/privacy_tiers.py's cheatsheet-derived tier map at RESOLUTION TIME,
  not just at authoring time: a path with no tier at all, or a tier other than
  public-ok, raises SystemExit rather than returning the value. This is the
  runtime assertion CLAUDE.md section 4 requires -- it protects a future
  token addition from silently reaching a private-only or secret field.

Run standalone to print every resolved token:
  ./.venv/bin/python analysis/report_tokens.py
"""
import csv
import datetime as dt
import json
import pathlib
import re
import sys


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:  # pragma: no cover - reachable only outside a checkout
                break
            p = p.parent
    raise SystemExit(  # pragma: no cover - the module's own path always resolves in-repo
        "repo root not found: no ancestor of the CWD or of this "
        "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
TEMPLATE = ROOT / "report-template.html"
sys.path.insert(0, str(ROOT / "analysis"))
import household as hh          # noqa: E402
import privacy_tiers as pt      # noqa: E402
import rates as R               # noqa: E402
# Constants only (its two committed correlation bounds), never its generator
# entry points -- see MIN_AGREEMENT_CORRELATION below. Importing beats
# copying the numbers here, which would silently drift from the gate they
# are supposed to mirror.
import threeway_production_validation as tpv   # noqa: E402
# Constants only, same reason: tou_audit.ROUNDING_PER_BUCKET is the whole-kWh
# rounding bound S1_VERDICT's sentence quotes, and importing it beats copying
# the number here where it would drift from the artifact it describes.
import tou_audit as ta                          # noqa: E402


# ---------------------------------------------------------------------------
# 1. Parse the template for its own token inventory (never hand-transcribed).
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"\{\{([A-Z0-9_]*)\}\}")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
GENERIC_META_TOKENS = {"TOKEN", "DOUBLE_BRACE_TOKENS"}


def template_tokens(html=None):
    """(live, comment_only) token-name sets from report-template.html.

    live = appears at least once OUTSIDE any <!-- --> span (static markup,
    table cells, JS chart config -- resolved and rendered directly).
    comment_only = appears ONLY inside <!-- --> spans (worked examples inside
    TODO blocks for a later prose-generation pass to reference), minus the two
    generic instructional strings TOKEN and DOUBLE_BRACE_TOKENS.
    """
    text = TEMPLATE.read_text() if html is None else html
    comment_spans = [m.span() for m in _COMMENT_RE.finditer(text)]

    def in_comment(pos):
        return any(s <= pos < e for s, e in comment_spans)

    live, comment_only = set(), set()
    for m in _TOKEN_RE.finditer(text):
        name = m.group(1)
        if not name:
            continue
        (comment_only if in_comment(m.start()) else live).add(name)
    comment_only -= live
    comment_only -= GENERIC_META_TOKENS
    return live, comment_only


# ---------------------------------------------------------------------------
# Cached loaders for the committed archive.
# ---------------------------------------------------------------------------
_json_cache = {}

# WHICH ARTIFACTS THE TOKEN CURRENTLY RESOLVING HAS ACTUALLY READ.
#
# Recorded at the loader, never declared. TOKENS' `sources` list is prose:
# nothing consumes it and nothing checks it, which is why the poison sweep in
# test_report_tokens.py discovers a token's artifacts by wrapping this loader
# instead of reading that list. A guard that trusted the declaration would be
# defeated by the same omission it exists to catch -- a new token that reads
# an artifact and forgets to name it.
#
# resolve_token() opens and closes the window (one clear per resolution), and
# _forbid_unearned_annual_unit() reads it. The set is recorded BEFORE the
# cache test on purpose: a second reader of an already-loaded artifact is
# still a reader of it.
_reads = set()


def _json(name):
    _reads.add(name)
    if name not in _json_cache:
        path = DATA / name
        if not path.is_file():
            raise SystemExit(f"report_tokens: missing committed data file data/{name}")
        _json_cache[name] = json.loads(path.read_text())
    return _json_cache[name]


_csv_cache = {}


def _csv_rows(name):
    if name not in _csv_cache:
        path = DATA / name
        if not path.is_file():
            raise SystemExit(f"report_tokens: missing committed data file data/{name}")
        with open(path, newline="") as f:
            _csv_cache[name] = list(csv.DictReader(f))
    return _csv_cache[name]


def _dig(obj, path):
    node = obj
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            raise SystemExit(f"report_tokens: path {path!r} not found in the source "
                              f"data (stuck at {key!r})")
    return node


def _rates_src():
    return pathlib.Path(R.__file__).read_text()


# ---------------------------------------------------------------------------
# household.yaml access, gated on the public-ok tier at every call.
# ---------------------------------------------------------------------------
_TIERS_CACHE = None


def _hh_tiers():
    global _TIERS_CACHE
    if _TIERS_CACHE is None:
        _TIERS_CACHE = pt.path_tiers()
    return _TIERS_CACHE


def _hh_node():
    """The raw parsed private/household.yaml dict, via household.py's own
    fail-closed loader (so a missing file/key fails exactly the way every
    other analysis script's household access fails)."""
    hh.get("household.utility")  # forces the load / the standard fail-closed message
    return hh._load()


def _hh_value(path):
    """[values] at a cheatsheet-contract path (privacy_tiers.resolve's `[]`
    list notation), after asserting the path is tiered public-ok.

    This is the runtime privacy gate CLAUDE.md section 4 requires: a token
    whose spec ever names a path this cheatsheet has not tiered, or has tiered
    private-only/secret, fails closed here -- it is never silently read.
    """
    tiers = _hh_tiers()
    tier = tiers.get(path)
    if tier is None:
        raise SystemExit(
            f"report_tokens: refusing to read household.yaml path {path!r}: it is "
            "not a field DATA-SOURCES-CHEATSHEET.md tiers at all (privacy_tiers."
            "path_tiers() has no entry for it) -- an untiered household key must "
            "never be published")
    if tier != "public-ok":
        raise SystemExit(
            f"report_tokens: refusing to read household.yaml path {path!r}: its "
            f"cheatsheet tier is {tier!r}, not public-ok -- CLAUDE.md section 4 "
            "forbids putting a private-only or secret household answer into any "
            "committed artifact, and a rendered report token is one")
    values, found = pt.resolve(_hh_node(), path)
    if not found:
        raise SystemExit(f"report_tokens: household.yaml has no value at path {path!r} "
                          "(private/household.yaml is missing this key)")
    return values


def hh1(path):
    """The single scalar value at a household.yaml path (asserts exactly one)."""
    values = _hh_value(path)
    if len(values) != 1:
        raise SystemExit(f"report_tokens: household.yaml path {path!r} resolved to "
                          f"{len(values)} values, expected exactly 1")
    return values[0]


# ---------------------------------------------------------------------------
# Format specs. Numeric leaf tokens (data_json/data_csv/rates_module/
# household_yaml) go through one of these by name. "derived" and
# "cited_constant" tokens usually build their own final string and pass
# fmt=None (str() passthrough).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TWO PRECONDITIONS EVERY FORMATTER BELOW ENFORCES ITSELF (issue #131, review
# round 5, part B).
#
# A formatter is the last code a figure passes through before a reader sees
# it, and it is the one place that cannot be reached by a route that skips the
# check. Four review rounds of adding a guard at whichever call site that
# round's reader happened to open left "$nan", "$inf", "-$nan", "$-0" and
# "$-1,234" all still reachable, each from a different token. So the two
# preconditions are stated once, here:
#
#   FINITE. Every formatter is a number-to-prose function and none of them has
#   a rendering for a non-number. `f"${nan:,.0f}"` does not fail -- it
#   cheerfully produces "$nan" -- so the check has to be explicit.
#
#   NON-NEGATIVE, for the CURRENCY formatters that carry no sign. _usd0 and
#   its relatives are the formatters a value uses when its sign is fixed by
#   CONSTRUCTION: a purchase price, a billed total, a published rate. Handed a
#   negative they print the minus INSIDE the sigil ("$-1,234"), which is the
#   defect this module has now corrected at seven exits over four rounds. A
#   figure whose sign the artifact decides belongs in _usd0_signed below --
#   which renders every non-negative value identically, so moving a token
#   across costs nothing -- and a structurally non-negative figure that
#   arrives negative is a broken artifact, which is a refusal.
#
# -0.0 is NOT negative for either purpose: it is zero, it prints "$0", and the
# `abs()` below is what stops `f"{-0.0:,.0f}"` rendering "-0".
# ---------------------------------------------------------------------------
def _unsigned_currency(fmt, v):
    """The shared precondition for the sign-free currency formatters."""
    if not _finite(v):
        raise SystemExit(f"report_tokens: {fmt} cannot render {v!r} -- it is not a "
                          "finite number")
    if v < 0:
        raise SystemExit(
            f"report_tokens: {fmt} refuses to render {v!r}, a NEGATIVE amount, because "
            "it would print the minus inside the dollar sigil. A figure whose sign the "
            "artifact decides belongs in usd0_signed; a figure that is non-negative by "
            "construction and arrived negative means the artifact behind it is wrong")
    return abs(v)


def _numeric(fmt, v):
    """The shared precondition for the sign-free NON-currency formatters. No
    sign test: a negative count, share or delta prints its own minus with
    nothing to sit inside, so only finiteness is at stake."""
    if not _finite(v):
        raise SystemExit(f"report_tokens: {fmt} cannot render {v!r} -- it is not a "
                          "finite number")
    return v


def _usd0(v):
    return f"${_unsigned_currency('usd0', v):,.0f}"


def _usd0_tilde(v):
    return f"~${_unsigned_currency('usd0_tilde', v):,.0f}"


def _usd2(v):
    return f"${_unsigned_currency('usd2', v):,.2f}"


def _usd3(v):
    return f"${_unsigned_currency('usd3', v):,.3f}"


def _num0(v):
    return f"{_numeric('num0', v):,.0f}"


def _num1(v):
    return f"{_numeric('num1', v):,.1f}"


def _num2(v):
    return f"{_numeric('num2', v):,.2f}"


def _pct0(v):
    return f"{round(_numeric('pct0', v))}%"


def _pct1(v):
    return f"{_numeric('pct1', v):.1f}%"


def _yr1(v):
    return f"{_numeric('yr1', v):.1f} yr"


def _cents1(v):
    return f"{_numeric('cents1', v) * 100:.1f}¢"


def _no_sign_for_a_non_number(fmt, v):
    """Refuse to put a SIGN on something that is not a number.

    The three formatters below exist to decide where a minus goes. A nan has
    no sign to place and an infinity has no magnitude to place it in front of,
    so every one of them fell through its own sign tests and rendered the
    Python repr with a sigil glued to it: `_usd0_signed(nan)` read "-$nan"
    (the `v >= 0` test is False for nan, so the NEGATIVE branch ran and
    MANUFACTURED a minus on a non-number), and `_usd0_plus(nan)` fell past
    both `>` and `<` and published "$0" -- a nan NPV rendered as an exact zero
    gain (issue #131 review round 5, findings 1, 2 and 8).

    Raised as SystemExit, not returned as a string, because resolve_token
    formats inside its own try and re-raises with the token's name attached:
    the refusal names the token whichever of the two ways the formatter was
    reached (declared as a token's `fmt`, or called inline by a derived
    formula, as NPV_AT_HISTORICAL_ESCALATION calls _usd0_plus)."""
    raise SystemExit(
        f"report_tokens: {fmt} cannot render {v!r} -- a sign belongs to a finite "
        "number, and this is not one")


def _usd0_signed(v):
    """Whole dollars with the sign OUTSIDE the sigil: "-$500", never "$-500".

    THE RULE THIS FORMATTER ENFORCES, and which every currency site in this
    module is now held to: a value whose sign is not fixed by CONSTRUCTION --
    a difference, a modeled saving, a margin, an NPV -- formats through one of
    the *_signed formatters. A value that is structurally non-negative -- a
    purchase price, a billed total, a kWh count -- keeps the plain one, and
    where such a value can still reach a REFUSAL MESSAGE with the wrong sign
    (a cost difference the refusal fires on precisely because it went
    non-positive) that message formats through here too.

    This defect has now been fixed at five exits across three review rounds
    -- two verdict clauses (round 2, finding 5), the section 7 expansion-cost
    refusal and the battery's own marginal-saving token (round 4, findings 8
    and 10) -- which is why the rule is written down here rather than left as
    a habit at each site.

    NEGATIVE ZERO IS NOT NEGATIVE. The test used to be `v >= 0`, which is True
    for -0.0, so a difference that lands on the negative side of an exact zero
    -- `0.0 - 0.0` under a subtraction whose left operand was itself -0.0, or
    any `round()` of a tiny loss -- took the non-negative branch and printed
    `_usd0(-0.0)`, which Python renders "$-0": the minus back inside the
    sigil, in the one formatter written to keep it out (issue #131 review
    round 5, finding 7). Only a value that is STRICTLY below zero gets the
    leading minus, and every other finite value formats its own magnitude, so
    -0.0 and +0.0 both read "$0"."""
    if not _finite(v):
        _no_sign_for_a_non_number("usd0_signed", v)
    return f"-{_usd0(-v)}" if v < 0 else _usd0(abs(v))


def _usd0_tilde_signed(v):
    """_usd0_tilde for a signed quantity: "~$4,900", "~-$500". Identical
    output at every non-negative value, so an approximate SAVING or VALUE can
    use it without changing what a positive one renders as."""
    if not _finite(v):
        _no_sign_for_a_non_number("usd0_tilde_signed", v)
    return f"~{_usd0_signed(v)}"


def _usd0_plus(v):
    """Whole dollars with an EXPLICIT sign on both directions: "+$8,656",
    "-$8,656", "$0". For a figure the report presents as a signed gain (an
    NPV), where the leading "+" is part of the reading rather than
    decoration -- it used to be a hardcoded "+" in front of an unsigned
    format, which rendered a negative NPV as "+$-3,000".

    The trailing case is `v == 0` and NOT a bare else. Written as an else it
    was the branch a nan reached -- `nan > 0` and `nan < 0` are both False --
    and it published an indeterminate net present value as the exact,
    confident figure "$0" (issue #131 review round 5, finding 8). A zero gain
    and an unknown one are opposite readings for someone deciding whether to
    buy, so the third state fails closed instead. Checked up front as well as
    in the trailing branch, so an INFINITY -- which does pass `v > 0` -- is
    refused by this formatter's own name rather than by _usd0's."""
    if not _finite(v):
        _no_sign_for_a_non_number("usd0_plus", v)
    if v > 0:
        return f"+{_usd0(v)}"
    if v < 0:
        return f"-{_usd0(-v)}"
    return _usd0(0)


def _raw(v):
    return str(v)


def _year(v):
    return f"{int(_numeric('year', v)):d}"


FORMATTERS = {
    None: _raw, "raw": _raw,
    "usd0": _usd0, "usd0_tilde": _usd0_tilde, "usd0_signed": _usd0_signed,
    "usd0_tilde_signed": _usd0_tilde_signed, "usd0_plus": _usd0_plus,
    "usd2": _usd2, "usd3": _usd3,
    "num0": _num0, "num1": _num1, "num2": _num2, "year": _year,
    "pct0": _pct0, "pct1": _pct1, "yr1": _yr1, "cents1": _cents1,
}

# Every format spec above that means "this value is a NUMBER, render it as
# one". A token declared with any of them is asserting the artifact field
# behind it is a finite quantity, and resolve_token holds it to that before
# the formatter ever sees the value -- see _NON_NUMERIC_FMTS' use there.
# Maintained by SUBTRACTION from FORMATTERS, so a format spec added later is
# numeric until someone says otherwise: the failure mode this closes is a new
# leaf token quietly publishing "$nan", and the safe default for an unknown
# formatter is to be checked rather than skipped.
_NON_NUMERIC_FMTS = frozenset({None, "raw"})


# ---------------------------------------------------------------------------
# THE THREE STATES A DERIVED CLAUSE CAN REACH (issue #131, review round 2).
#
# Every clause in this module that makes a QUALITATIVE claim about this
# household -- "the rate plan is right", "the EV charges overnight", "the
# battery repays its own cost", "the CCA costs more" -- resolves to exactly
# ONE of three states, and the state is NAMED at the call site rather than
# left implicit in a chain of nested conditionals:
#
#   SUPPORTED           the artifacts establish the claim. Render it.
#
#   SUPPORTED_OPPOSITE  the artifacts establish the CONTRARY claim. Render
#                       that. A household whose answer merely differs is an
#                       ordinary household with an ordinary report to
#                       generate; refusing there withholds fourteen other
#                       sections over one clause that simply reads the other
#                       way. (Review round one's defect was using refusal
#                       for this state.)
#
#   NOT_DETERMINED      the artifacts do not settle it: zero or missing
#                       observations, a degenerate/zero magnitude the claim's
#                       own wording presupposes, mixed or contradictory signs
#                       across artifacts that have to agree, or a non-finite
#                       quotient. Fail closed with a SystemExit naming the
#                       token AND the quantity that was indeterminate.
#                       CLAUDE.md section 0 is explicit that "not determined"
#                       is a legitimate and required answer -- this is the
#                       state that carries it. It is NOT a return to round
#                       one's defect: round one refused where the artifacts
#                       DID settle the question and merely settled it the
#                       other way.
#
# Making a clause binary -- render the confident claim or render the
# confident opposite -- is what let degenerate inputs select a confident
# branch: zero observations published a habit, a modeled loss published as
# "no saving", a zero excluded effect published as "a smaller effect". The
# discipline, not any one of those patches, is the fix.
#
# _claim() collapses the two RENDERABLE states to the boolean a caller
# branches on, and raises on the third, so each call site reads as one line
# naming its own subject.
# ---------------------------------------------------------------------------
class _ClaimState:
    """One of the three answers a derived clause can reach. The three
    instances below are singletons, compared with `is`; the name is what the
    code and the failure message read as."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):            # pragma: no cover - debugging aid only
        return f"<{self.name}>"


SUPPORTED = _ClaimState("SUPPORTED")
SUPPORTED_OPPOSITE = _ClaimState("SUPPORTED-OPPOSITE")
NOT_DETERMINED = _ClaimState("NOT-DETERMINED")


def _finite(*values):
    """True only when every value is a real, finite number.

    Non-finite quotients are one of NOT_DETERMINED's named triggers, and a
    payback of inf/nan compares as an ordinary float in every `>` and `min()`
    a branch might use -- so it is tested for by name rather than left to
    arithmetic. bool is excluded deliberately: True is not a magnitude."""
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if v != v or v in (float("inf"), float("-inf")):
            return False
    return True


# There is deliberately NO _sign() helper here. Its only two callers were the
# two cross-artifact comparisons issue #131's round four corrected -- a
# rounding pair and a two-scenario pair, neither of which a sign test could
# ever have settled -- and a bare three-way sign sitting in this module is an
# invitation to write that comparison a third time. A clause that branches on
# ONE magnitude goes through _sign_claim below; a pair of fields is compared
# only after its relationship has been traced to the generator.


def _claim(token, subject, state, detail,
           unsettled="the committed artifacts do not settle it"):
    """The boolean a two-way clause renders on, or a named refusal.

    Returns True for SUPPORTED and False for SUPPORTED_OPPOSITE. On
    NOT_DETERMINED it raises SystemExit naming `token` and `subject` -- the
    exact quantity left indeterminate -- with `detail` carrying the artifact
    values that made it so, and `unsettled` saying why they do not settle it
    (overridden only where the obstacle is not the artifacts themselves; see
    _best_plan)."""
    if state is NOT_DETERMINED:
        raise SystemExit(
            f"report_tokens: {token} cannot say {subject} -- {unsettled}: {detail}")
    if state is SUPPORTED:
        return True
    if state is SUPPORTED_OPPOSITE:
        return False
    raise SystemExit(   # pragma: no cover - unreachable while the three are the only states
        f"report_tokens: {token} reached an unknown claim state {state!r}")


def _require_finite(token, subject, **values):
    """NOT_DETERMINED for any non-finite input to a COMPARISON.

    Every branch below a comparison is written for real numbers, and a nan
    satisfies none of them: `nan > 0`, `nan < 0` and `nan == x` are all
    False, so it falls through every test and lands in whichever branch
    happens to be written last -- a CONFIDENT clause selected by a degenerate
    input, which is the shape this whole review round is about. An infinity
    is the same problem one step along: it wins every `>` it meets.

    Named per keyword so the refusal says which quantity was the bad one."""
    bad = {k: v for k, v in values.items() if not _finite(v)}
    _claim(token, subject, SUPPORTED if not bad else NOT_DETERMINED,
           ", ".join(f"{k} is {v!r}" for k, v in (bad or values).items()))


# ---------------------------------------------------------------------------
# THE SAME TWO PRECONDITIONS, FOR A FORMULA THAT WRITES ITS OWN PROSE
# (issue #131, review round 5, part B).
#
# The formatters above hold every LEAF token to "finite, and not negative
# behind a sigil". A `derived` formula that builds its own sentence with an
# f-string never reaches them -- `f"{peak_kw:.1f} kW"` and `f"${low:,.0f}"`
# format the value themselves -- and that is exactly the population round
# four's structural probe could not see, because it inspected declarations
# (issue #131 review round 5, finding 9). Twenty-one tokens in this module
# were in it, every one able to publish "nan kW", "inf kg CO2/MWh" or
# "$-1,234" out of a single bad artifact field.
#
# So a formula that interpolates a number states it here first. Two helpers
# rather than one, because a figure behind a DOLLAR SIGIL has the extra
# precondition and a kWh count does not, and collapsing them would either
# forbid a legitimate negative delta or wave a negative dollar figure through.
# Both RETURN their values in declaration order, so the check reads as part of
# the formula rather than as a line that can be deleted without the formula
# noticing.
# ---------------------------------------------------------------------------
def _figures(token, subject, **values):
    """The numbers a prose formula is about to interpolate: finite, in order."""
    _require_finite(token, subject, **values)
    return tuple(values.values())


def _amounts(token, subject, **values):
    """_figures for numbers a sentence prints behind a "$" it writes itself.

    Also NOT NEGATIVE, for _usd0's reason one level up: `f"${-1234:,.0f}"`
    puts the minus inside the sigil. A formula whose figure really can go
    either way calls _usd0_signed on it and passes it through _figures
    instead; this is for the ones whose own wording ("worth $X", "risks the
    $X-$Y grandfathering") presupposes an amount at or above zero, where a
    negative means the artifact behind the sentence is wrong.

    Returned through abs(), for _usd0_signed's negative-zero reason (issue
    #131 review round 5, finding 7, one level up): -0.0 passes the `< 0` test
    below because it IS zero, and then `f"${-0.0:,.2f}"` renders "$-0.00" --
    the minus back inside the sigil, from a value the check correctly let
    through. abs() is the identity on every other value the check permits."""
    return _not_below_zero(
        token, subject,
        "this sentence prints it behind a dollar sigil, where a negative renders "
        "the minus inside the sigil",
        **values)


def _quantities(token, subject, **values):
    """_amounts' test, for a figure that carries no sigil but cannot be negative.

    A daily production total, an inverter's measured peak power, a
    cooling-degree-day coefficient's annual kWh: each is a magnitude whose
    sign is fixed by what it MEASURES, not by a formatter's punctuation. A
    negative one renders perfectly well ("-1,234.5 kW") and is exactly the
    reading the poison sweep's output contract cannot catch, because nothing
    about the string is malformed -- only the physics is. So the refusal is
    stated here rather than left to the formatter.

    Shares its ladder with _amounts rather than copying it, for the reason
    _sign_claim gives one section up: two byte-identical copies of a check is
    how the two of them drift. What is NOT shared is the REASON, which is the
    half a maintainer reads in the failure message -- _amounts refuses because
    of where the minus lands, this refuses because the quantity has no negative
    reading at all."""
    return _not_below_zero(
        token, subject,
        "this is a magnitude with no negative reading, so a value below zero "
        "means the artifact behind it is wrong",
        **values)


def _not_below_zero(token, subject, why, **values):
    """The shared finite-and-not-negative ladder behind _amounts/_quantities."""
    _figures(token, subject, **values)
    bad = {k: v for k, v in values.items() if v < 0}
    _claim(token, subject, SUPPORTED if not bad else NOT_DETERMINED,
           ", ".join(f"{k} is {v!r}" for k, v in bad.items()) + " -- " + why)
    return tuple(abs(v) for v in values.values())


# ---------------------------------------------------------------------------
# BEFORE COMPARING TWO ARTIFACT FIELDS, ESTABLISH THEIR RELATIONSHIP
# (issue #131, review round 4).
#
# Two of round three's ten findings were guards that compared a pair of
# committed fields as though a disagreement between them were a contradiction,
# when the generators say it is nothing of the kind. Both aborted the WHOLE
# report for an ordinary household. So no comparison in this module may be
# written until its two fields have been traced to the generator that writes
# them, and the relationship recorded at the comparison site as exactly one
# of three:
#
#   SAME QUANTITY, INDEPENDENTLY COMPUTED
#       Two engines, two instruments, or a constant and the prose an artifact
#       states it in. A disagreement IS a contradiction and NOT_DETERMINED is
#       the right answer. Compare directly.
#       (e.g. _whole_kwh_rounding_bound: analysis/tou_audit.py's
#       ROUNDING_PER_BUCKET against the bound tou_audit_summary.json's
#       tolerance.basis states in words.)
#
#   ONE DERIVED FROM THE OTHER
#       Rounded, summed, rescaled, divided into a cost. Only a discrepancy
#       BEYOND the derivation's own tolerance means anything, so the
#       comparison carries that tolerance and is never exact and never a
#       bare sign test. _require_derived below is this case.
#       (e.g. packages.LOW.savings_yr is literally round() of
#       behavior_rebuild's scenarios.a.saved -- so a $0.37 saving gave sign
#       +1 against sign 0 and no household saving under fifty cents got a
#       report at all.)
#
#   DIFFERENT SCENARIOS OR DIFFERENT BASES
#       Two runs of the same engine over different inputs, or two engines on
#       different rate bases. A difference is EXPECTED and is not a
#       contradiction, so nothing may gate on their agreement -- at most, a
#       clause naming one of them names which.
#       (e.g. packages.MID's battery_alone_yr is the battery on the
#       UNSHIFTED baseline and battery_alone_post_ev_fix_yr is the battery
#       after the EV shift; CLAUDE.md section 1b describes exactly that
#       behavior/hardware overlap as the expected result.)
# ---------------------------------------------------------------------------
def _sign_claim(token, subject, magnitude, detail):
    """The three-state ladder every "is this worth doing" clause reaches for.

    SUPPORTED above zero, SUPPORTED_OPPOSITE at or below it -- an exact zero
    and a loss are both renderable answers, and the caller words them apart --
    and NOT_DETERMINED only when the magnitude is not a finite number at all.

    ONE ladder, because _free_fix_saving and _battery_alone carried
    byte-identical copies of it (issue #131 review round 4, finding 9). What
    is NOT shared is the relationship check that precedes it: those two sites
    read differently-related pairs, and folding their checks together is what
    put a rounding pair and a two-scenario pair through the same sign test in
    the first place."""
    state = (NOT_DETERMINED if not _finite(magnitude)
             else SUPPORTED if magnitude > 0
             else SUPPORTED_OPPOSITE)
    return _claim(token, subject, state, detail)


def _require_derived(token, subject, source, derived, tolerance, detail):
    """NOT_DETERMINED when a DERIVED field misses its source by more than the
    derivation's own tolerance.

    `source` is the value the generator computed FROM (already put through
    the derivation -- the quotient, the sum), `derived` the field it wrote,
    and `tolerance` the slack the derivation itself introduces: 0.5 for a
    round() to whole dollars, 0.05 for a round(_, 1) to a tenth of a year.
    Sign tests and equality tests are both wrong here; the first calls a
    rounding boundary a contradiction, the second calls the rounding one."""
    ok = _finite(source, derived) and abs(source - derived) <= tolerance
    _claim(token, subject, SUPPORTED if ok else NOT_DETERMINED, detail)


# round() to whole dollars moves a figure by at most half a dollar; round(_, 1)
# to a tenth of a year by at most a twentieth. The epsilon is for the binary
# representation of the boundary itself, not for extra slack.
_WHOLE_DOLLAR_ROUNDING = 0.5 + 1e-9
_TENTH_YEAR_ROUNDING = 0.05 + 1e-9


# ---------------------------------------------------------------------------
# Small TOU-boundary oracle: derives clock-hour windows by CALLING
# rates.period(), never by re-declaring the hours. This is what lets
# PEAK_WINDOW / CHEAP_WINDOW / the day-band prices stay correct if rates.py's
# windows ever change, and keeps this module out of
# test_scripts_runnable.py's TOU_EXEMPT concern (it reads the canonical
# module's own output, it does not re-implement TOU assignment).
# ---------------------------------------------------------------------------
def _weekday_runs():
    """[(start_hour, end_hour, period_label), ...] for a canonical (non-
    holiday) weekday, derived by sampling rates.period() every 15 minutes."""
    runs = []
    start, cur = 0.0, R.period(0.0, False)
    h = 0.25
    while h <= 24.0:
        lab = R.period(h, False) if h < 24.0 else None
        if lab != cur:
            runs.append((start, h, cur))
            start, cur = h, lab
        h += 0.25
    return runs


def _hour_label(h):
    """A clock label for a tariff window bound, at MINUTE resolution.

    This used to open `h = int(h)`, which TRUNCATED. The bounds come from
    _weekday_runs() sampling rates.period() every 15 minutes, so a tariff
    whose overnight super-off-peak run ends at 06:30 produced "6am" and
    sections 2, 5 and 15 named a "12am–6am" window half an hour shorter than
    the one the report tells the reader to charge inside (issue #131 review
    round 2, finding 9 -- the site the earlier int() sweep missed).

    The bound is SUPPORTED at minute resolution, so it is rendered to the
    minute rather than refused: refusing a window this module can name
    exactly would be the round-one defect again. A bound that is not a whole
    number of minutes has no clock label at all, and that IS state 3.

    THE MIDNIGHT END BOUND. _weekday_runs() closes its last run at h = 24.0,
    which is the SAME instant as h = 0.0 and reads "12am" on a clock. The
    meridiem test used to be a bare `hour < 12`, so 24 -- being neither less
    than 12 nor a value the `hour % 12 or 12` body ever showed as 24 --
    printed "12pm": a tariff whose last run ends at midnight named a window
    ending at NOON, twelve hours wrong, in the sentence telling the reader
    when the cheap window closes (issue #131 review round 4, finding 7). Both
    halves of the label now read the same clock, modulo the day."""
    minutes = h * 60
    if abs(minutes - round(minutes)) > 1e-9:
        raise SystemExit(
            f"report_tokens: cannot name the tariff window bound {h}h -- it is not a "
            "whole number of minutes, so there is no clock label to print for it")
    hour, minute = divmod(int(round(minutes)), 60)
    hour %= 24
    body = f"{hour % 12 or 12}" if minute == 0 else f"{hour % 12 or 12}:{minute:02d}"
    return f"{body}{'am' if hour < 12 else 'pm'}"


def _fmt_hour_range(h1, h2):
    # The am/pm elision is suppressed at the two hours whose label carries a
    # 12 -- "12–1am" would read as a window an hour long starting at noon --
    # and h1 is taken modulo the day for the same reason _hour_label is: a run
    # starting at h = 24.0 is a run starting at midnight.
    l1, l2 = _hour_label(h1), _hour_label(h2)
    if l1[-2:] == l2[-2:] and h1 % 24 not in (0, 12):
        return f"{l1[:-2]}–{l2}"
    return f"{l1}–{l2}"


def _peak_window():
    runs = [r for r in _weekday_runs() if r[2] == "on"]
    if len(runs) != 1:
        raise SystemExit(f"report_tokens: expected exactly one on-peak run in "
                          f"rates.period()'s weekday schedule, found {runs}")
    return _fmt_hour_range(runs[0][0], runs[0][1])


def _cheap_run():
    """The daytime (not overnight) weekday super-off-peak run -- the
    'structural gift' window highlighted in the Bottom line / Monday appendix.

    Returned as the raw (start_hour, end_hour, label) run rather than only as
    the formatted string, because section 2 needs the BOUNDS to ask which
    exports land inside the window, not just its printed name. The window is
    the tariff's own, from rates.period(); nothing here picks a 'midday'."""
    runs = [r for r in _weekday_runs() if r[2] == "sop" and r[0] > 0]
    if len(runs) != 1:
        raise SystemExit(f"report_tokens: expected exactly one daytime weekday "
                          f"super-off-peak run, found {runs}")
    return runs[0]


def _cheap_window():
    lo, hi, _lab = _cheap_run()
    return _fmt_hour_range(lo, hi)


def _overnight_cheap_run():
    """The OVERNIGHT weekday super-off-peak run (the one starting at
    midnight), as distinct from _cheap_run()'s daytime run."""
    runs = [r for r in _weekday_runs() if r[2] == "sop" and r[0] == 0]
    if len(runs) != 1:
        raise SystemExit(f"report_tokens: expected exactly one overnight weekday "
                          f"super-off-peak run starting at midnight, found {runs}")
    return runs[0]


_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _season_label():
    months = sorted(R.SUMMER_MONTHS)
    if months != list(range(months[0], months[-1] + 1)):
        raise SystemExit(f"report_tokens: rates.SUMMER_MONTHS {sorted(R.SUMMER_MONTHS)} "
                          "is not a contiguous range; the season-label formatter "
                          "assumes a single contiguous summer season")
    return f"{_MONTH_ABBR[months[0]]}–{_MONTH_ABBR[months[-1]]}"


def _rates_effective_date():
    m = re.search(r"effective (\d{1,2})/(\d{1,2})/(\d{4})", _rates_src())
    if not m:
        raise SystemExit("report_tokens: could not find an 'effective M/D/YYYY' date "
                          "in analysis/rates.py's module docstring")
    mo, day, yr = map(int, m.groups())
    return dt.date(yr, mo, day)


def _season_for_month(month):
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "fall", 10: "fall", 11: "fall"}[month]


def _fraction_to_month(fraction):
    month = max(1, min(12, round(fraction * 12) or 1))
    return month


# ---------------------------------------------------------------------------
# KNOWN GAPS: live template tokens with no committed source anywhere in this
# repo today (checked: data/*.json, data/*.csv, private/household.yaml,
# analysis/*.py constants, TECHNICAL.md). Kept small and named, per the brief:
# resolving these mechanically would require inventing a figure, which
# CLAUDE.md section 0 forbids. A later human/LLM pass can fill them once a
# script exists; until then resolve_token() raises SystemExit naming them.
# ---------------------------------------------------------------------------
KNOWN_GAPS = {
    "UTILITY_TOOL_BEST_PLAN_FIGURE": (
        "the utility plan-comparison tool's own dollar figure comes only from "
        "DATA-SOURCES-CHEATSHEET.md's plan_comparison_capture field, which is "
        "private-only (a screenshot of an account-specific page); no committed "
        "data/*.json or *.csv extracts the dollar figure from it, so there is no "
        "public-ok source for the exact number the tool quoted"),
    # THE OTHER HALF OF THE SAME CELL, and the reason it is a token at all.
    #
    # Section 3's fifth column is headed "<utility>'s own tool says", and its
    # household row used to answer it in FIXED MARKUP:
    #
    #     <td>{{UTILITY_TOOL_BEST_PLAN_FIGURE}} — "Your Best Plan" ✓</td>
    #
    # The figure was a token; the verdict beside it was not. So the page
    # asserted that the utility's tool had named THIS household's plan its
    # best one, for every household, with nothing able to make that false --
    # the same shape as the three assertions issue #196 removed from this row,
    # the card above it and the lead-in below it. It is worse than a stale
    # sentence: the two rankings can legitimately disagree, and the
    # disagreement is exactly what a reader of that column wants to see.
    #
    # WHY THE LABEL ITSELF STAYS LITERAL IN THE TEMPLATE. The tidier shape is
    # one token owning the whole cell, and it cannot work here.
    # generate_report.render() HTML-escapes every substituted token value with
    # quote=True (its own adversarial-review finding 4), so a human answer
    # carrying `"Your Best Plan"` publishes as `&quot;Your Best Plan&quot;`.
    # The straight quotes in the published cell can therefore only come from
    # the template. So the template keeps the LABEL -- the tool's own words,
    # the thing being asked about -- and this token carries the ANSWER that
    # follows it. Nothing in the fixed half says the label applies.
    #
    # WHY IT IS A GAP AND NOT SOURCED, even though this checkout's
    # research/sdge-plan-comparison-capture.md writes the tool's verdict down.
    # That note is prose a person typed after reading a screenshot -- the same
    # attestation this token asks for, one file over -- and nothing here reads
    # a token out of research/. Same standing as the FIGURE above, which has
    # sat in this dict with that note already committed. Making research/ a
    # token source is a real proposal and a different one: it needs a parse
    # contract every household's own capture note has to meet.
    "UTILITY_TOOL_BEST_PLAN_VERDICT": (
        "whether the utility's plan-comparison tool applied the \"Your Best Plan\" "
        "label in section 3's fifth column to THIS household's plan, and which plan "
        "it named if not. Same capture as UTILITY_TOOL_BEST_PLAN_FIGURE above and "
        "uncommitted for the same reason: DATA-SOURCES-CHEATSHEET.md's "
        "plan_comparison_capture is a private-only screenshot of an account-specific "
        "page, and no committed data/*.json or *.csv reads a verdict out of it. It "
        "cannot be derived either -- data/plan_results.csv ranks THIS repo's modeled "
        "annual totals, and where a third party's tool puts the same plans is a fact "
        "about that tool, not an arithmetic result. Answer with the mark that follows "
        "the label: a bare check where the tool named this plan, or a short phrase "
        "naming the plan it named instead. Answer nothing and the run refuses, which "
        "is the point: this repo cannot state either verdict on the human's behalf"),
    "EXPANSION_PAYBACK_YEARS": (
        "a payback needs a YIELD PRICED THE RIGHT WAY and a COST, and neither "
        "half is committed here. On the yield side, what one more kW of panels "
        "would earn is not the EXPORT_VALUE_SURPLUS_BOUND / "
        "EXPORT_VALUE_NETTING_BOUND range below: exports are the residual "
        "left after household load, not the shape of added production, so part "
        "of an added panel's output would displace an import and the rest is "
        "settled by each month's per-period NEM 2.0 netting. Pricing it needs a "
        "counterfactual re-billing of the year at a larger array, filed as issue "
        "#190 and not run by anything committed here. On the cost side, retrofit "
        "dollars per watt is a fact about a local installer market on a date, "
        "which this repo does not collect. A token would state the years as "
        "though both halves were measured"),
    "ELECTRIFICATION_VERDICT_SHORT": (
        "data/heat_pump_conversion.json now prices the space-heating side "
        "(install cost, three COP scenarios, real per-interval-billed "
        "midpoint net savings positive at every COP tested -- from barely "
        "so at COP 2.8 (within this model's own precision) to modestly so "
        "at COP 3.5/4.2 -- not the flat gas_decomposition:"
        "hp_heating_saving_yr figure the pre-issue-#1 comparison used), but "
        "hpwh_saving_yr (205) still has no committed install-cost source -- "
        "'which appliance pencils' needs BOTH sides costed the same way, "
        "and only one is, so the comparison still cannot be made honestly "
        "from what is committed"),
    "INCENTIVE_STATUS": (
        "DATA-SOURCES-CHEATSHEET.md's incentive_status field is an explicit "
        "'research task' (current federal ITC / CA SGIP status, deliberately not "
        "assumed to still exist) with no household.yaml storage location and no "
        "data/*.json artifact; nothing in this repo's committed archive records "
        "today's program status"),
}


# ---------------------------------------------------------------------------
# WHAT AN ANSWER TO A GAP HAS TO CARRY.
#
# A gap token refuses to resolve, and the operator answers it by hand
# (generate_report.py's --human-answers file, keyed "TOKEN:<name>"). That
# answer is spliced straight into the published page. Nothing checked it, and
# for one of these tokens the surrounding markup is a sentence the answer
# COMPLETES rather than a slot it fills, so a non-answer does not render as a
# blank -- it renders as an assertion:
#
#     <td>{{UTILITY_TOOL_BEST_PLAN_FIGURE}} — "Your Best Plan" {{..._VERDICT}}</td>
#
# The label is fixed markup (see the token's own note: render() escapes every
# substituted value, so the straight quotes can only come from the template),
# and only the mark after it is a token. Answer the verdict "" and the cell
# publishes `$4,519.65 — "Your Best Plan" `, which reads exactly as the tool
# having applied that label to this plan -- the fixed win-claim issue #196
# removed, restored by an empty string. A bare plan name reads the same way:
# `... "Your Best Plan" TOU-DR-P` says the tool named this plan and mentions
# another one. Neither is a verdict, and both publish silently.
#
# So the contract lives here, beside the gap it constrains, and it is the
# token's -- not the operator's, and not the renderer's. Two rules:
#
#   * every gap: an answer that is blank or only whitespace is not an answer.
#     A gap exists because this repo will not state something on the human's
#     behalf, and an empty attestation states it just as loudly as a wrong one.
#   * named gaps: the shape the sentence around the slot requires. The verdict
#     has to LEAD with a mark, which is what KNOWN_GAPS' own reason text asks
#     for ("a bare check where the tool named this plan, or a short phrase
#     naming the plan it named instead"), and it is the only part checkable
#     without reading the screenshot: a mark negates or affirms the label
#     beside it, and English after the mark can say whatever the capture shows.
#     The figure has to carry a digit, for the same reason one slot along -- an
#     empty figure publishes a verdict attached to a quotation that is not
#     there.
#
# WHAT THIS MODULE CANNOT DO. generate_report.run() splices the operator's
# answer with `resolved[name] = human_answers[override_key]` and never asks
# this module whether the answer is one; that call site has to pass it through
# validate_gap_answer() for a refusal to reach the page. The contract is
# declared and enforced here, so the check is one call away and cannot be
# reinvented differently at the splice; whether it is CALLED is that file's.
# ---------------------------------------------------------------------------
# Marks that state a verdict by themselves. Written out rather than derived
# because this is a vocabulary, not a rule: a check, a cross, and the shapes of
# each a capture note is likely to be typed with.
GAP_VERDICT_MARKS = ("✓", "✔", "✅", "✗", "✘", "❌", "×")


def _answer_leads_with_a_verdict_mark(answer):
    return answer.strip().startswith(GAP_VERDICT_MARKS)


def _answer_quotes_a_figure(answer):
    return any(ch.isdigit() for ch in answer)


GAP_ANSWER_CONTRACTS = {
    "UTILITY_TOOL_BEST_PLAN_VERDICT": (
        _answer_leads_with_a_verdict_mark,
        "the answer completes a cell whose fixed half is the tool's own label "
        "(<figure> — \"Your Best Plan\" <answer>), so it has to open with the mark "
        "that answers it -- one of " + " ".join(GAP_VERDICT_MARKS) + " -- optionally "
        "followed by the plan the tool named instead. An answer that states no "
        "verdict leaves the label standing as this report's own claim about a third "
        "party's tool"),
    "UTILITY_TOOL_BEST_PLAN_FIGURE": (
        _answer_quotes_a_figure,
        "the answer is the dollar figure the utility's tool quoted, so it has to "
        "carry a digit. Without one the cell publishes a verdict beside a quotation "
        "that is not there"),
}


def validate_gap_answer(name, answer):
    """The operator's answer to gap token `name`, or SystemExit saying what the
    answer has to carry and why.

    Returns the answer unchanged when it meets the contract -- callers can
    substitute the return value and keep the check on the same line.
    """
    spec = TOKENS.get(name)
    if spec is None or spec.get("kind") != "gap":
        raise SystemExit(f"report_tokens: {name!r} is not a gap token, so there is no "
                          "hand-written answer for it to carry -- it either resolves "
                          "from a committed source or is not declared at all")
    if not isinstance(answer, str) or not answer.strip():
        raise SystemExit(
            f"report_tokens: the answer given for gap token {name} is blank, and a "
            "blank attestation is not an answer -- the slot publishes into a sentence "
            f"that reads without it. What this gap needs: {spec['reason']}")
    check = spec.get("answer_contract")
    if check is not None and not check[0](answer):
        raise SystemExit(
            f"report_tokens: the answer given for gap token {name} ({answer!r}) does "
            f"not carry what that slot has to state -- {check[1]}")
    return answer


# ---------------------------------------------------------------------------
# 2. THE TOKEN MAP. One entry per sourceable token: the ones live in static
#    markup AND the legitimate comment-only examples inside <!-- TODO -->
#    blocks, which the brief for this module is explicit about ("legitimate
#    tokens... the map must source them too"). No count is written here on
#    purpose -- it went stale twice while the template grew, and
#    test_report_tokens.case_token_map_key_set_equals_the_templates_full_token_set
#    checks the key set against a fresh template parse, both ways, which is
#    the claim a number here was only approximating.
# ---------------------------------------------------------------------------
TOKENS = {}


def _tok(name, **spec):
    if name in TOKENS:
        raise SystemExit(f"report_tokens: token {name!r} declared twice in TOKENS")
    TOKENS[name] = spec


def is_attribute_only(name):
    """True for a token whose value is MARKUP, not language: it lands inside
    an HTML tag (a class, an id, an href) and never in text a reader reads.

    WHY THIS EXISTS. generate_report.py hands a prose block's LLM the values
    of every token live in that block's <h2> section, under the heading
    "Values you may cite by writing their {{TOKEN}} name", and accepts a
    returned fragment citing any of them. That is right for a figure and
    wrong for an attribute value: S4_ROW_CLASS resolves to a bare CSS class
    name ("win", "trails-tie"), carries no digit for the numeral guard to
    object to, and is a real in-scope token -- so a sentence reading "the
    matrix rates this plan {{S4_ROW_CLASS}}" clears every guard there is and
    publishes as "the matrix rates this plan win." The same value also feeds
    the block's cache key, so a row that changes standing re-authors two
    §4 prose blocks that never mentioned it. Neither is about the render:
    the token still resolves and still fills its attribute exactly as before.

    A FLAG, NOT A TEMPLATE SCAN, and the reason is measurable in this very
    template. Deriving "attribute-only" at runtime from the token's own
    position means deciding, in regex, whether an offset sits inside a tag.
    A plain "is it between < and >" scan answers YES for CHART_TITLE_SPREAD,
    which sits in a JS string in the chart config -- the `<=` in
    `c.p1DataIndex<=D.spBreak.summer` a few lines above opens a tag that
    never closes until the next `>`. Getting it right needs comment masking,
    <script>/<style> masking and a real tag scan, i.e. a small HTML parser
    on the path that decides what the model is allowed to see, where its
    next false positive is silent: a token quietly leaves a block's scope
    and the only symptom is prose that no longer cites a figure it should.

    So the declaration is the runtime rule (a dict lookup, which cannot
    misfire), and that parser lives in the TEST instead, where it is checked
    BOTH ways and a disagreement is a named failure:
    test_report_tokens.case_attribute_only_flags_match_where_the_template_
    puts_each_token asserts every flagged token occurs only inside a tag and
    every unflagged one never does. Move this token into running text, or
    flag one that already lives there, and that case fails.
    """
    return bool(TOKENS.get(name, {}).get("attribute_only"))


for _gap_name, _gap_reason in KNOWN_GAPS.items():
    # The contract rides on the token, so validate_gap_answer needs the name
    # and nothing else, and a gap with no shape rule still gets the blank floor.
    _tok(_gap_name, kind="gap", reason=_gap_reason,
         answer_contract=GAP_ANSWER_CONTRACTS.get(_gap_name))


# ---- household / plan / utility identity -----------------------------------
_tok("CLIMATE_ZONE", kind="household_yaml", path="household.climate_zone")
_tok("UTILITY_NAME", kind="household_yaml", path="household.utility")
# NOT a bare household.plan passthrough any more -- see _best_plan(), declared
# with the rest of the plan ranking further down (Python binds the lambda's
# name at call time, so the forward reference is fine and the helper stays
# beside the ranking it gates on).
_tok("BEST_PLAN", kind="derived", get=lambda ctx: _best_plan(ctx, "BEST_PLAN"),
     sources=["private/household.yaml:household.plan",
              "private/household.yaml:household.cca (which provider column ranks)",
              "data/plan_results.csv (the ranking report-template.html asserts)"])
_tok("NEM_STATUS", kind="household_yaml", path="household.nem_version")
_tok("PTO_DATE", kind="household_yaml", path="household.pto_date", fmt="raw")
_tok("SYSTEM_SIZE_KW_DC", kind="household_yaml", path="solar.kw_dc", fmt="num2")
_tok("AC_CEILING_KW", kind="household_yaml", path="solar.kw_ac", fmt="num2")
_tok("PANEL_COUNT", kind="household_yaml", path="solar.module_count", fmt="num0")
_tok("SYSTEM_GROSS_COST", kind="household_yaml", path="solar.install_invoice_usd", fmt="usd0")


def _install_payment_date(ctx):
    raw = hh1("solar.install_paid_date")           # e.g. "2019-12" or a date
    s = str(raw)
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m:
        yr, mo = int(m.group(1)), int(m.group(2))
        return f"{_MONTH_FULL[mo]} {yr}"
    d = dt.date.fromisoformat(s)
    return f"{_MONTH_FULL[d.month]} {d.day}, {d.year}"


_tok("INSTALL_PAYMENT_DATE", kind="derived", get=_install_payment_date,
     sources=["private/household.yaml:solar.install_paid_date"])


def _generation_provider_short(ctx):
    cca = hh1("household.cca")
    head = re.split(r"\s+[—–-]\s+|\(", cca)[0].strip()
    initials = "".join(w[0] for w in head.split() if w[:1].isupper())
    if not initials:
        raise SystemExit(f"report_tokens: could not derive an acronym from "
                          f"household.cca {cca!r}")
    return initials


_tok("GENERATION_PROVIDER_SHORT", kind="derived", get=_generation_provider_short,
     sources=["private/household.yaml:household.cca"])
_tok("GENERATION_PROVIDER", kind="derived",
     get=lambda ctx: f"{hh1('household.cca')} ({_generation_provider_short(ctx)})",
     sources=["private/household.yaml:household.cca"])


def _other_major_loads(ctx):
    # "vehicles" has no trailing "[]" in its cheatsheet contract path, so it
    # resolves (like cleaning_history) to [the whole list] -- one found node
    # holding every entry, not one node per vehicle.
    lists = _hh_value("vehicles")
    if len(lists) != 1 or not isinstance(lists[0], list):
        raise SystemExit("report_tokens: private/household.yaml:vehicles did not "
                          "resolve to a single list")
    n = len(lists[0])
    return f"{n} EV{'s' if n != 1 else ''}"


_tok("OTHER_MAJOR_LOADS", kind="derived", get=_other_major_loads,
     sources=["private/household.yaml:vehicles"])

_tok("HOME_DESCRIPTION", kind="cited_constant", value="Single-family home",
     source="TECHNICAL.md's stated subject system ('a single-family home in the "
            "SDG&E Coastal climate zone')")

def _as_date(v):
    """household.yaml date fields parse as datetime.date via PyYAML's implicit
    date resolver, but a fabricated/synthetic archive may hand this a plain
    'YYYY-MM-DD' string instead -- accept either."""
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


_tok("NEM_EXPIRY_YEAR", kind="derived",
     get=lambda ctx: _as_date(hh1("household.pto_date")).year + 20,
     sources=["private/household.yaml:household.pto_date (+20 yr NEM 2.0 term)"], fmt="year")

_tok("INVERTER_DESCRIPTION", kind="derived",
     get=lambda ctx: (lambda count, kw_ac:
         f"{int(count)} × {hh1('solar.inverter_model')}"
         f", ~{kw_ac * 1000 / count:.0f} VA each")(
         *_figures("INVERTER_DESCRIPTION", "what each microinverter is rated at",
                   **{"solar.inverter_count": hh1("solar.inverter_count"),
                      "solar.kw_ac": hh1("solar.kw_ac")})),
     sources=["private/household.yaml:solar.inverter_count/inverter_model/kw_ac"])

_tok("PANEL_MODEL_WATTS", kind="derived",
     get=lambda ctx: (lambda kw_dc, modules: f"{kw_dc * 1000 / modules:.0f} W")(
         *_figures("PANEL_MODEL_WATTS", "what each module is rated at",
                   **{"solar.kw_dc": hh1("solar.kw_dc"),
                      "solar.module_count": hh1("solar.module_count")})),
     sources=["private/household.yaml:solar.kw_dc/module_count"])


def _size_verification_source(ctx):
    sources = _hh_value("monitoring[].source")
    measures = _hh_value("monitoring[].measures")
    for s, m in zip(sources, measures):
        if "production" in m.lower():
            return f"the {s} device registration"
    raise SystemExit("report_tokens: no monitoring[] entry measures production")


_tok("SIZE_VERIFICATION_SOURCE", kind="derived", get=_size_verification_source,
     sources=["private/household.yaml:monitoring[].source/measures"])


# ---- day-band / TOU-derived tokens -----------------------------------------
_tok("PEAK_WINDOW", kind="derived", get=lambda ctx: _peak_window(),
     sources=["analysis/rates.py:period() (sampled)"])
_tok("CHEAP_WINDOW", kind="derived", get=lambda ctx: _cheap_window(),
     sources=["analysis/rates.py:period() (sampled)"])
_tok("DAYBAND_SEASON_LABEL", kind="derived", get=lambda ctx: _season_label(),
     sources=["analysis/rates.py:SUMMER_MONTHS"])
_tok("DAYBAND_SOP_PRICE", kind="derived", get=lambda ctx: _cents1(R.allin("S", "sop")),
     sources=["analysis/rates.py:allin('S','sop')"])
_tok("DAYBAND_OFFPEAK_PRICE", kind="derived",
     get=lambda ctx: "~" + _cents1(R.allin("S", "off")),
     sources=["analysis/rates.py:allin('S','off')"])
_tok("DAYBAND_ONPEAK_PRICE", kind="derived",
     get=lambda ctx: (lambda lo, hi: f"{round(lo * 100)}–{round(hi * 100)}¢")(
         min(R.allin("S", "on"), R.allin("W", "on")),
         max(R.allin("S", "on"), R.allin("W", "on"))),
     sources=["analysis/rates.py:allin('S'/'W','on')"])
_tok("SUPER_OFF_PEAK_RATE", kind="derived", get=lambda ctx: _cents1(R.allin("S", "sop")),
     sources=["analysis/rates.py:allin('S','sop')"])
_tok("SUMMER_ONPEAK_IMPORT_RATE", kind="derived",
     get=lambda ctx: _usd3(R.allin("S", "on")),
     sources=["analysis/rates.py:allin('S','on')"])
_tok("SUMMER_ONPEAK_EXPORT_RATE", kind="derived",
     get=lambda ctx: _usd3(R.credit("S", "on")),
     sources=["analysis/rates.py:credit('S','on')"])
_tok("RATES_EFFECTIVE_DATE", kind="derived",
     get=lambda ctx: _rates_effective_date().isoformat(),
     sources=["analysis/rates.py module docstring ('effective M/D/YYYY')"])
_tok("BILLED_GENERATION_RATES", kind="derived",
     get=lambda ctx: (f"${R.CEA['S']['on']} on-peak / ${R.CEA['S']['off']} off-peak / "
                       f"${R.CEA['S']['sop']} super-off-peak (summer)"),
     sources=["analysis/rates.py:CEA['S']"])


# ---- report_data.json -------------------------------------------------------
_tok("ANNUAL_IMPORT_KWH", kind="data_json", file="report_data.json",
     path=("totals", "imp"), fmt="num0")
_tok("ANNUAL_EXPORT_KWH", kind="data_json", file="report_data.json",
     path=("totals", "exp"), fmt="num0")


def _annual_production_kwh(ctx):
    rows = _csv_rows("enphase_daily_production.csv")
    for r in rows:
        if r["Date/Time"] == "Total":
            return float(r["Energy Delivered (kWh)"].replace(",", ""))
    raise SystemExit("report_tokens: enphase_daily_production.csv has no 'Total' footer row")


_tok("ANNUAL_PRODUCTION_KWH", kind="derived", get=_annual_production_kwh,
     sources=["data/enphase_daily_production.csv (Total footer row)"], fmt="num0")


def _pvoutput_annual_kwh(ctx):
    return sum(float(r["generated_kwh"]) for r in _csv_rows("pvoutput_daily.csv"))


def _production_agreement_pct(ctx):
    a, b = _annual_production_kwh(ctx), _pvoutput_annual_kwh(ctx)
    return abs(a - b) / ((a + b) / 2) * 100


_tok("PRODUCTION_SOURCE_COUNT", kind="cited_constant", value=2, fmt="num0",
     source="two production totals are backed by committed artifacts (Enphase CT "
            "meter via data/enphase_daily_production.csv's Total row, PVOutput via "
            "data/pvoutput_daily.csv summed); a third independently-derived series "
            "the hand-authored report also cites is disclosed there as an "
            "unarchived workpaper, not artifact-backed, so it is not counted here")
_tok("PRODUCTION_AGREEMENT_PCT", kind="derived",
     get=lambda ctx: f"±{round(_production_agreement_pct(ctx))}%",
     sources=["data/enphase_daily_production.csv", "data/pvoutput_daily.csv"])


def _annual_load_kwh(ctx):
    rd = _json("report_data.json")
    return rd["totals"]["imp"] + (_annual_production_kwh(ctx) - rd["totals"]["exp"])


_tok("ANNUAL_LOAD_KWH", kind="derived", get=lambda ctx: round(_annual_load_kwh(ctx)),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="num0")
_tok("SOLAR_COVERAGE_PCT", kind="derived",
     get=lambda ctx: round(_annual_production_kwh(ctx) / _annual_load_kwh(ctx) * 100),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="pct0")
_tok("SELF_CONSUMED_SHARE", kind="derived",
     get=lambda ctx: (lambda p, e: round((p - e) / p * 100))(
         _annual_production_kwh(ctx), _json("report_data.json")["totals"]["exp"]),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="pct0")
_tok("EXPORTED_SHARE", kind="derived",
     get=lambda ctx: (lambda p, e: round(e / p * 100))(
         _annual_production_kwh(ctx), _json("report_data.json")["totals"]["exp"]),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"], fmt="pct0")
def _capacity_factor(ctx):
    """The array's output as a share of running at nameplate all year.

    BOTH figures through the gate, S2_VERDICT's reason (issue #131 review
    round 6, finding 2): this f-string checked the nameplate and interpolated
    the production quotient unchecked, so a non-finite Total footer in
    data/enphase_daily_production.csv published "~nan%" -- while
    ANNUAL_PRODUCTION_KWH, the leaf token over that same cell, refused it."""
    production, kw_dc = _figures(
        "CAPACITY_FACTOR", "what fraction of nameplate the array delivers",
        **{"data/enphase_daily_production.csv's annual production":
           _annual_production_kwh(ctx),
           "solar.kw_dc": hh1("solar.kw_dc")})
    return f"~{production / (kw_dc * 8760) * 100:.1f}%"


_tok("CAPACITY_FACTOR", kind="derived", get=_capacity_factor,
     sources=["data/enphase_daily_production.csv", "private/household.yaml:solar.kw_dc"])
_tok("SPECIFIC_YIELD", kind="derived",
     get=lambda ctx: round(_annual_production_kwh(ctx) / hh1("solar.kw_dc")),
     sources=["data/enphase_daily_production.csv", "private/household.yaml:solar.kw_dc"], fmt="num0")

_tok("ONPEAK_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("on")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")
_tok("OFFPEAK_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("off")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")
_tok("SOP_IMPORT_SHARE_PCT", kind="derived",
     get=lambda ctx: round(_json("report_data.json")["periods_chart"]["import_share"][
         _json("report_data.json")["periods_chart"]["order"].index("sop")] * 100),
     sources=["data/report_data.json:periods_chart"], fmt="num0")

_tok("CHART_TITLE_PERIODS", kind="derived",
     get=lambda ctx: (lambda pc, i: (
         f"{round(pc['import_share'][i] * 100)}% of imports happen on-peak, driving "
         f"{round(pc['import_cost'][i] / sum(pc['import_cost']) * 100)}% of gross "
         "import cost"))(_json("report_data.json")["periods_chart"],
                          _json("report_data.json")["periods_chart"]["order"].index("on")),
     sources=["data/report_data.json:periods_chart"])


def _chart_title_hourly(ctx):
    kws = _json("report_data.json")["onpeak_kw_S"]
    # Every hour, not just the winner: max() over a series containing a nan
    # returns whichever element the comparison chain happens to leave standing,
    # so the WINNER can be finite while the series it was picked out of is not.
    _require_finite("CHART_TITLE_HOURLY", "which hour carries the summer on-peak peak",
                    **{f"onpeak_kw_S_{h}": v for h, v in sorted(kws.items())})
    peak_h, peak_kw = max(kws.items(), key=lambda kv: kv[1])
    return (f"Average demand by hour of day — on-peak ({_peak_window()}) reaches "
            f"{peak_kw:.1f} kW average summer draw at {_hour_label(int(peak_h))}")


_tok("CHART_TITLE_HOURLY", kind="derived", get=_chart_title_hourly,
     sources=["data/report_data.json:onpeak_kw_S"])


def _chart_title_battery(ctx):
    dp = _json("battery_dispatch_policies.json")
    before, after = _figures(
        "CHART_TITLE_BATTERY", "how far a battery moves on-peak imports",
        onpeak_import_kwh=dp["inputs"]["onpeak_import_kwh"],
        onpeak_after_greedy=dp["pw3"]["onpeak_after_greedy"])
    return (f"Projected grid import with a battery — on-peak imports fall from "
            f"{before:,} to {after:,} "
            "kWh/yr with one battery")


_tok("CHART_TITLE_BATTERY", kind="derived", get=_chart_title_battery,
     sources=["data/battery_dispatch_policies.json:inputs, pw3"])


def _chart_title_carbon(ctx):
    wm = _json("carbon_fullyear_results.json")["intensity_kg_per_mwh"]["window_means_annual"]
    overnight, midday = _figures(
        "CHART_TITLE_CARBON", "how the two windows' measured carbon intensities compare",
        sop_overnight_00_06=wm["sop_overnight_00_06"],
        solar_midday_10_14=wm["solar_midday_10_14"])
    return (f"Grid carbon by hour (measured) — overnight super-off-peak averages "
            f"{overnight:.0f} kg CO₂/MWh vs {midday:.0f} "
            "at solar midday")


_tok("CHART_TITLE_CARBON", kind="derived", get=_chart_title_carbon,
     sources=["data/carbon_fullyear_results.json:intensity_kg_per_mwh.window_means_annual"])


def _chart_title_spread(ctx):
    ds = _json("tou_spread.json")["delivery_spread"]
    summer, winter = _figures(
        "CHART_TITLE_SPREAD", "how many priced segments the spread rests on",
        summer_n=ds["summer"]["n"], winter_n=ds["winter"]["n"])
    return (f"On-peak minus super-off-peak delivery spread by rate segment — "
            f"{summer} summer and {winter} winter priced segments")


_tok("CHART_TITLE_SPREAD", kind="derived", get=_chart_title_spread,
     sources=["data/tou_spread.json:delivery_spread"])


def _sec9_teaser(ctx):
    # Sessions come from behavior_rebuild.json, NOT deep_results.json (issue
    # #130). Both detectors are committed and they disagree -- 563 vs 580 --
    # because they detect differently: deep_analyses.py gates on a flat
    # kw > 6.5 and drops blocks under 3 kWh, while behavior_rebuild.py
    # subtracts a rolling-percentile baseline and additionally gates on
    # minimum duration AND peak excess. (Stated as the mechanism only to the
    # extent the two sources show it; which detector is closer to truth is
    # not settled here.) Section 9's own body, and every dollar figure
    # downstream of it, use behavior_rebuild's 563, so a teaser sourced from
    # deep_results contradicted the section it introduces.
    #
    # THE ALWAYS-ON FLOOR COMES FROM quiet_night_floor.json, and from nothing
    # else (issue #140). Three artifacts have carried a figure for this one
    # load, and only one of them can back a published number:
    #   - data/extra_results.json:phantom has NO generator, in this repo or in
    #     its history -- extra_results.py lists it in FROZEN_KEYS and copies it
    #     through, saying in its own comment that the methodology has no
    #     record here. A figure that cannot be reproduced cannot be published
    #     (CLAUDE.md section 0).
    #   - data/deep_results.json:phantom does have a live generator, but
    #     deep_analyses.py prices it with a hardcoded flat $0.20/kWh with the
    #     rates module computed and unused on the line above; the hour-weighted
    #     all-in import rate across the analysis year is $0.375/kWh, so that
    #     blend is roughly half the real price of the energy. Tracked as its
    #     own issue (#172) against that script; see TECHNICAL.md section 3.5.
    #   - data/quiet_night_floor.json takes every rate from rates.py and
    #     prices the same load two independent ways that agree to 1.2%.
    # So the floor figures here are that artifact's, resolved through the SAME
    # token formulas sections 0 and 13 render, which is what stops one load
    # from carrying three numbers across three sections again.
    #
    # AND THE COST WEARS THE SAME COVERAGE GATE AS THE ENERGY (issue #140,
    # adversarial pass 2, finding 2). The price-map total is a sum over the
    # interval series the run was given, so its "/yr" is earned through
    # _night_floor_coverage exactly as NIGHT_FLOOR_ANNUAL_KWH's and
    # NIGHT_FLOOR_ANNUAL_COST's are. Half a coverage-aware sentence is worse
    # than none: the first version appended "/yr" unconditionally, so a
    # regenerated partial corpus rendered "4,944 kWh across the 200 nights
    # measured, less than a full year, about $1,730/yr" -- a window-qualified
    # energy figure and a falsely annualized dollar figure in one clause. The
    # window itself is not restated here because the kWh half already states
    # it, immediately before, for the same nights.
    br = _json("behavior_rebuild.json")
    covers, _nights, _why = _night_floor_coverage()
    sessions, = _figures(
        "SEC9_TEASER", "how many charging sessions section 9 found",
        ev_sessions=br["detection"]["sessions"])
    cost, = _amounts("SEC9_TEASER", "what the always-on overnight floor costs",
                     price_map_usd=_night_floor_pricing()["method_a_price_map"]["total_usd"])
    return (f"{sessions} EV charging sessions logged; an always-on overnight floor of "
            f"{_night_floor_annual_kwh(ctx)}, about ${cost:,.0f}"
            f"{'/yr' if covers else ''}")


_tok("SEC9_TEASER", kind="derived", get=_sec9_teaser,
     sources=["data/behavior_rebuild.json:detection.sessions",
              "data/quiet_night_floor.json:night_floor (median_kw, nights_total)",
              "data/quiet_night_floor.json:pricing.method_a_price_map.total_usd"])


def _sec12_teaser(ctx):
    """Section 12's own one-line conclusion: what the measured cleaning
    recovered -- or, for a household that never had one, that it is not
    determined and why.

    ISSUE #167. This used to subscript the sanity-check block for
    known_cleaning_gain_pct. soiling_analysis.py writes that block in TWO
    shapes, and the other one -- the ordinary outcome for any household whose
    cleaning_history does not contain the dated event the gain was measured on
    -- carries a `status` string and no gain at all. The subscript raised
    KeyError, resolve_all() failed, and the household lost the WHOLE report
    over one <summary> line.

    So it reads through _measured_cleaning(), the same binding CLEANING_EFFECT_
    PCT uses, and for the same reason: the teaser states a figure and names the
    event beside it, so both halves come off one record. When that binding
    cannot be made the answer is rendered, not raised -- _claim(...,
    NOT_DETERMINED, ...) is a SystemExit and would reproduce the total failure
    this fix removes."""
    entry, why = _measured_cleaning()
    if entry is None:
        return (f"what a cleaning recovers on this array is "
                f"{_NOT_DETERMINED_VERDICT} — {why}")
    sc = _cleaning_sanity_check()
    gain, = _figures("SEC12_TEASER", "how much production the cleaning recovered",
                     known_cleaning_gain_pct=sc["known_cleaning_gain_pct"])
    return f"the {sc['cleaning_date']} cleaning measured a {gain}% production gain"


_tok("SEC12_TEASER", kind="derived", get=_sec12_teaser,
     sources=["data/soiling_results.json:sanity_check_2024_cleaning",
              "private/household.yaml:cleaning_history"])


def _sec13_teaser(ctx):
    nem = _json("nem3_grandfathering.json")["grandfathering_value_range_usd_per_yr"]
    swing = _json("carbon_fullyear_results.json")["footprints_kg_co2_per_yr"]["detail"][
        "midday_cleaner_than_overnight_by"]
    low, high = _amounts("SEC13_TEASER", "what NEM 2.0 grandfathering is worth",
                         grandfathering_low=nem["low"], grandfathering_high=nem["high"])
    swing, = _figures("SEC13_TEASER", "how much dirtier overnight grid power is",
                      midday_cleaner_than_overnight_by=swing)
    return (f"NEM 2.0 worth ${low:,.0f}–{high:,.0f}/yr; overnight grid "
            f"power runs {swing:.0f} kg CO₂/yr dirtier than midday for the same load")


_tok("SEC13_TEASER", kind="derived", get=_sec13_teaser,
     sources=["data/nem3_grandfathering.json", "data/carbon_fullyear_results.json"])


# ---- bottom line / bills ----------------------------------------------------
# A YEAR'S ELECTRIC BILL IS NOT A STRUCTURALLY NON-NEGATIVE QUANTITY, so both
# of these take the signed formatter (issue #131 review round 5, part B's
# sweep). The round-4 rule read "a purchase price, a billed total, a kWh
# count" as the unsigned class, and a billed total does not belong in that
# list: under NEM a heavily over-producing household settles a year in CREDIT,
# and `f"${-412:,.0f}"` puts the minus inside the sigil. usd0_signed renders
# every non-negative value identically, so this house's own figures do not
# move. The same reasoning moves the four plan totals and the modeled annual
# below -- every one of them is a net-of-export bill, not a price.
_tok("ACTUAL_ANNUAL_BILL", kind="data_json", file="behavior_rebuild.json",
     path=("baseline", "actual_billed"), fmt="usd0_signed")
_tok("ACTUAL_MONTHLY_BILL", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["baseline"]["actual_billed"] / 12,
     sources=["data/behavior_rebuild.json:baseline.actual_billed"], fmt="usd0_signed")
_tok("ANALYSIS_DAYS", kind="data_json", file="behavior_rebuild.json",
     path=("window", "days"), fmt="num0")
_tok("ANALYSIS_START_DATE", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["window"]["start"].split(" ")[0],
     sources=["data/behavior_rebuild.json:window.start"])
_tok("ANALYSIS_END_DATE", kind="derived",
     get=lambda ctx: _json("behavior_rebuild.json")["window"]["end"].split(" ")[0],
     sources=["data/behavior_rebuild.json:window.end"])


def _analysis_window_short(ctx):
    w = _json("behavior_rebuild.json")["window"]
    s = dt.date.fromisoformat(w["start"].split(" ")[0])
    e = dt.date.fromisoformat(w["end"].split(" ")[0])
    return f"{_MONTH_ABBR[s.month]} {s.year} – {_MONTH_ABBR[e.month]} {e.year}"


_tok("ANALYSIS_WINDOW_SHORT", kind="derived", get=_analysis_window_short,
     sources=["data/behavior_rebuild.json:window"])
_tok("REPORT_DATE", kind="derived", get=lambda ctx: dt.date.today().isoformat(),
     sources=["system clock at generation time"])

_tok("BEHAVIOR_MODEL_SCRIPT", kind="cited_constant", value="analysis/behavior_rebuild.py",
     source="the generator that writes data/behavior_rebuild.json")
_tok("DISPATCH_SCRIPT", kind="cited_constant", value="analysis/battery_dispatch_policies.py",
     source="the generator that writes data/battery_dispatch_policies.json")
_tok("CARBON_SCRIPT", kind="cited_constant", value="analysis/carbon_fullyear.py",
     source="the generator that writes data/carbon_fullyear_results.json")
_tok("LIFETIME_SCRIPT", kind="cited_constant", value="analysis/lifetime_payback.py",
     source="the generator that writes data/lifetime_payback.json")
_tok("SOILING_SCRIPT", kind="cited_constant", value="analysis/soiling_analysis.py",
     source="the generator that writes data/soiling_results.json")
_tok("BILLING_MODEL_SCRIPT", kind="cited_constant", value="analysis/rates.py",
     source="the canonical bill_nem netting engine every absolute dollar in this "
            "report is validated against")


def _bill_count_and_period_count(ctx):
    rows = _csv_rows("electric_bill_summary.csv")
    periods = len(rows)
    # A PDF statement occasionally splits into two billing periods at a
    # mid-cycle rate change (CLAUDE.md 1: "one PDF can contain multiple billing
    # periods"); the split leaves a short stub period behind. A normal SDG&E
    # monthly cycle runs ~28-32 days, so any period under 15 days is treated as
    # a stub half of a statement rather than a statement of its own.
    stubs = sum(1 for r in rows if float(r["days"]) < 15)
    return periods, periods - stubs


_tok("BILLING_PERIOD_COUNT", kind="derived",
     get=lambda ctx: _bill_count_and_period_count(ctx)[0],
     sources=["data/electric_bill_summary.csv"], fmt="num0")
_tok("BILL_COUNT", kind="derived",
     get=lambda ctx: _bill_count_and_period_count(ctx)[1],
     sources=["data/electric_bill_summary.csv"], fmt="num0")


_tok("EV_FIX_SAVINGS_100", kind="data_json", file="behavior_rebuild.json",
     path=("scenarios", "a", "saved"), fmt="usd0_tilde_signed")
_tok("EV_FIX_SAVINGS_80", kind="data_json", file="behavior_rebuild.json",
     path=("scenarios", "b", "saved"), fmt="usd0_signed")
_tok("OVERLAP_DEDUCTION", kind="data_json", file="behavior_rebuild.json",
     path=("battery", "double_count_avoided"), fmt="usd0_signed")

_tok("BATTERY_SAVINGS_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "save"), fmt="usd0_signed")
_tok("BATTERY_SAVINGS_EVENING_ONLY", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "evening", "save"), fmt="usd0_signed")
_tok("BATTERY_EXP_SAVINGS_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3x", "greedy", "save"), fmt="usd0_signed")
_tok("KWH_SERVED_PRICE_AWARE", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "kwh_served"), fmt="num0")
_tok("KWH_SERVED_EXP", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3x", "greedy", "kwh_served"), fmt="num0")
_tok("CYCLES_PER_DAY", kind="data_json", file="battery_dispatch_policies.json",
     path=("pw3", "greedy", "cycles_per_day"), fmt="num2")
_tok("STORED_KWH_COST_SOLAR", kind="cited_constant", value="~8.4¢",
     source="analysis/battery_dispatch_policies.py module docstring: 'a stored kWh "
            "costs ~8.4c (midday surplus: forgone super-off-peak export credit / RTE)'")
_tok("STORED_KWH_COST_GRID", kind="cited_constant", value="~13.9¢",
     source="analysis/battery_dispatch_policies.py module docstring: '~13.9c "
            "(super-off-peak grid top-up / RTE)'")

_tok("BATTERY_COST", kind="data_json", file="package_results.json",
     path=("packages", "MID", "cost"), fmt="usd0_tilde")
_tok("BATTERY_EXPANDED_COST", kind="data_json", file="package_results.json",
     path=("packages", "HIGH", "cost"), fmt="usd0_tilde")


def _battery_sim_row(name):
    for row in _json("battery_sim.json"):
        if row["config"] == name:
            return row
    raise SystemExit(f"report_tokens: no battery_sim.json row named {name!r}")


_tok("BATTERY_MODEL", kind="cited_constant", value="Tesla Powerwall 3 (PW3)",
     source="analysis/battery_dispatch_policies.py module docstring / TECHNICAL.md "
            "section 6 (the shipping 13.5 kWh / 11.5 kW config); matches "
            "data/battery_sim.json's '1x Tesla Powerwall 3' row")
_tok("BATTERY_EXPANDED_MODEL", kind="cited_constant", value="PW3 + Expansion",
     source="analysis/battery_dispatch_policies.py module docstring / TECHNICAL.md "
            "section 6 (the shipping 27 kWh / 11.5 kW config); matches "
            "data/battery_sim.json's 'PW3 + 1 Expansion' row")
_tok("BATTERY_KWH", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["usable_kwh"],
     sources=["data/battery_sim.json"], fmt="num1")
_tok("BATTERY_KW", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["power_kw"],
     sources=["data/battery_sim.json"], fmt="num1")
_tok("BATTERY_EXPANDED_KWH", kind="derived",
     get=lambda ctx: _battery_sim_row("PW3 + 1 Expansion")["usable_kwh"],
     sources=["data/battery_sim.json"], fmt="num0")
_tok("BATTERY_CHARGE_KW", kind="cited_constant", value=5.0, fmt="num1",
     source="analysis/battery_dispatch_policies.py's CHARGE_KW constant (issue #40: "
            "the bare PW3 unit's nameplate CHARGE power, distinct from its 11.5 kW "
            "DISCHARGE rating in BATTERY_KW); data/battery_sim.json has no charge_kw "
            "field to derive this from, so it is cited directly from the source module")
_tok("BATTERY_EXPANDED_CHARGE_KW", kind="cited_constant", value=8.0, fmt="num1",
     source="analysis/battery_dispatch_policies.py's CHARGE_KW_WITH_EXPANSION constant "
            "(issue #40: the PW3 + Expansion config's nameplate CHARGE power; discharge "
            "stays 11.5 kW same as the bare unit, see BATTERY_EXPANDED_MODEL)")
_tok("BATTERY_SAVINGS_BASE", kind="derived",
     get=lambda ctx: _battery_sim_row("1x Tesla Powerwall 3")["net_annual_savings"],
     sources=["data/battery_sim.json"], fmt="usd0_signed")


def _payback_range(ctx):
    """The section 0 card's battery-alone payback span.

    Through _battery_alone -- the SHARED resolver, not a second reading of the
    same package. This token read packages.MID's two payback fields directly
    and sorted them, so it bypassed every consistency check the verdict beside
    it goes through: with a negative battery-alone saving the card printed the
    cost divided by that loss as though it were a length of time, and a card
    reading "~-290.0–6.2 yr" sat a few pixels above a verdict saying the
    battery does not repay (issue #131 review round 4, finding 6).

    It spans the paybacks that EXIST. The two scenarios behind them are the
    battery before and after the free EV-charging fix (see _battery_alone),
    which is what the card's own label describes and why a difference between
    them is the point of printing a span rather than a figure -- but a
    scenario whose saving is not positive contributes no payback at all, and
    the card falls back to naming the one that does.

    IT ALSO READS THE VERDICT'S OWN FLAG, not just the surviving quotients.
    Gating on `quotable` alone put the card back in the state finding 6
    described, one household along: the pair (+$2,328 before the free EV fix,
    -$50 after it) leaves the PRE-fix payback quotable, so the card printed
    "6.2 yr" while the verdict a few pixels above it -- correctly, off the
    post-fix scenario every package is built on -- read "does not repay its
    own cost" (issue #131 review round 5, finding 3). A payback the battery
    does not have is a payback the card must not assert, and whether it has
    one is _battery_alone's decision, made once, on the post-fix scenario.
    `quotable` stays in the test beside it, not because a household can fail
    only that half -- _battery_alone refuses a repaying battery with no
    quotable payback before it ever returns -- but so the emptiness cannot
    reach _payback_span's min() as a ValueError if that ever stops holding.

    The refusal left is a battery the report does not say repays. report-
    template.html's card asserts a payback in fixed markup this module cannot
    reach ("Battery-alone payback with price-aware dispatch"), so there is no
    string to render into it that makes the page true -- the same shape as
    _best_plan's chrome gate, and it goes away the same day that card is made
    conditional."""
    repays, post, _pb_post, quotable = _battery_alone("BATTERY_PAYBACK_RANGE")
    mid = _json("package_results.json")["packages"]["MID"]
    _claim("BATTERY_PAYBACK_RANGE", "how long the battery takes to repay its own cost",
           SUPPORTED if repays and quotable else NOT_DETERMINED,
           "data/package_results.json:packages.MID reports battery-alone savings of " +
           ", ".join(f"{k}={mid[k]!r}/yr" for k, _pb, _lab in _MID_BATTERY_SCENARIOS) +
           f", and the scenario the report's verdicts are written on -- "
           f"battery_alone_post_ev_fix_yr, at {post!r}/yr -- does not repay the battery",
           unsettled="report-template.html's section 0 card asserts a battery-alone "
                     "payback as fixed markup no token can reach, so no value rendered "
                     "into this slot makes the page true")
    return f"{_payback_span(quotable)} yr"


_tok("BATTERY_PAYBACK_RANGE", kind="derived", get=_payback_range,
     sources=["data/package_results.json:packages.MID"])


def _payback_evening_only(ctx):
    """The OTHER half of section 0's payback card: the same battery on an
    evening-only dispatch schedule.

    report-template.html renders the two into one label -- "Battery-alone
    payback with price-aware dispatch ({{BATTERY_PAYBACK_EVENING_ONLY}}
    evening-only)" -- so they are one card making one claim, and round four
    guarded exactly one of them. This one stayed a bare data_json read with
    no check of any kind, which is how "8.4 yr" and "nan yr" were the same
    code path (issue #131 review round 5, finding 5).

    RELATIONSHIP: ONE DERIVED FROM THE OTHER, and traceable the same way
    _battery_alone's paybacks are. analysis/package_results.py writes this
    field as `round(packages.MID.cost / evening, 1)` where `evening` is
    data/battery_dispatch_policies.json's pw3.evening.save -- so all three
    numbers are committed and the division is checkable to the twentieth of a
    year its own rounding moves it.

    It is REFUSED rather than inverted at a non-positive saving for the same
    reason BATTERY_PAYBACK_RANGE is: the card's label asserts a payback in
    fixed markup no token can reach, and a cost divided by a loss is not a
    length of time.
    """
    mid = _json("package_results.json")["packages"]["MID"]
    cost, pb = mid["cost"], mid["battery_alone_payback_evening_only_yr"]
    save = _json("battery_dispatch_policies.json")["pw3"]["evening"]["save"]
    token = "BATTERY_PAYBACK_EVENING_ONLY"
    subject = "how long the battery takes to repay on an evening-only schedule"
    _claim(token, subject,
           SUPPORTED if _finite(cost, save, pb) and cost > 0 and save > 0 and pb > 0
           else NOT_DETERMINED,
           f"data/package_results.json:packages.MID pairs a cost of {cost!r} with an "
           f"evening-only payback of {pb!r} yr, against a pw3.evening.save of {save!r}"
           "/yr in data/battery_dispatch_policies.json -- a payback needs a positive, "
           "finite cost and a positive, finite saving to be a length of time",
           unsettled="report-template.html's section 0 card names an evening-only "
                     "payback in fixed markup no token can reach, so no value rendered "
                     "into this slot makes the page true")
    _require_derived(
        token, subject, cost / save, pb, _TENTH_YEAR_ROUNDING,
        f"data/package_results.json:packages.MID divides its {cost!r} cost by the "
        f"{save!r}/yr evening-only saving in data/battery_dispatch_policies.json:"
        f"pw3.evening.save to {cost / save:.4f} yr, but reports "
        f"battery_alone_payback_evening_only_yr as {pb!r} -- more than rounding apart, "
        "so the two artifacts were composed from different runs")
    return pb


_tok("BATTERY_PAYBACK_EVENING_ONLY", kind="derived", get=_payback_evening_only,
     sources=["data/package_results.json:packages.MID",
              "data/battery_dispatch_policies.json:pw3.evening.save"], fmt="yr1")


def _battery_marginal_savings(ctx):
    """The battery's OWN annual saving, as the report states it.

    THE POST-EV-FIX SCENARIO, through _battery_alone -- not a second read of
    packages.MID. This token used to read battery_alone_yr, the battery on the
    UNSHIFTED baseline, while section 7's verdict had been re-based onto
    battery_alone_post_ev_fix_yr, the battery after the free fix. Two figures
    for one purchase, in one document: on the committed archive they read
    $2,328 and $2,238, and on a household whose battery value is mostly EV
    arbitrage the free fix already captures (+$2,328 before, -$50 after) they
    read a healthy saving and a loss. Round four's sign gate used to catch
    that pair by aborting; removing the gate -- correctly, since the two are
    DIFFERENT SCENARIOS and neither constrains the other -- left the mismatch
    with nothing behind it (issue #131 review round 5, finding 10).

    The fix is not another gate. Two scenarios cannot be made to agree, so the
    report quotes ONE of them, and it is the one every package, every payback
    and both battery verdicts are already built on: the saving that coexists
    with the behavior fix section 7 recommends first. CLAUDE.md section 3 is
    the rule -- when a figure is re-based, every instance moves -- and this
    was the instance left behind.

    Going through _battery_alone rather than re-reading the field is what
    makes that structural: the sentence and the figure now come out of one
    call, so a future re-basing cannot move one without the other.
    """
    _repays, post, _pb_post, _quotable = _battery_alone("BATTERY_MARGINAL_SAVINGS")
    return post


# A MODELED SAVING, so usd0_signed: the battery-alone figure is
# `b_sh - b2`, the battery billed against the EV-shifted year, and comes back
# negative on any household the battery costs money, which fmt="usd0"
# rendered as "$-120" (issue #131 review round 4, finding 8). Identical
# output at every non-negative value. Every sibling below is the same class of
# figure and takes the same formatter -- a modeled saving, a value, an NPV, an
# overlap deduction: quantities whose sign the artifact decides, not the
# schema.
_tok("BATTERY_MARGINAL_SAVINGS", kind="derived", get=_battery_marginal_savings,
     sources=["data/package_results.json:packages.MID.battery_alone_post_ev_fix_yr"],
     fmt="usd0_signed")

def _endurance(token, config):
    """A backup-endurance token's "336 h (p10 90 h)" pair, both hours checked.

    The pair used to be interpolated straight out of the artifact, so a nan
    median published "nan h (p10 90 h)" as an outage-endurance figure a reader
    would plan around (issue #131 review round 5, part B's sweep)."""
    e = _json("backup_endurance.json")[config]
    median, p10 = _figures(token, f"how long the battery runs {config} loads",
                           **{f"{config}_median_h": e["median_h"],
                              f"{config}_p10_h": e["p10_h"]})
    return f"{median} h (p10 {p10} h)"


_tok("ENDURANCE_ESSENTIALS", kind="derived",
     get=lambda ctx: _endurance("ENDURANCE_ESSENTIALS", "PW3|t1"),
     sources=["data/backup_endurance.json:'PW3|t1'"])
_tok("ENDURANCE_HOUSE", kind="derived",
     get=lambda ctx: _endurance("ENDURANCE_HOUSE", "PW3|t2"),
     sources=["data/backup_endurance.json:'PW3|t2'"])
_tok("ESSENTIALS_KWH_DAY", kind="cited_constant", value=17, fmt="num0",
     source="analysis/battery_backup_sims.py's essentials tier cap (t1 = "
            "min(load, 0.7 kW) -> 0.7 x 24h = 16.8 kWh/day, rounds to 17); "
            "data/backup_endurance.json stores the resulting endurance HOURS per "
            "config but not this input cap as its own field")


def _house_kwh_day(ctx):
    # A lighter-weight, fully committed-artifact derivation than re-running
    # battery_backup_sims.py's own SAM-8760 "nonev" heuristic (which needs the
    # private hourly archive and isn't packaged as an importable function):
    # whole-home load minus the year's total detected EV energy, per day.
    # This runs ~2 kWh/day (~4%) below the hand-authored report's own
    # SAM-8760-hourly-heuristic figure (44 vs 46) -- both are legitimate,
    # differently-scoped measurements of the same quantity; the gap is the
    # daily-average-vs-hourly-heuristic methodology difference, not an error.
    load = _annual_load_kwh(ctx)
    ev = _json("behavior_rebuild.json")["detection"]["ev_kwh_total"]
    days = _json("behavior_rebuild.json")["window"]["days"]
    return round((load - ev) / days)


_tok("HOUSE_KWH_DAY", kind="derived", get=_house_kwh_day,
     sources=["data/report_data.json", "data/enphase_daily_production.csv",
              "data/behavior_rebuild.json:detection.ev_kwh_total"], fmt="num0")

_tok("DISCOUNT_RATE", kind="data_json", file="tou_spread.json",
     path=("battery", "discount"), fmt="pct0")


def _pct0_from_fraction(v):
    return f"{round(_numeric('pct0_frac', v) * 100)}%"


FORMATTERS["pct0_frac"] = _pct0_from_fraction
TOKENS["DISCOUNT_RATE"]["fmt"] = "pct0_frac"

_tok("ESCALATION_HISTORICAL", kind="cited_constant", value="8%",
     source="one of the four escalation rungs battery_dispatch_policies.json's "
            "escalation_greedy_pw3_post_behavior tests (3/5/8/12%); chosen as "
            "'recent SDG&E history' because it sits inside this household's own "
            "MEASURED delivery-rate escalation range in data/tou_spread.json "
            "(delivery_cell_escalation: summer on-peak 7.63%/yr, winter on-peak "
            "11.3%/yr, winter off-peak 12.06%/yr)")


def _escalation_rung(key):
    rungs = _json("battery_dispatch_policies.json")["escalation_greedy_pw3_post_behavior"]
    if key not in rungs:
        raise SystemExit(f"report_tokens: escalation rung {key!r} not present in "
                          "battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior")
    return rungs[key]


_tok("PAYBACK_AT_HISTORICAL_ESCALATION", kind="derived",
     get=lambda ctx: _escalation_rung("8%")["payback"],
     sources=["data/battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior['8%']"],
     fmt="yr1")
_tok("NPV_AT_HISTORICAL_ESCALATION", kind="derived",
     # usd0_plus, not an inline "+$": npv10 is a discounted net present
     # value and goes negative on any escalation rung that does not carry the
     # pack, which the hardcoded plus rendered as "+$-3,000".
     get=lambda ctx: _usd0_plus(_escalation_rung('8%')['npv10']),
     sources=["data/battery_dispatch_policies.json:escalation_greedy_pw3_post_behavior['8%']"])


def _spread_observation_count(ctx):
    ts = _json("tou_spread.json")
    rows = _csv_rows("bill_tou_detail.csv")
    n_periods = len({r["period"] for r in rows})
    n_statements = len({r["statement_date"] for r in rows})
    priced, = _figures("SPREAD_OBSERVATION_COUNT",
                       "how many priced cells the spread rests on",
                       rows_priced=ts["inputs"]["rows_priced"])
    return (f"{priced} priced cells across {n_periods} "
            f"billing periods on {n_statements} statements")


_tok("SPREAD_OBSERVATION_COUNT", kind="derived", get=_spread_observation_count,
     sources=["data/tou_spread.json:inputs", "data/bill_tou_detail.csv"])

_tok("CARBON_SAMPLE_DESCRIPTION", kind="derived",
     get=lambda ctx: (lambda cov, covered: f"{covered} of the analysis year's "
                       f"{covered + len(cov['missing_dates'])} days")(
         _json("carbon_fullyear_results.json")["coverage"],
         *_figures("CARBON_SAMPLE_DESCRIPTION", "how much of the year CAISO data covers",
                   days_covered=_json("carbon_fullyear_results.json")
                   ["coverage"]["days_covered"])),
     sources=["data/carbon_fullyear_results.json:coverage"])


# ---- plans -------------------------------------------------------------
def _plan_row(provider):
    for r in _csv_rows("plan_results.csv"):
        if r["plan"] == hh1("household.plan") and r["provider"] == provider:
            return r
    raise SystemExit(f"report_tokens: no plan_results.csv row for "
                      f"{hh1('household.plan')!r}/{provider!r}")


def _join_plan_names(names):
    """"A", "A and B", "A, B and C" -- plan names read as prose."""
    names = list(names)
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _plan_ranking(ctx, token):
    """(plan, provider, plan_total, cheapest_total, winners): the household's
    own plan, the generation provider whose column ranks, what that column
    charges for the household's plan, the cheapest total in it, and every plan
    tying for that cheapest total -- all off data/plan_results.csv.

    ONE helper, every caller: sections 0 and 3 both tell the reader something
    about this house's tariff, and two independent rankings of the same CSV
    can drift apart over what "cheapest" means -- which provider column ranks,
    whether a tie counts as a win, whether the household's plan even appears.

    It RANKS; it does not refuse. This helper used to raise SystemExit unless
    the household's plan was the unique cheapest, which took the WHOLE report
    down for a household on a non-optimal tariff -- exactly the household
    section 3 exists to help, and exactly the reader who most needs the
    section. Both sentences now invert on the ranking instead. The two
    SystemExits left are the cases where there is no ranking to report at all:
    no priced rows for the household's provider, and no priced row for the
    household's own plan. Neither sentence can quote a figure that does not
    exist, which is the one thing failing closed is still for.
    """
    plan = hh1("household.plan")
    provider = _generation_provider_short(ctx)
    rows = [r for r in _csv_rows("plan_results.csv") if r["provider"] == provider]
    if not rows:
        raise SystemExit(f"report_tokens: {token} cannot rank plans -- "
                          f"data/plan_results.csv has no rows for the household's "
                          f"generation provider {provider!r}")
    totals = {r["plan"]: float(r["total"]) for r in rows}
    if plan not in totals:
        raise SystemExit(
            f"report_tokens: {token} cannot price the household's own plan -- "
            f"data/plan_results.csv's {provider} column prices {sorted(totals)}, "
            f"which does not include {plan!r}")
    # RELATIONSHIP: SAME QUANTITY, per plan, from ONE engine (analyze_norelief.py
    # writes every row of this column), so a difference between plans is the
    # ranking itself and nothing here gates on their agreement. What DOES have
    # to hold is that they are numbers: a nan total makes `t == cheapest` False
    # for EVERY row -- including its own -- so `winners` came back EMPTY, S0
    # published "a cheaper rate plan exists" off a non-finite input, and
    # BEST_PLAN died several tokens later with a bare IndexError instead of this
    # module's named refusal (issue #131 review round 4, finding 4). An infinity
    # is the mirror image: it never wins, but it makes min() meaningless the
    # moment two of them are present. Checked here, once, for every caller --
    # the non-finite sweep reached this module's other comparisons and skipped
    # the ranking that feeds three of its sentences.
    #
    # Not _require_finite: plan names carry hyphens, so they cannot be keyword
    # arguments, and naming the plan is the whole value of the message.
    bad = {p: t for p, t in totals.items() if not _finite(t)}
    _claim(token, "which rate plan is cheapest",
           SUPPORTED if not bad else NOT_DETERMINED,
           f"data/plan_results.csv's {provider} column prices " +
           ", ".join(f"{p} at {t!r}/yr" for p, t in sorted((bad or totals).items())))
    cheapest = min(totals.values())
    winners = sorted(p for p, t in totals.items() if t == cheapest)
    return plan, provider, totals[plan], cheapest, winners


# The three states this household's plan can be in, in the ranking section 3
# publishes. Named once because FIVE things branch on it -- section 0's
# verdict and its plan card, section 3's verdict, section 3's household row
# and the lead-in over the paragraph explaining that row -- and a reader meets
# all five on one page. Two of them disagreeing is not cosmetic drift: it is a
# card calling this plan the best a few hundred pixels above a sentence saying
# it is not, which is the contradiction issue #196 exists to remove.
_PLAN_STANDINGS = ("win", "tie", "trails")


def _plan_standing(ctx, token):
    """(standing, plan, plan_total, cheapest, winners): _plan_ranking with its
    three-way outcome NAMED here instead of re-derived at each caller.

    Every caller used to write `winners == [plan]` / `plan in winners` out for
    itself. That is one comparison, but it is also the whole of what section
    0's card, section 3's row, section 3's lead-in and both verdict sentences
    claim, and five copies of a rule are five chances for one of them to
    answer a tie differently from its neighbour. One helper, one answer.
    """
    plan, _provider, plan_total, cheapest, winners = _plan_ranking(ctx, token)
    standing = "win" if winners == [plan] else "tie" if plan in winners else "trails"
    return standing, plan, plan_total, cheapest, winners


def _best_plan(ctx, token):
    """The household's own plan -- at every standing, since issue #196.

    WHAT THIS USED TO REFUSE, AND WHY IT NO LONGER HAS TO. report-template.html
    did not ASK whether this plan wins; it ASSERTED it, in fixed markup no
    token could reach: a section 0 card reading "Best plan in every scenario",
    the `<tr class="win">` row carrying {{BEST_PLAN}} in section 3, and the
    running line "Why {{BEST_PLAN}} wins:". So this token failed closed for a
    household whose plan is not cheapest -- naming the chrome as the reason --
    and since generate_report.py folds any refusal into `failures`, that
    household got NO REPORT AT ALL over its own plan's name and two of its own
    modeled bills. Section 4's row was the same defect and issue #178 gave it
    a token; these three slots were the rest of it.

    All three are conditional now, and each takes its state from
    _plan_standing, the same helper both verdict sentences branch on:
    S3_ROW_CLASS paints the row (and badges it where the plan is not alone
    cheapest), S3_WHY_LEAD writes the lead-in over the paragraph that explains
    the ranking, and S0_BEST_PLAN_CARD writes the card's own label. There is
    no sentence left for a value here to contradict, so there is nothing left
    to gate: the answer to "is this the best plan" is written beside the name,
    in the chrome, at whatever the ranking says.

    THE RANKING CALL STAYS, and it is not vestigial. _plan_ranking is what
    establishes that data/plan_results.csv prices this household's plan at all
    and that the column it prices it in is made of numbers -- the two cases
    where BEST_PLAN_ANNUAL_* would quote a figure off a ranking that does not
    exist, and where a nan would otherwise reach this row as a bare
    IndexError several tokens later.
    """
    return _plan_ranking(ctx, token)[0]


def _best_plan_annual(token, provider):
    """A BEST_PLAN_ANNUAL_* total: the two money cells of section 3's
    household row.

    Still routed through _best_plan, which is now the ranking's own
    validity check rather than a chrome gate (see its docstring): these cells
    may not quote a figure out of a column that cannot be ranked."""
    _best_plan(CTX, token)
    return float(_plan_row(provider)["total"])


# usd0_signed for the reason ACTUAL_ANNUAL_BILL takes it: a plan's annual
# total is a bill net of exports, not a price, and nothing in
# data/plan_results.csv holds it above zero.
_tok("BEST_PLAN_ANNUAL_CCA", kind="derived",
     get=lambda ctx: _best_plan_annual("BEST_PLAN_ANNUAL_CCA", "CEA"),
     sources=["data/plan_results.csv"], fmt="usd0_signed")
_tok("BEST_PLAN_ANNUAL_BUNDLED", kind="derived",
     get=lambda ctx: _best_plan_annual("BEST_PLAN_ANNUAL_BUNDLED", "SDGE"),
     sources=["data/plan_results.csv"], fmt="usd0_signed")


# ---- section 3's household row, and section 0's plan card (issue #196) -----
#
# ONE CLASS PER STANDING, and the sole-cheapest state IS "win" -- the class
# section 3's row has always carried, painted by the same `tr.win td` rule.
# The other two need names of their own because report-template.html's `tie`
# and `trails` badges are SECTION 4's: they count the battery matrix's two
# columns ("tied for cheapest in both columns"), and section 3's table has no
# battery columns to count. Reusing them would put a false badge on the row --
# the exact defect issue #178's pair-of-standings class was written to avoid,
# one table over.
#
# WHAT SECTION 3's BADGES SAY INSTEAD names the column this ranking is over:
# the household's own generation provider, which is the column _plan_ranking
# ranks and the one this house actually pays. The bundled column beside it is
# priced but not ranked, and no badge here claims anything about it.
_S3_ROW_CLASS_BY_STANDING = {"win": "win", "tie": "s3-tie", "trails": "s3-trails"}
_S3_ROW_CLASSES = tuple(_S3_ROW_CLASS_BY_STANDING[s] for s in _PLAN_STANDINGS)


def _s3_row_class(ctx):
    """The CSS class on section 3's household row: one of _S3_ROW_CLASSES.

    The row opened as a fixed `<tr class="win">` until issue #196, so the
    three cells inside it -- this plan's name and its two annual totals --
    refused rather than render figures the row around them contradicted, and
    a household the CSV ranks second got no report at all. The claim has a
    token now, so it comes out true, false or tied, and the report is written
    either way.

    Identity on the STORED totals, through _plan_ranking's `==`/min(): a
    stored total above the minimum is a strictly dearer bill, and equal
    totals are a tie. No band -- widening "cheapest" by a dollar is what puts
    a runner-up into the winners' set and paints it as the winner (issue #141;
    see the rounding derivation further down).
    """
    return _S3_ROW_CLASS_BY_STANDING[_plan_standing(ctx, "S3_ROW_CLASS")[0]]


# fmt="raw", attribute_only=True: this value is markup, not language -- the
# same declaration S4_ROW_CLASS carries, for the same two reasons. See
# is_attribute_only() for what the flag does, and
# test_report_tokens.case_section_3s_row_class_is_a_state_the_stylesheet_can_paint
# for the guard an attribute value actually needs: that every state it can
# reach is a class report-template.html's own <style> block paints, and badges
# with a claim the ranking supports.
_tok("S3_ROW_CLASS", kind="derived", get=_s3_row_class, fmt="raw",
     attribute_only=True,
     sources=["data/plan_results.csv (the household provider's total column)",
              "private/household.yaml:household.plan",
              "private/household.yaml:household.cca (which provider column ranks)"])


def _s3_why_lead(ctx):
    """The bold lead-in over section 3's explanatory paragraph.

    It read "Why {{BEST_PLAN}} wins:" as fixed text, which is a claim -- and
    for a household whose plan does not win, a claim contradicted by the
    verdict line at the top of the same section. The lead-in now says which
    question the paragraph under it answers, which for a beaten household is
    "why the other plan wins", not "why yours does".
    """
    standing, plan, _plan_total, _cheapest, winners = _plan_standing(ctx, "S3_WHY_LEAD")
    if standing == "win":
        return f"Why {plan} wins:"
    if standing == "tie":
        return f"Why {plan} ties {_join_plan_names([p for p in winners if p != plan])}:"
    return (f"Why {_join_plan_names(winners)} "
            f"{'wins' if len(winners) == 1 else 'win'} instead:")


_tok("S3_WHY_LEAD", kind="derived", get=_s3_why_lead,
     sources=["data/plan_results.csv (the household provider's total column)",
              "private/household.yaml:household.plan",
              "private/household.yaml:household.cca (which provider column ranks)"])


# ---- section 7's package footing (issue #196) ------------------------------
#
# THE LAST SENTENCE IN THIS FAMILY THAT STATED NO STANDING. Section 7 opens the
# decision with "All packages keep <plan>", and that clause is TRUE of every
# household: data/package_results.json models all three packages with the house
# on the plan it is already on, and nothing committed prices any of them on a
# different tariff. What it does not say is that this is a MODELLING BASIS. In
# a section headed "The decision", under three package cards and a
# recommendation, "keep" reads as advice -- and for a household the ranking
# beats, the reader has just been told in section 0 that a cheaper plan exists
# and in section 3 by how much, and is then handed three packages priced on the
# losing tariff with nothing saying the two are on different footings.
#
# WHAT THIS TOKEN SAYS AND WHAT IT DELIBERATELY DOES NOT. It states the
# footing: whose plan the packages hold, whether the ranking beats it, and --
# when it does -- that switching is not inside any saving below. It does not
# price the switch. Re-basing the packages onto another plan is a different
# analysis with its own artifacts and its own unresolved baseline question (the
# savings below are deltas against ACTUAL BILLS, which were billed on this
# plan, so re-basing changes what the baseline means -- CLAUDE.md section 1's
# one-rate-vintage rule), and it is issue #200's, not this one's. No figure
# either: the gap in dollars is section 3's to state, and S3_VERDICT states it.
#
# WHY A SLOT AFTER THE PLAN NAME RATHER THAN A REWRITTEN CLAUSE IN FRONT OF IT.
# generate_report.render() HTML-escapes every token value, so no token can emit
# the <b> around {{BEST_PLAN}}; the bold plan name is fixed markup and pins the
# sentence's shape. A token in front of it could swap the verb and nothing
# else -- it could carry neither the pointer to section 3 nor the
# not-included-here clause, which is the whole substance. A token after it
# carries both AND qualifies the verb in the same breath, in one slot instead
# of two.
#
# WHY IT NAMES SECTION 3 BY ITS HEADING INSTEAD OF WRITING THE SIGIL. The same
# escaping: CLAUDE.md section 10 requires every "section N" reference in report
# prose to be a real <a href="#sN"> link, and an escaped token value cannot
# carry one. The sentence points at section 3 by that section's own heading
# ("Rate plan comparison"), which needs no sigil and no link.
#
# NO APOSTROPHE IN ANY BRANCH, for the third consequence of the same escaping:
# render() escapes with quote=True, so a possessive would reach the published
# HTML as "house&#x27;s". It displays correctly and reads as a defect in the
# source of a page whose whole point is that a reader can check it.
#
# THE PERIOD IS THE TOKEN'S, and that is what keeps the winning household's
# published page character-for-character what it was: at "win" this value IS
# ".", so the rendered sentence is the "All packages keep <plan>." index.html
# already carries. See
# test_report_tokens.case_section_7s_package_footing_states_the_plan_it_prices_on.
def _s7_plan_footing(ctx):
    """The clause closing section 7's "All packages keep <plan>" sentence: the
    footing the three packages below are priced on.

    Three states, off _plan_standing -- the same helper section 0's verdict and
    its card, section 3's verdict, section 3's row and section 3's lead-in all
    branch on, so this sentence cannot tell the reader a different story from
    the four statements above it. A household whose plan is beaten reads "not
    the cheapest one" here and "a cheaper rate plan exists" in section 0; a
    household whose plan wins reads neither, because there is nothing to
    disclose.
    """
    standing, plan, _plan_total, _cheapest, winners = _plan_standing(
        ctx, "S7_PLAN_FOOTING")
    if standing == "win":
        return "."
    if standing == "tie":
        others = _join_plan_names([p for p in winners if p != plan])
        return (f" — the plan this house is on, level with {others} at the cheapest "
                "modeled total in the rate plan comparison above.")
    # AGREEMENT ON THE WINNERS' SET, in BOTH halves of the sentence. The verb
    # takes it the same way S3_VERDICT does -- two plans tied ahead of this one
    # are "each price lower", not "prices lower" -- and so does the pronoun the
    # closing clause points back at them with. It said "switching to it" in
    # every branch, so a household beaten by two tied plans read "TOU-DR-P and
    # TOU-DR-1 each price lower ..., and none of the savings below includes
    # switching to it": a singular referent for a subject the same sentence has
    # just made plural, leaving the reader to guess which of the two it means.
    plural = len(winners) > 1
    verb = "each price" if plural else "prices"
    switch_to = "any of them" if plural else "it"
    return (" — the plan this house is on, not the cheapest one. "
            f"{_join_plan_names(winners)} {verb} lower in the rate plan comparison "
            f"above, and none of the savings below includes switching to {switch_to}.")


_tok("S7_PLAN_FOOTING", kind="derived", get=_s7_plan_footing,
     sources=["data/plan_results.csv (the household provider's total column)",
              "private/household.yaml:household.plan",
              "private/household.yaml:household.cca (which provider column ranks)"])


# data/deep_results.json:wildcard's keys are PROSE ("TOU-DR-P + PW3 (15 events
# dodged)", "EV-TOU-5 no battery"), so the plan name is the leading run before
# the "+ <battery>" or "no battery" qualifier. ONE regex, read here and nowhere
# else, because the card and section 9's heading both name "the wildcard plan"
# and a second split drifts from the first silently.
#
# IT ALREADY HAD. This split used to be written twice: here without the
# battery's name and in _wildcard_plan as r"\s*\+\s*PW3|\s+no battery", with
# this household's battery hardcoded into the second copy. Both agree on the
# keys THIS checkout happens to carry and part company on any other battery: a
# workup labelled "Powerwall 3" rather than "PW3" left the card saying
# "TOU-DR-P wildcard" while WILDCARD_PLAN resolved to the whole prose key, so
# section 9's heading read "can TOU-DR-P + Powerwall 3 (15 events dodged) + a
# battery beat EV-TOU-5?" beside a section 0 card naming something else. A
# hardcoded product name is not a parse rule; the qualifier is whatever follows
# the "+".
_WILDCARD_KEY_QUALIFIER_RE = re.compile(r"\s*\+|\s+no battery")


def _wildcard_key_plan(key):
    """The plan name one data/deep_results.json:wildcard key is about."""
    return _WILDCARD_KEY_QUALIFIER_RE.split(key)[0].strip()


def _wildcard_totals():
    """{plan: [modeled annual totals]} off data/deep_results.json:wildcard,
    keyed by _wildcard_key_plan -- the one split every reader of that artifact
    takes, so the card and the section 9 heading cannot disagree about which
    plan the wildcard is about.
    """
    totals = {}
    for key, value in _json("deep_results.json")["wildcard"].items():
        totals.setdefault(_wildcard_key_plan(key), []).append(value)
    return totals


def _wildcard_rivals(plan):
    """Every plan the wildcard workup prices OTHER than `plan`, sorted.

    THE SHARED FACT behind both sentences that name the wildcard: section 0's
    card names these plans in its parenthetical and section 9's heading asks
    whether the first of them can beat this house's plan. One list, one order,
    so the two cannot name different plans off the same artifact.
    """
    return sorted(name for name in _wildcard_totals() if name != plan)


def _wildcard_scenario(ctx):
    """(phrase, standing) for the section 9 wildcard, or None when that
    artifact cannot rank this household's plan against another.

    None DROPS the scenario from the card's list rather than refusing: the
    card's claim is "in every scenario tested", and a scenario that cannot be
    ranked was not tested. Refusing would take the whole report down for a
    household whose wildcard workup prices one plan -- the failure mode this
    issue is about.

    A NON-FINITE TOTAL DROPS THE WHOLE SCENARIO, and it used to be filtered
    out of the ranking instead -- `[t for t in ... if _finite(t)]` on our side
    and `any(_finite(t) ...)` on the rivals'. A plan with a nan beside a
    finite figure kept its finite figure, the min() was taken over what was
    left, and the card went on counting the wildcard as a scenario it scored:
    "Best plan in every scenario tested (..., TOU-DR-P wildcard)" over a
    comparison one of whose sides the artifact never priced. The value that
    was discarded is exactly the one that could have beaten this plan, so the
    filter does not merely lose precision -- it can invert the standing, and
    it does so silently, which is the same shape as the mixed-matrix finding
    one input along.

    EVERY total in the artifact is required, not just the two min() lands on:
    `theirs` is a minimum over every rival total and `ours` over every one of
    ours, so any one of them can move the answer, and a rival whose totals are
    ALL non-finite was dropped out of `rivals` entirely -- the same partial
    ranking, one step further along, and it also decides whether there is a
    rival to name at all. One rule instead of three: an artifact that is not
    made of numbers ranks nothing here.

    DROP RATHER THAN REFUSE, which is the opposite of what _bpm_cheapest and
    _plan_ranking do with a non-finite cell, and for a reason that is about
    the SENTENCE rather than about the number. Those two rank the CSV and the
    matrix, and section 3's verdict, section 3's row, section 4's row class
    and section 7's footing all have to say something about that ranking --
    there is no "this ranking is absent" state for them to land in, so the
    only honest answer is _claim's NOT_DETERMINED and a named SystemExit. The
    wildcard has that state already, and this function returns it three lines
    up for a household whose workup prices one plan. Nothing else reads these
    totals either: WILDCARD_PLAN parses the artifact's KEYS, and section 9's
    wildcard paragraph is a <!-- TODO --> block in report-template.html, so no
    token publishes a figure off them. A wildcard the artifact cannot rank is
    "this scenario is not determined", not "the report cannot be written" --
    CLAUDE.md section 0's two different answers -- and the card is already
    built to say the first by naming one scenario fewer.
    """
    plan = hh1("household.plan")
    totals = _wildcard_totals()
    if not _finite(*(t for values in totals.values() for t in values)):
        return None
    ours = totals.get(plan, [])
    rivals = _wildcard_rivals(plan)
    if not ours or not rivals:
        return None
    theirs = min(t for name in rivals for t in totals[name])
    ours = min(ours)
    standing = "win" if ours < theirs else "tie" if ours == theirs else "trails"
    # SLASH-JOINED, never _join_plan_names, and this is about the CARD's
    # punctuation rather than about prose. _plan_card_label lists the scenarios
    # it scored in a parenthetical joined with ", ", and _join_plan_names emits
    # ", " itself from three names up. Three rivals therefore published
    # "Cheapest in 2 of the 3 scenarios tested (no-battery, battery×plan
    # matrix, TOU-DR-1, TOU-DR-2 and TOU-DR-P wildcard)" -- a parenthetical
    # reading as five items beside a sentence counting three, and the count is
    # the claim. A scenario phrase has to be ONE item, so the rivals are joined
    # with a separator the list cannot mistake for its own. One name renders
    # exactly as before.
    return f"{'/'.join(rivals)} wildcard", standing


# The one clause that keeps section 0's card honest about a SPLIT battery
# matrix, and the reason it is a clause rather than a fourth scenario.
#
# THE DEFECT IT EXISTS TO REMOVE. The card scored the matrix as ONE scenario
# taken at the WORSE of its two columns, and three different households came
# out as one sentence:
#
#     loses no-batt, WINS with-batt   ->  "Cheapest in 2 of the 3 ... beaten in the rest"
#     WINS no-batt, loses with-batt   ->  the same sentence
#     loses BOTH columns              ->  the same sentence again
#
# So a household the matrix ranks cheapest in one of its two columns was
# published as having lost that scenario outright, indistinguishable from one
# that lost both. That is issue #178's finding one section along: the weaker of
# two columns is RIGHT for a row's CSS class, whose badge may say only what
# BOTH columns support, and WRONG for a sentence, which is all this card is.
#
# WHY NOT SCORE THE TWO COLUMNS AS TWO SCENARIOS (a count out of four). It is
# the tidier shape and it was written first, but the card's label is pinned
# CHARACTER FOR CHARACTER to the one index.html publishes
# (test_report_tokens.case_section_3s_published_chrome_round_trips_into_index
# _html), and naming two matrix columns in the parenthetical rewrites that
# label for the winning household too -- a silent rewrite of a published page,
# on the one path this issue is not about. Counting rankings the parenthetical
# does not name would be worse: the card would claim tests it does not list.
# So the matrix stays ONE named scenario, scored at the standing the matrix as
# a whole supports (cheapest in one of two columns is not cheapest in the
# matrix -- the same rule section 4's row applies), and the label carves the
# won column out of its own absolutes with this clause. The winning path never
# reaches it: a split pair needs a trailing column, and every branch above the
# clause requires none.
def _matrix_split_clause(worst, strongest):
    """The exception clause for a matrix whose two columns disagree, or "".

    Only a pair that TRAILS in one column and is cheapest in the other needs
    it. A win/tie pair is counted as "tie" and the tie branches already say
    "level with a rival", which is true of the column that ties and understates
    nothing the reader is owed; a pair that agrees has no exception to carve.
    """
    if worst != "trails" or strongest == "trails":
        return ""
    return (", except in one of the battery×plan matrix's two columns, where it "
            + ("is the cheapest plan" if strongest == "win" else "ties for cheapest"))


def _plan_card_label(csv_standing, matrix_pair, wildcard):
    """Section 0's card label, FROM STANDINGS ALONE -- no artifact reads, so
    the whole product of standings that can reach it is enumerable against the
    English it produces (test_report_tokens.case_section_0s_card_is_true_of_
    every_ranking_it_can_be_handed walks all 108 of them).

    csv_standing:  data/plan_results.csv's ranking of this household's plan.
    matrix_pair:   data/battery_plan_matrix.json's two columns, (worst,
                   strongest), sorted by _bpm_standing_pair.
    wildcard:      (phrase, standing), or None when deep_results.json cannot
                   rank this plan against another -- it prices one plan, or
                   it carries a total that is not a finite number -- in which
                   case the scenario is DROPPED rather than counted, since a
                   ranking that could not be taken was not a test.

    The label states the strongest true summary and nothing above it: sole
    cheapest everywhere is "Best plan in every scenario tested"; a tie
    somewhere drops "best" for "cheapest ... level with a rival"; anything
    beaten counts instead of quantifying, and section 3's plan table has the
    ranking that produced the count. Every branch is a claim about HOW MANY of
    the scenarios NAMED in the parenthetical price this plan cheapest AND how
    many of those are ties, plus _matrix_split_clause's exception where the
    matrix is half won.

    A TIE IS NEVER LET READ AS A SOLE WIN, in any branch. The every-scenario
    branch refuses to say "Best plan" once anything ties and quantifies the
    ties instead; the partial branch used to print a bare count and did not,
    so a plan tying in two scenarios and beaten in a third published "Cheapest
    in 2 of the 3 scenarios tested (...) — beaten in the rest", asserting sole
    cheapest in two scenarios it only drew. The same silence made two
    different households one sentence: cheapest outright in two scenarios, and
    cheapest in one with a tie in another, were byte-identical labels. The tie
    count is stated wherever there is one to state. It cannot arise in the
    last branch -- a tie IS a cheapest standing, so nothing counted cheapest
    means nothing tied -- and the guard checks that rather than assuming it
    (test_report_tokens.case_section_0s_card_is_true_of_every_ranking_it_can_
    be_handed reads a tie count out of all four branches).
    """
    worst, strongest = matrix_pair
    scenarios = [("no-battery", csv_standing), ("battery×plan matrix", worst)]
    if wildcard:
        scenarios.append(wildcard)
    split = _matrix_split_clause(worst, strongest)
    tested = ", ".join(phrase for phrase, _standing in scenarios)
    standings = [standing for _phrase, standing in scenarios]
    total, cheapest_in = len(standings), sum(s != "trails" for s in standings)
    tied_in = sum(s == "tie" for s in standings)
    if cheapest_in == total and not tied_in:
        return f"Best plan in every scenario tested ({tested}) — the solid conclusion"
    if cheapest_in == total:
        return (f"Cheapest plan in every scenario tested ({tested}), level with a rival "
                f"in {tied_in} of the {total} — nothing priced beats it")
    if cheapest_in:
        level = (f", level with a rival in {tied_in} of those" if tied_in else "")
        return (f"Cheapest in {cheapest_in} of the {total} scenarios tested ({tested})"
                f"{level} — beaten in the rest{split}")
    return (f"Not the cheapest in any of the {total} scenarios tested ({tested}) "
            f"— a cheaper plan exists in each{split}")


def _s0_best_plan_card(ctx):
    """Section 0's plan card label -- the card whose fixed text read "Best plan
    in every scenario — the solid conclusion".

    "IN EVERY SCENARIO" IS A QUANTIFIER, and it was written as fixed markup
    over three rankings nothing checked. They are read now: the no-battery
    ranking (data/plan_results.csv, the same one both verdict sentences use),
    the battery×plan matrix (data/battery_plan_matrix.json -- BOTH of its
    columns, through _bpm_standing_pair, with a half-won matrix carved out of
    the label's absolutes rather than collapsed into a loss; see
    _matrix_split_clause), and section 9's wildcard workup
    (data/deep_results.json:wildcard). The scenarios NAMED in the label are
    exactly the ones scored, so the parenthetical cannot claim a test that did
    not happen.

    It adds no failure mode of its own. The two refusals it can inherit --
    a matrix that does not price this plan, a column that is not made of
    numbers -- belong to _bpm_best/_bpm_cheapest and _plan_ranking, all of
    which report-template.html already resolves for section 3 and section 4.
    """
    return _plan_card_label(_plan_standing(ctx, "S0_BEST_PLAN_CARD")[0],
                            _bpm_standing_pair("S0_BEST_PLAN_CARD"),
                            _wildcard_scenario(ctx))


_tok("S0_BEST_PLAN_CARD", kind="derived", get=_s0_best_plan_card,
     sources=["data/plan_results.csv (the household provider's total column)",
              "data/battery_plan_matrix.json:plans (both columns, via "
              "_bpm_standing_pair)",
              "data/deep_results.json:wildcard",
              "private/household.yaml:household.plan",
              "private/household.yaml:household.cca (which provider column ranks)"])


def _bpm_plans():
    return _json("battery_plan_matrix.json")["plans"]


def _bpm_best():
    plans = _bpm_plans()
    plan = hh1("household.plan")
    if plan not in plans:
        raise SystemExit(f"report_tokens: household plan {plan!r} not present in "
                          "battery_plan_matrix.json:plans")
    return plan, plans[plan]


_BPM_COLUMNS = (("no_battery", "without a battery"),
                ("with_battery", "with one battery"))


# ---------------------------------------------------------------------------
# THE WHOLE-DOLLAR ROUNDING: WHAT IT SETTLES, AND WHAT IT ONLY BLURS
# (issue #141 adversarial review).
#
# THE GENERATOR FIRST, per the rule this module keeps re-learning: never
# compare two artifact fields without opening the script that writes both and
# recording WHICH relationship they are in. analysis/battery_plan_matrix.py
# bills one year, from one 15-minute trace, through one function (`bill_plan`)
# for every plan it prices. Two cells of the same column differ only in the
# plan's rate table; the two columns differ only in whether the dispatched
# battery trace or the raw one was billed.
#
#   RELATIONSHIP: the SAME QUANTITY, INDEPENDENTLY COMPUTED per plan. Neither
#   cell is derived from the other, neither is clamped, tuned or fitted to
#   this household, and no cell is a scenario the others are measured against.
#   So a difference between two cells in one column is a real difference of
#   two modeled bills -- with exactly one shared distortion, the last line of
#   the generator's loop:
#
#       plans[plan] = {"no_battery": round(no_b), "with_battery": round(with_b),
#                      "battery_value": round(no_b - with_b)}
#
# Every cell in data/battery_plan_matrix.json is therefore a modeled bill
# ROUNDED TO WHOLE DOLLARS. That rounding costs the cells their cents, and it
# is tempting to read that as costing them their ranking too. It does not, and
# the difference between the two is what the constants below are for.
#
# ORDER IS DETERMINED, exactly. round() is NON-DECREASING, so
# round(x) > round(y) implies x > y -- with no exception at the half-dollar,
# where Python rounds to even. If one cell stores 101 and another 100, the
# first bill lay in (100.5, 101.5) and the second in [99.5, 100.5], so the
# first was STRICTLY the dearer. Whichever plan a column stores lowest really
# is that column's cheapest plan, and two cells storing the same number are a
# real tie -- two bills the published artifact cannot separate.
#
# MAGNITUDE IS NOT. |stored - modeled| <= $0.50 per cell, so a DIFFERENCE of
# two cells carries twice that:
#
#       |(c_b - c_a) - (t_b - t_a)|  <=  $0.50 + $0.50  =  $1.00
#
# A stored $1 gap is a real gap of anywhere from about a cent to just under
# $2 (the $2 end needs both cells exactly on a half-dollar rounded the same
# way, which two cells a dollar apart cannot be). So "B is cheaper than A" is
# exact and "B is $1/yr cheaper than A" is not, and a sentence that SIZES a
# lead has to hedge where a sentence that merely names its holder does not.
#
# Hence, and this is the ONLY thing these constants are for: identity -- who
# is cheapest, which plans tie for it, whether that set changes with a
# battery -- is decided on the stored values themselves, with `==` and
# `min()`. The band applies where a sentence quotes or compares a SIZE.
#
# The band was briefly applied to identity as well ("cheapest" = every plan
# within $1.00 of the minimum). That is the error this comment exists to stop
# being repeated: it admitted a plan the artifact ranks SECOND into `winners`
# below, and section 4 rendered those cells inside a fixed class="win" row, so
# a household would have been shown a runner-up as its winner. That row's class
# is a token now (_s4_row_class), which changes nothing here: the token decides
# each column's standing on this same identity, so a band read into it would
# still paint a runner-up as the winner -- one branch further along. Rounding
# never licensed that. It licenses only the hedged wording of a size.
#
# PER COLUMN, never across columns. The bound above is for two cells rounded
# the same way inside one column; the no-battery and with-battery columns are
# different bills and nothing here compares one against the other.

# The most a stored DIFFERENCE of two cells in one column can be out by:
# $0.50 + $0.50. A SIZE bound, never a tie-break -- which cell is smaller is
# settled by the stored values themselves (monotonicity, above), so nothing
# that decides WHO leads may test against this.
_BPM_TIE_USD = 1.0

# The same arithmetic one level up, for a claim about a GAP BETWEEN GAPS: the
# widens/narrows verb compares (runner-up - this plan) without a battery
# against the same difference with one, so four rounded cells, four times the
# per-cell error. Monotonicity buys nothing here -- a difference of two
# differences is not one rounded value against another -- so unlike a lead,
# a gap CHANGE has neither its size nor its direction settled below $2.00,
# and this bound gates the verb itself.
_BPM_GAP_TIE_USD = 4 * 0.5   # == 2 * _BPM_TIE_USD


def _best_plan_matrix_cell(token, key):
    """A BEST_PLAN_*_MODELED / BATTERY_VALUE_BEST_PLAN figure: one cell of the
    household's own row in data/battery_plan_matrix.json.

    THE CHROME GATE THAT USED TO STAND HERE IS GONE (issue #178), because the
    chrome it was compensating for is gone. These three tokens are the cells
    of section 4's household row, and report-template.html used to open that
    row as a fixed `<tr class="win">` -- markup ASSERTING this household is
    on the plan the matrix prices cheapest, in a form no token could reach.
    No figure rendered into a cell could make that assertion true, so the
    three tokens refused whenever the matrix ranked this household second,
    PER COLUMN. A refusal is a token-resolution failure, which stops
    generate_report.py before it writes any index.html at all, so a household
    second in EITHER column got no report -- precisely the household section
    4 exists to help, and the trade the old docstring here recorded as
    deliberate while naming the template as the place to fix it.

    The row now opens `<tr class="{{S4_ROW_CLASS}}">`, and that token states
    both columns' standings off the same two columns (see _s4_row_class).
    There is no false sentence left for these figures to be rendered into: a
    runner-up's modeled bills are as real as a winner's, and showing them
    beside its standing is what the row is for. So they render.

    WHAT STILL REFUSES, because each of these is still true and none of them
    is about the chrome:

      * a household plan the matrix does not price, and a matrix with no
        plans in it at all -- both from _bpm_best(), which cannot return a
        row that is not there;
      * THIS TOKEN'S OWN CELL being non-finite, checked below by name.

    What is NOT checked here any more is the rest of the COLUMN. Ranking the
    columns is now S4_ROW_CLASS's work and it refuses a column that is not
    made of numbers (through _bpm_cheapest), so a matrix with a nan in a
    rival's cell still fails the report closed -- at the token that reads
    that cell, rather than at three tokens that do not. Each token guards
    what it prints.
    """
    plan, row = _bpm_best()
    # THE CELL THIS TOKEN ACTUALLY PRINTS, checked by name.
    #
    # This check used to sit after a loop that verified six OTHER numbers,
    # and BATTERY_VALUE_BEST_PLAN went out past it unchecked: `battery_value`
    # is a third field, in neither ranked column, so the loop's finiteness
    # test never covered it (issue #131 review round 5, finding 2). A guard
    # that checks its neighbours and not its own return value is the shape
    # that review kept finding, which is why this one is anchored on `key` --
    # the field the token returns -- and not on a column that happens to
    # contain it.
    cell = row[key]
    _claim(token, f"what data/battery_plan_matrix.json prices {plan}'s {key} at",
           SUPPORTED if _finite(cell) else NOT_DETERMINED,
           f"data/battery_plan_matrix.json:plans.{plan}.{key} is {cell!r}")
    return cell


# usd0_signed, same reason again: both cells are modeled annual BILLS under
# a plan, and a battery-plus-solar house on the right tariff can model below
# zero.
_tok("BEST_PLAN_NOBATT_MODELED", kind="derived",
     get=lambda ctx: _best_plan_matrix_cell("BEST_PLAN_NOBATT_MODELED", "no_battery"),
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0_signed")
_tok("BEST_PLAN_BATT_MODELED", kind="derived",
     get=lambda ctx: _best_plan_matrix_cell("BEST_PLAN_BATT_MODELED", "with_battery"),
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0_signed")
_tok("BATTERY_VALUE_BEST_PLAN", kind="derived",
     get=lambda ctx: _best_plan_matrix_cell("BATTERY_VALUE_BEST_PLAN", "battery_value"),
     sources=["data/battery_plan_matrix.json:plans"], fmt="usd0_signed")


def _bpm_rivals(token, column):
    """The SET of OTHER plans battery_plan_matrix.json prices cheapest in
    `column` -- the runner-up, or all of them where the column ties.

    A SET, for _bpm_cheapest's reason one rank down: at a tie there is more
    than one cheapest rival, and collapsing that to a single name lets the
    caller's choice of name -- not the artifact -- decide things. Since issue
    #141's per-column fix that matters twice over, because S4_VERDICT_SHORT
    now asks whether the runner-up is THE SAME PLAN in both columns, and two
    rivals storing the same cell must not be able to answer that by which key
    happened to sort or hash first.

    "Cheapest" is `==` on the stored cells, exactly as in _bpm_cheapest and
    for the same derivation: battery_plan_matrix.py rounds every cell to whole
    dollars, round() is non-decreasing, so a rival storing more than the
    minimum came from a strictly dearer bill. Order survives the rounding;
    only SIZE is blurred, and the callers hedge sizes separately.

    The one refusal is a matrix with no other plan in it at all: there is then
    no runner-up to name and no gap to take.

    Ranked on the matrix's OWN columns, not plan_results.csv's
    (battery_plan_matrix.py ties the two out in-script), because these columns
    are what the sentences downstream quote.

    NON-FINITE CELLS ARE REFUSED HERE, before the ranking runs, and `token`
    exists so that refusal can name the caller (issue #141 review round 3).
    This function's whole output is an ordering -- min() over the rival cells,
    then an `==` filter for ties -- and every comparison a nan takes part in
    is False, so a nan does not lose the min(), it makes the WINNER depend on
    dict order. The old note here said the callers guarded it; they did not.
    PLAN_MARGIN_VS_RUNNER_UP _require_finite's its MARGIN, which stays finite
    when a nan cell hands the runner-up slot to some other plan, so it
    published a real margin against a plan that is not the runner-up. A
    guarantee has to live where the ordering is taken.

    THE CELLS CHECKED ARE THE CELLS RANKED: `others` in `column`, which is
    exactly what min() and the `==` filter read. The household's OWN cell is
    not ranked here at all -- it is subtracted from the return value by both
    callers, each of which _require_finite's that difference immediately and
    by name (S4_VERDICT_SHORT its two gaps, PLAN_MARGIN_VS_RUNNER_UP its
    margin), so a nan there still refuses naming the gap it broke rather than
    a column it never entered.

    With the column finite, the `==` filter cannot come back empty (a real
    number equals itself), so the fallback below is unreachable defence rather
    than the load-bearing line it used to be.
    """
    plan, _best = _bpm_best()
    others = {k: v for k, v in _bpm_plans().items() if k != plan}
    if not others:
        raise SystemExit(f"report_tokens: battery_plan_matrix.json prices only {plan!r}, "
                          "so there is no runner-up plan to measure a margin against")
    _require_finite(token,
                    f"which other plan the matrix prices cheapest in its {column} column",
                    **{f"{p}_{column}": v[column] for p, v in others.items()})
    name, row = min(others.items(), key=lambda kv: kv[1][column])
    tied = {k for k, v in others.items() if v[column] == row[column]}
    # `or {name}` is now belt-and-braces: with the column finite, min()'s own
    # row is always in `tied`. It stays because an empty set is the one return
    # value the callers cannot read at all.
    return tied or {name}


def _runner_up(token, column="no_battery"):
    """ONE cheapest other plan in `column`, whether or not the household's own
    plan leads it.

    `token` is the caller's own name, passed straight through to _bpm_rivals
    so its finiteness refusal says which token was being resolved.

    Both callers -- S4_VERDICT_SHORT and PLAN_MARGIN_VS_RUNNER_UP -- describe
    the difference as this plan's "lead" / "margin", words that are false if
    the runner-up prices at or below it. That used to be refused here, which
    took the whole report down for a household on a plan the matrix does not
    put first. The gap is a real, quotable figure at every sign, so both
    callers now word themselves off its sign instead (issue #131 review).

    PER COLUMN, and the default is no_battery only because
    PLAN_MARGIN_VS_RUNNER_UP is declared as a no-battery margin. It used to be
    hardcoded to that column and S4_VERDICT_SHORT reused the answer for BOTH
    of its clauses, which is the defect issue #141's review found: with
    rivals at 200/200 and 300/150 against a household at 100/100, the runner-up
    is the first plan without a battery and the SECOND with one, and quoting
    the first for both published "leaves the lead at $100/yr against $100/yr"
    while the real margin against the with-battery runner-up had halved to $50.

    WHICH of several tied rivals gets named is settled by sorted() rather than
    by dict insertion order. That is a real, if small, improvement -- insertion
    order is the JSON's key order, which is not a fact about the household --
    and it is all that is needed here, because the tied cells are equal, so
    every FIGURE this returns is identical whichever tied name is picked. The
    decision that a tie could actually corrupt, "is the runner-up the same plan
    in both columns", is not taken on this function's return value at all: it
    is taken on _bpm_rivals' sets.
    """
    name = sorted(_bpm_rivals(token, column))[0]
    return name, _bpm_plans()[name]


def _bpm_cheapest(token, column):
    """The SET of plans battery_plan_matrix.json prices cheapest in `column`.

    A set and not a name: at a tie there is more than one cheapest plan, and
    collapsing that to whichever key sorted first would invent a ranking
    change out of a tie (or hide one behind it). Callers compare the sets.

    `token` names the caller in the refusal rather than being hardcoded here:
    a nan in the column is a real fail-closed condition, and the message has
    to name the token that was being resolved, not the first one that ever
    used this helper.

    "Cheapest" is an EQUALITY on the stored cells, and the whole-dollar
    rounding is why it can be: round() is non-decreasing, so a cell storing
    more than the minimum was a dearer bill and a cell storing the minimum
    was not (the derivation above). Widening this to a band -- every plan
    within $1.00 of the minimum -- was tried and is wrong: it puts a plan the
    column ranks second into a set the callers read as "the cheapest plans",
    which is a claim about ORDER, and order is the one thing the rounding
    leaves intact. What the rounding does blur is the SIZE of the gap between
    two plans, and the callers hedge that separately (issue #141 adversarial
    review).
    """
    totals = {p: v[column] for p, v in _bpm_plans().items()}
    _require_finite(token,
                    f"which plan the matrix prices cheapest in its {column} column",
                    **{f"{p}_{column}": t for p, t in totals.items()})
    cheapest = min(totals.values())
    return {p for p, t in totals.items() if t == cheapest}


# The three standings ONE COLUMN of the matrix can put the household's plan
# in, ORDERED WORST FIRST. A column settles three things and only two of them
# are a win or a loss:
#
#   trails  some rival stores less. The plan is not cheapest in this column.
#   tie     the plan stores this column's minimum and at least one rival
#           stores it too. A tied plan IS a cheapest plan -- which is why it
#           is not "trails" -- but it is not the only one.
#   win     the plan stores the minimum and no other plan does.
_S4_COLUMN_STANDINGS = ("trails", "tie", "win")

# The same three words section 3 ranks the CSV in, ordered the other way round.
# Section 0's card scores a matrix COLUMN against a CSV standing and a wildcard
# standing in one count, so the two vocabularies have to be the same set or the
# card is counting a state it cannot read. Checked at import rather than at each
# caller: this is a fact about two tuples, and the runtime guard it replaces
# (_matrix_standing's, which parsed a standing back out of a CSS class name)
# only existed because the card used to read this ranking through S4_ROW_CLASS.
#
# AN `if ... raise`, NOT AN `assert`, and the difference is the whole point of
# a guard whose job is to stop a card scoring a standing it cannot read.
# `python -O` compiles an assert statement out of the bytecode entirely, so the
# check would be absent in exactly the run where nothing else is watching --
# the card would go on counting a vocabulary that no longer matches and publish
# a label off it. A raise survives -O. SystemExit and not AssertionError
# because this module answers a condition it cannot honestly work around with
# SystemExit everywhere else (resolve_token, _plan_ranking, _bpm_best,
# _bpm_standing_pair), and it names itself in the message the way they do: an
# import-time refusal is what stops report_blocks, generate_report and the
# suites, so the message has to say which module refused and why.
if set(_S4_COLUMN_STANDINGS) != set(_PLAN_STANDINGS):
    raise SystemExit(
        "report_tokens: the matrix's per-column standings "
        f"{_S4_COLUMN_STANDINGS} and the CSV's {_PLAN_STANDINGS} are no longer the same "
        "vocabulary, so S0_BEST_PLAN_CARD cannot score them in one count")


def _bpm_column_standings(token):
    """[(column, phrase, standing)] -- where battery_plan_matrix.json puts the
    household's plan in EACH of its columns, in _BPM_COLUMNS order.

    ONE RANKING OF THE MATRIX, READ BY BOTH SENTENCES THAT QUOTE IT. Section
    4's row class needs the PAIR (sorted worst first and joined, so a badge
    says only what both columns support) and section 0's card needs each
    column ON ITS OWN (so a count is a count of rankings, not of artifacts).
    Those are two views of one three-way test, so the test is taken here,
    once. Two independent rankings of the same two columns drift over exactly
    the cases that matter -- whether a tie counts as cheapest, which column
    decides -- which is the rule _s4_row_class's docstring already keeps
    against the heading directly above it.

    Ranked through _bpm_cheapest, so its refusals stand for both callers: a
    column that is not made of numbers, and (through _bpm_best) a household
    plan the matrix does not price at all. `token` names the caller in those
    refusals.

    Identity on the STORED cells: battery_plan_matrix.py rounds every cell to
    whole dollars and round() is non-decreasing, so a cell above the minimum
    came from a strictly dearer bill and equal cells are a tie the artifact
    cannot separate. _BPM_TIE_USD bounds a SIZE and has no business here.
    """
    plan, _row = _bpm_best()
    out = []
    for column, phrase in _BPM_COLUMNS:
        winners = _bpm_cheapest(token, column)
        out.append((column, phrase,
                    "trails" if plan not in winners
                    else "tie" if len(winners) > 1 else "win"))
    return out


def _bpm_standing_pair(token):
    """(worst, strongest) -- the two columns' standings, sorted worst first.

    A LIST SORTED, never a set: the two columns agreeing is a state of its own
    ("win", "tie", "trails") and not a duplicate to be collapsed away.

    THE PAIR IS THE SHARED FACT. Section 4's row class is the pair joined, and
    section 0's card counts the WORSE of it while carving out the stronger
    column when they disagree -- two readings of one ranking, both taken here
    so a tie cannot be answered one way by the card and another by the row a
    screen below it.

    Exactly two, checked rather than assumed: min() used to catch the empty
    case by raising ValueError and nothing at all caught a third column, which
    would have silently produced "trails-tie" from three standings and a card
    clause that says "two columns" over a table with more.
    """
    standings = sorted((s for _column, _phrase, s in _bpm_column_standings(token)),
                       key=_S4_COLUMN_STANDINGS.index)
    if len(standings) != 2:
        raise SystemExit(
            f"report_tokens: {token} states one standing per battery column and "
            f"report_tokens._BPM_COLUMNS now names {len(standings)} of them "
            f"({[c for c, _p in _BPM_COLUMNS]}); section 4's row class is a pair and "
            "section 0's card's split clause counts two columns in as many words, so "
            "the vocabulary in _S4_ROW_CLASSES, the clause in _matrix_split_clause and "
            "the rules in report-template.html's <style> block all have to be "
            "re-derived before either can name a state")
    return tuple(standings)


# The states section 4's household ROW can be in, as the CSS class names
# report-template.html styles: ONE PER UNORDERED PAIR of the two columns'
# standings, each named worst-standing-first, the pairs ordered worst first.
#
# NOT THE WEAKEST OF THE TWO COLUMNS, which is what this was and why it had
# to change. The row spans both columns, so a class carrying only the weaker
# standing states something FALSE about the stronger one, and the stylesheet
# prints that falsehood: a household alone cheapest without a battery and
# beaten with one resolved "trails", and `tr.trails td:first-child::after`
# stamps the plan-name cell "not the cheapest" -- beside a no-battery cell
# that IS the cheapest in its column. That is issue #178's own defect (markup
# asserting a standing this household's cells do not carry) one state along.
# The mirror is the same thing pointing the other way: a plan alone cheapest
# in one column and tied in the other took "tie", badging the whole row "ties
# for cheapest" over a column it wins outright.
#
# So the class carries BOTH standings and every badge states only what both
# columns support. SIX, because two columns drawn from three standings make
# six unordered pairs, and the row is symmetric in them: WHICH column is
# which is already on the page twice -- in the two cells the badge sits
# beside, and in S4_VERDICT_SHORT's own clauses ("... without a battery, and
# ... with one") -- so naming it here would buy nine classes and three more
# badges to repeat what the reader is already looking at.
#
# DERIVED from the standings rather than written out, so no state can enter
# this vocabulary without a pair of column standings that reaches it, and
# none can leave it while a pair still does. The 9-way product of the two
# columns is driven against it in
# case_section_4s_row_class_is_a_state_the_stylesheet_can_paint, which also
# holds every member to a badge in report-template.html's own <style> block.
_S4_ROW_CLASSES = tuple(
    worst if worst == strongest else f"{worst}-{strongest}"
    for i, worst in enumerate(_S4_COLUMN_STANDINGS)
    for strongest in _S4_COLUMN_STANDINGS[i:])


def _s4_row_class(ctx):
    """The CSS class on section 4's household row: one of _S4_ROW_CLASSES.

    WHAT THIS TOKEN IS FOR (issue #178). The row used to open as a fixed
    `<tr class="win">`, and `tr.win td` paints it as the winner. That is a
    CLAIM about this household -- the matrix prices its plan cheapest -- made
    in markup no token could reach, so the three cells inside it refused
    rather than render figures the row around them contradicted, and a
    household the matrix ranks second in either column got no report at all.
    The claim now has a token, so it can come out true, false, or a tie, and
    the report is written either way.

    THE CLASS IS THE PAIR OF STANDINGS, not the weaker of them. One row spans
    two battery columns, and a class reporting one standing for both prints a
    badge that is false about the other -- "not the cheapest" beside a cell
    that is the cheapest in its column, or "ties for cheapest" beside one the
    plan wins outright. So the two columns' standings (_S4_COLUMN_STANDINGS:
    trails / tie / win) are both carried, sorted worst first and joined, and
    the badge report-template.html prints for each state says only what BOTH
    columns support. The nine combinations, and the class each resolves to:

        no battery   with battery   class         what its badge asserts
        ----------   ------------   -----------   -----------------------------
        win          win            win           cheapest, alone, both columns
        win          tie            tie-win       cheapest in both, tied in one
        tie          win            tie-win       "
        tie          tie            tie           tied for cheapest in both
        win          trails         trails-win    cheapest in one column only
        trails       win            trails-win    "
        tie          trails         trails-tie    tied cheapest in one only
        trails       tie            trails-tie    "
        trails       trails         trails        cheapest in neither column

    Every badge is a statement about HOW MANY of the two columns price this
    plan cheapest, and in how many of those it is the only one -- which is
    exactly what the pair records, so it is true of both members of every
    pair that has two. WHICH column is which is deliberately not in the
    badge: the reader is looking at both cells, and S4_VERDICT_SHORT names
    the columns in as many words directly above the table.

    RANKED THROUGH _bpm_cheapest, the helper S4_VERDICT_SHORT decides its
    Yes/No on, and never through a second ranking of the same columns. The
    heading and the row directly beneath it are read together or not at all
    (issue #141 review round 3), and two independent rankings of one artifact
    drift over exactly the cases that matter: whether a tie counts as
    cheapest, which column decides. One helper, one `==` on the stored cells,
    one answer for both.

    Identity on the STORED cells, with `==` and min(): battery_plan_matrix.py
    rounds every cell to whole dollars and round() is non-decreasing, so a
    cell above the minimum came from a strictly dearer bill and equal cells
    are a tie the artifact cannot separate (the derivation above).
    _BPM_TIE_USD bounds a SIZE and has no business here -- reading it into
    "cheapest" is what once admitted a runner-up into a winners' set.

    The refusals are the ones underneath it: a column that is not made of
    numbers (_bpm_cheapest -- a nan equals nothing, so it would not lose the
    ranking, it would empty it), and a household plan the matrix does not
    price at all (_bpm_best). Neither leaves a standing this token could
    state honestly.
    """
    # Off _bpm_standing_pair, which section 0's card reads the same two columns
    # through: the pair this class is built from and the standing that card
    # counts are one ranking seen twice, and a second ranking here would
    # eventually answer a tie differently from the card a screen above it.
    # That helper also owns the "exactly two columns" refusal, for both.
    worst, strongest = _bpm_standing_pair("S4_ROW_CLASS")
    return worst if worst == strongest else f"{worst}-{strongest}"


# fmt="raw", and THE SEAM GUARD HAS NOTHING TO READ FOR IT -- said here
# rather than left for whoever next wonders why this token is not in the
# tables. This value lands inside an HTML attribute, and the three seam
# classes test_report_tokens.py checks at every token seam (a doubled sigil,
# a lost dimension, a figure echoed beside itself) are all anchored on a
# FIGURE: a CSS class name has no digits, no unit, and no dimension a
# declared format could say it lost, so all three are inert on it and
# _SEAM_FMT_DIMENSIONS -- derived by running the numeric formatters -- has no
# entry for "raw" by construction. The format spec is a statement that there
# is nothing numeric here, not a dimension the guards can use.
#
# What an attribute token DOES need guarding is that its value is a class the
# stylesheet paints: an unstyled class name is not a broken render, it is a
# runner-up's row drawn exactly like every other row, which is the silent
# half of the defect this token exists to fix.
# case_section_4s_row_class_is_a_state_the_stylesheet_can_paint holds the
# whole vocabulary to report-template.html's own <style> block.
#
# attribute_only=True: this value is markup, not language. See
# is_attribute_only() below for what the flag does and what checks it.
_tok("S4_ROW_CLASS", kind="derived", get=_s4_row_class, fmt="raw",
     attribute_only=True,
     sources=["data/battery_plan_matrix.json:plans (both columns)",
              "private/household.yaml:household.plan"])


def _s4_verdict_short(ctx):
    """Section 4's in-heading verdict.

    THE PREFIX ANSWERS THE HEADING'S OWN QUESTION (issue #141). The template
    reads "Does a battery change which plan is best? {{S4_VERDICT_SHORT}}", so
    Yes/No has to answer THAT -- whether the plan the matrix prices cheapest
    differs between its two columns. It used to be spelled `widened`, the same
    test as the widens/narrows verb below, which answers a different question
    (does the battery grow the gap): with this household's artifact both gaps
    grow, so the heading published "Yes" directly above a section concluding
    that the plan choice does not change. The two decisions are now taken
    separately, off _bpm_cheapest (a ranking, at both battery states) and off
    the gaps respectively.

    EACH DECISION IS TAKEN ON WHAT THE ROUNDING LEAVES INTACT (issue #141
    adversarial review). battery_plan_matrix.py rounds every cell to whole
    dollars, and the two questions this sentence answers are affected
    differently by that. WHICH plan is cheapest survives it exactly, because
    round() is non-decreasing, so the Yes/No is taken on the stored cells with
    `==`. HOW BIG a lead is, and whether a lead grew, do not survive it: a
    stored $1 lead is a real lead of a cent to just under $2, and a stored $1
    change in a lead is four roundings deep and may not have happened at all.
    So the sizes are hedged against _BPM_TIE_USD (two cells) and the
    widens/narrows verb against _BPM_GAP_TIE_USD (four).

    THE RUNNER-UP IS PER COLUMN (issue #141 adversarial review). The rival
    this sentence measures against is selected inside each column separately,
    and the plan is compared against the rival that column actually ranks
    second. One rival ranked on no_battery used to be reused for both clauses,
    which quoted a with-battery figure against a plan that was not the
    with-battery runner-up; see the worked example in the body. When the two
    columns disagree about who the runner-up is, the sentence says so and
    names both, and it drops the widens/narrows/leaves verbs entirely --
    those are claims about ONE comparison priced twice, and a number that
    moved because the opponent changed is not that claim.

    THE NOUN AND THE SIGIL are a separate, earlier fix. "lead" and "widens
    ... lead" are false unless this plan leads at BOTH battery states, and the
    previous version tested only the no-battery gap -- so a plan leading by $200 without
    a battery and TRAILING by $500 with one still published "narrows EV-TOU-5's
    lead over EV-TOU-2 from $200/yr to $-500/yr": a lead that is not one, and a
    minus sign inside the dollar sigil (issue #131 review round 2, finding 5;
    the same commit fixed exactly this in _plan_margin_vs_runner_up and in the
    branch beside this one). Unless both gaps are positive it now states where
    the plan actually stands at each battery state, and every signed figure in
    this module goes through _usd0 / _usd0_signed rather than an inline
    f"${...}".
    """
    plan, best = _bpm_best()
    # THE RUNNER-UP IS TAKEN PER COLUMN (issue #141 adversarial review). One
    # _runner_up() call, ranked on no_battery, used to supply the rival for
    # BOTH clauses. But the cheapest rival can differ between the columns --
    # that is the whole point of a matrix that prices a battery under three
    # plans -- and when it does, quoting the no-battery rival with the
    # with-battery gap compares this plan against a plan that is not the one
    # the second figure was taken from. Worked example, all three plans valid:
    # us 100/100, B 200/200, C 300/150. The old code published "leaves
    # EV-TOU-5's lead over B at $100/yr against $100/yr" while the real
    # with-battery margin -- against C, the with-battery runner-up -- was $50,
    # half of it, and a lower C hides a near-tie entirely. The matrix rendered
    # directly below the heading shows all six cells, so the sentence was also
    # contradicting the table under it.
    #
    # GUARD DISCIPLINE, which case is this: DIFFERENT SCENARIOS. The two
    # columns are two different bills of the same year (battery / no battery)
    # under the same plan set, written by one run of battery_plan_matrix.py.
    # Neither column is derived from the other, neither is clamped, and no
    # cross-column arithmetic happens below -- each gap is a difference of two
    # cells INSIDE one column, and the only cross-column comparison left is
    # the widens/narrows verb, which is gated separately and only ever fires
    # when both columns measure against the SAME rival.
    #
    # SAME RIVAL OR NOT is decided on the two SETS, never on the two names.
    # A shared plan means one rival is a cheapest rival in both columns, so
    # there is a single comparison to talk about and the pre-existing wording
    # holds. Disjoint sets mean no plan is: the comparison genuinely changes
    # identity between the columns. Taking this off _runner_up()'s names
    # instead would let a tie between two rivals in one column invent an
    # identity change out of whichever name sorted first.
    rivals_no = _bpm_rivals("S4_VERDICT_SHORT", "no_battery")
    rivals_with = _bpm_rivals("S4_VERDICT_SHORT", "with_battery")
    shared = rivals_no & rivals_with
    if shared:
        name_no = name_with = sorted(shared)[0]
    else:
        name_no, name_with = sorted(rivals_no)[0], sorted(rivals_with)[0]
    plans = _bpm_plans()
    gap_no = plans[name_no]["no_battery"] - best["no_battery"]
    gap_with = plans[name_with]["with_battery"] - best["with_battery"]
    _require_finite("S4_VERDICT_SHORT", "how this plan stands against the runner-up",
                    no_battery_gap=gap_no, with_battery_gap=gap_with)
    # DOES THE BATTERY CHANGE WHICH PLAN IS BEST? Asked of SETS, built on the
    # stored cells exactly (_bpm_cheapest): each set is the plans that column
    # prices lowest, and a plan is in it only if no other plan stores lower.
    # Three answers, because the sets admit three shapes and only two of them
    # are a Yes/No:
    #
    # THE ANSWER IS TAKEN ON THE INTERSECTION, and on nothing else (issue
    # #141 review round 3). The question is whether ONE plan survives the
    # battery, so the quantity that answers it is the set of plans that are
    # cheapest-or-joint-cheapest in BOTH columns -- joint-cheapest being
    # cheapest, exactly as the win row's own gate reads it in
    # _best_plan_matrix_cell. Three answers, by the SIZE of that intersection:
    #
    #   empty      -> "Yes". Nothing cheapest without a battery is cheapest
    #      with one. The winner really does change, and the cells settle that
    #      they do.
    #   one plan   -> "No". Exactly one plan is cheapest-or-joint-cheapest at
    #      both battery states, so staying on it is the answer whatever the
    #      battery does -- this household's case, EV-TOU-5 by $961 and $1,612.
    #      It is STILL "No" when that plan only TIES for cheapest in one of
    #      the columns: a plan the artifact cannot separate from a rival in
    #      one column and prices strictly below every rival in the other is
    #      the single plan that is never beaten, which is the whole of what
    #      the heading asks.
    #   two or more-> "Too close to call". Every plan in the intersection is
    #      the minimum of both columns, so two of them means two plans priced
    #      IDENTICALLY in BOTH columns: there is no ranking change, so this is
    #      not "Yes", and there is no single plan to name either, so it is not
    #      a "No" that hands the reader a plan choice the artifact never made.
    #      That is a real tie in the artifact, not a rounding hedge -- which is
    #      why it survives the identity comparison going back to `==`.
    #
    # THE PREMISE THIS REPLACES, and why it was false: the third state used to
    # be "either set names more than one plan", justified as "no single plan is
    # the answer at both states". A tie in ONE column does not make that true.
    # us 100/100, B 100/200, C 300/300 ties B without a battery and beats it
    # outright with one, so the household's plan IS the answer at both states
    # -- and section 4's row says so, reading "tie" off this same helper
    # (joint-cheapest is cheapest), so the old branch put a heading saying the
    # question could not be settled directly above a row settling it. The
    # mirror, us 100/100 against B 200/100, is the same shape.
    # A heading and the row beneath it are read together or not at all.
    no_batt = _bpm_cheapest("S4_VERDICT_SHORT", "no_battery")
    with_batt = _bpm_cheapest("S4_VERDICT_SHORT", "with_battery")
    best_at_both = no_batt & with_batt
    if not best_at_both:
        answer = "Yes"
    elif len(best_at_both) == 1:
        answer = "No"
    else:
        answer = "Too close to call"

    def stands(gap, who_name):
        """Where this plan stands against `who_name` in one column, naming it
        when who_name is given.

        THE VERB OFF THE SIGN, THE FIGURE OFF THE BAND. A non-zero stored gap
        is a real ordering (round() is non-decreasing), so "leads"/"trails"
        is exact at every size, down to a stored dollar. What a stored dollar
        does not support is the figure "$1/yr", which could be a cent: inside
        the band the clause states the direction and BOUNDS the size at
        gap + $1.00 instead of quoting it. A stored gap of zero is the one
        case with no direction to state -- two identical cells, a real tie.

        _BPM_TIE_USD and not _BPM_GAP_TIE_USD, deliberately: a clause built
        here compares TWO cells of ONE column, which is the two roundings that
        constant is derived from. Four-cell error belongs to the verb below,
        which compares two of these gaps against each other.
        """
        who = f" {who_name}" if who_name else ""
        if gap > _BPM_TIE_USD:
            return f"leads{who} by {_usd0(gap)}/yr"
        if gap > 0:
            return f"leads{who} by under {_usd0(gap + _BPM_TIE_USD)}/yr"
        if gap == 0:
            return f"ties{who}"
        if gap < -_BPM_TIE_USD:
            return f"trails{who} by {_usd0(-gap)}/yr"
        return f"trails{who} by under {_usd0(-gap + _BPM_TIE_USD)}/yr"

    # A RIVAL CHANGE IS NOT A GAP CHANGE (issue #141 adversarial review).
    # "widens" / "narrows" / "leaves ... at" all assert something about ONE
    # comparison at two battery states: the same two plans, priced twice. When
    # the columns' runner-ups are different plans the sentence has four plans
    # in it, and a figure that moved from $100 to $50 may have moved because
    # the battery changed the bills, because the rival changed identity, or
    # any mixture -- so none of those three verbs is a claim the cells
    # support, and the size band cannot rescue them either (_BPM_GAP_TIE_USD
    # bounds the ERROR in a gap change, not the attribution of one).
    #
    # So this branch makes no cross-column claim at all. It says the rival
    # changed, which is the fact the reader needs and the one thing four cells
    # settle exactly -- the sets are disjoint on `==`, and round() is
    # non-decreasing -- and then states each column's standing against its own
    # runner-up, each clause hedged against its own two cells by stands().
    # Both rivals are NAMED, because a reader shown two figures and one name
    # would read a moving margin against a fixed opponent, which is the false
    # reading this whole fix exists to stop.
    if name_no != name_with:
        return (f"{answer} — the cheapest rival changes with the battery: "
                f"{plan} {stands(gap_no, name_no)} without one, and "
                f"{stands(gap_with, name_with)} with one")

    # ONE rival, both columns, so the two gaps are the same comparison priced
    # twice and the verbs above become available. The clauses below QUOTE the
    # two gaps as figures and compare them, which is the part of this sentence
    # the rounding does reach. Both gaps have to be large enough for the
    # quoted size to mean something ($1.00, two cells) before this branch
    # prints them; a smaller lead is still a real lead and falls through to
    # stands(), which names its holder and bounds its size instead of quoting
    # one. "widens"/"narrows" is a claim about the gap BETWEEN the two gaps,
    # four cells deep and not helped by monotonicity, hence _BPM_GAP_TIE_USD.
    if gap_no > _BPM_TIE_USD and gap_with > _BPM_TIE_USD:
        if abs(gap_with - gap_no) <= _BPM_GAP_TIE_USD:
            return (f"{answer} — the battery leaves {plan}'s lead over {name_no} at "
                    f"{_usd0(gap_no)}/yr against {_usd0(gap_with)}/yr, a change "
                    f"smaller than the rounding can resolve")
        return (f"{answer} — the battery "
                f"{'widens' if gap_with > gap_no else 'narrows'} {plan}'s lead over "
                f"{name_no} from {_usd0(gap_no)}/yr to {_usd0(gap_with)}/yr")

    return (f"{answer} — {plan} {stands(gap_no, name_no)} without a "
            f"battery, and {stands(gap_with, None)} with one")


_tok("S4_VERDICT_SHORT", kind="derived", get=_s4_verdict_short,
     sources=["data/battery_plan_matrix.json:plans"])


def _wildcard_plan(ctx):
    """The plan section 9's wildcard heading asks about.

    OFF _wildcard_rivals, which is off _wildcard_totals, which is off
    _wildcard_key_plan -- so this token and section 0's card read that
    artifact's prose keys through one split instead of two. This function used
    to carry its own, with this household's battery ("PW3") written into it;
    see _WILDCARD_KEY_QUALIFIER_RE for what the two disagreed about.
    """
    best = hh1("household.plan")
    rivals = _wildcard_rivals(best)
    if not rivals:
        raise SystemExit("report_tokens: could not identify a wildcard plan name in "
                          "deep_results.json:wildcard's keys")
    return rivals[0]


_tok("WILDCARD_PLAN", kind="derived", get=_wildcard_plan,
     sources=["data/deep_results.json:wildcard"])
def _plan_margin_vs_runner_up(ctx):
    """The household plan's margin over the cheapest other plan in
    battery_plan_matrix.json's no-battery column, SIGNED.

    A margin is a real figure at every sign, so this token no longer inherits
    a refusal from _runner_up() when the sign goes the other way -- a house
    priced above the runner-up has a negative margin, and the minus sign IS
    the conclusion. Formatted through the SHARED _usd0_signed rather than
    fmt="usd0", so the negative reads "-$500" and not "$-500" -- shared,
    because building that string inline here is exactly what left the
    identical defect standing next door in _s4_verdict_short."""
    gap = (_runner_up("PLAN_MARGIN_VS_RUNNER_UP")[1]["no_battery"]
           - _bpm_best()[1]["no_battery"])
    _require_finite("PLAN_MARGIN_VS_RUNNER_UP", "what this plan's margin is", margin=gap)
    return _usd0_signed(gap)


_tok("PLAN_MARGIN_VS_RUNNER_UP", kind="derived", get=_plan_margin_vs_runner_up,
     sources=["data/battery_plan_matrix.json:plans (no-battery column; ties out to "
              "data/plan_results.csv, asserted in-script)"])

_tok("NEM_GRANDFATHER_VALUE_RANGE", kind="derived",
     get=lambda ctx: (lambda low, high: f"${low:,.2f}–{high:,.2f}")(
         *_amounts("NEM_GRANDFATHER_VALUE_RANGE",
                   "what NEM 2.0 grandfathering is worth",
                   **{k: _json("nem3_grandfathering.json")
                      ["grandfathering_value_range_usd_per_yr"][k]
                      for k in ("low", "high")})),
     sources=["data/nem3_grandfathering.json:grandfathering_value_range_usd_per_yr"])


def _s8_verdict_short(ctx):
    nem = _json("nem3_grandfathering.json")["grandfathering_value_range_usd_per_yr"]
    low, high = _amounts("S8_VERDICT_SHORT", "what NEM 2.0 grandfathering is worth",
                         grandfathering_low=nem["low"], grandfathering_high=nem["high"])
    exp_pct = round(_json("report_data.json")["totals"]["exp"] /
                     _annual_production_kwh(ctx) * 100)
    # NOT "at low value". That clause priced the ALL-HOURS export share at the
    # midday cell of the price map, and the section's most-read sentence was
    # where it read hardest. The exports are worth somewhere between
    # EXPORT_VALUE_SURPLUS_BOUND and EXPORT_VALUE_NETTING_BOUND, and even the
    # low end is nearly twice the super-off-peak import rate, so "low value" is
    # not the reason the answer is no at either end of the range. The reason is
    # the clause that follows it -- the
    # NEM 2.0 growth cap and the grandfathering it puts at risk -- and that one
    # is artifact-backed.
    return (f"No, no, and not yet — the array already exports {exp_pct}% of "
            f"production, and expansion risks the "
            f"${low:,.0f}–{high:,.0f}/yr NEM 2.0 grandfathering")


_tok("S8_VERDICT_SHORT", kind="derived", get=_s8_verdict_short,
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv",
              "data/nem3_grandfathering.json"])
_tok("EXPANSION_VERDICT_SHORT", kind="derived",
     get=lambda ctx: (lambda exp_pct: f"No — already exports {exp_pct}% of "
                       "production at the wrong time of day")(
         round(_json("report_data.json")["totals"]["exp"] / _annual_production_kwh(ctx) * 100)),
     sources=["data/report_data.json:totals", "data/enphase_daily_production.csv"])

_tok("RECOMMENDED_PACKAGE_SUMMARY", kind="derived",
     get=lambda ctx: f"MID — behavior fix plus {TOKENS['BATTERY_MODEL']['value']}",
     sources=["data/package_results.json (MID is the starred package)",
              "analysis/battery_dispatch_policies.py"])


# ---- lifetime payback --------------------------------------------------
_tok("FIRST_FULL_YEAR", kind="data_json", file="lifetime_payback.json",
     path=("years", 0, "year"), fmt="year")
_tok("FIRST_YEAR_PRODUCTION_KWH", kind="data_json", file="lifetime_payback.json",
     path=("years", 0, "production_kwh"), fmt="num0")
_tok("FIRST_YEAR_VALUE", kind="derived",
     get=lambda ctx: round(_json("lifetime_payback.json")["years"][0]["value_usd"] / 100) * 100,
     sources=["data/lifetime_payback.json:years[0].value_usd"], fmt="usd0_tilde")


def _crossover_season_year(which):
    """(season_label, year, month) for lifetime_payback.json's crossover[which].
    month is the numeric calendar month (1-12) _fraction_to_month() already
    computes on the way to the season label -- returned alongside it (not
    just the label) so callers needing to compare crossover timing against
    "today" can do it chronologically instead of by comparing season NAMES,
    which sort alphabetically, not by calendar order (see _paid_off)."""
    c = _json("lifetime_payback.json")["crossover"][which]
    # Checked HERE, once, because four tokens read this one helper --
    # PAYBACK_CROSSOVER_DATE, PAYBACK_HEADLINE, PAYBACK_STATUS_SHORT and
    # S11_VERDICT_SHORT -- and every one of them prints the year into a
    # sentence about when the array pays for itself. A nan year published
    # "on pace to pay for itself by fall nan" from all four (issue #131
    # review round 5, part B's sweep). _paid_off also COMPARES it against
    # today, and a nan loses every comparison silently.
    token = f"crossover.{which}"
    year, fraction = _figures(
        token, "when the array's cumulative value crosses its cost",
        **{f"crossover_{which}_year": c["year"],
           f"crossover_{which}_fraction_through_year": c["fraction_through_year"]})
    month = _fraction_to_month(fraction)
    return _season_for_month(month), year, month


_tok("PAYBACK_CROSSOVER_DATE", kind="derived",
     get=lambda ctx: (lambda s, y, m: f"{s} {y}")(*_crossover_season_year("gross")),
     sources=["data/lifetime_payback.json:crossover.gross"])


def _paid_off(ctx):
    """Whether the crossover has already happened as of the system clock.
    Compares (year, MONTH) -- both numeric -- never (year, season-name):
    a Codex review caught the previous version comparing season NAMES as
    strings when years were equal ("fall" < "summer" is alphabetically TRUE,
    f < s, even though fall comes AFTER summer in the same calendar year),
    which could report a same-year crossover as already paid off before it
    had actually happened. Strict '<': a crossover landing in the CURRENT
    month is reported as NOT YET paid off -- this module's own resolution is
    monthly, not daily, so within the current month there is no way to know
    whether the crossover fell before or after "today", and CLAUDE.md
    section 0 records an event only after it has definitely happened, never
    on the strength of "sometime this month, probably.\""""
    season, year, month = _crossover_season_year("gross")
    today = dt.date.today()
    return (year, month) < (today.year, today.month)


_tok("PAYBACK_STATUS_SHORT", kind="derived",
     get=lambda ctx: "Paid off" if _paid_off(ctx) else
     f"Payback expected {_crossover_season_year('gross')[0]} "
     f"{_crossover_season_year('gross')[1]}",
     sources=["data/lifetime_payback.json:crossover.gross", "system clock"])
_tok("S11_VERDICT_SHORT", kind="derived",
     get=lambda ctx: "the solar array has already paid for itself" if _paid_off(ctx) else
     (lambda s, y, m: f"on pace to pay for itself by {s} {y}")(*_crossover_season_year("gross")),
     sources=["data/lifetime_payback.json:crossover.gross", "system clock"])
_tok("PAYBACK_HEADLINE", kind="derived",
     get=lambda ctx: (lambda s, y, m, cost: (
         f"Paid off — cumulative value crossed the ${cost:,.0f} gross cost in {s} {y}."
         if _paid_off(ctx) else
         f"On pace to cross the ${cost:,.0f} gross cost by {s} {y}."))(
         *_crossover_season_year("gross"),
         *_amounts("PAYBACK_HEADLINE", "what the array cost to install",
                   **{"solar.install_invoice_usd": hh1("solar.install_invoice_usd")})),
     sources=["data/lifetime_payback.json:crossover.gross",
              "private/household.yaml:solar.install_invoice_usd"])


def _solar_annual_value(ctx):
    nosolar = _json("lifetime_payback.json")["nosolar_bill_usd"]
    baseline = _json("package_results.json")["model_baseline_current_rates"]
    return round((nosolar - baseline) / 100) * 100


_tok("SOLAR_ANNUAL_VALUE", kind="derived", get=_solar_annual_value,
     sources=["data/lifetime_payback.json:nosolar_bill_usd",
              "data/package_results.json:model_baseline_current_rates"],
     fmt="usd0_tilde_signed")


# ---- cleaning / soiling -------------------------------------------------
_MEASURED_CLEANING_BLOCK = "sanity_check_2024_cleaning"


def _strip_not_determined(status):
    """A generator's own `status` string with its "not determined" prefix
    taken off, ready to be used as the REASON half of a refusal this module
    writes the prefix for.

    One copy, because two are how they drift: soiling_analysis.py writes
    "not determined — <reason>" into two different blocks (the cleaning
    sanity check and scenario B), and three tokens here render a refusal
    around whichever one they read. A caller that keeps the prefix publishes
    "not determined — not determined — ...".

    Returns "" for a missing/blank status, so a caller can fall back to a
    reason of its own naming the artifact and the field."""
    text = str(status or "").strip()
    if text.lower().startswith(_NOT_DETERMINED_VERDICT):
        text = text[len(_NOT_DETERMINED_VERDICT):].lstrip(" -–—:").strip()
    return text


def _trimmed(value, places=2):
    """A figure at `places` decimals with trailing zeros trimmed off.

    ISSUE #137. SOILING_RATE_RANGE rendered both bracket ends at ONE decimal,
    and the low end is data/soiling_results.json's scenario-A rate of 0.449
    %/month, which one decimal states as "0.4" -- a lower soiling rate than
    the artifact measured, published against a report and a TECHNICAL.md that
    both state the bracket as 0.45. Fixed precision cannot serve both ends of
    a bracket that spans an order of magnitude: two decimals states the low
    end honestly, and trimming keeps the high end from acquiring a
    significant figure it does not have ("2.40")."""
    text = f"{value:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _cleaning_entries():
    # cleaning_history resolves (via privacy_tiers.resolve, no trailing "[]" in
    # its contract path) to [the whole list] -- one found node holding the
    # entire list, since the tier belongs to the container as a whole.
    lists = _hh_value("cleaning_history")
    if len(lists) != 1 or not isinstance(lists[0], list):
        raise SystemExit("report_tokens: private/household.yaml:cleaning_history did "
                          "not resolve to a single list")
    entries = lists[0]
    if not entries:
        raise SystemExit("report_tokens: private/household.yaml:cleaning_history is empty")
    return entries


def _cleaning_sanity_check():
    # _dig, not a bare subscript: _cleaning_entry() reaches this, so seven §12
    # tokens that used to need only household.yaml and cleaning_study_daily.csv
    # now fail if the block is absent. A named refusal says which artifact and
    # which key, instead of a KeyError from inside a token getter.
    return _dig(_json("soiling_results.json"), (_MEASURED_CLEANING_BLOCK,))


def _measured_cleaning():
    """The cleaning_history record the published gain was measured ON, as
    (entry, None) -- or (None, reason) when the artifact and the history do not
    between them name one event.

    THE FIGURE AND THE EVENT BESIDE IT ARE ONE CLAIM. §12's h3 reads "The
    {{CLEANING_DATE}} cleaning ({{CLEANING_PRICE}}): {{CLEANING_EFFECT_PCT}}
    production gain", and the gain is soiling_analysis.py's
    difference-in-differences estimate for ONE dated event -- that module says
    so in its own words ("they must never be attributed to any other event")
    and pins its block to that exact record. Nothing tied the token half to the
    same record: the date, price and year came off cleaning_history's FIRST
    entry, which the intake file guarantees nothing about -- not its order, not
    its length, not that it contains the measured cleaning at all. A household
    that records its cleanings newest-first, or adds a second one, or is the
    committed household.example.yaml (2023-01-01), renders a heading naming one
    cleaning and stating another's measurement.

    So the match is made HERE, once, and every §12 token that names or measures
    the cleaning reads through it. THE DATE IS THE ARTIFACT'S OWN
    (sanity_check_2024_cleaning.cleaning_date), never a literal repeated on this
    side: soiling_analysis.py owns which event it measured, and a second copy of
    that date in this module is a second thing to keep in step.

    A history that does not contain that date is an ORDINARY HOUSEHOLD, not a
    broken one -- soiling_analysis.py writes its own "not determined" block for
    exactly that case -- so this returns a reason rather than raising, and the
    report still generates. Two entries sharing the date is the third answer:
    they are two records with two prices and nothing in either file says which
    one the study measured, so the gain is refused rather than settled by
    taking whichever was listed first."""
    sc = _cleaning_sanity_check()
    when = sc.get("cleaning_date")
    if when is None or sc.get("known_cleaning_gain_pct") is None:
        # soiling_analysis.py's own not-determined shape: a `status` string and
        # no gain. Its reason is better than one written here, so it is passed
        # through when present -- with its own "not determined" prefix taken
        # off, exactly as _spread_trend takes it off tou_spread's verdicts,
        # because the caller writes that prefix and a status carrying one too
        # renders it twice.
        status = _strip_not_determined(sc.get("status"))
        return None, (status or
                      f"data/soiling_results.json:{_MEASURED_CLEANING_BLOCK} states no "
                      "measured cleaning gain")
    try:
        target = _as_date(when)
    except (TypeError, ValueError):
        return None, (f"data/soiling_results.json:{_MEASURED_CLEANING_BLOCK}"
                      f".cleaning_date is {when!r}, which is not a date")
    matched = []
    for entry in _cleaning_entries():
        if not isinstance(entry, dict) or "date" not in entry:
            continue
        try:
            if _as_date(entry["date"]) == target:
                matched.append(entry)
        except (TypeError, ValueError):
            continue
    if len(matched) == 1:
        return matched[0], None
    if not matched:
        return None, (f"no recorded cleaning on {target}, the date the measured gain "
                      "belongs to")
    return None, (f"{len(matched)} cleanings are recorded on {target}, so which one "
                  "the measured gain belongs to is ambiguous")


def _cleaning_entry():
    """The cleaning §12 describes: the measured one whenever the artifact and
    the history agree on which that is.

    The fallback to the first recorded cleaning is what keeps a household
    WITHOUT the measured event reporting its cleaning history at all -- and it
    cannot misattribute, because the only path that reaches it is the one on
    which CLEANING_EFFECT_PCT states no figure to misattribute."""
    entry, _reason = _measured_cleaning()
    return entry if entry is not None else _cleaning_entries()[0]


_tok("CLEANING_PRICE", kind="derived",
     get=lambda ctx: _usd0(_cleaning_entry()["cost_usd"]),
     sources=["private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"])
_tok("CLEANING_YEAR", kind="derived",
     get=lambda ctx: _as_date(_cleaning_entry()["date"]).year,
     sources=["private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"], fmt="year")
_tok("CLEANING_DATE", kind="derived",
     get=lambda ctx: (lambda d: f"{_MONTH_FULL[d.month]} {d.day}, {d.year}")(
         _as_date(_cleaning_entry()["date"])),
     sources=["private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"])
_tok("CLEANING_DATE_SHORT", kind="derived",
     get=lambda ctx: (lambda d: f"{_MONTH_ABBR[d.month]} {d.day}")(
         _as_date(_cleaning_entry()["date"])),
     sources=["private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"])


def _cleaning_window_medians(ctx, token):
    """The 30-day pre- and post-cleaning median daily production, in kWh/day.

    THE NAMED REFUSAL BELOW IS THE ONE ISSUE #131's ROUND 5 ADDED AND #138
    CARRIED AWAY WITH THE QUOTIENT IT GUARDED (ISSUE #171). Three tokens read
    these two medians -- CLEANED_PRE_MEDIAN and CLEANED_POST_MEDIAN publish
    them directly, CLEANED_RATIO divides one by the other -- and every one of
    them is labelled `measured` in section 12's windows table. A zero pre-
    window median makes the ratio a bare ZeroDivisionError and a non-finite
    median in EITHER window publishes "nan" under that label, so the check
    belongs here, once, where all three consumers pass through it, rather than
    on the one that happens to divide.

    It is a refusal and not a fallback on purpose: a 30-day window of daily
    production whose median is zero or not a number is a broken study CSV, not
    a household that merely differs -- unlike the cleaning-history mismatches
    one function up, which are ordinary and are rendered. `token` names which
    of the three asked, so the message says which figure could not be
    produced."""
    d0 = _as_date(_cleaning_entry()["date"])
    clean_date = dt.datetime(d0.year, d0.month, d0.day)
    rows = _csv_rows("cleaning_study_daily.csv")
    parsed = [(dt.datetime.strptime(r["date"], "%Y%m%d"), float(r["generated_kwh"]))
              for r in rows]
    pre = sorted(v for d, v in parsed
                 if clean_date - dt.timedelta(days=30) <= d < clean_date)
    post = sorted(v for d, v in parsed
                  if clean_date < d <= clean_date + dt.timedelta(days=30))
    if not pre or not post:
        raise SystemExit("report_tokens: data/cleaning_study_daily.csv does not cover "
                          "a full 30-day window on both sides of the cleaning date")

    def median(xs):
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    pre_median, post_median = median(pre), median(post)
    _claim(token, "what the cleaning's production windows were",
           SUPPORTED if _finite(pre_median, post_median) and pre_median > 0
           else NOT_DETERMINED,
           f"data/cleaning_study_daily.csv's 30-day windows around {d0} have medians "
           f"of {pre_median!r} kWh/day before and {post_median!r} kWh/day after; a "
           "pre-cleaning median of zero, and a median in either window that is not a "
           "finite production level, are not levels a gain, a ratio or a published "
           "median can be measured against")
    return pre_median, post_median


_tok("CLEANED_PRE_MEDIAN", kind="derived",
     get=lambda ctx: _cleaning_window_medians(ctx, "CLEANED_PRE_MEDIAN")[0],
     sources=["data/cleaning_study_daily.csv", "private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"],
     fmt="num1")
_tok("CLEANED_POST_MEDIAN", kind="derived",
     get=lambda ctx: _cleaning_window_medians(ctx, "CLEANED_POST_MEDIAN")[1],
     sources=["data/cleaning_study_daily.csv", "private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"],
     fmt="num1")
_tok("CLEANED_RATIO", kind="derived",
     get=lambda ctx: (lambda pre, post: round(post / pre, 3))(
         *_cleaning_window_medians(ctx, "CLEANED_RATIO")),
     sources=["data/cleaning_study_daily.csv", "private/household.yaml:cleaning_history",
              "data/soiling_results.json:sanity_check_2024_cleaning"], fmt="num2")
def _cleaning_effect_pct(ctx):
    """The cleaning's measured production gain: the DIFFERENCE-IN-DIFFERENCES
    estimate, read off the soiling study's own artifact.

    NOT the cleaned year's naive post/pre window ratio, which is what this
    token used to compute and which CLEANED_RATIO already publishes one row
    down, in the per-year windows table (issue #138). The two are different
    statistics, not two roundings of one: the raw ratio counts the seasonal
    decline that every control year shows as if the cleaning had caused it,
    so it lands near +5% while the estimate the section concludes with -- the
    cleaned year's ratio measured AGAINST the control years' -- is +11.8%.
    Filling the heading from the raw ratio printed one figure above a
    paragraph stating the other. Both statistics stay; each is now named
    where it appears.

    Same artifact and field as SEC12_TEASER, which states this figure in the
    section's own <summary>.

    The SIGN is carried by this token. The heading reads "+11.8% production
    gain" and report-template.html supplies no sigil in front of the slot, so
    a household whose cleaning measured a LOSS renders its own minus rather
    than having a "+" glued on beside it.

    STATED ONLY FOR THE EVENT IT WAS MEASURED ON. The figure belongs to one
    dated cleaning and the heading around it names a date and a price, so the
    two are bound together in _measured_cleaning() -- see its docstring for the
    misattribution this prevents. When that binding cannot be made the answer
    is NOT_DETERMINED, and it is rendered rather than raised: the state is
    named at the call site the way this module's three-state discipline
    requires, but _claim() is deliberately not the exit taken with it, because
    _claim's NOT_DETERMINED is a SystemExit and a household whose cleanings
    simply differ from ours would then generate no report at all. It is
    rendered as the words "not determined" followed by the reason, which
    CLAUDE.md section 0 makes a legitimate published answer.

    SPREAD_TREND_SUMMER/WINTER and BATTERY_ON_MEASURED_SPREAD render an
    undetermined answer the same way, but they are NOT a precedent for this
    slot and should not be read as one: each of those sits inside a TODO block,
    where a writer phrases the sentence around whatever the value turns out to
    be. This one is static fill. The reason text is therefore kept short and
    names no file path, since it is published prose here rather than a note to
    a writer.

    THE HEADING AROUND IT no longer belongs to this token. Section 12's h3 is
    filled by CLEANING_EFFECT_CLAIM and the CLEANING_EFFECT pill pair below,
    which move the noun phrase and the evidence label with the state (issue
    #168); this token stays the bare figure, and the section's own windows
    table and body still cite it."""
    sc = _cleaning_sanity_check()
    entry, why = _measured_cleaning()
    state = SUPPORTED if entry is not None else NOT_DETERMINED
    if state is NOT_DETERMINED:
        return f"{_NOT_DETERMINED_VERDICT} — {why}"
    gain, = _figures("CLEANING_EFFECT_PCT", "what the cleaning recovered",
                     known_cleaning_gain_pct=sc["known_cleaning_gain_pct"])
    _claim("CLEANING_EFFECT_PCT", "what the cleaning recovered", state,
           f"data/soiling_results.json:{_MEASURED_CLEANING_BLOCK}."
           f"known_cleaning_gain_pct is {gain!r}%, the difference-in-differences "
           f"gain analysis/soiling_analysis.py measured for the "
           f"{sc['cleaning_date']} cleaning private/household.yaml:cleaning_history "
           f"records at cost_usd {entry.get('cost_usd')!r}")
    return f"{gain:+.1f}%"


_tok("CLEANING_EFFECT_PCT", kind="derived", get=_cleaning_effect_pct,
     sources=["data/soiling_results.json:sanity_check_2024_cleaning"
              ".known_cleaning_gain_pct",
              "private/household.yaml:cleaning_history"])


# ---------------------------------------------------------------------------
# AN EVIDENCE PILL THAT FOLLOWS THE DETERMINATION STATE (ISSUE #168).
#
# report-template.html labels each claim with a pill -- <span class="pill g">
# measured</span> and its modeled/estimated siblings. Section 12's h3 carried
# a FIXED green `measured` pill beside a figure that is allowed to come back
# "not determined", so the household that gets the refusal also got a green
# stamp asserting the absent value was measured. That is CLAUDE.md section 9's
# "precision must match evidence density" broken by the markup rather than by
# the number, and section 0's "not determined" papered over.
#
# WHY IT IS A PAIR OF TOKENS AND NOT MARKUP IN A TOKEN VALUE.
# generate_report.render() HTML-escapes every substituted value with
# quote=True, so a token cannot emit <span class="pill g">measured</span> --
# it would publish as visible entity soup. So the SPAN stays fixed in the
# template and the two things that vary become tokens:
#
#     <span class="pill {{X_PILL_CLASS}}">{{X_PILL_LABEL}}</span>
#
# The template already had the label half of this shape (§13's
# `modeled · {{CARBON_SAMPLE_DESCRIPTION}}`); what is new is the class moving
# with it, so the COLOUR cannot disagree with the word.
#
# GENERAL, NOT A SECTION-12 SPECIAL CASE. _evidence_pill_tokens declares the
# pair for any claim that can come back undetermined, from one predicate --
# the same predicate the claim's own token branches on, passed in rather than
# re-derived, so the pill and the sentence cannot disagree about the state.
# SPREAD_TREND_SUMMER, SPREAD_TREND_WINTER and BATTERY_ON_MEASURED_SPREAD are
# the other three tokens in this module that can render "not determined"; they
# sit inside <!-- TODO --> blocks today, where a writer phrases the label along
# with the sentence, and they adopt this by calling it once each if any of them
# is ever moved into static markup.
#
# The class is looked up from the LABEL rather than passed separately, so a
# label and a colour cannot be set to disagree at the call site either, and an
# unknown label fails closed instead of publishing an unstyled pill.
# ---------------------------------------------------------------------------
def _pill_class(label):
    """The CSS class report-template.html's legend gives an evidence label.

    Matched on the label's FIRST segment so the qualified forms the template
    already uses ("measured · single event", "modeled · 364 days") style as
    what they are. Returns None for anything the legend does not define."""
    legend = {"measured": "g", "modeled": "y", "estimated": "r",
              _NOT_DETERMINED_VERDICT: "r"}
    return legend.get(label.split("·", 1)[0].strip().lower())


def _evidence_pill_tokens(prefix, *, determined, on, sources, off=None):
    """Declare {prefix}_PILL_CLASS and {prefix}_PILL_LABEL for one claim.

    `determined` is a zero-argument predicate -- True when the claim beside
    the pill states a figure, False when it states a refusal. `on` is the
    evidence label the determined claim earns; `off` defaults to the words
    "not determined", which is what the claim itself renders."""
    def label(ctx):
        return on if determined() else (off or _NOT_DETERMINED_VERDICT)

    def css(ctx):
        text = label(ctx)
        cls = _pill_class(text)
        if cls is None:
            raise SystemExit(
                f"report_tokens: {prefix}_PILL_CLASS has no class for the evidence "
                f"label {text!r} -- report-template.html's §14 legend defines "
                "measured / modeled / estimated")
        return cls

    # fmt="raw", attribute_only=True on the CLASS half: that value is markup,
    # not language -- the same declaration S3_ROW_CLASS and S4_ROW_CLASS carry,
    # for the same reason (see is_attribute_only()). The LABEL half is the
    # opposite: it is the word a reader reads, so it stays citable prose.
    _tok(f"{prefix}_PILL_CLASS", kind="derived", get=css, fmt="raw",
         attribute_only=True, sources=sources)
    _tok(f"{prefix}_PILL_LABEL", kind="derived", get=label, sources=sources)


def _cleaning_effect_claim(ctx):
    """Section 12's h3 claim, whole: the figure AND the noun phrase and method
    clause around it, so both follow the determination state (ISSUE #168).

    The template used to read "{{CLEANING_EFFECT_PCT}} production gain,
    difference-in-differences vs the control years", which is fixed wording
    wrapped around a value that can be a refusal. A household without the
    measured event published:

        ... ($150): not determined — <reason> production gain,
        difference-in-differences vs the control years  [measured]

    -- the reason mid-sentence, the sentence still asserting a
    difference-in-differences estimate, and a green pill on top. So the claim
    is one token: determined, it leads with the figure and names the method;
    undetermined, it leads with the quantity, says it is not determined, and
    claims no method for a value that was never computed."""
    entry, why = _measured_cleaning()
    if entry is None:
        return f"production gain {_NOT_DETERMINED_VERDICT} — {why}"
    return (f"{_cleaning_effect_pct(ctx)} production gain, "
            "difference-in-differences vs the control years")


_tok("CLEANING_EFFECT_CLAIM", kind="derived", get=_cleaning_effect_claim,
     sources=["data/soiling_results.json:sanity_check_2024_cleaning"
              ".known_cleaning_gain_pct",
              "private/household.yaml:cleaning_history"])
_evidence_pill_tokens(
    "CLEANING_EFFECT",
    determined=lambda: _measured_cleaning()[0] is not None,
    on="measured · single event",
    sources=["data/soiling_results.json:sanity_check_2024_cleaning"
             ".known_cleaning_gain_pct",
             "private/household.yaml:cleaning_history"])
_SOILING_SCENARIOS = (
    ("A", "scenario_A_this_years_evidence", "the rain-recovery end"),
    ("B", "scenario_B_2024_cleaning_evidence", "the cleaning-implied end"),
)


def _soiling_rate_range(ctx):
    """The bracket §12 reconciles: how fast the array soils, per dry month.

    TWO SCENARIOS, AND EITHER CAN BE ABSENT. soiling_analysis.py writes
    scenario B from the measured cleaning, and for a household whose
    cleaning_history does not contain that event it writes a `status` string
    in place of every field -- the same second shape SEC12_TEASER reads
    (issue #167). This token subscripted rate_pct_per_month unconditionally
    and raised KeyError on it, so resolve_all() failed and no report was
    generated at all (ISSUE #170). It is the second half of that pair: with
    both fixed, a status-shaped artifact leaves every token resolving.

    The refusal is RENDERED, not raised, for the reason CLEANING_EFFECT_PCT's
    docstring gives at length: _claim(..., NOT_DETERMINED, ...) is a
    SystemExit, and a household whose cleanings simply differ from this one's
    must still get its report. Each scenario carries its own reason, so a
    bracket with one live end still states that end rather than collapsing
    to a bare refusal -- what IS measured is published, and only what is not
    is refused (CLAUDE.md section 0).

    Precision: see _trimmed (ISSUE #137)."""
    econ = _json("soiling_results.json")["annual_economics"]
    rates, missing = {}, []
    for label, key, subject in _SOILING_SCENARIOS:
        block = econ.get(key) or {}
        rate = block.get("rate_pct_per_month")
        if rate is None:
            missing.append((subject, _strip_not_determined(block.get("status")) or
                            f"data/soiling_results.json:annual_economics.{key} states "
                            "no rate"))
        else:
            rates[label] = rate
    if rates:
        _figures("SOILING_RATE_RANGE", "how fast the array soils",
                 **{f"scenario_{label}_rate_pct_per_month": rate
                    for label, rate in rates.items()})
    if len(rates) == len(_SOILING_SCENARIOS):
        lo, hi = min(rates.values()), max(rates.values())
        return f"{_trimmed(lo)}–{_trimmed(hi)}%/month"
    reasons = "; ".join(f"{subject}: {why}" for subject, why in missing)
    if not rates:
        return f"{_NOT_DETERMINED_VERDICT} — {reasons}"
    only = _trimmed(next(iter(rates.values())))
    return (f"{only}%/month on the one scenario the artifacts settle; the rest of "
            f"the bracket is {_NOT_DETERMINED_VERDICT} — {reasons}")


_tok("SOILING_RATE_RANGE", kind="derived", get=_soiling_rate_range,
     sources=["data/soiling_results.json:annual_economics"])


# ---- Monday appendix ----------------------------------------------------
def _metric_now(ctx):
    return TOKENS["ONPEAK_IMPORT_SHARE_PCT"]["get"](ctx)


_tok("METRIC_NOW", kind="derived",
     get=lambda ctx: f"{_metric_now(ctx)}%",
     sources=["data/report_data.json:periods_chart (on-peak import share)"])
_tok("METRIC_TARGET", kind="derived",
     get=lambda ctx: (lambda dp, tot: f"~{round(dp['pw3']['onpeak_after_greedy'] / tot * 100)}%")(
         _json("battery_dispatch_policies.json"), _json("report_data.json")["totals"]["imp"]),
     sources=["data/battery_dispatch_policies.json:pw3.onpeak_after_greedy",
              "data/report_data.json:totals.imp"])


# ---- per-section one-line verdicts (issue #131) --------------------------
# CLAUDE.md section 10 requires every h2 to open with its own one-line
# conclusion. Three mechanisms carry one, and every h2 uses exactly one of
# them: an in-heading verdict ({{S4_VERDICT_SHORT}}, {{S8_VERDICT_SHORT}},
# {{S11_VERDICT_SHORT}}), a <summary> .teaser ({{SEC9_TEASER}},
# {{SEC12_TEASER}}, {{SEC13_TEASER}}), or the <p class="verdict"> line the
# tokens below fill. Rules every sentence here obeys:
#   * it opens with the same "In one sentence: " stem the hand-authored
#     section 10 verdict already uses, and it reads correctly standing alone
#     -- the token owns every sigil and unit, the template contributes none
#     (issue #129);
#   * it is PLAIN TEXT. Token values are HTML-escaped at render
#     (generate_report.py), so a tag, an entity, or a bare "&" would ship as
#     "&amp;" rather than as markup;
#   * the basic-tier ones (sections 0-7 and the Monday appendix) stay inside
#     CLAUDE.md section 10's density cap -- 35 words to the first sentence
#     break, at most one parenthetical or em-dash aside before it;
#   * a qualitative COMPARISON in the sentence is computed, not asserted, so
#     it inverts rather than lies if the artifacts move.
VERDICT_STEM = "In one sentence: "


def _battery_model_short():
    """BATTERY_MODEL without its parenthetical acronym gloss -- these
    sentences already spend their one allowed aside elsewhere."""
    return re.sub(r"\s*\([^)]*\)", "", TOKENS["BATTERY_MODEL"]["value"]).strip()


def _free_fix_saving(token):
    """(behavior_saving, package_saving, saves_money) for the free EV-charging
    fix: data/behavior_rebuild.json:scenarios.a.saved, data/package_results.
    json:packages.LOW.savings_yr, and whether the artifacts say the move is
    worth making.

    Three sentences pass through here, which is the point of it being one
    place: section 0 ("the free EV-charging fix saves a modeled $X/yr
    whatever you buy"), section 7 ("the free EV-charging fix is worth a
    modeled $X/yr") and the Monday appendix ("reprogramming the chargers this
    week ... captures the free savings").

    RELATIONSHIP: ONE DERIVED FROM THE OTHER. analysis/package_results.py
    reads behavior_rebuild.json and writes `savings_yr` as literally
    `round(scenarios.a.saved)` -- one figure and its whole-dollar rounding,
    not two measurements to be cross-checked. The previous guard compared
    their SIGNS, so a household saving $0.37/yr had sign +1 against a rounded
    sign of 0, the pair read as "two committed artifacts contradicting each
    other", and every household saving under fifty cents a year got no report
    at all (issue #131 review round 4, finding 2). What a derivation permits
    is checked instead: they may differ by up to the half-dollar the rounding
    itself moves them, and no more. Past that the artifacts really have come
    apart -- packages.LOW.savings_yr was composed from a DIFFERENT run of
    behavior_rebuild.py than the one committed beside it -- and that is a real
    contradiction, because the two are supposed to be one number.

    THE SIGN IS TAKEN ON THE ROUNDED FIGURE, which is the figure all three
    sentences PRINT: every one of them formats through _usd0, so a $0.37
    saving reads "$0/yr" on the page whichever field it came from. Deciding
    "worth doing" off the unrounded value would have sold a move the same
    sentence prices at $0, and sent a -$0.37 loss into the loss branch to
    print "costs a modeled $-0/yr".

    Three states, and ZERO IS NOT THE SAME STATE AS A LOSS:

      SUPPORTED           the rounded figure is above zero. The three
                          sentences sell it and quote it.
      SUPPORTED_OPPOSITE  it is exactly zero, or below it. Those are
                          DIFFERENT sentences, so every caller reads the sign
                          as well as the boolean: "adds no modeled saving"
                          and "costs a modeled $800/yr" are not
                          interchangeable. Rendering a modeled loss as a
                          neutral non-event is what sent the Monday appendix
                          after an afternoon's work the model prices at
                          -$800/yr (issue #131 review round 2, finding 4).
      NOT_DETERMINED      either figure is non-finite, or the pair has
                          drifted past what the rounding explains.

    A LOSS IS STATED, NOT REFUSED, and the reason is the tri-state rule
    itself: state 3 is for questions the artifacts do not settle, and a loss
    they agree on is settled. CLAUDE.md section 0 requires publishing what the
    data shows rather than withholding an unwelcome finding, and the
    reader-harm here (an appendix recommending a move the model prices as a
    loss) is cured by SAYING it loses money -- strictly more informative than
    withholding fifteen sections.
    """
    saved = _json("behavior_rebuild.json")["scenarios"]["a"]["saved"]
    low = _json("package_results.json")["packages"]["LOW"]["savings_yr"]
    _require_derived(
        token, "what the free EV-charging fix is worth", saved, low,
        _WHOLE_DOLLAR_ROUNDING,
        f"data/package_results.json:packages.LOW.savings_yr is {low!r}/yr, which is "
        f"not the whole-dollar rounding of the {saved!r}/yr its own generator reads "
        "out of data/behavior_rebuild.json:scenarios.a.saved -- the two artifacts "
        "were composed from different runs")
    saves = _sign_claim(
        token, "whether shifting EV charging is worth doing", low,
        f"data/package_results.json:packages.LOW.savings_yr is {low!r}/yr")
    return saved, low, saves


def _free_fix_clause(saving, saves, sell):
    """One free-fix clause, in the sentence the state calls for.

    `sell` is a CALLABLE returning the SUPPORTED wording (each section sells
    the move in its own words); the two SUPPORTED_OPPOSITE wordings are
    shared, because a zero and a loss mean the same thing in all three
    sections and only the selling sentence differs between them.

    A callable and not a string, because both selling sentences interpolate
    the saving behind a dollar sigil -- "saves a modeled $X/yr" -- and passing
    them by value built that string on the loss branch too, where the figure
    is negative and _usd0 has nothing honest to render (issue #131 review
    round 5, part B). The sentence that cannot be true is now never composed,
    rather than composed as "$-800" and then thrown away.

    `saving` is the WHOLE-DOLLAR figure the caller also prints, never the
    unrounded one -- see _free_fix_saving -- so the zero branch is reached by
    exactly the values that render as "$0"."""
    if saves:
        return sell()
    if saving == 0:
        return "shifting EV charging adds no modeled saving"
    return f"shifting EV charging costs a modeled {_usd0(-saving)}/yr"


# The MID package's two battery-alone scenarios, in the order section 0's
# payback range reads them: (saving field, payback field, the phrase naming
# the scenario). analysis/package_results.py sources the first from
# battery_dispatch_policies.json's pw3.greedy.save -- the price-aware battery
# billed against the UNSHIFTED baseline -- and the second from that artifact's
# post_behavior.mid.battery_marginal, the same battery billed against the year
# AFTER the EV shift.
_MID_BATTERY_SCENARIOS = (
    ("battery_alone_yr", "battery_alone_payback_yr",
     "on the unshifted baseline"),
    ("battery_alone_post_ev_fix_yr", "battery_alone_payback_post_fix_yr",
     "after the free EV-charging fix"),
)
_POST_FIX = 1     # the scenario index sections 0 and 7 word themselves off


def _battery_alone(token):
    """(repays, post_fix_saving, post_fix_payback, quotable_paybacks) for the
    MID package's battery -- ONE verdict, shared by sections 0 and 7 and by
    the section 0 payback card.

    Sections 0 and 7 describe the SAME purchase out of the SAME package and
    used to judge it off two different fields, so a mixed pair could publish
    "a Tesla Powerwall 3 does not repay its own cost" in the report's most
    prominent sentence and "adds its own $2,238/yr" in section 7, in the same
    document (issue #131 review round 2, finding 1). One decision, here.

    RELATIONSHIP, battery_alone_yr against battery_alone_post_ev_fix_yr:
    DIFFERENT SCENARIOS. Trace them through analysis/package_results.py into
    analysis/battery_dispatch_policies.py and the first is `base - billed(the
    year with a battery)` while the second is `b_sh - b2`, the same battery
    against the EV-SHIFTED year. They are not two measurements of one
    quantity; they are the battery's value before and after the free fix, and
    CLAUDE.md section 1b describes exactly that overlap as the expected
    result of modelling behavior and hardware together. A household whose
    battery value is mostly EV arbitrage the free fix already captures gets
    +$2,328 against -$50 -- an ordinary, correct answer -- and the previous
    guard, which required the two to agree in SIGN, aborted the entire report
    over it (issue #131 review round 4, finding 1). Nothing gates on their
    agreement now.

    The verdict is the POST-FIX scenario's, and only its. It is the saving
    that coexists with the behavior fix every package recommends first, it is
    what the payback quoted beside it is computed from, and it is what the
    HIGH package's expansion marginal is measured against.

    RELATIONSHIP, each saving against ITS OWN payback: ONE DERIVED FROM THE
    OTHER. package_results.py writes `round(packages.MID.cost / saving, 1)`,
    and all three of those fields are in the artifact, so the derivation is
    checkable exactly -- to the twentieth of a year its own rounding moves it.
    Past that the artifact contradicts itself about one quantity and neither
    the "sound optional buy" clause nor the "does not repay" one can be
    written on top of it. This subsumes the old "positive savings against
    non-positive paybacks" guard and is strictly tighter, while comparing
    only fields that really are two forms of one number.

    `quotable_paybacks` are the paybacks that EXIST: a scenario has one only
    where its own saving is positive, because cost divided by a loss is not a
    length of time. Section 0's card publishes them and must never print one
    of the negatives (see _payback_range).
    """
    mid = _json("package_results.json")["packages"]["MID"]
    cost = mid["cost"]
    # The cost is the numerator of every payback below, so without a usable
    # one no payback can be CHECKED -- each would be quoted on the artifact's
    # own say-so.
    #
    # But that is a reason to refuse a PAYBACK, not a reason to refuse the
    # report. Round four checked it unconditionally, up front, and so aborted
    # every household whose package_results.json carries an unusable MID cost
    # even where nothing downstream quotes a payback at all: the battery does
    # not repay, both callers render "never repays its own cost", section 0's
    # card fails closed on its own, and fifteen sections went with it anyway
    # (issue #131 review round 5, finding 4). So it is a condition on the
    # value being USED, checked below once it is known whether any payback
    # survives to be printed. The derivation check is skipped rather than
    # silently passed while the cost is unusable -- skipping it cannot leak a
    # payback, because the same unusable cost makes the refusal below fire on
    # every path that would have printed one.
    cost_ok = _finite(cost) and cost > 0
    savings, paybacks, quotable = [], [], []
    for save_key, pb_key, label in _MID_BATTERY_SCENARIOS:
        save, pb = mid[save_key], mid[pb_key]
        # The quotient has to EXIST to be compared against: a non-finite or
        # zero saving has none. A non-finite PAYBACK is not excused the same
        # way -- an infinity is exactly what a broken artifact reports beside
        # a real saving, and _require_derived refuses it by name.
        if cost_ok and _finite(save) and save != 0:
            _require_derived(
                token, f"how long the battery takes to repay its own cost {label}",
                cost / save, pb, _TENTH_YEAR_ROUNDING,
                f"data/package_results.json:packages.MID divides its {cost!r} cost by "
                f"a {save!r}/yr {save_key} to {cost / save:.4f} yr, but reports "
                f"{pb_key} as {pb!r} -- more than rounding apart, so the artifact "
                "contradicts itself about one quantity")
        savings.append(save)
        paybacks.append(pb)
        if _finite(save, pb) and save > 0 and pb > 0:
            quotable.append(pb)
    post, pb_post = savings[_POST_FIX], paybacks[_POST_FIX]
    repays = _sign_claim(
        token, "whether the battery repays its own cost", post,
        f"data/package_results.json:packages.MID reports a "
        f"battery_alone_post_ev_fix_yr of {post!r}/yr")
    # NOW the cost matters, and only now: a payback is about to be quoted --
    # by section 7's "(~6.5-yr payback)" clause, which renders whenever the
    # battery repays, or by section 0's card, which prints every quotable
    # payback -- and there is no verified numerator behind it.
    _claim(token, "how long the battery takes to repay its own cost",
           SUPPORTED if cost_ok or not (repays or quotable) else NOT_DETERMINED,
           f"data/package_results.json:packages.MID reports a cost of {cost!r}, which "
           "is not a positive, finite amount to divide by a saving")
    # A battery that repays has a payback, and both callers go on to print
    # one. The derivation check above already rules this out through every
    # path a real artifact can take -- it is here so that the emptiness can
    # never reach _payback_span as a bare min() on an empty sequence, which
    # would surface as a ValueError instead of this module's named refusal.
    _claim(token, "how long the battery takes to repay its own cost",
           SUPPORTED if quotable or not repays else NOT_DETERMINED,
           f"data/package_results.json:packages.MID reports a positive "
           f"battery_alone_post_ev_fix_yr of {post!r}/yr against a cost of {cost!r} "
           f"and paybacks of {paybacks!r}, none of which is a usable quotient")
    return repays, post, pb_post, quotable


def _payback_span(paybacks):
    """"6.2–6.5" over a spread of real paybacks, "6.5" over one -- or over
    several that print the same, since "6.5–6.5" is not a range."""
    lo, hi = f"{min(paybacks):.1f}", f"{max(paybacks):.1f}"
    return lo if lo == hi else f"{lo}–{hi}"


def _battery_warranty_years(token):
    """How many years the battery is warranted for, off the artifact whose own
    decision rule is already written in those terms.

    data/uncertainty_results.json:meta.warranty_yr is what
    analysis/uncertainty_propagation.py's WARRANTY_YR writes out, and the
    figure the rest of this analysis already decides by: that module reports
    prob_within_warranty_10yr as the battery's headline probability, and
    data/battery_sizing_curve.json stops sizing at "the first grid point whose
    own marginal kWh ... pays back ... in more than 10 years (the Powerwall 3
    warranty term)". So the threshold below is READ, not invented -- which is
    the only reason this module is allowed to have one at all (CLAUDE.md
    section 0)."""
    years = _json("uncertainty_results.json")["meta"]["warranty_yr"]
    _claim(token, "how long the battery is warranted for",
           SUPPORTED if _finite(years) and years > 0 else NOT_DETERMINED,
           f"data/uncertainty_results.json:meta.warranty_yr is {years!r}, which is not "
           "a positive, finite number of years")
    return years


def _s0_verdict(ctx):
    # "the rate plan is right" is section 3's claim, made here in the
    # report's most prominent sentence -- so it goes through section 3's own
    # ranking rather than being asserted on the strength of the plan the
    # household happens to be on. Shared helper, not a second implementation:
    # the two must never disagree about which plan wins.
    #
    # Three-way, because the ranking has three outcomes and each of them is a
    # true sentence about some household: sole cheapest, tied for cheapest,
    # beaten. The clause is the same length either way (5-6 words), which is
    # what keeps every branch inside section 10's density cap on a sentence
    # that already spends 34 of its 35 words.
    standing, plan, _plan_total, _cheapest, winners = _plan_standing(ctx, "S0_VERDICT")
    if standing == "win":
        plan_clause = "the rate plan is right"
    elif standing == "tie":
        plan_clause = "the rate plan ties for cheapest"
    else:
        plan_clause = "a cheaper rate plan exists"
    # Same shape, second claim, three states: "saves a modeled $X/yr" is false
    # at X <= 0, and an exact zero and a modeled LOSS are not the same
    # sentence either (see _free_fix_saving, shared with sections 7 and 15).
    # The WHOLE-DOLLAR figure, in the branch test and in the sentence: it is
    # what _usd0 prints either way, and passing the unrounded one sent a
    # -$0.37 saving into the loss branch to render "costs a modeled $-0/yr".
    _saved, low, free_fix_saves = _free_fix_saving("S0_VERDICT")
    fix_clause = _free_fix_clause(
        low, free_fix_saves,
        lambda: f"the free EV-charging fix saves a modeled {_usd0(low)}/yr "
                "whatever you buy")
    # Whether the battery repays its own cost is decided in ONE place, off ONE
    # scenario, shared with section 7 and with the payback card -- the three
    # used to read the package independently and could publish opposite
    # verdicts on the same battery in the same report. _battery_alone carries
    # the artifact-consistency refusals; the span below is over the paybacks
    # that exist, never over a cost divided by a loss.
    repays, _post, _pb_post, quotable = _battery_alone("S0_VERDICT")
    # "SOUND OPTIONAL BUY" IS A MAGNITUDE CLAIM, NOT A SIGN CLAIM.
    #
    # It used to be selected by `repays` alone -- battery_alone_post_ev_fix_yr
    # > 0 -- so a battery saving a few dollars a year against a ~$14,500
    # purchase read as a sound buy in the report's most prominent sentence,
    # with the payback beside it running to three figures. Nothing weighed the
    # saving or the payback against the cost (issue #131 review round 6,
    # finding 6). A sign test answers "does this repay at all"; the word
    # "sound" answers "does it repay inside the life of the thing you are
    # buying", and only the second is a purchase recommendation.
    #
    # The horizon is the battery's own warranted life, read off
    # data/uncertainty_results.json -- the same term the Monte Carlo already
    # reports its headline probability against and the sizing curve already
    # stops at. So the branch turns on a committed figure, and a household
    # whose battery repays only past that term gets the true sentence (its
    # payback, and that it lands past the warranty) rather than either a
    # confident "sound" or a withheld report. Every payback the range prints
    # is tested, not just the friendliest one: the claim is made about the
    # whole span the reader is shown.
    if repays:
        warranty = _battery_warranty_years("S0_VERDICT")
        span = _payback_span(quotable)
        if max(quotable) <= warranty:
            battery_clause = (f"a {_battery_model_short()} is a sound optional buy at a "
                              f"{span}-year hardware-alone payback")
        else:
            battery_clause = (f"a {_battery_model_short()} repays in {span} years, past "
                              f"its {warranty:g}-year warranty")
    else:
        # Left at six words on purpose. Naming the post-fix basis in the
        # clause itself ("... once the charging fix is in") reads better and
        # puts this sentence at 36 words, over CLAUDE.md section 10's cap on
        # a lead that already spends 35 -- and the cap governs every branch,
        # not just the one that renders today. Section 7 states the basis in
        # full, where the tier has room for it.
        battery_clause = f"a {_battery_model_short()} does not repay its own cost"
    # "hardware-alone" is deliberate: CLAUDE.md section 2 forbids crediting
    # the free behavior saving to the hardware, and both ends of this range
    # are package_results.json's OWN battery-alone paybacks.
    #
    # This clause used to read "the biggest win costs nothing". On the plain
    # reading -- the largest saving available is the free one -- that is
    # false on this household's own artifacts: the EV fix is worth
    # scenarios.a.saved and the battery's own post-fix saving
    # (packages.MID.battery_alone_post_ev_fix_yr) is larger. Only a
    # net-of-cost or per-dollar reading rescued it, and a reader does not
    # silently apply one to the word "biggest". What is TRUE, and what the
    # section actually needs, is that the free move is large and does not
    # depend on buying anything -- so the sentence says exactly that and
    # ranks nothing. No guard can fix a superlative that no artifact
    # supports; the honest fix is to stop claiming it.
    return f"{VERDICT_STEM}{plan_clause}, {fix_clause}, and {battery_clause}."


_tok("S0_VERDICT", kind="derived", get=_s0_verdict,
     sources=["data/behavior_rebuild.json:scenarios.a.saved",
              "data/package_results.json:packages.LOW.savings_yr (sign guard)",
              "data/package_results.json:packages.MID.battery_alone_payback_yr",
              "data/package_results.json:packages.MID.battery_alone_payback_post_fix_yr",
              "data/uncertainty_results.json:meta.warranty_yr",
              "data/plan_results.csv (the household provider's total column)",
              "private/household.yaml:household.plan", "private/household.yaml:household.cca"])


def _daily_production_series():
    """{column: {date: kWh}} for every numeric column of
    data/threeway_production_validation.csv (its first column is the date)."""
    rows = _csv_rows("threeway_production_validation.csv")
    if not rows:
        raise SystemExit("report_tokens: data/threeway_production_validation.csv is empty")
    date_key = list(rows[0])[0]
    series = {}
    for col in list(rows[0])[1:]:
        vals = {r[date_key]: float(r[col]) for r in rows if r[col] not in (None, "")}
        if vals:
            series[col] = vals
    if len(series) < 2:
        raise SystemExit("report_tokens: data/threeway_production_validation.csv has "
                          "fewer than two usable production series to correlate")
    return series


def _min_pairwise_daily_correlation():
    """The WEAKEST pairwise daily Pearson correlation among the committed
    production series, over the dates each pair shares. Weakest, not mean:
    the claim it backs is 'they all agree at least this well', which a mean
    could satisfy while one pair drifted."""
    series = _daily_production_series()
    names = sorted(series)
    worst = None
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = sorted(set(series[a]) & set(series[b]))
            if len(shared) < 30:
                raise SystemExit(f"report_tokens: production series {a!r} and {b!r} "
                                  f"share only {len(shared)} dated observations -- too "
                                  "few to quote a daily correlation from")
            xs = [series[a][d] for d in shared]
            ys = [series[b][d] for d in shared]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cx = [x - mx for x in xs]
            cy = [y - my for y in ys]
            den = (sum(v * v for v in cx) * sum(v * v for v in cy)) ** 0.5
            if den == 0:
                raise SystemExit(f"report_tokens: production series {a!r}/{b!r} has zero "
                                  "variance; a correlation is undefined")
            r = sum(u * v for u, v in zip(cx, cy)) / den
            worst = r if worst is None else min(worst, r)
    return worst


# A GUARD BOUND, not a measured or published quantity: no artifact in this
# repo reports a "minimum correlation at which two production series agree",
# and inventing a round one would be the kind of unsourced figure CLAUDE.md
# section 0 forbids. So it is composed from the two FIXED constants already
# committed in analysis/threeway_production_validation.py -- the generator
# that WRITES the file this token correlates. That script refuses to publish
# unless (a) the two reference instruments correlate at least
# REF_CORRELATION_SANITY_MIN with each other, and (b) the third, derived
# series sits no more than CORRELATION_FLOOR_BELOW_REF beneath that observed
# reference correlation. Their difference is therefore the weakest pairwise
# correlation a passing run of that generator can put into the artifact:
# below it, the file did not come from a run its own validator would have
# published, and the word "agree" has nothing left to stand on. Never
# rendered -- it gates the sentence, it does not appear in it.
MIN_AGREEMENT_CORRELATION = (tpv.REF_CORRELATION_SANITY_MIN
                             - tpv.CORRELATION_FLOOR_BELOW_REF)


def _whole_kwh_rounding_bound():
    """The +/-N kWh a printed whole-kWh bucket can hide.

    DERIVED from analysis/tou_audit.py's ROUNDING_PER_BUCKET -- the constant
    the generator itself used -- and then CROSS-CHECKED against the sentence
    data/tou_audit_summary.json's tolerance.basis prints.

    The direction is the fix (issue #131 review round 2, finding 8). The
    previous version regex-parsed that prose and returned the parsed number as
    the bound, so the digit S1_VERDICT's sentence is held to came out of a
    hand-typed string, while the docstring claimed the constant was its
    source. Now the constant IS the source and the prose is corroboration.
    Both refusals are kept, and both are real: an artifact stating a
    DIFFERENT bound was written by a different vintage of the generator and is
    not one this sentence may quote, and a tolerance.basis that no longer
    states a bound at all has stopped corroborating the digit -- neither is
    silently ignored in favour of the constant."""
    bound = ta.ROUNDING_PER_BUCKET
    basis = _json("tou_audit_summary.json")["tolerance"]["basis"]
    m = re.search(r"per-bucket rounding bound is ([\d.]+) kWh", basis)
    if not m:
        raise SystemExit(
            "report_tokens: S1_VERDICT cannot check the rounding digit it claims -- "
            "data/tou_audit_summary.json:tolerance.basis no longer states a per-bucket "
            f"rounding bound to corroborate analysis/tou_audit.py's {bound} kWh "
            f"ROUNDING_PER_BUCKET: {basis!r}")
    stated = float(m.group(1))
    if stated != bound:
        raise SystemExit(
            f"report_tokens: data/tou_audit_summary.json states a {stated} kWh per-bucket "
            f"rounding bound while analysis/tou_audit.py's ROUNDING_PER_BUCKET is "
            f"{bound}; the artifact and its generator disagree about "
            "the digit S1_VERDICT's sentence quotes")
    return bound


def _s1_verdict(ctx):
    ab = _json("tou_audit_summary.json")["rules"]["as_billed"]
    # Fail closed rather than publish a false claim: the sentence says every
    # billed bucket rebuilds, so a single failing bucket must stop the render.
    if ab["buckets_failing"]:
        raise SystemExit(
            f"report_tokens: S1_VERDICT refuses to claim all {ab['buckets']} billed TOU "
            f"buckets rebuild -- data/tou_audit_summary.json:rules.as_billed reports "
            f"{ab['buckets_failing']} failing bucket(s): {ab.get('failing_buckets')}")
    # buckets_failing alone does not prove what this sentence says. A bucket
    # PASSES the audit within max(1.0 kWh, 0.5% of billed), which on the
    # largest committed bucket (1,379 kWh) is a +/-6.9 kWh band -- while the
    # sentence claims agreement "to the whole-kWh rounding digit the
    # statements print", a +/-0.5 kWh claim. So zero failing buckets can hold
    # with the real residual ten times what the words assert. Guard the
    # quantity the sentence actually asserts: the worst residual in the file,
    # against the artifact's own stated whole-kWh rounding bound.
    bound = _whole_kwh_rounding_bound()
    residual = ab["max_abs_residual_kwh"]
    # Finiteness before the two comparisons and before the sentence prints the
    # bucket count: `nan > bound` and `nan < MIN_AGREEMENT_CORRELATION` are
    # both False, so a nan walked past BOTH refusals below and published
    # "all nan billed TOU buckets rebuild ... at nan daily correlation" as a
    # measured claim (issue #131 review round 5, part B's sweep).
    _require_finite("S1_VERDICT", "whether the billed TOU buckets rebuild",
                    buckets=ab["buckets"], max_abs_residual_kwh=residual,
                    whole_kwh_rounding_bound=bound)
    if residual > bound:
        raise SystemExit(
            f"report_tokens: S1_VERDICT refuses to claim the billed TOU buckets rebuild "
            f"to the whole-kWh rounding digit -- data/tou_audit_summary.json:rules."
            f"as_billed.max_abs_residual_kwh is {residual} kWh against the {bound} kWh "
            f"bound its own tolerance.basis states for a printed whole-kWh bucket "
            f"(the audit's own pass rule is looser, so {ab['buckets_failing']} failing "
            "bucket(s) does not establish this)")
    # Same reason, second claim. A correlation coefficient is a signed ratio,
    # so r <= 0 makes the printed number contradict the word beside it
    # ("agree at -0.1300 daily correlation") -- but a merely POSITIVE r does
    # not earn the word either: "agree at 0.0010 daily correlation" describes
    # three series that share almost no day-to-day shape. The sentence claims
    # agreement, so it has to clear the weakest agreement the artifact's own
    # generator would ever publish (MIN_AGREEMENT_CORRELATION above).
    r = _min_pairwise_daily_correlation()
    _require_finite("S1_VERDICT", "whether the production series agree",
                    weakest_pairwise_daily_correlation=r)
    if r < MIN_AGREEMENT_CORRELATION:
        raise SystemExit(
            f"report_tokens: S1_VERDICT refuses to say the independent production series "
            f"agree -- data/threeway_production_validation.csv's weakest pair correlates "
            f"at {r:.4f} daily, under the {MIN_AGREEMENT_CORRELATION:.4f} floor "
            f"analysis/threeway_production_validation.py's own publication gate implies")
    # No apostrophe, no ampersand, no tag anywhere in these sentences: token
    # values are HTML-escaped at render (an apostrophe would ship as &#x27;),
    # so the plain text is also the rendered text.
    return (f"{VERDICT_STEM}all {ab['buckets']} billed TOU buckets rebuild from the raw "
            "meter intervals to the whole-kWh rounding digit the statements print, and "
            f"the independent production series agree at "
            f"{r:.4f} daily correlation.")


_tok("S1_VERDICT", kind="derived", get=_s1_verdict,
     sources=["data/tou_audit_summary.json:rules.as_billed.buckets",
              "data/tou_audit_summary.json:rules.as_billed.buckets_failing",
              "data/tou_audit_summary.json:rules.as_billed.max_abs_residual_kwh",
              "data/tou_audit_summary.json:tolerance.basis (the whole-kWh rounding "
              "bound the sentence quotes; cross-checked against "
              "analysis/tou_audit.py:ROUNDING_PER_BUCKET)",
              "data/threeway_production_validation.csv (pairwise daily correlation)"])


def _analysis_window_dates():
    """(start, end) of the analysis year, off behavior_rebuild.json's own
    window -- the same window every other annual figure in this file uses."""
    w = _json("behavior_rebuild.json")["window"]
    return (dt.date.fromisoformat(w["start"].split(" ")[0]),
            dt.date.fromisoformat(w["end"].split(" ")[0]))


# The weighted hour-of-day profiles rebuild the year's exports to well inside
# this band today (0.001%). The bound is a publication-rounding allowance, not
# a judgment about how much drift is acceptable: report_data.json prints the
# profiles to 3 decimals and its totals to whole kWh, which alone can move the
# reconstruction by ~0.05%. 1% leaves room for that and nothing else -- a
# profile that no longer describes this window misses by far more.
_EXPORT_REBUILD_TOLERANCE = 0.01


def _midday_export_share(ctx):
    """Share of the year's exported kWh that leaves inside the tariff's own
    daytime super-off-peak run -- "midday", defined by rates.period()'s
    weekday schedule (_cheap_run()) rather than by a window picked here.

    report_data.json carries two hour-of-day export profiles, one per tariff
    season, each a mean day for its own season. Weighting them by the real
    number of summer and winter days in the analysis window (season
    membership from rates.SUMMER_MONTHS) turns them back into the year's
    exported kWh by hour, which is what a claim about WHEN exports happen
    needs; the annual totals alone say nothing about timing.

    The reconstruction is checked against report_data.json's own export total
    before any share is taken. If the weighted profiles no longer rebuild the
    artifact's own annual exports, they are not describing this window and
    nothing derived from them may be published (CLAUDE.md section 0)."""
    days = _season_day_counts()
    profiles = _hourly_export_profiles()
    lo, hi, _lab = _cheap_run()
    # The profiles are hour-of-day buckets, so a window that starts or ends
    # mid-hour cannot be summed out of them -- slicing at int(lo):int(hi)
    # would silently drop or add a whole hour of exports and publish the
    # result as this tariff's midday share. Fail closed instead.
    if lo != int(lo) or hi != int(hi):
        raise SystemExit(
            f"report_tokens: S2_VERDICT cannot time exports against a {lo}-{hi}h daytime "
            "super-off-peak run -- data/report_data.json's export profiles are hourly "
            "buckets and cannot resolve a window boundary inside an hour")
    total = midday = 0.0
    for seas, n in days.items():
        exp = profiles[seas]
        total += sum(exp) * n
        midday += sum(exp[int(lo):int(hi)]) * n
    _assert_profiles_rebuild_the_year("S2_VERDICT", "when exports happen", total)
    return midday / total


# ---------------------------------------------------------------------------
# THE ONE READER BEHIND EVERY CLAIM THIS MODULE MAKES ABOUT EXPORT HOURS.
# Three tokens ask report_data.json's hour-of-day export profiles a question --
# S2_VERDICT asks WHEN the exports leave, EXPORT_VALUE_SURPLUS_BOUND and
# EXPORT_VALUE_NETTING_BOUND ask WHAT THEY ARE WORTH under each of the two NEM
# 2.0 settlement treatments -- and every answer is only this window's if the
# profiles still rebuild the artifact's own annual export total. Split out
# rather than copied so they cannot end up validating the same artifact to
# different standards, or reading a differently-shaped one without noticing.
# ---------------------------------------------------------------------------
def _hourly_export_profiles():
    """report_data.json's two hour-of-day export profiles, shape-checked.

    Returned as committed: {"S": [24 floats], "W": [24 floats]}, each a MEAN
    DAY for its own tariff season. Callers scale them by their own day counts,
    because the two questions need different splits of the window -- the midday
    SHARE needs only the season (its window is super-off-peak on every day
    type), the export VALUE needs the day type too."""
    rd = _json("report_data.json")
    out = {}
    for seas in ("S", "W"):
        exp = rd[f"hourly_{seas}"]["exp"]
        if len(exp) != 24:
            raise SystemExit(f"report_tokens: data/report_data.json hourly_{seas}.exp has "
                             f"{len(exp)} hours, not 24 -- it is not an hour-of-day profile")
        out[seas] = exp
    return out


def _season_day_counts():
    """Days of the analysis window per tariff season."""
    start, end = _analysis_window_dates()
    days = {"S": 0, "W": 0}
    d = start
    while d <= end:
        days["S" if d.month in R.SUMMER_MONTHS else "W"] += 1
        d += dt.timedelta(days=1)
    return days


def _assert_profiles_rebuild_the_year(token, subject, total):
    """Refuse to publish anything off the profiles unless they still describe
    this window.

    RELATIONSHIP, `total` against data/report_data.json:totals.exp: SAME
    QUANTITY, INDEPENDENTLY COMPUTED. Both are the analysis window's exported
    kWh -- one summed by analysis/build_report_data.py straight off the meter
    intervals into totals.exp, the other rebuilt here by weighting that same
    generator's per-season MEAN-DAY profiles by the window's real day counts.
    Neither is derived from the other, so they are entitled to agree, and a
    disagreement past the rounding allowance means the profiles are describing
    a different year than the totals are. Evidence: they rebuild to 0.001% on
    the committed artifact (see _EXPORT_REBUILD_TOLERANCE above for why the
    allowance is publication rounding and nothing more)."""
    published = _json("report_data.json")["totals"]["exp"]
    if total <= 0:
        raise SystemExit("report_tokens: data/report_data.json's hour-of-day export "
                         "profiles carry no exported kWh; there is no timing to report")
    # Checked BEFORE it becomes a divisor. resolve_token catches KeyError,
    # IndexError, TypeError and ValueError, so a ZeroDivisionError here would
    # escape the named-SystemExit contract this module documents and surface
    # as a raw traceback -- and a household that exported nothing all year is
    # an ordinary reason for totals.exp to be zero.
    if published <= 0:
        raise SystemExit(
            f"report_tokens: {token} cannot say {subject} -- "
            f"data/report_data.json:totals.exp is {published:,.0f} kWh, so this window "
            "has no exports to describe")
    if abs(total - published) / published > _EXPORT_REBUILD_TOLERANCE:
        raise SystemExit(
            f"report_tokens: {token} refuses to say {subject} -- "
            f"data/report_data.json's season-weighted hour-of-day profiles rebuild "
            f"{total:,.0f} kWh of exports against its own totals.exp of {published:,.0f} "
            f"kWh, past the {_EXPORT_REBUILD_TOLERANCE:.0%} rebuild bound, so the profiles "
            f"do not describe this window")


# ---------------------------------------------------------------------------
# WHAT AN EXPORTED kWh IS WORTH IS A RANGE, AND THE TWO ENDS ARE TWO TOKENS.
#
# THE QUANTITY. One figure for the whole window: the price the year's exported
# kWh fetched, averaged over the hours the array actually exports in. It is not
# any single cell of the price map -- most of this profile's exports leave in
# the daytime super-off-peak run, but the rest leave in off-peak and on-peak
# hours priced several times higher, so quoting the super-off-peak cell prices
# that remainder at the wrong end of the map. The split itself is not typed
# here: section 8 publishes it, and case_s8_export_period_split_matches_the_
# profiles in test_report_consistency.py recomputes it from the same profiles,
# so there is one copy of those figures rather than two to keep in step.
#
# WHY IT IS A RANGE AND NOT A NUMBER. rates.bill_nem_monthly(), the engine
# behind every other published figure in this report, settles NEM 2.0 by
# MONTHLY PER-PERIOD NETTING. Inside one month and one TOU period an exported
# kWh first cancels an imported one, and only what is left over after the whole
# period has been netted is paid the export credit. The two treatments price
# the same kWh differently, and each is a real end of the answer:
#
#   SURPLUS  rates.credit()  = UDC + CEA         -- a kWh that nets nothing and
#                                                   is settled as surplus
#   NETTING  rates.energy()  = UDC + CEA + PCIA  -- a kWh that cancels an import
#                                                   in its own month and period
#
# THE NETTING END IS energy(), NOT allin(). allin() = energy() + NBC is what a
# GROSS IMPORT costs, and an export does not reduce gross imports:
# bill_nem_monthly() charges NBC on m[imp].sum() before any netting, so the NBC
# on the cancelled import is billed either way. Pricing an export at allin()
# would credit it with avoiding a non-bypassable charge that is still on the
# statement -- the same NBC-netting error CLAUDE.md section 9 records, one
# level up. Measured against the engine rather than argued: adding 1 kWh of
# export to a netting cell moves rates.bill_nem() by exactly energy(), and to a
# surplus cell by exactly credit(). case_the_two_export_bounds_are_the_two_
# settlement_treatments in test_report_tokens.py re-runs that probe.
#
# WHICH END DOMINATES, and why neither may be published alone. Imports exceed
# exports in all six season/period cells of data/report_data.json:period_split,
# so most of this window's exports net rather than reaching surplus and the
# truth sits nearer the netting end. "Nearer" is as far as the committed
# artifacts go: period_split is an ANNUAL total and the netting is MONTHLY, so
# nothing here resolves how any individual month settled. A bound does not
# claim to be the value, and both bounds are published so that no reader takes
# one for it.
#
# NEITHER END IS WHAT ONE MORE kW OF PANELS WOULD EARN, and section 8 must not
# be written as though it were. Exports are the RESIDUAL left after household
# load, not the shape of added production: some of an added panel's output
# would displace an import at that hour's own import rate instead of leaving
# the meter at all. Answering that needs a counterfactual re-billing of the
# year at a larger array (issue #190), which nothing committed here runs.
#
# NEITHER IS AN IMPORT RATE, and neither is an average of an import rate and an
# export price: that would be a different question (self-consumption), and
# section 8 states the import side separately as SUPER_OFF_PEAK_RATE.
# ---------------------------------------------------------------------------
def _export_value_bound(token, subject, rate, rate_name):
    """The year's exports priced hour by hour through `rate`, in $/kWh.

    `rate_name` is the rates.py function's own name, carried in rather than
    read off `rate.__name__`: the rate map entries are lambdas, so the
    attribute says "<lambda>" and a refusal message would name nothing.

    HOW IT IS WEIGHTED. report_data.json's per-season mean-day export profiles,
    scaled to the window's real day counts (the same reader and the same
    rebuild gate as _midday_export_share), then priced at the period
    rates.period() assigns each hour. The day counts are split by DAY TYPE as
    well as by season, and the day type comes from rates.off_peak_day() -- the
    holiday-aware rule rates.py insists every caller use. A bare weekday
    schedule applied to all 365 days would price the window's weekend and
    holiday mornings, 111 days of it here, on a schedule the tariff does not
    bill them under.

    WHAT THE DAY-TYPE RULE IS WORTH, so nobody re-derives it as a rounding
    argument: pricing all 365 days on the weekday schedule adds 0.75 cents at
    either end (23.64 against 22.89 surplus, 26.47 against 25.72 netting) --
    two decimals because at one the same delta prints as 0.7 on one end and
    0.8 on the other, and a reader checking the subtraction is owed digits
    that agree with the claim above them. That is
    not precision, it is the 6-10am band, which the tariff bills off-peak on a
    weekday and super-off-peak on a weekend and holiday. Getting it wrong
    misprices 111 of this window's days, not a rounding digit's worth of any of
    them.

    THE ONE ASSUMPTION, stated because it is not artifact-backed and because
    both published bounds carry it: the profiles are a mean day per SEASON, not
    per season and day type, so the day-type split assumes a weekend's export
    SHAPE matches a weekday's. The season-wide mean has already discarded the
    day type; applying it to weekday and off-peak-day counts is a modelled
    assumption rather than a reconstruction of what those days did. Solar
    production does not know what day it is; household load does, so weekend
    exports are somewhat differently shaped. No committed artifact in this repo
    splits the export profile finely enough to remove the assumption, and the
    report says so where it publishes the figures."""
    start, end = _analysis_window_dates()
    profiles = _hourly_export_profiles()
    days = {}
    d = start
    while d <= end:
        key = ("S" if d.month in R.SUMMER_MONTHS else "W", R.off_peak_day(d))
        days[key] = days.get(key, 0) + 1
        d += dt.timedelta(days=1)
    total = value = 0.0
    for (seas, is_off_peak_day), n in days.items():
        for hour, kwh in enumerate(profiles[seas]):
            total += kwh * n
            value += kwh * n * rate(seas, R.period(hour, is_off_peak_day))
    _assert_profiles_rebuild_the_year(token, subject, total)
    exported, paid = _quantities(
        token, subject,
        **{"the kWh data/report_data.json's export profiles rebuild": total,
           f"what analysis/rates.py:{rate_name}() pays for them": value})
    return paid / exported


_EXPORT_BOUND_SOURCES = [
    "data/report_data.json:hourly_S.exp / hourly_W.exp (the hour-of-day "
    "export profiles the weighting runs over)",
    "data/report_data.json:totals.exp (the annual export total the "
    "reconstruction is gated against)",
    "data/behavior_rebuild.json:window (the days the profiles are scaled by)",
    "analysis/rates.py:period() and off_peak_day() (which price each hour "
    "of each day type earns)",
    "analysis/rates.py:SUMMER_MONTHS (which season each day belongs to)",
]

_tok("EXPORT_VALUE_SURPLUS_BOUND", kind="derived", fmt="cents1",
     get=lambda ctx: _export_value_bound(
         "EXPORT_VALUE_SURPLUS_BOUND",
         "the surplus end of what an exported kWh is worth", R.credit, "credit"),
     sources=["analysis/rates.py:credit() (UDC+CEA, what NEM 2.0 pays a surplus "
              "export -- the LOW end, every exported kWh settled as surplus)"]
             + _EXPORT_BOUND_SOURCES)

_tok("EXPORT_VALUE_NETTING_BOUND", kind="derived", fmt="cents1",
     get=lambda ctx: _export_value_bound(
         "EXPORT_VALUE_NETTING_BOUND",
         "the netting end of what an exported kWh is worth", R.energy, "energy"),
     sources=["analysis/rates.py:energy() (UDC+CEA+PCIA, the netted energy rate an "
              "export cancels inside its own month and period -- the HIGH end, "
              "every exported kWh netted against an import; NOT allin(), because "
              "rates.bill_nem_monthly() charges NBC on gross imports and an export "
              "does not reduce them)"]
             + _EXPORT_BOUND_SOURCES)


def _overnight_ev_night_counts(ctx):
    """(nights with EV charging, nights without, nights observed) inside the
    tariff's overnight super-off-peak run.

    quiet_night_floor.json censuses every night of the analysis window for
    EV-session kWh in a handful of named windows, classifying each night with
    behavior_rebuild.detect_sessions() -- the same detector behind every other
    EV figure in this report. The window is looked up by the tariff's own
    overnight super-off-peak bounds, so a tariff whose overnight window the
    census never counted fails closed instead of borrowing a neighbouring
    window's answer.

    behavior_rebuild.json's own ev_kwh_sop_already cannot answer this: its
    super-off-peak bucket pools the overnight run WITH the midday one, so a
    household that charges at noon and one that charges at 3am are
    indistinguishable inside it."""
    lo, hi, _lab = _overnight_cheap_run()
    census = (_json("quiet_night_floor.json")["night_floor"]
              ["issue_114_investigation"]["ev_absence_by_window"])
    # The census is keyed by whole clock hours. int() would truncate a tariff
    # whose overnight run ends at 06:30 down to the "0-6h" key and hand back a
    # count of a window nobody measured for it -- borrowing a neighbouring
    # window's answer, which is precisely what this lookup's fail-closed
    # promise is against. A fractional bound has no key, so say so.
    if lo != int(lo) or hi != int(hi):
        raise SystemExit(
            f"report_tokens: S2_VERDICT cannot say when the EV charges -- the tariff's "
            f"overnight super-off-peak window runs {lo}-{hi}h, and "
            f"data/quiet_night_floor.json's ev_absence_by_window is keyed by whole "
            f"clock hours ({sorted(census)}), so none of its counts covers this window")
    label = f"{int(lo)}-{int(hi)}h"
    if label not in census:
        raise SystemExit(
            f"report_tokens: S2_VERDICT cannot say when the EV charges -- the tariff's "
            f"overnight super-off-peak window is {label}, which data/quiet_night_floor.json's "
            f"ev_absence_by_window never counted (it has {sorted(census)})")
    entry = census[label]
    absent, observed = entry["n"], entry["n_eligible_nights"]
    return observed - absent, absent, observed


def _s2_verdict(ctx):
    # BOTH interpolated figures go through the gate, not just the household
    # one. This sentence prints the production total AND divides it by the
    # nameplate for the specific yield, so a non-finite production published
    # "produced nan kWh at nan kWh/kW" -- while ANNUAL_PRODUCTION_KWH, the
    # LEAF token over the very same cell of the very same CSV, failed closed
    # on it (issue #131 review round 6, finding 2). Which side of the report a
    # figure arrives from is not a reason to check it or skip it: a formula
    # that interpolates a number states it here first, wherever it came from.
    production, kw_dc = _figures(
        "S2_VERDICT", "how big the array is and what it produced",
        **{"data/enphase_daily_production.csv's annual production":
           _annual_production_kwh(ctx),
           "solar.kw_dc": hh1("solar.kw_dc")})
    # The specific yield below divides by this. Same exposure as totals.exp in
    # _midday_export_share: ZeroDivisionError is not in resolve_token's caught
    # set, so a household.yaml with no array size (or a zero one) would crash
    # out of the named-SystemExit contract with a raw traceback.
    if kw_dc <= 0:
        raise SystemExit(
            f"report_tokens: S2_VERDICT cannot quote a specific yield -- "
            f"private/household.yaml:solar.kw_dc is {kw_dc}, so there is no array "
            "size to divide the year's production by")
    _start, end = _analysis_window_dates()
    pto = _as_date(hh1("household.pto_date"))
    age = end.year - pto.year - ((end.month, end.day) < (pto.month, pto.day))
    # "is healthy" was a judgment no artifact in this repo makes. Nothing
    # committed here carries a specific-yield expectation or a degradation
    # trend for this array, so the word rendered identically whatever the
    # meter said -- a claim with no computation behind it, which is what
    # CLAUDE.md section 0 forbids. Guarding it would mean inventing a
    # kWh/kW floor, and unlike S1's correlation bound there is no committed
    # gate to anchor one to. So the judgment is dropped rather than
    # threshold-guarded: the yield and the production total are measured and
    # stand on their own.
    #
    # The closing clause is this section's real conclusion, and both halves of
    # it are claims about TIMING. Annual production and export TOTALS -- all
    # this sentence used to read -- cannot support either half: the same
    # totals arise whether the array exports at noon or at dusk, and whether
    # the car charges at 3am or at 3pm. So each half now comes off an
    # hour-resolved artifact, and the one that stays qualitative is gated on
    # its own count rather than asserted.
    midday_share = _midday_export_share(ctx)
    # "charges overnight" is a habitual claim, so the census has to show the
    # habit: the EV must charge inside the overnight window on more nights
    # than it skips. Majority is the whole content of the word, not a cutoff
    # chosen to clear today's data -- on a house that charges during the day
    # the two counts swap and the clause says so instead.
    #
    # THREE states, because "does not usually charge overnight" is a claim
    # about an observed habit just as much as its opposite is. A bare
    # `charging > absent` sent a census entry of 0 charging / 0 absent across
    # 0 eligible nights straight into that second sentence, publishing a habit
    # claim with NO OBSERVATION BEHIND IT (issue #131 review round 2, finding
    # 3). Nights counted is what separates the two renderable states from the
    # unmeasured one; a census reporting more absences than nights it watched
    # is incoherent about the same quantity and goes the same way.
    #
    # RELATIONSHIP, charging against absent: ONE DERIVED FROM THE OTHER --
    # _overnight_ev_night_counts returns `observed - absent` as the charging
    # count off ONE census entry, so `charging < 0` IS the "more absences than
    # nights watched" incoherence and no separate cross-check is owed.
    # Finiteness is owed, though, and was missing: Python's json parser accepts
    # a bare NaN, and a nan census satisfies none of `observed <= 0`,
    # `absent < 0`, `charging < 0` or `charging > absent`, so it fell straight
    # through this three-state gate into the confident "does not usually charge
    # overnight" branch -- a habit claim selected by a non-number (issue #131
    # review round 4, finding 5). Every three-state gate in this module tests
    # finiteness FIRST for exactly this reason.
    lo, hi, _lab = _overnight_cheap_run()
    charging, absent, observed = _overnight_ev_night_counts(ctx)
    if not _finite(charging, absent, observed):
        ev_state = NOT_DETERMINED
    elif observed <= 0 or absent < 0 or charging < 0:
        ev_state = NOT_DETERMINED
    elif charging > absent:
        ev_state = SUPPORTED
    else:
        ev_state = SUPPORTED_OPPOSITE
    charges_overnight = _claim(
        "S2_VERDICT", "whether the EV usually charges overnight", ev_state,
        f"data/quiet_night_floor.json's ev_absence_by_window counted {charging} "
        f"charging and {absent} absent night(s) across {observed} eligible night(s) "
        f"in the tariff's {int(lo)}-{int(hi)}h overnight super-off-peak window")
    ev_clause = ("while the EV charges overnight" if charges_overnight
                 else "while the EV does not usually charge overnight")
    return (f"{VERDICT_STEM}at age {age} the {kw_dc:,.2f} kW array produced "
            f"{production:,.0f} kWh at {production / kw_dc:,.0f} kWh/kW, but "
            f"{round(midday_share * 100)}% of its exports leave in the "
            f"{_cheap_window()} window {ev_clause}.")


_tok("S2_VERDICT", kind="derived", get=_s2_verdict,
     sources=["data/enphase_daily_production.csv (Total footer row)",
              "data/report_data.json:hourly_S.exp / hourly_W.exp",
              "data/report_data.json:totals.exp (rebuild check)",
              "data/quiet_night_floor.json:night_floor.issue_114_investigation."
              "ev_absence_by_window",
              "data/behavior_rebuild.json:window.start/end",
              "analysis/rates.py:SUMMER_MONTHS", "analysis/rates.py:period() (sampled)",
              "private/household.yaml:solar.kw_dc", "private/household.yaml:household.pto_date"])


def _s3_verdict(ctx):
    # Computed, never asserted, and INVERTED rather than refused: a household
    # whose plan no longer wins is the one this section is written for, and
    # "you are on the wrong tariff, here is the one that wins and by how
    # much" is the most useful sentence section 3 can carry. The ranking
    # itself lives in _plan_ranking, shared with S0_VERDICT, which reports the
    # same three outcomes in the report's headline.
    standing, plan, plan_total, cheapest, winners = _plan_standing(ctx, "S3_VERDICT")
    # EVERY plan TOTAL in this sentence formats through _usd0_signed, in all
    # three branches. These are annual bills net of exports off
    # data/plan_results.csv -- the same field BEST_PLAN_ANNUAL_CCA is declared
    # signed for -- so a household whose modeled bill goes negative got a
    # REFUSAL instead of section 3 once _usd0 started rejecting a negative
    # amount (issue #131 review round 6, finding 5). Identical output at every
    # non-negative total, so nothing published changes.
    #
    # The DIFFERENCE on the last line keeps plain _usd0, and that is the
    # distinction the sweep is drawing rather than an oversight: this branch
    # is reached only when `plan` is not among the cheapest, so plan_total is
    # strictly above cheapest and the gap is positive by construction. A
    # negative one would mean the ranking above contradicted itself, and
    # _usd0's refusal is the right answer to that.
    if standing == "win":
        claim = (f"{plan} is still the cheapest plan for this house at a modeled "
                 f"{_usd0_signed(cheapest)}/yr, and every alternative priced costs more.")
    elif standing == "tie":
        others = [p for p in winners if p != plan]
        claim = (f"{plan} ties {_join_plan_names(others)} as the cheapest plan for this "
                 f"house at a modeled {_usd0_signed(cheapest)}/yr, and nothing priced "
                 "costs less.")
    else:
        verb = "prices" if len(winners) == 1 else "each price"
        claim = (f"{plan} is not the cheapest plan for this house at a modeled "
                 f"{_usd0_signed(plan_total)}/yr, because {_join_plan_names(winners)} "
                 f"{verb} {_usd0(plan_total - cheapest)}/yr lower.")
    return VERDICT_STEM + claim


_tok("S3_VERDICT", kind="derived", get=_s3_verdict,
     sources=["data/plan_results.csv (the household provider's total column)",
              "private/household.yaml:household.plan", "private/household.yaml:household.cca"])


def _s5_verdict(ctx):
    rd = _json("report_data.json")
    pc = rd["periods_chart"]
    on = pc["order"].index("on")
    kwh_share = pc["import_share"][on]
    cost_share = rd["onpeak"]["share_of_energy_cost"]
    # The "but ... so timing sets this bill" conclusion rests on a comparison
    # the sentence never states: it holds only while the on-peak window takes
    # a LARGER share of cost than of kWh. Check it rather than assume it --
    # and check it ON THE ROUNDED FIGURES THE SENTENCE PRINTS. Guarding the
    # unrounded shares passes at kwh 0.1720 / cost 0.1740, where the published
    # words read "takes 17% of imported kWh but 17% of the import energy cost,
    # so timing sets this bill" and the reader can see the two printed figures
    # contradict the conclusion drawn from them. The reader gets whole
    # percents, so whole percents are what has to diverge.
    #
    # And it INVERTS rather than refusing. A house whose on-peak window costs
    # no more than its share of kWh is a house where timing does not drive the
    # bill -- an ordinary result, and the one its section 5 needs to be told.
    #
    # Finiteness FIRST, before the rounding: round(float('inf')) does not
    # return an infinity, it raises OverflowError, and OverflowError was not in
    # resolve_token's caught tuple -- so a non-finite share in either artifact
    # crashed the generator with a bare traceback instead of a named refusal,
    # in the one non-finite sweep of round four that skipped this comparison
    # because its branches read as a rounding, not a division (issue #131
    # review round 5, finding 6). The tuple has ArithmeticError in it now too,
    # but that is a floor: this says WHICH share was not a number.
    _require_finite("S5_VERDICT",
                    "whether the on-peak window costs more than its share of kWh",
                    onpeak_import_share=kwh_share, onpeak_share_of_energy_cost=cost_share)
    kwh_pct, cost_pct = round(kwh_share * 100), round(cost_share * 100)
    if cost_pct > kwh_pct:
        claim = (f"{kwh_pct}% of imported kWh but {cost_pct}% of the import energy cost, "
                 "so timing, more than total consumption, sets this bill.")
    elif cost_pct == kwh_pct:
        claim = (f"{kwh_pct}% of imported kWh and the same share of the import energy "
                 "cost, so timing does not drive this bill on its own.")
    else:
        claim = (f"{kwh_pct}% of imported kWh but only {cost_pct}% of the import energy "
                 "cost, so total consumption, more than timing, sets this bill.")
    return f"{VERDICT_STEM}the {_peak_window()} on-peak window takes {claim}"


_tok("S5_VERDICT", kind="derived", get=_s5_verdict,
     sources=["data/report_data.json:periods_chart.import_share",
              "data/report_data.json:onpeak.share_of_energy_cost",
              "analysis/rates.py:period() (sampled)"])


def _s6_verdict(ctx):
    dp = _json("battery_dispatch_policies.json")
    greedy, evening = dp["pw3"]["greedy"]["save"], dp["pw3"]["evening"]["save"]
    expanded = dp["pw3x"]["greedy"]["save"]
    _require_finite("S6_VERDICT", "which of the two upgrades is worth more",
                    greedy_save=greedy, evening_save=evening, expanded_save=expanded)
    policy_gap = greedy - evening
    capacity_gap = expanded - greedy
    # The closing clause is a comparison, not a conclusion pasted in: on
    # another household's artifacts the second pack could well win. But a
    # bare > splits three cases into two and mislabels the other two.
    #   * At an exact tie neither side is worth more, and ">" quietly hands
    #     the win to the pack.
    #   * When the WINNING gap is itself <= 0, "worth more than" is
    #     comparatively true and reads as purchase guidance for an option
    #     that lowers the modeled saving -- the same reader-harm the S7
    #     guard exists to prevent. Both gaps non-positive is exactly that
    #     case (whichever is larger is still a loss or a wash), and the
    #     clause has to say so instead of ranking them.
    # Each branch is held to CLAUDE.md section 10's 35-word density cap on
    # the whole sentence, including the branches today's data never takes:
    # the lead spends 18, leaving 17 for the tail.
    if policy_gap <= 0 and capacity_gap <= 0:
        tail = "and neither the dispatch settings nor a bigger pack adds any saving"
    elif policy_gap == capacity_gap:
        tail = "and the dispatch settings and a bigger pack are worth the same"
    elif policy_gap > capacity_gap:
        tail = "so the dispatch settings are worth more than a bigger pack"
    else:
        tail = "so a bigger pack is worth more than the dispatch settings"
    # _usd0_signed, not _usd0: both figures are modeled savings and either can
    # come back negative on another household's dispatch run, which _usd0
    # would print as "$-120/yr" (issue #131 review round 2, finding 5's
    # sweep). Identical output at every non-negative value.
    return (f"{VERDICT_STEM}one {_battery_model_short()} on price-aware dispatch models "
            f"{_usd0_signed(greedy)}/yr against {_usd0_signed(evening)} on an "
            f"evening-only schedule, {tail}.")


_tok("S6_VERDICT", kind="derived", get=_s6_verdict,
     sources=["data/battery_dispatch_policies.json:pw3.greedy.save",
              "data/battery_dispatch_policies.json:pw3.evening.save",
              "data/battery_dispatch_policies.json:pw3x.greedy.save"])


def _s7_verdict(ctx):
    pk = _json("package_results.json")["packages"]
    low, mid, high = pk["LOW"], pk["MID"], pk["HIGH"]
    # "is worth a modeled $X/yr" has no honest rendering at X <= 0, so the
    # clause reads the other way there; shared with sections 0 and 15, which
    # make the same claim about the same move, and three-state, so a modeled
    # LOSS reads as a loss rather than as a neutral non-event. Both inverted
    # clauses are SHORTER than the published one, so neither can push this
    # sentence -- already at section 10's 35-word cap -- over it.
    _saved, low_savings, free_fix_saves = _free_fix_saving("S7_VERDICT")
    fix_clause = _free_fix_clause(
        low_savings, free_fix_saves,
        lambda: f"the free EV-charging fix is worth a modeled "
                f"{_usd0(low_savings)}/yr whatever you buy")
    if low["cost"]:
        raise SystemExit(f"report_tokens: S7_VERDICT refuses to call the behavior package "
                          f"free -- data/package_results.json:packages.LOW.cost is "
                          f"{_usd0_signed(low['cost'])}")
    # Whether the battery repays is _battery_alone's ONE decision, on ONE
    # field, shared with section 0 -- not a second reading of the same
    # package. battery_alone_post_ev_fix_yr, not battery_alone_yr: the payback
    # quoted beside it is the POST-fix one, and pairing the pre-fix saving
    # with the post-fix payback would mix two runs of the integrated pipeline.
    # A battery that never repays is an ordinary household's answer rather
    # than a reason to withhold the section, so that state renders too.
    repays, mid_saving, mid_payback, _quotable = _battery_alone("S7_VERDICT")
    if repays:
        battery_clause = (f"one {_battery_model_short()} adds its own "
                          f"{_usd0(mid_saving)}/yr (~{mid_payback:.1f}-yr payback)")
    else:
        battery_clause = f"one {_battery_model_short()} never repays its own cost"
    # _usd0_signed, not an inline f"${...}": this refusal fires PRECISELY when
    # the difference is non-positive, so the one branch that ever prints it is
    # the one that printed "$-500" -- the same minus-inside-the-sigil defect
    # the round-2 sweep took out of the verdict clauses, still standing in the
    # refusal beside them (issue #131 review round 4, finding 10).
    exp_cost = high["cost"] - mid["cost"]
    if exp_cost <= 0:
        raise SystemExit(
            f"report_tokens: S7_VERDICT refuses to call the HIGH package an expansion -- "
            f"data/package_results.json prices it {_usd0_signed(exp_cost)} against MID, "
            f"so there is no extra cost for the extra pack to pay back")
    marginal = high["marginal_vs_mid_yr"]
    _require_finite("S7_VERDICT", "whether the expansion pack repays its extra cost",
                    expansion_cost=exp_cost, expansion_marginal_saving=marginal)
    # Sign first, arithmetic second. At marginal <= 0 the second pack saves
    # nothing extra and no payback exists; dividing anyway returns a NEGATIVE
    # "payback" that sorts below mid_payback and publishes the opposite
    # purchase advice. Only two positive, finite paybacks are comparable.
    # The middle branch is a SLOWER payback, not an absent saving. At
    # marginal > 0 the expansion does save money (today $216/yr), so wording
    # it as "not savings" would state the opposite of
    # packages.HIGH.marginal_vs_mid_yr. It also has to stay readable as a
    # different claim from the marginal <= 0 branch: "saves too little to
    # match that payback" and "never repays" are not the same purchase
    # advice, and a reader deciding what to buy needs to know which one holds.
    # The clause is comparative because its CONDITION is comparative -- it
    # turns on the expansion's payback exceeding the first unit's, so it may
    # not assert more than that at the boundary where the two nearly tie.
    # The comparison also needs its own equality branch, for the reason the
    # section 6 tie and the section 10 zero-delta case both needed one: with
    # two branches, an expansion repaying at EXACTLY the first unit's rate
    # falls through to "faster than that", which is false. Three-way, so a tie
    # is a tie. `ratio` is compared, never re-divided, so the tie branch is
    # reachable on exactly the floats the > test rejected.
    # Each branch is also held to CLAUDE.md section 10's 35-word density cap
    # on the whole sentence, not just the one that renders today: 25 words of
    # lead leave 10 for the tail. "faster than the first unit" spent 11.
    #
    # The three comparative branches all measure against the first unit's
    # payback, so they are only available while there IS one. Where the first
    # unit never repays, "that" refers to nothing and the tail states the
    # expansion's own payback outright instead.
    if marginal <= 0:
        tail = "and the expansion pack never repays its extra cost"
    elif not repays:
        # Printed at one decimal, with the plural agreed to the printed
        # string: ":.0f" plus a hardcoded "years" published "in 1 years" for
        # anything from 0.5 to 1.5 yr and "in 0 years" for anything under
        # half a year (issue #131 review round 2, finding 7).
        years = exp_cost / marginal
        tail = (f"while the expansion pack repays its extra cost in {years:.1f} "
                f"{'year' if f'{years:.1f}' == '1.0' else 'years'}")
    # BOTH SIDES OF THE COMPARISON ON ONE BASIS. "that" refers to the payback
    # this very sentence printed six words earlier -- "(~6.5-yr payback)", the
    # artifact's own tenth-year figure -- and the tail used to compare an
    # EXACT quotient against it. Near the boundary the two are not the same
    # quantity: an expansion repaying in 6.54 yr beside a printed 6.5 read
    # "saves too little to match that" while both figures on the page said
    # 6.5, and the reverse mis-ordering is available at 6.46 (issue #131
    # review round 6, finding 7). Rounding both to the tenth of a year the
    # sentence publishes makes the comparison the one the reader can check,
    # and makes the tie branch reachable on the pairs that actually tie ON THE
    # PAGE rather than only on exactly-equal floats.
    elif (ratio := round(exp_cost / marginal, 1)) > (printed := round(mid_payback, 1)):
        tail = "and the expansion pack saves too little to match that"
    elif ratio == printed:
        tail = "and the expansion pack pays back at the same rate"
    else:
        tail = "and the expansion pack pays back faster than that"
    return f"{VERDICT_STEM}{fix_clause}; {battery_clause}, {tail}."


_tok("S7_VERDICT", kind="derived", get=_s7_verdict,
     sources=["data/package_results.json:packages.LOW.savings_yr",
              "data/behavior_rebuild.json:scenarios.a.saved (sign guard)",
              "data/package_results.json:packages.LOW.cost",
              "data/package_results.json:packages.MID.battery_alone_post_ev_fix_yr",
              "data/package_results.json:packages.MID.battery_alone_payback_post_fix_yr",
              "data/package_results.json:packages.HIGH.marginal_vs_mid_yr",
              "data/package_results.json:packages.HIGH.cost"])


def _s10_verdict(ctx):
    a = _json("cca_bundled_counterfactual.json")["direction_a_cca_repriced_at_bundled"]
    utility = hh1("household.utility")
    # The provider's own name, taken off household.cca's head exactly the way
    # GENERATION_PROVIDER_SHORT takes its acronym off the same field.
    cca_name = re.split(r"\s+[—–-]\s+|\(", hh1("household.cca"))[0].strip()
    delta = a["delta_usd_per_year"]
    # `days` is in the same check because the sentence prints it as its own
    # evidence ("same-date bill rates, 335 days"), and a non-finite day count
    # published "nan days" as the window the comparison rests on.
    _require_finite("S10_VERDICT", "which generation arrangement cost this household more",
                    delta_usd_per_year=delta, days=a["days"])
    # This clause reports a DIRECTION, and at an exact tie there is no
    # direction to report. A two-way ternary has to send delta == 0 somewhere,
    # and "less than" is where it went: "$0/yr less than bundled generation"
    # reads as the CCA being cheaper while the two cost exactly the same.
    # Same shape as section 6's tie, and worded the same way -- the equality
    # case says the two cost the same and quotes no direction at all.
    if delta == 0:
        comparison = f"the same as bundled {utility} generation"
    else:
        direction = "more than" if delta > 0 else "less than"
        comparison = f"{_usd0(abs(delta))}/yr {direction} bundled {utility} generation"
    # Both qualitative claims are computed, not asserted. "materially larger"
    # compares the EXCLUDED net-export credit against the priced delta this
    # sentence quotes. Only the SIZE adjective turns on that comparison: the
    # caveat itself ("not fully settled") rests on the artifact's own
    # excluded_net_export_note, which says the priced delta is not the full
    # answer whatever the excluded side weighs. So the adjective inverts and
    # the sentence still renders -- refusing here withheld the section from
    # any household whose unpriced side happened not to dominate, which is the
    # BETTER-evidenced household of the two.
    #
    # The size adjective had no ZERO branch, so an excluded net-export credit
    # of exactly $0 still published "a smaller, unpriced net-export effect
    # means the whole-household answer is not fully settled" -- asserting that
    # an effect exists while the artifact says nothing at all was excluded
    # (issue #131 review round 2, finding 6). Whether anything is excluded is
    # its own three-state question, asked before the adjective: at zero there
    # is no effect to size, and the caveat that rests on its existence goes
    # with it.
    unpriced = abs(a["excluded_net_export_cca_credit_usd"])
    priced = abs(a["delta_usd"])
    if not _finite(unpriced, priced):
        excl_state = NOT_DETERMINED
    elif unpriced > 0:
        excl_state = SUPPORTED
    else:
        excl_state = SUPPORTED_OPPOSITE
    has_unpriced = _claim(
        "S10_VERDICT", "whether any net-export credit is excluded from this comparison",
        excl_state,
        f"data/cca_bundled_counterfactual.json reports an "
        f"excluded_net_export_cca_credit_usd of "
        f"{a['excluded_net_export_cca_credit_usd']!r} against a priced delta_usd of "
        f"{a['delta_usd']!r}")
    if has_unpriced:
        if unpriced > priced:
            size = "a materially larger, unpriced net-export effect"
        elif unpriced == priced:
            size = "an equally large, unpriced net-export effect"
        else:
            size = "a smaller, unpriced net-export effect"
        tail = f" — {size} means the whole-household answer is not fully settled."
    else:
        tail = ", with no net-export credit excluded from it."
    return (f"{VERDICT_STEM}on the net-import energy this analysis can price, staying on "
            f"the CCA ({cca_name}) would have cost this household about "
            f"{comparison} "
            f"({a['confidence']} · same-date bill rates, {a['days']} days){tail}")


_tok("S10_VERDICT", kind="derived", get=_s10_verdict,
     sources=["data/cca_bundled_counterfactual.json:direction_a_cca_repriced_at_bundled",
              "private/household.yaml:household.utility",
              "private/household.yaml:household.cca"])


def _s14_verdict(ctx):
    d = _rates_effective_date()
    # Deliberately NOT "every figure traces to a script and an artifact":
    # section 14 itself names figures that do not (unarchived workpapers),
    # so that phrasing would overclaim under CLAUDE.md section 0.
    # "absolute BILLS", not "absolute dollars", for the same reason: the
    # report also carries absolute dollars that no statement produced (the
    # ~$14,500 battery, the package prices). CLAUDE.md sections 0 and 1
    # anchor the BILL figures to the statements and use the model only for
    # deltas, which is what section 14's own package-math paragraph says
    # ("absolute bills anchored to the ... actual"); the verdict summarises
    # that methodology rather than widening it to every dollar on the page.
    return (f"{VERDICT_STEM}absolute bills are anchored to the actual statements, savings are "
            f"model deltas at {d.month}/{d.day}/{d.year} rates, and each figure carries a "
            "confidence label with the few non-artifact-backed items named as such.")


_tok("S14_VERDICT", kind="derived", get=_s14_verdict,
     sources=["analysis/rates.py module docstring ('effective M/D/YYYY')"])


def _overnight_cheap_window():
    lo, hi, _lab = _overnight_cheap_run()
    return _fmt_hour_range(lo, hi)


def _s15_verdict(ctx):
    # This sentence names no figure, which is exactly why it needs the check:
    # "captures the free savings" reads as instruction, and at a non-positive
    # saving it would send the reader after a loss. Same helper sections 0 and
    # 7 use, so the three cannot disagree about whether the move is worth
    # making -- and, like them, the clause inverts. A Monday list that opens
    # "there is nothing left to capture here" is a useful thing to be told;
    # withholding the whole appendix over it is not.
    # Three states here too, and this is the section where the distinction
    # earns its keep: a Monday list is an INSTRUCTION list, so wording a
    # modeled loss as "adds no modeled saving" leaves the reader with no
    # reason not to spend the afternoon on it anyway. The loss branch tells
    # them to leave the schedules alone and quotes what moving them costs.
    _saved, low, free_fix_saves = _free_fix_saving("S15_VERDICT")
    if free_fix_saves:
        lead = (f"reprogramming the chargers this week to finish inside the "
                f"{_overnight_cheap_window()} super-off-peak window captures the free savings")
    elif low == 0:
        lead = (f"reprogramming the chargers into the {_overnight_cheap_window()} "
                "super-off-peak window adds no modeled saving here")
    else:
        lead = (f"leave the charger schedules alone; moving them into the "
                f"{_overnight_cheap_window()} super-off-peak window costs a modeled "
                f"{_usd0(-low)}/yr here")
    return (f"{VERDICT_STEM}{lead}; everything else on the list is verification "
            "before spending money.")


_tok("S15_VERDICT", kind="derived", get=_s15_verdict,
     sources=["analysis/rates.py:period() (sampled)",
              "data/behavior_rebuild.json:scenarios.a.saved (sign guard)",
              "data/package_results.json:packages.LOW.savings_yr (sign guard)"])


# ---- data / rate / env source inventories -------------------------------
def _monitoring_fields():
    sources = _hh_value("monitoring[].source")
    measures = _hh_value("monitoring[].measures")
    resolutions = _hh_value("monitoring[].resolution")
    return list(zip(sources, measures, resolutions))


def _data_sources_summary(ctx):
    parts = [f"utility {hh1('household.utility')} Green Button 15-minute interval data"]
    for source, measures, resolution in _monitoring_fields():
        parts.append(f"{source} ({measures}, {resolution})")
    return "; ".join(parts)


_tok("DATA_SOURCES_SUMMARY", kind="derived", get=_data_sources_summary,
     sources=["private/household.yaml:household.utility, monitoring[]"])


def _data_sources_detail(ctx):
    parts = [f"{hh1('household.utility')} Green Button 15-minute interval export "
             "and plan-comparison tool capture"]
    for source, measures, resolution in _monitoring_fields():
        parts.append(f"{source} — {measures}, {resolution}")
    return "; ".join(parts) + "."


_tok("DATA_SOURCES_DETAIL", kind="derived", get=_data_sources_detail,
     sources=["private/household.yaml:household.utility, monitoring[]"])
_tok("RATE_SOURCES_DETAIL", kind="derived",
     get=lambda ctx: (
         f"{hh1('household.utility')} Total Rates Tables and {hh1('household.cca')} "
         f"Adopted Residential Rates, both effective {_rates_effective_date().isoformat()}; "
         f"PCIA ${R.PCIA}; NBC ${R.NBC}; BSC ${R.BSC}/day (analysis/rates.py)."),
     sources=["private/household.yaml:household.utility/cca", "analysis/rates.py"])


def _env_sources_detail(ctx):
    precip = _json("soiling_results.json")["meta"]["precip_source"]
    carbon = _json("carbon_fullyear_results.json")["source"]
    return f"{precip}; {carbon['name']} (fetched {carbon['fetched']})."


_tok("ENV_SOURCES_DETAIL", kind="derived", get=_env_sources_detail,
     sources=["data/soiling_results.json:meta.precip_source",
              "data/carbon_fullyear_results.json:source"])


# ---- provenance (CLAUDE.md section 11, verbatim-required) ----------------
_tok("GENERATION_TOOL", kind="cited_constant", value="Claude Cowork (Fable 5)",
     source="CLAUDE.md section 11's required provenance sentence")
_tok("REVIEW_TOOL_1", kind="cited_constant", value="Claude Code (Fable 5)",
     source="CLAUDE.md section 11's required provenance sentence")
_tok("REVIEW_TOOL_2", kind="cited_constant", value="Codex (GPT-5.6 Sol)",
     source="CLAUDE.md section 11's required provenance sentence")


# ---- gas / electrification ------------------------------------------------
# usd0_signed on both, for ACTUAL_ANNUAL_BILL's reason: a summed year of gas
# service and a modeled annual electric baseline are bills, and a bill can
# settle in credit.
_tok("ACTUAL_ANNUAL_GAS_BILL", kind="derived",
     get=lambda ctx: sum(float(r["total_gas_service"]) for r in _csv_rows("gas_bill_summary.csv")),
     sources=["data/gas_bill_summary.csv"], fmt="usd0_signed")
_tok("MODELED_ANNUAL_AT_CURRENT_RATES", kind="data_json", file="package_results.json",
     path=("model_baseline_current_rates",), fmt="usd0_signed")
# ELECTRIFICATION_VERDICT_SHORT is declared in KNOWN_GAPS above (heat-pump
# space heating has a committed install-cost basis since issue #1; HPWH
# still does not, so a "which pencils" verdict needing both still isn't).


# ===========================================================================
# ISSUE #132. THE FIGURES A COMMITTED ARTIFACT ALREADY ANSWERS, BUT NO TOKEN
# EXPOSED.
#
# report_blocks.CLASSIFICATION marked fourteen TODO blocks "human". For eleven
# of them the fact the block asks for is sitting in a committed data/*.json or
# data/*.csv file; what was missing was a TOKENS entry, so the pipeline could
# not hand the value to a prose pass and the block stayed blocked. Eight of the
# fourteen HUMAN_REASONS entries said so in as many words -- "no
# report_tokens.py entry" -- which is a test of THIS MODULE's inventory, not of
# the evidence. That is the wrong test, and it is why the map rotted: every
# artifact added after the map was written left a stale "human" behind it.
#
# So the tokens below exist to move the test back onto the evidence. Each one
# names the artifact and field it reads, and each is written to the same three
# rules the rest of this module follows:
#
#   * a formula that interpolates a number states its preconditions first
#     (_figures / _amounts / _quantities), so a poisoned artifact field
#     produces a refusal naming the token rather than "nan kW";
#   * a clause that makes a QUALITATIVE claim resolves to one of the three
#     states above, and a household whose artifacts read the other way gets
#     the other sentence rather than a refusal;
#   * "not determined" is rendered, not papered over. Two of these blocks
#     (the per-season spread trend, the battery re-run on the measured spread)
#     have artifacts whose own published verdict IS "not determined", and the
#     tokens carry that verdict and its stated reason verbatim from the
#     artifact's own machine-readable fields.
#
# NO CROSS-ARTIFACT COMPARISON IS MADE HERE WITHOUT ITS RELATIONSHIP TRACED
# FIRST (the rule three sections up). Three pairs were considered and the
# reasoning is recorded at the site: the enphase daily mean vs
# soiling_results' own mean (different day sets by construction -- not
# compared), the EV window buckets vs the EV total (one derived from the
# other, a partition of the same series -- compared, with the rounding
# tolerance), and the tou_spread uniform ladder vs
# battery_dispatch_policies' ladder (the same quantity independently
# recomputed -- but nothing here renders both, so nothing gates on it).
# ===========================================================================

# ---------------------------------------------------------------------------
# A LEGITIMATE EMISSION IS DATA, NOT AN ERROR (issue #132, adversarial pass 2).
#
# _figures / _amounts / _quantities refuse on sight, which is what makes them
# worth having -- and which makes them the wrong thing to point at a field
# whose generator deliberately emits None, an empty list, or an explicit
# "does not apply" marker as a REAL RESULT. Guarding against the value the
# artifact happens to hold today is not guarding; the rule is to open the
# generator and establish what it can legitimately write.
#
# The instance that proved it: tou_spread.py returns payback_yr None when a
# narrowing spread leaves the battery unrecovered inside its horizon, and says
# in a comment above the emission that this is "a real result, not an error ...
# the one verdict it most needs to be able to report". Refusing it aborted the
# WHOLE report for exactly the household whose battery does not pay back.
# Treating a determinate answer as NOT_DETERMINED is the mirror image of the
# defect class this module already carries three sections of commentary about.
#
# Two shapes recur across this repo's generators, so they get one reader each:
# the applicability marker below, and the null-payback branch in
# _battery_on_measured_spread.
# ---------------------------------------------------------------------------
def _applicability(node):
    """(applies, reason) for a committed section that may say the domain does
    not exist for this household.

    Two markers, because two generator families write them and both are
    deliberate: behavior_rebuild.py / extended_findings.py emit
    {"not_applicable": True, "reason": ...} for a household whose intake flag
    is false, and heat_pump_conversion.py / all_electric_endgame.py collapse
    their whole artifact to {"applicable": False, "reason": ...}. Both
    docstrings are explicit that this is not_applicable and NOT not_determined
    -- the intake DID determine the answer, the domain simply does not exist
    here -- so the honest render is the artifact's own reason, and a refusal
    would withhold fifteen unrelated sections over a question this household
    does not have."""
    if not isinstance(node, dict):
        return True, ""
    if node.get("not_applicable") is True or node.get("applicable") is False:
        return False, str(node.get("reason", "")).strip()
    return True, ""


def _does_not_apply(reason):
    """The rendered form of an inapplicable section."""
    return "not applicable to this household" + (f" — {reason}" if reason else "")


def _measured(token, subject, unit, spec, **values):
    """ONE non-negative artifact figure, rendered with the unit the token owns.

    Issue #129's rule is that a sentence-building token carries its own sigils
    and units, which turns a one-field token into three lines of boilerplate --
    the precondition, the format, the unit. Written out at each site those
    three lines were a lambda long enough that the precondition was the easiest
    part to leave out, which is the whole failure mode _quantities exists to
    close. So they are one call."""
    value, = _quantities(token, subject, **values)
    return f"{value:{spec}} {unit}"


# ---- daily production range (section 2) -----------------------------------
_ENPHASE_DAILY = "enphase_daily_production.csv"
_ENPHASE_TOTAL_ROW = "Total"


def _enphase_daily_rows():
    """[(date, kWh)] for data/enphase_daily_production.csv's DAILY rows.

    The file ends in a "Total" footer row whose energy cell carries a
    thousands separator ("16,501.77"); _annual_production_kwh reads exactly
    that cell, and every per-day statistic must exclude exactly that row or
    the maximum is the year and the mean is off by a factor of a day."""
    rows = _csv_rows(_ENPHASE_DAILY)
    if not rows:
        raise SystemExit(f"report_tokens: data/{_ENPHASE_DAILY} is empty")
    date_key, energy_key = list(rows[0])[0], list(rows[0])[1]
    out = []
    for r in rows:
        label = (r[date_key] or "").strip()
        if label == _ENPHASE_TOTAL_ROW or not label:
            continue
        cell = (r[energy_key] or "").strip().replace(",", "")
        if not cell:
            continue
        out.append((dt.datetime.strptime(label, "%m/%d/%Y").date(), float(cell)))
    if not out:
        raise SystemExit(f"report_tokens: data/{_ENPHASE_DAILY} holds no dated daily "
                          "production rows, only its Total footer")
    return out


def _long_date(d):
    return f"{_MONTH_ABBR[d.month]} {d.day}, {d.year}"


def _daily_production_mean(ctx):
    days = _enphase_daily_rows()
    # NOT cross-checked against soiling_results.json's own
    # production_crosscheck.enphase_mean_kwh, and the relationship is why:
    # soiling_analysis.py averages the SAME file over the days it shares with
    # data/pvoutput_daily.csv (its `common` intersection), so the two means are
    # equal only while the two instruments cover identical calendars. A day
    # PVOutput missed is a legitimate divergence, not a contradiction, and
    # gating on their agreement would abort the report over an ordinary gap in
    # a second monitoring feed.
    total, = _quantities("DAILY_PRODUCTION_MEAN", "the array's mean daily output",
                         year_total_kwh=sum(v for _d, v in days))
    return f"{total / len(days):,.1f} kWh/day"


def _daily_production_extreme(token, best):
    days = _enphase_daily_rows()
    date, kwh = (max if best else min)(days, key=lambda dv: (dv[1], dv[0]))
    kwh, = _quantities(token, "the array's best or worst measured day",
                       day_kwh=kwh)
    return f"{kwh:,.1f} kWh on {_long_date(date)}"


_tok("DAILY_PRODUCTION_MEAN", kind="derived", get=_daily_production_mean,
     sources=[f"data/{_ENPHASE_DAILY} (daily rows, Total footer excluded)"])
_tok("DAILY_PRODUCTION_BEST", kind="derived",
     get=lambda ctx: _daily_production_extreme("DAILY_PRODUCTION_BEST", True),
     sources=[f"data/{_ENPHASE_DAILY} (daily rows, Total footer excluded)"])
_tok("DAILY_PRODUCTION_WORST", kind="derived",
     get=lambda ctx: _daily_production_extreme("DAILY_PRODUCTION_WORST", False),
     sources=[f"data/{_ENPHASE_DAILY} (daily rows, Total footer excluded)"])


# ---- array degradation (sections 2 and 9) ---------------------------------
def _degradation():
    return _json("gross_import_decomposition.json")["degradation"]


def _degradation_naive_range(ctx):
    """The naive multi-year trend, as the SPAN of the three estimators
    gross_import_decomposition.py fits to the same annual series.

    Three estimators of one quantity, computed by one script from one series:
    they are MEANT to differ (OLS is pulled by the endpoints, CAGR uses only
    them, Theil-Sen uses neither), so their spread is the answer and not a
    contradiction -- nothing here gates on their agreement.

    Direction is read off the span rather than assumed. An array that gained
    over the window is an ordinary array with an ordinary sentence to print,
    and a span that straddles zero is neither claim -- the third branch says
    the estimators disagree instead of picking whichever one is written last.
    """
    deg = _degradation()
    ols, cagr, theil = _figures(
        "DEGRADATION_NAIVE_RANGE", "how fast the array's output is trending",
        ols_pct_per_yr=deg["ols_pct_per_yr"], cagr_pct_per_yr=deg["cagr_pct_per_yr"],
        theil_sen_pct_per_yr=deg["theil_sen_pct_per_yr"])
    lo, hi = min(ols, cagr, theil), max(ols, cagr, theil)
    # NO CHANGE IS ITS OWN ANSWER, tested FIRST and tested on the DIGITS THIS
    # SENTENCE PRINTS (issue #132, Codex pass 1, finding 2). `hi <= 0` was the
    # first branch, so an array whose three estimators all sat at zero -- a
    # stable array, the outcome an owner most wants to hear -- published as
    # "0.0–0.0%/yr of decline", a direction word attached to a magnitude of
    # nothing. Rounding makes the same sentence reachable without an exact
    # zero: a -0.04%/yr trend also prints "0.0–0.0". So the test is on the
    # rendered tenths rather than on the raw floats, which is the only version
    # that can promise the printed figure and the printed word agree.
    if f"{abs(lo):,.1f}" == "0.0" and f"{abs(hi):,.1f}" == "0.0":
        return "no measurable change — all three estimators round to 0.0%/yr"
    if hi <= 0:
        return f"{abs(hi):,.1f}–{abs(lo):,.1f}%/yr of decline"
    if lo >= 0:
        return f"{lo:,.1f}–{hi:,.1f}%/yr of gain"
    return (f"between {lo:+,.1f}%/yr and {hi:+,.1f}%/yr — the three estimators "
            "disagree on the direction")


def _array_efficiency_series(ctx):
    eff = _degradation()["annual_efficiency_kwh_per_kw_day"]
    if not eff:
        raise SystemExit("report_tokens: ARRAY_EFFICIENCY_SERIES has no years to print -- "
                          "data/gross_import_decomposition.json:degradation."
                          "annual_efficiency_kwh_per_kw_day is empty")
    years = sorted(eff, key=str)
    values = _quantities("ARRAY_EFFICIENCY_SERIES", "the array's yearly size-normalized output",
                         **{f"year_{y}": eff[y] for y in years})
    body = " · ".join(f"{y} {v:,.2f}" for y, v in zip(years, values))
    return f"{body} kWh/kW/day"


def _degradation_weather_caveat(ctx):
    """Why the naive trend is not a degradation rate: the two confounders,
    sized against the trend itself.

    THE ONE COMPARISON HERE, and its relationship. single_event_soiling_swing_
    pct is a VALIDATED before/after gain measured across one 2024 cleaning
    (soiling_analysis.py's own figure, re-used read-only by
    gross_import_decomposition.py); total_change_pct_2021_2025 is that same
    script's multi-year change in annual size-normalized output. They are
    DIFFERENT QUANTITIES on the same array -- a single event against a
    multi-year trend -- so their sizes carry no agreement obligation at all,
    and the comparison is a magnitude reading, not a consistency gate. Both
    orderings are renderable; neither refuses."""
    deg = _degradation()
    clearsky, spread, change = _quantities(
        "DEGRADATION_WEATHER_CAVEAT", "how large the confounders are next to the trend",
        clearsky_annual_spread_pct=deg["clearsky_annual_spread_pct"],
        peak_to_trough_pct=deg["peak_to_trough_pct_2021_2025"],
        total_change_pct=abs(deg["total_change_pct_2021_2025"]))
    # gross_import_decomposition.py reads this straight off
    # soiling_results.json's sanity_check with .get(), so it is None whenever no
    # cleaning gain was ever measured -- an ordinary household that has not had
    # its panels cleaned (issue #132, /review finding 2). The geometry half of
    # this sentence stands on its own; the soiling half is simply not available.
    swing_raw = deg.get("single_event_soiling_swing_pct")
    # THE GEOMETRY CONCLUSION IS COMPUTED, NOT ASSERTED (issue #132, Codex pass
    # 1, finding 3). "varies only X%, so geometry cannot explain the Y% spread"
    # was fixed text sitting beside two numbers it never compared, so an
    # artifact whose clear-sky spread equalled or exceeded the observed one
    # published a sentence contradicting its own figures -- "varies only
    # 99.00% ... so geometry cannot explain the 14.0% spread". Both readings
    # are real and both are renderable, so the comparison selects the clause
    # rather than decorating it.
    _require_finite("DEGRADATION_WEATHER_CAVEAT",
                    "whether array geometry could account for the observed spread",
                    clearsky_annual_spread_pct=clearsky, peak_to_trough_pct=spread)
    if clearsky < spread:
        head = (f"clear-sky insolation varies only {clearsky:,.2f}% between years, so "
                f"array geometry cannot explain the {spread:,.1f}% peak-to-trough spread")
    else:
        head = (f"clear-sky insolation varies by {clearsky:,.2f}% between years, as much "
                f"as the {spread:,.1f}% peak-to-trough spread itself, so array geometry "
                "alone could account for it")
    # The same treatment for the soiling clause's own boundary: _sign_claim
    # returns SUPPORTED_OPPOSITE at EXACTLY zero, so an event precisely the
    # size of the whole change read "smaller than" it. Equality gets its own
    # words.
    if swing_raw is None:
        return (f"{head}; no cleaning gain has been measured on this array, so the size "
                "of the soiling confounder is not determined")
    swing, = _quantities("DEGRADATION_WEATHER_CAVEAT",
                         "how large the measured soiling event is",
                         single_event_soiling_swing_pct=swing_raw)
    _sign_claim(
        "DEGRADATION_WEATHER_CAVEAT",
        "whether one measured soiling event is larger than the whole multi-year change",
        swing - change,
        f"a validated single-event soiling swing of {swing}% against a "
        f"{change}% total change across the record")
    if swing > change:
        tail = f"larger than the {change:,.1f}% total change across the record"
    elif swing < change:
        tail = f"smaller than the {change:,.1f}% total change across the record"
    else:
        tail = f"exactly the size of the {change:,.1f}% total change across the record"
    return (f"{head}; one validated cleaning measured an {swing:,.1f}% single-event "
            f"soiling swing, {tail}")


_tok("DEGRADATION_NAIVE_RANGE", kind="derived", get=_degradation_naive_range,
     sources=["data/gross_import_decomposition.json:degradation "
              "(ols/cagr/theil_sen_pct_per_yr)"])
_tok("ARRAY_EFFICIENCY_SERIES", kind="derived", get=_array_efficiency_series,
     sources=["data/gross_import_decomposition.json:degradation."
              "annual_efficiency_kwh_per_kw_day"])
_tok("DEGRADATION_WEATHER_CAVEAT", kind="derived", get=_degradation_weather_caveat,
     sources=["data/gross_import_decomposition.json:degradation "
              "(clearsky_annual_spread_pct, peak_to_trough_pct_2021_2025, "
              "single_event_soiling_swing_pct, total_change_pct_2021_2025)"])


# ---- measured PV peak power vs the AC ceiling (section 9) -------------------
def _pv_ac_ceiling():
    """(ceiling, reason) -- (None, why) when the run had no PV to reconstruct.

    service_headroom.py OMITS pv_ac_ceiling entirely on a household with no
    on-site generation, and says why in its own words: "a computation that does
    not apply is not the same as one that ran and found nothing". Reading
    straight through the missing key turned that into a resolve failure that
    took the whole report with it (issue #132, /review finding 3)."""
    gr = _json("service_headroom.json").get("gross_reconstruction") or {}
    ceiling = gr.get("pv_ac_ceiling")
    if ceiling is None:
        return None, str(gr.get("identity", "")).strip()
    return ceiling, ""


def _pv_peak_or_not_applicable(fn):
    def get(ctx):
        ceiling, identity = _pv_ac_ceiling()
        if ceiling is None:
            return _does_not_apply(
                "data/service_headroom.json reconstructs no PV for this household"
                + (f" ({identity})" if identity else ""))
        return fn(ceiling)
    return get


def _pv_peak_observed(ceiling):
    corr = ceiling["corroboration"]
    if not corr:
        raise SystemExit("report_tokens: PV_PEAK_OBSERVED has no instruments to quote -- "
                          "data/service_headroom.json:gross_reconstruction.pv_ac_ceiling."
                          "corroboration is empty")
    observed = _quantities(
        "PV_PEAK_OBSERVED", "the largest output each instrument recorded",
        **{f"instrument_{i}_kw": c["observed_kw"] for i, c in enumerate(corr)})
    parts = [f"{kw:,.2f} kW ({c['instrument']})" for kw, c in zip(observed, corr)]
    return " · ".join(parts)


def _pv_peak_headroom(ceiling):
    """How close the closest instrument got to the inverter nameplate.

    Read from the artifact's own below_nameplate_by_kw rather than recomputed
    as (ceiling - observed): service_headroom.py writes both, so recomputing
    it here would be a second implementation of one subtraction with nothing
    to check it against. The SMALLEST gap is the one the clipping question
    turns on -- 'nothing got closer than this'."""
    corr = ceiling["corroboration"]
    if not corr:
        raise SystemExit("report_tokens: PV_PEAK_HEADROOM has no instruments to quote -- "
                          "data/service_headroom.json:gross_reconstruction.pv_ac_ceiling."
                          "corroboration is empty")
    gaps = _figures(
        "PV_PEAK_HEADROOM", "how far the measured peaks sit below the inverter nameplate",
        **{f"instrument_{i}_gap_kw": c["below_nameplate_by_kw"] for i, c in enumerate(corr)})
    closest = min(gaps)
    if closest < 0:
        return (f"{abs(closest):,.2f} kW ABOVE the inverter nameplate — an instrument "
                "recorded more than the array can deliver")
    return f"{closest:,.2f} kW below the inverter nameplate"


_CLEANING_PEAKS = "cleaning_study_peaks_2024.csv"


def _multiyear_peak(ctx):
    """The largest instantaneous power in data/cleaning_study_peaks_2024.csv,
    as a share of the inverter AC ceiling.

    A DIFFERENT WINDOW from the corroboration maxima above -- that file covers
    the 2024 cleaning study, this report's analysis year is later -- so a
    larger value here is not a contradiction of them and nothing gates on the
    two agreeing. What it IS is the closest approach to the ceiling anywhere in
    the committed record, which is the number the clipping question needs.

    The ceiling comes from household.yaml's solar.kw_ac, the same intake field
    service_headroom.py's own pv_ac_ceiling.basis names as its source, so the
    percentage is measurement-over-nameplate and not one artifact checked
    against another."""
    rows = _csv_rows(_CLEANING_PEAKS)
    if not rows:
        raise SystemExit(f"report_tokens: data/{_CLEANING_PEAKS} is empty")
    date_key, watt_key = list(rows[0])[0], list(rows[0])[1]
    peaks = [(r[date_key], float(r[watt_key])) for r in rows if (r[watt_key] or "").strip()]
    if not peaks:
        raise SystemExit(f"report_tokens: data/{_CLEANING_PEAKS} holds no peak-power values")
    date, watts = max(peaks, key=lambda dw: (dw[1], dw[0]))
    watts, ceiling_kw = _quantities(
        "PEAK_POWER_MULTIYEAR", "the largest measured instantaneous PV output",
        peak_w=watts, inverter_ac_ceiling_kw=float(hh1("solar.kw_ac")))
    _claim("PEAK_POWER_MULTIYEAR", "what share of the inverter ceiling the peak reached",
           SUPPORTED if ceiling_kw > 0 else NOT_DETERMINED,
           f"private/household.yaml:solar.kw_ac is {ceiling_kw!r}, which is not an "
           "inverter rating a measured peak can be expressed as a share of")
    when = dt.datetime.strptime(date.strip(), "%Y%m%d").date()
    # The FILE'S OWN SPAN, named in the sentence. This reads one 60-row
    # cleaning-study export covering part of one summer, and section 9 framed
    # it as the closest approach "anywhere in the committed record" -- a
    # superlative over a record this token never opens (issue #132, /review
    # finding 7). It says what it covers instead.
    days = sorted(dt.datetime.strptime(d.strip(), "%Y%m%d").date() for d, _w in peaks)
    window = f"{_long_date(days[0])} – {_long_date(days[-1])}"
    share = watts / 1000 / ceiling_kw * 100
    # And an instrument reading ABOVE the nameplate is a contradiction of the
    # stated ceiling, not a percentage to print as fact (finding 8).
    if watts / 1000 > ceiling_kw:
        return (f"{watts / 1000:,.2f} kW on {_long_date(when)}, ABOVE the inverter AC "
                f"ceiling — the {window} sample contradicts the stated nameplate")
    return (f"{watts / 1000:,.2f} kW on {_long_date(when)}, {share:.0f}% of the inverter "
            f"AC ceiling, across the {window} sample")


def _pv_peak_basis(ceiling):
    """What each of those maxima actually measures, and why the largest of
    them is not a ceiling.

    The three figures PV_PEAK_OBSERVED prints are not comparable readings of
    one quantity: service_headroom's own `measures` fields say one is a mean
    over an hour, one is a lower bound on production in a quarter-hour, and one
    is a five-minute AC sample. Its corroboration_reading adds that they
    corroborate the nameplate rather than establish it, and
    why_not_the_observed_maximum warns that a quarter-hour can legitimately
    carry more than a quarter of the best full hour. Without those, a reader
    cannot tell whether the numbers rule clipping in or out -- which is the
    only question section 9 asks of them."""
    corr = ceiling["corroboration"]
    if not corr:
        raise SystemExit("report_tokens: PV_PEAK_BASIS has no instruments to describe -- "
                          "data/service_headroom.json:gross_reconstruction.pv_ac_ceiling."
                          "corroboration is empty")
    missing = [c.get("instrument") for c in corr if not str(c.get("measures", "")).strip()]
    if missing:
        raise SystemExit(
            f"report_tokens: PV_PEAK_BASIS cannot say what {missing} measure -- "
            "data/service_headroom.json's corroboration entries have no `measures` text, "
            "and the peaks are not comparable without it")
    if not str(ceiling.get("corroboration_reading", "")).strip():
        raise SystemExit(
            "report_tokens: PV_PEAK_BASIS will not describe the measured peaks without "
            "data/service_headroom.json:gross_reconstruction.pv_ac_ceiling."
            "corroboration_reading, the condition that artifact states for reading them")
    parts = [f"{c['instrument']} is {c['measures']}" for c in corr]
    return (" · ".join(parts) + " — these corroborate the inverter nameplate rather than "
            "establish it, and the largest is not a per-interval ceiling: a quarter-hour "
            "can carry more than a quarter of the best full hour")


_tok("PV_PEAK_OBSERVED", kind="derived", get=_pv_peak_or_not_applicable(_pv_peak_observed),
     sources=["data/service_headroom.json:gross_reconstruction.pv_ac_ceiling.corroboration"])
_tok("PV_PEAK_BASIS", kind="derived", get=_pv_peak_or_not_applicable(_pv_peak_basis),
     sources=["data/service_headroom.json:gross_reconstruction.pv_ac_ceiling "
              "(corroboration[].measures, corroboration_reading, "
              "why_not_the_observed_maximum)"])
_tok("PV_PEAK_HEADROOM", kind="derived", get=_pv_peak_or_not_applicable(_pv_peak_headroom),
     sources=["data/service_headroom.json:gross_reconstruction.pv_ac_ceiling.corroboration"])
_tok("PEAK_POWER_MULTIYEAR", kind="derived", get=_multiyear_peak,
     sources=[f"data/{_CLEANING_PEAKS}", "private/household.yaml:solar.kw_ac"])


# ---- weather-normalized cooling regression (section 9) ---------------------
def _weather():
    return _json("weather_results.json")


def _cooling_regression_r2(ctx):
    r2, = _figures("COOLING_REGRESSION_R2", "how much of the daily load temperature explains",
                   r2=_weather()["r2"])
    _claim("COOLING_REGRESSION_R2", "how much of the daily load temperature explains",
           SUPPORTED if 0.0 <= r2 <= 1.0 else NOT_DETERMINED,
           f"data/weather_results.json:r2 is {r2!r}, which is outside the [0, 1] range a "
           "coefficient of determination can take")
    return f"{r2:.2f}"


def _cooling_sensitivity_per_100_cdd(ctx):
    """The load the fitted slope implies for 100 extra cooling-degree-days.

    Energy, not dollars: nothing committed prices a marginal cooling kWh at the
    hours a hot spell actually puts it in, and multiplying by a blended rate
    here would be exactly the year-end-lump-sum shortcut CLAUDE.md section 1b
    forbids. The slope is SIGNED (a house can draw less on hotter days) and
    prints its own minus, so this one takes _figures rather than
    _quantities."""
    slope, = _figures("COOLING_SENSITIVITY_PER_100_CDD",
                      "how much load 100 extra cooling-degree-days add",
                      kwh_per_cdd65=_weather()["kwh_per_cdd65"])
    return f"{slope * 100:,.0f} kWh"


_tok("COOLING_BASE_LOAD", kind="derived",
     get=lambda ctx: _measured("COOLING_BASE_LOAD",
                               "the temperature-independent daily load",
                               "kWh/day", ",.1f", base_kwh_day=_weather()["base_kwh_day"]),
     sources=["data/weather_results.json:base_kwh_day"])
# _figures, not _quantities: a fitted slope's sign is the fit's to decide (a
# household can draw LESS on hotter days), and a leading minus outside a sigil
# is a reading, not a malformed render.
_tok("COOLING_KWH_PER_CDD", kind="derived",
     get=lambda ctx: "%s kWh per cooling-degree-day" % f"""{_figures(
         "COOLING_KWH_PER_CDD", "the fitted cooling slope",
         kwh_per_cdd65=_weather()["kwh_per_cdd65"])[0]:,.1f}""",
     sources=["data/weather_results.json:kwh_per_cdd65"])
_tok("COOLING_REGRESSION_R2", kind="derived", get=_cooling_regression_r2,
     sources=["data/weather_results.json:r2"])
# _figures, not _quantities, for COOLING_KWH_PER_CDD's reason one line up:
# annual_cooling_kwh is that same fitted slope multiplied out over the year, so
# a fit that runs the other way produces a negative here too. Refusing it while
# publishing the slope it came from made one regression simultaneously
# publishable and not (issue #132, /review finding 4).
_tok("ANNUAL_COOLING_KWH", kind="derived",
     get=lambda ctx: "%s kWh/yr" % f"""{_figures(
         "ANNUAL_COOLING_KWH", "the year of cooling load the fit attributes",
         annual_cooling_kwh=_weather()["annual_cooling_kwh"])[0]:,.0f}""",
     sources=["data/weather_results.json:annual_cooling_kwh"])
_tok("COOLING_SENSITIVITY_PER_100_CDD", kind="derived",
     get=_cooling_sensitivity_per_100_cdd,
     sources=["data/weather_results.json:kwh_per_cdd65"])
# usd0_signed on both: these are modeled savings, whose sign the artifact
# decides -- a pre-cooling shift that costs money on this tariff is a real
# answer and belongs outside the sigil, not inside it.
_tok("PRECOOL_SHIFT_VALUE", kind="derived",
     get=lambda ctx: _usd0_signed(_weather()["precool_shift_value"]) + "/yr",
     sources=["data/weather_results.json:precool_shift_value"])
_tok("SETPOINT_VALUE", kind="derived",
     get=lambda ctx: _usd0_signed(_weather()["setpoint_value"]) + "/yr",
     sources=["data/weather_results.json:setpoint_value"])


# ---- EV charging report card (section 9) -----------------------------------
def _ev_detection():
    """behavior_rebuild.json's detector, NOT deep_results.json's.

    The two are committed and they disagree (563 sessions vs 580) because they
    detect differently -- SEC9_TEASER's own comment traces the mechanism, and
    issue #130 settled that section 9's body and every dollar figure downstream
    of it read behavior_rebuild's. Two DIFFERENT DETECTORS on the same series
    are the third relationship case: a difference is expected, nothing gates on
    their agreement, and the report cites one of them by name.

    Returns (detection, reason): behavior_rebuild.py replaces this whole block
    with its _not_applicable stub on a household whose intake says it has no
    EV, so every caller below renders that answer instead of reading fields the
    stub does not carry."""
    det = _json("behavior_rebuild.json")["detection"]
    applies, reason = _applicability(det)
    return (det if applies else None), reason


def _ev_or_not_applicable(fn):
    """A token get() that answers the artifact's own 'no EV here' when the
    detector block is a not-applicable stub, and runs `fn(detection)`
    otherwise."""
    def get(ctx):
        det, reason = _ev_detection()
        return _does_not_apply(reason) if det is None else fn(det)
    return get


# Four figures each rounded to a tenth of a kWh move a sum by at most a
# twentieth each; the epsilon is for the binary representation of the boundary,
# the same way _WHOLE_DOLLAR_ROUNDING's is.
_TENTH_KWH_ROUNDING = 0.05 + 1e-9


def _ev_window_kwh(token):
    """(total, sop, off, on), after checking the three windows really do
    partition the total.

    ONE DERIVED FROM THE OTHER, not two independent measurements:
    behavior_rebuild.py sums one EV series into rates.period()'s three labels
    ("on"/"off"/"sop" -- there is no fourth) and writes the total alongside,
    each rounded to a tenth of a kWh. So the only honest test is the sum
    against the total WITHIN four roundings' worth of slack; an equality test
    would call the rounding a contradiction, and a sign test could not see a
    missing window at all. A real failure here means the buckets no longer
    partition the series, which is precisely what makes the compliance share
    below meaningless."""
    det, _reason = _ev_detection()
    total, sop, off, on = _quantities(
        token, "how the year's EV charging splits across the tariff windows",
        ev_kwh_total=det["ev_kwh_total"], ev_kwh_sop_already=det["ev_kwh_sop_already"],
        ev_kwh_offpeak=det["ev_kwh_offpeak"], ev_kwh_onpeak=det["ev_kwh_onpeak"])
    _require_derived(
        token, "how the year's EV charging splits across the tariff windows",
        sop + off + on, total, _TENTH_KWH_ROUNDING * 4,
        f"the three TOU windows sum to {sop + off + on:,.1f} kWh while "
        f"data/behavior_rebuild.json:detection.ev_kwh_total states {total:,.1f} kWh -- "
        "they no longer partition the same series")
    return total, sop, off, on


def _ev_window_decomposition(_det):
    _total, sop, off, on = _ev_window_kwh("EV_WINDOW_DECOMPOSITION")
    return (f"{sop:,.0f} kWh already super-off-peak · {off:,.0f} kWh off-peak · "
            f"{on:,.0f} kWh on-peak")


def _ev_sop_compliance_pct(_det):
    total, sop, _off, _on = _ev_window_kwh("EV_SOP_COMPLIANCE_PCT")
    _claim("EV_SOP_COMPLIANCE_PCT", "what share of EV charging already lands super-off-peak",
           SUPPORTED if total > 0 else NOT_DETERMINED,
           f"data/behavior_rebuild.json:detection.ev_kwh_total is {total!r} kWh, which is "
           "not a year of charging a share can be taken of")
    return f"{sop / total * 100:.0f}%"


def _ev_detection_basis(det):
    """What counts as a charging session, in the detector's own words.

    "563 sessions" is not an observation, it is the output of a rule with three
    thresholds in it, and behavior_rebuild.py publishes that rule beside the
    count precisely so the count can be read. A second committed detector
    (deep_results.json) applies a different rule to the same series and reaches
    a different number; naming the rule is what lets the report say which one
    it is quoting.

    detection.ev_kwh_expected is deliberately NOT rendered here: it is a bare
    literal in the generator with no committed provenance anywhere in this
    repo, so publishing it as a cross-check would give a figure an authority
    nothing supports (CLAUDE.md section 0)."""
    rule = str(det.get("rule", "")).strip()
    if not rule:
        raise SystemExit(
            "report_tokens: EV_DETECTION_BASIS will not publish a session count without "
            "data/behavior_rebuild.json:detection.rule, the definition that count is the "
            "output of")
    return rule


# Every one of these six goes through _ev_or_not_applicable, so a household
# whose intake says it has no EV renders behavior_rebuild.py's own stated
# reason instead of aborting the report on a field its _not_applicable stub
# does not carry. EV_SESSION_COUNT loses its data_json declaration for the
# same reason: a leaf token cannot branch.
_tok("EV_SESSION_COUNT", kind="derived",
     get=_ev_or_not_applicable(lambda det: _num0(
         _quantities("EV_SESSION_COUNT", "how many charging sessions the detector found",
                     sessions=det["sessions"])[0])),
     sources=["data/behavior_rebuild.json:detection.sessions"])
_tok("EV_DETECTION_BASIS", kind="derived",
     get=_ev_or_not_applicable(_ev_detection_basis),
     sources=["data/behavior_rebuild.json:detection.rule"])
_tok("EV_ANNUAL_KWH", kind="derived",
     get=_ev_or_not_applicable(lambda det: _measured(
         "EV_ANNUAL_KWH", "the year of EV charging the detector found",
         "kWh/yr", ",.0f", ev_kwh_total=det["ev_kwh_total"])),
     sources=["data/behavior_rebuild.json:detection.ev_kwh_total"])
_tok("EV_AVG_SESSION_KWH", kind="derived",
     get=_ev_or_not_applicable(lambda det: _measured(
         "EV_AVG_SESSION_KWH", "the average charging session",
         "kWh", ",.1f", avg_session_kwh=det["avg_session_kwh"])),
     sources=["data/behavior_rebuild.json:detection.avg_session_kwh"])
_tok("EV_WINDOW_DECOMPOSITION", kind="derived",
     get=_ev_or_not_applicable(_ev_window_decomposition),
     sources=["data/behavior_rebuild.json:detection (ev_kwh_total and the three windows)"])
_tok("EV_SOP_COMPLIANCE_PCT", kind="derived",
     get=_ev_or_not_applicable(_ev_sop_compliance_pct),
     sources=["data/behavior_rebuild.json:detection (ev_kwh_total, ev_kwh_sop_already)"])


# ---- electrification: what each appliance costs and repays (section 10) ----
#
# BOTH artifacts collapse to {"applicable": False, "reason": ...} on a
# household whose intake says it has no gas service -- heat_pump_conversion.py
# and all_electric_endgame.py each return exactly that dict instead of every
# section below it. These were the first tokens in this module to read either
# file, so before issue #132's second pass a no-gas household went from getting
# a report (its gas tokens summing an empty bill corpus to zero) to getting
# none at all. Each token now answers with the artifact's own reason.
def _endgame():
    return _json("all_electric_endgame.json")


def _gas_section(file, *path):
    """(node, reason) for a gas-conversion artifact's section, or (None,
    reason) when the artifact says this household has no gas."""
    doc = _json(file)
    applies, reason = _applicability(doc)
    if not applies:
        return None, reason
    node = doc
    for key in path:
        node = node[key]
    return node, reason


def _gas_or_not_applicable(file, fn, *path):
    def get(ctx):
        node, reason = _gas_section(file, *path)
        return _does_not_apply(reason) if node is None else fn(node)
    return get


def _wh_conversion():
    return _endgame()["water_heater_conversion"]


def _payback_or_never(token, subject, node):
    """A payback_years that may legitimately be null.

    heat_pump_conversion.payback_and_npv() returns {"payback_years": None,
    "note": "no positive annual savings on this basis -- no payback"} whenever
    the conversion does not save money, and every payback in
    all_electric_endgame comes through that same function. That is the answer
    for a household the conversion does not pay off on -- and the four tokens
    reading it pushed the null through _quantities, which refuses it, aborting
    the WHOLE report for exactly that household (issue #132, /review finding
    1). It is the tou_spread null-payback defect again, at four sites the
    pass-2 sweep did not open.

    Returns None for "never repays" and a checked float otherwise, so each
    caller writes the sentence its own reader needs."""
    if node is None:
        raise SystemExit(f"report_tokens: {token} has no payback record to read for "
                          f"{subject}")
    years = node.get("payback_years")
    if years is None:
        return None
    return _quantities(token, subject, payback_years=years)[0]


def _wh_headline_payback():
    """The water heater's own payback at the artifact's OWN headline scenario.

    headline_uef names the COP scenario rather than this module picking one,
    so a regenerated artifact that moves its headline moves this token with
    it instead of silently quoting a scenario the generator no longer leads
    with."""
    wh = _wh_conversion()
    key = wh["headline_uef"]
    scenarios = wh["payback"]
    if key not in scenarios:
        raise SystemExit(
            f"report_tokens: data/all_electric_endgame.json's water heater names "
            f"{key!r} as its headline scenario, but water_heater_conversion.payback has "
            f"only {sorted(scenarios)}")
    return scenarios[key]


# ---------------------------------------------------------------------------
# THE QUALIFIER TRAVELS WITH THE FIGURE (issue #132, adversarial review pass 1).
#
# The first version of these tokens exposed all_electric_endgame.json's
# headline electrification economics -- a 30.8-year water-heater payback, a
# 96.4-year combined payback, a "cost-effective" order -- and left behind the
# caveat sitting in the SAME object saying every one of them is the pure
# 100%-water-heater computation, NOT VERIFIED against this household's actual
# appliance fuel mix. A prose block is handed its scoped token VALUES and
# nothing else, so withholding the caveat does not make the block cautious; it
# makes the block unable to be cautious. The figures would have published as
# household fact.
#
# So the rule this section follows, and which the audit behind it applied to
# all forty-three of issue #132's tokens: where the artifact itself states a
# condition for reading a number, that condition is exposed too -- INLINE with
# the value when it is short (a range, a share, a date), as its own token when
# the artifact wrote a sentence. Nothing is paraphrased into something softer,
# and no bound is invented where the artifact declines to give one: this
# household's water-heater share sensitivity is explicit that its scenarios are
# ILLUSTRATIVE and "NOT a proven bound -- the true share could be lower still",
# and the tokens below say exactly that.
# ---------------------------------------------------------------------------
def _wh_share_sensitivity():
    return _wh_conversion()["water_heater_share_sensitivity"]


def _hpwh_share_caveat(ctx):
    """What the water-heater figures rest on, in the artifact's own terms.

    Reads the two caveat strings water_heater_conversion publishes beside the
    payback -- not_verified_caveat and the sensitivity's own basis -- and
    states the two facts they turn on: the fuel-mix assumption is unverified,
    and the scenarios that propagate it are illustrative rather than a bound.
    The artifact's wording is condensed, never softened; a reader who wants the
    full paragraph has the artifact named in this token's sources."""
    wh = _wh_conversion()
    for field in ("not_verified_caveat", "upper_bound_caveat"):
        if not str(wh.get(field, "")).strip():
            raise SystemExit(
                f"report_tokens: HPWH_SHARE_CAVEAT will not publish a water-heater "
                f"payback without data/all_electric_endgame.json:water_heater_conversion."
                f"{field}, the condition that artifact states for reading it")
    share, = _figures("HPWH_SHARE_CAVEAT", "what share of the gas floor the water heater uses",
                      headline_water_heater_share=_wh_share_sensitivity()
                      ["scenarios"]["100pct_full_floor"]["water_heater_share"])
    return (f"the pure {share * 100:.0f}%-water-heater computation, NOT VERIFIED against this "
            "household's own appliance fuel mix, which is not determined — the share "
            "scenarios below are illustrative, not a proven bound, and the true share "
            "could be lower still")


def _hpwh_payback_sensitivity(ctx):
    """The same payback across the artifact's own illustrative share scenarios.

    Every scenario is named with its own share, rather than reduced to a span:
    a span reads as a bracket, and the artifact is explicit that these are NOT
    a bracket -- the true share could sit below all of them."""
    scenarios = _wh_share_sensitivity()["scenarios"]
    if not scenarios:
        raise SystemExit("report_tokens: data/all_electric_endgame.json:"
                          "water_heater_conversion.water_heater_share_sensitivity.scenarios "
                          "is empty -- there is no sensitivity to state")
    rows = []
    for key in sorted(scenarios, key=lambda k: -scenarios[k]["water_heater_share"]):
        s = scenarios[key]
        share, = _quantities("HPWH_PAYBACK_SENSITIVITY", f"the {key} scenario's share",
                             water_heater_share=s["water_heater_share"])
        years = _payback_or_never("HPWH_PAYBACK_SENSITIVITY",
                                  f"the {key} scenario's payback",
                                  s["payback"]["central_install"])
        rows.append(f"{share * 100:.0f}% share → "
                    + (f"{years:,.1f} yr" if years is not None else "never repays"))
    return " · ".join(rows) + " (illustrative scenarios, not a bound)"


def _hpwh_savings_bound(ctx):
    """Why the gas saving behind the payback is an upper bound.

    all_electric_endgame prices the WHOLE non-heating gas floor, which its own
    upper_bound_caveat says may contain end uses a heat-pump water heater does
    not replace and which this household's single unsplit gas meter cannot
    separate."""
    wh = _wh_conversion()
    if not str(wh.get("upper_bound_caveat", "")).strip():
        raise SystemExit(
            "report_tokens: HPWH_SAVINGS_BOUND has no upper_bound_caveat to state in "
            "data/all_electric_endgame.json:water_heater_conversion")
    saving, = _amounts("HPWH_SAVINGS_BOUND", "the gas saving the payback is built on",
                       floor_savings_annual_usd=wh["floor_savings_annual_usd"])
    return (f"${saving:,.0f}/yr prices the WHOLE non-heating gas floor, which one unsplit "
            "gas meter cannot separate into end uses — an upper bound on the water "
            "heater's own gas saving, not a water-heater-only figure")


def _hpwh_cost_basis(ctx):
    """What the water-heater install cost IS -- the companion to
    HEAT_PUMP_COST_BASIS, and the artifact wrote its note to draw exactly that
    contrast.

    all_electric_endgame's install_cost.note says the water-heater figure comes
    from general contractor-pricing guides and is explicitly "not a CA-specific
    engineering study the way heat_pump_conversion.py's furnace figure is". A
    reader handed "$4,200 installed" with no note reads household-specific
    pricing; the two costs in this block do not have the same standing, and
    only the furnace one could be qualified (issue #132, Codex pass 3).

    The note is RENDERED, not restated, for HEAT_PUMP_COST_BASIS's reason: the
    comparison between the two sources is one the artifact makes and this
    module cannot."""
    note = str(_wh_conversion()["install_cost"].get("note", "")).strip()
    if not note:
        raise SystemExit(
            "report_tokens: HPWH_COST_BASIS will not describe an install cost without "
            "data/all_electric_endgame.json:water_heater_conversion.install_cost.note, "
            "which is where that figure's source and its standing are stated")
    return note


def _hpwh_install_cost(ctx):
    cost = _wh_conversion()["install_cost"]
    central, low, high = _amounts(
        "HPWH_INSTALL_COST", "what a heat-pump water heater costs installed",
        central_usd=cost["central_usd"], low_usd=cost["low_usd"], high_usd=cost["high_usd"])
    return f"${central:,.0f} installed (quoted range ${low:,.0f}–{high:,.0f})"


def _heat_pump_central_payback():
    """The furnace conversion at the CENTRAL COP scenario, selected by the
    generator's own "central_" key prefix rather than by this module hardcoding
    a COP -- data/heat_pump_conversion.json's COP_SCENARIOS have moved before
    and the key would go stale silently."""
    paybacks = _json("heat_pump_conversion.json")["payback"]
    central = sorted(k for k in paybacks if str(k).startswith("central"))
    if len(central) != 1:
        raise SystemExit(
            "report_tokens: data/heat_pump_conversion.json:payback needs exactly one "
            f"'central_*' COP scenario to quote; found {central}")
    return paybacks[central[0]]


def _heat_pump_cost_basis(ctx):
    """What the furnace install cost is, and is not.

    THE QUALIFYING CLAUSE IS THE ARTIFACT'S OWN, VERBATIM. An earlier version
    paraphrased install_cost.note as "priced on an example system larger than
    this household's own sizing" -- a comparison between the study's example
    and this house that this module never made and cannot make (issue #132,
    Codex pass 1's sweep: a conclusion asserted without checking the condition
    that makes it true). heat_pump_conversion.py states that comparison itself,
    with the tonnages and the "not quantified" it belongs with, so the note is
    rendered rather than restated."""
    cost = _json("heat_pump_conversion.json")["install_cost"]
    span = cost.get("sensitivity_range_usd") or []
    if len(span) < 2:
        raise SystemExit(
            "report_tokens: HEAT_PUMP_COST_BASIS needs data/heat_pump_conversion.json:"
            f"install_cost.sensitivity_range_usd to span at least two values, got {span!r}")
    note = str(cost.get("note", "")).strip()
    if not note:
        raise SystemExit(
            "report_tokens: HEAT_PUMP_COST_BASIS will not describe an install cost without "
            "data/heat_pump_conversion.json:install_cost.note, which is where the study, "
            "its example system and its own caveats are stated")
    values = _amounts("HEAT_PUMP_COST_BASIS", "the install-cost range the study supports",
                      **{f"bound_{i}_usd": v for i, v in enumerate(span)})
    return f"${min(values):,.0f}–{max(values):,.0f} across the study's own range; {note}"


def _heat_pump_payback(ctx):
    """The furnace payback at the central efficiency scenario, WITH the span
    the other committed scenarios produce.

    The efficiency assumption dominates this figure -- the committed scenarios
    run from about a century to millennia -- so a bare central number reads as
    a precision the model does not have. Both are rendered together, and the
    scenario keys are the generator's own."""
    paybacks = _json("heat_pump_conversion.json")["payback"]
    central = _payback_or_never(
        "HEAT_PUMP_PAYBACK", "the furnace payback at the central efficiency assumption",
        _heat_pump_central_payback()["standalone"])
    span = [_payback_or_never("HEAT_PUMP_PAYBACK", f"the {k} scenario's furnace payback",
                              paybacks[k]["standalone"]) for k in sorted(paybacks)]
    repaying = [y for y in span if y is not None]
    never = len(span) - len(repaying)
    head = (f"{central:,.1f} yr at the central efficiency assumption" if central is not None
            else "never repays at the central efficiency assumption")
    if not repaying:
        return f"{head} — and on none of the {len(span)} scenarios modelled"
    tail = (f"{min(repaying):,.1f}–{max(repaying):,.1f} yr across the scenarios modelled"
            if len(repaying) > 1 else f"{repaying[0]:,.1f} yr on the only scenario that repays")
    return f"{head} ({tail}" + (f"; {never} never repay)" if never else ")")


_tok("HPWH_INSTALL_COST", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _hpwh_install_cost(None)),
     sources=["data/all_electric_endgame.json:water_heater_conversion.install_cost"])
_tok("HPWH_COST_BASIS", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _hpwh_cost_basis(None)),
     sources=["data/all_electric_endgame.json:water_heater_conversion.install_cost.note"])
_tok("HPWH_SHARE_CAVEAT", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _hpwh_share_caveat(None)),
     sources=["data/all_electric_endgame.json:water_heater_conversion.not_verified_caveat",
              "data/all_electric_endgame.json:water_heater_conversion."
              "water_heater_share_sensitivity.basis"])
_tok("HPWH_PAYBACK_SENSITIVITY", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _hpwh_payback_sensitivity(None)),
     sources=["data/all_electric_endgame.json:water_heater_conversion."
              "water_heater_share_sensitivity.scenarios"])
_tok("HPWH_SAVINGS_BOUND", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _hpwh_savings_bound(None)),
     sources=["data/all_electric_endgame.json:water_heater_conversion.upper_bound_caveat",
              "data/all_electric_endgame.json:water_heater_conversion."
              "floor_savings_annual_usd"])
_tok("HEAT_PUMP_COST_BASIS", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda _n: _heat_pump_cost_basis(None)),
     sources=["data/heat_pump_conversion.json:install_cost (note, sensitivity_range_usd)"])
_tok("HPWH_PAYBACK", kind="derived",
     get=_gas_or_not_applicable(
         "all_electric_endgame.json",
         lambda _n: (lambda y: _yr1(y) if y is not None
                     else "never repays on this basis — no positive annual saving")(
             _payback_or_never("HPWH_PAYBACK",
                               "how long the water heater takes to repay itself",
                               _wh_headline_payback()["central_install"]))),
     sources=["data/all_electric_endgame.json:water_heater_conversion.payback"
              "[headline_uef].central_install.payback_years"])
_tok("HPWH_NET_SAVINGS", kind="derived",
     get=_gas_or_not_applicable(
         "all_electric_endgame.json",
         lambda _n: _usd0_signed(_wh_headline_payback()["annual_net_savings_usd"]) + "/yr"),
     sources=["data/all_electric_endgame.json:water_heater_conversion.payback"
              "[headline_uef].annual_net_savings_usd"])
_tok("HEAT_PUMP_INSTALL_COST", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda node: _usd0(node["standalone_usd"]),
                                "install_cost"),
     sources=["data/heat_pump_conversion.json:install_cost.standalone_usd"])
def _heat_pump_marginal(field):
    """The furnace conversion priced as an UPGRADE AT REPLACEMENT TIME.

    heat_pump_conversion.py carries two install-cost bases and two paybacks:
    `standalone`, which charges the whole system to the decision, and
    `marginal_over_ac_replacement`, which charges only the difference over the
    air-conditioner replacement that was happening anyway. Section 10's own
    recommendation is replace-on-failure -- the second basis is the one that
    prices that recommendation, and only the first was tokenized, so the
    instruction concluded "wait for it to die" while showing the number for
    not waiting (issue #132, /review finding 6)."""
    doc = _json("heat_pump_conversion.json")
    if field == "cost":
        return _usd0(doc["install_cost"]["marginal_over_ac_replacement_usd"])
    node = _heat_pump_central_payback()["marginal_over_ac_replacement"]
    years = _payback_or_never("HEAT_PUMP_MARGINAL_PAYBACK",
                              "the furnace payback as an upgrade at replacement time", node)
    return (f"{years:,.1f} yr" if years is not None
            else "never repays even as an upgrade at replacement time")


_tok("HEAT_PUMP_MARGINAL_INSTALL_COST", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda _n: _heat_pump_marginal("cost")),
     sources=["data/heat_pump_conversion.json:install_cost."
              "marginal_over_ac_replacement_usd"])
_tok("HEAT_PUMP_MARGINAL_PAYBACK", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda _n: _heat_pump_marginal("payback")),
     sources=["data/heat_pump_conversion.json:payback[central_*]."
              "marginal_over_ac_replacement.payback_years"])
_tok("HEAT_PUMP_PAYBACK", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda _n: _heat_pump_payback(None)),
     sources=["data/heat_pump_conversion.json:payback[*].standalone.payback_years"])


_ENDGAME_STEP_LABEL = {"water_heater": "the water heater", "furnace": "the furnace"}


def _electrification_sequence(ctx):
    """The cost-effective order, WITH the condition under which it holds.

    The order is computed on the same unverified 100%-water-heater basis as
    every figure beside it, and the artifact's own share_robustness says both
    how far the share can fall before the order REVERSES and that on the
    furnace's other committed install-cost basis it does not survive the named
    scenarios at all. Rendering the bare order would publish "water heater
    first" as a settled recommendation; the crossover travels with it instead,
    so the qualifier cannot be dropped by whoever writes the prose."""
    seq = _endgame()["sequencing_and_paybacks"]
    order = seq["order"]
    if not order:
        raise SystemExit("report_tokens: data/all_electric_endgame.json:"
                          "sequencing_and_paybacks.order is empty -- there is no sequence "
                          "to state")
    unknown = [s for s in order if s not in _ENDGAME_STEP_LABEL]
    if unknown:
        raise SystemExit(
            f"report_tokens: ELECTRIFICATION_SEQUENCE has no label for step(s) {unknown} "
            "in data/all_electric_endgame.json:sequencing_and_paybacks.order")
    steps = ", then ".join(_ENDGAME_STEP_LABEL[s] for s in order)
    rob = seq.get("share_robustness")
    if not rob:
        raise SystemExit(
            "report_tokens: ELECTRIFICATION_SEQUENCE will not publish an order without "
            "data/all_electric_endgame.json:sequencing_and_paybacks.share_robustness, the "
            "check that artifact runs on whether the order survives its own unverified "
            "water-heater-share assumption")
    crossover, = _quantities(
        "ELECTRIFICATION_SEQUENCE", "the share at which the order reverses",
        crossover_water_heater_share=rob["crossover_water_heater_share"])
    survives = _claim(
        "ELECTRIFICATION_SEQUENCE", "whether the order survives the share uncertainty",
        SUPPORTED if rob.get("robust_across_named_scenarios") is True
        else SUPPORTED_OPPOSITE if rob.get("robust_across_named_scenarios") is False
        else NOT_DETERMINED,
        "share_robustness.robust_across_named_scenarios is "
        f"{rob.get('robust_across_named_scenarios')!r}, neither true nor false")
    held = ("holds across every illustrative share scenario modelled"
            if survives else "does NOT hold across the illustrative share scenarios modelled")
    return (f"{steps} — on an unverified water-heater-share assumption; the order {held}, "
            f"and reverses below a {crossover * 100:.0f}% water-heater share")


def _electrification_combined_payback(ctx):
    combined = _endgame()["sequencing_and_paybacks"]["complete_transition_payback"]
    years = _payback_or_never("ELECTRIFICATION_COMBINED_PAYBACK",
                              "how long the whole transition takes to repay itself",
                              combined)
    cost, = _amounts("ELECTRIFICATION_COMBINED_PAYBACK",
                     "what the whole transition costs to install",
                     combined_install_usd=combined["combined_install_usd"])
    if years is None:
        return (f"never repays the ${cost:,.0f} of installed cost — no positive annual "
                "saving on this basis")
    return f"{years:,.1f} yr on ${cost:,.0f} of installed cost"


def _electrification_incentives(ctx):
    """What the committed incentive research found for the ELECTRIFICATION
    appliances, with the date it was verified attached.

    Deliberately NOT the same fact as {{INCENTIVE_STATUS}}, which is still a
    KNOWN_GAPS token: that one is about STORAGE, and no committed artifact
    records today's federal residential storage-credit status. This one reads
    heat_pump_conversion.json's own incentives block, which does carry a
    dollar figure, a verified_date and its sources."""
    inc = _json("heat_pump_conversion.json")["incentives"]
    usd, = _amounts("ELECTRIFICATION_INCENTIVES",
                    "what electrification incentives this household can claim",
                    incentive_usd=inc["usd"])
    when = inc.get("verified_date")
    if not when:
        raise SystemExit(
            "report_tokens: data/heat_pump_conversion.json:incentives has no "
            "verified_date -- an incentive figure with no as-of date is not something "
            "this report may publish (CLAUDE.md section 0)")
    return f"${usd:,.0f} (verified {when})"


_tok("ELECTRIFICATION_SEQUENCE", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _electrification_sequence(None)),
     sources=["data/all_electric_endgame.json:sequencing_and_paybacks.order"])
_tok("ELECTRIFICATION_COMBINED_PAYBACK", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _electrification_combined_payback(None)),
     sources=["data/all_electric_endgame.json:sequencing_and_paybacks."
              "complete_transition_payback"])
def _electrification_meter_removal_caveat(ctx):
    """Why "the whole transition" is not a confirmed gas-meter removal.

    all_electric_endgame states, beside the combined payback, that neither
    payback represents a confirmed meter removal while a possible third gas end
    use remains unpriced. s10#4 publishes that payback as the cost of going
    all-electric, so the caveat has to travel with it (issue #132, /review
    finding 9's ancestor walk). Rendered verbatim, for HEAT_PUMP_COST_BASIS's
    reason: the artifact states the gap and this module cannot."""
    caveat = str(_endgame()["sequencing_and_paybacks"].get(
        "third_end_use_caveat", "")).strip()
    if not caveat:
        raise SystemExit(
            "report_tokens: ELECTRIFICATION_METER_REMOVAL_CAVEAT will not publish a "
            "whole-transition payback without data/all_electric_endgame.json:"
            "sequencing_and_paybacks.third_end_use_caveat, which states what that "
            "payback does and does not establish")
    return caveat


_tok("ELECTRIFICATION_METER_REMOVAL_CAVEAT", kind="derived",
     get=_gas_or_not_applicable("all_electric_endgame.json",
                                lambda _n: _electrification_meter_removal_caveat(None)),
     sources=["data/all_electric_endgame.json:sequencing_and_paybacks."
              "third_end_use_caveat"])
_tok("ELECTRIFICATION_INCENTIVES", kind="derived",
     get=_gas_or_not_applicable("heat_pump_conversion.json",
                                lambda _n: _electrification_incentives(None)),
     sources=["data/heat_pump_conversion.json:incentives"])


# ---- cleaning cadence (section 12) -----------------------------------------
def _cleaning_cadence():
    """data/extra_results.json's optimal-cadence model, keyed by soiling rate.

    The keys ARE the modeled soiling rates in %/month (TECHNICAL.md section
    3.11), so the bracket this token names is read off the artifact's own keys
    rather than assumed to match SOILING_RATE_RANGE's -- that token brackets
    two scenarios in data/soiling_results.json, a different pair of estimates,
    and asserting the two brackets are the same would be a cross-artifact claim
    nothing here checks."""
    cad = _json("extra_results.json")["cleaning"]
    if not cad:
        raise SystemExit("report_tokens: data/extra_results.json:cleaning is empty -- "
                          "there is no cadence model to quote")
    return cad, sorted(cad, key=lambda k: float(k))


def _cleaning_best_month(ctx):
    """When one cleaning a year should happen.

    The three soiling rates are DIFFERENT SCENARIOS of the same model, so
    disagreement between them is an expected outcome and not a contradiction:
    when they agree the answer is one date, and when they do not, every
    scenario's own answer is named. Nothing refuses on their disagreement."""
    cad, keys = _cleaning_cadence()
    picks = [str(cad[k]["best1"]) for k in keys]
    if len(set(picks)) == 1:
        return f"{picks[0]}, at every soiling rate modelled"
    return " · ".join(f"{k}%/month: {p}" for k, p in zip(keys, picks))


def _cleaning_value_range(token, field, tail):
    cad, keys = _cleaning_cadence()
    values = _amounts(token, "what a cleaning is worth at each modelled soiling rate",
                      **{f"soiling_{k.replace('.', '_')}_pct_per_month": cad[k][field]
                         for k in keys})
    lo, hi = min(values), max(values)
    return (f"${lo:,.0f}–{hi:,.0f}/yr across the {float(keys[0]):g}–{float(keys[-1]):g}"
            f"%/month soiling bracket{tail}")


_SOP_SEASON_LABEL = {"S": "summer", "W": "winter"}


def _midday_marginal_value_range(ctx):
    """What a marginal midday kWh is actually worth, as the RANGE it is.

    s12#5's caveat used to compare the cadence model's pricing against
    {{SUPER_OFF_PEAK_RATE}} -- rates.allin("S", "sop"), the SUMMER SUPER-OFF-PEAK
    IMPORT rate -- and called it "what a marginal midday kWh earns" (issue #132,
    Codex pass 2). Three things are wrong with that comparator and only the
    direction of the conclusion survives them:

      * recovered midday production that is EXPORTED earns the export credit,
        not an import rate it never offsets;
      * the token is summer-only, and the winter cells differ;
      * self-consumed and exported energy are two different readings, so the
        answer is a span, not a point.

    So all four super-off-peak cells are read from the canonical rates module
    -- both seasons, both sides -- and the span between them is the answer.
    WHICH END IS WHICH IS COMPUTED, not assumed: nothing here asserts that the
    export credit is the lower one, because that is exactly the shape of
    assertion the previous pass spent its findings on. rates.py rather than
    extra_results.json's frozen price_map copy, for CLAUDE.md section 9's
    single-rates-module rule -- the copy is itself cross-checked against these
    same calls by quiet_night_floor.py."""
    readings = []
    for season in sorted(_SOP_SEASON_LABEL):
        label = _SOP_SEASON_LABEL[season]
        readings.append((f"{label} export credit", R.credit(season, "sop")))
        readings.append((f"{label} super-off-peak import", R.allin(season, "sop")))
    values = _quantities(
        "MIDDAY_MARGINAL_VALUE_RANGE", "what a marginal midday kWh is worth",
        **{f"cell_{i}": v for i, (_lab, v) in enumerate(readings)})
    priced = sorted(zip(values, (lab for lab, _v in readings)))
    (lo, lo_label), (hi, hi_label) = priced[0], priced[-1]
    if lo == hi:
        return (f"{_cents1(lo)}/kWh — every super-off-peak cell, import-offset and "
                "export-credit, prices the same in both seasons")
    return (f"{_cents1(lo)}–{_cents1(hi)}/kWh — {lo_label} at the low end, "
            f"{hi_label} at the high end")


_tok("MIDDAY_MARGINAL_VALUE_RANGE", kind="derived", get=_midday_marginal_value_range,
     sources=["analysis/rates.py: allin(season, 'sop') and credit(season, 'sop') "
              "for both seasons -- the four super-off-peak price-map cells"])
_tok("CLEANING_BEST_MONTH", kind="derived", get=_cleaning_best_month,
     sources=["data/extra_results.json:cleaning[*].best1"])
_tok("CLEANING_SINGLE_VALUE_RANGE", kind="derived",
     get=lambda ctx: _cleaning_value_range("CLEANING_SINGLE_VALUE_RANGE", "save1", ""),
     sources=["data/extra_results.json:cleaning[*].save1"])
_tok("CLEANING_SECOND_MARGINAL_RANGE", kind="derived",
     get=lambda ctx: _cleaning_value_range("CLEANING_SECOND_MARGINAL_RANGE", "marginal2nd",
                                           ", for the second cleaning of the year"),
     sources=["data/extra_results.json:cleaning[*].marginal2nd"])


# ---- the measured TOU spread, per season (section 13) ----------------------
_NOT_DETERMINED_VERDICT = "not determined"


def _spread_season(token, season):
    ds = _json("tou_spread.json")["delivery_spread"]
    if season not in ds:
        raise SystemExit(f"report_tokens: data/tou_spread.json:delivery_spread has no "
                          f"{season!r} season for {token} to report")
    return ds[season]


def _spread_corpus(token, season, s):
    """As much of the corpus line as the artifact actually carries.

    tou_spread._fit_spread has THREE exits and only the last one is fully
    populated: a season with fewer than three paired observations returns
    {"n", "verdict"} alone, and one whose fit is undefined returns {"n",
    "n_independent", "verdict"}. Both are ordinary outcomes on a short bill
    corpus -- exactly the household most likely to be reading this report for
    the first time -- so the fields are reported when present and skipped when
    not, rather than being read unconditionally and aborting the whole run on a
    KeyError."""
    parts = []
    for key, unit in (("n", "priced observations"),
                      ("n_independent", "independent rate changes"),
                      ("span_days", "days")):
        if s.get(key) is None:
            continue
        value, = _quantities(token, f"how much rate history the {season} spread rests on",
                             **{key: s[key]})
        parts.append(f"{value:,.0f} {unit}")
    return ", ".join(parts)


def _spread_trend(token, season):
    """Either the surviving trend or the artifact's own "not determined", with
    the reason it publishes for it.

    tou_spread.py writes `verdict` as a machine-readable field precisely so a
    consumer does not have to re-derive the adequacy rules, and CLAUDE.md
    section 0 makes "not determined" a required answer rather than a hole to
    fill -- so this token renders it, reason and corpus and all.

    THE VERDICT IS MATCHED BY PREFIX, NOT BY EQUALITY. _fit_spread's two
    degenerate exits write "not determined -- fewer than 3 paired observations"
    and "not determined -- fit undefined on N independent level(s)": both ARE
    the not-determined answer and carry their reason inside the verdict string,
    and an equality test sent them down the branch that reads
    escalation_pct_yr, which those exits do not write (issue #132, adversarial
    pass 2's sweep). Likewise not_determined_because can legitimately come back
    empty -- tou_spread's own battery block defaults it -- so an absent reason
    is reported as absent rather than raised on."""
    s = _spread_season(token, season)
    verdict = str(s.get("verdict", "")).strip()
    if not verdict:
        raise SystemExit(
            f"report_tokens: {token} has no verdict to report -- "
            f"data/tou_spread.json:delivery_spread.{season} states none, and this "
            "sentence is that verdict")
    corpus = _spread_corpus(token, season, s)
    if verdict.lower().startswith(_NOT_DETERMINED_VERDICT):
        # The reason lives in not_determined_because when the fit ran, and
        # inside the verdict string itself on the degenerate exits.
        because = [str(b) for b in (s.get("not_determined_because") or [])]
        inline = verdict[len(_NOT_DETERMINED_VERDICT):].lstrip(" -–—:").strip()
        if inline:
            because.insert(0, inline)
        head = f"not determined ({corpus})" if corpus else "not determined"
        return f"{head} — {'; '.join(because)}" if because else head
    pct, = _figures(token, f"the {season} spread trend",
                    escalation_pct_yr=s["escalation_pct_yr"])
    lo, hi = _figures(token, f"the {season} spread trend's interval",
                      ci_low_pct_yr=s["escalation_ci95_pct_yr"][0],
                      ci_high_pct_yr=s["escalation_ci95_pct_yr"][1])
    # r2 is None when the ln-fit has no variance to explain -- tou_spread
    # writes the null itself. That is a missing statistic, not a broken one, so
    # the clause naming it is dropped rather than the whole trend refused.
    r2 = s.get("r2")
    fit = f", r² {_figures(token, f'the {season} fit quality', r2=r2)[0]:.3f}" \
        if r2 is not None else ""
    tail = f"; {corpus}" if corpus else ""
    return f"{pct:+,.2f}%/yr (95% CI {lo:+,.2f} to {hi:+,.2f}%/yr{fit}{tail})"


_tok("SPREAD_TREND_SUMMER", kind="derived",
     get=lambda ctx: _spread_trend("SPREAD_TREND_SUMMER", "summer"),
     sources=["data/tou_spread.json:delivery_spread.summer"])
_tok("SPREAD_TREND_WINTER", kind="derived",
     get=lambda ctx: _spread_trend("SPREAD_TREND_WINTER", "winter"),
     sources=["data/tou_spread.json:delivery_spread.winter"])


def _battery_on_measured_spread(ctx):
    """The battery re-run on the MEASURED spread, per season.

    The uniform ladder section 13's own table prints is
    battery_dispatch_policies.json's; tou_spread.py recomputes the same ladder
    from the same seed saving, which makes those two the SAME QUANTITY
    INDEPENDENTLY COMPUTED -- but nothing in this token renders both, so there
    is no comparison to gate on. What it renders is the per-season re-run,
    which on a corpus whose spread trend is not determined is itself not
    determined, and says so with the artifact's own reason."""
    batt = _json("tou_spread.json")["battery"]
    per = batt.get("per_period") or {}
    if not per:
        raise SystemExit("report_tokens: data/tou_spread.json:battery.per_period is "
                          "empty -- there is no per-season re-run to report")
    parts = []
    for season in sorted(per):
        block = per[season]
        verdict = str(block.get("verdict", "")).strip()
        if verdict.lower().startswith(_NOT_DETERMINED_VERDICT):
            # `because` can legitimately arrive empty: tou_spread builds it
            # from a list of suppression reasons that can all be suppressed in
            # turn, and its own reader defaults it rather than requiring it.
            because = [str(b) for b in (block.get("because") or [])]
            parts.append(f"{season}: not determined"
                         + (f" — {'; '.join(because)}" if because else ""))
            continue
        npv, = _figures("BATTERY_ON_MEASURED_SPREAD",
                        f"the {season} re-run's net present value", npv10=block["npv10"])
        # A NULL PAYBACK IS THE ANSWER, NOT A MISSING ONE. tou_spread._payback
        # returns payback_yr None when a narrowing spread leaves the battery
        # unrecovered inside its fifteen-year horizon, and the generator's own
        # comment calls that "a real result, not an error ... the one verdict it
        # most needs to be able to report". Refusing it withheld the entire
        # report from the household whose battery does not pay back -- the
        # household the answer matters most to (issue #132, adversarial pass 2).
        # The ten-year NPV is present and meaningful in that case and is still
        # reported; only a payback that is neither null nor a finite number is
        # a refusal, and that one comes from _figures below.
        years = block.get("payback_yr")
        if years is None:
            parts.append(f"{season}: does not repay within the model horizon, "
                         f"{_usd0_plus(npv)} over ten years")
            continue
        years, = _figures("BATTERY_ON_MEASURED_SPREAD",
                          f"the {season} re-run's payback", payback_yr=years)
        parts.append(f"{season}: {years:,.1f} yr payback, {_usd0_plus(npv)} over ten years")
    return " · ".join(parts)


_tok("BATTERY_ON_MEASURED_SPREAD", kind="derived", get=_battery_on_measured_spread,
     sources=["data/tou_spread.json:battery.per_period"])
_tok("SPREAD_BATTERY_SEED_SAVING", kind="derived",
     get=lambda ctx: _usd0_signed(_json("tou_spread.json")["battery"]
                                  ["seed_year1_saving_usd"]) + "/yr",
     sources=["data/tou_spread.json:battery.seed_year1_saving_usd"])


# ---- the quiet-night floor, decomposed (section 13) ------------------------
# A year of nights. A leap year's 366 clears it too, which is right: the gate
# asks whether the corpus covers a year, not whether it is exactly 365 long.
# BOTH ENDS, and this is the whole point of the pair (issue #132, Codex pass 1,
# finding 1): the first version tested only `nights >= 365`, so a two-year
# corpus rendered 730 nights of energy as "18,046 kWh/yr" -- an annual unit on
# roughly double a year, which is a worse error than the short-corpus case it
# was written to catch, because the number is inflated rather than reduced.
# A gate on a range tests the range.
_FULL_YEAR_NIGHTS = (365, 366)
# floor_kw_priced is round(median_kw, 4) in quiet_night_floor.py; half of the
# last retained decimal is the whole slack that derivation introduces.
_FOUR_DECIMAL_ROUNDING = 0.00005 + 1e-12

# THE SIZE THE ASSURANCE CLAUSE IS SOLD ON (issue #140, adversarial pass 2,
# finding 1). "Close enough to say the monthly-netting treatment is not where
# a material error would be hiding" is a claim about MAGNITUDE, so it needs a
# magnitude to be checked against -- stated here, once, rather than implied by
# an f-string that renders the assurance whatever the two totals turn out to
# be. Below the threshold the sentence is earned; at or above it the same
# formula prints the divergence and says the report does not settle it.
#
# 2% of the re-bill total, because the artifact already quantifies the one
# limitation this sentence implicitly ranks itself below:
# pricing.floor_assumption_violations.usd_dropped_at_export_rate is the energy
# the constant-floor split cannot account for and DROPS ($85.52 against a
# $3,196/yr re-bill on this household -- 2.7%). Netting is "not where a
# material error would be hiding" exactly while the netting gap stays under
# the limitation the artifact does own up to; past that, netting would be the
# LARGER of the two and the sentence would be false. 2 is the round figure
# below 2.7. Today's gap is 1.2%.
_NETTING_MATERIALITY_PCT = 2.0

# quiet_night_floor.py writes gap_usd as round(a - b, 2) and gap_pct as
# round(100 * gap_usd / b, 2). Half of the last retained place is the slack in
# each. gap_pct carries a second term as well -- it is built on the ALREADY
# ROUNDED gap_usd, so a recomputation straight from the two totals can differ
# by what half a cent is worth as a percentage of b (about 0.0002 points on
# this household, and smaller as b grows). The caller adds that term rather
# than padding this one, so neither tolerance hides the other's arithmetic.
_CENT_ROUNDING = 0.005 + 1e-9
_PERCENTAGE_POINT_ROUNDING = 0.005 + 1e-9


def _night_floor_coverage():
    """(covers_a_year, nights, why) for the quiet-night corpus.

    A COUNT IS NOT A WINDOW. nights_total is only how many dated rows the run
    produced, so on its own it cannot tell one complete year from 365 dates
    scattered across three (Codex pass 1's second half of finding 1). The
    artifact does carry the window: night_floor.daily_series is one row per
    date, so its first and last give a real span, and comparing that span to
    the row count says whether the record is also gapless.

    Complete therefore means all three: the span is one calendar year, the
    count matches the span (no holes), and the count is itself a year.

    AND WHERE THERE ARE NO DATES, THE ANSWER IS NO (issue #174). The first
    version fell back to the bare count when daily_series was absent, empty,
    null, or carried nothing parseable, accepting 365 as a year on its own --
    which is the inference the paragraph above says is unavailable, made in
    the one situation where nothing else is left to check it against. A
    fallback to a count is the span check not running, not a weaker span
    check. So a corpus with no dated record fails closed: it is reported at
    the size it really is, and it does not get to call that size a year.

    NOT A REFUSAL, per the design constraint issue #140 recorded. All six
    callers were written with a window branch, so each has an honest sentence
    for a corpus that is not a year, and the `why` returned here is written to
    read inside it: "... across the 365 nights measured, with no dated record
    of which nights they were". The report still ships; only the annual unit
    goes.

    AND IT COSTS NO ORDINARY HOUSEHOLD ITS YEAR, checked against the generator
    rather than assumed. quiet_night_floor.night_floor_series() builds
    daily_series by grouping the export on calendar date and writes every
    group as a row carrying an ISO `date`, then sets nights_total to len() of
    that same list -- so the count and the dated record are the SAME LIST
    counted two ways, unique by construction, and a household whose meter
    really did run a contiguous year always clears this. Losing the dates
    while keeping the count takes an artifact this generator did not write."""
    nf = _night_floor()
    nights, = _quantities("NIGHT_FLOOR_COVERAGE", "how many nights the corpus holds",
                          nights_total=nf["nights_total"])
    nights = int(nights)
    lo, hi = _FULL_YEAR_NIGHTS
    dates = []
    for row in (nf.get("daily_series") or []):
        try:
            dates.append(dt.date.fromisoformat(str(row["date"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not dates:
        return False, nights, "with no dated record of which nights they were"
    span = (max(dates) - min(dates)).days + 1
    if not lo <= span <= hi:
        return False, nights, ("spanning more than a full year" if span > hi
                               else "spanning less than a full year")
    if nights != span or len(set(dates)) != span:
        return False, nights, f"with gaps across a {span}-day window"
    if not lo <= nights <= hi:
        return False, nights, ("more than a full year" if nights > hi
                               else "less than a full year")
    return True, nights, ""


_NIGHT_FLOOR_ARTIFACT = "quiet_night_floor.json"

# THE UNIT AND THE ADJECTIVE, NOT THE NOUN. "/yr", "/year", "per year" and
# every word starting "annual" are how a figure claims a year. The bare word
# "year" is not on this list and must not be: _night_floor_coverage's own
# `why` strings say "less than a full year" and "more than a full year", and
# a sentence that reports the window it actually measured is the CORRECT
# rendering -- the thing this guard exists to leave alone.
_ANNUAL_CLAIM = re.compile(r"/yr\b|/year\b|\bper year\b|\bannual", re.IGNORECASE)


def _forbid_unearned_annual_unit(name, rendered):
    """No token may publish an annual claim off quiet_night_floor.json without
    passing _night_floor_coverage. Structural, not per-call.

    WHY THIS EXISTS AT ALL (issue #140, adversarial pass 3). Four tokens were
    taught the coverage gate one review finding at a time -- NIGHT_FLOOR_
    ANNUAL_KWH, then NIGHT_FLOOR_ANNUAL_COST, then SEC9_TEASER and PHANTOM_
    METHOD_DISCREPANCY -- and each round's sweep missed the next one, because
    every one of them is an INDEPENDENT f-string that happens to type "/yr".
    A fifth (NIGHT_FLOOR_SENSITIVITY_PER_100W) and a sixth (NIGHT_FLOOR_
    PRICING_BASIS, which claims the year in a word rather than a unit) were
    still bypassing it when this was written. The pattern is not "these
    tokens were careless"; it is that nothing in the module could tell a
    correct annual unit from an unearned one, so correctness had to be
    retyped at every exit and could only ever be checked by eye.
    So the check moved to the one place every token leaves through.

    IT GATES ON THE READ, NOT THE DECLARATION -- see _reads. A token added
    later that reads this artifact and writes "/yr" is caught whether or not
    it names the artifact in `sources` and whether or not anyone remembers
    this gate exists, which is the whole point.

    AND IT REFUSES, where the six tokens above render their own window. That
    difference is deliberate. A token whose author wrote the window branch
    has an honest sentence to fall back on and must never be withheld over a
    label (the correction issue #132 made). A token that has NOT been written
    for a partial corpus has no honest rendering available at all: the only
    two options are a false annual figure or a named stop, and the stop names
    the token, the corpus and the gate to route it through. It cannot fire on
    a household whose corpus really is a year, which is every household this
    check is not about.

    THE COST OF ITS BREADTH, STATED. It tests the whole rendered string, so a
    future token that reads this artifact for one fact and prints an annual
    figure from a DIFFERENT artifact would be refused on a partial night-floor
    corpus even though its own figure was sound. That is the trade taken
    knowingly: the false positive costs one named refusal and one line of
    routing, and the false negative it prevents is a published number that is
    wrong by however far the corpus falls short of a year."""
    if _NIGHT_FLOOR_ARTIFACT not in _reads:
        return
    if not isinstance(rendered, str) or not _ANNUAL_CLAIM.search(rendered):
        return
    covers, nights, why = _night_floor_coverage()
    if covers:
        return
    raise SystemExit(
        f"report_tokens: {name} renders an annual claim from "
        f"data/{_NIGHT_FLOOR_ARTIFACT}, whose corpus holds {nights:,.0f} nights, "
        f"{why} -- every figure this module takes off that artifact is summed or "
        f"scaled over the interval series the run was given, so its annual unit has "
        f"to be earned through _night_floor_coverage() the way NIGHT_FLOOR_ANNUAL_KWH "
        f"and NIGHT_FLOOR_ANNUAL_COST earn theirs, and this sentence has not earned "
        f"it: {rendered}")


def _night_floor():
    return _json(_NIGHT_FLOOR_ARTIFACT)["night_floor"]


def _night_floor_pricing():
    return _json("quiet_night_floor.json")["pricing"]


def _night_floor_sample(ctx):
    nf = _night_floor()
    quiet, total = _quantities(
        "NIGHT_FLOOR_SAMPLE", "how many nights the floor is measured on",
        quiet_nights=nf["quiet_nights"], nights_total=nf["nights_total"])
    _claim("NIGHT_FLOOR_SAMPLE", "what share of nights were excluded",
           SUPPORTED if total > 0 else NOT_DETERMINED,
           f"data/quiet_night_floor.json:night_floor.nights_total is {total!r}, which is "
           "not a year of nights a sample can be taken from")
    return (f"{quiet:,.0f} of {total:,.0f} nights "
            f"({(total - quiet) / total * 100:.1f}% excluded for a high-power interval)")


def _night_floor_annual_kwh(ctx):
    """The floor's implied annual energy, on the artifact's OWN stated method.

    quiet_night_floor.py prices the measured floor as a CONSTANT across every
    hour of the year (pricing.floor_kw_basis says so in as many words), so the
    implied energy is that constant times the hours it was applied to.

    THE RATE IS THE ONE THE PRICING USED, NOT A PARALLEL READ OF THE SAME
    FIELD. pricing.floor_kw_priced is literally round(night_floor.median_kw, 4)
    in the generator -- ONE DERIVED FROM THE OTHER, so they are compared within
    that rounding and the priced value is what this figure is built on. Reading
    median_kw instead would have let the kWh figure and the $ figure beside it
    drift apart silently if the generator ever priced something else.

    AND THE /yr LABEL IS EARNED, NOT ASSUMED (issue #132, adversarial pass 3).
    The previous version annualized as nights_total x 24 and called the result
    "/yr". nights_total is only the count of dates present in the series, so a
    partial or gappy corpus produced a SMALLER number still wearing an annual
    unit -- understating the load and contradicting the 8,760-hour basis the
    artifact declares. The arithmetic is unchanged where the corpus really does
    cover a year (365 x 24 = 8,760, which is exactly the artifact's stated
    basis); where it does not, the figure is reported over the window it
    actually measures and says so, rather than being refused. A refusal here
    would withhold the whole report over a label, which is the failure the
    previous pass corrected."""
    nf = _night_floor()
    priced = _json("quiet_night_floor.json")["pricing"]["floor_kw_priced"]
    covers, nights, why = _night_floor_coverage()
    kw, median = _quantities(
        "NIGHT_FLOOR_ANNUAL_KWH", "how much energy the always-on floor draws",
        floor_kw_priced=priced, median_kw=nf["median_kw"])
    _require_derived(
        "NIGHT_FLOOR_ANNUAL_KWH", "which floor the annual energy is built on",
        median, kw, _FOUR_DECIMAL_ROUNDING,
        f"data/quiet_night_floor.json prices {kw!r} kW while night_floor.median_kw is "
        f"{median!r} kW -- the energy figure and the cost figure beside it would be "
        "built on different floors")
    kwh = kw * nights * 24
    return (f"{kwh:,.0f} kWh/yr" if covers
            else f"{kwh:,.0f} kWh across the {nights:,.0f} nights measured, {why}")


def _night_floor_annual_cost(ctx):
    """Both committed pricings of the same floor, side by side.

    They are the SAME QUANTITY INDEPENDENTLY COMPUTED -- a price-map
    multiplication and a full re-bill of a counterfactual load series -- and
    the artifact's own `reconciliation` block already explains the gap between
    them (a PCIA effect on kWh left in still-net-positive buckets). So both are
    printed and NEITHER gates the other: an artifact that has already
    reconciled its two methods to a stated cause is not evidence of a
    contradiction, and refusing on their difference would withhold the section
    over the very disagreement the generator documents.

    EVERY SECTION THAT PRICES THIS LOAD NOW RESOLVES THROUGH HERE (issue
    #140). Two older figures for the same load exist in the archive --
    extra_results.json:phantom, which has no generator at all, and
    deep_results.json:phantom, whose generator prices the energy at a
    hardcoded flat $0.20/kWh -- and neither backs a published number any
    more; SEC9_TEASER's own comment states the evidence for that. They are
    labelled as superseded workpapers in TECHNICAL.md sections 3.5 and 3.11,
    which is where CLAUDE.md puts method lineage, so a reader who finds one in
    the archive can see it is not the live figure.

    THE SAME COVERAGE GATE AS THE kWh FIGURE, for the same reason: both
    pricing methods sum over the interval series the run was given, so on a
    short corpus their totals are window totals and "/yr" would be a claim
    neither of them makes."""
    pricing = _json("quiet_night_floor.json")["pricing"]
    covers, nights, why = _night_floor_coverage()
    a, b = _amounts("NIGHT_FLOOR_ANNUAL_COST", "what the always-on floor costs",
                    price_map_usd=pricing["method_a_price_map"]["total_usd"],
                    rebill_usd=pricing["method_b_rebill"]["total_usd"])
    if covers:
        return f"${a:,.0f}/yr by the price map · ${b:,.0f}/yr by a full re-bill"
    return (f"${a:,.0f} by the price map · ${b:,.0f} by a full re-bill, across the "
            f"{nights:,.0f} nights measured, {why}")


# ---------------------------------------------------------------------------
# EACH CLAUSE OF THE PUBLISHED SCOPE SENTENCE, BESIDE THE TERM IN THE
# ARTIFACT'S OWN STATEMENT THAT CARRIES IT (issue #140, /review finding 3).
#
# PHANTOM_METHOD_DISCREPANCY publishes a compression of
# pricing.reconciliation.scope_of_agreement, and the check in front of it
# asked only whether that field was non-blank. Deletion was therefore guarded
# and DRIFT was not -- and drift is the likelier failure: if the two methods
# stop sharing rates.py, or stop starting from the identical _split_floor
# allocation, or the agreement stops being limited to the netting treatment,
# the presence check still passes and the report keeps publishing a scope
# claim the artifact no longer makes.
#
# The relationship, in the terms _require_derived's preamble requires before
# any two artifact facts are set against each other, is SAME QUANTITY,
# INDEPENDENTLY WRITTEN -- one statement about scope, rendered twice: once by
# quiet_night_floor.py into the artifact, once by the f-string below into the
# report. A disagreement IS a contradiction, so it refuses.
#
# TRACED TO THE GENERATOR, per that same preamble, and this is what keeps
# finding 1's failure from repeating here: quiet_night_floor.py writes
# scope_of_agreement as a FIXED LITERAL in its reconciliation block, not as
# anything computed from a household's meter. No household can fail this
# check by having different data; only an edit to the generator can, which is
# exactly the drift it exists to catch.
#
# WHAT IT DOES NOT COVER, stated rather than implied. It tests the terms each
# published clause rests on, not the whole statement, so a rewrite that keeps
# all three terms while adding a fourth limitation around them passes. The
# docstring claims no more than that.
_SCOPE_CLAUSES = (
    ("both methods split the floor out of the meter the same way", "_split_floor"),
    ("take every rate from the same module", "rates.py"),
    ("neither one checks the other's rates or its constant-floor model", "netting"),
)


def _phantom_method_discrepancy(ctx):
    """The two live pricings of this one load, and how far apart they land.

    WHAT THIS TOKEN NOW COMPARES, AND WHY THAT CHANGED (issue #140). It used
    to set section 9's figure against section 13's, because the two sections
    priced the same overnight load out of two different artifacts. They no
    longer do: every section resolves the floor through the NIGHT_FLOOR_*
    formulas above, all of them reading quiet_night_floor.json. The name still
    describes what is measured here -- the discrepancy between the two METHODS
    that price the phantom floor -- but the two methods are now method (a),
    per-interval multiplication against the price map, and method (b), a full
    monthly NEM re-bill of the counterfactual year.

    CLAUDE.md section 0 requires exactly this and nothing less: two live
    methods run on the same data, reconciled explicitly and quantified, which
    is also the one thing section 9 forbids being read as process narrative
    (both figures are current; neither supersedes the other).

    IT REPORTS THE SCOPE OF THE AGREEMENT, NOT JUST ITS SIZE. Both methods
    start from the identical _split_floor allocation and take every rate
    constant from the same rates.py, so their closeness says the netting and
    aggregation treatment is not where a material error lives -- and says
    nothing about the rate constants (an error there is inherited by both) or
    about the constant-floor allocation itself, whose own limitation
    quiet_night_floor.py quantifies separately. The artifact states that scope
    in pricing.reconciliation.scope_of_agreement, and this token refuses to
    render an agreement claim at all if that statement is dropped OR if it
    stops making the claims the sentence below compresses -- see
    _SCOPE_CLAUSES, which pins each published clause to the term in the
    artifact's own statement that carries it, and which states exactly what
    that check does and does not cover.

    THE GAP IS RECOMPUTED FROM THE TWO TOTALS, NOT READ (issue #140,
    adversarial pass 2, finding 1). The first version of this formula read
    method (a)'s total, method (b)'s total and the artifact's precomputed
    gap_usd/gap_pct as three INDEPENDENT facts, and then rendered assurance
    about their closeness unconditionally. A stale or half-regenerated
    artifact would therefore print two materially divergent totals beside an
    obsolete "1.2% apart" and an explicit promise to the reader that no
    material netting error exists -- the published assurance and the published
    figures disagreeing inside one sentence. So the gap this token prints is
    the difference between the two totals it also prints, and the artifact's
    own fields are demoted to a CHECK on that arithmetic: they are compared to
    the recomputation within the rounding quiet_night_floor.py applies to
    each, and an artifact whose stated gap does not match its own totals is
    refused by name rather than resolved in either field's favour.

    THE ASSURANCE IS CONDITIONAL, THE FIGURES ARE NOT. The "not where a
    material error would be hiding" clause renders only while the recomputed
    gap is inside _NETTING_MATERIALITY_PCT, which states the threshold and why
    that number. Past it, the same formula publishes both totals and the
    distance between them and says the report does not settle which is right.
    A refusal would be wrong here: a household regenerating this artifact can
    legitimately land on a wider gap, and it must still get a report -- what
    it must not get is a sentence promising the gap is small while printing
    one that is not. Nothing gates on the two agreeing for the reason the
    artifact gives: it has already decomposed the gap to its cause (PCIA
    priced differently inside buckets whose net sign does not change).

    THE ANNUAL UNIT IS THE COVERAGE GATE'S, NOT THIS FORMULA'S (finding 2 of
    the same pass). Both totals are sums over the interval series the run was
    given, exactly as in NIGHT_FLOOR_ANNUAL_COST, so "/yr" is earned through
    _night_floor_coverage or it is not written at all -- otherwise a
    regenerated partial corpus published a correctly window-qualified kWh
    figure beside falsely annualized dollars in the same sentence."""
    pricing = _night_floor_pricing()
    rec = pricing.get("reconciliation") or {}
    scope = str(rec.get("scope_of_agreement", "")).strip()
    if not scope:
        raise SystemExit(
            "report_tokens: PHANTOM_METHOD_DISCREPANCY will not state that two pricing "
            "methods agree without data/quiet_night_floor.json:pricing.reconciliation."
            "scope_of_agreement, the artifact's own statement of what their agreement "
            "does and does not validate")
    unsupported = [(clause, term) for clause, term in _SCOPE_CLAUSES
                   if term.lower() not in scope.lower()]
    if unsupported:
        raise SystemExit(
            "report_tokens: PHANTOM_METHOD_DISCREPANCY will not publish a scope claim "
            "data/quiet_night_floor.json:pricing.reconciliation.scope_of_agreement no "
            "longer makes -- this sentence's clause(s) "
            + "; ".join(f"{clause!r} (rests on {term!r})" for clause, term in unsupported)
            + f" have lost their support in that statement: {scope}")
    covers, nights, why = _night_floor_coverage()
    a, b = _amounts("PHANTOM_METHOD_DISCREPANCY", "what each method prices the floor at",
                    price_map_usd=pricing["method_a_price_map"]["total_usd"],
                    rebill_usd=pricing["method_b_rebill"]["total_usd"])
    _claim("PHANTOM_METHOD_DISCREPANCY", "how far apart the two methods are",
           SUPPORTED if b > 0 else NOT_DETERMINED,
           f"the re-bill total is ${b!r}, which cannot be a base for a percentage")
    gap_usd = a - b
    gap_pct = 100.0 * gap_usd / b
    stated_usd, stated_pct = _figures(
        "PHANTOM_METHOD_DISCREPANCY", "how far apart the two pricings land",
        gap_usd=rec.get("gap_usd"), gap_pct=rec.get("gap_pct"))
    _require_derived(
        "PHANTOM_METHOD_DISCREPANCY", "how far apart the two pricings land",
        gap_usd, stated_usd, _CENT_ROUNDING,
        f"data/quiet_night_floor.json:pricing.reconciliation.gap_usd states "
        f"{stated_usd!r} while the same artifact's two totals (${a!r} and ${b!r}) are "
        f"{gap_usd:,.2f} apart -- the reconciliation does not describe the figures "
        "beside it, so neither reading of the gap can be published")
    _require_derived(
        "PHANTOM_METHOD_DISCREPANCY", "how far apart the two pricings land",
        gap_pct, stated_pct, _PERCENTAGE_POINT_ROUNDING + 100.0 * _CENT_ROUNDING / b,
        f"data/quiet_night_floor.json:pricing.reconciliation.gap_pct states "
        f"{stated_pct!r}% while the same artifact's two totals (${a!r} and ${b!r}) are "
        f"{gap_pct:.2f}% apart -- the reconciliation does not describe the figures "
        "beside it, so neither reading of the gap can be published")
    per_year = "/yr" if covers else ""
    totals = (f"the price map makes it ${a:,.0f}/yr and a full NEM re-bill ${b:,.0f}/yr"
              if covers else
              f"across the {nights:,.0f} nights measured, {why}, the price map makes it "
              f"${a:,.0f} and a full NEM re-bill ${b:,.0f}")
    verdict = (" — close enough to say the monthly-netting treatment is not where a "
               "material error would be hiding, and no more than that: "
               if abs(gap_pct) < _NETTING_MATERIALITY_PCT else
               f" — past the {_NETTING_MATERIALITY_PCT:.0f}% this reconciliation treats "
               "as small, so this report does not settle which of the two is right: ")
    return (f"{totals}, ${abs(gap_usd):,.0f}{per_year} or {abs(gap_pct):.1f}% apart"
            f"{verdict}both methods split the floor out of the meter "
            "the same way and take every rate from the same module, so neither one checks "
            "the other's rates or its constant-floor model")


_tok("PHANTOM_METHOD_DISCREPANCY", kind="derived", get=_phantom_method_discrepancy,
     sources=["data/quiet_night_floor.json:pricing.method_a_price_map.total_usd",
              "data/quiet_night_floor.json:pricing.method_b_rebill.total_usd",
              "data/quiet_night_floor.json:pricing.reconciliation "
              "(gap_usd, gap_pct, scope_of_agreement)"])


def _night_floor_seasonality(ctx):
    nf = _night_floor()
    monthly = nf.get("monthly_median_kw") or {}
    if not monthly:
        raise SystemExit("report_tokens: data/quiet_night_floor.json:night_floor."
                          "monthly_median_kw is empty -- there is no seasonality to state")
    values = _quantities("NIGHT_FLOOR_SEASONALITY", "how the floor moves through the year",
                         **{f"month_{m}_kw": v for m, v in sorted(monthly.items(), key=str)})
    keys = [m for m, _v in sorted(monthly.items(), key=str)]
    pairs = sorted(zip(values, keys), key=lambda vk: (vk[0], str(vk[1])))
    lo_kw, lo_m = pairs[0]
    hi_kw, hi_m = pairs[-1]
    return (f"{lo_kw:,.2f} kW in {_MONTH_ABBR[int(lo_m)]} to "
            f"{hi_kw:,.2f} kW in {_MONTH_ABBR[int(hi_m)]}")


def _night_floor_pricing_basis(ctx):
    """The assumption both annual figures rest on, and its own label.

    NIGHT_FLOOR_ANNUAL_KWH and NIGHT_FLOOR_ANNUAL_COST extend a floor MEASURED
    in a four-hour overnight window across every hour of the year.
    quiet_night_floor.py labels that step "modeled", not measured, in
    confidence_labels.pricing -- and section 13's own heading pill for this
    subsection reads `measured`, which is true of the floor and not of the
    year built from it. Its floor_assumption_violations block adds the
    direction of the resulting error, which is the part a reader needs: the
    shortfall is DROPPED rather than credited, so the annual figures are
    conservative by roughly that amount rather than inflated by it.

    AND THE WORD "ANNUAL" IS A FIGURE HERE, NOT A FRAME (issue #140,
    adversarial pass 3). usd_dropped_at_export_rate is summed over the
    interval series the run was given, exactly as both pricing methods are,
    so on a partial corpus it is a window total -- and this sentence's job is
    to say what the figures BESIDE it rest on, which on that corpus is no
    longer a year. There is no "/yr" to drop, so the unqualified claim was
    the word itself: "leaving the annual figures conservative by about $86"
    beside two window-qualified figures describes a report that does not
    exist. The gate is _night_floor_coverage, the same one those figures
    pass through; the method clause moves with it, because "applied as a
    constant across every hour of the year" is what the pricing did only
    where the run really held a year of hours."""
    doc = _json("quiet_night_floor.json")
    label = str(doc.get("confidence_labels", {}).get("pricing", "")).strip()
    if not label:
        raise SystemExit(
            "report_tokens: NIGHT_FLOOR_PRICING_BASIS will not publish an annual figure "
            "without data/quiet_night_floor.json:confidence_labels.pricing, the label that "
            "artifact puts on extending a four-hour measurement across the year")
    violations = doc["pricing"]["floor_assumption_violations"]
    dropped, = _amounts("NIGHT_FLOOR_PRICING_BASIS",
                        "how much the constant-floor assumption drops rather than credits",
                        usd_dropped_at_export_rate=violations["usd_dropped_at_export_rate"])
    kind = label.split("--")[0].strip().rstrip(":").strip() or "modeled"
    covers, nights, why = _night_floor_coverage()
    if covers:
        return (f"{kind}, not measured: the floor is measured in a four-hour overnight window "
                "and then applied as a constant across every hour of the year — where that "
                f"assumption exceeds what the meter can account for, the shortfall is dropped "
                f"rather than credited, leaving the annual figures conservative by about "
                f"${dropped:,.0f}")
    return (f"{kind}, not measured: the floor is measured in a four-hour overnight window "
            "and then applied as a constant across every hour the run priced — where that "
            f"assumption exceeds what the meter can account for, the shortfall is dropped "
            f"rather than credited, leaving those figures conservative by about "
            f"${dropped:,.0f} across the {nights:,.0f} nights measured, {why}")


_tok("NIGHT_FLOOR_PRICING_BASIS", kind="derived", get=_night_floor_pricing_basis,
     sources=["data/quiet_night_floor.json:confidence_labels.pricing",
              "data/quiet_night_floor.json:pricing.floor_assumption_violations"])
_tok("NIGHT_FLOOR_MEDIAN", kind="derived",
     get=lambda ctx: _measured("NIGHT_FLOOR_MEDIAN", "the quiet-night floor",
                               "kW", ",.2f", median_kw=_night_floor()["median_kw"]),
     sources=["data/quiet_night_floor.json:night_floor.median_kw"])
_tok("NIGHT_FLOOR_SPREAD", kind="derived",
     get=lambda ctx: "%s kW (p10) to %s kW (p90)" % tuple(
         f"{v:,.2f}" for v in _quantities(
             "NIGHT_FLOOR_SPREAD", "how much the quiet-night floor varies",
             p10_kw=_night_floor()["p10_kw"], p90_kw=_night_floor()["p90_kw"])),
     sources=["data/quiet_night_floor.json:night_floor (p10_kw, p90_kw)"])
_tok("NIGHT_FLOOR_SAMPLE", kind="derived", get=_night_floor_sample,
     sources=["data/quiet_night_floor.json:night_floor (quiet_nights, nights_total)"])
_tok("NIGHT_FLOOR_ANNUAL_KWH", kind="derived", get=_night_floor_annual_kwh,
     sources=["data/quiet_night_floor.json:night_floor (median_kw, nights_total)",
              "data/quiet_night_floor.json:pricing.floor_kw_basis (the constant-load method)"])
_tok("NIGHT_FLOOR_ANNUAL_COST", kind="derived", get=_night_floor_annual_cost,
     sources=["data/quiet_night_floor.json:pricing.method_a_price_map.total_usd",
              "data/quiet_night_floor.json:pricing.method_b_rebill.total_usd"])
_tok("NIGHT_FLOOR_CYCLING", kind="derived",
     get=lambda ctx: _measured(
         "NIGHT_FLOOR_CYCLING", "how much the floor cycles within a night", "kW", ",.2f",
         cycling_within_night_std_kw_median=_night_floor()
         ["cycling_within_night_std_kw_median"]),
     sources=["data/quiet_night_floor.json:night_floor."
              "cycling_within_night_std_kw_median"])
_tok("NIGHT_FLOOR_SEASONALITY", kind="derived", get=_night_floor_seasonality,
     sources=["data/quiet_night_floor.json:night_floor.monthly_median_kw"])


def _night_floor_sensitivity(ctx):
    """What a watt off the floor is worth -- the one recoverable figure this
    repo can actually measure.

    HOW MUCH OF A FLOOR IS REMOVABLE IS NOT A METERED QUANTITY. Nothing in
    this archive can say which appliance behind the floor could be switched
    off, so a "realistic recoverable $/yr" is an opinion wearing a figure's
    clothes (CLAUDE.md section 0). What IS computed, by re-billing the whole
    year at 100 W steps, is the RATE: what each 100 W removed returns. A
    reader can multiply that by whatever they find on a plug meter; the report
    does not do the multiplication for them.

    THE STEP IS A GRID, AND THE FIGURE IS READ OFF IT. usd_per_100w_at_current
    _floor is the marginal at the sensitivity step NEAREST the measured floor,
    not the exact marginal at this household's own wattage -- the artifact's
    own note says so.

    BUT "NEAREST" HAS ENDS, AND A HOUSEHOLD OUTSIDE THEM IS NOT A DEFECT
    (issue #140, /review finding 1). The step was checked against the measured
    floor with a bare half-step tolerance, and a miss was a REFUSAL that
    generate_report folds into its failures -- so an ordinary household got no
    report at all. quiet_night_floor.sensitivity_per_100w() does not compute a
    step for any floor: it rounds the floor onto the ladder and then CLAMPS
    the result into [STEP_W, MAX_REDUCTION_W], bounds whose own comment says
    they bracket THIS household's measured floor. A 1.40 kW floor therefore
    lands on the 1,200 W end and misses by 200 W; a 0.03 kW floor lands on the
    100 W end and misses by 70; only a floor already inside the ladder passed.
    That is the same shape as the two comparisons _require_derived's preamble
    was written for, a third time: a guard asserting a relationship between
    two artifact fields without reading the generator that writes both.

    SO THE CLAMP IS DETECTED, NOT REFUSED. The bounds are read off the ladder
    rather than restated here -- sensitivity_per_100w() builds its steps as
    range(STEP_W, MAX_REDUCTION_W + STEP_W, STEP_W) and clamps into that same
    range, so the ladder's smallest and largest rungs ARE those two constants
    and neither number has to appear in this module to be honoured (the
    ladder's own rung spacing supplies the half-step tolerance the same way).
    A floor inside the ladder renders exactly as before, on the half-step
    nearness test. A floor outside it still has a real rate -- the ladder was
    genuinely re-billed at that end -- but it is the rate at the ladder's END,
    not at this household's floor, so the WORDING degrades to say precisely
    that instead of the report failing to generate. What is still refused is
    an artifact that contradicts itself: a floor outside the range whose
    floor_w_used is not the end the clamp would have produced.

    AND THE LADDER IS NOT A STRAIGHT LINE, which is the difference between a
    rate and a multiplier. The artifact says so in linearity_note; this token
    does not quote that note, it recomputes the same argument from the ladder's
    own marginal_usd_per_100w column and prints the spread, so a reader can see
    how far the rate moves across the tested range instead of multiplying one
    figure by any amount removed. Same shape as DEGRADATION_WEATHER_CAVEAT
    rebuilding clearsky_note's argument out of the numbers beside it.

    AND THE LADDER HAS TO BE A LADDER (issue #140, /review findings 4). Two
    degenerate shapes reached prose through arithmetic rather than through a
    refusal: an empty steps list raised ValueError out of min()/max(), which
    resolve_token's catch-all turns into a generic "failed to resolve", and
    two steps sharing a reduction_w collapsed to ONE key in the comprehension
    below, so the published spread silently narrowed instead of reporting the
    collision. Both are named refusals now, in this module's own style, for
    _forbid_unearned_annual_unit's reason one level up: a guard that reports
    a generic exception is a guard a maintainer cannot route.

    AND THE RATE IS A RATE PER YEAR ONLY WHERE THERE IS A YEAR (issue #140,
    adversarial pass 3). Every figure in this sentence -- the rate at the
    floor and both ends of the ladder it is read off -- comes from
    sensitivity_per_100w.steps, and quiet_night_floor.sensitivity_per_100w()
    builds each step by re-billing the interval series THE RUN WAS GIVEN
    (br.bill(f0) - br.bill(f2)) and writing the difference as
    annual_savings_usd. On a corpus that is not a year that difference is a
    window saving, and the field's own name is the trap: this formula used to
    append "/yr" unconditionally, so a partial corpus published a
    part-year saving as a per-year rate in sections 9 and 13 -- the same
    defect the same pass fixed in SEC9_TEASER, PHANTOM_METHOD_DISCREPANCY,
    NIGHT_FLOOR_ANNUAL_KWH and NIGHT_FLOOR_ANNUAL_COST, at the one exit that
    sweep missed. So the unit comes from _night_floor_coverage, like theirs.

    AND THE LADDER'S TWO ENDS CARRY THE WINDOW THEMSELVES (issue #140,
    /review finding 5). They used to be left bare on the argument that the
    window was stated "immediately before them" -- it was not: on a partial
    corpus the window clause sits about forty words and a full clause earlier,
    with the step, the nearness qualifier and the multiplier caveat between,
    and $249-$323 carries no "/yr" for _ANNUAL_CLAIM to catch. That is exactly
    the defect class the structural guard exists for, at the one exit the
    regex cannot see, so the endpoints are qualified where they appear rather
    than justified from a distance."""
    sens = _json("quiet_night_floor.json")["sensitivity_per_100w"]
    steps = sens.get("steps") or []
    _claim("NIGHT_FLOOR_SENSITIVITY_PER_100W",
           "how far the rate moves along the ladder",
           SUPPORTED if steps else NOT_DETERMINED,
           "data/quiet_night_floor.json:sensitivity_per_100w.steps is empty -- there is no "
           "re-billed ladder to read a rate off, no range for that rate to move across, and "
           "no bounds to tell a clamped reading from a near one")
    rungs = [s["reduction_w"] for s in steps]
    repeated = sorted({w for w in rungs if rungs.count(w) > 1})
    _claim("NIGHT_FLOOR_SENSITIVITY_PER_100W",
           "how far the rate moves along the ladder",
           SUPPORTED if not repeated else NOT_DETERMINED,
           f"data/quiet_night_floor.json:sensitivity_per_100w.steps repeats reduction_w "
           f"{repeated} -- two marginals at one reduction cannot both be the marginal there, "
           "and collapsing them would narrow the published spread instead of saying so")
    ladder = sorted(rungs)
    gaps = [b - a for a, b in zip(ladder, ladder[1:])]
    spacing = min(gaps) if gaps else ladder[0]
    _claim("NIGHT_FLOOR_SENSITIVITY_PER_100W",
           "how far apart the ladder's rungs sit",
           SUPPORTED if spacing > 0 else NOT_DETERMINED,
           f"data/quiet_night_floor.json:sensitivity_per_100w.steps gives a rung spacing of "
           f"{spacing!r} W, which is no ladder at all -- there is no half-step for a "
           "'nearest' to be measured against")
    lowest, highest = float(ladder[0]), float(ladder[-1])
    at_floor = sens["usd_per_100w_at_current_floor"]
    per_100w, = _amounts("NIGHT_FLOOR_SENSITIVITY_PER_100W",
                         "what removing 100 W of the floor returns",
                         value_usd=at_floor["value_usd"])
    step_w, floor_kw = _quantities(
        "NIGHT_FLOOR_SENSITIVITY_PER_100W", "which step the rate was read off",
        floor_w_used=at_floor["floor_w_used"], median_kw=_night_floor()["median_kw"])
    floor_w = floor_kw * 1000.0
    half_step = spacing / 2.0
    below = floor_w < lowest - half_step
    above = floor_w > highest + half_step
    if below or above:
        end_w = lowest if below else highest
        _claim("NIGHT_FLOOR_SENSITIVITY_PER_100W",
               "which end of the ladder the rate was read off",
               SUPPORTED if step_w == end_w else NOT_DETERMINED,
               f"the measured floor is {floor_w:,.0f} W, outside the {lowest:,.0f}-"
               f"{highest:,.0f} W range this ladder was re-billed over, so the only rate "
               f"available is the one at its {end_w:,.0f} W end -- but the artifact read it "
               f"off the {step_w:,.0f} W step")
        read_off = (
            f"read off the {step_w:,.0f} W step at the {'bottom' if below else 'top'} of "
            f"the re-billed ladder, since this household's floor ({floor_w:,.0f} W) sits "
            f"{'below' if below else 'above'} the {lowest:,.0f}–{highest:,.0f} W range that "
            "ladder covers")
        near = "a rate at that end of the ladder"
    else:
        _claim("NIGHT_FLOOR_SENSITIVITY_PER_100W",
               "what a household at THIS floor gets back per 100 W",
               SUPPORTED if abs(step_w - floor_w) <= half_step else NOT_DETERMINED,
               f"the rate was read off the {step_w:,.0f} W step while the measured floor is "
               f"{floor_w:,.0f} W, more than half a {spacing:,.0f} W step away, so it is not "
               "the step nearest this floor")
        read_off = (f"read off the {step_w:,.0f} W step nearest the measured floor rather "
                    "than computed at this household's own exact wattage")
        near = "a rate near this floor"
    marginals = _quantities(
        "NIGHT_FLOOR_SENSITIVITY_PER_100W", "how far the rate moves along the ladder",
        **{f"step_{s['reduction_w']}_w": s["marginal_usd_per_100w"] for s in steps})
    lo, hi = min(marginals), max(marginals)
    covers, nights, why = _night_floor_coverage()
    rate = (f"about ${per_100w:,.0f}/yr" if covers else
            f"about ${per_100w:,.0f} across the {nights:,.0f} nights measured, {why},")
    spread = (f"the same ladder's marginal runs from ${lo:,.0f} to ${hi:,.0f} per 100 W "
              "across the range it was re-billed over" if covers else
              f"the same ladder's marginal runs from ${lo:,.0f} to ${hi:,.0f} per 100 W "
              f"over those same {nights:,.0f} nights, across the range it was re-billed over")
    return (f"{rate} for every 100 W taken off it, {read_off} — and it is {near} rather "
            f"than a multiplier for any amount removed, since {spread}")


_tok("NIGHT_FLOOR_SENSITIVITY_PER_100W", kind="derived", get=_night_floor_sensitivity,
     sources=["data/quiet_night_floor.json:sensitivity_per_100w."
              "usd_per_100w_at_current_floor",
              "data/quiet_night_floor.json:sensitivity_per_100w.steps "
              "(the re-billed ladder: its spread, its rung spacing and its two ends)",
              "data/quiet_night_floor.json:night_floor.median_kw"])


# ---------------------------------------------------------------------------
# 3. The resolver.
# ---------------------------------------------------------------------------
class _Ctx:
    """Passed to every 'derived' formula. Present mainly so formulas read as
    ctx.something() and stay easy to extend without changing every lambda's
    signature; today it carries no state of its own."""


CTX = _Ctx()


def resolve_token(name, spec=None):
    """The rendered string for one token. Raises SystemExit naming `name` and
    what failed if it cannot be produced."""
    if spec is None:
        if name not in TOKENS:
            raise SystemExit(f"report_tokens: unknown token {name!r} -- not in TOKENS")
        spec = TOKENS[name]
    kind = spec["kind"]
    # One read-window per resolution; _forbid_unearned_annual_unit closes it.
    _reads.clear()
    try:
        if kind == "gap":
            raise SystemExit(f"report_tokens: token {name} has no committed source "
                              f"in this repo -- {spec['reason']}")
        elif kind == "data_json":
            raw = _dig(_json(spec["file"]), spec["path"])
        elif kind == "data_csv":
            raw = spec["get"](_csv_rows(spec["file"]))
        elif kind == "rates_module":
            raw = spec["get"](R)
        elif kind == "household_yaml":
            values = _hh_value(spec["path"])
            if spec.get("multi"):
                raw = values
            else:
                if len(values) != 1:
                    raise SystemExit(
                        f"household.yaml path {spec['path']!r} resolved to "
                        f"{len(values)} values, expected exactly 1")
                raw = values[0]
        elif kind == "cited_constant":
            raw = spec["value"]
        elif kind == "derived":
            raw = spec["get"](CTX)
        else:
            raise SystemExit(f"unknown token kind {kind!r}")

        # FORMATTING HAPPENS INSIDE THE TRY, and the finiteness of a numeric
        # value is checked before a formatter is handed it (issue #131 review
        # round 5, findings 1, 2 and 5). Both halves of that sentence were
        # defects:
        #
        #   * A leaf token declared with a numeric format spec is ASSERTING
        #     its artifact field is a number. Nothing checked it, so a nan in
        #     packages.MID.battery_alone_yr published "-$nan" and an infinity
        #     published "$inf" -- and the per-token guards that did exist were
        #     added one review finding at a time, at whichever exit that
        #     round's reader happened to reach. This is the guard the class
        #     needed: EVERY leaf token, of every kind, checked once, here.
        #   * The formatter used to run outside the try, so a refusal raised
        #     inside one (the three signed formatters now raise on a
        #     non-number) reached the caller WITHOUT the token's name on it,
        #     and no ValueError or ArithmeticError from a formatter was
        #     converted into this module's named failure either.
        #
        # It does not replace the named per-site guards -- "S5_VERDICT cannot
        # say which share is larger" tells a maintainer what broke and this
        # says only that something did -- it is the floor beneath them.
        fmt = spec.get("fmt")
        if fmt is not None and fmt not in FORMATTERS:
            raise SystemExit(f"report_tokens: token {name} has unknown format spec {fmt!r}")
        if fmt not in _NON_NUMERIC_FMTS and not _finite(raw):
            raise SystemExit(
                f"report_tokens: token {name} resolved to {raw!r}, which is not a "
                f"finite number -- its format spec {fmt!r} has nothing honest to "
                "render for it")
        # The last gate every token passes through, and the only one that can
        # see the finished sentence. See _forbid_unearned_annual_unit.
        rendered = FORMATTERS.get(fmt, _raw)(raw)
        _forbid_unearned_annual_unit(name, rendered)
        return rendered
    except SystemExit as e:
        msg = str(e)
        if name in msg or kind == "gap":
            raise
        raise SystemExit(f"report_tokens: failed to resolve token {name} ({kind}): {msg}")
    # ArithmeticError, not just ValueError: round(float('inf')) and
    # int(float('inf')) raise OverflowError, and a per-interval quotient can
    # raise ZeroDivisionError. Neither was in this tuple, so an infinity in a
    # share field left section 5's verdict crashing out of the generator with
    # a bare OverflowError instead of this module's named refusal (issue #131
    # review round 5, finding 6).
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as e:
        raise SystemExit(f"report_tokens: failed to resolve token {name} ({kind}): "
                          f"{type(e).__name__}: {e}")


def resolve_all(include_gaps=False):
    """{TOKEN: rendered_string} for every token in TOKENS.

    Gap tokens (kind='gap') are omitted by default -- they are a documented,
    named exception, not silently-produced empty strings. Pass
    include_gaps=True to get resolve_token's SystemExit behavior surfaced as
    part of a bulk run (used by the "everything really resolves" test to prove
    the gap set is exactly KNOWN_GAPS and nothing else silently fails).
    """
    out = {}
    failures = []
    for name, spec in TOKENS.items():
        if spec.get("kind") == "gap" and not include_gaps:
            continue
        try:
            out[name] = resolve_token(name, spec)
        except SystemExit as e:
            failures.append(str(e))
    if failures:
        raise SystemExit("report_tokens: " + str(len(failures)) +
                          " token(s) failed to resolve:\n" + "\n".join(failures))
    return out


def main():
    live, comment_only = template_tokens()
    total = live | comment_only
    missing = total - set(TOKENS)
    extra_gaps = {n for n, s in TOKENS.items() if s.get("kind") == "gap"}
    if missing:
        raise SystemExit(f"report_tokens: {len(missing)} template token(s) have no "
                          f"TOKENS entry at all: {sorted(missing)}")
    resolved = resolve_all(include_gaps=False)
    print(f"{len(TOKENS)} tokens declared ({len(live)} live, {len(comment_only)} "
          f"comment-only); {len(resolved)} resolved, {len(extra_gaps)} named gap(s):")
    for name in sorted(extra_gaps):
        print(f"  GAP  {name}")
    for name in sorted(resolved):
        val = resolved[name]
        shown = val if len(val) <= 80 else val[:77] + "..."
        print(f"  {name} = {shown}")


if __name__ == "__main__":
    main()
