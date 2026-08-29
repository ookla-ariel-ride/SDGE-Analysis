#!/usr/bin/env python3
"""Tests for prose_blocks.py (issue #255 part 1): the extraction rules on small
synthetic pages, the cap check with a positive control on both sides of the
limit, the tier split, the CLI's exit codes, and a read-only smoke run over the
committed index.html.

Run from the repo root:  ./.venv/bin/python analysis/test_prose_blocks.py
"""
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import prose_blocks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "analysis" / "prose_blocks.py"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def _texts(html):
    return [b.text for b in prose_blocks.extract(html)]


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=cwd or ROOT)


# --- exclusions ---------------------------------------------------------------
@case
def case_table_cells_are_not_blocks():
    html = ("<table><tr><td><p>this paragraph sits inside a table cell</p></td></tr></table>"
            "<p>this paragraph sits outside the table</p>")
    assert _texts(html) == ["this paragraph sits outside the table"], _texts(html)
    return "text inside <table> is excluded, even in a <p> within a cell"


@case
def case_nav_dayband_meta_and_progress_are_excluded():
    html = ("<nav><div>Bottom line Plans Battery Packages</div></nav>"
            "<div class=\"dayband\"><div>super off-peak 12am to 6am at 11 cents</div></div>"
            "<div class=\"meta\"><div class=\"meta-row\"><span class=\"meta-k\">Window</span>"
            "<span class=\"meta-v\">one full year of fifteen minute data</span></div></div>"
            "<div class=\"progress\"><p>reading progress hairline text here</p></div>"
            "<p>this is the only running prose on the page</p>")
    assert _texts(html) == ["this is the only running prose on the page"], _texts(html)
    return "<nav>, .dayband, .meta (and the .meta-row inside it) and .progress are excluded"


@case
def case_class_match_is_by_token_not_substring():
    html = ("<p class=\"pill y\">modeled at current rates for one year</p>"
            "<p class=\"pillbox\">a class that merely starts with pill still counts here</p>")
    assert _texts(html) == ["a class that merely starts with pill still counts here"], _texts(html)
    return "class=\"pill y\" is excluded; class=\"pillbox\" is not (token match, not substring)"


@case
def case_script_and_style_text_is_excluded():
    html = ("<script>var words = 'these are not prose words at all';</script>"
            "<style>.x { content: 'nor are these style words prose'; }</style>"
            "<p>only this sentence is visible prose text</p>")
    assert _texts(html) == ["only this sentence is visible prose text"], _texts(html)
    return "<script> and <style> text is excluded"


@case
def case_code_span_is_dropped_from_the_blocks_chars():
    with_code = "<p>see the artifact <code>data/battery_plan_matrix.json</code> for the figure</p>"
    without = "<p>see the artifact  for the figure</p>"
    (a,) = prose_blocks.extract(with_code)
    (b,) = prose_blocks.extract(without)
    assert a.text == "see the artifact for the figure", a.text
    assert a.chars == b.chars == len("see the artifact for the figure"), (a.chars, b.chars)
    assert a.words == 6, a.words
    return "a <code> span inside a <p> contributes nothing to the block's text, chars or words"


@case
def case_pill_inside_a_paragraph_is_dropped():
    html = ("<p>the battery saves money every year <span class=\"pill g\">measured</span> "
            "on this tariff</p>")
    (b,) = prose_blocks.extract(html)
    assert b.text == "the battery saves money every year on this tariff", b.text
    return "an evidence pill inside a paragraph is stripped from the paragraph's text"


# --- block boundaries -----------------------------------------------------------
@case
def case_nested_note_div_yields_its_paragraphs_not_the_div():
    html = ("<div class=\"note\" data-label=\"Caveat\">\n"
            "  <p>first paragraph inside the note has enough words</p>\n"
            "  <p>second paragraph inside the note has enough words</p>\n"
            "</div>")
    blocks = prose_blocks.extract(html)
    assert [b.tag for b in blocks] == ["p", "p"], [(b.tag, b.text) for b in blocks]
    assert [b.line for b in blocks] == [2, 3], [b.line for b in blocks]
    assert blocks[0].cls == "" and blocks[0].id == "", (blocks[0].cls, blocks[0].id)
    return "div.note > p yields the <p>s; the div holds only whitespace and yields nothing"


@case
def case_bare_text_div_yields_the_div():
    html = "<div class=\"note\"><b>Bold claim sentence here.</b> Then the plain rest of the note.</div>"
    (b,) = prose_blocks.extract(html)
    assert b.tag == "div" and b.cls == "note", (b.tag, b.cls)
    assert b.text == "Bold claim sentence here. Then the plain rest of the note.", b.text
    assert b.label == "div.note", b.label
    return "a div with bare text (inline <b> does not split it) yields the div itself"


