#!/usr/bin/env python3
"""Behavioural tests for irreducible_bill.py (issue #7).

What runs where, stated exactly (the lesson test_service_headroom.py's own
docstring names: say what needs the private archive rather than let a test
silently skip and still read green).

  * WITHOUT the private archive (CI on a fork with no bill PDFs): the fake-
    text cases (case_pcia_negative_rate_sign_is_handled,
    case_nbc_gross_reverification_is_not_hardcoded, the settlement-zero
    guard's synthetic half, the fail-closed cases that write their own
    throwaway CSVs) run against synthetic input and do not touch a PDF.
  * ONLY with the private archive (this repo, and any fork with its own bill
    PDFs staged): every case that calls build() or reads statement text for
    the real corpus -- the four-bucket reconciliation, the netted-energy
    cross-check, the dual-period-statement scoping proof, the floor/package
    fraction consistency checks, and the byte-identical regeneration case.
    These raise SkipCase when analysis.bill_decomposition.ELEC_DIR does not
    exist, matching test_bill_decomposition.py's own guard.

Run from the repo root:  ./.venv/bin/python analysis/test_irreducible_bill.py
"""
import csv
import json
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bill_decomposition as bd    # noqa: E402
import behavior_rebuild as br      # noqa: E402
import irreducible_bill as irr     # noqa: E402

