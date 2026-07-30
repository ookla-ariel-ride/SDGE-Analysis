#!/usr/bin/env python3
"""Guards for the year-over-year bill decomposition in bill_decomposition.py.

MOST OF THIS SUITE RUNS IN A CLEAN CHECKOUT. The generator needs the bill PDFs —
the charged CEA per-TOU rates and the billing-mode sentences exist nowhere else —
but its output, data/bill_decomposition.json, is committed, so the properties that
matter are checked against the committed artifact and against the two committed
bill artifacts it must agree with. Only the byte-for-byte regeneration case needs
the private archive, and it says so instead of passing quietly.

WHAT IS BEING GUARDED, and why each case exists:

  THE TRAP. The billing-history export reports current_charges of $0.00 for
  2024-06-27, the base period of this comparison, and for every statement through
  2025-04-02. A decomposition built on that column would compare $0.00 against
  $398.56 and call the difference real. Two cases hold the answer: the mode
  question is answered from statement text with the statements named
  (case_the_billing_mode_question_is_answered_from_statement_text), and every one
  of the export's numbers is explained in terms of the statement's own lines
  (case_the_export_is_reconciled_for_every_statement) — including that the step
  off $0.00 lands on the presentation change and not on any change in billing.

  THE IDENTITIES. Laspeyres, Paasche, interaction, scale and mix are exact
  algebra, so the cases check them as equalities to the cent rather than within a
  band, at the aggregate AND per cell, and check that the per-cell rows add up to
  the aggregate. Three synthetic fixtures pin the behaviour the identities are
  supposed to have: price-only movement makes the two readings agree, so does
  quantity-only movement, and the interaction term is exactly the spread between
  them when both move.

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
"""
import copy
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bill_decomposition as B

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "data" / "bill_decomposition.json"
CENT = 0.005


