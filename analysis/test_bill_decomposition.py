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

  ALGEBRA THAT BALANCES IS NOT ALGEBRA THAT MEANS SOMETHING — the two cases below
  exist because both failure modes reconcile perfectly while saying the wrong
  thing.

  A VINTAGE TERM NEEDS A BASE TARIFF. On a cell billed as a net export the base
  effective price is $0 because the energy settled at the annual true-up, not
  because a tariff said $0. Running q(s1 - g0) there prices the export-to-import
  regime change and publishes it as a supply vintage, and every identity still
  holds. case_the_vintage_and_provider_terms_cover_only_the_cells_that_support_
  them asserts the attributed terms are built from the import-in-both cells alone
  and that the rest is carried under its own name, netting_regime_usd;
  case_a_flipped_cell_cannot_be_attributed_to_vintage_or_provider drives the same
  rule from a synthetic pair where the exporting cell would otherwise have
  contributed a large, entirely spurious supply vintage.

  AND THE SAME RULE ONE LEVEL UP. The whole-period provider figure is the same
  arithmetic over the whole statement, so the same $0 gets in the same way: on a
  cell billed as a net export in the CURRENT period SDG&E's printed bundled
  comparison is $0 by deferred settlement while the CCA still books a credit
  there. case_a_current_export_cell_cannot_enter_the_whole_provider_comparison
  drives it synthetically — the export cell's difference must land in
  unallocated_netting_settlement_usd and must not move the provider effect — and
  case_the_published_provider_effect_is_restricted_to_current_imports holds the
  committed artifact and index.html to it. Two more cases close the remaining
  routes for a settlement $0 to be read as a price:
  case_an_import_cell_priced_at_zero_is_refused_as_a_tariff asserts an
  import-in-both cell whose base or comparison rate prints $0 is refused rather
  than attributed, and case_the_quantity_split_names_its_settlement_zero_base_
  prices asserts the scale/mix split names the cells whose base price is a
  settlement $0 and the kWh it therefore values at zero.

  A TRANSFORMATION OF TWO POINTS IS NOT A RATE. Two matched endpoints with no
  comparable pair between them cannot establish an annual price path, and "per
  year" reads as an observed yearly change.
  case_no_per_year_price_figure_is_published asserts the index publishes total
  changes only, carries the two-endpoint limit in machine-readable form, and that
  the report never restates the Fisher reading as an annual rate.

  A POINT IS NOT A BOUND. Paasche price == Laspeyres price + interaction, so
  pairing Paasche price with Laspeyres quantity and calling the price figure "the"
  price effect hands the whole interaction to price while the note beside it says
  the interaction is not allocated. case_the_published_reading_allocates_none_of_
  the_interaction asserts the artifact publishes intervals whose width is exactly
  the interaction, publishes both exact pairings, publishes no bare price_usd or
  quantity_usd, and that index.html carries the bounds and no point attribution.

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
            "sdge_bundled_comparison_usd": B._c(q * s),
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
    # the published "reading" states both exact pairings, and each one sums to the
    # energy change with no residual
    rd = agg["reading"]
    pairs = {(p["price_basis"], p["quantity_basis"]): p for p in rd["exact_pairings"]}
    assert set(pairs) == {("laspeyres", "paasche"), ("paasche", "laspeyres")}, pairs
    for key, p in pairs.items():
        assert abs(p["price_usd"] + p["quantity_usd"] - p["sum_usd"]) < CENT, p
        assert abs(p["sum_usd"] - d) <= 0.01, (key, p, d)
    assert abs(rd["of_which_scale_usd"] + rd["of_which_tou_mix_usd"]
               - agg["laspeyres"]["quantity_usd"]) <= 0.02, rd
    assert abs(rd["interaction_usd"] - agg["interaction_usd"]) < CENT
    return (f"all {len(checks)} index identities hold to the cent against an energy "
            f"change of ${d}, and both exact pairings are published")


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
                      "provider_usd_paasche", "netting_regime_usd_paasche",
                      "netting_regime_usd_laspeyres", "price_split_attributed",
                      "base_rate_effective", "current_rate_effective",
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
        assert set(s) == {"delivery_vintage_usd", "supply_vintage_usd", "provider_usd",
                          "netting_regime_usd"}, s
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