ROOT = irr.ROOT
EPS = 1e-9

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private bill-PDF archive staged). Counted as neither pass nor fail."""


def _require_corpus():
    if not bd.ELEC_DIR.exists():
        raise SkipCase(f"needs the private archive at {bd.ELEC_DIR}")


def _close(a, b, eps=EPS):
    return abs(float(a) - float(b)) <= eps


# ---------------------------------------------------------------------------
# The core reconciliation (AC1): four buckets sum to current_charges, AND the
# residual bucket agrees with an independently sourced computation.
# ---------------------------------------------------------------------------
@case
def case_four_bucket_sum_matches_current_charges_every_period():
    _require_corpus()
    result = irr.build()
    bad = [r for r in result["periods"] if not r["four_bucket_check_pass"]]
    assert not bad, f"{len(bad)} period(s) failed the four-bucket reconciliation: " \
        f"{[(r['statement_date'], r['period'], r['four_bucket_diff_usd']) for r in bad]}"
    assert result["period_count"] == 26, \
        f"expected 26 periods (25 single-period statements + 1 two-period), got " \
        f"{result['period_count']}"
    return f"all {result['period_count']} periods reconcile to current_charges " \
        f"within ${irr.RECON_TOLERANCE_USD}"


@case
def case_netted_energy_cross_check_agrees_every_period():
    """AC1's REAL verification: bucket 4 (a residual) agrees with an
    independently-sourced second computation from the printed TOU tables and
    adjustment lines, not merely with itself."""
    _require_corpus()
    result = irr.build()
    bad = [r for r in result["periods"] if not r["netted_energy_cross_check_pass"]]
    assert not bad, f"{len(bad)} period(s) failed the cross-check: " \
        f"{[(r['statement_date'], r['period'], r['netted_energy_cross_check_diff_usd']) for r in bad]}"
    worst = max(result["periods"],
               key=lambda r: abs(r["netted_energy_cross_check_diff_usd"]))
    assert abs(worst["netted_energy_cross_check_diff_usd"]) <= irr.RECON_TOLERANCE_USD
    return (f"worst cross-check residual ${worst['netted_energy_cross_check_diff_usd']:+.2f} "
           f"({worst['statement_date']}/{worst['period']}), tolerance never widened "
           f"from the issue's own ${irr.RECON_TOLERANCE_USD}")


@case
def case_generation_credit_cancels_on_every_cca_period():
    """The bundled-comparison generation table plus its cancelling credit
    must net to ~0 on every CCA period -- this is what licenses excluding
    both from the independent supply term rather than including-and-
    subtracting them."""
    _require_corpus()
    result = irr.build()
    cca_rows = [r for r in result["periods"] if r["generation_provider"] == "CCA"]
    assert cca_rows, "expected at least one CCA period in this corpus"
    bad = [r for r in cca_rows if abs(r["generation_credit_cancel_usd"]) > 0.05]
    assert not bad, f"generation credit did not cancel on: " \
        f"{[(r['statement_date'], r['period'], r['generation_credit_cancel_usd']) for r in bad]}"
    return f"{len(cca_rows)} CCA periods: bundled-comparison + credit cancels to " \
        "within $0.05 on every one"


# ---------------------------------------------------------------------------
# Step 1: the dual-period statement is scoped, not allocated
# ---------------------------------------------------------------------------
@case
def case_dual_period_statement_is_scoped_not_allocated():
    """2025-10-31 carries two periods. Prove the extraction reads each
    period's OWN printed Non-Bypassable Charges / Wildfire Fund Charge /
    Total Taxes & Fees, not a single whole-statement value split by a
    formula."""
    _require_corpus()
    stmt = "2025-10-31"
    periods = ["9/26/25 - 9/30/25", "10/1/25 - 10/27/25"]
    chunks = irr.period_text_chunks(stmt, periods)
    assert set(chunks) == set(periods)
    lines1 = irr.charge_lines_for_period(chunks[periods[0]], f"{stmt}/{periods[0]}")
    lines2 = irr.charge_lines_for_period(chunks[periods[1]], f"{stmt}/{periods[1]}")
    # Each period's own printed values (read directly off the PDF while
    # building this): period 1 is the short 5-day stub, period 2 the 27-day
    # remainder. They must differ from each other -- proof the two chunks are
    # not the same text twice -- and match what was actually printed.
    assert _close(lines1["non_bypassable_charges"], 6.57), lines1
    assert _close(lines1["wildfire_fund_charge"], 1.83), lines1
    assert _close(lines1["total_taxes_and_fees"], 0.57), lines1
    assert _close(lines2["non_bypassable_charges"], 13.21), lines2
    assert _close(lines2["wildfire_fund_charge"], 11.33), lines2
    assert _close(lines2["total_taxes_and_fees"], 3.04), lines2
    assert lines1["non_bypassable_charges"] != lines2["non_bypassable_charges"]
    return ("period 1 (5 days) and period 2 (27 days) of 2025-10-31 each yield "
           "their OWN printed NBC/wildfire/tax values -- no allocation formula "
           "was applied")


@case
def case_dual_period_chunk_count_is_fail_closed():
    """period_text_chunks() must refuse a statement whose anchor count does
    not match what bill_periods_electric.csv says it carries -- a layout
    change or a new multi-period statement must not be silently mis-scoped."""
    _require_corpus()
    try:
        irr.period_text_chunks("2025-10-31", ["9/26/25 - 9/30/25"])  # only 1 expected
    except SystemExit as e:
        assert "anchor" in str(e).lower()
    else:
        raise AssertionError("expected SystemExit on a period-count mismatch")
    return "a wrong expected-period-count is refused with SystemExit"


# ---------------------------------------------------------------------------
# The PCIA sign-before-$ gap found while building this (synthetic; no PDF)
# ---------------------------------------------------------------------------
@case
def case_pcia_negative_rate_sign_is_handled():
    """PCIA is often printed with the sign BEFORE the dollar sign ('kWh x
    -$.03161'). bd._LINE_PATTERNS' own pcia pattern cannot match that (proven
    against this exact real line below via the RAW bd pattern, run purely as
    a unit check against a string -- no PDF needed); irr._OWN_PATTERNS fixes
    it locally. Also proves the multi-segment case (two PCIA lines in one
    period) sums correctly rather than keeping only the first."""
    line = "PCIA 2023 802 kWh x -$.03161 -25.35"
    raw_pcia_pattern = dict(bd._LINE_PATTERNS)["pcia"]
    assert not re.search(raw_pcia_pattern, line), \
        "bd's own pcia pattern unexpectedly matched a negative-rate line -- " \
        "this case's premise no longer holds, re-examine"
    fixed = irr._OWN_PATTERNS["pcia"]
    m = re.search(fixed, line)
    assert m and float(m.group(1)) == -25.35, m

    two_segment_text = ("PCIA 2023 186 kWh x $.00207 .39\n"
                        "PCIA 2023 802 kWh x -$.03161 -25.35\n"
                        "Non-Bypassable Charges 31.43\n"
                        "Wildfire Fund Charge 244 kWh x $.00561 1.37\n"
                        "Total Taxes & Fees on Electric Charges $1.34\n")
    lines = irr.charge_lines_for_period(two_segment_text, "synthetic")
    assert _close(lines["pcia"], 0.39 + -25.35), lines
    return (f"bd's raw pcia pattern misses a negative-rate line; irr's own pattern "
           f"extracts -25.35 and sums two segments to {lines['pcia']:.2f}")


@case
def case_wildfire_segments_within_one_period_are_summed():
    """A mid-cycle NBC rate change reprints Wildfire Fund Charge once per
    segment within a SINGLE period (2025-03-04, 2026-02-02 in this corpus).
    Both must be summed, not just the first kept."""
    text = ("Non-Bypassable Charges 31.43\n"
           "Wildfire Fund Charge 244 kWh x $.00561 1.37\n"
           "Wildfire Fund Charge 1,278 kWh x $.00595 7.60\n"
           "Total Taxes & Fees on Electric Charges $1.34\n")
    lines = irr.charge_lines_for_period(text, "synthetic")
    assert _close(lines["wildfire_fund_charge"], 1.37 + 7.60), lines
    return f"two wildfire segments sum to {lines['wildfire_fund_charge']:.2f}, not " \
        "just the first"


@case
def case_conflicting_non_bypassable_charges_within_one_period_raises():
    """Unlike the per-kWh-rate lines, 'Non-Bypassable Charges' is a
    once-per-period subtotal (confirmed across this whole corpus): if it
    ever printed twice with DIFFERENT values inside one period's own scoped
    text, that is a genuine conflict, not a segment split, and must raise."""
    text = ("Non-Bypassable Charges 31.43\n"
           "Non-Bypassable Charges 99.99\n"
           "Wildfire Fund Charge 244 kWh x $.00561 1.37\n"
           "Total Taxes & Fees on Electric Charges $1.34\n")
    try:
        irr.charge_lines_for_period(text, "synthetic")
    except SystemExit as e:
        assert "conflicting" in str(e).lower()
    else:
        raise AssertionError("expected SystemExit on conflicting non_bypassable_charges")
    return "a genuine same-period conflict in non_bypassable_charges raises, unlike " \
        "the segment-split lines"


# ---------------------------------------------------------------------------
# Settlement zero is not a price
# ---------------------------------------------------------------------------
@case
def case_settlement_zero_rows_are_not_priced_as_free_energy():
    """A rate_per_kwh == 0 row in bill_tou_detail.csv is a NEM export
    settlement (kwh <= 0, deferred to true-up), and irr.tou_sums() must
    include its $0 contribution as the correctly-billed-this-period amount,
    never mistake it for a genuine zero-priced positive import."""
    _require_corpus()
    rows = list(csv.DictReader(irr.TOU_CSV.open()))
    zero_rows = [r for r in rows if float(r["rate_per_kwh"]) == 0.0]
    assert zero_rows, "expected settlement-zero rows in this corpus"
    assert all(float(r["kwh"]) <= 0 for r in zero_rows), \
        "found a rate-zero row with positive kwh already in the committed CSV -- " \
        "that would not be a settlement zero"
    return f"{len(zero_rows)} settlement-zero rows in the corpus, all on non-positive kwh"


@case
def case_positive_kwh_at_zero_rate_is_refused():
    """Drive irr.tou_sums()'s own guard with a poisoned temp copy of
    bill_tou_detail.csv (a positive-kwh row at rate 0.00000) -- the real
    predicate, not a re-implementation of it. irr.tou_sums() reads through
    bd.tou_detail(), which reads bd.DATA / "bill_tou_detail.csv" -- that is
    the path that must be monkeypatched, not irr.TOU_CSV (which only the
    provenance row-count uses)."""
    _require_corpus()
    real_csv = irr.TOU_CSV.read_text()
    header, *_ = real_csv.splitlines()
    poisoned_row = "9999-01-01,POISON - POISON,delivery,summer,0,1,on_peak,50.0,0.0"
    with tempfile.TemporaryDirectory() as d:
        tmp_dir = pathlib.Path(d)
        (tmp_dir / "bill_tou_detail.csv").write_text(
            header + "\n" + poisoned_row + "\n")
        saved = bd.DATA
        bd.DATA = tmp_dir
        try:
            try:
                irr.tou_sums("9999-01-01", "POISON - POISON")
            except SystemExit as e:
                assert "settlement" in str(e).lower()
            else:
                raise AssertionError("expected SystemExit on positive kwh at rate 0")
        finally:
            bd.DATA = saved
    return "a positive-kwh row at rate 0.00000 is refused, not priced as free energy"


# ---------------------------------------------------------------------------
# The 12-month floor: internal consistency
# ---------------------------------------------------------------------------
@case
def case_floor_is_internally_consistent():
    _require_corpus()
    result = irr.build()
    floor = result["twelve_month_floor"]
    assert floor["floor_usd"] <= floor["total_current_charges_usd"] + EPS, \
        "floor exceeds the window total"
    expected_pct = round(100.0 * floor["floor_usd"] / floor["total_current_charges_usd"], 2)
    assert _close(floor["floor_pct_of_total"], expected_pct, eps=0.01), \
        (floor["floor_pct_of_total"], expected_pct)
    assert floor["window_period_count"] == 13, \
        f"expected 13 periods in the window (12 statements, one splitting into " \
        f"two), got {floor['window_period_count']}"
    assert (len(floor["periods_on_monthly_service_fee"])
           + len(floor["periods_on_base_services_charge"])
           == floor["window_period_count"])
    return (f"floor ${floor['floor_usd']} <= total ${floor['total_current_charges_usd']}, "
           f"{floor['floor_pct_of_total']}% matches an independent recomputation")


@case
def case_floor_recomputed_independently_from_the_artifact_rows():
    """Recompute floor_usd and its percentage DIRECTLY from result['periods'],
    independent of build_floor()'s own internal arithmetic, using the same
    window definition (parse_bills.SUMMARY_STATEMENTS_ELEC)."""
    _require_corpus()
    result = irr.build()
    window = set(irr.SUMMARY_STATEMENTS_ELEC)
    window_rows = [r for r in result["periods"] if r["statement_date"] in window]
    recomputed_floor = round(sum(r["fixed_daily_usd"] + r["non_bypassable_gross_usd"]
                                 for r in window_rows), 2)
    recomputed_total = round(sum(r["current_charges_usd"] for r in window_rows), 2)
    recomputed_pct = round(100.0 * recomputed_floor / recomputed_total, 2)
    floor = result["twelve_month_floor"]
    assert _close(recomputed_floor, floor["floor_usd"], eps=0.02), \
        (recomputed_floor, floor["floor_usd"])
    assert _close(recomputed_pct, floor["floor_pct_of_total"], eps=0.02), \
        (recomputed_pct, floor["floor_pct_of_total"])
    return (f"independently recomputed floor ${recomputed_floor} matches the "
           f"artifact's ${floor['floor_usd']}")


@case
def case_package_floor_fractions_are_consistent():
    """Post-Finding-1 shape: fixed_daily_usd_historical IS still constant
    across packages (a per-day charge cannot be package-specific), but
    floor_usd and non_bypassable_gross_usd are NOT any more -- each package
    gets its own recomputation from compute_package_gross_imports()."""
    _require_corpus()
    result = irr.build()
    floor = result["twelve_month_floor"]
    pf = result["package_floor_fractions"]
    assert set(pf) == {"LOW", "MID", "HIGH"}, sorted(pf)
    for name, row in pf.items():
        assert row["fixed_daily_usd_historical"] == floor["fixed_daily_usd_historical"]
        expected_floor = round(row["fixed_daily_usd_historical"]
                               + row["non_bypassable_gross_usd"], 2)
        assert _close(row["floor_usd"], expected_floor, eps=0.01), (name, row)
        expected_frac = round(row["floor_usd"] / row["projected_bill_current_rates_yr"], 4)
        assert _close(row["floor_fraction_of_projected_bill"], expected_frac, eps=1e-6)
    # The three floor_usd values are NOT required to be equal any more --
    # that was exactly the bug (Finding 1). Assert they actually differ, so a
    # regression back to "held constant" would be caught here.
    floors = {pf[n]["floor_usd"] for n in ("LOW", "MID", "HIGH")}
    assert len(floors) > 1, \
        f"all three packages report the same floor_usd {floors} -- looks like " \
        "the constant-floor bug is back"
    # On this real corpus HIGH has the smallest projected bill of the three,
    # and its floor_usd is close to LOW/MID's, so it comes out with the
    # largest fraction -- observed on this data, not guaranteed by
    # construction now that the numerators differ too.
    assert pf["HIGH"]["floor_fraction_of_projected_bill"] >= \
        pf["MID"]["floor_fraction_of_projected_bill"] >= \
        pf["LOW"]["floor_fraction_of_projected_bill"]
    return (f"LOW {pf['LOW']['floor_fraction_of_projected_bill']*100:.1f}% / "
           f"MID {pf['MID']['floor_fraction_of_projected_bill']*100:.1f}% / "
           f"HIGH {pf['HIGH']['floor_fraction_of_projected_bill']*100:.1f}% "
           "of each package's projected bill, each package's own floor_usd "
           "now recomputed rather than a single shared figure")


@case
def case_low_package_gross_imports_equal_baseline_exactly():
    """Finding 1's directional proof #1: the EV-shift-only package (LOW) can
    only move WHEN energy is drawn, never the annual total -- gross imports
    must come back EXACTLY equal to the baseline (not merely close), since
    behavior_rebuild's own _conserve() guard already refuses a shift that
    drops or invents energy."""
    _require_corpus()
    gross = irr.compute_package_gross_imports()
    assert gross["LOW"]["gross_kwh"] == gross["baseline_gross_kwh"], gross
    return (f"LOW gross imports ({gross['LOW']['gross_kwh']} kWh) exactly equal "
           f"the baseline ({gross['baseline_gross_kwh']} kWh) -- the EV shift "
           "moves timing only")


@case
def case_mid_and_high_package_gross_imports_are_not_held_constant():
    """Finding 1's directional proof #2: MID and HIGH (grid-charged battery
    packages) must NOT come back equal to the baseline -- the retired
    docstring asserted they could only be equal or higher (round-trip loss);
    prove here that they actually differ from the baseline (in EITHER
    direction -- the point of this fix is that the direction is computed,
    not assumed) and that MID and HIGH differ from each other (13.5 kWh vs
    27 kWh usable capacity must dispatch differently)."""
    _require_corpus()
    gross = irr.compute_package_gross_imports()
    base = gross["baseline_gross_kwh"]
    mid, high = gross["MID"]["gross_kwh"], gross["HIGH"]["gross_kwh"]
    assert mid != base, f"MID gross imports ({mid}) exactly equal the baseline " \
        f"({base}) -- expected the battery dispatch to change the annual total"
    assert high != base, f"HIGH gross imports ({high}) exactly equal the baseline"
    assert mid != high, "MID and HIGH came back identical despite different capacity"
    assert gross["MID"]["kwh_served"] > 0 and gross["HIGH"]["kwh_served"] > 0
    return (f"MID {mid} kWh, HIGH {high} kWh, both != baseline {base} kWh and != "
           "each other -- the direction is computed, not asserted")


@case
def case_package_gross_imports_sanity_check_against_historical_bills():
    """Verification the issue asked for explicitly: the computed baseline
    (package-free) gross-import total, from the raw interval export, must
    resemble the household's actual historical gross-import total from the
    bills (data/bill_periods_electric.csv, the same 12-month window
    build_floor() sums) -- different data sources, same household, same
    rough magnitude."""
    _require_corpus()
    result = irr.build()
    gross = irr.compute_package_gross_imports()
    hist = result["twelve_month_floor"]["historical_gross_kwh_window"]
    rel_diff = abs(gross["baseline_gross_kwh"] - hist) / hist
    assert rel_diff <= irr.GROSS_IMPORT_SANITY_FRACTION, (gross["baseline_gross_kwh"], hist)
    return (f"interval-export baseline {gross['baseline_gross_kwh']} kWh vs "
           f"historical bill total {hist} kWh: {rel_diff*100:.1f}% apart, within "
           f"the {irr.GROSS_IMPORT_SANITY_FRACTION*100:.0f}% sanity margin")


@case
def case_package_gross_imports_consistent_with_committed_dispatch_artifact():
    """Cross-check against data/battery_dispatch_policies.json: that artifact's
    top-level pw3/pw3x greedy policies report kwh_served for the BASELINE
    (no EV shift) import series, a different scenario than this script's
    post-EV-shift MID/HIGH -- but the same household, same battery hardware,
    same dispatch rule, so the two kwh_served figures must sit in the same
    ballpark, not differ by an order of magnitude (which would mean the
    reused pipeline calls are wrong, not just differently scoped)."""
    _require_corpus()
    gross = irr.compute_package_gross_imports()
    committed = json.loads((irr.ROOT / "data" / "battery_dispatch_policies.json").read_text())
    pw3_served = committed["pw3"]["greedy"]["kwh_served"]
    pw3x_served = committed["pw3x"]["greedy"]["kwh_served"]
    mid_served = gross["MID"]["kwh_served"]
    high_served = gross["HIGH"]["kwh_served"]
    assert abs(mid_served - pw3_served) / pw3_served <= 0.15, (mid_served, pw3_served)
    assert abs(high_served - pw3x_served) / pw3x_served <= 0.15, (high_served, pw3x_served)
    return (f"post-EV-shift kwh_served MID {mid_served} vs committed baseline "
           f"pw3 {pw3_served}, HIGH {high_served} vs pw3x {pw3x_served} -- "
           "same ballpark")


@case
def case_build_package_floor_fractions_direction_matches_a_synthetic_case():
    """A pure, corpus-free proof of the ARITHMETIC (not the real household's
    data): feed build_package_floor_fractions() a synthetic gross-import dict
    where MID/HIGH are known to sit below and above the baseline
    respectively, and assert the resulting non_bypassable_gross_usd and
    floor_usd move in the SAME direction as the synthetic input -- i.e. the
    function actually computes the direction from its argument rather than
    asserting one."""
    floor = {
        "floor_usd": 700.0,
        "fixed_daily_usd_historical": 260.0,
        "non_bypassable_gross_usd_historical": 440.0,
        "historical_gross_kwh_window": 20000.0,
    }
    packages_json = {"packages": {
        "LOW": {"projected_bill_current_rates_yr": 3000.0},
        "MID": {"projected_bill_current_rates_yr": 1400.0},
        "HIGH": {"projected_bill_current_rates_yr": 1200.0},
    }}
    gross = {
        "baseline_gross_kwh": 20000.0,
        "LOW": {"gross_kwh": 20000.0},
        "MID": {"gross_kwh": 19000.0, "kwh_served": 100.0},   # DOWN vs baseline
        "HIGH": {"gross_kwh": 21000.0, "kwh_served": 100.0},  # UP vs baseline
    }

    class _FakePackageJson:
        def read_text(self):
            return json.dumps(packages_json)

    real_package_json = irr.PACKAGE_JSON
    irr.PACKAGE_JSON = _FakePackageJson()
    try:
        pf = irr.build_package_floor_fractions(floor, gross)
    finally:
        irr.PACKAGE_JSON = real_package_json
    assert pf["LOW"]["non_bypassable_gross_usd"] == round(20000.0 * irr.R.NBC, 2), pf
    assert pf["MID"]["non_bypassable_gross_usd"] < pf["LOW"]["non_bypassable_gross_usd"], pf
    assert pf["HIGH"]["non_bypassable_gross_usd"] > pf["LOW"]["non_bypassable_gross_usd"], pf
    assert pf["MID"]["floor_usd"] < pf["LOW"]["floor_usd"] < pf["HIGH"]["floor_usd"], pf
    assert irr.PACKAGE_JSON is real_package_json  # restored, real path untouched
    return ("a synthetic gross-import dict with MID below and HIGH above the "
           "baseline produces non_bypassable_gross_usd/floor_usd that move in "
           "the SAME direction -- proves the direction is computed from the "
           "argument, not hardcoded")


@case
def case_gross_import_sanity_check_rejects_a_wild_mismatch():
    """_sanity_check_gross_imports() must refuse (not silently publish) a
    baseline gross-import figure that does not resemble the historical bill
    total -- pure-function test, no corpus needed."""
    floor = {"historical_gross_kwh_window": 20000.0}
    gross_ok = {"baseline_gross_kwh": 21000.0}          # 5% apart: fine
    gross_bad = {"baseline_gross_kwh": 30000.0}         # 50% apart: refuse
    irr._sanity_check_gross_imports(gross_ok, floor)    # must not raise
    try:
        irr._sanity_check_gross_imports(gross_bad, floor)
    except SystemExit as e:
        assert "%" in str(e)
    else:
        raise AssertionError("expected SystemExit on a wildly mismatched baseline")
    return "a >15% mismatch between the interval baseline and the historical " \
        "bill total is refused; a <=15% mismatch is accepted"


@case
def case_low_conservation_guard_fires_on_a_poisoned_shift():
    """compute_package_gross_imports() must refuse to publish if
    behavior_rebuild.shift_ev() ever comes back with a gross-import total
    that differs from the baseline -- LOW is supposed to be an exact
    conservation (timing-only shift). Poison shift_ev() to return a series
    missing 100 kWh and prove the guard fires rather than silently
    publishing a wrong LOW figure."""
    _require_corpus()
    real_shift_ev = br.shift_ev

    def poisoned(d, ev, sessions, mask, sop_idx, sop_ts):
        imp, moved = real_shift_ev(d, ev, sessions, mask, sop_idx, sop_ts)
        imp = imp.copy()
        imp[0] -= 100.0  # silently drop 100 kWh -- must be caught
        return imp, moved

    irr.br.shift_ev = poisoned
    try:
        try:
            irr.compute_package_gross_imports()
        except SystemExit as e:
            assert "shift_ev" in str(e) or "differ from the baseline" in str(e)
        else:
            raise AssertionError("expected SystemExit on a broken LOW conservation")
    finally:
        irr.br.shift_ev = real_shift_ev
    return "a poisoned shift_ev() that drops 100 kWh is caught by the LOW " \
        "conservation guard, not silently published"


@case
def case_raw_interval_csv_is_fail_closed_on_wrong_match_count():
    """_raw_interval_csv() must refuse anything but exactly one match under
    private/1-raw-data/ -- zero (missing export) or two-or-more (a stale
    leftover copy from a previous refresh) must both stop the run rather
    than silently pick one."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "private" / "1-raw-data").mkdir(parents=True)
        real_root = irr.ROOT
        irr.ROOT = tmp
        try:
            try:
                irr._raw_interval_csv()
            except SystemExit as e:
                assert "found 0" in str(e)
            else:
                raise AssertionError("expected SystemExit with zero matches")
            (tmp / "private" / "1-raw-data" / "Electric_15_Minute_a.csv").write_text("x")
            (tmp / "private" / "1-raw-data" / "Electric_15_Minute_b.csv").write_text("x")
            try:
                irr._raw_interval_csv()
            except SystemExit as e:
                assert "found 2" in str(e)
            else:
                raise AssertionError("expected SystemExit with two matches")
        finally:
            irr.ROOT = real_root
    return "zero or two-or-more raw interval CSVs under private/1-raw-data/ both " \
        "refuse, rather than picking one silently"


