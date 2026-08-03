#!/usr/bin/env python3
"""Guards for the year-over-year bill decomposition in bill_decomposition.py.

MOST OF THIS SUITE RUNS IN A CLEAN CHECKOUT. The generator needs the bill PDFs —
the charged CEA per-TOU rates and the billing-mode sentences exist nowhere else —
but its output, data/bill_decomposition.json, is committed, so the properties that
matter are checked against the committed artifact and against the two committed
bill artifacts it must agree with. Only the byte-for-byte regeneration case needs
the private archive, and it says so instead of passing quietly.

WHAT IS BEING GUARDED, and why each case exists:

  A SIGN PRINTED BEFORE THE DOLLAR, NOT AFTER (issue #46). SDG&E prints a negative
  per-kWh rate as "kWh x -$.02828 -28.31", minus before the $, not "kWh x $-.02828".
  Three _LINE_PATTERNS entries required a literal $ right after "x " with no
  tolerance for a sign in front of it, so the line failed to match at all and
  charge_lines()'s caller read the missing key as a silent $0 instead of the real
  (negative) charge. case_charge_lines_accepts_a_minus_sign_printed_before_the_
  dollar_sign proves both sign positions parse to the same magnitude from a
  synthetic fixture; case_the_real_corpus_no_longer_drops_a_negative_pcia_line
  re-scans every real statement and asserts none of them still drop the line.
  12 of the 25 real statements print PCIA in the negative-before-$ form; 3 of
  those 12 ALSO hit an unrelated, pre-existing bug (a mid-cycle rate change
  reprints another line twice within one period with two different values,
  which charge_lines()'s conflict guard correctly refuses, out of this issue's
  scope) and so are skipped, leaving 9 statements this case checks the exact
  recovered value of — see that case's own docstring for the precise count
  breakdown, independently re-derived from the corpus rather than hardcoded.
  Neither of the two statements this module actually compares (the 2024-06-27
  base and 2026-07-02 current) happens to print PCIA in the affected form, so
  data/bill_decomposition.json is unchanged by the fix — verified by
  regenerating it and diffing against the committed copy.

  THE TRAP. The billing-history export reports current_charges of $0.00 for
  2024-06-27, the base period of this comparison, and for every statement through
  2025-04-02. A decomposition built on that column would compare $0.00 against
  $398.56 and call the difference real. Two cases hold the answer: the mode
  question is answered from statement text with the statements named
  (case_the_billing_mode_question_is_answered_from_statement_text), and every one
  of the export's numbers is explained in terms of the statement's own lines
  (case_the_export_is_reconciled_for_every_statement) — including that the step
  off $0.00 lands on the presentation change and not on any change in billing.

  AND THAT ANSWER IS PROVED PER STATEMENT, NOT ASSUMED FROM A FLAG. "Payment
  Required This Month" is a yes/no field; a wording change or a real billing-mode
  change could flip it while the artifact went on asserting uninterrupted accrual.
  So every "No" has to carry a recognised true-up deferral sentence and every
  "Yes" has to prove it is the annual settlement — by saying so and by having a
  billing period that ends on the true-up date the earlier statements printed.
  case_an_accruing_statement_needs_its_deferral_sentence and
  case_a_payable_statement_must_prove_it_is_the_annual_settlement drive both
  refusals from synthetic statement text, and
  case_the_billing_mode_counts_come_from_the_validated_rows asserts the published
  counts and prose are derived from the scan rather than written in.

  A SETTLEMENT $0 IS NOT A PRICE, AND THE TYPE ENFORCES IT. Under NEM 2.0 a TOU
  bucket billed as a net export prints "Rate/kWh $.00000" and is charged $0
  because the export settled at the annual true-up. Coercing that to 0.0 and
  multiplying reconciles perfectly while measuring the settlement change and
  printing it as a price — the same defect surfaced three times in this module, at
  the per-cell split, at the headline provider figure, and inside the published
  price and quantity bounds. Convention did not stop it, so the representation
  does: such a cell carries a Settlement, and every arithmetic operator on it
  raises. case_a_settlement_non_price_refuses_every_arithmetic_use holds the type
  to that, operator by operator, including the `rate or 0.0` idiom.

  WHICH MEANS THE INDEX FIGURES COVER ONLY THE CELLS THAT SUPPORT THEM.
  case_the_published_index_covers_only_the_priced_cells asserts the price effect,
  the quantity effect, the interaction, the bounds, the scale/mix split and the
  vintage/provider split are all computed over the cells billed as net imports in
  BOTH periods, and nowhere else; case_the_energy_change_is_priced_cells_plus_
  settlement asserts each flipped cell's COMPLETE dollar change is carried as its
  own top-level component beside them rather than inside either;
  case_a_flipped_cell_is_outside_every_index_term drives the same rule from a
  synthetic pair; and case_the_quantity_split_prices_no_kwh_at_a_settlement_zero
  asserts the scale/mix split now values every kWh at an observed tariff.

  A POINT IS NOT A BOUND. Paasche price == Laspeyres price + interaction, so
  pairing Paasche price with Laspeyres quantity and calling the price figure "the"
  price effect hands the whole interaction to price while the note beside it says
  the interaction is not allocated. case_the_published_reading_allocates_none_of_
  the_interaction asserts the artifact publishes intervals whose width is exactly
  the interaction, publishes both exact pairings, publishes no bare price_usd or
  quantity_usd, and that index.html carries the bounds and no point attribution.

  ONE FIGURE CANNOT CARRY TWO SCOPES. SDG&E's printed bundled table prices only
  the cells billed as net imports, while the CCA-only riders and CEA's product
  adders are charged once per period on the period's own kWh. Adding whole-period
  riders to a cell-restricted CEA total and calling the result a comparison "over
  five cells" compares two different quantity scopes.
  case_the_provider_comparison_publishes_two_scopes asserts both readings exist,
  that each states what it covers, that the energy-only one is cell-matched on
  both sides with no riders anywhere in it, and that the report quotes both with
  their scopes; case_a_current_export_cell_cannot_enter_the_provider_comparison
  drives the underlying exclusion synthetically.

  THE CCA AUTHORITY BOUNDARY (issue #2, binding here). On a CCA statement SDG&E
  prints a bundled-generation comparison table beside a bill the CCA charged.
  case_the_printed_bundled_comparison_is_never_priced_as_supply asserts it enters
  the ledger only as a pair that nets to zero, that the supply dollars are the CEA
  page's and differ from it, and that the ONLY place it is used is the same-date
  provider counterfactual. case_a_provider_effect_is_refused_without_a_same_date_
  comparison feeds a cell with no comparison rate and asserts the script refuses
  rather than estimating one.

  THE LIKE-FOR-LIKE INDEX. The TOU mix rotated hard between these two periods
  (on-peak is 5.3% of base kWh and 25.7% of current), so a ratio of blended $/kWh
  reads as a price move when most of it is mix.
  case_the_like_for_like_index_is_fixed_weight recomputes Laspeyres, Paasche and
  Fisher from the per-cell rows and asserts the naive blended ratio is nowhere
  near them — i.e. that the artifact publishes the index and not the trap.

  A TRANSFORMATION OF TWO POINTS IS NOT A RATE. Two matched endpoints with no
  comparable pair between them cannot establish an annual price path, and "per
  year" reads as an observed yearly change.
  case_no_per_year_price_figure_is_published asserts the index publishes total
  changes only, carries the two-endpoint limit in machine-readable form, and that
  the report never restates the Fisher reading as an annual rate.
"""
import copy
import datetime as dt
import json
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bill_decomposition as B


class SkipCase(Exception):
    """Typed skip signal (matching test_parse_bills.py's convention, issue #44
    AC4) -- a case raises this instead of returning a "SKIP ..."-prefixed
    string, so a case that legitimately returns a message starting with those
    five letters can never be silently miscounted as skipped."""


ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "data" / "bill_decomposition.json"
CENT = 0.005


def _artifact():
    assert ARTIFACT.exists(), f"{ARTIFACT} is not committed"
    return json.loads(ARTIFACT.read_text())


def _report():
    return (ROOT / "index.html").read_text()


def _raises(fn, *needles):
    try:
        fn()
    except SystemExit as e:
        msg = str(e)
        for n in needles:
            assert n in msg, f"message {msg!r} does not name {n!r}"
        return msg
    raise AssertionError("expected SystemExit, got a return")


def _cells(spec):
    """A synthetic period ledger in the shape decompose() consumes: one entry per
    (season, TOU period), spec keyed by cell with (kwh, delivery, supply,
    comparison) rates.

    Rates are built through the generator's own constructors, so a cell with no net
    import carries a Settlement exactly as a real statement's export cell does — the
    fixture cannot accidentally hand the code a 0.0 the real data would never have."""
    cells = {}
    for key in B.CELLS:
        q, d, g, s = spec.get(key, (0.0, 0.0, 0.0, 0.0))
        name = B._key(*key)
        delivery, supply = B._c(q * d), B._c(q * g)
        comparison = B._c(q * s)
        cells[key] = {
            "kwh": q,
            "delivery_usd": delivery,
            "supply_usd": supply,
            "usd": B._c(delivery + supply),
            "delivery_rate_effective":
                B._effective_rate(delivery, q, name, "delivery rate"),
            "supply_rate_effective": B._effective_rate(supply, q, name, "supply rate"),
            "rate_effective": B._effective_rate(B._c(delivery + supply), q, name,
                                                "effective billed rate"),
            "sdge_bundled_comparison_rate":
                B._effective_rate(comparison, q, name,
                                  "same-date bundled comparison rate"),
            "sdge_bundled_comparison_usd": B._counterfactual_usd(comparison, q, name),
        }
    return {"cells": cells}


def _statement(payment_required, extra=""):
    """Just enough synthetic statement text for classify_statement()."""
    return ("Net Energy Metering Summary\n"
            f"Payment Required This Month: {payment_required}\n"
            "Total Charges this Month $123.45\n" + extra)


