#!/usr/bin/env python3
"""Behavioural tests for cca_bundled_counterfactual.py (issue #11, Phase 2).

Three kinds of case, matching test_cca_rate_extraction.py's own convention:
  * cases that read only the COMMITTED, PUBLIC artifacts (data/cca_generation_rates.csv,
    data/bill_periods_electric.csv, data/bill_tou_detail.csv) and the rates_history engine
    run unconditionally, no private archive required -- these exercise the numeric core
    (Direction A, Direction B, the provider/vintage split, the reconciliation) and are the
    primary correctness checks this phase asked for.
  * cases that need the REAL statement PDFs (the PCIA/franchise-fee bill-line citations, and
    byte-identical regeneration of the full artifact, which includes those citations) gate on
    _require_archive() and SKIP rather than fail when this checkout lacks private/.
  * fail-closed cases that inject synthetic data (a monkeypatched DATA directory, or injected
    statement text under CX._TEXT_CACHE) to exercise refusal paths without needing the real
    corpus or the private archive at all.

Run from the repo root:  ./.venv/bin/python analysis/test_cca_bundled_counterfactual.py
"""
import csv
import glob
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cca_bundled_counterfactual as CX  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ELEC_GLOB = str(ROOT / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_*.pdf")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no private
    statement-PDF archive). Counted as neither pass nor fail."""


def _require_archive():
    files = [p for p in glob.glob(ELEC_GLOB) if pathlib.Path(p).is_file()]
    if not files:
        raise SkipCase(f"needs the private statement-PDF archive ({ELEC_GLOB}), "
                       "which this checkout does not have")
    return files


EPS = 0.02


# ---------------------------------------------------------------------------
# (a) numeric core -- committed artifacts + rates_history engine only, no PDFs
# ---------------------------------------------------------------------------
@case
def case_direction_a_priced_cells_are_exactly_the_net_import_cells():
    """Every row Direction A prices must be a net import (kWh > 0); every row it excludes
    (whichever reason) must be a net export. A row on the wrong side of this line would mean
    a Settlement-shaped bug -- pricing a deferred true-up bucket, or dropping a real charge."""
    a = CX.direction_a(CX.cca_period_cells())
    bad_priced = [r for r in a["priced_detail"] if r["kwh"] <= 0]
    bad_absent = [r for r in a["excluded_absent_detail"] if r["kwh"] > 0]
    bad_carried = [r for r in a["excluded_carried_export_detail"] if r["kwh"] > 0]
    assert not bad_priced, f"priced a net-export cell: {bad_priced}"
    assert not bad_absent, f"excluded-absent a net-import cell: {bad_absent}"
    assert not bad_carried, f"excluded-carried-export a net-import cell: {bad_carried}"
    total = a["priced_cells"] + a["excluded_absent_cells"] + a["excluded_carried_export_cells"]
    return (f"{a['priced_cells']} priced, {a['excluded_absent_cells']} excluded-absent, "
           f"{a['excluded_carried_export_cells']} excluded-carried-export, {total} total, "
           "every row on the correct side of the kWh>0 line")


@case
def case_direction_b_priced_cells_are_exactly_the_net_import_cells():
    b = CX.direction_b(CX.cca_flat_rate())
    bad_priced = [r for r in b["priced_detail"] if r["kwh"] <= 0]
    bad_excl = [r for r in b["excluded_detail"] if r["kwh"] > 0]
    assert not bad_priced, f"priced a net-export bundled cell: {bad_priced}"
    assert not bad_excl, f"excluded a net-import bundled cell: {bad_excl}"
    return f"{b['priced_cells']} priced, {b['excluded_net_export_cells']} excluded, all correctly gated"


@case
def case_direction_a_cca_charged_equals_the_sum_of_its_own_priced_rows():
    """The headline cca_charged_usd / bundled_comparison_usd totals must equal the sum of
    the per-row detail this same function returns -- catches a future refactor that changes
    the aggregate without keeping the detail in sync."""
    a = CX.direction_a(CX.cca_period_cells())
    cca_sum = round(sum(r["cca_usd"] for r in a["priced_detail"]), 2)
    bundled_sum = round(sum(r["bundled_comparison_usd"] for r in a["priced_detail"]), 2)
    assert abs(cca_sum - a["cca_charged_usd"]) < EPS, (cca_sum, a["cca_charged_usd"])
    assert abs(bundled_sum - a["bundled_comparison_usd"]) < EPS, (bundled_sum, a["bundled_comparison_usd"])
    assert abs((cca_sum - bundled_sum) - a["delta_usd"]) < EPS
    return f"cca={cca_sum} bundled={bundled_sum} delta={a['delta_usd']}, all reconcile"


@case
def case_direction_b_actual_equals_the_sum_of_its_own_priced_rows():
    b = CX.direction_b(CX.cca_flat_rate())
    actual_sum = round(sum(r["actual_bundled_usd"] for r in b["priced_detail"]), 2)
    hyp_sum = round(sum(r["hypothetical_cca_usd"] for r in b["priced_detail"]), 2)
    assert abs(actual_sum - b["actual_bundled_usd"]) < EPS
    assert abs(hyp_sum - b["hypothetical_cca_usd"]) < EPS
    return f"actual={actual_sum} hypothetical={hyp_sum}"


@case
def case_cca_flat_rate_matches_phase1s_committed_six_values():
    """Cross-checks Phase 1's own headline finding (case_cca_rate_is_flat_across_the_whole_corpus
    in test_cca_rate_extraction.py) rather than assuming it: the six season x TOU rates this
    script reads must equal the values that module's docstring and test both record."""
    flat = CX.cca_flat_rate()
    expected = {
        ("winter", "on_peak"): 0.2443, ("winter", "off_peak"): 0.15782,
        ("winter", "super_off_peak"): 0.05187, ("summer", "on_peak"): 0.51684,
        ("summer", "off_peak"): 0.15975, ("summer", "super_off_peak"): 0.04961,
    }
    assert flat == expected, f"{flat} != {expected}"
    return f"all 6 cells match Phase 1: {flat}"


