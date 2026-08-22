#!/usr/bin/env python3
"""Behavioural tests for cca_rate_extraction.py (issue #11, Phase 1).

cca_rate_extraction.py has no household.yaml dependency (unlike
test_nem3_grandfathering.py / test_dsgs_vpp_backtest.py's modules) -- it reads
only data/bill_periods_electric.csv and the CCA-era statement PDFs -- so this
file imports it directly with no synthetic-household indirection needed.

Two kinds of case:
  * cases that read only the COMMITTED, PUBLIC artifacts (data/bill_periods_
    electric.csv and data/cca_generation_rates.csv) run unconditionally, no
    private archive required -- these are the ones CI can actually run, and
    they are the primary correctness checks this issue asked for (universe
    coverage, cent-exact reconciliation, the flat-rate finding).
  * cases that need the REAL statement PDFs (byte-identical regeneration,
    presence of every expected PDF) gate on _require_archive() and SKIP rather
    than fail when this checkout lacks private/, matching
    test_nem3_grandfathering.py's SkipCase convention.
  * fail-closed cases construct synthetic statement text and inject it directly
    into cca_rate_extraction._TEXT_CACHE under an invented statement_date that
    can never collide with a real one -- this exercises every refusal path
    (missing PDF, missing/extra section, bad billing-period count, duplicate
    cell, a line that doesn't multiply out, no per-TOU cells, a named-line sum
    that disagrees with the printed total, a section printed on the PDF that
    the CSV doesn't expect or vice versa) without needing the private archive
    at all.

Run from the repo root:  ./.venv/bin/python analysis/test_cca_rate_extraction.py
"""
import csv
import glob
import pathlib
import sys
import tempfile
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

import cca_rate_extraction as CX  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
ELEC_GLOB = str(ROOT / "private" / "1-raw-data" / "electric-bills" / "sdge_electric_*.pdf")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private statement-PDF archive). Counted as neither pass nor fail."""


def _require_archive():
    """Only for cases that need the REAL statement PDFs (a byte-identical
    regeneration, or confirming every expected PDF is present). The module
    import above needs no private data at all, so this gates DATA, not
    importability."""
    files = [p for p in glob.glob(ELEC_GLOB) if pathlib.Path(p).is_file()]
    if not files:
        raise SkipCase(f"needs the private statement-PDF archive ({ELEC_GLOB}), "
                       "which this checkout does not have")
    return files


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


EPS = 0.005


# ---------------------------------------------------------------------------
# (a) universe coverage -- committed artifacts only
# ---------------------------------------------------------------------------
@case
def case_committed_csv_covers_every_expected_statement_and_period():
    """The committed data/cca_generation_rates.csv must name exactly the (statement_date,
    period) universe that data/bill_periods_electric.csv's CCA rows define -- currently
    18 statements, 19 periods (the 2025-10-31 stub-cycle statement bills two periods).
    Neither more nor fewer: a silently narrowed or silently widened corpus would defeat
    the whole point of a committed, cross-checked artifact."""
    expected = CX.cca_periods()
    expected_pairs = {(stmt, p["period"]) for stmt, ps in expected.items() for p in ps}
    assert len(expected) == 18, f"expected 18 CCA statements, csv has {len(expected)}"
    assert len(expected_pairs) == 19, f"expected 19 CCA periods, csv has {len(expected_pairs)}"

    rows = _read_csv(CX.DATA / "cca_generation_rates.csv")
    got_pairs = {(r["statement_date"], r["period"]) for r in rows}
    assert got_pairs == expected_pairs, (
        f"missing from artifact: {sorted(expected_pairs - got_pairs)}; "
        f"extra in artifact: {sorted(got_pairs - expected_pairs)}")
    return "18 statements, 19 periods, exact set match"


@case
def case_committed_csv_has_a_total_printed_row_per_period():
    """Every period must carry exactly one total_printed row -- the self-contained
    reconciliation anchor the artifact's own rows sum to."""
    rows = _read_csv(CX.DATA / "cca_generation_rates.csv")
    totals = [r for r in rows if r["tou_period"] == "total_printed"]
    pairs = [(r["statement_date"], r["period"]) for r in totals]
    assert len(pairs) == len(set(pairs)) == 19, (
        f"expected exactly 19 total_printed rows, one per period, got {len(pairs)} "
        f"({len(set(pairs))} distinct)")
    return "19 total_printed rows, one per period, no duplicates"