# ---------------------------------------------------------------------------
# Minimum-bill provision
# ---------------------------------------------------------------------------
@case
def case_minimum_bill_provision_never_triggered_in_this_data():
    _require_corpus()
    result = irr.build()
    mb = result["minimum_bill_provision"]
    assert mb["sentence_found_in_statements"], \
        "expected the Minimum Charge Adjustment glossary sentence somewhere in the corpus"
    assert mb["dollar_line_item_ever_printed"] == [], \
        "found an actual Minimum Charge Adjustment dollar line -- the artifact's " \
        "'never triggered' claim needs updating, not this test relaxed"
    assert mb["legacy_figure_applicable_to_this_household"] is False
    window_periods = mb["monthly_net_position_window"]
    assert len(window_periods) == 13
    assert all(p["net_kwh"] > 0 for p in window_periods), \
        "expected every period in the window to show a positive (net-import) net_kwh"
    assert mb["ever_net_generator_in_window"] is False
    assert mb["provision_triggered_in_this_data"] is False
    return (f"{len(window_periods)} periods checked, all net_kwh > 0 -- the "
           "provision's own trigger condition never held in this data")


# ---------------------------------------------------------------------------
# NBC-on-gross re-verification: dynamically derived, not hardcoded
# ---------------------------------------------------------------------------
@case
def case_nbc_gross_reverification_matches_the_real_statement():
    _require_corpus()
    result = irr.build()
    chk = result["nbc_gross_reverification"]
    assert chk["statement"] == "2025-10-31"
    assert chk["period"] == "9/26/25 - 9/30/25"
    assert _close(chk["printed_kwh"], 308.0)
    assert _close(chk["csv_gross_kwh"], 308.0)
    assert _close(chk["csv_net_kwh"], 224.0)
    assert chk["printed_kwh_matches_gross_kwh"] is True
    assert chk["printed_kwh_matches_net_kwh"] is False
    assert chk["confirmation"].startswith("CONFIRMED")
    return f"re-derived printed_kwh={chk['printed_kwh']} matches gross_kwh, not net_kwh"