def case_the_vintage_and_provider_terms_cover_only_the_cells_that_support_them():
    """A vintage term is a difference of two tariffs. On a cell billed as a net export
    the base effective price is $0 because the energy settled at the annual true-up,
    not because a tariff said $0 — so q(s1 - g0) there would price the export-to-import
    regime change and publish it as a supply vintage, with the algebra still balancing.
    The attributed terms must therefore be built from the like-for-like cells alone,
    with the rest carried under its own name."""
    art = _artifact()
    agg = art["decomposition"]["aggregate"]
    cells = art["decomposition"]["per_cell"]
    scope = agg["price_split_scope"]
    like = {c["cell"] for c in cells if c["both_periods_net_import"]}
    flip = {c["cell"] for c in cells if not c["both_periods_net_import"]}
    assert like and flip, "the corpus no longer has both kinds of cell"
    assert set(scope["attributed_cells"]) == like, (scope["attributed_cells"], like)
    assert set(scope["unattributed_cells"]) == flip, (scope["unattributed_cells"], flip)
    assert scope["unattributed_term"] == "netting_regime_usd", scope
    # per cell: an unattributed cell contributes NOTHING to vintage or provider, and
    # an attributed cell contributes nothing to the netting-regime term
    for c in cells:
        vint = ("delivery_vintage_usd", "supply_vintage_usd", "provider_usd")
        for basis in ("laspeyres", "paasche"):
            attributed = [c[f"{t.replace('_usd', '')}_usd_{basis}"] for t in vint]
            regime = c[f"netting_regime_usd_{basis}"]
            if c["both_periods_net_import"]:
                assert regime == 0.0, (c["cell"], basis, regime)
                assert abs(sum(attributed)
                           - c[f"{basis}_price_usd"]) <= 0.02, c["cell"]
            else:
                assert attributed == [0.0, 0.0, 0.0], (c["cell"], basis, attributed)
                assert abs(regime - c[f"{basis}_price_usd"]) <= 0.02, (c["cell"], basis)
    # aggregate: all four terms still sum exactly to the price effect on each basis
    for basis, total in (("laspeyres", agg["laspeyres"]["price_usd"]),
                         ("paasche", agg["paasche"]["price_usd"])):
        s = agg[f"price_split_{basis}_basis"]
        assert abs(sum(s.values()) - total) <= 0.02, (basis, s, total)
        attributed = round(s["delivery_vintage_usd"] + s["supply_vintage_usd"]
                           + s["provider_usd"], 2)
        want = round(sum(c[f"{basis}_price_usd"] for c in cells
                         if c["both_periods_net_import"]), 2)
        assert abs(attributed - want) <= 0.02, (basis, attributed, want)
        # and they are the same dollars the like-for-like section publishes
        lfl = art["like_for_like"]["price_effect_split_usd"][basis]
        assert abs(sum(lfl.values()) - attributed) <= 0.02, (basis, lfl, attributed)
    # the regime term is not a rounding crumb — this is the term the old all-cell
    # split was silently folding into "vintage"
    assert abs(agg["price_split_paasche_basis"]["netting_regime_usd"]) > 10.0, agg
    return ("the vintage and provider terms are built from the "
            f"{len(like)} like-for-like cells only; the other {len(flip)} carry "
            "$%.2f (Laspeyres) / $%.2f (Paasche) under netting_regime_usd, and all "
            "four terms still sum to the price effect on each basis"
            % (agg["price_split_laspeyres_basis"]["netting_regime_usd"],
               agg["price_split_paasche_basis"]["netting_regime_usd"]))


def case_a_flipped_cell_cannot_be_attributed_to_vintage_or_provider():
    """The same rule, driven synthetically: a cell that exports in the base period and
    imports in the current one must land wholly in the netting-regime term whatever
    the printed rates say, while a cell that imports in both keeps its split."""
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30),
                   ("summer", "off_peak"): (-50.0, 0.0, 0.0, 0.0)})
    cur = _cells({("summer", "on_peak"): (120.0, 0.25, 0.40, 0.35),
                  ("summer", "off_peak"): (80.0, 0.22, 0.33, 0.31)})
    agg = B.decompose(base, cur)["aggregate"]
    for basis in ("laspeyres", "paasche"):
        s = agg[f"price_split_{basis}_basis"]
        assert abs(sum(s.values()) - agg[basis]["price_usd"]) < CENT, (basis, s)
        assert s["netting_regime_usd"] != 0.0, (basis, s)
    scope = agg["price_split_scope"]
    assert scope["attributed_cells"] == ["summer.on_peak"], scope
    assert "summer.off_peak" in scope["unattributed_cells"], scope
    assert set(scope["attributed_cells"]) | set(scope["unattributed_cells"]) == \
        {B._key(*k) for k in B.CELLS}, scope
    # the exporting cell's supply move (0.00 -> 0.33 against a 0.31 comparison) would
    # have read as +$24.75 of supply vintage on current weights if it were attributed
    assert agg["price_split_paasche_basis"]["supply_vintage_usd"] == \
        B._c(120.0 * (0.35 - 0.30)), agg["price_split_paasche_basis"]
    # every attributed dollar comes from the import-in-both cell alone
    attributed = sum(agg["price_split_paasche_basis"][t] for t in
                     ("delivery_vintage_usd", "supply_vintage_usd", "provider_usd"))
    assert abs(attributed - 120.0 * ((0.25 + 0.40) - (0.20 + 0.30))) < CENT, attributed
    return ("a cell that flips from net export to net import contributes nothing to "
            "the vintage or provider terms and all of its price movement to "
            "netting_regime, while the import-in-both cell keeps its three-way split")