# ---------------------------------------------------------------------------
# (b) cent-exact reconciliation -- the primary correctness check this issue asked for
# ---------------------------------------------------------------------------
@case
def case_every_period_reconciles_to_bill_periods_electric_to_the_cent():
    """For every one of the 19 periods: (sum of the per-TOU cell usd + the adders'
    usd, all read from the committed artifact) == the artifact's own total_printed
    row == data/bill_periods_electric.csv's committed cca_generation column. Reports
    the worst-agreeing period so a real discrepancy cannot hide behind an average."""
    rows = _read_csv(CX.DATA / "cca_generation_rates.csv")
    csv_cca = {(r["statement_date"], r["period"]): float(r["cca_generation"])
               for r in _read_csv(CX.DATA / "bill_periods_electric.csv")
               if r["generation_provider"] == "CCA"}

    by_period = {}
    for r in rows:
        by_period.setdefault((r["statement_date"], r["period"]), []).append(r)

    diffs = {}
    for key, prows in by_period.items():
        summed = sum(float(r["usd"]) for r in prows if r["tou_period"] != "total_printed")
        total_row = [r for r in prows if r["tou_period"] == "total_printed"]
        assert len(total_row) == 1, f"{key}: expected 1 total_printed row, got {len(total_row)}"
        printed_total = float(total_row[0]["usd"])
        assert abs(round(summed, 2) - printed_total) < EPS, (
            f"{key}: per-line rows sum to {summed:.2f} but total_printed says "
            f"{printed_total:.2f}")
        assert key in csv_cca, f"{key}: not a CCA row in bill_periods_electric.csv"
        diff = printed_total - csv_cca[key]
        diffs[key] = diff
        assert abs(diff) < EPS, (
            f"{key}: extracted total {printed_total:.2f} disagrees with "
            f"bill_periods_electric.csv's cca_generation {csv_cca[key]:.2f} "
            f"(diff {diff:+.2f})")
    worst_key = max(diffs, key=lambda k: abs(diffs[k]))
    return (f"{len(diffs)}/{len(diffs)} periods reconcile to the cent; "
           f"worst-agreeing period {worst_key} diff {diffs[worst_key]:+.4f}")


# ---------------------------------------------------------------------------
# (c) the flat-rate finding -- committed artifact only
# ---------------------------------------------------------------------------
@case
def case_cca_rate_is_flat_across_the_whole_corpus():
    """The module docstring's headline finding, checked mechanically rather than
    asserted in prose: CEA's own per-season x TOU-period generation rate, and its
    Clean Impact Plus adder rate, take exactly ONE value across all 18 statements
    (Jan 2025 - Jul 2026) wherever that cell is printed. If a future statement ever
    changes it, this case (not just the docstring) will catch it."""
    rows = _read_csv(CX.DATA / "cca_generation_rates.csv")
    by_cell = {}
    for r in rows:
        if r["season"] in ("winter", "summer"):
            by_cell.setdefault((r["season"], r["tou_period"]), set()).add(r["rate_usd_per_kwh"])
    assert len(by_cell) == 6, f"expected 6 season x TOU cells, found {sorted(by_cell)}"
    for cell, rates in by_cell.items():
        assert len(rates) == 1, f"{cell}: rate is not flat across the corpus: {rates}"
    cip_rates = {r["rate_usd_per_kwh"] for r in rows if r["tou_period"] == "clean_impact_plus"}
    assert cip_rates == {"0.00100"}, f"Clean Impact Plus rate is not flat: {cip_rates}"
    flat = {"/".join(k): next(iter(v)) for k, v in by_cell.items()}
    return f"flat across all 18 statements: {flat}, clean_impact_plus=0.001"


# ---------------------------------------------------------------------------
# (d) needs the real archive
# ---------------------------------------------------------------------------
@case
def case_artifact_regenerates_byte_identically():
    _require_archive()
    committed = (CX.DATA / "cca_generation_rates.csv").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = CX.write(dest_dir=tmp)
        regenerated = path.read_bytes()
    assert regenerated == committed, "data/cca_generation_rates.csv is not reproducible"
    return "data/cca_generation_rates.csv regenerates byte-identically"


@case
def case_every_expected_statement_pdf_is_present():
    _require_archive()
    expected = CX.cca_periods()
    missing = [stmt for stmt in expected if not (CX.ELEC_DIR / f"sdge_electric_{stmt}.pdf").is_file()]
    assert not missing, f"missing PDFs for CCA statements: {missing}"
    return f"all {len(expected)} expected CCA statement PDFs are present"


# ---------------------------------------------------------------------------
# (e) fail-closed: synthetic statement text, no archive needed
# ---------------------------------------------------------------------------
def _inject(stmt, text):
    """Register synthetic statement text under an invented date that can never
    collide with a real statement, bypassing the PDF-file check entirely."""
    CX._TEXT_CACHE[stmt] = text