@case
def case_provider_plus_vintage_equals_total_change_over_the_shared_subset():
    """The algebraic identity provider_vs_vintage() itself enforces (and would raise on):
    re-asserted here as an independent check so a future refactor that quietly loosens the
    tolerance inside that function still gets caught."""
    a = CX.direction_a(CX.cca_period_cells())
    pv = CX.provider_vs_vintage(a)
    ic = pv["identity_check"]
    residual = ic["shared_subset_total_change_usd"] - (
        ic["shared_subset_vintage_usd"] + ic["shared_subset_provider_usd"])
    assert abs(residual) < EPS, f"identity residual {residual} too large"
    assert pv["cca_side_vintage_effect_usd"] == 0.0
    return (f"provider {ic['shared_subset_provider_usd']} + vintage "
           f"{ic['shared_subset_vintage_usd']} = total {ic['shared_subset_total_change_usd']}")


@case
def case_two_cells_have_no_vintage_baseline_and_are_named():
    """summer/off_peak and winter/off_peak have no bundled-charged observation anywhere in
    the bundled era (a real, disclosed data gap, not a bug) -- every affected row must be
    exactly one of those two cells."""
    a = CX.direction_a(CX.cca_period_cells())
    pv = CX.provider_vs_vintage(a)
    cells = {(r["season"], r["tou_period"]) for r in pv["cells_with_no_vintage_baseline"]}
    assert cells == {("summer", "off_peak"), ("winter", "off_peak")}, cells
    assert pv["g0_bundled_charged_rate"]["summer/off_peak"] is None
    assert pv["g0_bundled_charged_rate"]["winter/off_peak"] is None
    return f"{len(pv['cells_with_no_vintage_baseline'])} rows, both cells named, no others"


@case
def case_annualization_is_365_25_days_over_the_whole_sample():
    delta, days = 100.0, 200
    got = CX._annualize(delta, days)
    want = round(delta / days * 365.25, 2)
    assert got == want, (got, want)
    return f"$100 over 200 days annualizes to ${got}/yr"


@case
def case_reconciliation_disagrees_by_well_over_20pct_and_does_not_average():
    """The real finding this phase exists to report: Direction A and Direction B disagree by
    a wide margin, and reconciliation() must name the reason rather than average the two."""
    a = CX.direction_a(CX.cca_period_cells())
    b = CX.direction_b(CX.cca_flat_rate())
    pv = CX.provider_vs_vintage(a)
    recon = CX.reconciliation(a, b, pv)
    assert recon["agree_within_20pct"] is False, recon
    assert abs(recon["pct_difference_b_vs_a"]) > 20.0
    assert recon["explanation_if_disagreeing"] is not None
    assert "vintage" in recon["explanation_if_disagreeing"].lower()
    assert "average" not in recon["explanation_if_disagreeing"].lower() or \
        "not averaged" in recon["explanation_if_disagreeing"].lower()
    return (f"disagree by {recon['pct_difference_b_vs_a']}%, explanation names the vintage "
           "effect and states the two figures are not averaged")


