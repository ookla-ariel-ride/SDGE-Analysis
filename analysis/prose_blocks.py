#!/usr/bin/env python3
"""Extract the running-prose blocks of a report page and measure them (issue #255).

CLAUDE.md section 10 caps a paragraph of running prose at about 800 characters.
Nothing measured that until now: the cap lived in a sentence, and the audit that
produced issue #255 found 46 blocks over it, the worst near 3,800 characters.
This module is the instrument. It turns index.html into a list of prose blocks,
each with its source line and its visible-text length, so the cap can be checked
by a command instead of by reading.

WHAT COUNTS AS A PROSE BLOCK
  One of the elements in BLOCK_TAGS (p, li, figcaption, summary, blockquote,
  dd, dt, h1-h4, div), taken as the INNERMOST such element that directly
  contains the text. A nested block tag starts a new block: a <div class="note">
  holding three <p>s yields the three <p>s and, for the div itself, only the
  text it holds directly (usually nothing). A <div class="note"> with bare text
  yields the div. Inline elements (b, i, span, a, ...) do not split a block;
  their text belongs to the block around them. A <p> whose optional </p> is
  omitted ends where HTML ends it: at the next start tag in P_IMPLICIT_CLOSERS
  (ul, ol, table, section, details, div, h1-h6, ...), so `<p>text<ul>` measures
  the same as `<p>text</p><ul>`.

WHAT IS EXCLUDED (the whole subtree, no text collected)
  Elements that are not running prose: script, style, nav, table, svg, canvas,
  button, select, option, label, pre, and code (a code span inside a paragraph
  is stripped from the paragraph's text, so a citation like `data/x.json` does
  not count toward its length). Also any element whose class list contains one
  of SKIP_CLASSES: the day-band, the header meta ledger, the nav and TOC, the
  evidence pills, chart legends, the back-to-top button and the reading-progress
  hairline. Excluding by class token means class="pill y" is excluded and
  class="meta-row" is not on its own; meta-row sits inside class="meta", which
  is.

MEASUREMENT
  <br> becomes a space; whitespace is collapsed to single spaces; entities are
  decoded. chars is len(text) of the collapsed visible text and words is the
  number of whitespace-separated tokens. Blocks under MIN_WORDS words are
  dropped: a "4.2 kWh" table-free figure card or a one-word heading is not a
  paragraph and cannot break the cap. A caller that needs the short blocks too
  passes extract(html, min_words=1); prose_rhythm.py does, because a
  three-word block can still shout a word even though it cannot break the cap.

TIERS
  --tier splits the page at the line of the page's `<details id="advanced">`:
  blocks that open on an earlier line are the basic tier, blocks from that line
  on are the advanced tier. A page with no such element is all basic.
  advanced_lines() finds that element by PARSING, not by scanning the source:
  an id in a comment or a script string is not an element, `id="advanced-help"`
  is not `id="advanced"`, and a page may legitimately have more than one match
  to report. advanced_line() keeps the single-value contract (the first line, or
  None); a caller that must not guess which one is meant reads advanced_lines()
  and decides for itself, the way prose_rhythm.py does.

USAGE
  prose_blocks.py [--max-chars N] [--tier basic|advanced|all] [--list] [PATH]
    PATH defaults to <repo root>/index.html.
    Without --max-chars: prints `PROSE BLOCKS <N> blocks, <W> words` and exits 0
    (--list then lists every block).
    With --max-chars N: prints `PROSE BLOCKS <N> blocks, <W> words, <K> over N`,
    lists each offending block as `L<line> <tag> <chars> chars: <first 80 chars>`,
    and exits 1 when K > 0. When K == 0 the summary reads `PROSE BLOCKS OK ...`.

  As a library:
    extract(html_text, min_words=MIN_WORDS) -> list[Block]
    over_limit(blocks, max_chars) -> list[Block]
    advanced_lines(html_text) -> list[int]      every real advanced-tier marker
    advanced_line(html_text)    -> int | None   the first line, or None
    advanced_offsets(html_text) -> [int]        every offset, document order
    advanced_offset(html_text)  -> int | None   the first offset, or None (the
                                                value select_tier compares)
    report(path, max_chars=None, tier="all") -> (blocks, over)

Stdlib only. Runs anywhere, with or without private/ and with or without git.
"""
import argparse
import dataclasses
import pathlib
import sys
from html.parser import HTMLParser

