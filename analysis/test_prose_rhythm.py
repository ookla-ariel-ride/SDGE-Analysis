#!/usr/bin/env python3
"""Tests for prose_rhythm.py (issue #256): the four rhythm metrics on small
synthetic pages with a positive control on both sides of every threshold, the
heading exclusion, the acronym allowlist, the tier split, the CLI's exit codes,
the committed index.html clearing every limit in both tiers (this is the gate
issue #256 asks for), and a seeded-defect case per metric proving the gate
fails when the habit comes back.

Run from the repo root:  ./.venv/bin/python analysis/test_prose_rhythm.py
"""
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import prose_blocks  # noqa: E402
import prose_lint  # noqa: E402
import prose_rhythm  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "analysis" / "prose_rhythm.py"
PAGE = ROOT / "index.html"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# --- helpers ---------------------------------------------------------------
def _filler(words):
    """A page of plain paragraphs holding exactly `words` words and no metric hit.

    Paragraphs run 10 words (59 characters), the last one longer when `words`
    is not a multiple of 10, so no block approaches the 800-character cap and
    no metric other than the one under test can fire.
    """
    assert words >= 10, words
    para = ["alpha"] * 10
    out = ["<p>" + " ".join(para) + "</p>"] * (words // 10 - 1)
    out.append("<p>" + " ".join(["alpha"] * (10 + words % 10)) + "</p>")
    return "\n".join(out)


def _page_with(snippet, total_words):
    """`snippet` padded with filler to exactly `total_words` words of prose.

    An em dash between spaces is its own whitespace-separated token, so a
    snippet's word count is measured rather than counted by eye.
    """
    used = prose_rhythm.measure(snippet)["words"]
    page = snippet + "\n" + _filler(total_words - used)
    assert prose_rhythm.measure(page)["words"] == total_words, prose_rhythm.measure(page)
    return page


def _kinds(violations):
    """The metric named by each violation, e.g. {'em dashes', 'intensifiers'}."""
    out = set()
    for v in violations:
        body = v.split(": ", 1)[1]
        for name in ("em dashes", "'X, not Y' tails", "ALL-CAPS emphasis",
                     "intensifier", "block(s) over"):
            if name in body or name in v:
                out.add(name)
    return out


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd,
                          capture_output=True, text=True)


def _seed_into_basic(html, snippet):
    """`html` with `snippet` spliced in just above the advanced-tier boundary.

    The snippet lands in the BASIC tier, so a seeded defect shows up on the
    tier whose prose the reader meets first.
    """
    split = prose_blocks.advanced_line(html)
    assert split is not None, "the committed page must have a <details id=\"advanced\">"
    lines = html.split("\n")
    lines.insert(split - 1, snippet)
    return "\n".join(lines)


# --- negative control -------------------------------------------------------
@case
def case_plain_prose_page_has_no_violations():
    html = _filler(1000)
    m = prose_rhythm.measure(html)
    assert (m["words"], m["em_dashes"], m["tails"]) == (1000, 0, 0), m
    assert m["caps"] == [] and m["intensifiers"] == [] and m["long_blocks"] == [], m
    assert prose_rhythm.check(html) == [], prose_rhythm.check(html)
    return "1,000 words of plain paragraphs measure 0 on every metric and pass every limit"


# --- 1. em dashes -----------------------------------------------------------
@case
def case_em_dash_rate_exactly_at_the_limit_passes_and_one_over_fails():
    three = "<p>alpha — beta — gamma — delta epsilon zeta eta</p>"
    at_limit = _page_with(three, 1000)
    m = prose_rhythm.measure(at_limit)
    assert m["em_dashes"] == 3 and m["em_per_1k"] == 3.0, m
    assert prose_rhythm.check(at_limit) == [], prose_rhythm.check(at_limit)
    # One word fewer, same three dashes: 3.003/1k, over a ceiling compared with >.
    one_word_short = _page_with(three, 999)
    v_short = prose_rhythm.check(one_word_short)
    assert _kinds(v_short) == {"em dashes"}, v_short
    over = _page_with("<p>alpha — beta — gamma — delta — epsilon zeta eta</p>", 1000)
    mo = prose_rhythm.measure(over)
    assert mo["em_dashes"] == 4 and mo["em_per_1k"] == 4.0, mo
    v = prose_rhythm.check(over)
    assert _kinds(v) == {"em dashes"}, v
    return ("3 em dashes in 1,000 words passes at exactly 3.0/1k; the same 3 in 999 words "
            "(3.0/1k rounded, 3.003 exact) and a 4th in 1,000 both fail")