@case
def case_reconciliation_does_not_overclaim_full_vintage_attribution():
    """Codex review (issue #11), defect 1: an earlier version of this text asserted, as a
    flat unqualified fact, "The gap is a rate-vintage effect, not a disagreement about the
    provider comparison." That overclaimed a causal attribution the script never actually
    proved -- the $177.09 vintage-effect figure is computed exclusively from Direction A's
    own sample/weights, and Direction A and Direction B also differ in period count, season
    composition, usage weights, and included cells, none of which has been separately
    decomposed from the vintage effect. This asserts the CURRENT text: (a) never states the
    unqualified old claim, (b) explicitly names that sample composition differs and has not
    been separately decomposed, and (c) still says plainly that the vintage effect itself is
    real/verified and the likely major driver -- hedged, not hand-waved."""
    a = CX.direction_a(CX.cca_period_cells())
    b = CX.direction_b(CX.cca_flat_rate())
    pv = CX.provider_vs_vintage(a)
    recon = CX.reconciliation(a, b, pv)
    text = recon["explanation_if_disagreeing"]
    assert text is not None
    assert "The gap is a rate-vintage effect, not a disagreement" not in text, (
        "the old unqualified causal claim must not reappear")
    assert "NOT RULED OUT" in text or "not ruled out" in text.lower()
    assert "none of this has been separately decomposed" in text.lower(), (
        "the text must acknowledge the A-vs-B sample-composition differences have not been "
        "separately decomposed from the vintage effect")
    assert "has not been performed" in text.lower(), (
        "the text must state plainly that a rigorous A-vs-B decomposition has not been done")
    assert "different periods" in text.lower() and "different seasonal composition" in text.lower()
    assert "NOT PROVEN to be fully attributable" in text or \
        "not proven to be fully attributable" in text.lower()
    # Still says plainly what IS known -- this is a hedge, not a retraction.
    assert "REAL" in text and "LARGE" in text and "LIKELY MAJOR" in text
    return "reconciliation text hedges full vintage attribution while still naming it as verified and likely dominant"


@case
def case_both_directions_agree_on_sign_cca_costs_more():
    """A weaker but real corroboration: whatever the disagreement in magnitude, both
    directions land on the SAME side (CCA costing more than bundled would have) -- if they
    ever disagreed in sign that would be a much bigger story than a vintage-mixing artifact."""
    a = CX.direction_a(CX.cca_period_cells())
    b = CX.direction_b(CX.cca_flat_rate())
    assert a["delta_usd"] > 0, a["delta_usd"]
    assert b["delta_usd"] > 0, b["delta_usd"]
    return f"both directions: CCA cost more (A=${a['delta_usd']}, B=${b['delta_usd']})"


@case
def case_rep_date_picks_the_matching_season_half_of_a_split_period():
    """A period spanning the May/June season boundary (5/29/25 - 6/26/25): the WINTER cell's
    representative date must be the period START (winter still in force on 5/29), the SUMMER
    cell's must be the period END (summer took over by the end of the period)."""
    import datetime as dt
    start, end = dt.date(2025, 5, 29), dt.date(2025, 6, 26)
    assert CX._rep_date("winter", start, end) == start
    assert CX._rep_date("summer", start, end) == end
    return "winter->period start, summer->period end, across a May/June boundary period"


@case
def case_not_determined_is_populated_and_committed_json_agrees():
    out = json.loads((CX.DATA / "cca_bundled_counterfactual.json").read_text())
    assert len(out["not_determined"]) >= 3, out["not_determined"]
    assert any("189.93" in s or "franchise" in s.lower() for s in out["not_determined"])
    return f"{len(out['not_determined'])} disclosed ambiguities in the committed artifact"


@case
def case_recommendation_headline_is_direction_a_not_an_average():
    out = json.loads((CX.DATA / "cca_bundled_counterfactual.json").read_text())
    rec = out["recommendation"]
    assert rec["headline_basis"] == "direction_a (modeled -- same-date bill rates)"
    assert rec["confidence"] == "modeled"
    assert rec["annual_dollar_result_usd"] == out["direction_a_cca_repriced_at_bundled"]["delta_usd_per_year"]
    return f"headline ${rec['annual_dollar_result_usd']}/yr, confidence={rec['confidence']}"