BLOCK_TAGS = frozenset({
    "p", "li", "figcaption", "summary", "blockquote", "dd", "dt",
    "h1", "h2", "h3", "h4", "h5", "h6", "div",
    # Semantic flow containers. Bare text directly inside one of these was
    # invisible to every metric, so an ordinary HTML refactor could switch
    # the gate off for real prose (Codex adversarial review, PR #262).
    "section", "article", "aside", "main", "header", "footer",
})
SKIP_TAGS = frozenset({
    "script", "style", "nav", "table", "svg", "canvas", "button", "select",
    "option", "label", "pre", "code",
})
SKIP_CLASSES = frozenset({
    "day-band", "meta", "nav", "dayband", "band", "skip", "toc", "pill",
    "legend", "back-to-top", "progress",
})
# Elements that never close, so they must not be pushed onto the open-element
# stack (a </br> that never comes would otherwise leave the stack misaligned).
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})
# Start tags before which HTML lets an author omit </p> (the spec's "p end tag
# omission" rule). Valid markup may write `<p>text<ul>...` and mean two blocks;
# without this list the open <p> would absorb the list's text.
P_IMPLICIT_CLOSERS = frozenset({
    "address", "article", "aside", "blockquote", "details", "div", "dl",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav", "ol",
    "p", "pre", "section", "table", "ul",
})
MIN_WORDS = 4
# The advanced tier opens at `<details id="advanced">`. The id is matched whole
# and case-sensitively: `advanced-help` is a different element, and moving the
# boundary is exactly what a stray near-miss would do to every tier measurement
# downstream.
ADVANCED_TAG = "details"
ADVANCED_ID = "advanced"
PREVIEW_CHARS = 80
TIERS = ("basic", "advanced", "all")


@dataclasses.dataclass
class Block:
    tag: str
    id: str
    cls: str
    line: int
    # Absolute character offset of the opening tag. Tier selection compares
    # this, not `line`: minified or same-line markup puts blocks either side of
    # the advanced marker on one line, and a line comparison then hands a whole
    # tier to the wrong side (Codex adversarial review, PR #262, pass 2).
    pos: int
    text: str
    chars: int
    words: int

    @property
    def label(self):
        """`tag#id`, else `tag.class`, else `tag`: how the block is named in output."""
        if self.id:
            return f"{self.tag}#{self.id}"
        if self.cls:
            return f"{self.tag}." + ".".join(self.cls.split())
        return self.tag


class _Positions:
    """Absolute character offsets from html.parser's (line, column) positions.

    html.parser reports a position, not an offset, and a tier boundary compared
    by line alone breaks on same-line markup, so every parser here records where
    a tag really starts in the document."""

    def _index_lines(self, html_text):
        self._line_start = [0]
        for line in html_text.splitlines(keepends=True):
            self._line_start.append(self._line_start[-1] + len(line))

    def _offset(self, line, col):
        starts = getattr(self, "_line_start", None)
        if not starts or line - 1 >= len(starts):
            return 0
        return starts[line - 1] + col


class _Frame:
    __slots__ = ("tag", "skip", "parts", "line", "pos", "id", "cls", "is_block")

    def __init__(self, tag, skip, is_block, line, pos, id_, cls):
        self.tag = tag
        self.skip = skip          # True when THIS element started an excluded subtree
        self.is_block = is_block  # True when this element collects prose of its own
        self.parts = []
        self.line = line
        self.pos = pos
        self.id = id_
        self.cls = cls