@case
def case_double_hyphen_substitute_counts_but_a_compound_or_range_does_not():
    substitute = "<p>alpha -- beta and gamma delta epsilon zeta eta theta</p>\n" + _filler(990)
    assert prose_rhythm.measure(substitute)["em_dashes"] == 1, prose_rhythm.measure(substitute)
    inert = ("<p>on-peak super-off-peak 2024-2025 EV-TOU-5 alpha beta gamma delta</p>\n"
             + _filler(990))
    assert prose_rhythm.measure(inert)["em_dashes"] == 0, prose_rhythm.measure(inert)
    return "' -- ' counts as an em dash; hyphenated compounds and numeric ranges do not"


# --- 2. "X, not Y" tails ----------------------------------------------------
@case
def case_tail_rate_exactly_at_the_limit_passes_and_one_over_fails():
    tail = "<p>the meter reads load, not export, and charge, not heat, at dusk, not noon</p>"
    at_limit = _page_with(tail, 2000)
    m = prose_rhythm.measure(at_limit)
    assert m["tails"] == 3 and m["tails_per_1k"] == 1.5, m
    assert prose_rhythm.check(at_limit) == [], prose_rhythm.check(at_limit)
    over = _page_with(
        tail + "\n<p>it charges from the array rather than the grid on clear days</p>", 2000)
    mo = prose_rhythm.measure(over)
    assert mo["tails"] == 4 and mo["tails_per_1k"] == 2.0, mo
    v = prose_rhythm.check(over)
    assert _kinds(v) == {"'X, not Y' tails"}, v
    return "3 tails in 2,000 words passes at exactly 1.5/1k; a 4th ('rather than') fails"


# --- 3. ALL-CAPS emphasis ---------------------------------------------------
@case
def case_allowlisted_acronym_does_not_count_and_a_shouted_word_does():
    clean = "<p>the CAISO and CPUC figures under NEM and the SGIP rebate apply</p>" + _filler(990)
    assert prose_rhythm.measure(clean)["caps"] == [], prose_rhythm.measure(clean)
    shouted = "<p>the battery NEVER exports and the meter ALWAYS records it</p>" + _filler(990)
    m = prose_rhythm.measure(shouted)
    assert sorted(m["caps"]) == ["ALWAYS", "NEVER"], m
    v = prose_rhythm.check(shouted)
    assert _kinds(v) == {"ALL-CAPS emphasis"}, v
    assert "ALWAYS, NEVER" in v[0], v
    return "CAISO/CPUC/NEM/SGIP pass as acronyms; NEVER and ALWAYS are reported as emphasis"


@case
def case_caps_inside_a_longer_token_and_two_letter_runs_are_not_emphasis():
    html = "<p>the EV-TOU-5 plan and SDG&amp;E bill and kWh/HDD ratio and AC load</p>" + _filler(990)
    m = prose_rhythm.measure(html)
    assert m["caps"] == [], m
    return "TOU inside EV-TOU-5, SDG in SDG&E, HDD after a slash and the 2-letter AC are not emphasis"


@case
def case_every_acronym_on_the_committed_page_is_allowlisted():
    html = PAGE.read_text(encoding="utf-8")
    m = prose_rhythm.measure(html)
    assert m["caps"] == [], m["caps"]
    text = "\n".join(b.text for b in prose_blocks.extract(html)
                     if b.tag not in prose_rhythm.HEADING_TAGS)
    used = {w for w in prose_rhythm.CAPS_RE.findall(text)}
    assert used <= prose_rhythm.ACRONYMS, sorted(used - prose_rhythm.ACRONYMS)
    assert len(used) >= 50, sorted(used)
    return (f"the page's running prose uses {len(used)} distinct all-caps tokens and every one "
            f"is in ACRONYMS")