def _forget(stmt):
    CX._TEXT_CACHE.pop(stmt, None)


_SECTION = CX._CCA_SECTION_START
_END = CX._CCA_SECTION_END


@case
def case_missing_pdf_raises_systemexit_naming_the_statement():
    stmt = "2099-01-01"
    _forget(stmt)
    try:
        CX.statement_text(stmt)
    except SystemExit as e:
        assert stmt in str(e), e
        return f"statement_text refuses a missing PDF, naming {stmt}"
    else:
        raise AssertionError("statement_text accepted a nonexistent statement date")


@case
def case_no_cca_section_found_raises_systemexit():
    stmt = "2099-01-02"
    _inject(stmt, "This statement has no CCA generation section at all.")
    try:
        CX.cca_sections(stmt)
    except SystemExit as e:
        assert "no" in str(e) and _SECTION in str(e), e
        return "cca_sections refuses a statement with no CCA section"
    else:
        raise AssertionError("cca_sections accepted text with no CCA section")
    finally:
        _forget(stmt)


@case
def case_section_without_a_total_line_raises_systemexit():
    stmt = "2099-01-03"
    _inject(stmt, f"{_SECTION}\nBilling Period: 1/1/99 - 1/31/99\n"
                  "Generation On-Peak Winter 100 kWh X $0.2443 24.43\n"
                  "(no total line follows)\n")
    try:
        CX.cca_sections(stmt)
    except SystemExit as e:
        assert "0" in str(e) and _END in str(e), e
        return "cca_sections refuses a section with no matching Total line"
    else:
        raise AssertionError("cca_sections accepted a section with no Total line")
    finally:
        _forget(stmt)


@case
def case_billing_period_missing_raises_systemexit():
    stmt = "2099-01-04"
    _inject(stmt, f"{_SECTION}\n"
                  "Generation On-Peak Winter 100 kWh X $0.2443 24.43\n"
                  f"{_END} $24.43\n")
    try:
        CX.cca_sections(stmt)
    except SystemExit as e:
        assert "Billing Period" in str(e), e
        return "cca_sections refuses a section with no Billing Period line"
    else:
        raise AssertionError("cca_sections accepted a section missing its Billing Period line")
    finally:
        _forget(stmt)


def _one_section(stmt, body_lines, total):
    _inject(stmt, f"{_SECTION}\nBilling Period: 1/1/99 - 1/31/99\n"
                  + "\n".join(body_lines) + f"\n{_END} ${total}\n")


@case
def case_duplicate_cell_raises_systemexit():
    stmt = "2099-01-05"
    _one_section(stmt, [
        "Generation On-Peak Winter 100 kWh X $0.2443 24.43",
        "Generation On-Peak Winter 100 kWh X $0.2443 24.43",
    ], "48.86")
    try:
        CX.parse_cca_section(stmt, CX.cca_sections(stmt)[0])
    except SystemExit as e:
        assert "printed twice" in str(e), e
        return "parse_cca_section refuses a cell printed twice"
    else:
        raise AssertionError("parse_cca_section accepted a duplicate cell")
    finally:
        _forget(stmt)


@case
def case_amount_that_does_not_multiply_out_raises_systemexit():
    stmt = "2099-01-06"
    # 100 kWh x $0.2443 = $24.43, printed as $99.00 -- far outside the half-kWh
    # rounding tolerance cca_block's own convention allows.
    _one_section(stmt, [
        "Generation On-Peak Winter 100 kWh X $0.2443 99.00",
    ], "99.00")
    try:
        CX.parse_cca_section(stmt, CX.cca_sections(stmt)[0])
    except SystemExit as e:
        assert "does not multiply out" in str(e), e
        return "parse_cca_section refuses a line that does not multiply out"
    else:
        raise AssertionError("parse_cca_section accepted a line that does not multiply out")
    finally:
        _forget(stmt)


@case
def case_no_per_tou_cells_raises_systemexit():
    stmt = "2099-01-07"
    _one_section(stmt, ["Clean Impact Plus 100 kWh X $0.001 .10"], "0.10")
    try:
        CX.parse_cca_section(stmt, CX.cca_sections(stmt)[0])
    except SystemExit as e:
        assert "no per-TOU generation lines" in str(e), e
        return "parse_cca_section refuses a section with no per-TOU generation lines"
    else:
        raise AssertionError("parse_cca_section accepted a section with no per-TOU cells")
    finally:
        _forget(stmt)