class _Extractor(_Positions, HTMLParser):
    def __init__(self, min_words=MIN_WORDS):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.skip_depth = 0
        self.blocks = []
        self.min_words = min_words

    # -- helpers ------------------------------------------------------------
    def _current_block(self):
        for frame in reversed(self.stack):
            if frame.is_block:
                return frame
        return None

    def _append(self, text):
        if self.skip_depth:
            return
        frame = self._current_block()
        if frame is not None:
            frame.parts.append(text)

    def _close(self, frame):
        if frame.skip:
            self.skip_depth -= 1
        if frame.is_block:
            text = " ".join("".join(frame.parts).split())
            words = len(text.split())
            if words >= self.min_words:
                self.blocks.append(Block(frame.tag, frame.id, frame.cls, frame.line,
                                         frame.pos, text, len(text), words))

    def _close_open_p(self):
        """Close an open <p> the way the HTML parser does when a start tag that
        ends a paragraph arrives: pop down to the innermost prose block if it
        is a <p>, closing any inline elements still open inside it."""
        frame = self._current_block()
        if frame is None or frame.tag != "p":
            return
        while self.stack:
            popped = self.stack.pop()
            self._close(popped)
            if popped is frame:
                return

    def _implicit_close(self, tag):
        """Close what HTML closes for us: an open <p> when any block tag opens,
        and an open <li> (or <dd>/<dt>) when its sibling opens with no list
        element in between. Without this, `<li>a<li>b` nests b inside a and the
        two blocks come out in reverse order."""
        self._close_open_p()
        siblings = {"li": ("li",), "dd": ("dd", "dt"), "dt": ("dd", "dt")}.get(tag)
        if siblings is None:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            t = self.stack[i].tag
            if t in ("ul", "ol", "dl", "menu") or self.stack[i].skip:
                return
            if t in siblings:
                while len(self.stack) > i:
                    self._close(self.stack.pop())
                return

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "br":
            self._append(" ")
        if tag in P_IMPLICIT_CLOSERS:
            # `<p>text<ul>` with the optional </p> omitted: the paragraph ends
            # here, before this tag is classified as skipped, block or inline.
            self._close_open_p()
        if tag in VOID_TAGS:
            return
        attrs = {k.lower(): (v or "") for k, v in attrs}
        classes = set(attrs.get("class", "").split())
        starts_skip = (not self.skip_depth
                       and (tag in SKIP_TAGS or bool(classes & SKIP_CLASSES)))
        if starts_skip:
            self.skip_depth += 1
        is_block = not self.skip_depth and tag in BLOCK_TAGS
        if is_block:
            self._implicit_close(tag)
        line, col = self.getpos()
        self.stack.append(_Frame(tag, starts_skip, is_block, line,
                                 self._offset(line, col),
                                 attrs.get("id", ""), attrs.get("class", "")))

    def handle_startendtag(self, tag, attrs):
        # <br/> and friends: treat exactly like the start tag; a void tag is
        # never pushed, and a non-void self-closed tag opens nothing.
        tag = tag.lower()
        if tag == "br":
            self._append(" ")
        if tag in P_IMPLICIT_CLOSERS:
            self._close_open_p()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                # Close everything the stray-open elements above it left open,
                # innermost first, so nested blocks finalize before their parent.
                while len(self.stack) > i:
                    self._close(self.stack.pop())
                return
        # An end tag with no open element: ignore it.

    def handle_data(self, data):
        self._append(data)

    def finish(self):
        while self.stack:
            self._close(self.stack.pop())
        # Blocks finalize at their END tag, so an inner block lands before its
        # parent; the list is returned in document order of the OPENING tag.
        self.blocks.sort(key=lambda b: b.pos)
        return self.blocks


def extract(html_text, min_words=MIN_WORDS):
    """The prose blocks of a page, in document order, each at least min_words words.

    min_words defaults to MIN_WORDS, the 4-word floor the 800-character cap is
    measured under. Pass 1 for every visible block, short ones included.
    """
    parser = _Extractor(min_words)
    parser._index_lines(html_text)
    parser.feed(html_text)
    parser.close()
    return parser.finish()


def over_limit(blocks, max_chars):
    """The blocks whose visible text is longer than max_chars, in document order."""
    return [b for b in blocks if b.chars > max_chars]