_SETTLEMENT_TEXT = (
    "Net Energy Metering Annual True-Up Bill\n"
    "Your account has been settled and all applicable generation credits have been "
    "applied.\n"
    # No meter/account line: the classifier reads only the settlement sentences
    # and the True-Up Date field, and a synthetic one still trips the PII gate's
    # labelled-account-number rule, which is working as intended.
    "True-Up Date: 12/26/2099 Version: 2.0\n")


def _with_statement_text(txt, fn):
    """Run fn() with B.statement_text patched to return txt for any statement,
    restoring the original afterward regardless of outcome."""
    saved = B.statement_text
    B.statement_text = lambda stmt: txt
    try:
        return fn()
    finally:
        B.statement_text = saved


# ---------------------------------------------------------------------------
# charge_lines() — a minus sign printed BEFORE the dollar sign (issue #46)
# ---------------------------------------------------------------------------
def case_charge_lines_accepts_a_minus_sign_printed_before_the_dollar_sign():
    """SDG&E prints a negative per-kWh rate as 'kWh x -$.02828 -28.31' — the minus
    BEFORE the dollar sign — not 'kWh x $-.02828' (minus after, which _NUM's own
    leading sign already covered). Three _LINE_PATTERNS entries (wildfire_fund_
    charge, pcia, incremental_procurement_cost_adjustment) required a literal '$'
    right after 'x ' with no tolerance for a sign in front of it, so a negative
    rate line failed to match AT ALL rather than parsing to a wrong value, and
    lines.get(name, 0.0) then silently priced the missing charge at zero.

    This is not a hypothetical: 12 of the real 25 statements on file print PCIA in
    exactly this negative-before-$ form (2025-03-04, 04-02, 05-02, 06-03, 07-02,
    08-01, 09-02, 10-01, 10-31, 12-03, 2026-01-06, 02-02), and before this fix
    charge_lines() returned no 'pcia' key at all for every one of them (see the
    reproduction below, run against the real corpus, in
    case_the_real_corpus_no_longer_drops_a_negative_pcia_line — 9 of those 12 are
    checked there for their exact recovered value; the other 3 also hit an
    unrelated, pre-existing bug that makes charge_lines() raise for a different
    reason before it ever gets to return a pcia value, so they are out of this
    issue's scope).

    Each affected line is checked against a synthetic statement chunk in both the
    negative-before-$ and the plain positive form, asserting the same magnitude
    comes out with the sign flipped — not that the key goes missing."""
    positive_txt = (
        "Wildfire Fund Charge 1,000 kWh x $.00595 5.95\n"
        "PCIA 2023 1,000 kWh x $.02828 28.28\n"
        "Incremental Procurement Cost Adjustment 1,000 kWh x $.00006 .06\n"
    )
    negative_txt = (
        "Wildfire Fund Charge 1,000 kWh x -$.00595 -5.95\n"
        "PCIA 2023 1,000 kWh x -$.02828 -28.28\n"
        "Incremental Procurement Cost Adjustment 1,000 kWh x -$.00006 -.06\n"
    )
    pos = _with_statement_text(positive_txt, lambda: B.charge_lines("synthetic"))
    neg = _with_statement_text(negative_txt, lambda: B.charge_lines("synthetic"))
    for name in ("wildfire_fund_charge", "pcia", "incremental_procurement_cost_adjustment"):
        assert name in neg, (
            f"'{name}' went missing on the minus-before-$ form instead of parsing "
            "to a negative value")
        assert neg[name] == -pos[name], (
            f"'{name}': expected {-pos[name]} from the negative-before-$ form "
            f"(the mirror of the positive form's {pos[name]}), got {neg[name]}")
    return ("charge_lines() extracts the wildfire/PCIA/IPA rate lines whether the "
            "minus prints before or after the $")


_NEG_PCIA_LINE = re.compile(
    r"PCIA \d+\s+[\d,]+ kWh x [−-]\$[\d,.]+\s+([−-][\d,.]+)")


def case_the_real_corpus_no_longer_drops_a_negative_pcia_line():
    """Every statement in the corpus, re-scanned. rates.py documents PCIA as
    routinely negative, and SDG&E prints that as a minus sign BEFORE the dollar
    sign ('PCIA 2023 802 kWh x -$.03161 -25.35'). Before this fix, every one of
    these lines failed to match _LINE_PATTERNS' pcia entry at all, so
    charge_lines() came back with no 'pcia' key — a silent $0, not a parse
    failure.

    Ground truth, independently re-derived from the real corpus by this case
    (not asserted from memory — issue #46's own PR first stated this as '9 of
    25', which was actually the narrower, different count of statements where
    charge_lines() *returns a value* rather than the count that *print the
    form*; this case pins both numbers separately so they cannot be conflated
    again): 12 of the 25 statements print PCIA in the negative-before-$ form
    (2025-03-04, 04-02, 05-02, 06-03, 07-02, 08-01, 09-02, 10-01, 10-31, 12-03,
    2026-01-06, 02-02). Of those 12, 3 (2025-03-04, 2025-10-31, 2026-02-02)
    ALSO carry an unrelated, pre-existing bug — a mid-cycle rate change
    reprints 'Wildfire Fund Charge' or 'Non-Bypassable Charges' twice within
    one period with two different values, which charge_lines()'s conflict
    guard correctly refuses — so charge_lines() raises for them before it ever
    reaches the pcia line; fixing that is out of this issue's scope, so those
    3 are skipped. The remaining 9 are asserted on individually: each must
    yield a NEGATIVE 'pcia' value from charge_lines() that matches — not just
    resembles — the actual printed line's own amount, independently re-parsed
    here rather than by re-using bill_decomposition's own pattern, so this
    case cannot pass merely because it shares a regex with the code under
    test. A corpus that quietly rotated to all-positive PCIA would fail the
    12-count assertion below rather than pass silently."""
    if not B.ELEC_DIR.exists():
        return "SKIP needs the private bill PDF archive"
    known_dual_valued = {"2025-03-04", "2025-10-31", "2026-02-02"}
    negative_form_statements = []
    any_pcia_checked = 0
    confirmed_negative = 0
    for stmt in sorted(B.statement_dates()):
        txt = B.statement_text(stmt)
        neg_match = _NEG_PCIA_LINE.search(txt)
        if neg_match:
            negative_form_statements.append(stmt)
        if not re.search(r"PCIA \d+\s+[\d,]+ kWh x", txt) or stmt in known_dual_valued:
            continue
        any_pcia_checked += 1
        lines = B.charge_lines(stmt)
        assert "pcia" in lines, f"{stmt}: 'pcia' line present in the text but missing from charge_lines()"
        assert isinstance(lines["pcia"], float), f"{stmt}: pcia is {type(lines['pcia'])}, not float"
        if neg_match:
            printed = float(neg_match.group(1).replace(",", ""))
            assert lines["pcia"] < 0, (
                f"{stmt}: prints PCIA negative-before-$ but charge_lines() returned "
                f"{lines['pcia']}, not a negative value")
            assert lines["pcia"] == printed, (
                f"{stmt}: charge_lines() returned {lines['pcia']}, but this case's own "
                f"independent re-parse of the printed line gives {printed}")
            confirmed_negative += 1
    assert len(negative_form_statements) == 12, (
        f"expected 12 of 25 statements to print the negative-before-$ PCIA form, "
        f"found {len(negative_form_statements)}: {negative_form_statements}")
    assert confirmed_negative == 9, (
        f"expected 9 of the 12 negative-before-$ statements to be checkable here "
        f"(12 minus the 3 known dual-valued statements out of scope), got "
        f"{confirmed_negative}")
    assert any_pcia_checked == 15, (
        f"expected 15 non-dual-valued PCIA-bearing statements in total (9 "
        f"negative-before-$ + 6 plain positive), got {any_pcia_checked}")
    return (f"{confirmed_negative} of {len(negative_form_statements)} negative-before-$ "
            f"PCIA statements recover their exact printed negative value (the other 3 "
            f"hit an unrelated, pre-existing duplicate-line bug, out of scope); "
            f"{any_pcia_checked} PCIA-bearing statements checked in total")


# ---------------------------------------------------------------------------
# The billing-mode question — the trap, handled before any arithmetic
# ---------------------------------------------------------------------------
def case_the_billing_mode_question_is_answered_from_statement_text():
    a = _artifact()["billing_mode"]["finding"]
    assert a["answer"] == "accrues to the annual true-up", a["answer"]
    assert a["answer_holds_for_both_compared_periods"] is True
    named = {e["statement_date"]: e for e in a["established_by"]}
    for stmt in (B.BASE["statement"], B.CURRENT["statement"]):
        assert stmt in named, f"{stmt} is not named among the establishing statements"
        e = named[stmt]
        assert "true" in e["quote"].lower() and "not required" in e["quote"].lower(), \
            f"{stmt}'s quote does not say the charge is not payable: {e['quote']!r}"
        assert e["net_energy_metering_summary"] == "Payment Required This Month: No"
    settle = {s["statement_date"] for s in a["annual_settlement_statements"]}
    assert settle == {"2024-12-30", "2026-01-06"}, settle
    scan = _artifact()["billing_mode"]["per_statement"]
    payable = {r["statement_date"] for r in scan
               if r["payment_required_this_month"] == "Yes"}
    assert payable == settle, (payable, settle)
    # and the base period's accrual is the cost series, not the $0.00 payment line
    base = next(r for r in scan if r["statement_date"] == B.BASE["statement"])
    assert base["period_accrual_usd"] == 48.25, base
    return (f"billing mode answered from statement text over {len(scan)} statements: "
            f"accrual to the annual true-up, named on {B.BASE['statement']} and "
            f"{B.CURRENT['statement']}, payable only on {sorted(settle)}")