@case
def case_div_with_both_bare_text_and_a_nested_p_yields_both():
    html = ("<div class=\"rec\">the div's own bare words come first here"
            "<p>and the nested paragraph is its own block</p></div>")
    blocks = prose_blocks.extract(html)
    assert [(b.tag, b.text) for b in blocks] == [
        ("div", "the div's own bare words come first here"),
        ("p", "and the nested paragraph is its own block"),
    ], [(b.tag, b.text) for b in blocks]
    # Opening-tag order, which is the documented contract: the <div> opens
    # first even though it closes last. Ordering by line put the <p> first
    # whenever both shared a line -- an accident of the sort key, not a rule
    # (the same key mis-assigned tiers on a minified page, PR #262 pass 2).
    assert blocks[0].pos < blocks[1].pos, [(b.tag, b.pos) for b in blocks]
    return ("bare text belongs to the innermost block that directly holds it; the <p> is "
            "separate, and both come back in opening-tag order")


@case
def case_br_becomes_a_space_and_whitespace_collapses():
    html = "<p>line one of the\n   paragraph<br>line two of it<br/>and\tline   three</p>"
    (b,) = prose_blocks.extract(html)
    assert b.text == "line one of the paragraph line two of it and line three", b.text
    assert b.chars == len(b.text) and b.words == 12, (b.chars, b.words)
    return "<br> and <br/> become a space; runs of whitespace collapse to one space"


@case
def case_entities_are_decoded_before_measuring():
    html = "<p>SDG&amp;E&#39;s rate is 5&nbsp;cents per kWh &mdash; measured</p>"
    (b,) = prose_blocks.extract(html)
    assert b.text == "SDG&E's rate is 5 cents per kWh — measured", b.text
    return "entities count as their decoded character, not their source spelling"


@case
def case_line_numbers_are_the_opening_tags_source_line():
    html = ("<html>\n<body>\n\n<p>first block on line four of the file</p>\n"
            "<ul>\n<li>list item on\nline six spans two lines</li>\n</ul>\n"
            "<h2 id=\"s3\">Heading on line nine has words</h2>\n</body></html>")
    blocks = prose_blocks.extract(html)
    got = [(b.tag, b.line) for b in blocks]
    assert got == [("p", 4), ("li", 6), ("h2", 9)], got
    assert blocks[2].id == "s3" and blocks[2].label == "h2#s3", (blocks[2].id, blocks[2].label)
    return "each block carries the 1-based source line of its opening tag (li: 6, h2: 9)"


@case
def case_blocks_under_four_words_are_ignored():
    html = ("<p>one two three</p><div class=\"big\">4.2 kWh</div>"
            "<p>one two three four</p><h3>Two words</h3>")
    blocks = prose_blocks.extract(html)
    assert [b.text for b in blocks] == ["one two three four"], [b.text for b in blocks]
    return "blocks of fewer than 4 words are dropped; a 4-word block is kept"


@case
def case_unclosed_li_and_p_do_not_merge_into_one_block():
    html = "<ul><li>first item with enough words<li>second item with enough words</ul>"
    blocks = prose_blocks.extract(html)
    assert [b.text for b in blocks] == ["first item with enough words",
                                        "second item with enough words"], [b.text for b in blocks]
    return "an <li> left unclosed is closed by the parent's end tag, not merged with the next"


# --- the omitted </p> -------------------------------------------------------------
P_OMISSION_SPEC = frozenset(
    "address article aside blockquote details div dl fieldset figcaption figure "
    "footer form h1 h2 h3 h4 h5 h6 header hgroup hr main menu nav ol p pre section "
    "table ul".split())


def _shape(html):
    return [(b.tag, b.text, b.chars, b.line) for b in prose_blocks.extract(html)]


@case
def case_closer_list_is_the_html_spec_p_end_tag_omission_list():
    assert prose_blocks.P_IMPLICIT_CLOSERS == P_OMISSION_SPEC, (
        sorted(prose_blocks.P_IMPLICIT_CLOSERS ^ P_OMISSION_SPEC))
    return "P_IMPLICIT_CLOSERS is exactly the spec's 30-tag </p>-omission list"