class _AdvancedFinder(_Positions, HTMLParser):
    """Every line where a real `<details id="advanced">` element opens.

    A parser, not a source scan, because the source scan this replaced matched
    text that is not an element at all: `<details id="advanced-help">` written
    inside an HTML comment moved the tier boundary to the comment's line, which
    silently re-tiered the whole page. handle_comment is not handle_starttag, and
    html.parser hands script and style bodies to handle_data rather than parsing
    them, so a comment and a string in a script are both invisible here.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.offsets = []

    def _check(self, tag, attrs):
        if tag.lower() != ADVANCED_TAG:
            return
        for key, value in attrs:
            if key.lower() == "id" and (value or "").strip() == ADVANCED_ID:
                line, col = self.getpos()
                self.lines.append(line)
                self.offsets.append(self._offset(line, col))
                return

    def handle_starttag(self, tag, attrs):
        self._check(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._check(tag, attrs)


def advanced_lines(html_text):
    """1-based lines of every real `<details id="advanced">`, in document order.

    Empty when the page has none. Two or more entries mean the page states its
    tier boundary twice; this function reports that instead of picking one, so a
    caller can refuse rather than measure the wrong half of the page.
    """
    parser = _AdvancedFinder()
    parser._index_lines(html_text)
    parser.feed(html_text)
    parser.close()
    return parser.lines


def advanced_offsets(html_text):
    """Character offsets of every real `<details id="advanced">`, document order.

    The tier boundary is compared as an offset, never as a line: same-line or
    minified markup puts blocks either side of the marker on one line, and a
    line comparison then assigns a whole tier to the wrong side.
    """
    parser = _AdvancedFinder()
    parser._index_lines(html_text)
    parser.feed(html_text)
    parser.close()
    return parser.offsets


def advanced_offset(html_text):
    """The first real advanced-tier offset, or None: what select_tier compares."""
    offsets = advanced_offsets(html_text)
    return offsets[0] if offsets else None


def advanced_line(html_text):
    """1-based line of the first `<details id="advanced">`, or None if there is none."""
    lines = advanced_lines(html_text)
    return lines[0] if lines else None


def select_tier(blocks, split_pos, tier):
    """The blocks of one tier: basic opens before split_pos, advanced from it on.

    split_pos is a character OFFSET (advanced_offsets), not a line number. A
    line comparison read every block sharing the marker's line as advanced, so
    a minified page handed its whole basic tier to the advanced side and every
    rate was measured over the wrong words (Codex adversarial review, PR #262).
    """
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, not {tier!r}")
    if tier == "all":
        return list(blocks)
    if split_pos is None:
        return list(blocks) if tier == "basic" else []
    if tier == "basic":
        return [b for b in blocks if b.pos < split_pos]
    return [b for b in blocks if b.pos >= split_pos]


def report(path, max_chars=None, tier="all"):
    """(blocks, over) for the page at path; over is [] when max_chars is None."""
    html_text = pathlib.Path(path).read_text(encoding="utf-8")
    blocks = select_tier(extract(html_text), advanced_offset(html_text), tier)
    over = over_limit(blocks, max_chars) if max_chars is not None else []
    return blocks, over


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (matches stamp_report_version.py)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains both analysis/ and data/")


def default_page():
    return _repo_root() / "index.html"


def format_line(block):
    """`L<line> <tag> <chars> chars: <first 80 chars>` (an ellipsis marks a cut)."""
    preview = block.text[:PREVIEW_CHARS]
    if len(block.text) > PREVIEW_CHARS:
        preview += "…"
    return f"L{block.line} {block.tag} {block.chars} chars: {preview}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", nargs="?", help="page to measure (default: <repo root>/index.html)")
    p.add_argument("--max-chars", type=int, default=None, metavar="N",
                   help="fail (exit 1) when any block's visible text exceeds N characters")
    p.add_argument("--tier", choices=TIERS, default="all",
                   help="measure only one tier, split at <details id=\"advanced\">")
    p.add_argument("--list", action="store_true", dest="list_blocks",
                   help="list every block (or, with --max-chars, every offender)")
    args = p.parse_args(argv)
    if args.max_chars is not None and args.max_chars < 1:
        p.error("--max-chars must be a positive integer")
    path = pathlib.Path(args.path) if args.path else default_page()
    if not path.is_file():
        print(f"PROSE BLOCKS ERROR: no such file {path}")
        return 2
    blocks, over = report(path, args.max_chars, args.tier)
    words = sum(b.words for b in blocks)
    tier_note = "" if args.tier == "all" else f" ({args.tier} tier)"
    if args.max_chars is None:
        print(f"PROSE BLOCKS {len(blocks)} blocks, {words} words{tier_note}")
        if args.list_blocks:
            for b in blocks:
                print(format_line(b))
        return 0
    ok = "OK " if not over else ""
    print(f"PROSE BLOCKS {ok}{len(blocks)} blocks, {words} words, "
          f"{len(over)} over {args.max_chars}{tier_note}")
    if over or args.list_blocks:
        for b in over:
            print(format_line(b))
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