# --- 4. intensifiers --------------------------------------------------------
@case
def case_each_intensifier_is_flagged_and_the_word_list_is_prose_lints():
    assert prose_rhythm.INTENSIFIER_RE.pattern.count("|") == len(prose_lint.INTENSIFIERS) - 1
    for word in sorted(prose_lint.INTENSIFIERS):
        html = f"<p>the {word} reading of the meter record is what follows here</p>" + _filler(990)
        m = prose_rhythm.measure(html)
        assert [w.lower() for w in m["intensifiers"]] == [word], (word, m["intensifiers"])
        assert _kinds(prose_rhythm.check(html)) == {"intensifier"}, (word, prose_rhythm.check(html))
    upper = "<p>the Honest verdict is that the array underperforms in June rain</p>" + _filler(990)
    assert [w.lower() for w in prose_rhythm.measure(upper)["intensifiers"]] == ["honest"]
    inert = "<p>the robustness of the estimate is reported in the appendix below</p>" + _filler(990)
    assert prose_rhythm.measure(inert)["intensifiers"] == [], prose_rhythm.measure(inert)
    return (f"all {len(prose_lint.INTENSIFIERS)} words of prose_lint.INTENSIFIERS are flagged, "
            f"case-insensitively; 'robustness' is not")


@case
def case_prose_lint_flags_the_same_intensifiers_on_a_fragment():
    v = prose_lint.lint("The honest verdict is that the array underperforms.")
    assert any("intensifier" in x for x in v), v
    assert prose_lint.lint("The verdict is that the array underperforms.") == []
    return "prose_lint gates the same word list on a generated fragment, so the two cannot drift"


# --- headings ---------------------------------------------------------------
@case
def case_headings_are_excluded_from_the_measured_text():
    dashes = "alpha — beta — gamma — delta — epsilon — zeta"
    in_heading = f"<h2>{dashes}</h2>\n<h3>{dashes}</h3>\n" + _filler(1000)
    assert prose_rhythm.measure(in_heading)["em_dashes"] == 0, prose_rhythm.measure(in_heading)
    in_prose = f"<p>{dashes}</p>\n<p>{dashes}</p>\n" + _filler(1000)
    assert prose_rhythm.measure(in_prose)["em_dashes"] == 10, prose_rhythm.measure(in_prose)
    assert prose_rhythm.measure(in_heading)["words"] == 1000
    return "the same 5 em dashes count 0 in an h2/h3 heading verdict and 10 in two paragraphs"


# --- tiers ------------------------------------------------------------------
@case
def case_a_defect_in_one_tier_does_not_fail_the_other():
    shout = "<p>the battery NEVER exports on a cloudy winter afternoon here</p>"
    html = (_filler(1000) + "\n<details id=\"advanced\" class=\"advanced\">\n"
            + shout + "\n" + _filler(1000) + "\n</details>")
    assert prose_rhythm.check(html, "basic") == [], prose_rhythm.check(html, "basic")
    adv = prose_rhythm.check(html, "advanced")
    assert _kinds(adv) == {"ALL-CAPS emphasis"} and adv[0].startswith("advanced tier:"), adv
    assert _kinds(prose_rhythm.check(html, "all")) == {"ALL-CAPS emphasis"}
    assert prose_rhythm.measure(html, "basic")["words"] == 1000
    assert prose_rhythm.measure(html, "advanced")["words"] == 1010
    return "a shouted word seeded in the advanced tier fails advanced and all, and not basic"


# --- the 800-character cap carried over from issue #255 ---------------------
@case
def case_block_cap_positive_control_on_both_sides_of_800():
    exact = ("words " * 134)[:800]
    over = ("words " * 134)[:801]
    assert len(exact) == 800 and len(over) == 801
    ok = f"<p>{exact}</p>"
    assert prose_rhythm.check(ok) == [], prose_rhythm.check(ok)
    bad = f"<p>{over}</p>"
    v = prose_rhythm.check(bad)
    assert _kinds(v) == {"block(s) over"}, v
    assert "801 chars" in v[0], v
    assert prose_rhythm.check(bad, limits={"max_block_chars": 900}) == []
    return "an 800-character block passes, an 801-character block fails, and limits= overrides it"