@case
def case_direction_a_confidence_is_modeled_not_measured():
    """Regression guard (issue #11, second Codex review, confirmed). Direction A combines
    this household's own MEASURED per-cell kWh with SDG&E's own real, same-date PRINTED
    bundled-generation comparison rate -- both inputs are genuine bill-sourced figures -- but
    multiplying them produces a COMPUTED dollar total for a bundled arrangement the household
    never actually had here. Nobody ever billed this household this number; it is this
    script's own reconstruction. CLAUDE.md's `measured` tier means an observed fact (a meter
    reading, an actual bill line), not a computed counterfactual built from real inputs, so
    this must be labeled `modeled` -- never the literal string `measured` -- no matter how
    strong the inputs are (an earlier version of this script mislabeled it `measured`; this
    case, and the byte-identity gate on the committed JSON, are what would catch that
    regression recurring). Checked positively (matches the specific new label) rather than
    only negatively (isn't the old one), and checked against BOTH the live function output
    and the committed artifact, plus the recommendation that inherits the same basis, so a
    future edit that fixes one and not the others still fails this case."""
    a = CX.direction_a(CX.cca_period_cells())
    assert a["confidence"] == "modeled", a["confidence"]
    assert a["confidence"] != "measured"
    assert "confidence_detail" in a and a["confidence_detail"], \
        "expected a confidence_detail explaining the measured-inputs vs modeled-output nuance"
    detail_upper = a["confidence_detail"].upper()
    assert "MEASURED" in detail_upper and "MODELED" in detail_upper, (
        "the detail should explain the measured-INPUTS vs modeled-OUTPUT distinction, not "
        "just assert a bare label")
    # Direction B is untouched by this fix -- still modeled, for its own, different reason
    # (CEA never served this household in 2024 at all).
    b = CX.direction_b(CX.cca_flat_rate())
    assert b["confidence"] == "modeled"

    out = json.loads((CX.DATA / "cca_bundled_counterfactual.json").read_text())
    da = out["direction_a_cca_repriced_at_bundled"]
    rec = out["recommendation"]
    assert da["confidence"] == "modeled"
    assert out["direction_b_bundled_repriced_at_cca"]["confidence"] == "modeled"
    assert rec["confidence"] == "modeled"
    assert rec["headline_basis"] != "direction_a (measured)"
    assert "modeled" in rec["headline_basis"].lower()
    # The dollar figures themselves must be untouched by a labeling fix.
    assert da["delta_usd"] == 74.07, da["delta_usd"]
    assert da["delta_usd_per_year"] == 49.46, da["delta_usd_per_year"]
    return (f"direction_a confidence={a['confidence']!r} (was 'measured'), "
           f"recommendation confidence={rec['confidence']!r}, headline_basis="
           f"{rec['headline_basis']!r}, dollar figures unchanged")


# ---------------------------------------------------------------------------
# (a2) Codex review (issue #11) regression cases -- defect 1 (excluded net-export scope)
# and defect 2 (segment-level bundled-comparison pricing)
# ---------------------------------------------------------------------------
@case
def case_direction_a_excluded_net_export_credit_is_live_and_material():
    """Defect 1: the excluded net-export CCA credit must be a LIVE sum over the same
    committed CSV every other Direction-A figure reads (never a hardcoded literal), must
    equal the sum of cca_usd across BOTH excluded categories, and must be disclosed as
    materially LARGER in magnitude than the priced-cell headline it sits beside -- that is
    the whole point of the disclosure, not an incidental fact."""
    a = CX.direction_a(CX.cca_period_cells())
    expected = round(sum(r["cca_usd"] for r in
                          a["excluded_absent_detail"] + a["excluded_carried_export_detail"]), 2)
    assert abs(a["excluded_net_export_cca_credit_usd"] - expected) < EPS, \
        (a["excluded_net_export_cca_credit_usd"], expected)
    assert a["excluded_net_export_cells"] == \
        a["excluded_absent_cells"] + a["excluded_carried_export_cells"]
    assert a["excluded_net_export_cca_credit_usd"] < 0, "a net-export bucket is a credit"
    assert abs(a["excluded_net_export_cca_credit_usd"]) > abs(a["delta_usd"]), (
        "the excluded export-side effect must be disclosed as larger than the priced "
        "headline, not a footnote to it")
    assert "NOT DETERMINED" in a["excluded_net_export_note"]
    return (f"excluded net-export credit ${a['excluded_net_export_cca_credit_usd']} over "
           f"{a['excluded_net_export_cells']} cells, live-summed and larger in magnitude "
           f"than the ${a['delta_usd']} priced-cell delta")


@case
def case_direction_b_excluded_net_export_hypothetical_is_live():
    """The Direction B analogue: a live-summed, modeled (not measured) hypothetical
    credit over the excluded net-export cells, disclosed the same way."""
    b = CX.direction_b(CX.cca_flat_rate())
    expected = round(sum(r["hypothetical_cca_usd"] for r in b["excluded_detail"]), 2)
    assert abs(b["excluded_net_export_hypothetical_cca_credit_usd"] - expected) < EPS, \
        (b["excluded_net_export_hypothetical_cca_credit_usd"], expected)
    assert b["excluded_net_export_hypothetical_cca_credit_usd"] < 0
    assert "excluded_net_export_note" in b and "MODELED" in b["excluded_net_export_note"]
    return (f"Direction B excluded hypothetical credit "
           f"${b['excluded_net_export_hypothetical_cca_credit_usd']}, live-summed and "
           "labeled modeled")