@case
def case_unclosed_p_ends_at_a_ul():
    html = ("<p>one two three four five<ul><li>six seven eight nine ten</li></ul>"
            "eleven twelve thirteen fourteen fifteen")
    got = _shape(html)
    # Positive control on the counts: with the <p> left open past the <ul>, the
    # five trailing words land in it and the page has a p of TEN words; closed
    # at the <ul>, the p holds five, the li holds five, the trailing words are
    # bare text in no block, and no block holds ten.
    assert got == [("p", "one two three four five", 23, 1),
                   ("li", "six seven eight nine ten", 24, 1)], got
    words = [b.words for b in prose_blocks.extract(html)]
    assert words == [5, 5] and 10 not in words, words
    return "<p>...<ul> with no </p>: the p holds its five words, the li its five, the tail neither"


@case
def case_unclosed_p_ends_at_a_table():
    html = ("<p>one two three four five<table><tr><td>six seven eight nine ten</td></tr>"
            "</table>eleven twelve thirteen fourteen fifteen"
            "<p>sixteen seventeen eighteen nineteen twenty</p>")
    got = _shape(html)
    # Table text is excluded either way; the tell is the bare text after the
    # table, which an unclosed p would absorb into a ten-word block.
    assert got == [("p", "one two three four five", 23, 1),
                   ("p", "sixteen seventeen eighteen nineteen twenty", 42, 1)], got
    assert 10 not in [b.words for b in prose_blocks.extract(html)]
    return "<p>...<table> with no </p>: the p ends at the table and holds five words"


@case
def case_unclosed_p_ends_at_a_section_and_at_details_summary():
    section = "<p>one two three four five<section>six seven eight nine ten</section>"
    assert _shape(section) == [("p", "one two three four five", 23, 1)], _shape(section)
    details = ("<p>one two three four five<details><summary>six seven eight nine ten</summary>"
               "eleven twelve thirteen fourteen fifteen</details>")
    assert _shape(details) == [("p", "one two three four five", 23, 1),
                               ("summary", "six seven eight nine ten", 24, 1)], _shape(details)
    return "<section> and <details><summary> end an unclosed <p>; their bare text is not its text"


@case
def case_explicit_and_implicit_p_close_measure_identically():
    body = "one two three four five<ul><li>six seven eight nine ten</li></ul>\n<p>tail words here now</p>"
    explicit = prose_blocks.extract("<p>" + body.replace("<ul>", "</p><ul>", 1))
    implicit = prose_blocks.extract("<p>" + body)
    key = lambda bs: [(b.tag, b.text, b.chars, b.words, b.line) for b in bs]  # noqa: E731
    assert key(explicit) == key(implicit), (key(explicit), key(implicit))
    assert len(key(explicit)) == 3, key(explicit)
    return "<p>x</p><ul> and <p>x<ul> yield the same blocks, chars, words and lines"


@case
def case_inline_tags_do_not_close_an_open_p():
    html = ("<p>one <b>two</b> three <a href=\"#s1\">four</a> five <span>six</span> seven"
            "<ul><li>eight nine ten eleven twelve</li></ul>")
    got = _shape(html)
    assert got == [("p", "one two three four five six seven", 33, 1),
                   ("li", "eight nine ten eleven twelve", 28, 1)], got
    return "<b>, <a> and <span> inside a <p> keep it open; the <ul> after them closes it"


# --- the cap --------------------------------------------------------------------
@case
def case_over_limit_positive_control_on_both_sides_of_800():
    word = "words "
    over = (word * 134)[:801]      # 801 characters of visible text
    exact = (word * 134)[:800]     # 800 characters, not over
    assert len(over) == 801 and len(exact) == 800
    html = f"<p id=\"a\">{over}</p><p id=\"b\">{exact}</p>"
    blocks = prose_blocks.extract(html)
    assert [b.chars for b in blocks] == [801, 800], [b.chars for b in blocks]
    listed = prose_blocks.over_limit(blocks, 800)
    assert [b.id for b in listed] == ["a"], [b.id for b in listed]
    return "over_limit lists the 801-char paragraph and not the 800-char one"