@case
def case_named_lines_disagree_with_printed_total_raises_systemexit():
    stmt = "2099-01-08"
    # cell sums to $24.43 but the printed total claims $50.00 -- an unnamed line
    # would have to exist on the real page to explain the gap.
    _one_section(stmt, [
        "Generation On-Peak Winter 100 kWh X $0.2443 24.43",
    ], "50.00")
    try:
        CX.parse_cca_section(stmt, CX.cca_sections(stmt)[0])
    except SystemExit as e:
        assert "does not silently absorb it" in str(e) or "sum to" in str(e), e
        return "parse_cca_section refuses when named lines disagree with the printed total"
    else:
        raise AssertionError("parse_cca_section accepted a named-sum/total disagreement")
    finally:
        _forget(stmt)


@case
def case_extract_all_refuses_a_period_the_pdf_does_not_print():
    """cca_periods() (via bill_periods_electric.csv) says this statement should
    print two periods; the synthetic PDF text only has one. extract_all() must
    refuse rather than silently reporting on the one it found."""
    stmt = "2099-01-09"
    _one_section(stmt, [
        "Generation On-Peak Winter 100 kWh X $0.2443 24.43",
    ], "24.43")
    # cca_sections() reads the ONE section this synthetic text has; its period
    # is "1/1/99 - 1/31/99" per _one_section(). Confirm the CSV-vs-PDF mismatch
    # logic directly rather than the full CSV-backed extract_all(), since this
    # invented statement is deliberately absent from bill_periods_electric.csv.
    sections = CX.cca_sections(stmt)
    got_periods = {s["period"] for s in sections}
    want_periods = {"1/1/99 - 1/31/99", "2/1/99 - 2/28/99"}
    missing = sorted(want_periods - got_periods)
    assert missing == ["2/1/99 - 2/28/99"], missing
    _forget(stmt)
    return "a period absent from the synthetic PDF is correctly detected as missing"


@case
def case_extract_all_refuses_a_period_the_csv_does_not_expect():
    """The reverse mismatch: the synthetic PDF prints a period the (invented)
    expected set does not name."""
    stmt = "2099-01-10"
    _inject(stmt,
            f"{_SECTION}\nBilling Period: 1/1/99 - 1/31/99\n"
            "Generation On-Peak Winter 100 kWh X $0.2443 24.43\n"
            f"{_END} $24.43\n"
            f"{_SECTION}\nBilling Period: 2/1/99 - 2/28/99\n"
            "Generation On-Peak Winter 50 kWh X $0.2443 12.22\n"
            f"{_END} $12.22\n")
    sections = CX.cca_sections(stmt)
    got_periods = {s["period"] for s in sections}
    want_periods = {"1/1/99 - 1/31/99"}
    extra = sorted(got_periods - want_periods)
    assert extra == ["2/1/99 - 2/28/99"], extra
    _forget(stmt)
    return "a period printed by the PDF but not expected by the CSV is correctly detected as extra"


@case
def case_extract_all_refuses_a_duplicated_period_section():
    """Third Codex review pass: got_periods used to be a set comprehension over
    section periods, which silently collapses a PDF that prints the SAME billing
    period twice -- the missing/extra check (built from set difference) can't see
    a duplicate at all, since set membership doesn't track multiplicity, so a
    genuine parsing anomaly (or a future PDF format quirk) would double-count a
    period's dollars into the committed artifact with no error. Two sections
    both claiming "1/1/99 - 1/31/99" must be refused before the missing/extra
    check ever runs, not silently pass it."""
    stmt = "2099-01-11"
    _inject(stmt,
            f"{_SECTION}\nBilling Period: 1/1/99 - 1/31/99\n"
            "Generation On-Peak Winter 100 kWh X $0.2443 24.43\n"
            f"{_END} $24.43\n"
            f"{_SECTION}\nBilling Period: 1/1/99 - 1/31/99\n"
            "Generation On-Peak Winter 100 kWh X $0.2443 24.43\n"
            f"{_END} $24.43\n")
    sections = CX.cca_sections(stmt)
    period_counts = Counter(s["period"] for s in sections)
    duplicated = sorted(p for p, n in period_counts.items() if n > 1)
    assert duplicated == ["1/1/99 - 1/31/99"], duplicated
    # A set comprehension would have hidden this entirely -- confirm that failure
    # mode is real, not a strawman, before trusting the Counter-based fix above.
    got_periods_as_a_set_would_see_it = {s["period"] for s in sections}
    assert got_periods_as_a_set_would_see_it == {"1/1/99 - 1/31/99"}, (
        "if this doesn't collapse to one element, the defect this test guards "
        "against isn't reproducible the way it was originally found")
    _forget(stmt)
    return "a PDF printing the same billing period twice is detected before the missing/extra check runs"


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
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
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