def case_an_accruing_statement_needs_its_deferral_sentence():
    """A "No" in the payment field is not evidence that the energy charge accrued to
    the annual true-up — it is a payment flag. If the sentence that says WHY is not on
    the statement, the wording may have changed or the account may have stopped
    accruing, and this analysis's whole cost series rests on the difference. Refuse."""
    _raises(lambda: B.classify_statement("2099-01-01", _statement("No"),
                                         ["1/1/99 - 1/31/99"], dt.date(2099, 12, 26)),
            "2099-01-01", "neither recognised true-up deferral sentence",
            "will not assume accrual")
    # both recognised wordings are accepted, and each carries its true-up date out
    for extra, date_text in (
            ("*Payment not required for NEM charges. Your account will true up on "
             "Dec 26, 2099", "Dec 26, 2099"),
            ("Payment is not required at this time.\nYour account will true-up on "
             "Dec 26, 2099.", "Dec 26, 2099")):
        row = B.classify_statement("2099-01-01", _statement("No", extra),
                                   ["1/1/99 - 1/31/99"], None)
        assert row["billing_mode"] == "accrues to the annual true-up", row
        assert row["true_up_date"] == date_text, row
        assert row["annual_settlement"] is False, row
    # and the committed scan has one for every accruing statement
    for r in _artifact()["billing_mode"]["per_statement"]:
        if r["payment_required_this_month"] != "No":
            continue
        assert r["true_up_date"], r
        assert "true" in r["establishing_quote"].lower() and \
            "not required" in r["establishing_quote"].lower(), r
    return ("a statement printing 'Payment Required This Month: No' without a "
            "recognised true-up deferral sentence fails closed by name; both printed "
            "wordings are accepted and every accruing statement in the corpus carries "
            "one")


def case_a_payable_statement_must_prove_it_is_the_annual_settlement():
    """A "Yes" was previously labelled an annual settlement on the strength of the flag
    alone. A payable statement that is NOT a true-up settlement means the account
    stopped accruing — the one thing this analysis cannot survive silently — so it has
    to say it is a settlement AND close the true-up the earlier statements named."""
    period = ["11/26/99 - 12/26/99"]
    due = dt.date(2099, 12, 26)
    # says nothing about being a settlement
    _raises(lambda: B.classify_statement("2099-12-30", _statement("Yes"), period, due),
            "2099-12-30", "does not say it is the annual settlement",
            "stopped accruing")
    # says it, but no earlier statement named a true-up date for it to close
    _raises(lambda: B.classify_statement("2099-12-30",
                                         _statement("Yes", _SETTLEMENT_TEXT), period,
                                         None),
            "no earlier statement named a true-up date", "unverified")
    # says it, but its period does not end on the true-up date that was printed
    _raises(lambda: B.classify_statement("2099-12-30",
                                         _statement("Yes", _SETTLEMENT_TEXT),
                                         ["11/26/99 - 12/20/99"], due),
            "none of its billing periods ends on 12/26/2099", "will not assert that it")
    # says it, period matches, but its own True-Up Date field disagrees
    wrong = _SETTLEMENT_TEXT.replace("12/26/2099", "12/26/2098")
    _raises(lambda: B.classify_statement("2099-12-30", _statement("Yes", wrong),
                                         period, due),
            "its own 'True-Up Date:' field reads 12/26/2098")
    # the real thing passes, and records what proved it
    row = B.classify_statement("2099-12-30", _statement("Yes", _SETTLEMENT_TEXT),
                               period, due)
    assert row["annual_settlement"] is True, row
    assert row["billing_mode"] == "annual true-up settlement", row
    ev = row["settlement_evidence"]
    assert len(ev["quotes"]) == 2, ev
    assert ev["printed_true_up_date"] == "12/26/2099", ev
    assert ev["matching_period_end"] == "11/26/99 - 12/26/99", ev
    # and both committed settlements carry that evidence
    for s in _artifact()["billing_mode"]["finding"]["annual_settlement_statements"]:
        p = s["proved_by"]
        assert len(p["quotes"]) == 2, s
        assert p["matching_period_end"] in s["true_up_period_ends"], s
        assert p["printed_true_up_date"], s
    return ("a payable statement is labelled an annual settlement only if it says so "
            "and closes the true-up the earlier statements printed; four ways of "
            "failing that fail closed, and both committed settlements carry the "
            "matching quotes, period end and printed true-up date")


def case_the_billing_mode_counts_come_from_the_validated_rows():
    """The prose used to say 'the two annual settlement statements' as a constant. A
    third settlement, or a statement that stopped accruing, would have left the artifact
    asserting uninterrupted accrual against its own rows."""
    art = _artifact()["billing_mode"]
    f, scan = art["finding"], art["per_statement"]
    accruing = [r for r in scan if r["billing_mode"] == "accrues to the annual true-up"]
    payable = [r for r in scan if r["annual_settlement"]]
    assert f["statements_scanned"] == len(scan), f
    assert f["statements_accruing"] == len(accruing), f
    assert f["statements_payable"] == len(payable), f
    assert len(accruing) + len(payable) == len(scan), (len(accruing), len(payable))
    prose = f["what_changed_and_when"]["the_billing_mode_did_not_change"]
    assert f"{len(accruing)} of the {len(scan)} statements" in prose, prose
    assert f"the other {len(payable)}" in prose, prose
    assert "the two annual settlement statements" not in prose, prose
    assert len(f["annual_settlement_statements"]) == len(payable), f
    return (f"every published count is derived from the {len(scan)} validated rows "
            f"({len(accruing)} accruing, {len(payable)} settlements) and the prose "
            "names them from the same source")


def case_the_export_is_reconciled_for_every_statement():
    art = _artifact()["billing_mode"]
    rec = art["billing_history_export_reconciliation"]
    rows = rec["rows"]
    assert rec["max_abs_residual_usd"] == 0.0, rec["max_abs_residual_usd"]
    for r in rows:
        assert abs(r["residual_usd"]) < CENT, r
        assert abs(r["export_current_charges_usd"] - r["explained_usd"]) < CENT, r
    # every $0.00 export row is explained by the ledger deferral, not by a free month
    zeros = [r for r in rows if r["export_current_charges_usd"] == 0.0]
    assert zeros, "no $0.00 export rows — the trap this analysis exists for is gone"
    for r in zeros:
        assert r["deferred_to_nem_ledger_usd"] == r["period_accrual_usd"], r
        assert r["period_accrual_usd"] > 0, r
    # the step off $0.00 lands on the presentation change, and nowhere else
    change = art["finding"]["what_changed_and_when"]["the_presentation_changed_on"]
    deferring = sorted(r["statement_date"] for r in rows
                       if r["deferred_to_nem_ledger_usd"] != 0.0)
    plain = sorted(r["statement_date"] for r in rows
                   if r["deferred_to_nem_ledger_usd"] == 0.0)
    assert max(deferring) < change <= min(plain), (max(deferring), change, min(plain))
    return (f"all {len(rows)} export rows explained to the cent; {len(zeros)} $0.00 rows "
            f"are deferrals into the NEM ledger, and the step off $0.00 is at {change}")


def case_the_mode_change_is_presentation_not_billing():
    f = _artifact()["billing_mode"]["finding"]["what_changed_and_when"]
    assert f["the_presentation_changed_on"] == "2025-05-02", f
    scan = _artifact()["billing_mode"]["per_statement"]
    by = {r["statement_date"]: r for r in scan}
    before, after = by["2025-04-02"], by["2025-05-02"]
    assert before["nem_ledger_block_printed"] and not after["nem_ledger_block_printed"]
    # what did NOT change: both are still "not payable this month"
    assert before["payment_required_this_month"] == "No"
    assert after["payment_required_this_month"] == "No"
    assert before["true_up_date"] == after["true_up_date"] == "Dec 26, 2025", \
        (before["true_up_date"], after["true_up_date"])
    return ("the 2025-05-02 statement drops the Net Metering Account Summary block "
            "while keeping the same true-up date and the same 'not payable' status — "
            "a presentation change, not a billing-mode change")


def case_an_interleaved_ledger_presentation_cannot_be_called_one_changeover():
    """The contiguity guard used to compare each ledger date against the maximum OF
    THE LEDGER DATES — its own bound, so it could never fire. A ledger block that
    stopped and came back would have been published as a single permanent
    changeover on a date that never existed."""
    scan = copy.deepcopy(_artifact()["billing_mode"]["per_statement"])
    by = {r["statement_date"]: r for r in scan}
    # make the ledger reappear after it stopped: 2025-06-03 sits after the
    # 2025-05-02 statement that first printed without it
    assert by["2025-06-03"]["nem_ledger_block_printed"] is False
    by["2025-06-03"]["nem_ledger_block_printed"] = True
    msg = _raises(lambda: B.billing_mode_finding(scan),
                  "not a single contiguous run", "2025-06-03", "2025-05-02")
    assert "changeover" in msg, msg
    return ("a ledger block reappearing after it stopped is refused instead of being "
            "reported as one changeover date")


def case_a_statement_missing_from_the_corpus_fails_closed():
    """A missing PDF is simply absent from the glob, so every loop over the corpus
    completes and the artifact still claims all 25 statements were scanned. The
    expected set therefore comes from the committed periods artifact, not from the
    files on disk."""
    want = sorted({s for (s, _) in B.periods()})
    if not B.ELEC_DIR.exists():
        # The synthetic half below still runs everywhere; only the closing check
        # that the REAL corpus satisfies the gate needs the archive.
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for stmt in want[:-1]:
                (d / f"sdge_electric_{stmt}.pdf").touch()
            saved, B.ELEC_DIR = B.ELEC_DIR, d
            try:
                _raises(B.statement_dates, "do not match", want[-1])
            finally:
                B.ELEC_DIR = saved
        raise SkipCase("the real-corpus half needs the private archive; the withheld-"
                       "statement refusal was still exercised synthetically")
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for stmt in want[:-1]:                       # one statement withheld
            (d / f"sdge_electric_{stmt}.pdf").touch()
        saved = B.ELEC_DIR
        B.ELEC_DIR = d
        try:
            msg = _raises(B.statement_dates, "do not match", want[-1])
            assert "parse_bills" in msg, msg
        finally:
            B.ELEC_DIR = saved
    B.statement_dates()          # the real corpus still passes the gate
    return "a statement present in the artifact but missing from the PDFs fails closed"