@case
def case_recommendation_discloses_excluded_scope_and_does_not_overclaim():
    """Defect 1's actual fix: the recommendation must (a) name the excluded cell count
    and dollar magnitude, (b) say the whole-household answer is NOT FULLY DETERMINED, and
    (c) no longer contain the old unqualified whole-household assertion this defect
    report flagged ("does not by itself justify [opting out]") -- that phrasing implied
    the priced-cell figure settled a question it does not, on its own, settle."""
    out = json.loads((CX.DATA / "cca_bundled_counterfactual.json").read_text())
    rec = out["recommendation"]
    a = out["direction_a_cca_repriced_at_bundled"]
    text = rec["text"]
    assert "NOT FULLY DETERMINED" in text
    assert str(a["excluded_net_export_cells"]) in text, \
        "the excluded cell count must be named in the recommendation, not just the artifact"
    assert "-378" in text or "-$378" in text, \
        "the excluded dollar magnitude must be named in the recommendation text"
    assert "ONLY on the net-import cells" in text
    assert "does not by itself justify" not in text, \
        "the old overclaiming phrase must not reappear"
    assert "scope_caveat" in rec and "NOT FULLY DETERMINED" in rec["scope_caveat"]
    return "recommendation names the excluded scope/magnitude and drops the old overclaiming phrase"


@case
def case_segment_level_pricing_matches_naive_for_every_single_segment_priced_row():
    """Defect 2, the no-regression half: for the overwhelming majority of priced cells,
    whose printed bundled-comparison table did NOT change mid-cycle, the new segment-level
    pricing must reproduce the OLD one-representative-date product exactly -- the fix must
    be a no-op wherever there was nothing to fix."""
    cca_cells = CX.cca_period_cells()
    a = CX.direction_a(cca_cells)
    periods = {p["period"]: p for p in CX.periods_by_provider()["CCA"]}
    checked = 0
    for row in a["priced_detail"]:
        if row["bundled_comparison_n_segments"] != 1:
            continue
        p = periods[row["period"]]
        rep_date = CX._rep_date(row["season"], p["start"], p["end"])
        naive_rate = CX.RH.rates_on(rep_date).generation_comparison_table(
            row["season"], row["tou_period"])
        naive_usd = round(row["kwh"] * naive_rate, 2)
        assert abs(naive_usd - row["bundled_comparison_usd"]) < EPS, (row, naive_usd)
        checked += 1
    assert checked > 40, f"expected most of the 50 priced rows to be single-segment, got {checked}"
    return (f"{checked} single-segment priced rows: segment-level pricing == the old "
           "representative-date pricing, to the cent")


@case
def case_segment_level_pricing_fixes_the_real_mid_period_rate_change_period():
    """Defect 2, the real regression: 1/28/25 - 2/26/25 is a real CCA period on file whose
    printed bundled-generation comparison table changed mid-cycle (data/bill_tou_detail.csv
    prints segment 0 at 4 days and segment 1 at 26 days for this period's generation
    section, at two different rates for winter/on_peak). The old approach (one
    representative date's rate times the whole period's kWh) and the fixed approach
    (each segment's own kWh at its own rate) must give DIFFERENT numbers here -- if they
    ever agreed, the fix would not be exercised by this period at all."""
    period = "1/28/25 - 2/26/25"
    season, tou = "winter", "on_peak"
    p = next(pp for pp in CX.periods_by_provider()["CCA"] if pp["period"] == period)
    cca_cells = CX.cca_period_cells()
    c = cca_cells[(p["statement_date"], p["period"])][(season, tou)]
    assert c["kwh"] > 0, "this cell must be a priced (net-import) cell"

    rep_date = CX._rep_date(season, p["start"], p["end"])
    naive_rate = CX.RH.rates_on(rep_date).generation_comparison_table(season, tou)
    naive_usd = round(c["kwh"] * naive_rate, 2)

    a = CX.direction_a(cca_cells)
    row = next(r for r in a["priced_detail"] if r["period"] == period
               and r["season"] == season and r["tou_period"] == tou)
    assert row["bundled_comparison_n_segments"] == 2, (
        f"expected this real period to carry 2 printed rate segments, got "
        f"{row['bundled_comparison_n_segments']}")
    fixed_usd = row["bundled_comparison_usd"]
    assert abs(fixed_usd - naive_usd) > 0.10, (
        f"expected the segment-level fix to change this period's priced value; naive="
        f"{naive_usd} fixed={fixed_usd}")
    return (f"{period} {season}/{tou}: naive representative-date pricing = ${naive_usd}, "
           f"segment-level pricing = ${fixed_usd} (${round(fixed_usd - naive_usd, 2)} "
           "different) on a real, on-file mid-period rate change")