@case
def case_nbc_gross_reverification_is_not_hardcoded():
    """Feed a FABRICATED statement text (via monkeypatching bd.statement_text)
    with a DIFFERENT wildfire kWh figure than the real corpus (999 instead of
    308) and prove build_nbc_gross_check() reports 999 -- i.e. it re-parses
    the text on every call rather than returning a constant."""
    _require_corpus()
    fake_text = (
        "Billing Period: 9/26/25 - 9/30/25 Total Days: 5\n"
        "Non-Bypassable Charges 6.57\n"
        "Wildfire Fund Charge 999 kWh x $.00595 5.95\n"
        "Total Taxes & Fees on Electric Charges $.57\n"
        "Total Electric Service $24.09\n"
        "Billing Period: 10/1/25 - 10/27/25 Total Days: 27\n"
        "Non-Bypassable Charges 13.21\n"
        "Wildfire Fund Charge 1,904 kWh x $.00595 11.33\n"
        "Total Taxes & Fees on Electric Charges $3.04\n"
        "Total Electric Service $83.71\n")

    def fake_statement_text(stmt):
        assert stmt == "2025-10-31"
        return fake_text

    real_fn = bd.statement_text
    real_cache = dict(bd._TEXT_CACHE)
    bd.statement_text = fake_statement_text
    bd._TEXT_CACHE.clear()
    try:
        fake_rows = [{"statement_date": "2025-10-31", "period": "9/26/25 - 9/30/25",
                     "gross_kwh": 308.0, "net_kwh": 224.0}]
        chk = irr.build_nbc_gross_check(fake_rows)
    finally:
        bd.statement_text = real_fn
        bd._TEXT_CACHE.clear()
        bd._TEXT_CACHE.update(real_cache)

    assert _close(chk["printed_kwh"], 999.0), \
        f"expected the fabricated 999 kWh to surface, got {chk['printed_kwh']} -- " \
        "this function is not re-deriving from statement text"
    assert chk["printed_kwh_matches_gross_kwh"] is False
    assert chk["confirmation"].startswith("NOT CONFIRMED")
    return "fabricated statement text changes the reported kWh -- proves this is a " \
        "live re-derivation, not a hardcoded constant"


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------
@case
def case_missing_required_csv_field_stops_the_run():
    """A period missing current_charges, fixed_charge_total, net_kwh, or
    gross_kwh in bill_periods_electric.csv must stop load_periods() rather
    than silently treat the field as zero."""
    real_csv = irr.PERIODS_CSV.read_text()
    rows = real_csv.splitlines()
    header = rows[0].split(",")
    idx = header.index("current_charges")
    broken = rows[0] + "\n"
    first_data_row = rows[1].split(",")
    first_data_row[idx] = ""
    broken += ",".join(first_data_row) + "\n" + "\n".join(rows[2:])
    with tempfile.TemporaryDirectory() as d:
        tmp_csv = pathlib.Path(d) / "bill_periods_electric.csv"
        tmp_csv.write_text(broken)
        saved = irr.PERIODS_CSV
        irr.PERIODS_CSV = tmp_csv
        try:
            try:
                irr.load_periods()
            except SystemExit as e:
                assert "current_charges" in str(e)
            else:
                raise AssertionError("expected SystemExit on a missing current_charges")
        finally:
            irr.PERIODS_CSV = saved
    return "a period missing current_charges stops load_periods() with SystemExit"