@case
def case_check_rejects_an_unknown_limit_and_an_unknown_tier():
    try:
        prose_rhythm.check(_filler(10), limits={"em_per_1k": 3, "nonsense": 1})
    except ValueError as e:
        assert "nonsense" in str(e), e
    else:
        raise AssertionError("an unknown limit key must raise")
    try:
        prose_rhythm.measure(_filler(10), tier="middle")
    except ValueError as e:
        assert "middle" in str(e), e
    else:
        raise AssertionError("an unknown tier must raise")
    return "check() refuses an unknown limit key and measure() refuses an unknown tier"


# --- the gate: the committed page -------------------------------------------
@case
def case_committed_index_html_clears_every_limit_in_both_tiers():
    html = PAGE.read_text(encoding="utf-8")
    numbers = []
    for tier in ("basic", "advanced", "all"):
        v = prose_rhythm.check(html, tier)
        assert v == [], v
        m = prose_rhythm.measure(html, tier)
        assert m["words"] > 1000, m
        numbers.append(f"{tier} {m['words']}w em {m['em_per_1k']:.1f}/1k "
                       f"tails {m['tails_per_1k']:.1f}/1k caps {len(m['caps'])} "
                       f"intens {len(m['intensifiers'])} long {len(m['long_blocks'])}")
    return "the committed index.html passes every threshold: " + "; ".join(numbers)


# --- seeded defects: one per metric, injected into the committed page --------
@case
def case_seeded_em_dash_run_fails_the_committed_page():
    html = PAGE.read_text(encoding="utf-8")
    line = "<p>alpha — beta — gamma — delta — epsilon — zeta — eta</p>"
    seeded = _seed_into_basic(html, "\n".join([line] * 5))
    m = prose_rhythm.measure(seeded, "basic")
    assert m["em_dashes"] == 31 and m["em_per_1k"] > 3.0, m
    v = prose_rhythm.check(seeded, "basic")
    assert _kinds(v) == {"em dashes"}, v
    assert m["em_sites"] and m["em_sites"][0].startswith("L"), m["em_sites"][:1]
    return ("seeded 30 extra em dashes into the basic tier (1 -> 31, 0.1 -> "
            f"{m['em_per_1k']:.1f}/1k): the em-dash rule fires and nothing else does")


@case
def case_seeded_x_not_y_tails_fail_the_committed_page():
    html = PAGE.read_text(encoding="utf-8")
    line = ("<p>the meter reads load, not export, and stores charge, not heat, "
            "and bills demand, not energy</p>")
    seeded = _seed_into_basic(html, "\n".join([line] * 5))
    m = prose_rhythm.measure(seeded, "basic")
    assert m["tails"] == 16 and m["tails_per_1k"] > 1.5, m
    v = prose_rhythm.check(seeded, "basic")
    assert _kinds(v) == {"'X, not Y' tails"}, v
    return ("seeded 15 extra 'X, not Y' tails into the basic tier (1 -> 16, 0.1 -> "
            f"{m['tails_per_1k']:.1f}/1k): the tail rule fires and nothing else does")


@case
def case_seeded_all_caps_emphasis_fails_the_committed_page():
    html = PAGE.read_text(encoding="utf-8")
    seeded = _seed_into_basic(
        html, "<p>the battery NEVER exports on a cloudy winter afternoon here</p>")
    m = prose_rhythm.measure(seeded, "basic")
    assert m["caps"] == ["NEVER"], m["caps"]
    v = prose_rhythm.check(seeded, "basic")
    assert _kinds(v) == {"ALL-CAPS emphasis"}, v
    return ("seeded one non-acronym ALL-CAPS word (NEVER) into the basic tier: the allowlist "
            "does not cover it and the ALL-CAPS rule fires alone")


