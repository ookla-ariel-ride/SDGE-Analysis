#!/usr/bin/env python3
"""Tests for prose_lint.py (issue #39 part 5): one case per banned
construction, plus a couple of clean-text negative controls so the linter
isn't just permanently tripping on everything.

Run from the repo root:  ./.venv/bin/python analysis/test_prose_lint.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import prose_lint  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def case_clean_technical_prose_passes():
    text = ("The battery serves every on-peak and off-peak import above its stored-energy "
           "cost, leaving super-off-peak imports alone.")
    assert prose_lint.lint(text) == [], prose_lint.lint(text)
    return "ordinary technical prose with a legitimate three-item list produces no violations"


@case
def case_clean_prose_with_a_domain_triad_is_not_flagged_as_rule_of_three():
    text = "Imports split across on-peak, off-peak, and super-off-peak windows."
    assert prose_lint.lint(text) == [], prose_lint.lint(text)
    return "a legitimate domain-vocabulary triad (TOU periods) is not flagged"


# --- 1. process-narrative bans (CLAUDE.md section 9's literal list) --------
@case
def case_flags_below_the_earlier_estimate():
    text = "This is 12% below the earlier 28-day estimate."
    v = prose_lint.lint(text)
    assert any("below the earlier" in x for x in v), v
    return "'below the earlier ... estimate' is flagged"


@case
def case_flags_carried_from_the_retired_workpaper():
    text = "This figure was carried from the retired soiling workpaper."
    v = prose_lint.lint(text)
    assert any("carried from the retired" in x for x in v), v
    return "'carried from the retired ... workpaper' is flagged"


@case
def case_flags_supersedes_the_previous():
    text = "This finding supersedes the previous estimate."
    v = prose_lint.lint(text)
    assert any("supersedes the previous" in x for x in v), v
    return "'supersedes the previous ...' is flagged"


@case
def case_flags_originally_we():
    text = "Originally we assumed a different climate zone."
    v = prose_lint.lint(text)
    assert any("originally we" in x for x in v), v
    return "'originally we ...' is flagged"


@case
def case_flags_this_replaces():
    text = "This replaces the earlier battery figure."
    v = prose_lint.lint(text)
    assert any("this replaces" in x for x in v), v
    return "'this replaces ...' is flagged"


@case
def case_flags_kept_for_reference():
    text = "The legacy cross-plan ranking is kept for reference."
    v = prose_lint.lint(text)
    assert any("kept for reference" in x for x in v), v
    return "'... is kept for reference' is flagged"


# --- 2. negative parallelism -----------------------------------------------
@case
def case_flags_negative_parallelism():
    text = "The battery is not just a savings tool, but a genuine backup asset."
    v = prose_lint.lint(text)
    assert any("negative parallelism" in x for x in v), v
    return "'not just X, but Y' negative parallelism is flagged"


# --- 3. filler transitions --------------------------------------------------
@case
def case_flags_filler_transition_it_is_worth_noting():
    text = "It is worth noting that the battery pays back within its warranty term."
    v = prose_lint.lint(text)
    assert any("filler transition" in x for x in v), v
    return "'it is worth noting that' filler transition is flagged"


@case
def case_flags_filler_transition_at_the_end_of_the_day():
    text = "At the end of the day, the plan comparison is clear."
    v = prose_lint.lint(text)
    assert any("filler transition" in x for x in v), v
    return "'at the end of the day' filler transition is flagged"


# --- 4. promotional adjectives ----------------------------------------------
@case
def case_flags_promotional_adjective_cutting_edge():
    text = "This cutting-edge battery chemistry improves round-trip efficiency."
    v = prose_lint.lint(text)
    assert any("promotional adjective" in x for x in v), v
    return "'cutting-edge' promotional adjective is flagged"


@case
def case_flags_promotional_adjective_seamless():
    text = "The dispatch policy seamlessly shifts load into the cheap window."
    v = prose_lint.lint(text)
    assert any("promotional adjective" in x for x in v), v
    return "'seamlessly' promotional adjective is flagged"


@case
def case_flags_promotional_verb_leverage():
    text = "Leveraging the battery's stored energy avoids on-peak imports."
    v = prose_lint.lint(text)
    assert any("promotional adjective" in x for x in v), v
    return "'leveraging' promotional-register verb is flagged"


# --- 5. rule-of-three padding ------------------------------------------------
@case
def case_flags_rule_of_three_padding():
    text = "The new dispatch policy is fast, reliable, and efficient."
    v = prose_lint.lint(text)
    assert any("rule-of-three padding" in x for x in v), v
    return "a triad of generic quality adjectives is flagged as rule-of-three padding"


@case
def case_does_not_flag_a_triad_with_only_one_padding_adjective():
    text = "The array is compact, quiet, and mounted on the south roof."
    v = prose_lint.lint(text)
    assert not any("rule-of-three padding" in x for x in v), v
    return "a triad with at most one generic quality adjective is not flagged"


@case
def case_a_fragment_can_trip_more_than_one_rule_at_once():
    text = ("It is worth noting that this cutting-edge battery is not just efficient, "
           "but also reliable.")
    v = prose_lint.lint(text)
    kinds = {x.split(":")[0] for x in v}
    assert len(kinds) >= 2, v
    return f"a single fragment tripping multiple rules reports all of them: {sorted(kinds)}"


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
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    print(f"\n{ran}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