# ---------------------------------------------------------------------------
# A settlement $0 is not a price — enforced by the representation
# ---------------------------------------------------------------------------
def case_a_settlement_non_price_refuses_every_arithmetic_use():
    """The defect this module kept re-growing was always the same shape: a cell with no
    observable rate carried 0.0, and q*(p1-p0) then multiplied it. Every identity still
    held. The fix that cannot be forgotten is a type whose arithmetic raises, so this
    case pins each operator — including truth-testing, which is what `rate or 0.0`
    used."""
    s = B.Settlement("winter.off_peak", "base supply rate")
    ops = {
        "s + 1": lambda: s + 1, "1 + s": lambda: 1 + s,
        "s - 1": lambda: s - 1, "1 - s": lambda: 1 - s,
        "s * 2": lambda: s * 2, "2 * s": lambda: 2 * s,
        "s / 2": lambda: s / 2, "2 / s": lambda: 2 / s,
        "s ** 2": lambda: s ** 2, "-s": lambda: -s, "abs(s)": lambda: abs(s),
        "round(s, 2)": lambda: round(s, 2), "float(s)": lambda: float(s),
        "int(s)": lambda: int(s), "bool(s)": lambda: bool(s),
        "s or 0.0": lambda: s or 0.0, "s == 0": lambda: s == 0,
        "s != 0": lambda: s != 0, "s < 1": lambda: s < 1, "s > 1": lambda: s > 1,
    }
    for name, fn in ops.items():
        try:
            fn()
        except B.SettlementNotAPrice as e:
            assert "winter.off_peak" in str(e) and "base supply rate" in str(e), e
            continue
        raise AssertionError(f"{name} returned a value instead of refusing")
    assert issubclass(B.SettlementNotAPrice, SystemExit)
    # the three deliberate readings
    assert B.is_observed(s) is False and B.is_observed(0.42) is True
    assert B.is_observed(None) is False
    assert B.json_price(s) is None and B.json_price(0.123456) == 0.12346
    _raises(lambda: B.observed_rate(s, "winter.off_peak", "base supply rate"),
            "settlement non-price", "refusing")
    # and it cannot be serialised into the artifact by accident either
    try:
        json.dumps({"rate": s})
    except TypeError:
        pass
    else:
        raise AssertionError("a Settlement serialised into JSON")
    return (f"{len(ops)} arithmetic, coercion and comparison routes on a settlement "
            "non-price all raise SettlementNotAPrice naming the cell and the rate; "
            "observed_rate refuses it, json_price renders it null, and json.dumps "
            "cannot emit it")


def case_an_import_cell_priced_at_zero_is_refused_as_a_tariff():
    """The other way a settlement $0 could still get in: a cell that IS a net import in
    both periods but whose base or comparison rate prints $0 anyway. That is a parsing
    artefact or a settlement leak, never a tariff, and every term built on it — plus
    like_for_like's percentages, which divide by it — would be wrong."""
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
    for field, needle in (("supply_rate_effective", "base supply rate"),
                          ("delivery_rate_effective", "base delivery rate")):
        bad = copy.deepcopy(base)
        bad["cells"][("summer", "on_peak")][field] = 0.0
        _raises(lambda: B.decompose(bad, cur), needle, "is not a tariff")
    zeroed = copy.deepcopy(cur)
    zeroed["cells"][("summer", "on_peak")]["sdge_bundled_comparison_rate"] = 0.0
    _raises(lambda: B.decompose(base, zeroed), "same-date bundled comparison rate",
            "is not a tariff")
    # and a Settlement on an import cell means the parse is wrong, not that the cell
    # should quietly drop out of the priced set
    leaked = copy.deepcopy(base)
    leaked["cells"][("summer", "on_peak")]["supply_rate_effective"] = \
        B.Settlement("summer.on_peak", "base supply rate")
    _raises(lambda: B.decompose(leaked, cur), "settlement non-price", "refusing")
    # the real artifact carries no such cell
    for c in _artifact()["decomposition"]["per_cell"]:
        if not c["priced"]:
            continue
        for f in ("base_delivery_rate", "base_supply_rate", "base_rate_effective",
                  "sdge_bundled_comparison_rate_current_date"):
            assert c[f] not in (0.0, None), (c["cell"], f)
    return ("an import-in-both cell whose base delivery, base supply or same-date "
            "comparison rate is $0 — or is a settlement non-price — is refused rather "
            "than attributed")


# ---------------------------------------------------------------------------
# The decomposition identities, over the cells that support them
# ---------------------------------------------------------------------------
def case_the_whole_change_reconciles_within_a_dollar():
    art = _artifact()
    r = art["reconciliation"]
    p = art["periods"]
    observed = round(p["current"]["current_charges"] - p["base"]["current_charges"], 2)
    assert r["observed_change_usd"] == observed, (r["observed_change_usd"], observed)
    # the published identity, term by term, exactly as it is stated
    assert r["identity"] == ("observed change = (price + quantity + interaction, over "
                             "the priced cells) + netting/settlement + non-energy "
                             "bridge"), r["identity"]
    terms = (r["priced_cells_laspeyres_price_usd"]
             + r["priced_cells_laspeyres_quantity_usd"]
             + r["priced_cells_interaction_usd"]
             + r["netting_settlement_usd"]
             + r["non_energy_change_usd"])
    assert abs(terms - r["components_sum_usd"]) <= 0.02, (terms, r)
    assert abs(r["components_sum_usd"] - r["observed_change_usd"]) <= 1.0, r
    assert abs(r["residual_usd"]) <= 1.0, r
    # and the two intermediate totals it also publishes are consistent with it
    assert abs(r["priced_cells_change_usd"] + r["netting_settlement_usd"]
               - r["energy_change_usd"]) < CENT, r
    assert abs(r["energy_change_usd"] + r["non_energy_change_usd"]
               - r["components_sum_usd"]) < CENT, r
    return (f"observed ${observed} = priced cells ${r['priced_cells_change_usd']} "
            f"(price ${r['priced_cells_laspeyres_price_usd']} + quantity "
            f"${r['priced_cells_laspeyres_quantity_usd']} + interaction "
            f"${r['priced_cells_interaction_usd']}) + netting/settlement "
            f"${r['netting_settlement_usd']} + non-energy "
            f"${r['non_energy_change_usd']}, residual ${r['residual_usd']} "
            f"(tolerance ${r['tolerance_usd']})")


def case_every_published_identity_holds_exactly():
    agg = _artifact()["decomposition"]["aggregate"]
    ids = _artifact()["decomposition"]["identities"]
    pc = agg["priced_cells"]
    d = pc["change_usd"]
    checks = [
        ("L price + L quantity + interaction, priced cells",
         ids["priced_laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd"], d),
        ("L price + P quantity, priced cells",
         ids["priced_laspeyres_price_plus_paasche_quantity_usd"], d),
        ("P price + L quantity, priced cells",
         ids["priced_paasche_price_plus_laspeyres_quantity_usd"], d),
        ("scale + mix", ids["scale_plus_mix_usd"],
         ids["priced_laspeyres_quantity_usd"]),
        ("delivery vintage + supply vintage + provider (L)",
         ids["price_split_sum_laspeyres_usd"], ids["priced_laspeyres_price_usd"]),
        ("delivery vintage + supply vintage + provider (P)",
         ids["price_split_sum_paasche_usd"], ids["priced_paasche_price_usd"]),
        ("priced-cell change + netting/settlement",
         ids["priced_change_plus_netting_settlement_usd"], agg["energy_change_usd"]),
    ]
    for name, got, want in checks:
        assert abs(got - want) <= 0.01, f"{name}: {got} != {want}"
    # the published "reading" states both exact pairings, and each one sums to the
    # priced cells' change with no residual
    rd = pc["reading"]
    pairs = {(p["price_basis"], p["quantity_basis"]): p for p in rd["exact_pairings"]}
    assert set(pairs) == {("laspeyres", "paasche"), ("paasche", "laspeyres")}, pairs
    for key, p in pairs.items():
        assert abs(p["price_usd"] + p["quantity_usd"] - p["sum_usd"]) < CENT, p
        assert abs(p["sum_usd"] - d) <= 0.01, (key, p, d)
    assert rd["sums_to"] == "priced_cells.change_usd", rd
    assert abs(rd["of_which_scale_usd"] + rd["of_which_tou_mix_usd"]
               - pc["laspeyres"]["quantity_usd"]) <= 0.02, rd
    assert abs(rd["interaction_usd"] - pc["interaction_usd"]) < CENT
    return (f"all {len(checks)} index identities hold to the cent against a priced-cell "
            f"change of ${d}, and both exact pairings are published")


def case_the_decomposition_is_per_cell_not_only_aggregate():
    art = _artifact()["decomposition"]
    cells = art["per_cell"]
    pc = art["aggregate"]["priced_cells"]
    assert len(cells) == len(B.CELLS), len(cells)
    seen = {(c["season"], c["tou_period"]) for c in cells}
    assert seen == set(B.CELLS), sorted(seen)
    priced = [c for c in cells if c["priced"]]
    settled = [c for c in cells if not c["priced"]]
    assert priced and settled, "the corpus no longer has both kinds of cell"
    index_fields = ("laspeyres_price_usd", "laspeyres_quantity_usd",
                    "paasche_price_usd", "paasche_quantity_usd", "interaction_usd",
                    "delivery_vintage_usd_paasche", "supply_vintage_usd_paasche",
                    "provider_usd_paasche", "delivery_vintage_usd_laspeyres",
                    "supply_vintage_usd_laspeyres", "provider_usd_laspeyres")
    for c in cells:
        assert abs(c["current_usd"] - c["base_usd"] - c["change_usd"]) < CENT, c
        if c["priced"]:
            for field in index_fields:
                assert field in c, f"{c['cell']} has no {field}"
            for field in ("base_rate_effective", "current_rate_effective",
                          "sdge_bundled_comparison_rate_current_date"):
                assert c[field] is not None, (c["cell"], field)
            assert abs(c["laspeyres_price_usd"] + c["laspeyres_quantity_usd"]
                       + c["interaction_usd"] - c["change_usd"]) <= 0.02, c
        else:
            # a settlement cell carries no index term at all — not a zero, not a null
            for field in index_fields:
                assert field not in c, f"{c['cell']} publishes {field} anyway"
            assert c["netting_settlement_usd"] == c["change_usd"], c
            assert c["why_not_priced"], c
    for total, field in ((pc["laspeyres"]["price_usd"], "laspeyres_price_usd"),
                         (pc["laspeyres"]["quantity_usd"], "laspeyres_quantity_usd"),
                         (pc["paasche"]["price_usd"], "paasche_price_usd"),
                         (pc["paasche"]["quantity_usd"], "paasche_quantity_usd"),
                         (pc["interaction_usd"], "interaction_usd")):
        got = round(sum(c[field] for c in priced), 2)
        assert abs(got - total) <= 0.02, f"{field}: priced cells sum to {got} != {total}"
    got = round(sum(c["change_usd"] for c in cells), 2)
    assert abs(got - art["aggregate"]["energy_change_usd"]) <= 0.02, got
    return (f"{len(cells)} season x TOU cells; the {len(priced)} priced ones each carry "
            f"their own price, quantity, interaction and vintage/provider terms that "
            f"sum to the aggregate, and the {len(settled)} settlement cells carry no "
            "index term at all, only their whole dollar change")


