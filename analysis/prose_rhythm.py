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
  prose_blocks.extract() supplies the blocks (it already drops tables, the
  day-band, the .meta ledger, nav, pills, <code> spans and every other
  non-prose subtree; see its docstring), and this module then drops the
  HEADING blocks h1-h4. Headings are excluded because CLAUDE.md section 10
  makes the heading verdict a piece of the design language, the same way it
  does for day-band labels, .meta rows and table cells: an em dash there is a
  typographic separator the template asks for, not a writer reaching for one.
  So the text measured here is what a reader reads as sentences, and nothing
  else.

THE FOUR METRICS
  1. EM DASHES per 1,000 words. Counts the character "—" plus the " -- "
     substitute (a double hyphen surrounded by spaces; a hyphenated compound
     or a range never matches).
  2. "X, NOT Y" TAILS per 1,000 words. Counts `,\\s+not\\s` and `rather than`.
     One is a useful contrast. A page full of them is a tic.
  3. ALL-CAPS EMPHASIS, an absolute count. A run of three or more capitals
     that is not in ACRONYMS. Two-letter forms are out of scope by
     construction, so shouting a two-letter word is not caught.
  4. INTENSIFIERS, an absolute count. The words in prose_lint.INTENSIFIERS
     ("genuine", "honest", "robust" and their -ly forms), which claim candour
     instead of showing it. The word list lives in prose_lint.py so the
     fragment gate and this page gate cannot drift apart.
  Carried over from issue #255: no block over LIMITS["max_block_chars"]
  visible characters. That check is prose_blocks.over_limit(), called here,
  not reimplemented, and it is applied to the tier's blocks INCLUDING
  headings, exactly as `prose_blocks.py --max-chars 800` applies it.

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
  regex.

USAGE
  prose_rhythm.py [--tier basic|advanced|all] [--strict] [PATH]
    PATH defaults to <repo root>/index.html.
    Prints one `PROSE RHYTHM ...` summary line per tier measured (the default
    --tier all reports basic, advanced and the whole page), then an indented
    offender list under any tier that breaks a limit. Exit 0 unless --strict,
    which exits 1 when any measured tier has a violation; a missing file exits 2.

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

# Headings carry the design language's own em dashes (CLAUDE.md section 10's
# heading verdicts), so they are not running prose for this module's purposes.
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})

# The allowlist for metric 3, grown by hand over issues #252 and #253: every
# entry is an acronym, initialism or all-caps proper name this report's
# vocabulary actually uses. Entries the running prose does not currently use
# (JSON, CSV, PVWATTS, ...) appear elsewhere on the page, inside the tables and
# <code> spans the extractor excludes, and stay here so restating one in prose
# is not a false alarm. Two-letter forms are deliberately absent: the
# three-or-more rule cannot reach them.
ACRONYMS = frozenset({
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
    # all-caps proper names: the repo's own documents and the package tiers
    "CLAUDE", "TECHNICAL", "GLOSSARY", "README", "LOW", "MID", "HIGH",
})

# 1. An em dash, or the " -- " substitute. A hyphenated compound ("on-peak")
#    and a numeric range ("2024-2025") never match: the substitute needs
#    whitespace on both sides.
EM_DASH_RE = re.compile(r"—|(?<=\s)--(?=\s)")
# 2. The "X, not Y" tail and its "rather than" twin.
TAIL_RE = re.compile(r",\s+not\s|\brather than\b", re.I)
# 3. A run of three or more capitals standing alone. The lookbehind and
#    lookahead keep the run from being a slice of a longer token: EV-TOU-5,
#    kWh/HDD and SDG&E are single words, not emphasis.
CAPS_RE = re.compile(r"(?<![A-Za-z0-9$§/&\-])([A-Z]{3,})(?![A-Za-z0-9\-])")
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


def _sites(blocks, pattern):
    """(count, [f"L<line> <snippet>"]) for every match of pattern across blocks."""
    count = 0
    sites = []
    for b in blocks:
        for m in pattern.finditer(b.text):
            count += 1
            sites.append(f"L{b.line} {_snippet(b.text, m.start(), m.end())}")
    return count, sites


def _words(blocks, pattern, keep=None):
    """(matched words, [f"L<line> <word>"]) for matches kept by `keep(word)`."""
    words = []
    sites = []
    for b in blocks:
        for m in pattern.finditer(b.text):
            word = m.group(1) if m.groups() else m.group(0)
            if keep is not None and not keep(word):
                continue
            words.append(word)
            sites.append(f"L{b.line} {word}")
    return words, sites


def per_1k(count, words):
    """Rate per 1,000 words. An empty tier has no rate, so it reads 0.0."""
    return 0.0 if not words else count / words * 1000.0


def measure(html_text, tier="all", max_block_chars=None):
    """Every rhythm figure for one tier of a page, as a dict.

    Keys: tier, blocks, words, em_dashes, em_per_1k, tails, tails_per_1k,
    caps (the offending words), intensifiers (the offending words, as written),
    long_blocks (prose_blocks.Block objects over max_block_chars, which
    defaults to LIMITS["max_block_chars"]), and one `*_sites` list per metric
    giving `L<line> <what>` for each hit.
    """
    if max_block_chars is None:
        max_block_chars = LIMITS["max_block_chars"]
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, not {tier!r}")
    all_blocks = prose_blocks.extract(html_text)
    tier_blocks = prose_blocks.select_tier(all_blocks,
                                           prose_blocks.advanced_line(html_text), tier)
    prose = [b for b in tier_blocks if b.tag not in HEADING_TAGS]
    words = sum(b.words for b in prose)
    em, em_sites = _sites(prose, EM_DASH_RE)
    tails, tail_sites = _sites(prose, TAIL_RE)
    caps, caps_sites = _words(prose, CAPS_RE, keep=lambda w: w not in ACRONYMS)
    intens, intens_sites = _words(prose, INTENSIFIER_RE)
    # The 800-character cap is prose_blocks' own gate, applied to the same
    # blocks `prose_blocks.py --max-chars 800 --tier T` would apply it to,
    # headings included.
    long_blocks = prose_blocks.over_limit(tier_blocks, max_block_chars)
    return {
        "tier": tier,
        "blocks": len(prose),
        "words": words,
        "em_dashes": em,
        "em_per_1k": per_1k(em, words),
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
        out.append(f"{tier} tier: em dashes {m['em_per_1k']:.1f} per 1,000 words "
                   f"(limit {lim['em_per_1k']:.1f}; {m['em_dashes']} in {m['words']} words)")
    if m["tails_per_1k"] > lim["tails_per_1k"]:
        out.append(f"{tier} tier: 'X, not Y' tails {m['tails_per_1k']:.1f} per 1,000 words "
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
        m = measure(html_text, tier)
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