def _artifact():
    assert ARTIFACT.exists(), f"{ARTIFACT} is not committed"
    return json.loads(ARTIFACT.read_text())


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
    comparison) rates."""
    cells = {}
    for key in B.CELLS:
        q, d, g, s = spec.get(key, (0.0, 0.0, 0.0, 0.0))
        cells[key] = {
            "kwh": q,
            "delivery_usd": B._c(q * d),
            "supply_usd": B._c(q * g),
            "usd": B._c(q * d + q * g),
            "delivery_rate_effective": d,
            "supply_rate_effective": g,
            "rate_effective": d + g,
            "sdge_bundled_comparison_rate": s,
        }
    return {"cells": cells}


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


# ---------------------------------------------------------------------------
# The decomposition identities
# ---------------------------------------------------------------------------
def case_the_whole_change_reconciles_within_a_dollar():
    art = _artifact()
    r = art["reconciliation"]
    p = art["periods"]
    observed = round(p["current"]["current_charges"] - p["base"]["current_charges"], 2)
    assert r["observed_change_usd"] == observed, (r["observed_change_usd"], observed)
    assert abs(r["components_sum_usd"] - r["observed_change_usd"]) <= 1.0, r
    assert abs(r["residual_usd"]) <= 1.0, r
    assert abs(r["energy_change_usd"] + r["non_energy_change_usd"]
               - r["components_sum_usd"]) < CENT, r
    return (f"observed ${observed} = energy ${r['energy_change_usd']} + non-energy "
            f"${r['non_energy_change_usd']}, residual ${r['residual_usd']} "
            f"(tolerance ${r['tolerance_usd']})")


def case_every_published_identity_holds_exactly():
    agg = _artifact()["decomposition"]["aggregate"]
    ids = _artifact()["decomposition"]["identities"]
    d = agg["energy_change_usd"]
    checks = [
        ("L price + L quantity + interaction",
         ids["laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd"], d),
        ("L price + P quantity", ids["laspeyres_price_plus_paasche_quantity_usd"], d),
        ("P price + L quantity", ids["paasche_price_plus_laspeyres_quantity_usd"], d),
        ("scale + mix", ids["scale_plus_mix_usd"], ids["laspeyres_quantity_usd"]),
        ("delivery vintage + supply vintage + provider",
         ids["price_split_sum_usd"], ids["laspeyres_price_usd"]),
    ]
    for name, got, want in checks:
        assert abs(got - want) <= 0.01, f"{name}: {got} != {want}"
    # the published "reading" is one of those identities, stated once
    rd = agg["reading"]
    assert abs(rd["price_usd"] + rd["quantity_usd"] - d) <= 0.01, rd
    assert abs(rd["of_which_scale_usd"] + rd["of_which_tou_mix_usd"]
               - rd["quantity_usd"]) <= 0.02, rd
    assert abs(rd["interaction_usd"] - agg["interaction_usd"]) < CENT
    return (f"all {len(checks)} index identities hold to the cent against an energy "
            f"change of ${d}")


def case_the_decomposition_is_per_cell_not_only_aggregate():
    art = _artifact()["decomposition"]
    cells = art["per_cell"]
    agg = art["aggregate"]
    assert len(cells) == len(B.CELLS), len(cells)
    seen = {(c["season"], c["tou_period"]) for c in cells}
    assert seen == set(B.CELLS), sorted(seen)
    for c in cells:
        for field in ("laspeyres_price_usd", "laspeyres_quantity_usd",
                      "paasche_price_usd", "paasche_quantity_usd", "interaction_usd",
                      "delivery_vintage_usd_paasche", "supply_vintage_usd_paasche",
                      "provider_usd_paasche", "base_rate_effective",
                      "current_rate_effective",
                      "sdge_bundled_comparison_rate_current_date"):
            assert field in c, f"{c['cell']} has no {field}"
        assert abs(c["current_usd"] - c["base_usd"] - c["change_usd"]) < CENT, c
        assert abs(c["laspeyres_price_usd"] + c["laspeyres_quantity_usd"]
                   + c["interaction_usd"] - c["change_usd"]) <= 0.02, c
    for total, field in ((agg["laspeyres"]["price_usd"], "laspeyres_price_usd"),
                         (agg["laspeyres"]["quantity_usd"], "laspeyres_quantity_usd"),
                         (agg["paasche"]["price_usd"], "paasche_price_usd"),
                         (agg["paasche"]["quantity_usd"], "paasche_quantity_usd"),
                         (agg["interaction_usd"], "interaction_usd")):
        got = round(sum(c[field] for c in cells), 2)
        assert abs(got - total) <= 0.02, f"{field}: cells sum to {got} != {total}"
    got = round(sum(c["change_usd"] for c in cells), 2)
    assert abs(got - agg["energy_change_usd"]) <= 0.02, got
    return (f"{len(cells)} season x TOU cells, each carrying its own price, quantity, "
            "interaction and provider/vintage terms, and every one of them sums to "
            "the aggregate")


def case_price_only_movement_makes_the_two_readings_agree():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "super_off_peak"): (400.0, 0.04, 0.06, 0.06)})
    cur = _cells({("summer", "on_peak"): (100.0, 0.25, 0.35, 0.35),
                  ("summer", "super_off_peak"): (400.0, 0.05, 0.07, 0.07)})
    d = B.decompose(base, cur)["aggregate"]
    assert abs(d["laspeyres"]["price_usd"] - d["paasche"]["price_usd"]) < CENT, d
    assert abs(d["interaction_usd"]) < CENT, d
    assert abs(d["laspeyres"]["quantity_usd"]) < CENT, d
    assert abs(d["laspeyres"]["price_usd"] - d["energy_change_usd"]) < CENT, d
    return ("with quantities held fixed the two price readings coincide and the "
            "interaction term is exactly zero")


def case_quantity_only_movement_makes_the_two_readings_agree():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "super_off_peak"): (400.0, 0.04, 0.06, 0.06)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.20, 0.30, 0.30),
                  ("summer", "super_off_peak"): (350.0, 0.04, 0.06, 0.06)})
    d = B.decompose(base, cur)["aggregate"]
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
    d = B.decompose(base, cur)["aggregate"]
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


# ---------------------------------------------------------------------------
# Provider vs vintage, and the CCA authority boundary
# ---------------------------------------------------------------------------
def case_provider_and_vintage_are_reported_as_separate_terms():
    agg = _artifact()["decomposition"]["aggregate"]
    for basis in ("price_split_laspeyres_basis", "price_split_paasche_basis"):
        s = agg[basis]
        assert set(s) == {"delivery_vintage_usd", "supply_vintage_usd", "provider_usd"}, s
    lasp = agg["price_split_laspeyres_basis"]
    assert abs(sum(lasp.values()) - agg["laspeyres"]["price_usd"]) <= 0.02, lasp
    paas = agg["price_split_paasche_basis"]
    assert abs(sum(paas.values()) - agg["paasche"]["price_usd"]) <= 0.02, paas
    whole = agg["provider_effect_read_whole"]
    assert abs(whole["total_paid_for_supply_usd"]
               - whole["cea_charged_supply_usd"]
               - whole["cea_product_adders_usd"]
               - whole["cca_unbundling_riders_usd"]) < CENT, whole
    assert abs(whole["provider_effect_usd"]
               - (whole["total_paid_for_supply_usd"]
                  - whole["sdge_bundled_same_date_counterfactual_usd"])) < CENT, whole
    assert "bundled PCIA charge" in whole["why_the_riders_belong_on_this_side"], whole
    # the vintage term is same-provider-two-dates; the provider term is
    # two-providers-same-date. Per cell, the two must be built from three distinct
    # rates, or the split is decorative.
    for c in _artifact()["decomposition"]["per_cell"]:
        if not c["both_periods_net_import"]:
            continue
        rates = (c["base_supply_rate"], c["sdge_bundled_comparison_rate_current_date"],
                 c["current_supply_rate"])
        assert len(set(rates)) == 3, f"{c['cell']} supply rates collapse: {rates}"
    return ("provider and vintage are separate published terms that sum to the price "
            f"effect on both weight bases; read whole the provider effect is "
            f"${whole['provider_effect_usd']} ({whole['provider_effect_pct']}%)")


def case_the_printed_bundled_comparison_is_never_priced_as_supply():
    art = _artifact()
    cur = art["period_ledgers"]["current"]["terms"]
    assert cur["printed_bundled_comparison_net_of_its_credit"] == 0.0, cur
    whole = art["decomposition"]["aggregate"]["provider_effect_read_whole"]
    counter = whole["sdge_bundled_same_date_counterfactual_usd"]
    assert counter > 0, counter
    assert abs(cur["energy_supply"] - counter) > 1.0, (cur["energy_supply"], counter)
    bridge = {t["term"]: t for t in art["non_energy_bridge"]}
    assert bridge["printed_bundled_comparison_net_of_its_credit"]["change_usd"] == 0.0
    for c in art["decomposition"]["per_cell"]:
        if c["current_kwh"] == 0:
            continue
        assert c["current_supply_rate"] != \
            c["sdge_bundled_comparison_rate_current_date"], c["cell"]
    return (f"the printed bundled comparison (${counter}) is carried only as a pair "
            f"that nets to $0 and as the same-date counterfactual; the supply dollars "
            f"are the CEA page's ${cur['energy_supply']}")


def case_a_provider_effect_is_refused_without_a_same_date_comparison():
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30)})
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
    cur["cells"][("summer", "on_peak")]["sdge_bundled_comparison_rate"] = None
    _raises(lambda: B.decompose(base, cur), "summer", "on_peak",
            "refusing to estimate")
    return ("with no same-date bundled comparison for a cell the split is refused, "
            "naming the cell, rather than an estimated provider effect being invented")


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
    flipped = {c["cell"] for c in art["decomposition"]["per_cell"]
               if not c["both_periods_net_import"]}
    assert excluded == flipped, (excluded, flipped)
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
    for basis in ("laspeyres", "paasche"):
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
        return ("SKIP regeneration needs the private archive (the bill PDF corpus and "
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
    case_the_billing_mode_question_is_answered_from_statement_text,
    case_the_export_is_reconciled_for_every_statement,
    case_the_mode_change_is_presentation_not_billing,
    case_the_whole_change_reconciles_within_a_dollar,
    case_every_published_identity_holds_exactly,
    case_the_decomposition_is_per_cell_not_only_aggregate,
    case_price_only_movement_makes_the_two_readings_agree,
    case_quantity_only_movement_makes_the_two_readings_agree,
    case_the_interaction_term_is_the_spread_between_the_two_readings,
    case_an_inconsistent_cell_breaks_the_identity_check,
    case_provider_and_vintage_are_reported_as_separate_terms,
    case_the_printed_bundled_comparison_is_never_priced_as_supply,
    case_a_provider_effect_is_refused_without_a_same_date_comparison,
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
            if msg.startswith("SKIP"):
                print(f"SKIP  {msg[5:]}")
                skipped += 1
            else:
                print(f"PASS  {msg}")
                ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