def case_the_published_index_covers_only_the_priced_cells():
    """Every index figure — the bounds, the scale/mix split, the vintage/provider split
    — has to be built from cells with an observable price at BOTH ends. Naming a
    contribution does not remove a settlement zero from a calculation, so the check is
    that the settlement cells are outside the arithmetic, not that they are labelled."""
    art = _artifact()
    agg = art["decomposition"]["aggregate"]
    cells = art["decomposition"]["per_cell"]
    pc, scope = agg["priced_cells"], agg["scope"]
    priced = [c for c in cells if c["priced"]]
    settled = [c for c in cells if not c["priced"]]
    assert scope["priced_cells"] == [c["cell"] for c in priced], scope
    assert scope["settlement_cells"] == [c["cell"] for c in settled], scope
    assert pc["cells"] == scope["priced_cells"], pc["cells"]
    # a priced cell is a net import at BOTH ends; a settlement cell is not
    for c in priced:
        assert c["base_net_import"] and c["current_net_import"], c
    for c in settled:
        assert not (c["base_net_import"] and c["current_net_import"]), c
    # the price split has THREE terms, and no fourth carrying a settlement figure
    for basis in ("price_split_laspeyres_basis", "price_split_paasche_basis"):
        s = pc[basis]
        assert set(s) == {"delivery_vintage_usd", "supply_vintage_usd",
                          "provider_usd"}, s
        assert "netting_regime_usd" not in s, s
    # every published aggregate equals the sum over the priced rows and nothing else
    for got, field in ((pc["laspeyres"]["price_usd"], "laspeyres_price_usd"),
                       (pc["paasche"]["price_usd"], "paasche_price_usd"),
                       (pc["laspeyres"]["quantity_usd"], "laspeyres_quantity_usd"),
                       (pc["paasche"]["quantity_usd"], "paasche_quantity_usd"),
                       (pc["interaction_usd"], "interaction_usd")):
        assert abs(got - round(sum(c[field] for c in priced), 2)) <= 0.02, field
    for basis in ("laspeyres", "paasche"):
        for term in ("delivery_vintage", "supply_vintage", "provider"):
            want = round(sum(c[f"{term}_usd_{basis}"] for c in priced), 2)
            got = pc[f"price_split_{basis}_basis"][f"{term}_usd"]
            assert abs(got - want) <= 0.02, (basis, term, got, want)
        # the three terms are the whole price effect over these cells
        s = pc[f"price_split_{basis}_basis"]
        assert abs(sum(s.values()) - pc[basis]["price_usd"]) <= 0.02, (basis, s)
        # and they are the same dollars like_for_like publishes
        lfl = art["like_for_like"]["price_effect_split_usd"][basis]
        assert abs(sum(lfl.values()) - sum(s.values())) <= 0.02, (basis, lfl, s)
    # the quantity split's kWh totals are the priced cells', not the periods'
    split = pc["quantity_split_laspeyres_basis"]
    assert abs(split["base_net_kwh"] - sum(c["base_kwh"] for c in priced)) < 0.05, split
    assert abs(split["current_net_kwh"]
               - sum(c["current_kwh"] for c in priced)) < 0.05, split
    assert split["base_net_kwh"] != art["periods"]["base"]["net_kwh"], split
    # the scope note says which cells each figure covers, in prose
    assert "priced_cells" in pc["reading"]["covers"] or \
        all(c in pc["reading"]["covers"] for c in scope["priced_cells"]), pc["reading"]
    return (f"every index figure is computed over the {len(priced)} priced cells "
            f"({', '.join(scope['priced_cells'])}) and nothing else; the price split "
            "has three terms and no settlement term hiding inside it")


def case_the_energy_change_is_priced_cells_plus_settlement():
    """The complete dollar change of every export-flipped cell is a top-level component
    beside the index figures, not a sub-key of the price split. That is the difference
    between excluding a settlement zero and renaming its contribution."""
    art = _artifact()
    agg = art["decomposition"]["aggregate"]
    cells = art["decomposition"]["per_cell"]
    ns, pc = agg["netting_settlement"], agg["priced_cells"]
    settled = [c for c in cells if not c["priced"]]
    assert ns["cells"] == [c["cell"] for c in settled], ns
    want = round(sum(c["change_usd"] for c in settled), 2)
    assert abs(ns["change_usd"] - want) < CENT, (ns["change_usd"], want)
    rows = {r["cell"]: r for r in ns["per_cell"]}
    assert set(rows) == {c["cell"] for c in settled}, sorted(rows)
    for c in settled:
        r = rows[c["cell"]]
        assert r["change_usd"] == c["change_usd"], (r, c)
        assert abs(r["current_usd"] - r["base_usd"] - r["change_usd"]) < CENT, r
        assert "settled at the annual true-up" in r["why_not_priced"], r
    # it sits OUTSIDE the price and quantity figures: both exact pairings, and the
    # Laspeyres triple, close on the PRICED cells' change with nothing left over for it
    for price, quantity in (("laspeyres", "paasche"), ("paasche", "laspeyres")):
        assert abs(pc[price]["price_usd"] + pc[quantity]["quantity_usd"]
                   - pc["change_usd"]) <= 0.02, (price, quantity)
    assert abs(pc["laspeyres"]["price_usd"] + pc["laspeyres"]["quantity_usd"]
               + pc["interaction_usd"] - pc["change_usd"]) <= 0.02, pc
    ident = agg["energy_identity"]
    assert abs(ident["priced_cells_change_usd"] + ident["netting_settlement_change_usd"]
               - ident["energy_change_usd"]) < CENT, ident
    assert ident["priced_cells_change_usd"] == pc["change_usd"], ident
    assert ident["netting_settlement_change_usd"] == ns["change_usd"], ident
    # it is a substantive amount, not a rounding crumb — this is the money the old
    # all-cell reading was carrying inside the price bounds
    assert abs(ns["change_usd"]) > 10.0, ns
    assert abs(ns["change_usd"]) > abs(pc["reading"]["price_usd_high"]), ns
    # and the report states it as its own component
    report = _report()
    assert f"{abs(ns['change_usd']):,.2f}" in report, \
        "index.html does not state the netting/settlement component"
    return (f"the {len(settled)} settlement cells' complete change "
            f"${ns['change_usd']} is a top-level component: priced cells "
            f"${pc['change_usd']} + netting/settlement ${ns['change_usd']} = energy "
            f"${agg['energy_change_usd']}, with neither inside the other")


def case_a_flipped_cell_is_outside_every_index_term():
    """The same rule, driven synthetically: a cell that exports in the base period and
    imports in the current one contributes to no price, quantity or interaction term at
    all — its whole change is the settlement component — while a cell that imports in
    both keeps its full split."""
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "off_peak"): (-50.0, 0.0, 0.0, 0.0)})
    cur = _cells({("summer", "on_peak"): (120.0, 0.25, 0.40, 0.35),
                  ("summer", "off_peak"): (80.0, 0.22, 0.33, 0.31)})
    out = B.decompose(base, cur)
    agg = out["aggregate"]
    pc = agg["priced_cells"]
    assert agg["scope"]["priced_cells"] == ["summer.on_peak"], agg["scope"]
    assert "summer.off_peak" in agg["scope"]["settlement_cells"], agg["scope"]
    assert set(agg["scope"]["priced_cells"]) | set(agg["scope"]["settlement_cells"]) == \
        {B._key(*k) for k in B.CELLS}, agg["scope"]
    # every index figure is the import-in-both cell's, exactly
    q0, q1 = 100.0, 120.0
    p0, p1 = 0.50, 0.65
    assert abs(pc["laspeyres"]["price_usd"] - q0 * (p1 - p0)) < CENT, pc
    assert abs(pc["paasche"]["price_usd"] - q1 * (p1 - p0)) < CENT, pc
    assert abs(pc["laspeyres"]["quantity_usd"] - p0 * (q1 - q0)) < CENT, pc
    assert abs(pc["interaction_usd"] - (p1 - p0) * (q1 - q0)) < CENT, pc
    # the flipped cell's supply move (nothing -> $0.33 against a $0.31 comparison)
    # would have read as +$24.75 of supply vintage on current weights if attributed
    assert pc["price_split_paasche_basis"]["supply_vintage_usd"] == \
        B._c(q1 * (0.35 - 0.30)), pc["price_split_paasche_basis"]
    attributed = sum(pc["price_split_paasche_basis"][t] for t in
                     ("delivery_vintage_usd", "supply_vintage_usd", "provider_usd"))
    assert abs(attributed - q1 * (p1 - p0)) < CENT, attributed
    # and the flipped cell's WHOLE change is the settlement component
    ns = agg["netting_settlement"]
    assert abs(ns["change_usd"] - B._c(80.0 * 0.55 - 0.0)) < CENT, ns
    assert abs(pc["change_usd"] + ns["change_usd"]
               - agg["energy_change_usd"]) < CENT, (pc, ns)
    row = next(r for r in out["per_cell"] if r["cell"] == "summer.off_peak")
    assert row["priced"] is False and "laspeyres_price_usd" not in row, row
    assert row["base_rate_effective"] is None, row
    return ("a cell that flips from net export to net import contributes to no price, "
            "quantity, interaction or vintage term; its whole $44.00 change is the "
            "settlement component, and the import-in-both cell keeps the entire index")


