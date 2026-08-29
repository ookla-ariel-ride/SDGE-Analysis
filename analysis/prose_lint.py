#!/usr/bin/env python3
"""A mechanical linter for the banned prose constructions CLAUDE.md already
enumerates in writing (issue #39 part 5). This is a GATE for
analysis/generate_report.py's generated fragments: lint(fragment) returning
any violation means the run hard-fails that block rather than publishing it,
exactly like the numeral guard. It is independent of --humanize (an optional
second LLM pass): humanized output must clear this SAME linter, not a looser
one.

RULES, straight from CLAUDE.md
  1. Process-narrative bans (CLAUDE.md section 9's own literal grep list):
     "supersedes the previous", "below the earlier ... estimate", "carried
     from the retired ... workpaper", "originally we", "this replaces",
     "... is kept for reference". CLAUDE.md's own text uses "..." as a
     placeholder for corpus-specific words (a day count, a workpaper name) in
     several of these -- PROCESS_NARRATIVE_PATTERNS matches on the STABLE
     words around each placeholder, not a literal reproduction of CLAUDE.md's
     example fill-ins.
  2. Negative parallelism: "not just X, but Y" (CLAUDE.md section 10's
     humanizer-checklist rule, and named again in section 8's delegation
     rules as a construction to avoid).
  3. Filler transitions: stock throat-clearing phrases the humanizer skill's
     source checklist (Wikipedia's "Signs of AI writing") flags.
  4. Promotional adjectives: generic marketing-register words ("cutting-edge",
     "seamless", "robust", ...) that CLAUDE.md section 10 bans from running
     prose.
  5. Intensifiers: "genuine", "honest", "robust" and their -ly forms, which
     claim candour instead of showing it (issue #253 stripped 30 of them out
     of index.html). INTENSIFIERS is the canonical word list for this rule and
     for the page-level version of it in analysis/prose_rhythm.py.
  6. Rule-of-three padding: CLAUDE.md section 10 bans "rule-of-three padding"
     outright, but a bare "A, B, and C" list is also just how a lot of
     legitimate technical prose reads in this report (e.g. "on-peak,
     off-peak, and super-off-peak"), so a length-3-list detector alone would
     false-positive constantly on domain vocabulary. This module narrows the
     check to a TRIAD OF GENERIC QUALITY ADJECTIVES (PADDING_ADJECTIVES) --
     "fast, reliable, and efficient" reads as padding; "on-peak, off-peak,
     and super-off-peak" does not, because none of those three words is a
     generic quality adjective. This is a deliberately narrow, low-false-
     positive heuristic, not a general list-of-three detector.

WHAT THIS MODULE DELIBERATELY DOES NOT CHECK
  CLAUDE.md section 10 also exempts the report's own STRUCTURAL em dash usage
  (day-band labels, table cells, meta rows, heading verdicts) from an em-dash
  density rule that applies to RUNNING PROSE in the full rendered page. This
  linter runs on isolated, single-slot PROSE FRAGMENTS before they are
  spliced into the page -- a fragment is never a day-band label, a table
  cell, a meta row, or a heading, so there is no structural em dash usage for
  it to produce, and no density rule to write or exempt. Implementing one
  here would be dead code guarding against a case this module's own inputs
  cannot produce. The density rule that DOES apply, to the finished page and
  per tier, lives in analysis/prose_rhythm.py (issue #256); it excludes
  headings for exactly the structural reason above.

USAGE
  lint(text) -> list[str] of violation descriptions (empty means clean).
  Run standalone to lint a file or stdin:
    ./.venv/bin/python analysis/prose_lint.py path/to/fragment.html
    echo "some prose" | ./.venv/bin/python analysis/prose_lint.py
"""
import re
import sys

# ---------------------------------------------------------------------------
# 1. Process-narrative bans (CLAUDE.md section 9's literal list).
# ---------------------------------------------------------------------------
PROCESS_NARRATIVE_PATTERNS = [
    (re.compile(r"below the earlier\b.{0,25}\bestimate\b", re.I),
     "'below the earlier ... estimate' (CLAUDE.md section 9 process-narrative ban)"),
    (re.compile(r"carried from the retired\b.{0,30}\bworkpaper\b", re.I),
     "'carried from the retired ... workpaper' (CLAUDE.md section 9 ban)"),
    (re.compile(r"supersedes the previous\b", re.I),
     "'supersedes the previous ...' (CLAUDE.md section 9 ban)"),
    (re.compile(r"\boriginally we\b", re.I),
     "'originally we ...' (CLAUDE.md section 9 ban)"),
    (re.compile(r"\bthis replaces\b", re.I),
     "'this replaces ...' (CLAUDE.md section 9 ban)"),
    (re.compile(r"\bis kept for reference\b", re.I),
     "'... is kept for reference' (CLAUDE.md section 9 ban)"),
]

# ---------------------------------------------------------------------------
# 2. Negative parallelism.
# ---------------------------------------------------------------------------
_NEGATIVE_PARALLELISM_RE = re.compile(r"\bnot just\b[^.]{0,80}\bbut\b", re.I)

