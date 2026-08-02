#!/usr/bin/env python3
"""Extract the CCA's own charged per-season x TOU-period generation rates from
every CCA-era bill PDF (issue #11, Phase 1).

THE GAP THIS FILLS. analysis/rates_history.py's RateSet.cca_generation() fails
closed by design on every CCA date -- its docstring says plainly "the CCA's own
per-TOU rates appear only on the CCA bill pages, which parse_bills.py does not
extract -- only the period total (bill_periods_electric.cca_generation) is
committed." That refusal is correct and this script does not touch it. The only
place those per-TOU CCA rates have ever been parsed in this repository is
bill_decomposition.py's private cca_block(), used for exactly ONE hardcoded
statement (2026-07-02, its CURRENT comparison date). This script generalizes
that same parsing approach -- same section anchors, same per-line regex, same
kWh-rounding tolerance, same "named lines must sum to the printed total" gate --
across every CCA-era statement PDF, and commits the result as
data/cca_generation_rates.csv so later phases (the bidirectional counterfactual)
have a real per-TOU CCA rate to reprice against instead of a period total.

THE 18 STATEMENTS, THE 19 PERIODS. data/bill_periods_electric.csv carries 19 rows
with generation_provider == "CCA", spanning 18 distinct statement_date values --
one statement (2025-10-31) bills TWO periods (9/26/25-9/30/25 and
10/1/25-10/27/25), a stub cycle straddling SDG&E's move from the flat Monthly
Service Fee to the per-day Base Services Charge (CLAUDE.md / issue #7). Its PDF
prints two separate "Community Choice Aggregation (CCA) Electric Generation
Charges" sections, each with its own "Billing Period: ..." line and its own
"Total CCA Electric Generation Charges" line. Every other CCA statement prints
exactly one such section. This script does not assume one section per PDF: it
finds every section a statement's PDF actually contains, reads each section's
own printed "Billing Period" line to match it to the right row of
bill_periods_electric.csv, and fails closed if the section count or the billing
periods found do not exactly match what that CSV says this statement should
contain.

FORMAT VARIATION FOUND ACROSS THE CORPUS, AND WHY IT DID NOT NEED SPECIAL-CASING.
2025-10-01's statement interleaves a right-hand "Breakdown of Current Charges"
column into the same extracted text lines as the CCA section (the same
interleaving bill_decomposition.py's printed_tou_blocks() documents for the 2026
SDG&E delivery/generation tables). It does not break this parser: every per-line
regex here anchors on a complete printed phrase ("Generation On-Peak Summer 500
kWh X $0.51684 258.30") that stays contiguous on one extracted line even when
other columns are interleaved elsewhere in the text, and the section is bounded
by an exact string search for the section header and the "Total CCA Electric
Generation Charges" line, neither of which the interleaving splits. Every one of
the 18 statements parsed with the SAME regex set and the SAME section anchors --
no statement needed its own code path.

THE FINDING: CCA'S OWN CHARGED RATE DID NOT MOVE ONCE IN 18 MONTHS. Extracted
across all 18 statements (Jan 2025 - Jul 2026), CEA's own per-season x TOU-period
generation rate is IDENTICAL on every statement that prints that cell:
  winter   on-peak 0.2443   off-peak 0.15782   super off-peak 0.05187
  summer   on-peak 0.51684  off-peak 0.15975   super off-peak 0.04961
and its Clean Impact Plus product-adder rate is 0.001/kWh on every one of the 18
statements. This is a real, checked finding, not an assumption this script made
going in -- see case_cca_rate_is_flat_across_the_whole_corpus in
test_cca_rate_extraction.py. It contrasts with data/rate_vintages.csv's
generation_printed_comparison rows, which show SDG&E's PRINTED bundled-generation
(EECC) comparison table changing repeatedly over the same 18 months (e.g. summer
on-peak comparison 0.38826 -> 0.40592 -> 0.47019): the bundled comparison table
moved with the tariff vintage while the CCA's own charged rate that this
household actually paid did not move once. Later phases should not assume this
flatness continues; it is reported here as what the 18 statements on file show,
not as a property of the CCA tariff going forward.

A DISCLOSED AMBIGUITY: the Clean Impact Plus line's own kWh base. CEA prints
"Clean Impact Plus 945 kWh X $0.001 .95" alongside a period whose net_kwh (per
bill_periods_electric.csv) is 946.0 -- close but not identical, and the same
one-or-two-kWh gap recurs on other statements. The statements do not say what
quantity Clean Impact Plus is levied on; it is not simply net_kwh. This script
records exactly what is printed (kwh, rate, usd) and does not assert what the
kWh base represents -- see the clean_impact_plus rows' note field.

Inputs   data/bill_periods_electric.csv           the expected (statement, period)
                                                   universe and the cca_generation
                                                   column this reconciles against
         private/1-raw-data/electric-bills/*.pdf  the 18 CCA-era statement PDFs
Output   data/cca_generation_rates.csv             written atomically; run twice ->
                                                    byte-identical

Run directly:  ./.venv/bin/python analysis/cca_rate_extraction.py
"""
import csv
import os
import pathlib
import re
import sys
import tempfile
from collections import Counter