@case
def case_seeded_intensifier_fails_the_committed_page():
    html = PAGE.read_text(encoding="utf-8")
    seeded = _seed_into_basic(
        html, "<p>the honest verdict is that the array underperforms in June</p>")
    m = prose_rhythm.measure(seeded, "basic")
    assert [w.lower() for w in m["intensifiers"]] == ["honest"], m["intensifiers"]
    v = prose_rhythm.check(seeded, "basic")
    assert _kinds(v) == {"intensifier"}, v
    return ("seeded the intensifier 'the honest verdict' into the basic tier: the intensifier "
            "rule fires alone")


@case
def case_seeded_over_cap_paragraph_fails_the_committed_page():
    html = PAGE.read_text(encoding="utf-8")
    long_text = ("word " * 200).strip()   # 999 visible characters
    seeded = _seed_into_basic(html, f"<p>{long_text}</p>")
    v = prose_rhythm.check(seeded, "basic")
    assert _kinds(v) == {"block(s) over"}, v
    assert "999 chars" in v[0], v
    return ("seeded a 999-character paragraph into the basic tier: issue #255's 800-character "
            "cap fires alone")


# --- the CLI ----------------------------------------------------------------
@case
def case_cli_on_the_committed_page_reports_three_tiers_and_exits_0():
    r = _run("--strict", str(PAGE))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    lines = r.stdout.splitlines()
    assert len(lines) == 4, lines
    assert [l.split()[2] for l in lines[:3]] == ["tier=basic", "tier=advanced", "tier=all"], lines
    assert lines[-1] == "PROSE RHYTHM OK", lines
    r2 = _run("--tier", "basic", str(PAGE))
    assert r2.returncode == 0 and len(r2.stdout.splitlines()) == 2, r2.stdout
    assert "tier=basic" in r2.stdout and "max 3.0" in r2.stdout, r2.stdout
    return "--strict on index.html prints basic/advanced/all + `PROSE RHYTHM OK` and exits 0"


@case
def case_cli_strict_exits_1_and_names_the_offenders():
    page_text = ("<p>the battery NEVER exports and the honest verdict says so</p>\n"
                 + _filler(1000))
    with tempfile.TemporaryDirectory() as td:
        page = pathlib.Path(td) / "page.html"
        page.write_text(page_text, encoding="utf-8")
        strict = _run("--strict", "--tier", "basic", str(page))
        loose = _run("--tier", "basic", str(page))
    assert strict.returncode == 1, (strict.returncode, strict.stdout)
    assert loose.returncode == 0, (loose.returncode, loose.stdout)
    assert strict.stdout == loose.stdout, (strict.stdout, loose.stdout)
    assert "caps=1 intensifiers=1" in strict.stdout, strict.stdout
    assert "  ALL-CAPS emphasis:\n    L1 NEVER" in strict.stdout, strict.stdout
    assert "  intensifiers:\n    L1 honest" in strict.stdout, strict.stdout
    assert "PROSE RHYTHM FAIL: 2 violation(s)" in strict.stdout, strict.stdout
    return "--strict exits 1 and lists `L<line> <word>` per offender; without it the same report exits 0"


@case
def case_cli_missing_file_exits_2():
    with tempfile.TemporaryDirectory() as td:
        r = _run(str(pathlib.Path(td) / "absent.html"))
    assert r.returncode == 2, (r.returncode, r.stdout)
    assert r.stdout.startswith("PROSE RHYTHM ERROR: no such file"), r.stdout
    return "a missing page exits 2 with `PROSE RHYTHM ERROR`, distinct from a violation's 1"


@case
def case_cli_defaults_to_the_repo_index_html():
    r = _run("--tier", "advanced", cwd=str(ROOT / "analysis"))
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    expected = prose_rhythm.summary_line(
        prose_rhythm.measure(PAGE.read_text(encoding="utf-8"), "advanced"))
    assert r.stdout.splitlines()[0] == expected, (r.stdout, expected)
    return "with no PATH the CLI walks up to the repo root and measures its index.html"


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