# ---------------------------------------------------------------------------
# 3. Filler transitions (Wikipedia "Signs of AI writing" stock phrases).
# ---------------------------------------------------------------------------
FILLER_TRANSITIONS = [
    "it is worth noting that", "it's worth noting that", "it should be noted that",
    "in today's world", "in today's landscape", "at the end of the day",
    "when it comes to", "needless to say", "in conclusion", "in summary",
    "on the other hand", "as previously mentioned", "with that said",
    "moving forward", "last but not least",
]

# ---------------------------------------------------------------------------
# 4. Promotional adjectives.
# ---------------------------------------------------------------------------
PROMOTIONAL_ADJECTIVES = [
    "cutting-edge", "cutting edge", "state-of-the-art", "seamless", "seamlessly",
    "robust", "unparalleled", "revolutionary", "game-changing", "game changer",
    "world-class", "best-in-class", "unmatched", "unrivaled", "groundbreaking",
    "transformative", "unlock", "unlocking", "elevate", "elevating", "delve",
    "delving", "boast", "boasts", "boasting", "leverage", "leveraging",
    "empower", "empowering", "supercharge", "next-generation", "next generation",
]

# ---------------------------------------------------------------------------
# 5. Intensifiers that claim candour instead of showing it.
#
# "genuine", "honest" and "robust" assert a quality the sentence around them
# should be demonstrating; issue #253 stripped 30 of them out of index.html.
# This frozenset is the CANONICAL list for both gates: prose_lint checks it on
# each generated fragment, and analysis/prose_rhythm.py imports it to check the
# rendered page, so the two cannot drift apart. "robust" also appears in
# PROMOTIONAL_ADJECTIVES above, where it earns its place on a different ground
# (marketing register); a fragment using it trips both rules, which this
# linter has always allowed.
# ---------------------------------------------------------------------------
INTENSIFIERS = frozenset({
    "genuine", "genuinely", "honest", "honestly", "robust", "robustly",
})
_INTENSIFIER_RE = re.compile(
    r"\b(" + "|".join(sorted(INTENSIFIERS, key=len, reverse=True)) + r")\b", re.I)

# ---------------------------------------------------------------------------
# 6. Rule-of-three padding: a narrow, low-false-positive heuristic.
# ---------------------------------------------------------------------------
PADDING_ADJECTIVES = {
    "fast", "reliable", "efficient", "robust", "simple", "elegant", "powerful",
    "seamless", "comprehensive", "versatile", "innovative", "intuitive",
    "scalable", "effective", "valuable", "straightforward", "flexible",
    "convenient", "user-friendly", "streamlined", "dynamic",
}
_TRIAD_RE = re.compile(r"\b([A-Za-z-]+),\s*([A-Za-z-]+),?\s+and\s+([A-Za-z-]+)\b")


def _check_process_narrative(text):
    return [f"process-narrative construction: {label}"
           for pattern, label in PROCESS_NARRATIVE_PATTERNS if pattern.search(text)]


def _check_negative_parallelism(text):
    m = _NEGATIVE_PARALLELISM_RE.search(text)
    if m:
        return [f"negative parallelism ('not just X, but Y'): {m.group(0)!r}"]
    return []


def _check_filler_transitions(text):
    low = text.lower()
    return [f"filler transition: {phrase!r}" for phrase in FILLER_TRANSITIONS if phrase in low]


def _check_promotional_adjectives(text):
    low = text.lower()
    hits = []
    for word in PROMOTIONAL_ADJECTIVES:
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            hits.append(f"promotional adjective: {word!r}")
    return hits


def _check_intensifiers(text):
    hits = sorted({m.group(1).lower() for m in _INTENSIFIER_RE.finditer(text)})
    return [f"intensifier: {word!r}" for word in hits]


def _check_rule_of_three(text):
    violations = []
    for m in _TRIAD_RE.finditer(text):
        words = [w.lower() for w in m.groups()]
        hits = [w for w in words if w in PADDING_ADJECTIVES]
        if len(hits) >= 2:
            violations.append(f"rule-of-three padding: {m.group(0)!r} (generic quality "
                              f"adjectives: {hits})")
    return violations


CHECKS = [
    _check_process_narrative,
    _check_negative_parallelism,
    _check_filler_transitions,
    _check_promotional_adjectives,
    _check_intensifiers,
    _check_rule_of_three,
]


def lint(text):
    """All violations found in `text` (empty list means clean). Order
    matches CHECKS; a fragment can trip more than one rule at once."""
    violations = []
    for check in CHECKS:
        violations.extend(check(text))
    return violations


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = pathlib_read(argv[0]) if argv else sys.stdin.read()
    violations = lint(text)
    if not violations:
        print("clean: no banned construction found")
        return 0
    print(f"{len(violations)} violation(s):")
    for v in violations:
        print(f"  - {v}")
    return 1


def pathlib_read(path):
    import pathlib
    return pathlib.Path(path).read_text()


if __name__ == "__main__":
    sys.exit(main())