import pdfplumber


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (same convention as
    every other generator in this repo, so the private/verify sandbox pattern
    works unchanged)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
ELEC_DIR = ROOT / "private" / "1-raw-data" / "electric-bills"

SEASONS = ("winter", "summer")
TOU = ("on_peak", "off_peak", "super_off_peak")

# Printed negatives are U+2212 MINUS SIGN, and rates print without a leading zero
# -- identical to bill_decomposition.py's convention, reused here on purpose.
_NUM = r"[−-]?(?:[\d,]*\.\d+|[\d,]+)"
_PRINTED_TOU = {"On-Peak": "on_peak", "Off-Peak": "off_peak",
                "Super Off-Peak": "super_off_peak"}

_CCA_SECTION_START = "Community Choice Aggregation (CCA) Electric Generation Charges"
_CCA_SECTION_END = "Total CCA Electric Generation Charges"
_CCA_LINE = re.compile(
    rf"Generation (On-Peak|Off-Peak|Super Off-Peak) (Summer|Winter)\s+({_NUM}) kWh X "
    rf"\$({_NUM})\s+({_NUM})")
_CIP_LINE = re.compile(rf"Clean Impact Plus\s+([\d,]+) kWh X \$({_NUM})\s+({_NUM})")
_SST_LINE = re.compile(rf"State Surcharge Tax\s+({_NUM})")
_TOTAL_LINE = re.compile(rf"Total CCA Electric Generation Charges \$({_NUM})")
_BILLING_PERIOD = re.compile(r"Billing Period: ([\d/]+ - [\d/]+)")


def _f(s):
    """'1,904' -> 1904.0 ; '−140' (U+2212 minus) -> -140.0"""
    return float(str(s).replace(",", "").replace("−", "-").replace("$", ""))


def _c(x):
    """Round to cents; +0.0 folds -0.0 to 0.0 so the artifact is stable."""
    return round(x, 2) + 0.0


# ---------------------------------------------------------------------------
# Statement text
# ---------------------------------------------------------------------------
_TEXT_CACHE = {}


def _statement_path(stmt):
    p = ELEC_DIR / f"sdge_electric_{stmt}.pdf"
    if not p.exists():
        raise SystemExit(
            f"statement {stmt} not found at {p} -- the CEA per-TOU generation "
            "rates appear nowhere else, so this extraction reads the bill PDF "
            "directly and cannot proceed without it")
    return p


def statement_text(stmt):
    if stmt not in _TEXT_CACHE:
        with pdfplumber.open(_statement_path(stmt)) as pdf:
            _TEXT_CACHE[stmt] = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    return _TEXT_CACHE[stmt]


# ---------------------------------------------------------------------------
# The expected universe: every (statement_date, period) row in
# bill_periods_electric.csv billed under generation_provider == CCA.
# ---------------------------------------------------------------------------
def _read_csv(path):
    if not path.exists():
        raise SystemExit(f"required artifact missing: {path}")
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def cca_periods():
    """{statement_date: [{"period": ..., "cca_generation": ..., "net_kwh": ...}, ...]}
    for every row of data/bill_periods_electric.csv billed under CCA, in file
    order. This -- not the PDFs on disk -- defines what SHOULD be extracted, so
    a missing or extra PDF fails closed here rather than silently narrowing the
    corpus this artifact claims to cover (same rationale as
    bill_decomposition.statement_dates())."""
    out = {}
    for r in _read_csv(DATA / "bill_periods_electric.csv"):
        if r["generation_provider"] != "CCA":
            continue
        out.setdefault(r["statement_date"], []).append({
            "period": r["period"],
            "cca_generation": _f(r["cca_generation"]),
            "net_kwh": _f(r["net_kwh"]),
        })
    if not out:
        raise SystemExit(
            "data/bill_periods_electric.csv carries no generation_provider == CCA "
            "rows -- nothing to extract")
    return out