def case_the_published_reading_allocates_none_of_the_interaction():
    """The artifact says the interaction is published and not allocated. The reading it
    prints has to match that claim: pairing Paasche price with Laspeyres quantity and
    calling the price figure "the" price effect would hand the whole interaction to
    price, because Paasche price == Laspeyres price + interaction."""
    agg = _artifact()["decomposition"]["aggregate"]
    rd = agg["reading"]
    assert rd["convention"].startswith("bounds"), rd["convention"]
    lp, pp = agg["laspeyres"]["price_usd"], agg["paasche"]["price_usd"]
    lq, pq = agg["laspeyres"]["quantity_usd"], agg["paasche"]["quantity_usd"]
    inter = agg["interaction_usd"]
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
    # and the prose says so, in the artifact and in the report
    assert "interval" in rd["basis"], rd["basis"]
    assert "accounts" in rd["basis"] and "no figure" in rd["basis"], rd["basis"]
    assert "never split" in rd["interaction_note"] or \
        "never allocated" in rd["interaction_note"], rd["interaction_note"]
    report = (ROOT / "index.html").read_text()
    assert "price accounts for" not in report, \
        "index.html still states a point price attribution"
    for end in (rd["price_usd_low"], rd["price_usd_high"],
                rd["quantity_usd_low"], rd["quantity_usd_high"]):
        assert f"{abs(end):,.2f}" in report, \
            f"index.html omits the published bound {end}"
    return (f"price and quantity are published as intervals (price "
            f"${rd['price_usd_low']} to ${rd['price_usd_high']}, quantity "
            f"${rd['quantity_usd_low']} to ${rd['quantity_usd_high']}), each exactly "
            f"${abs(inter)} wide because that is the unallocated interaction, and the "
            "report states no point attribution either")


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


def case_a_current_export_cell_cannot_enter_the_whole_provider_comparison():
    """The per-cell split refuses a settlement $0; the WHOLE-period provider figure has
    to refuse it too, or the same defect just moves up a level. On a cell billed as a
    net export in the current period SDG&E's printed bundled comparison is $0 because
    the export settled at the annual true-up — it is not a bundled tariff of zero —
    while the CCA still books a credit against that cell. Netting the two publishes a
    settlement outcome as a provider PRICE effect, and the arithmetic balances while
    doing it.

    Driven synthetically: one import cell that supports the comparison, one export cell
    that does not. The export cell's −$2.00 must not appear in the provider effect."""
    cells = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.25),
                    ("summer", "off_peak"): (-40.0, 0.0, 0.05, 0.0)})["cells"]
    w = B.provider_effect_whole_period(cells, 1.0, 10.0)
    assert w["scope"]["attributed_cells"] == ["summer.on_peak"], w["scope"]
    assert "summer.off_peak" in w["scope"]["excluded_cells"], w["scope"]
    assert set(w["scope"]["attributed_cells"]) | set(w["scope"]["excluded_cells"]) == \
        {B._key(*k) for k in B.CELLS}, w["scope"]
    # the attributed side is the import cell alone: CEA $30 + $1 adders + $10 riders
    # against $25 of printed bundled supply
    assert w["cea_charged_supply_usd"] == 30.0, w
    assert w["sdge_bundled_same_date_counterfactual_usd"] == 25.0, w
    assert w["total_paid_for_supply_usd"] == 41.0, w
    assert w["provider_effect_usd"] == 16.0, w
    # the export cell's difference is published, separately and under its own name
    assert w["unallocated_netting_settlement_usd"] == -2.0, w
    assert "settlement" in w["unallocated_netting_settlement_note"]
    # and NOT folded into the provider effect: unrestricted it would have read $14.00,
    # $2.00 of which is the export cell's settlement rather than any price
    assert w["all_cells_including_settlement_difference_usd"] == 14.0, w
    assert w["provider_effect_usd"] != w["all_cells_including_settlement_difference_usd"]
    for r in w["per_cell"]:
        if not r["current_net_import"]:
            assert r["sdge_bundled_same_date_usd"] == 0.0, r
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
    return ("a cell billed as a net export in the current period cannot enter the "
            "whole-period provider comparison: its $0 printed counterfactual is deferred "
            "settlement, so its −$2.00 is published as unallocated netting/settlement "
            "instead of moving the $16.00 provider effect to $14.00")