# --- tiers ----------------------------------------------------------------------
@case
def case_tier_split_at_the_advanced_details_line():
    html = ("<p>basic tier paragraph number one</p>\n"
            "<p>basic tier paragraph number two</p>\n"
            "<details id=\"advanced\" class=\"advanced\"><summary>The full evidence lives here</summary>\n"
            "<p>advanced tier paragraph number one</p>\n"
            "</details>")
    blocks = prose_blocks.extract(html)
    split = prose_blocks.advanced_offset(html)
    # An OFFSET now, not a line: the marker opens at character 78, right after
    # the two basic paragraphs. Comparing lines mis-tiered a page whose markup
    # shares a line (PR #262 pass 2); the same-line case below proves it.
    assert split == html.index('<details id="advanced"'), (split, html[:90])
    assert prose_blocks.advanced_line(html) == 3, prose_blocks.advanced_line(html)
    basic = prose_blocks.select_tier(blocks, split, "basic")
    adv = prose_blocks.select_tier(blocks, split, "advanced")
    assert [b.line for b in basic] == [1, 2], [b.line for b in basic]
    assert [(b.tag, b.line) for b in adv] == [("summary", 3), ("p", 4)], adv
    assert len(prose_blocks.select_tier(blocks, split, "all")) == 4
    assert prose_blocks.advanced_line("<p>no advanced tier on this page</p>") is None
    assert prose_blocks.select_tier(blocks, None, "advanced") == []
    assert len(prose_blocks.select_tier(blocks, None, "basic")) == 4
    return "basic = blocks before the <details id=\"advanced\"> line, advanced = from it on"


# --- the CLI --------------------------------------------------------------------
@case
def case_cli_offender_exits_1_and_names_the_block():
    long = ("word " * 200).strip()   # 999 chars
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "page.html"
        page.write_text(f"<p>short clean paragraph here</p>\n<p class=\"small\">{long}</p>\n",
                        encoding="utf-8")
        r = _run("--max-chars", "800", str(page))
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    lines = r.stdout.splitlines()
    assert lines[0] == "PROSE BLOCKS 2 blocks, 204 words, 1 over 800", lines[0]
    assert lines[1].startswith("L2 p 999 chars: word word "), lines[1]
    assert lines[1].endswith("…"), lines[1]
    assert len(lines) == 2, lines
    return "an offender: exit 1, summary without OK, one `L<line> <tag> <chars> chars:` line"


@case
def case_cli_clean_page_exits_0_with_ok():
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "page.html"
        page.write_text("<p>a clean paragraph under the cap</p>\n", encoding="utf-8")
        r = _run("--max-chars", "800", str(page))
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout == "PROSE BLOCKS OK 1 blocks, 6 words, 0 over 800\n", r.stdout
        # No cap: summary only, exit 0, even though the page would fail a tiny cap.
        r2 = _run(str(page))
        assert r2.returncode == 0 and r2.stdout == "PROSE BLOCKS 1 blocks, 6 words\n", r2.stdout
        # --list without a cap lists every block.
        r3 = _run("--list", str(page))
        assert r3.stdout.splitlines()[1] == "L1 p 31 chars: a clean paragraph under the cap", r3.stdout
        # --tier is reported on the summary line.
        r4 = _run("--max-chars", "800", "--tier", "advanced", str(page))
        assert r4.returncode == 0 and r4.stdout == (
            "PROSE BLOCKS OK 0 blocks, 0 words, 0 over 800 (advanced tier)\n"), r4.stdout
    return "clean page: `PROSE BLOCKS OK ...` and exit 0; no cap: summary only; tiers labelled"


@case
def case_cli_missing_file_exits_2():
    with tempfile.TemporaryDirectory() as td:
        r = _run("--max-chars", "800", str(pathlib.Path(td) / "absent.html"))
    assert r.returncode == 2 and r.stdout.startswith("PROSE BLOCKS ERROR"), (r.returncode, r.stdout)
    return "a missing page is an error (exit 2), not a clean run"


# --- the committed page ----------------------------------------------------------
@case
def case_committed_index_html_extracts_at_least_200_blocks():
    page = ROOT / "index.html"
    blocks, over = prose_blocks.report(page)
    assert len(blocks) >= 200, len(blocks)
    assert over == [], over
    html_text = page.read_text(encoding="utf-8")
    split = prose_blocks.advanced_offset(html_text)
    assert split is not None, "index.html has no <details id=\"advanced\">"
    basic = prose_blocks.select_tier(blocks, split, "basic")
    adv = prose_blocks.select_tier(blocks, split, "advanced")
    assert basic and adv, (len(basic), len(adv))
    assert all(b.words >= prose_blocks.MIN_WORDS for b in blocks)
    r = _run(str(page))
    assert r.returncode == 0 and r.stdout.startswith(f"PROSE BLOCKS {len(blocks)} blocks, "), r.stdout
    return (f"index.html: {len(blocks)} blocks ({len(basic)} basic, {len(adv)} advanced), "
            f"{sum(b.words for b in blocks)} words; the 800-char cap is enforced "
            "by analysis/test_prose_rhythm.py (issue #256)")


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran = 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            # Stopping is this runner's choice; going quiet is not.
            print(f"\n{ran}/{len(CASES)} ran before this failure stopped the run")
            raise SystemExit(1)
    print(f"\n{ran}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