# ---------------------------------------------------------------------------
# One statement's CCA page(s)
# ---------------------------------------------------------------------------
def cca_sections(stmt):
    """Every CCA generation-charges section on one statement's PDF, in printed
    order: [{"period": "...", "body": <text between the section header and its
    own Total line>, "total_end": <index of the Total line>}, ...].

    A statement prints one section per billing period it covers -- one, except
    for the 9/26/25-9/30/25 + 10/1/25-10/27/25 stub-cycle statement
    (2025-10-31), which prints two, each with its own Billing Period line and
    its own Total line. This walks every section header found, in order, and
    pairs each with the next Total line after it -- never the first Total line
    in the whole text -- so a multi-section statement cannot have its first
    section's body accidentally swallow its second section's lines."""
    txt = statement_text(stmt)
    starts = [m.start() for m in re.finditer(re.escape(_CCA_SECTION_START), txt)]
    if not starts:
        raise SystemExit(
            f"{stmt}: no '{_CCA_SECTION_START}' section found -- this statement "
            "was expected to be CCA-era per data/bill_periods_electric.csv")
    ends = [m.start() for m in re.finditer(re.escape(_CCA_SECTION_END), txt)]
    if len(starts) != len(ends):
        raise SystemExit(
            f"{stmt}: found {len(starts)} '{_CCA_SECTION_START}' header(s) but "
            f"{len(ends)} '{_CCA_SECTION_END}' line(s) -- a section is missing "
            "its total and this parser must not guess which body it belongs to")
    sections = []
    for i, s in enumerate(starts):
        e = next((x for x in ends if x > s), None)
        if e is None:
            raise SystemExit(
                f"{stmt}: CCA section at text offset {s} has no "
                f"'{_CCA_SECTION_END}' line after it")
        if i + 1 < len(starts) and e > starts[i + 1]:
            raise SystemExit(
                f"{stmt}: CCA section at offset {s} does not close before the "
                f"next section starts at {starts[i + 1]} -- the layout does not "
                "match the one-section-per-period assumption and must not be "
                "guessed at")
        body = txt[s:e]
        bp = _BILLING_PERIOD.findall(body)
        if len(bp) != 1:
            raise SystemExit(
                f"{stmt}: CCA section at offset {s} has {len(bp)} 'Billing "
                "Period:' line(s) inside it, expected exactly 1")
        sections.append({"period": bp[0], "body": body, "total_end": e})
    return sections


def parse_cca_section(stmt, section):
    """One section's cells, adders and printed total -- the same shape and the
    same checks as bill_decomposition.cca_block(), generalized to run on a
    section boundary rather than the whole-statement boundary."""
    body, end = section["body"], section["total_end"]
    period = section["period"]
    cells = {}
    for m in _CCA_LINE.finditer(body):
        key = (m.group(2).lower(), _PRINTED_TOU[m.group(1)])
        if key in cells:
            raise SystemExit(f"{stmt} {period}: CCA cell {key} printed twice")
        kwh, rate, usd = _f(m.group(3)), _f(m.group(4)), _f(m.group(5))
        # CEA prices unrounded kWh and prints the quantity rounded to whole kWh,
        # so kWh x rate reproduces the printed amount only to within half a kWh
        # of rate (bill_decomposition.cca_block's own documented tolerance,
        # reused unchanged) -- a genuinely wrong rate or amount still fails.
        if abs(kwh * rate - usd) > abs(rate) * 0.5 + 0.015:
            raise SystemExit(
                f"{stmt} {period}: CCA line {key} does not multiply out to "
                f"within the printed kWh rounding: {kwh} x {rate} != {usd}")
        cells[key] = {"kwh": kwh, "rate": rate, "usd": usd}
    if not cells:
        raise SystemExit(f"{stmt} {period}: CCA section carries no per-TOU generation lines")

    adders = {}
    m = _CIP_LINE.search(body)
    if m:
        adders["clean_impact_plus"] = {"kwh": _f(m.group(1)), "rate": _f(m.group(2)),
                                        "usd": _f(m.group(3))}
    m = _SST_LINE.search(body)
    if m:
        adders["state_surcharge_tax"] = {"usd": _f(m.group(1))}

    txt = statement_text(stmt)
    m = _TOTAL_LINE.match(txt, pos=end)
    if not m:
        raise SystemExit(f"{stmt} {period}: could not read the printed total after "
                          f"'{_CCA_SECTION_END}' at offset {end}")
    total = _f(m.group(1))
    named = sum(c["usd"] for c in cells.values()) + sum(a["usd"] for a in adders.values())
    if _c(named) != _c(total):
        raise SystemExit(
            f"{stmt} {period}: the named CCA lines sum to {_c(named)} against a "
            f"printed total of {total} -- an unnamed line was added to the CEA "
            "page and this script must not silently absorb it")
    return {"period": period, "cells": cells, "adders": adders, "total_usd": total}