def case_the_published_provider_effect_is_restricted_to_current_imports():
    """The committed artifact, against the same rule."""
    art = _artifact()
    w = art["decomposition"]["aggregate"]["provider_effect_read_whole"]
    cells = art["decomposition"]["per_cell"]
    imports = [c["cell"] for c in cells if c["current_net_import"]]
    exports = [c["cell"] for c in cells if not c["current_net_import"]]
    assert imports and exports, "the corpus no longer has both kinds of current cell"
    assert w["scope"]["attributed_cells"] == imports, (w["scope"], imports)
    assert w["scope"]["excluded_cells"] == exports, (w["scope"], exports)
    rows = {r["cell"]: r for r in w["per_cell"]}
    assert set(rows) == {c["cell"] for c in cells}, sorted(rows)
    for cell in exports:
        assert rows[cell]["sdge_bundled_same_date_usd"] == 0.0, rows[cell]
        assert rows[cell]["current_net_import"] is False, rows[cell]
    assert abs(sum(rows[c]["cea_charged_usd"] for c in imports)
               - w["cea_charged_supply_usd"]) < CENT, w
    assert abs(sum(rows[c]["sdge_bundled_same_date_usd"] for c in imports)
               - w["sdge_bundled_same_date_counterfactual_usd"]) < CENT, w
    assert abs(w["provider_effect_usd"]
               - (w["total_paid_for_supply_usd"]
                  - w["sdge_bundled_same_date_counterfactual_usd"])) < CENT, w
    # the excluded difference is published, and the two sides recompose to the
    # unrestricted figure — so the restriction is auditable rather than a quiet trim
    assert abs(w["unallocated_netting_settlement_usd"]
               - sum(rows[c]["difference_usd"] for c in exports)) < CENT, w
    assert abs(w["provider_effect_usd"] + w["unallocated_netting_settlement_usd"]
               - w["all_cells_including_settlement_difference_usd"]) < CENT, w
    # the restriction actually bites: the attributed CEA supply is NOT the period's
    # whole supply line, and the published effect is not the unrestricted one
    supply = art["period_ledgers"]["current"]["terms"]["energy_supply"]
    assert abs(w["cea_charged_supply_usd"] - supply) > CENT, (w, supply)
    assert w["provider_effect_usd"] != \
        w["all_cells_including_settlement_difference_usd"], w
    assert w["unallocated_netting_settlement_usd"] != 0.0, w
    # and the report quotes the restricted figure, with the excluded amount stated
    report = (ROOT / "index.html").read_text()
    assert f"${w['total_paid_for_supply_usd']:,.2f}" in report, \
        "index.html does not quote the restricted CEA-side total"
    assert f"${w['provider_effect_usd']:,.2f}" in report, \
        "index.html does not quote the restricted provider effect"
    assert f"{abs(w['unallocated_netting_settlement_usd']):,.2f}" in report, \
        "index.html does not state the excluded settlement amount"
    assert f"${w['all_cells_including_settlement_difference_usd']:,.2f}" not in report, \
        "index.html still quotes the unrestricted provider figure"
    return (f"the whole-period provider effect covers the {len(imports)} cells billed as "
            f"net imports in 2026 (${w['provider_effect_usd']}, "
            f"{w['provider_effect_pct']}%), with the export cell's "
            f"${w['unallocated_netting_settlement_usd']} published separately as "
            "unallocated netting/settlement")