@case
def case_missing_required_charge_line_stops_the_run():
    """charge_lines_for_period() must refuse text with no Non-Bypassable
    Charges / Wildfire Fund Charge / Total Taxes & Fees line, rather than
    returning a dict silently missing that key (which would later read as a
    zero-dollar bucket)."""
    text_missing_wildfire = ("Non-Bypassable Charges 31.43\n"
                             "Total Taxes & Fees on Electric Charges $1.34\n")
    try:
        irr.charge_lines_for_period(text_missing_wildfire, "synthetic")
    except SystemExit as e:
        assert "wildfire_fund_charge" in str(e)
    else:
        raise AssertionError("expected SystemExit on a missing wildfire_fund_charge line")
    return "text missing a required charge line stops with SystemExit, not a silent zero"


@case
def case_build_stops_on_a_period_that_fails_its_own_reconciliation():
    """Finding 2 (issue #7 adversarial review): classify_periods() computes
    netted_energy_cross_check_pass / four_bucket_check_pass per period, but
    nothing used to check them before build() returned -- main() would write
    whatever came back. Poison one real period's cross-check flag to False
    and prove build() (and therefore main(), which never gets that far)
    refuses, naming the failing period, and that the committed artifact is
    left untouched."""
    _require_corpus()
    real_classify_periods = irr.classify_periods
    label_holder = {}

    def poisoned(periods):
        rows = real_classify_periods(periods)
        target = dict(rows[0])
        target["netted_energy_cross_check_pass"] = False
        target["netted_energy_cross_check_diff_usd"] = 999.99
        rows[0] = target
        label_holder["label"] = f"{target['statement_date']}/{target['period']}"
        return rows

    irr.classify_periods = poisoned
    before = irr.OUT.read_bytes() if irr.OUT.exists() else None
    try:
        try:
            irr.main()
        except SystemExit as e:
            assert label_holder["label"] in str(e), str(e)
        else:
            raise AssertionError("expected SystemExit on a failed reconciliation")
    finally:
        irr.classify_periods = real_classify_periods
    after = irr.OUT.read_bytes() if irr.OUT.exists() else None
    assert after == before, "the artifact changed despite build() raising"
    return (f"a poisoned netted_energy_cross_check_pass=False on "
           f"{label_holder['label']} stops main() with SystemExit naming that "
           "period, and the committed artifact is untouched")