# ---------------------------------------------------------------------------
# Whole-corpus extraction, cross-checked against bill_periods_electric.csv
# ---------------------------------------------------------------------------
def extract_all():
    """Every CCA statement's per-TOU rates, matched to and reconciled against
    data/bill_periods_electric.csv's cca_generation column. Fails closed on any
    statement whose printed sections do not exactly match that CSV's periods,
    or whose printed total disagrees with the committed period total."""
    expected = cca_periods()
    results = []
    for stmt in sorted(expected):
        want_periods = {p["period"]: p for p in expected[stmt]}
        sections = cca_sections(stmt)
        period_counts = Counter(s["period"] for s in sections)
        duplicated = sorted(p for p, n in period_counts.items() if n > 1)
        if duplicated:
            raise SystemExit(
                f"{stmt}: the PDF prints the same CCA billing-period section "
                f"more than once for {duplicated} -- a set-based missing/extra "
                "check would silently collapse this and double-count the "
                "period; refusing rather than guessing which copy is correct")
        got_periods = set(period_counts)
        missing = sorted(set(want_periods) - got_periods)
        extra = sorted(got_periods - set(want_periods))
        if missing or extra:
            raise SystemExit(
                f"{stmt}: the CCA sections printed on the PDF do not match the "
                f"CCA periods in data/bill_periods_electric.csv -- missing from "
                f"the PDF: {missing}, present on the PDF with no matching CSV "
                f"row: {extra}")
        for section in sections:
            parsed = parse_cca_section(stmt, section)
            csv_row = want_periods[parsed["period"]]
            diff = _c(parsed["total_usd"] - csv_row["cca_generation"])
            if abs(diff) > 0.01:
                raise SystemExit(
                    f"{stmt} {parsed['period']}: extracted CCA total "
                    f"${parsed['total_usd']:.2f} disagrees with "
                    f"data/bill_periods_electric.csv's committed cca_generation "
                    f"of ${csv_row['cca_generation']:.2f} (diff ${diff:+.2f}) -- "
                    "the per-TOU extraction does not reconcile to the already-"
                    "validated period total, which is a real parsing problem, "
                    "not something to paper over")
            results.append({
                "statement_date": stmt,
                "period": parsed["period"],
                "cells": parsed["cells"],
                "adders": parsed["adders"],
                "total_usd": parsed["total_usd"],
                "committed_cca_generation_usd": csv_row["cca_generation"],
                "reconciliation_diff_usd": diff,
            })
    return results


# ---------------------------------------------------------------------------
# The committed artifact
# ---------------------------------------------------------------------------
_HEADER = ["statement_date", "period", "season", "tou_period", "kwh",
           "rate_usd_per_kwh", "usd", "provider", "authority", "evidence",
           "source_pdf", "note"]


def _rows():
    rows = []
    for r in extract_all():
        stmt, period = r["statement_date"], r["period"]
        src = f"sdge_electric_{stmt}.pdf"
        for season in SEASONS:
            for tou in TOU:
                c = r["cells"].get((season, tou))
                if c is None:
                    continue
                rows.append([
                    stmt, period, season, tou, f"{c['kwh']:.1f}",
                    f"{c['rate']:.5f}", f"{c['usd']:.2f}", "CCA",
                    "charged_tariff", "direct", src,
                    f"printed on {period}; kWh x rate reproduces the printed "
                    "amount only to within CEA's half-kWh unrounding tolerance"])
        cip = r["adders"].get("clean_impact_plus")
        if cip is not None:
            rows.append([
                stmt, period, "", "clean_impact_plus", f"{cip['kwh']:.1f}",
                f"{cip['rate']:.5f}", f"{cip['usd']:.2f}", "CCA",
                "charged_tariff", "direct", src,
                "CEA's own per-kWh product adder; printed kWh base does not "
                "exactly equal bill_periods_electric.csv's net_kwh for this "
                "period (undisclosed by the statement) -- recorded as printed, "
                "not reconciled to net_kwh"])
        sst = r["adders"].get("state_surcharge_tax")
        if sst is not None:
            rows.append([
                stmt, period, "", "state_surcharge_tax", "", "",
                f"{sst['usd']:.2f}", "CCA", "charged_fee", "direct", src,
                "a flat per-period surcharge printed as a dollar amount only, "
                "not a per-kWh rate"])
        rows.append([
            stmt, period, "", "total_printed", "", "", f"{r['total_usd']:.2f}",
            "CCA", "charged_total", "direct", src,
            "sum of the per-TOU cells and adders above, to the cent; "
            f"reconciles to data/bill_periods_electric.csv's cca_generation "
            f"(${r['committed_cca_generation_usd']:.2f}) with a diff of "
            f"${r['reconciliation_diff_usd']:+.2f}"])
    return rows


def write(dest_dir=None):
    dest = pathlib.Path(dest_dir or DATA)
    rows = _rows()
    path = dest / "cca_generation_rates.csv"
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(_HEADER)
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path, rows


def main():
    path, rows = write()
    n_stmt = len({r[0] for r in rows})
    n_periods = len({(r[0], r[1]) for r in rows})
    print(f"wrote {path}: {len(rows)} rows across {n_stmt} statements, "
          f"{n_periods} billing periods")
    return 0


if __name__ == "__main__":
    sys.exit(main())