def case_the_quantity_split_prices_no_kwh_at_a_settlement_zero():
    """scale and mix value every kWh at the base period's effective price. When they ran
    over all six cells, three of those base prices were a settlement $0 and 795 kWh of
    net swing was valued at nothing. Over the priced cells that cannot happen — and the
    check is that it cannot, not that it is disclosed."""
    art = _artifact()
    agg = art["decomposition"]["aggregate"]
    pc = agg["priced_cells"]
    split = pc["quantity_split_laspeyres_basis"]
    cells = {c["cell"]: c for c in art["decomposition"]["per_cell"]}
    for cell in pc["cells"]:
        c = cells[cell]
        assert c["base_net_import"], c
        assert c["base_rate_effective"] not in (0.0, None), c
        assert abs(c["laspeyres_quantity_usd"]
                   - c["base_rate_effective"]
                   * (c["current_kwh"] - c["base_kwh"])) <= 0.02, c
    # the retired disclosure keys are gone, because there is nothing left to disclose
    assert "cells_priced_at_a_settlement_zero_base_rate" not in split, split
    assert "net_kwh_change_valued_at_zero" not in split, split
    assert "settlement_zero_caveat" not in split, split
    why = split["every_base_price_here_is_an_observed_tariff"]
    assert "no kWh here is valued at a settlement $0" in why, why
    assert "raises rather than multiplying" in why, why
    # the identity still holds, on the priced cells' own kWh
    assert abs(split["scale_usd"] + split["tou_mix_usd"]
               - pc["laspeyres"]["quantity_usd"]) <= 0.02, split
    assert split["base_net_kwh"] != art["periods"]["base"]["net_kwh"], split
    assert split["current_net_kwh"] != art["periods"]["current"]["net_kwh"], split
    return (f"the scale/mix split runs on {split['base_net_kwh']} -> "
            f"{split['current_net_kwh']} kWh of priced-cell net import, every base "
            "price in it an observed tariff; no kWh is valued at a settlement $0 and "
            "the disclosure that used to be needed is gone")


def case_price_only_movement_makes_the_two_readings_agree():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "super_off_peak"): (400.0, 0.04, 0.06, 0.06)})
    cur = _cells({("summer", "on_peak"): (100.0, 0.25, 0.35, 0.35),
                  ("summer", "super_off_peak"): (400.0, 0.05, 0.07, 0.07)})
    d = B.decompose(base, cur)["aggregate"]["priced_cells"]
    assert abs(d["laspeyres"]["price_usd"] - d["paasche"]["price_usd"]) < CENT, d
    assert abs(d["interaction_usd"]) < CENT, d
    assert abs(d["laspeyres"]["quantity_usd"]) < CENT, d
    assert abs(d["laspeyres"]["price_usd"] - d["change_usd"]) < CENT, d
    return ("with quantities held fixed the two price readings coincide and the "
            "interaction term is exactly zero")


def case_quantity_only_movement_makes_the_two_readings_agree():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "super_off_peak"): (400.0, 0.04, 0.06, 0.06)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.20, 0.30, 0.30),
                  ("summer", "super_off_peak"): (350.0, 0.04, 0.06, 0.06)})
    d = B.decompose(base, cur)["aggregate"]["priced_cells"]
    assert abs(d["laspeyres"]["quantity_usd"] - d["paasche"]["quantity_usd"]) < CENT, d
    assert abs(d["interaction_usd"]) < CENT, d
    assert abs(d["laspeyres"]["price_usd"]) < CENT, d
    split = d["quantity_split_laspeyres_basis"]
    # total quantity is unchanged here, so the whole quantity effect is TOU mix
    assert abs(split["scale_usd"]) < CENT, split
    assert abs(split["tou_mix_usd"] - d["laspeyres"]["quantity_usd"]) < CENT, split
    return ("with prices held fixed the two quantity readings coincide, the "
            "interaction term is zero, and a pure re-shuffle across TOU periods "
            "lands entirely in the mix term")


def case_the_interaction_term_is_the_spread_between_the_two_readings():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "super_off_peak"): (400.0, 0.04, 0.06, 0.06)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35),
                  ("summer", "super_off_peak"): (300.0, 0.05, 0.07, 0.07)})
    d = B.decompose(base, cur)["aggregate"]["priced_cells"]
    spread = d["paasche"]["price_usd"] - d["laspeyres"]["price_usd"]
    assert abs(spread - d["interaction_usd"]) < CENT, (spread, d["interaction_usd"])
    spread_q = d["paasche"]["quantity_usd"] - d["laspeyres"]["quantity_usd"]
    assert abs(spread_q - d["interaction_usd"]) < CENT, spread_q
    assert d["interaction_usd"] != 0.0
    return ("when both price and quantity move, the interaction term equals the "
            "Paasche-minus-Laspeyres spread on both the price and the quantity side "
            "— it is reported, never allocated away")


def case_an_inconsistent_cell_breaks_the_identity_check():
    """The identities are algebra, so they can only fail if a cell's dollars and its
    effective rate disagree — which is exactly what a parsing regression looks like.
    The guard must fire rather than publish a decomposition of numbers that are not
    the bill's."""
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
    bad = copy.deepcopy(cur)
    bad["cells"][("summer", "on_peak")]["usd"] += 500.0
    _raises(lambda: B.decompose(base, bad), "identity broken")
    return ("a cell whose dollars contradict its effective rate is refused by the "
            "identity check instead of being published")


def case_the_published_reading_allocates_none_of_the_interaction():
    """The artifact says the interaction is published and not allocated. The reading it
    prints has to match that claim: pairing Paasche price with Laspeyres quantity and
    calling the price figure "the" price effect would hand the whole interaction to
    price, because Paasche price == Laspeyres price + interaction."""
    pc = _artifact()["decomposition"]["aggregate"]["priced_cells"]
    rd = pc["reading"]
    assert rd["convention"].startswith("bounds"), rd["convention"]
    lp, pp = pc["laspeyres"]["price_usd"], pc["paasche"]["price_usd"]
    lq, pq = pc["laspeyres"]["quantity_usd"], pc["paasche"]["quantity_usd"]
    inter = pc["interaction_usd"]
    # the published interval is exactly the two readings, and its width is exactly
    # the interaction — so neither endpoint is being sold as an attribution
    assert (rd["price_usd_low"], rd["price_usd_high"]) == (min(lp, pp), max(lp, pp)), rd
    assert (rd["quantity_usd_low"], rd["quantity_usd_high"]) == \
        (min(lq, pq), max(lq, pq)), rd
    assert abs((rd["price_usd_high"] - rd["price_usd_low"]) - abs(inter)) <= 0.02, rd
    assert abs((rd["quantity_usd_high"] - rd["quantity_usd_low"])
               - abs(inter)) <= 0.02, rd
    assert abs(abs(rd["interval_width_usd"]) - abs(inter)) < CENT, rd
    # no point estimate is published under a name that would read as one
    assert "price_usd" not in rd and "quantity_usd" not in rd, sorted(rd)
    for pair in rd["exact_pairings"]:
        assert pair["price_basis"] != pair["quantity_basis"], pair
    # and the reading says which cells it covers, because it no longer covers all six
    for cell in pc["cells"]:
        assert cell in rd["covers"], (cell, rd["covers"])
    assert "settlement cells are outside" in rd["covers"], rd["covers"]
    # and the prose says so, in the artifact and in the report
    assert "interval" in rd["basis"], rd["basis"]
    assert "accounts" in rd["basis"] and "no figure" in rd["basis"], rd["basis"]
    assert "never" in rd["interaction_note"], rd["interaction_note"]
    report = _report()
    assert "price accounts for" not in report, \
        "index.html still states a point price attribution"
    for end in (rd["price_usd_low"], rd["price_usd_high"],
                rd["quantity_usd_low"], rd["quantity_usd_high"]):
        assert f"{abs(end):,.2f}" in report, \
            f"index.html omits the published bound {end}"
    # the retired all-cell bounds must not survive anywhere in the report
    for retired in ("257.22", "436.15", "367.15", "237.86", "168.86"):
        assert retired not in report, f"index.html still carries the retired {retired}"
    return (f"price and quantity are published as intervals over the priced cells "
            f"(price ${rd['price_usd_low']} to ${rd['price_usd_high']}, quantity "
            f"${rd['quantity_usd_low']} to ${rd['quantity_usd_high']}), each exactly "
            f"${abs(inter)} wide because that is the unallocated interaction, and the "
            "report states no point attribution either")


# ---------------------------------------------------------------------------
# Provider vs vintage, and the CCA authority boundary
# ---------------------------------------------------------------------------
def case_provider_and_vintage_are_reported_as_separate_terms():
    pc = _artifact()["decomposition"]["aggregate"]["priced_cells"]
    prov = _artifact()["decomposition"]["aggregate"]["provider_comparison"]
    for basis in ("price_split_laspeyres_basis", "price_split_paasche_basis"):
        s = pc[basis]
        assert set(s) == {"delivery_vintage_usd", "supply_vintage_usd",
                          "provider_usd"}, s
    assert abs(sum(pc["price_split_laspeyres_basis"].values())
               - pc["laspeyres"]["price_usd"]) <= 0.02, pc
    assert abs(sum(pc["price_split_paasche_basis"].values())
               - pc["paasche"]["price_usd"]) <= 0.02, pc
    assert "bundled PCIA charge" in prov["why_the_riders_belong_on_this_side"], prov
    # the vintage term is same-provider-two-dates; the provider term is
    # two-providers-same-date. Per cell, the two must be built from three distinct
    # rates, or the split is decorative.
    for c in _artifact()["decomposition"]["per_cell"]:
        if not c["priced"]:
            continue
        rates = (c["base_supply_rate"], c["sdge_bundled_comparison_rate_current_date"],
                 c["current_supply_rate"])
        assert len(set(rates)) == 3, f"{c['cell']} supply rates collapse: {rates}"
    # and the per-cell provider term names its own, third scope
    assert prov["per_cell_term_paasche_usd"] == \
        pc["price_split_paasche_basis"]["provider_usd"], prov
    for cell in pc["cells"]:
        assert cell in prov["per_cell_term_scope"], prov["per_cell_term_scope"]
    return ("provider and vintage are separate published terms that sum to the priced "
            "cells' price effect on both weight bases, each built from three distinct "
            "rates per cell")