# ---------------------------------------------------------------------------
# (a3) Codex review (issue #11) regression cases -- defect 2 (a period or cell silently
# missing from the CCA/bundled cell mapping must fail closed, never price as zero rows)
# ---------------------------------------------------------------------------
@case
def case_direction_a_refuses_a_period_missing_entirely_from_cca_cells():
    """Defect 2: if data/cca_generation_rates.csv were ever partially regenerated and
    silently dropped an entire CCA period, direction_a's own cca_cells.get(key, {}) used to
    swallow it as zero rows with no error, while n_periods/days (read from
    bill_periods_electric.csv, untouched here) would still report full coverage. Deletes one
    real period's entry from the live cca_cells mapping and confirms direction_a() now
    refuses, naming the missing period."""
    cca_cells = CX.cca_period_cells()
    missing_key = next(iter(cca_cells))
    del cca_cells[missing_key]
    try:
        CX.direction_a(cca_cells)
    except SystemExit as e:
        msg = str(e)
        assert "no rows at all" in msg, msg
        assert missing_key[1] in msg, (missing_key, msg)
        return (f"direction_a refuses a wholly missing CCA period ({missing_key[1]!r}), "
               "named in the error")
    else:
        raise AssertionError("direction_a silently priced a period missing from cca_cells")


@case
def case_direction_a_refuses_a_period_missing_one_expected_cell():
    """Defect 2, the narrower case: a period IS present in cca_cells but is missing one of
    its structurally-expected (season, TOU) cells (a hand-edited or corrupted CSV row
    dropped). Removes exactly one cell from one real period's entry and confirms
    direction_a() names that specific missing cell rather than silently pricing the rest."""
    cca_cells = CX.cca_period_cells()
    key = next(k for k, cells in cca_cells.items() if len(cells) >= 3)
    modified = dict(cca_cells)
    inner = dict(cca_cells[key])
    removed_cell = next(iter(inner))
    del inner[removed_cell]
    modified[key] = inner
    try:
        CX.direction_a(modified)
    except SystemExit as e:
        msg = str(e)
        assert "missing" in msg.lower(), msg
        assert str(removed_cell) in msg, (removed_cell, msg)
        return f"direction_a refuses a period missing {removed_cell}, named in the error"
    else:
        raise AssertionError("direction_a silently priced a period missing one expected cell")


@case
def case_direction_a_real_corpus_passes_the_coverage_gate_cleanly():
    """No false positives: the real, complete data/cca_generation_rates.csv must pass the
    new coverage gate without raising, and must still price the full 19-period, 66-cell
    corpus exactly as before the gate was added."""
    a = CX.direction_a(CX.cca_period_cells())
    assert a["n_periods"] == 19, a["n_periods"]
    total = a["priced_cells"] + a["excluded_absent_cells"] + a["excluded_carried_export_cells"]
    assert total == 66, total
    return f"real corpus passes the coverage gate cleanly: {a['n_periods']} periods, {total} cells"


@case
def case_bundled_generation_cells_refuses_a_period_missing_entirely():
    """Defect 2 sweep -- Direction B's analogous check: a bundled period
    bill_periods_electric.csv defines, but with zero generation-section rows in
    bill_tou_detail.csv, must refuse rather than silently contribute zero dollars while its
    days still count toward full coverage."""
    tmp = tempfile.mkdtemp()
    try:
        pdir = pathlib.Path(tmp)
        with open(pdir / "bill_periods_electric.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "days", "generation_provider", "net_kwh",
                       "gross_kwh", "sdge_delivery", "cca_generation", "current_charges",
                       "base_services_charge", "monthly_service_fee", "fixed_charge_total"])
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "31", "bundled", "10", "10",
                       "5.00", "0.0", "5.00", "", "16.0", "16.0"])
            w.writerow(["2099-03-01", "2/1/99 - 2/28/99", "28", "CCA", "10", "10",
                       "5.00", "3.00", "8.00", "16.0", "", "16.0"])
        with open(pdir / "bill_tou_detail.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "section", "season", "segment",
                       "segment_days", "tou_period", "kwh", "rate_per_kwh"])
            # No generation-section rows at all for the bundled period above.
        orig = CX.DATA
        CX.DATA = pdir
        try:
            CX.bundled_generation_cells()
        except SystemExit as e:
            assert "no rows at all" in str(e), e
            assert "1/1/99 - 1/31/99" in str(e), e
            return "bundled_generation_cells refuses a bundled period with no generation rows at all"
        else:
            raise AssertionError("bundled_generation_cells silently accepted a wholly missing period")
        finally:
            CX.DATA = orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case