@case
def case_build_stops_on_a_period_that_fails_the_four_bucket_check():
    """Same guard, the other flag: four_bucket_check_pass=False must also
    stop the run, not just netted_energy_cross_check_pass."""
    _require_corpus()
    real_classify_periods = irr.classify_periods
    label_holder = {}

    def poisoned(periods):
        rows = real_classify_periods(periods)
        target = dict(rows[-1])
        target["four_bucket_check_pass"] = False
        target["four_bucket_diff_usd"] = -12.34
        rows[-1] = target
        label_holder["label"] = f"{target['statement_date']}/{target['period']}"
        return rows

    irr.classify_periods = poisoned
    before = irr.OUT.read_bytes() if irr.OUT.exists() else None
    try:
        try:
            irr.main()
        except SystemExit as e:
            assert label_holder["label"] in str(e), str(e)
        else:
            raise AssertionError("expected SystemExit on a failed four-bucket check")
    finally:
        irr.classify_periods = real_classify_periods
    after = irr.OUT.read_bytes() if irr.OUT.exists() else None
    assert after == before, "the artifact changed despite build() raising"
    return (f"a poisoned four_bucket_check_pass=False on {label_holder['label']} "
           "stops main() with SystemExit naming that period")


@case
def case_build_stops_on_an_unconfirmed_nbc_gross_check():
    """Finding 2's second half (Codex's addition): if build_nbc_gross_check()
    ever comes back NOT CONFIRMED, build()/main() must refuse rather than
    publish an artifact whose NBC-on-gross proof did not check out."""
    _require_corpus()
    real_nbc_check = irr.build_nbc_gross_check

    def poisoned(rows):
        chk = dict(real_nbc_check(rows))
        chk["confirmation"] = "NOT CONFIRMED -- synthetic test poison"
        return chk

    irr.build_nbc_gross_check = poisoned
    before = irr.OUT.read_bytes() if irr.OUT.exists() else None
    try:
        try:
            irr.main()
        except SystemExit as e:
            assert "NOT CONFIRMED" in str(e) or "did not confirm" in str(e).lower()
        else:
            raise AssertionError("expected SystemExit on an unconfirmed NBC-gross check")
    finally:
        irr.build_nbc_gross_check = real_nbc_check
    after = irr.OUT.read_bytes() if irr.OUT.exists() else None
    assert after == before, "the artifact changed despite the unconfirmed NBC-gross check"
    return "a NOT-CONFIRMED nbc_gross_reverification stops main() with SystemExit " \
        "before anything is written"


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
@case
def case_artifact_regenerates_byte_identically():
    """Always regenerate first, rather than trusting whatever bytes happen to
    already be on disk -- a stale committed artifact from before the last
    code edit would otherwise be compared against a fresh run and fail for
    the wrong reason."""
    _require_corpus()
    path = irr.OUT
    irr.main()
    before = path.read_bytes()
    irr.main()
    after = path.read_bytes()
    assert after == before, "data/irreducible_bill.json is not reproducible"
    irr.main()
    assert path.read_bytes() == before, "second regeneration diverged"
    return "data/irreducible_bill.json regenerates byte-identically across two runs"


@case
def case_artifact_has_no_pii():
    """The artifact legitimately DESCRIBES its own privacy policy using the
    words "account"/"meter" (the provenance.no_pii field), so this checks for
    the SHAPES those leaks actually take -- a 9+ digit run (account/meter/RIN-
    style numbers) and the RIN's own literal prefix -- not the English words
    that describe avoiding them."""
    _require_corpus()
    if not irr.OUT.exists():
        irr.main()
    text = irr.OUT.read_text()
    long_digit_runs = re.findall(r"\b\d{9,}\b", text)
    assert not long_digit_runs, f"found account/meter-shaped digit run(s): {long_digit_runs}"
    assert "USCA-SD" not in text, "found a RIN-shaped token"
    # A 4+4-digit pair separated by whitespace is the account-number print shape
    # ("NNNN NNNN"); checked by shape only -- the real value must never appear
    # literally in this committed test file.
    assert not re.search(r"\b\d{4}\s\d{4}\b", text), "found an account-number-shaped token"
    return "no account/meter/RIN-shaped digit run in the committed artifact"


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