def case_the_provider_comparison_publishes_two_scopes():
    """The CCA-only riders and CEA's product adders are charged once per period on the
    period's own kWh; SDG&E's printed bundled table prices only the cells billed as net
    imports. Adding the first to a cell-restricted CEA total and calling the result a
    comparison "over five cells" compares two different quantity scopes. Two figures,
    each labelled, or none."""
    art = _artifact()
    prov = art["decomposition"]["aggregate"]["provider_comparison"]
    cells = art["decomposition"]["per_cell"]
    ledger = art["period_ledgers"]["current"]["terms"]
    eo, wp = prov["energy_only_on_the_common_cells"], prov["whole_period_arrangement"]
    imports = [c["cell"] for c in cells if c["current_net_import"]]
    exports = [c["cell"] for c in cells if not c["current_net_import"]]
    assert imports and exports, "the corpus no longer has both kinds of current cell"
    # (1) the energy-only figure: same cells, same kWh, no riders on either side
    rows = {r["cell"]: r for r in prov["per_cell"]}
    assert set(rows) == {c["cell"] for c in cells}, sorted(rows)
    assert eo["excluded_cells"] == exports, (eo, exports)
    for cell in exports:
        assert rows[cell]["sdge_bundled_same_date_usd"] is None, rows[cell]
        assert rows[cell]["difference_usd"] is None, rows[cell]
    assert abs(sum(rows[c]["cea_charged_usd"] for c in imports)
               - eo["cea_charged_supply_usd"]) < CENT, eo
    assert abs(sum(rows[c]["sdge_bundled_same_date_usd"] for c in imports)
               - eo["sdge_bundled_same_date_usd"]) < CENT, eo
    assert abs(eo["difference_usd"] - (eo["cea_charged_supply_usd"]
                                       - eo["sdge_bundled_same_date_usd"])) < CENT, eo
    for rider in (ledger["unbundling_riders"], ledger["cca_product_adders"]):
        assert rider != 0.0, ledger
        assert abs(eo["cea_charged_supply_usd"]
                   - (eo["cea_charged_supply_usd"] - rider)) > CENT
    assert eo["cea_charged_supply_usd"] != ledger["energy_supply"], eo
    for cell in imports:
        assert cell in eo["covers"], (cell, eo["covers"])
    assert "energy only, both sides" in eo["covers"], eo["covers"]
    # (2) the whole-period figure: both sides whole, and the scope gap is named
    side = wp["cca_side"]
    assert side["cea_charged_supply_all_cells_usd"] == ledger["energy_supply"], side
    assert side["cea_product_adders_usd"] == ledger["cca_product_adders"], side
    assert side["cca_unbundling_riders_usd"] == ledger["unbundling_riders"], side
    assert abs(side["total_usd"] - (side["cea_charged_supply_all_cells_usd"]
                                    + side["cea_product_adders_usd"]
                                    + side["cca_unbundling_riders_usd"])) < CENT, side
    assert abs(wp["difference_usd"]
               - (side["total_usd"] - wp["bundled_side"]["total_usd"])) < CENT, wp
    assert "the whole billing period, both sides" in wp["covers"], wp["covers"]
    gap = wp["cea_booked_on_the_cells_the_bundled_table_does_not_price_usd"]
    assert abs(gap - (side["cea_charged_supply_all_cells_usd"]
                      - eo["cea_charged_supply_usd"])) < CENT, wp
    assert f"{gap:,.2f}" in wp["the_two_sides_do_not_price_identical_energy"], wp
    for cell in exports:
        assert cell in wp["the_two_sides_do_not_price_identical_energy"], wp
    # (3) the two are genuinely different figures, and neither is the retired mixed one
    assert eo["difference_usd"] != wp["difference_usd"], (eo, wp)
    mixed = round(eo["cea_charged_supply_usd"] + side["cea_product_adders_usd"]
                  + side["cca_unbundling_riders_usd"], 2)
    report = _report()
    assert f"{mixed:,.2f}" not in report, \
        "index.html still quotes the mixed-scope CEA-side total"
    # the retired mixed-scope provider effect and its percentage, in every form the
    # report could still be carrying them
    for retired in ("19.76", "$19.76 (10.9%)", "10.9%) provider",
                    "unallocated netting/settlement"):
        assert retired not in report, f"index.html still carries the retired {retired!r}"
    # (4) the report quotes both, with their scopes
    for figure in (eo["cea_charged_supply_usd"], eo["sdge_bundled_same_date_usd"],
                   abs(eo["difference_usd"]), side["total_usd"],
                   abs(wp["difference_usd"])):
        assert f"{figure:,.2f}" in report, f"index.html omits {figure}"
    return (f"two labelled comparisons: energy only over the {len(imports)} common "
            f"cells, ${eo['cea_charged_supply_usd']} CEA against "
            f"${eo['sdge_bundled_same_date_usd']} bundled = ${eo['difference_usd']} "
            f"({eo['difference_pct']}%); and the whole-period arrangement, "
            f"${side['total_usd']} against ${wp['bundled_side']['total_usd']} = "
            f"${wp['difference_usd']} ({wp['difference_pct']}%), with the ${gap} of CEA "
            "credit on the unpriced export cell named as the scope gap")


def case_the_printed_bundled_comparison_is_never_priced_as_supply():
    art = _artifact()
    cur = art["period_ledgers"]["current"]["terms"]
    assert cur["printed_bundled_comparison_net_of_its_credit"] == 0.0, cur
    prov = art["decomposition"]["aggregate"]["provider_comparison"]
    counter = prov["energy_only_on_the_common_cells"]["sdge_bundled_same_date_usd"]
    assert counter > 0, counter
    assert abs(cur["energy_supply"] - counter) > 1.0, (cur["energy_supply"], counter)
    bridge = {t["term"]: t for t in art["non_energy_bridge"]}
    assert bridge["printed_bundled_comparison_net_of_its_credit"]["change_usd"] == 0.0
    for c in art["decomposition"]["per_cell"]:
        if not c["current_net_import"]:
            continue
        assert c["current_supply_rate"] != \
            c["sdge_bundled_comparison_rate_current_date"], c["cell"]
    return (f"the printed bundled comparison (${counter}) is carried only as a pair "
            f"that nets to $0 and as the same-date counterfactual; the supply dollars "
            f"are the CEA page's ${cur['energy_supply']}")


def case_a_current_export_cell_cannot_enter_the_provider_comparison():
    """On a cell billed as a net export in the current period SDG&E's printed bundled
    comparison is $0 because the export settled at the annual true-up — it is not a
    bundled tariff of zero — while the CCA still books a credit against that cell.
    Netting the two publishes a settlement outcome as a provider PRICE effect, and the
    arithmetic balances while doing it. The counterfactual is a Settlement, so the
    subtraction cannot even be written.

    Driven synthetically: one import cell that supports the comparison, one export cell
    that does not."""
    cells = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.25),
                    ("summer", "off_peak"): (-40.0, 0.0, 0.05, 0.0)})["cells"]
    w = B.provider_effect_whole_period(cells, 1.0, 10.0)
    eo, wp = w["energy_only_on_the_common_cells"], w["whole_period_arrangement"]
    assert eo["excluded_cells"] == [c for c in
                                    (B._key(*k) for k in B.CELLS)
                                    if c != "summer.on_peak"], eo["excluded_cells"]
    # energy only, same cell both sides: CEA $30 against $25 of printed bundled supply
    assert eo["cea_charged_supply_usd"] == 30.0, eo
    assert eo["sdge_bundled_same_date_usd"] == 25.0, eo
    assert eo["difference_usd"] == 5.0, eo
    assert eo["difference_pct"] == 20.0, eo
    # whole period: CEA on ALL cells (30 + the export cell's -2) + 1 adders + 10 riders
    assert wp["cca_side"]["cea_charged_supply_all_cells_usd"] == 28.0, wp
    assert wp["cca_side"]["total_usd"] == 39.0, wp
    assert wp["difference_usd"] == 14.0, wp
    assert wp["cea_booked_on_the_cells_the_bundled_table_does_not_price_usd"] == -2.0, wp
    # the export cell carries no difference at all — not a zero, a null
    row = next(r for r in w["per_cell"] if r["cell"] == "summer.off_peak")
    assert row["sdge_bundled_same_date_usd"] is None and row["difference_usd"] is None
    assert "settled at the annual true-up" in row["why_the_bundled_side_is_absent"]
    # a cell with no counterfactual at all is refused, not estimated
    gone = copy.deepcopy(cells)
    gone[("summer", "on_peak")]["sdge_bundled_comparison_usd"] = None
    _raises(lambda: B.provider_effect_whole_period(gone, 1.0, 10.0),
            "summer", "on_peak", "refusing to estimate one")
    # a period with no current net-import cell has no counterfactual anywhere
    allexp = _cells({("summer", "off_peak"): (-40.0, 0.0, 0.05, 0.0)})["cells"]
    _raises(lambda: B.provider_effect_whole_period(allexp, 1.0, 10.0),
            "refusing to publish a provider effect")
    # and an export cell that DOES carry a printed comparison is a real observed
    # counterfactual — it must not be dropped silently by this restriction
    observed = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.25),
                       ("summer", "off_peak"): (-40.0, 0.0, 0.05, 0.03)})["cells"]
    _raises(lambda: B.provider_effect_whole_period(observed, 1.0, 10.0),
            "OBSERVED bundled", "must not be discarded")
    return ("a cell billed as a net export in the current period carries a settlement "
            "non-price, so it cannot enter the energy-only comparison at all; it "
            "appears in the whole-period arrangement only as the named −$2.00 of CEA "
            "credit on energy the bundled table does not price")