def case_bundled_generation_cells_refuses_a_period_missing_one_cell():
    """Defect 2 sweep, narrower case: the bundled period has SOME generation rows but is
    missing one of its three expected TOU cells for the season it spans."""
    tmp = tempfile.mkdtemp()
    try:
        pdir = pathlib.Path(tmp)
        with open(pdir / "bill_periods_electric.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "days", "generation_provider", "net_kwh",
                       "gross_kwh", "sdge_delivery", "cca_generation", "current_charges",
                       "base_services_charge", "monthly_service_fee", "fixed_charge_total"])
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "31", "bundled", "10", "10",
                       "5.00", "0.0", "5.00", "", "16.0", "16.0"])
            w.writerow(["2099-03-01", "2/1/99 - 2/28/99", "28", "CCA", "10", "10",
                       "5.00", "3.00", "8.00", "16.0", "", "16.0"])
        with open(pdir / "bill_tou_detail.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "section", "season", "segment",
                       "segment_days", "tou_period", "kwh", "rate_per_kwh"])
            # winter/on_peak and winter/off_peak present, winter/super_off_peak missing.
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "generation", "winter", "0",
                       "31", "on_peak", "5.0", "0.30000"])
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "generation", "winter", "0",
                       "31", "off_peak", "5.0", "0.20000"])
        orig = CX.DATA
        CX.DATA = pdir
        try:
            CX.bundled_generation_cells()
        except SystemExit as e:
            assert "missing" in str(e).lower(), e
            assert "super_off_peak" in str(e), e
            return "bundled_generation_cells refuses a period missing one expected TOU cell"
        else:
            raise AssertionError("bundled_generation_cells silently accepted a partial cell set")
        finally:
            CX.DATA = orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# (b) fail-closed: synthetic data, no archive needed
# ---------------------------------------------------------------------------
@case
def case_annualize_refuses_a_non_positive_day_count():
    try:
        CX._annualize(10.0, 0)
    except SystemExit as e:
        assert "annualize" in str(e)
        return "_annualize refuses a zero-day sample"
    else:
        raise AssertionError("_annualize accepted a zero-day sample")


@case
def case_cca_flat_rate_refuses_a_non_flat_corpus():
    """Injects a synthetic data/cca_generation_rates.csv with two different rates for the
    same (season, TOU) cell -- Direction B's single-rate assumption must refuse rather than
    silently pick one or average them."""
    tmp = tempfile.mkdtemp()
    try:
        header = ["statement_date", "period", "season", "tou_period", "kwh",
                  "rate_usd_per_kwh", "usd", "provider", "authority", "evidence",
                  "source_pdf", "note"]
        rows = []
        for season in ("winter", "summer"):
            for tou in ("on_peak", "off_peak", "super_off_peak"):
                rate = "0.20000" if not (season == "summer" and tou == "on_peak") else "0.20000"
                rows.append(["2099-01-01", "p1", season, tou, "10.0", rate, "2.00",
                            "CCA", "charged_tariff", "direct", "x.pdf", ""])
        # break flatness on exactly one cell
        rows.append(["2099-02-01", "p2", "summer", "on_peak", "10.0", "0.30000", "3.00",
                    "CCA", "charged_tariff", "direct", "x.pdf", ""])
        with open(pathlib.Path(tmp) / "cca_generation_rates.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        orig = CX.DATA
        CX.DATA = pathlib.Path(tmp)
        try:
            CX.cca_flat_rate()
        except SystemExit as e:
            assert "not flat" in str(e), e
            return "cca_flat_rate refuses a corpus where a cell's rate is not flat"
        else:
            raise AssertionError("cca_flat_rate accepted a non-flat corpus")
        finally:
            CX.DATA = orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case
def case_bundled_generation_cells_refuses_a_nonzero_rate_on_a_net_export_row():
    """Injects a synthetic bill_tou_detail.csv where a net-export (kWh<=0) generation row
    carries a nonzero printed rate -- the credit-lines-print-zero invariant this script
    relies on (and re-checks rather than assumes) must refuse."""
    tmp = tempfile.mkdtemp()
    try:
        pdir = pathlib.Path(tmp)
        with open(pdir / "bill_periods_electric.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "days", "generation_provider", "net_kwh",
                       "gross_kwh", "sdge_delivery", "cca_generation", "current_charges",
                       "base_services_charge", "monthly_service_fee", "fixed_charge_total"])
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "31", "bundled", "10", "10",
                       "5.00", "0.0", "5.00", "", "16.0", "16.0"])
            # periods_by_provider() requires both providers present -- a CCA row is
            # irrelevant to the invariant under test but required for that gate to pass.
            w.writerow(["2099-03-01", "2/1/99 - 2/28/99", "28", "CCA", "10", "10",
                       "5.00", "3.00", "8.00", "16.0", "", "16.0"])
        with open(pdir / "bill_tou_detail.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["statement_date", "period", "section", "season", "segment",
                       "segment_days", "tou_period", "kwh", "rate_per_kwh"])
            w.writerow(["2099-01-01", "1/1/99 - 1/31/99", "generation", "winter", "0",
                       "31", "off_peak", "-5.0", "0.05000"])
        orig = CX.DATA
        CX.DATA = pdir
        try:
            CX.bundled_generation_cells()
        except SystemExit as e:
            assert "nonzero printed rate" in str(e), e
            return "bundled_generation_cells refuses a net-export row with a nonzero rate"
        else:
            raise AssertionError("bundled_generation_cells accepted a broken invariant")
        finally:
            CX.DATA = orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@case
