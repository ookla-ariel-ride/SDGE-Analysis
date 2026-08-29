#!/usr/bin/env python3
"""Measure the running-prose RHYTHM of a report page, per tier (issue #256).

WHY THIS EXISTS
  prose_lint.py gates the fragments generate_report.py splices INTO the page,
  and prose_blocks.py measures how long a paragraph runs. Neither one ever
  looked at the finished index.html, so four habits of machine prose
  accumulated across roughly thirty commits with every suite green: em dashes
  at 17 per 1,000 words, 156 ALL-CAPS emphasis words, "X, not Y" tails, and
  the intensifiers "genuine", "honest" and "robust". Issues #251-#255 removed
  them by hand. This module is the instrument that keeps them gone: it reads
  the committed page, measures four rates over running prose, and fails a
  build that lets one climb back.

WHAT COUNTS AS RUNNING PROSE
  prose_blocks.extract(html, min_words=1) supplies the blocks: every visible
  block of the page, SHORT BLOCKS AND HEADINGS INCLUDED (tables, the day-band,
  the .meta ledger, nav, pills and <code> spans are already gone; see that
  module's docstring). Short blocks are in because a three-word card can shout
  a word or claim candour as loudly as a paragraph, and prose_blocks' 4-word
  floor exists for the 800-character cap, which a short block cannot break.
  Headings are in for the same reason, with ONE exception: the em-dash metric
  skips them, because CLAUDE.md section 10 makes the heading verdict a piece of
  the design language, the same way it does for day-band labels, .meta rows and
  table cells, so an em dash there is a typographic separator the template asks
  for rather than a writer reaching for one. That exemption is about dashes and
  nothing else: an ALL-CAPS word, an intensifier or an "X, not Y" tail in a
  heading is the same habit it is in a paragraph, and is counted.

  Two word counts follow from that split, and each metric names its own:
    prose_words  the non-heading blocks -- the denominator for em dashes
    words        every measured block   -- the denominator for tails

  Text is NORMALIZED before any pattern runs (see normalize()), so a
  typographic variant of a character cannot walk past a rule that spells out
  its plain form.

THE FOUR METRICS
  1. EM DASHES per 1,000 prose words, headings excluded. Counts every code
     point in LONG_DASHES (em dash, horizontal bar, two- and three-em dash and
     the small/vertical presentation forms, all normalized to "—") plus the
     " -- " substitute (a double hyphen surrounded by spaces; a hyphenated
     compound or a range never matches). The EN dash is deliberately not in
     the set: this report writes ranges with it ("6–9pm", "2024–2025").
  2. "X, NOT Y" TAILS per 1,000 words. Counts a comma followed by "not",
     allowing a closing quote between the two (`"measured," not "modeled"`
     is the same tail as `measured, not modeled`), and `rather than`.
     One is a useful contrast. A page full of them is a tic.
  3. ALL-CAPS EMPHASIS, an absolute count. A run of three or more capitals
     that is not in ACRONYMS and is not a package label used as a name (see
     PACKAGE_LABELS). Two-letter forms are out of scope by construction, so
     shouting a two-letter word is not caught.
  4. INTENSIFIERS, an absolute count. The words in prose_lint.INTENSIFIERS
     ("genuine", "honest", "robust" and their -ly forms), which claim candour
     instead of showing it. The word list lives in prose_lint.py so the
     fragment gate and this page gate cannot drift apart.
  Carried over from issue #255: no block over LIMITS["max_block_chars"]
  visible characters. That check is prose_blocks.over_limit(), called here,
  not reimplemented, and it is applied to the tier's blocks INCLUDING headings
  and EXCLUDING the sub-4-word blocks, exactly the set
  `prose_blocks.py --max-chars 800` applies it to.

THE TIER BOUNDARY
  The basic/advanced split is the line of the page's `<details id="advanced">`,
  found by parsing (prose_blocks.advanced_lines). A tier measurement is only
  meaningful when that boundary is unambiguous, so measure() raises
  TierBoundaryError for --tier basic or --tier advanced unless the page has
  EXACTLY ONE such element. A page with none, or with two, is a defect in the
  page; reading it as one big basic tier would dilute every rate on it by
  however many advanced words the reader never counted. --tier all needs no
  boundary and does not check one, and the CLI's default measures all three
  tiers, so the ordinary run still checks it.

THE THRESHOLDS AND WHERE THEY COME FROM
  em dashes    <= 3.0 per 1,000 words   acceptance criterion of issue #252
  "X, not Y"   <= 1.5 per 1,000 words   acceptance criterion of issue #253
  ALL-CAPS     == 0                     acceptance criterion of issue #252
  intensifiers == 0                     acceptance criterion of issue #253
  block length <= 800 characters        CLAUDE.md section 10, via issue #255
  They are ceilings on a rate, not targets. The rewrite that closed #252-#255
  landed the page far under every one of them, so the headroom is wide: on the
  committed page the basic tier reads 0.1 em dashes and 0.1 tails per 1,000
  words over 8,049 words, the advanced tier 0.1 and 0.9 over 15,126 words, and
  both tiers carry zero ALL-CAPS words, zero intensifiers and zero over-cap
  blocks. A tier would have to gain roughly two dozen new em dashes before the
  first threshold bit. That distance is the point: the gate is meant to catch
  a drift, and a rate that suddenly sits near its ceiling is itself the signal.

ADDING AN ACRONYM
  ACRONYMS below is the allowlist for metric 3. When a new acronym enters the
  prose, the CLI names it under `ALL-CAPS emphasis:` with its source line; add
  it to the frozenset in the group it belongs to and rerun. Do not widen the
  regex, and do not put an ordinary English word in there: LOW, MID and HIGH
  were in ACRONYMS once, which quietly licensed "the cost is HIGH" anywhere on
  the page. They live in PACKAGE_LABELS now, on the narrower terms described
  there.

USAGE
  prose_rhythm.py [--tier basic|advanced|all] [--strict] [PATH]
    PATH defaults to <repo root>/index.html.
    Prints one `PROSE RHYTHM ...` summary line per tier measured (the default
    --tier all reports basic, advanced and the whole page), then an indented
    offender list under any tier that breaks a limit. Exit 0 unless --strict,
    which exits 1 when any measured tier has a violation. A missing file, and a
    page whose advanced-tier boundary is not exactly one element, both exit 2
    with `PROSE RHYTHM ERROR` -- an error about the input, never a verdict on it.

  As a library:
    measure(html_text, tier="all") -> dict of counts, per-1,000-word rates and offenders
    check(html_text, tier="all", limits=None) -> list[str] of violation descriptions

Stdlib only. Reads index.html and analysis/ only: no private/, no git, no network.
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import prose_blocks  # noqa: E402
import prose_lint  # noqa: E402

class TierBoundaryError(ValueError):
    """The page does not state its basic/advanced boundary exactly once."""


# Headings carry the design language's own em dashes (CLAUDE.md section 10's
# heading verdicts), so the EM-DASH metric alone skips them. Every other metric
# reads them: shouting, an intensifier and an "X, not Y" tail are the same
# habit in a heading as in a paragraph.
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# The allowlist for metric 3, grown by hand over issues #252 and #253: every
# entry is an acronym, initialism or all-caps proper name this report's
# vocabulary actually uses. Entries the running prose does not currently use
# (JSON, CSV, PVWATTS, ...) appear elsewhere on the page, inside the tables and
# <code> spans the extractor excludes, and stay here so restating one in prose
# is not a false alarm. Two-letter forms are deliberately absent: the
# three-or-more rule cannot reach them.
ACRONYMS = frozenset({
    # Hyphenated domain tokens. CAPS_RE reads a hyphenated chain as ONE word
    # (a shout survives hyphens), so each rate plan and file name that really
    # is written this way is named here rather than loosening the pattern.
    "TOU-DR", "TOU-DR1", "TOU-DR2", "TOU-DR-P", "TOU-ELEC", "EV-TOU-5",
    "EV-TOU-2", "DATA-SOURCES-CHEATSHEET",
    # utilities, tariffs, programs, regulators
    "SDGE", "SDG", "CAISO", "CPUC", "CEC", "CCA", "CEA", "NEM", "NBT", "NBC",
    "PCIA", "UDC", "TOU", "IOU", "VNEM", "SGIP", "DSGS", "ELRP", "PSPS", "VPP",
    "DR", "ITC", "HEEHRA", "TECH", "DOE", "EIA", "NREL", "PVWATTS", "SAM",
    "NEC", "PTO", "ATO", "BSC", "MCA", "SES", "ISO", "LMP", "SAIDI",
    # equipment, physics, units
    "HVAC", "HPWH", "HPSH", "AFUE", "ASHRAE", "SEER", "EER", "COP", "UEF",
    "MPPT", "SOC", "PW3", "MWH", "KWH", "HDD", "CTS", "ELEC", "EVS", "ICE",
    # statistics and finance
    "RMSE", "MAE", "OLS", "NPV", "ROI", "CAGR", "AWD",
    # data sources and file formats
    "ACIS", "NOAA", "FHWA", "JSON", "CSV", "PDF", "PDFS", "HTML", "URL", "API",
    "GPT", "FAQ", "YTD",
    # clock and calendar
    "DST", "UTC", "PST", "PDT",
    # all-caps proper names: the repo's own documents
    "CLAUDE", "TECHNICAL", "GLOSSARY", "README",
})

# The package tiers of section 7. These are ordinary English words as well as
# names, so they are NOT in ACRONYMS: a blanket entry there licensed "the cost
# is HIGH" on any page. A label is exempt only where it is being used as a
# NAME, which takes two things at once:
#   - the page must define it, by carrying a package-card heading of the shape
#     `LABEL — $0 · behavior only` (see PACKAGE_CARD_RE and package_labels());
#     a page with no such card has no packages to name, so the word is a shout.
#   - the occurrence must not sit in predicate position. A copula or a degree
#     word immediately in front of it ("the cost is HIGH", "the credit was
#     LOW", "very HIGH") makes it an adjective, whatever the page defines.
# Everything the committed page actually writes -- "§7 LOW", "more than MID",
# "7.9% of LOW's bill", "MID and HIGH both come in lower than LOW" -- is a name
# in a noun position and passes; nothing on it is in predicate position.
# The cost of the second rule, stated plainly: "the recommendation is LOW" is
# reported even though it means the package. That is the intended trade. The
# sentence is ambiguous to a reader too, and the page writes it as
# "Recommendation: LOW today", which is not.
PACKAGE_LABELS = frozenset({"LOW", "MID", "HIGH"})
PACKAGE_CARD_RE = re.compile(r"^([A-Z]{3,})\s*—\s*\S")
# A copula (or degree word), then any run of adverbs and degree words, then the
# label: "is HIGH", "is still HIGH", "is financially HIGH", "was very LOW".
# Reading only the word in front of the label let one adverb walk past the rule
# (Codex adversarial review, PR #262, pass 2). The run is deliberately limited
# to -ly adverbs and a closed list of degree words, so an ordinary noun phrase
# after the copula -- "is 7.9% of LOW", "is the recommendation for MID" -- keeps
# the label in a NAME position, which is what the page's 19 real uses are.
_MODIFIER = (r"(?:\w+ly|not|yet|still|even|also|again|already|almost|nearly|"
             r"about|only|just|quite|very|so|too|really|rather|somewhat|fairly|"
             r"unusually|extremely|especially|particularly|notably|consistently|"
             # Common adverbs with no -ly: temporal, frequency and degree. Without
             # these, "is now HIGH" and "is often HIGH" walked past the rule
             # (Codex adversarial review, PR #262, pass 3).
             r"now|then|often|never|always|sometimes|seldom|rarely|soon|once|twice|"
             r"ever|far|much|more|most|less|least|well|quite|pretty|somewhat)")
EMPHASIS_LEAD_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|am|seems?|seemed|stays?|stayed|"
    r"remains?|remained|looks?|looked|runs?|ran|gets?|got|feels?|felt|"
    r"too|very|so|quite|really|how|extremely|unusually)\s+"
    rf"(?:{_MODIFIER}\s+)*$", re.I)

# Visually equivalent long dashes, all folded to "—" by normalize() so one
# spelling of the rule catches every spelling of the character. The EN dash
# (U+2013) is deliberately absent: this report writes ranges with it.
LONG_DASHES = "—―⸺⸻︱﹘"
# Closing quotes a writer can slip between the comma and "not", straight or
# curly, single or double, plus the guillemets.
CLOSING_QUOTES = "\"'‘’“”»›"
_NORMALIZE = str.maketrans({c: "—" for c in LONG_DASHES})

# 1. An em dash, or the " -- " substitute. A hyphenated compound ("on-peak")
#    and a numeric range ("2024-2025") never match: the substitute needs
#    whitespace on both sides. Every other long dash is already an em dash by
#    the time this runs.
# 2. A SPACED en dash is the same rhetorical dash by another character, and
#    counts. A compact one is a range ("6–9pm", "2024–2025", "22.9–25.7¢"),
#    which this report writes 196 times, and never counts. Excluding the en
#    dash outright let the same dash-heavy rhythm come back under a
#    different code point (Codex adversarial review, PR #262, pass 3).
EM_DASH_RE = re.compile(r"—|(?<=\s)--(?=\s)|(?<=\s)–(?=\s)")
# 2. The "X, not Y" tail and its "rather than" twin. A closing quote may stand
#    between the comma and the "not" (`"measured," not "modeled"`); it is the
#    same tail, and reading it as a different one is how the rule was evaded.
TAIL_RE = re.compile(
    r",(?:\s+|\s*[" + re.escape(CLOSING_QUOTES) + r"]+\s*)not\s|\brather than\b", re.I)
# 3. A run of three or more capitals standing alone. The lookbehind and
#    lookahead keep the run from being a slice of a longer token: EV-TOU-5,
#    kWh/HDD and SDG&E are single words, not emphasis.
# A run of capitals, or a hyphenated chain of them ("THIS-WORD-IS-SHOUTED"),
# which every single-run pattern rejected because each part touches a hyphen
# (Codex review, PR #262). A chain is reported as one word, so a domain token
# like EV-TOU-5 is exempted by naming it in ACRONYMS, not by the pattern.
CAPS_RE = re.compile(r"(?<![A-Za-z0-9$§/&\-])([A-Z]{3,}(?:-[A-Z0-9]{1,})*)(?![A-Za-z0-9])")
# 4. The intensifiers, from prose_lint's shared word list.
INTENSIFIER_RE = re.compile(
    r"\b(" + "|".join(sorted(prose_lint.INTENSIFIERS, key=len, reverse=True)) + r")\b",
    re.I)

LIMITS = {
    "em_per_1k": 3.0,        # issue #252
    "tails_per_1k": 1.5,     # issue #253
    "caps": 0,               # issue #252
    "intensifiers": 0,       # issue #253
    "max_block_chars": 800,  # CLAUDE.md section 10, issue #255
}
# ("basic", "advanced", "all"), in the order the CLI reports them when it is
# asked for all three.
TIERS = prose_blocks.TIERS
SNIPPET_CHARS = 60


def _snippet(text, start, end):
    """`...match...` with a little context, whitespace already collapsed."""
    left = max(0, start - SNIPPET_CHARS // 2)
    right = min(len(text), end + SNIPPET_CHARS // 2)
    out = text[left:right]
    if left:
        out = "…" + out
    if right < len(text):
        out = out + "…"
    return out


def normalize(text):
    """Block text with every long dash folded to "—", ready to be measured.

    A rule written against one code point is evaded by the next one that looks
    identical on the page, so the variants are folded away before any pattern
    runs. The translation is one character for one character, which keeps every
    match offset (and so every reported snippet) aligned with the visible text.
    """
    return text.translate(_NORMALIZE)


def _measurable(blocks):
    """[(block, normalized text)] -- what every metric below actually reads."""
    return [(b, normalize(b.text)) for b in blocks]


def package_labels(blocks):
    """The PACKAGE_LABELS this page defines, read off its package-card headings.

    A card heading reads `LOW — $0 · behavior only`: the label, an em dash, then
    the price and the note. A page carrying that heading has a package called
    LOW and its prose may name it; a page without one does not, and the same
    word there is emphasis. The blocks passed in are the WHOLE page's, not one
    tier's, because the advanced tier's methodology names packages the basic
    tier's section 7 defines.
    """
    out = set()
    for b, text in _measurable(blocks):
        if b.tag not in HEADING_TAGS:
            continue
        m = PACKAGE_CARD_RE.match(text)
        if m is not None and m.group(1) in PACKAGE_LABELS:
            out.add(m.group(1))
    return frozenset(out)


def _sites(items, pattern):
    """(count, [f"L<line> <snippet>"]) for every match of pattern across items."""
    count = 0
    sites = []
    for b, text in items:
        for m in pattern.finditer(text):
            count += 1
            sites.append(f"L{b.line} {_snippet(text, m.start(), m.end())}")
    return count, sites


def _words(items, pattern, keep=None):
    """(matched words, [f"L<line> <word>"]) for matches kept by `keep(word, text, start)`."""
    words = []
    sites = []
    for b, text in items:
        for m in pattern.finditer(text):
            word = m.group(1) if m.groups() else m.group(0)
            if keep is not None and not keep(word, text, m.start()):
                continue
            words.append(word)
            sites.append(f"L{b.line} {word}")
    return words, sites


def _caps_keeper(defined):
    """A `keep` for the ALL-CAPS metric: True when this run of capitals is a shout.

    `defined` is the set of package labels the page earns from its own cards.
    """
    def keep(word, text, start):
        if word in ACRONYMS:
            return False
        if (word in PACKAGE_LABELS and word in defined
                and EMPHASIS_LEAD_RE.search(text[:start]) is None):
            return False
        return True
    return keep


def tier_split(html_text, tier):
    """The advanced-tier boundary line for `tier`, refusing an ambiguous page.

    Raises TierBoundaryError for a per-tier measurement of a page that does not
    carry exactly one `<details id="advanced">`. Reading such a page as all
    basic is what the previous source-scan did after a decoy in a comment moved
    the boundary to line 1, and every rate it printed was measured over the
    wrong words. The "all" tier spans the whole page either way, so it neither
    needs the boundary nor checks it.
    """
    if tier == "all":
        return None
    lines = prose_blocks.advanced_lines(html_text)
    offsets = prose_blocks.advanced_offsets(html_text)
    if len(offsets) != 1:
        raise TierBoundaryError(
            f"tier={tier} needs exactly one <details id=\"advanced\"> to split the page; "
            f"this one has {len(offsets)}"
            + (f" (lines {', '.join(str(n) for n in lines)})" if lines else ""))
    # A character OFFSET, which is what select_tier compares: the marker and the
    # blocks either side of it can share a line, and a line comparison then
    # hands a whole tier to the wrong side (Codex adversarial review, pass 2).
    return offsets[0]


def per_1k(count, words):
    """Rate per 1,000 words. An empty tier has no rate, so it reads 0.0."""
    return 0.0 if not words else count / words * 1000.0


def measure(html_text, tier="all", max_block_chars=None):
    """Every rhythm figure for one tier of a page, as a dict.

    Keys: tier, blocks, words (every measured block), prose_words (the
    non-heading blocks, the em-dash denominator), em_dashes, em_per_1k, tails,
    tails_per_1k, caps (the offending words), intensifiers (the offending
    words, as written), long_blocks (prose_blocks.Block objects over
    max_block_chars, which defaults to LIMITS["max_block_chars"]), and one
    `*_sites` list per metric giving `L<line> <what>` for each hit.

    Raises TierBoundaryError when tier is "basic" or "advanced" and the page
    does not carry exactly one `<details id="advanced">`.
    """
    if max_block_chars is None:
        max_block_chars = LIMITS["max_block_chars"]
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, not {tier!r}")
    # min_words=1: every visible block, short ones included. The 4-word floor
    # belongs to the 800-character cap, and is reapplied to that check alone.
    all_blocks = prose_blocks.extract(html_text, min_words=1)
    tier_blocks = prose_blocks.select_tier(all_blocks, tier_split(html_text, tier), tier)
    measured = _measurable(tier_blocks)
    prose = [(b, t) for b, t in measured if b.tag not in HEADING_TAGS]
    words = sum(b.words for b in tier_blocks)
    prose_words = sum(b.words for b, _ in prose)
    em, em_sites = _sites(prose, EM_DASH_RE)
    tails, tail_sites = _sites(measured, TAIL_RE)
    caps, caps_sites = _words(measured, CAPS_RE, keep=_caps_keeper(package_labels(all_blocks)))
    intens, intens_sites = _words(measured, INTENSIFIER_RE)
    # The 800-character cap covers EVERY measured block of the tier, headings
    # and short blocks included. Re-applying prose_blocks' 4-word floor here
    # let a paragraph holding one 801-character token report nothing, which
    # contradicts this module's own visible-block coverage (Codex adversarial
    # review, PR #262, pass 2). The standalone `prose_blocks.py --max-chars`
    # report keeps that floor; this gate is the stricter of the two.
    long_blocks = prose_blocks.over_limit(tier_blocks, max_block_chars)
    return {
        "tier": tier,
        "blocks": len(measured),
        "words": words,
        "prose_words": prose_words,
        "em_dashes": em,
        "em_per_1k": per_1k(em, prose_words),
        "em_sites": em_sites,
        "tails": tails,
        "tails_per_1k": per_1k(tails, words),
        "tail_sites": tail_sites,
        "caps": caps,
        "caps_sites": caps_sites,
        "intensifiers": intens,
        "intensifier_sites": intens_sites,
        "long_blocks": long_blocks,
    }


def check(html_text, tier="all", limits=None):
    """Violation descriptions for one tier (empty list means the tier is clean)."""
    lim = dict(LIMITS)
    if limits:
        unknown = set(limits) - set(LIMITS)
        if unknown:
            raise ValueError(f"unknown limit(s): {sorted(unknown)}")
        lim.update(limits)
    m = measure(html_text, tier, lim["max_block_chars"])
    out = []
    if m["em_per_1k"] > lim["em_per_1k"]:
        out.append(f"{tier} tier: em dashes {m['em_per_1k']:.3g} per 1,000 words "
                   f"(limit {lim['em_per_1k']:.1f}; {m['em_dashes']} in {m['prose_words']} "
                   f"non-heading words)")
    if m["tails_per_1k"] > lim["tails_per_1k"]:
        out.append(f"{tier} tier: 'X, not Y' tails {m['tails_per_1k']:.3g} per 1,000 words "
                   f"(limit {lim['tails_per_1k']:.1f}; {m['tails']} in {m['words']} words)")
    if len(m["caps"]) > lim["caps"]:
        out.append(f"{tier} tier: {len(m['caps'])} ALL-CAPS emphasis word(s) "
                   f"(limit {lim['caps']}): " + ", ".join(sorted(set(m["caps"]))))
    if len(m["intensifiers"]) > lim["intensifiers"]:
        out.append(f"{tier} tier: {len(m['intensifiers'])} intensifier(s) "
                   f"(limit {lim['intensifiers']}): "
                   + ", ".join(sorted(set(w.lower() for w in m["intensifiers"]))))
    if m["long_blocks"]:
        out.append(f"{tier} tier: {len(m['long_blocks'])} block(s) over "
                   f"{lim['max_block_chars']} characters: "
                   + ", ".join(f"L{b.line} {b.tag} {b.chars} chars" for b in m["long_blocks"]))
    return out


def offenders(m, limits=None):
    """`L<line> <what>` lists for the metrics of a measured tier that break a limit.

    Only a violated metric is listed. A page under every ceiling prints
    nothing, so the CLI stays quiet until it has something to say.
    """
    lim = dict(LIMITS)
    if limits:
        lim.update(limits)
    out = {}
    if m["em_per_1k"] > lim["em_per_1k"]:
        out["em dashes"] = m["em_sites"]
    if m["tails_per_1k"] > lim["tails_per_1k"]:
        out["'X, not Y' tails"] = m["tail_sites"]
    if len(m["caps"]) > lim["caps"]:
        out["ALL-CAPS emphasis"] = m["caps_sites"]
    if len(m["intensifiers"]) > lim["intensifiers"]:
        out["intensifiers"] = m["intensifier_sites"]
    if m["long_blocks"]:
        out["blocks over the cap"] = [prose_blocks.format_line(b) for b in m["long_blocks"]]
    return out


def summary_line(m, limits=None):
    """The one-line report for a measured tier (no trailing newline)."""
    lim = dict(LIMITS)
    if limits:
        lim.update(limits)
    return (f"PROSE RHYTHM tier={m['tier']} words={m['words']} "
            f"prose-words={m['prose_words']} "
            f"em={m['em_dashes']} ({m['em_per_1k']:.1f}/1k, max {lim['em_per_1k']:.1f}) "
            f"tails={m['tails']} ({m['tails_per_1k']:.1f}/1k, max {lim['tails_per_1k']:.1f}) "
            f"caps={len(m['caps'])} intensifiers={len(m['intensifiers'])} "
            f"long-blocks={len(m['long_blocks'])}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", nargs="?", help="page to measure (default: <repo root>/index.html)")
    p.add_argument("--tier", choices=TIERS, default="all",
                   help="measure one tier; 'all' reports basic, advanced and the whole page")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when any measured tier breaks a limit")
    args = p.parse_args(argv)
    path = pathlib.Path(args.path) if args.path else prose_blocks.default_page()
    if not path.is_file():
        print(f"PROSE RHYTHM ERROR: no such file {path}")
        return 2
    html_text = path.read_text(encoding="utf-8")
    tiers = TIERS if args.tier == "all" else (args.tier,)
    violations = []
    for tier in tiers:
        try:
            m = measure(html_text, tier)
        except TierBoundaryError as e:
            # A page that cannot be split is an error about the input, not a
            # verdict on its prose: exit 2, the same class as a missing file,
            # so a broken page can never be mistaken for a clean one.
            print(f"PROSE RHYTHM ERROR: {path}: {e}")
            return 2
        print(summary_line(m))
        found = check(html_text, tier)
        for v in found:
            print(f"  {v}")
        for metric, sites in offenders(m).items():
            print(f"  {metric}:")
            for site in sites:
                print(f"    {site}")
        violations.extend(found)
    if violations:
        print(f"PROSE RHYTHM FAIL: {len(violations)} violation(s)")
        return 1 if args.strict else 0
    print("PROSE RHYTHM OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