def case_an_import_cell_priced_at_zero_is_refused_as_a_tariff():
    """The third place the settlement $0 could enter: a cell that IS a net import in
    both periods but whose base or comparison rate still prints $0. Attributing it
    would build a vintage or a provider term on a $0 that is not a tariff, and
    like_for_like would then divide by it."""
    base = _cells({("summer", "on_peak"): (100.0, 0.20, 0.30, 0.30)})
    for field, needle in (("supply_rate_effective", "base supply rate"),
                          ("delivery_rate_effective", "base delivery rate")):
        bad = copy.deepcopy(base)
        bad["cells"][("summer", "on_peak")][field] = 0.0
        cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
        _raises(lambda: B.decompose(bad, cur), needle, "is not a tariff")
    cur = _cells({("summer", "on_peak"): (150.0, 0.25, 0.35, 0.35)})
    cur["cells"][("summer", "on_peak")]["sdge_bundled_comparison_rate"] = 0.0
    _raises(lambda: B.decompose(base, cur), "same-date bundled comparison rate",
            "is not a tariff")
    # the real artifact carries no such cell
    for c in _artifact()["decomposition"]["per_cell"]:
        if not c["both_periods_net_import"]:
            continue
        for f in ("base_delivery_rate", "base_supply_rate",
                  "sdge_bundled_comparison_rate_current_date"):
            assert c[f] != 0.0, (c["cell"], f)
    return ("an import-in-both cell whose base delivery, base supply or same-date "
            "comparison rate is $0 is refused rather than attributed — on an import "
            "cell a $0 is a settlement or parsing artefact, not a tariff")


def case_the_quantity_split_names_its_settlement_zero_base_prices():
    """scale and mix split the LASPEYRES end of the quantity interval, so they value
    every kWh at the base effective price — which on a base-export cell is $0 by
    settlement. The identity is right; the artifact has to say which cells it prices
    at zero so the low end is not read as "that energy was worth nothing"."""
    art = _artifact()
    agg = art["decomposition"]["aggregate"]
    split = agg["quantity_split_laspeyres_basis"]
    cells = art["decomposition"]["per_cell"]
    zero = [c["cell"] for c in cells if not c["base_net_import"]]
    assert zero, "no base-export cell — the disclosure has nothing to guard"
    assert split["cells_priced_at_a_settlement_zero_base_rate"] == zero, split
    want = round(sum(c["current_kwh"] - c["base_kwh"] for c in cells
                     if not c["base_net_import"]), 1)
    assert abs(split["net_kwh_change_valued_at_zero"] - want) < 0.05, split
    assert "settled at the annual true-up" in split["settlement_zero_caveat"], split
    assert "not a statement that the" in split["settlement_zero_caveat"], split
    # every named cell really is priced at zero in the base period
    by = {c["cell"]: c for c in cells}
    for cell in zero:
        assert by[cell]["base_rate_effective"] == 0.0, by[cell]
        assert by[cell]["laspeyres_quantity_usd"] == 0.0, by[cell]
    # and the identity still holds: scale + mix == the Laspeyres quantity term
    assert abs(split["scale_usd"] + split["tou_mix_usd"]
               - agg["laspeyres"]["quantity_usd"]) <= 0.02, split
    return (f"the scale/mix split names the {len(zero)} cell(s) it prices at a "
            f"settlement $0 base rate and the {split['net_kwh_change_valued_at_zero']} "
            "kWh of net swing it therefore values at zero, and still sums to the "
            "Laspeyres quantity term")


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
    report = (ROOT / "index.html").read_text()
    fisher = f"{abs(idx['fisher_pct']):.1f}%"
    assert fisher in report, f"index.html omits the published Fisher reading {fisher}"
    for bad in (f"{fisher}/yr", f"{fisher} per year", f"{fisher} a year",
                f"{fisher}/year"):
        assert bad not in report, f"index.html states {bad!r} as an annual rate"
    return (f"the price index publishes total changes over {idx['years_apart']} years "
            "and no per-year rate, with the two-endpoint limit stated in the artifact")


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
    case_the_vintage_and_provider_terms_cover_only_the_cells_that_support_them,
    case_a_flipped_cell_cannot_be_attributed_to_vintage_or_provider,
    case_the_published_reading_allocates_none_of_the_interaction,
    case_the_printed_bundled_comparison_is_never_priced_as_supply,
    case_a_current_export_cell_cannot_enter_the_whole_provider_comparison,
    case_the_published_provider_effect_is_restricted_to_current_imports,
    case_an_import_cell_priced_at_zero_is_refused_as_a_tariff,
    case_the_quantity_split_names_its_settlement_zero_base_prices,
    case_no_per_year_price_figure_is_published,
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