def case_missing_bundled_pcia_sentence_raises_systemexit():
    """Injects synthetic text under BASE_STMT's cache key with no PCIA sentence -- the AC3
    claim must refuse rather than publish on an assumption. Restores whatever the real cache
    held (or clears it) so later cases see the real statement again."""
    had_real = CX.BASE_STMT in CX._TEXT_CACHE
    saved = CX._TEXT_CACHE.get(CX.BASE_STMT)
    CX._TEXT_CACHE[CX.BASE_STMT] = "This statement has no PCIA sentence anywhere on it."
    try:
        CX.pcia_and_franchise_evidence()
    except SystemExit as e:
        assert "bundled-PCIA sentence" in str(e), e
        return "pcia_and_franchise_evidence refuses a base statement with no PCIA sentence"
    else:
        raise AssertionError("pcia_and_franchise_evidence accepted a statement with no PCIA sentence")
    finally:
        if had_real:
            CX._TEXT_CACHE[CX.BASE_STMT] = saved
        else:
            CX._TEXT_CACHE.pop(CX.BASE_STMT, None)


@case
def case_missing_franchise_line_raises_systemexit():
    had_real = CX.BASE_STMT in CX._TEXT_CACHE
    saved = CX._TEXT_CACHE.get(CX.BASE_STMT)
    CX._TEXT_CACHE[CX.BASE_STMT] = (
        "$1.97 of your Electricity Generation Charge is your bundled PCIA charge. "
        "No franchise fee line appears anywhere on this synthetic statement.")
    try:
        CX._franchise_line(CX.BASE_STMT)
    except SystemExit as e:
        assert "franchise-fee line" in str(e), e
        return "_franchise_line refuses a statement with no franchise-fee line"
    else:
        raise AssertionError("_franchise_line accepted a statement with no franchise-fee line")
    finally:
        if had_real:
            CX._TEXT_CACHE[CX.BASE_STMT] = saved
        else:
            CX._TEXT_CACHE.pop(CX.BASE_STMT, None)


# ---------------------------------------------------------------------------
# (c) needs the real PDF archive
# ---------------------------------------------------------------------------
@case
def case_bundled_pcia_sentence_matches_the_real_base_statement():
    _require_archive()
    CX._TEXT_CACHE.pop(CX.BASE_STMT, None)
    CX._TEXT_CACHE.pop(CX.CURRENT_STMT, None)
    ev = CX.pcia_and_franchise_evidence()
    quoted = ev["pcia"]["bundled_pcia_sentence_quoted"]
    assert "bundled PCIA charge" in quoted, quoted
    assert ev["pcia"]["bundled_pcia_usd_this_statement"] == 1.97, ev["pcia"]
    return f"real base statement quotes: {quoted!r}"


@case
def case_franchise_fee_bases_differ_between_the_two_eras_on_real_statements():
    _require_archive()
    CX._TEXT_CACHE.pop(CX.BASE_STMT, None)
    CX._TEXT_CACHE.pop(CX.CURRENT_STMT, None)
    ev = CX.pcia_and_franchise_evidence()
    ff = ev["franchise_fee"]
    assert ff["bundled"]["base_usd"] < ff["cca"]["base_usd"], ff
    assert ff["bundled"]["label"] != ff["cca"]["label"]
    return (f"bundled base ${ff['bundled']['base_usd']} ({ff['bundled']['label']!r}) vs "
           f"CCA base ${ff['cca']['base_usd']} ({ff['cca']['label']!r})")


@case
def case_full_artifact_regenerates_byte_identically():
    _require_archive()
    committed = (CX.DATA / "cca_bundled_counterfactual.json").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = CX.write(dest_dir=tmp)
        regenerated = path.read_bytes()
    assert regenerated == committed, "data/cca_bundled_counterfactual.json is not reproducible"
    return "data/cca_bundled_counterfactual.json regenerates byte-identically"


def main():
    listed = [c.__name__ for c in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