def case_a_provider_effect_is_refused_without_a_same_date_comparison():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
    cur["cells"][("summer", "on_peak")]["sdge_bundled_comparison_rate"] = None
    _raises(lambda: B.decompose(base, cur), "summer", "on_peak",
            "refusing to estimate")
    return ("with no same-date bundled comparison for a cell the split is refused, "
            "naming the cell, rather than an estimated provider effect being invented")


def case_no_per_year_price_figure_is_published():
    """A compound-equivalent transformation of two matched endpoints is not an observed
    annual rate. There is no matched observation between these two statements, so no
    per-year figure may be published — a downstream consumer would read "per year" as a
    measured yearly change."""
    art = _artifact()
    idx = art["like_for_like"]["price_index"]
    assert "fisher_pct_per_year" not in idx, idx
    per_year = [k for k in idx if k.endswith("_per_year") or "per_year" in k]
    assert not per_year, per_year
    assert idx["is_total_change_not_a_rate"] is True, idx
    assert "two matched" in idx["no_annual_rate_path"], idx["no_annual_rate_path"]
    assert "no matched observation between them" in idx["no_annual_rate_path"], idx
    assert idx["years_apart"] > 1.0, idx
    # the report may quote the total change over the window, never a per-year rate
    report = _report()
    fisher = f"{abs(idx['fisher_pct']):.1f}%"
    assert fisher in report, f"index.html omits the published Fisher reading {fisher}"
    for bad in (f"{fisher}/yr", f"{fisher} per year", f"{fisher} a year",
                f"{fisher}/year"):
        assert bad not in report, f"index.html states {bad!r} as an annual rate"
    return (f"the price index publishes total changes over {idx['years_apart']} years "
            "and no per-year rate, with the two-endpoint limit stated in the artifact")


# ---------------------------------------------------------------------------
# Like-for-like
# ---------------------------------------------------------------------------
def case_the_like_for_like_index_is_fixed_weight():
    art = _artifact()
    lfl = art["like_for_like"]
    rows = lfl["cells"]
    assert rows, "no like-for-like cells"
    for r in rows:
        assert r["base_kwh"] > 0 and r["current_kwh"] > 0, r
    excluded = set(lfl["excluded_cells"])
    flipped = {c["cell"] for c in art["decomposition"]["per_cell"] if not c["priced"]}
    assert excluded == flipped, (excluded, flipped)
    assert {r["cell"] for r in rows} == \
        set(art["decomposition"]["aggregate"]["scope"]["priced_cells"]), rows
    q0p0 = sum(r["base_kwh"] * r["base_rate_effective"] for r in rows)
    q0p1 = sum(r["base_kwh"] * r["current_rate_effective"] for r in rows)
    q1p0 = sum(r["current_kwh"] * r["base_rate_effective"] for r in rows)
    q1p1 = sum(r["current_kwh"] * r["current_rate_effective"] for r in rows)
    idx = lfl["price_index"]
    assert abs(idx["laspeyres_pct"] - 100.0 * (q0p1 / q0p0 - 1.0)) < 0.05, idx
    assert abs(idx["paasche_pct"] - 100.0 * (q1p1 / q1p0 - 1.0)) < 0.05, idx
    fisher = ((q0p1 / q0p0) * (q1p1 / q1p0)) ** 0.5
    assert abs(idx["fisher_pct"] - 100.0 * (fisher - 1.0)) < 0.05, idx
    # the trap the fixed weights avoid: the blended-rate ratio is not a price index
    blended = 100.0 * ((q1p1 / sum(r["current_kwh"] for r in rows))
                       / (q0p0 / sum(r["base_kwh"] for r in rows)) - 1.0)
    assert abs(blended - idx["fisher_pct"]) > 25.0, (blended, idx["fisher_pct"])
    assert abs(lfl["price_effect_usd"]["laspeyres"] - (q0p1 - q0p0)) < 0.02
    assert abs(lfl["price_effect_usd"]["paasche"] - (q1p1 - q1p0)) < 0.02
    # and it is the same price effect the decomposition publishes as its bounds
    pc = art["decomposition"]["aggregate"]["priced_cells"]
    for basis in ("laspeyres", "paasche"):
        assert abs(lfl["price_effect_usd"][basis] - pc[basis]["price_usd"]) <= 0.02, basis
        s = lfl["price_effect_split_usd"][basis]
        assert abs(sum(s.values()) - lfl["price_effect_usd"][basis]) <= 0.02, s
    return (f"the like-for-like index is fixed-weight (Laspeyres {idx['laspeyres_pct']}%, "
            f"Paasche {idx['paasche_pct']}%, Fisher {idx['fisher_pct']}% over "
            f"{idx['years_apart']} yr); the blended-rate ratio it replaces would have "
            f"read {blended:+.1f}%")


# ---------------------------------------------------------------------------
# The artifact against the committed bill artifacts, and regeneration
# ---------------------------------------------------------------------------
def case_the_artifact_agrees_with_the_committed_bill_artifacts():
    art = _artifact()
    per = B.periods()
    for side, spec in (("base", B.BASE), ("current", B.CURRENT)):
        want = per[(spec["statement"], spec["period"])]
        got = art["periods"][side]
        for field in ("days", "generation_provider", "net_kwh", "gross_kwh",
                      "current_charges"):
            assert got[field] == want[field], (side, field, got[field], want[field])
        led = art["period_ledgers"][side]
        assert abs(led["total_usd"] - want["current_charges"]) < CENT, (side, led)
        assert abs(sum(led["terms"].values()) - want["current_charges"]) < CENT, led
    # and the one substantive fact about the base bill: banked NEM credits cancelled
    # the whole energy charge, so the $48.25 IS the fixed and non-bypassable block
    b = art["period_ledgers"]["base"]["terms"]
    assert abs(b["energy_delivery"] + b["energy_supply"]
               + b["applied_nem_generation_credit"]) < CENT, b
    assert abs(b["fixed_charge"] + b["non_bypassable"]
               - art["periods"]["base"]["current_charges"]) < CENT, b
    return ("both period ledgers tie to data/bill_periods_electric.csv to the cent, "
            "and the base bill's $48.25 is exactly its fixed + non-bypassable block "
            "because banked NEM credits cancelled all of its energy")


def case_the_artifact_labels_its_confidence_and_its_limits():
    c = _artifact()["confidence"]
    assert c["label"] == "measured", c
    assert "not_measured" in c and c["not_measured"], c
    assert "5/25/24" in c["not_measured"] or "5/25/2024" in c["not_measured"], c
    return ("the artifact is labelled measured and states the limit: no bill-derived "
            "price change exists before the corpus starts")


def case_the_generator_reproduces_the_committed_artifact():
    if not B.ELEC_DIR.exists() or not B.HISTORY_CSV.exists():
        raise SkipCase("regeneration needs the private archive (the bill PDF corpus and "
                       "the billing-history export)")
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        B.write(tmp)
        first = (tmp / "bill_decomposition.json").read_bytes()
        B.write(tmp)
        second = (tmp / "bill_decomposition.json").read_bytes()
        assert first == second, "two runs of the generator differ"
        assert first == ARTIFACT.read_bytes(), (
            "the generator does not reproduce data/bill_decomposition.json "
            "byte-for-byte")
    return "the generator is deterministic and reproduces the committed artifact exactly"


CASES = [
    case_charge_lines_accepts_a_minus_sign_printed_before_the_dollar_sign,
    case_the_real_corpus_no_longer_drops_a_negative_pcia_line,
    case_the_billing_mode_question_is_answered_from_statement_text,
    case_an_accruing_statement_needs_its_deferral_sentence,
    case_a_payable_statement_must_prove_it_is_the_annual_settlement,
    case_the_billing_mode_counts_come_from_the_validated_rows,
    case_the_export_is_reconciled_for_every_statement,
    case_the_mode_change_is_presentation_not_billing,
    case_an_interleaved_ledger_presentation_cannot_be_called_one_changeover,
    case_a_statement_missing_from_the_corpus_fails_closed,
    case_a_settlement_non_price_refuses_every_arithmetic_use,
    case_an_import_cell_priced_at_zero_is_refused_as_a_tariff,
    case_the_whole_change_reconciles_within_a_dollar,
    case_every_published_identity_holds_exactly,
    case_the_decomposition_is_per_cell_not_only_aggregate,
    case_the_published_index_covers_only_the_priced_cells,
    case_the_energy_change_is_priced_cells_plus_settlement,
    case_a_flipped_cell_is_outside_every_index_term,
    case_the_quantity_split_prices_no_kwh_at_a_settlement_zero,
    case_price_only_movement_makes_the_two_readings_agree,
    case_quantity_only_movement_makes_the_two_readings_agree,
    case_the_interaction_term_is_the_spread_between_the_two_readings,
    case_an_inconsistent_cell_breaks_the_identity_check,
    case_the_published_reading_allocates_none_of_the_interaction,
    case_provider_and_vintage_are_reported_as_separate_terms,
    case_the_provider_comparison_publishes_two_scopes,
    case_the_printed_bundled_comparison_is_never_priced_as_supply,
    case_a_current_export_cell_cannot_enter_the_provider_comparison,
    case_a_provider_effect_is_refused_without_a_same_date_comparison,
    case_no_per_year_price_figure_is_published,
    case_the_like_for_like_index_is_fixed_weight,
    case_the_artifact_agrees_with_the_committed_bill_artifacts,
    case_the_artifact_labels_its_confidence_and_its_limits,
    case_the_generator_reproduces_the_committed_artifact,
]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
