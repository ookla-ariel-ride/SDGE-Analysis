#!/usr/bin/env python3
"""
Detailed-bill PDF parser — the committed script behind every bill-derived artifact.

WHY THIS EXISTS
    data/electric_bill_summary.csv and data/gas_bill_summary.csv were originally
    produced by an in-session pdfplumber extraction with no committed script, which
    violates the CLAUDE.md §9 gate "every committed artifact regenerable by its
    committed script". This script parses the bill PDFs and regenerates them, and
    extends the same extraction across the full downloaded corpus.

INPUTS (private, gitignored — see DATA-SOURCES-CHEATSHEET.md §D for how to fetch them)
    private/1-raw-data/electric-bills/sdge_electric_<statement-date>.pdf
    private/1-raw-data/gas-bills/sdge_gas_<statement-date>.pdf
    Filenames carry the STATEMENT date. Periods are read from the PDF text, never
    inferred from the filename: one statement can contain two billing periods when a
    rate change splits it mid-cycle (CLAUDE.md §1 — parse periods, not files).

OUTPUTS (committed, de-identified)
    data/bill_periods_electric.csv  one row per electric billing period
    data/bill_periods_gas.csv       one row per gas billing period
    data/bill_tou_detail.csv        long format: per period × section × season × TOU
                                    period → kWh and $/kWh as printed on the bill
    data/electric_bill_summary.csv  regenerated (same schema as the original)
    data/gas_bill_summary.csv       regenerated (same schema as the original)

PRIVACY (CLAUDE.md §4)
    The PDFs contain name, service address, account number, meter number, and the
    CCA service-delivery-point id. NONE of those are extracted. Outputs carry only
    dates, quantities, rates, and dollar amounts. How the household pays its bills
    (arrangements, schedules, balances owed) is private-tier and never emitted.

FAIL-CLOSED
    Every period must yield the required fields. A statement that parses to a missing
    period, day count, usage figure, or charge total raises SystemExit rather than
    emitting a zero — a silently-zeroed row would corrupt every downstream sum.
"""
import os
import pathlib
import re
import sys

import pandas as pd
import pdfplumber


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (matches the other scripts,
    so the private/verify sandbox works unchanged)."""
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
GAS_DIR = ROOT / "private" / "1-raw-data" / "gas-bills"

# The original committed summaries covered these statements only (the analysis year).
# Regenerating exactly this window is the reproduction gate in main().
SUMMARY_STATEMENTS_ELEC = [
    "2025-08-01", "2025-09-02", "2025-10-01", "2025-10-31", "2025-12-03",
    "2026-01-06", "2026-02-02", "2026-03-04", "2026-04-02", "2026-05-04",
    "2026-06-03", "2026-07-02",
]
SUMMARY_STATEMENTS_GAS = [
    "2025-07-30", "2025-08-28", "2025-09-29", "2025-10-29", "2025-11-28",
    "2025-12-30", "2026-01-29", "2026-03-02", "2026-03-31", "2026-04-30",
    "2026-06-01", "2026-06-30",
]

# Two quirks of the printed numbers: negatives use U+2212 MINUS SIGN rather than an
# ASCII hyphen, and rates are printed without a leading zero ("$.04013").
_NUM = r"[−-]?(?:[\d,]*\.\d+|[\d,]+)"


def _f(s):
    """'1,904' -> 1904.0 ; '−140' (U+2212 minus) -> -140.0"""
    return float(str(s).replace(",", "").replace("−", "-").replace("$", ""))


def _text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _statement_date(path):
    m = re.search(r"(\d{4}-\d{2}-\d{2})\.pdf$", str(path))
    if not m:
        raise SystemExit(f"filename does not carry a statement date: {path}")
    return m.group(1)


# ---------------------------------------------------------------------------
# Electric
# ---------------------------------------------------------------------------
def parse_electric(path):
    """Return one dict per billing period found in a single electric statement."""
    txt = _text(path)
    stmt = _statement_date(path)

    # Authoritative period markers live in the "Detail of Current Charges" sections.
    marks = list(re.finditer(
        r"Billing Period:\s*(\d+/\d+/\d+)\s*-\s*(\d+/\d+/\d+)\s*Total Days:\s*(\d+)", txt))
    if not marks:
        raise SystemExit(f"{path.name}: no 'Billing Period ... Total Days' marker found")

    # CCA generation totals are printed in their own later section, keyed by period.
    cca = {}
    for blk in re.finditer(
            r"Billing Period:\s*(\d+/\d+/\d+)\s*-\s*(\d+/\d+/\d+)(.*?)"
            r"Total CCA Electric Generation Charges\s*\$(" + _NUM + r")", txt, re.S):
        cca[f"{blk.group(1)} - {blk.group(2)}"] = _f(blk.group(4))

    rows, tou = [], []
    for i, m in enumerate(marks):
        period = f"{m.group(1)} - {m.group(2)}"
        days = int(m.group(3))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(txt)
        chunk = txt[m.end():end]

        def one(pattern, what, required=True):
            hit = re.search(pattern, chunk)
            if not hit:
                if required:
                    raise SystemExit(
                        f"{path.name} [{period}]: could not find {what}. The bill layout "
                        f"may have changed — fix the parser rather than defaulting a value.")
                return None
            return _f(hit.group(1))

        net = one(r"Total Usage:\s*(" + _NUM + r")", "net usage")
        gross = one(r"Non Bypassable Charges Usage:\s*(" + _NUM + r")", "gross (NBC) usage")
        delivery = one(r"Total Electric Service\s*\$(" + _NUM + r")", "Total Electric Service")
        bsc = one(r"Base Services Charge\s*\$[\d.]+\s*x\s*\d+\s*days\s*(" + _NUM + r")",
                  "Base Services Charge", required=False)
        # Generation is either billed by the CCA on its own pages, or bundled into
        # SDG&E's "Total Electric Service" when the account is not on a CCA. This
        # household predates its CCA enrollment inside the downloaded corpus, so both
        # shapes occur; generation_provider records which, because sdge_delivery
        # INCLUDES generation in the bundled case and excludes it under a CCA.
        gen = cca.get(period)
        if gen is None:
            if re.search(r"CCA Electric Generation|CLEAN ENERGY ALLIANCE", chunk):
                raise SystemExit(
                    f"{path.name} [{period}]: the bill mentions CCA generation but no "
                    f"total was parsed (found periods: {sorted(cca)}) — fix the parser.")
            provider, gen = "bundled", 0.0
            current = delivery
        else:
            provider = "CCA"
            current = round(delivery + gen, 2)

        rows.append(dict(
            statement_date=stmt, period=period, days=days,
            generation_provider=provider,
            net_kwh=net, gross_kwh=gross,
            sdge_delivery=delivery, cca_generation=gen,
            current_charges=current,
            base_services_charge=bsc,
        ))

        # TOU detail. The PDF is two-column, so extract_text() interleaves unrelated
        # right-column lines between a block's "kWh used" and "Rate/kWh" rows. Anchor
        # on the usage row, take the NEXT rate row after it, and read the season from
        # the nearest preceding "<SEASON> USAGE" header (that header line itself is
        # sometimes split across the column boundary, so it is looked up separately).
        # Anchor on the "<SEASON> USAGE On-Peak ..." headers: the phrase "kWh used"
        # also appears in the usage graphic and the glossary boilerplate, so scanning
        # for it directly picks up rows that are not TOU tables at all.
        gen_marker = re.search(r"Electricity Generation \(Details below\)", chunk)
        gen_at = gen_marker.start() if gen_marker else len(chunk)
        WINDOW = 600  # chars after a header within which its two data rows appear

        for h in re.finditer(r"(SUMMER|WINTER) USAGE\s+On-Peak", chunk):
            win = chunk[h.end():h.end() + WINDOW]
            u = re.search(r"kWh used\s+(" + _NUM + r")\s+(" + _NUM + r")\s+(" + _NUM + r")", win)
            r_row = re.search(
                r"Rate/kWh\s+\$(" + _NUM + r")\s+\$(" + _NUM + r")\s+\$(" + _NUM + r")", win)
            if not u or not r_row:
                raise SystemExit(
                    f"{path.name} [{period}]: a '{h.group(1)} USAGE' header has no "
                    f"kWh/rate rows within {WINDOW} chars — bill layout changed.")
            section = "generation" if h.start() >= gen_at else "delivery"
            for j, tp in enumerate(("on_peak", "off_peak", "super_off_peak")):
                tou.append(dict(
                    statement_date=stmt, period=period, section=section,
                    season=h.group(1).lower(), tou_period=tp,
                    kwh=_f(u.group(1 + j)), rate_per_kwh=_f(r_row.group(1 + j)),
                ))
    return rows, tou


# ---------------------------------------------------------------------------
# Gas
# ---------------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_gas(path):
    txt = _text(path)
    stmt = _statement_date(path)

    m = re.search(r"Gas\s+([A-Z][a-z]{2} \d{1,2}, \d{4})\s*-\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s+"
                  r"(" + _NUM + r")\s+Therms\s+(" + _NUM + r")", txt)
    if not m:
        raise SystemExit(f"{path.name}: could not find the gas billing-period summary line")
    start_s, end_s, therms, amount = m.group(1), m.group(2), _f(m.group(3)), _f(m.group(4))

    tot = re.search(r"Total Gas Service\s*\$(" + _NUM + r")", txt)
    if not tot:
        raise SystemExit(f"{path.name}: could not find 'Total Gas Service'")
    total_service = _f(tot.group(1))

    # Baseline / non-baseline $/therm. When a rate change splits the period the bill
    # prints two rate blocks; the committed summary carried the FIRST (earlier) one.
    rate = re.search(r"Rate/Therm\s+\$(" + _NUM + r")\s+\$(" + _NUM + r")", txt)
    baseline = _f(rate.group(1)) if rate else None
    nonbaseline = _f(rate.group(2)) if rate else None

    mm, dd, yyyy = re.match(r"([A-Za-z]{3})\w* (\d{1,2}), (\d{4})", end_s).groups()
    file_month = f"{mm.lower()} {yyyy}"

    return dict(
        statement_date=stmt, period=f"{start_s} - {end_s}",
        period_end_month=file_month, therms=therms,
        total_gas_service=total_service, billed_amount=amount,
        baseline_rate=baseline, nonbaseline_rate=nonbaseline,
    )


# ---------------------------------------------------------------------------
def main():
    for d in (ELEC_DIR, GAS_DIR):
        if not d.is_dir():
            raise SystemExit(
                f"missing {d} — download the statements first "
                f"(DATA-SOURCES-CHEATSHEET.md §D describes the bulk-download method)")

    elec_rows, tou_rows = [], []
    for f in sorted(ELEC_DIR.glob("sdge_electric_*.pdf")):
        r, t = parse_electric(f)
        elec_rows.extend(r)
        tou_rows.extend(t)
    gas_rows = [parse_gas(f) for f in sorted(GAS_DIR.glob("sdge_gas_*.pdf"))]

    if not elec_rows or not gas_rows:
        raise SystemExit("no statements parsed — is the corpus staged?")

    elec = pd.DataFrame(elec_rows)
    # Sort chronologically by the period's start date. Sorting on the period STRING
    # would put "10/1/25 - 10/27/25" before "9/26/25 - 9/30/25".
    elec["_start"] = pd.to_datetime(elec.period.str.split(" - ").str[0], format="%m/%d/%y")
    elec = elec.sort_values("_start").drop(columns="_start")
    gas = pd.DataFrame(gas_rows).sort_values("statement_date")
    tou = pd.DataFrame(tou_rows)

    # Duplicate billing periods would double-count a year (CLAUDE.md §1).
    dupes = elec.period[elec.period.duplicated()].tolist()
    if dupes:
        raise SystemExit(f"duplicate electric billing periods parsed: {dupes}")

    elec.to_csv(DATA / "bill_periods_electric.csv", index=False)
    gas.to_csv(DATA / "bill_periods_gas.csv", index=False)
    tou.to_csv(DATA / "bill_tou_detail.csv", index=False)

    # ---- regenerate the original summaries (the reproduction gate) ----
    # These two predate this script and were committed with CRLF line endings; they are
    # rewritten with CRLF so the gate is a genuine byte-for-byte reproduction of the
    # known-good originals rather than a content-only match. New artifacts above use LF.
    es = elec[elec.statement_date.isin(SUMMARY_STATEMENTS_ELEC)]
    es = es[["period", "days", "net_kwh", "gross_kwh",
             "sdge_delivery", "cca_generation", "current_charges"]]
    es.to_csv(DATA / "electric_bill_summary.csv", index=False, lineterminator="\r\n")

    gs = gas[gas.statement_date.isin(SUMMARY_STATEMENTS_GAS)].copy()
    gs = gs.rename(columns={"period_end_month": "file_month"})
    gs = gs[["file_month", "therms", "total_gas_service",
             "baseline_rate", "nonbaseline_rate"]].sort_values("file_month")
    gs.to_csv(DATA / "gas_bill_summary.csv", index=False, lineterminator="\r\n")

    print(f"electric: {len(elec)} billing periods from "
          f"{elec.statement_date.nunique()} statements "
          f"({elec.statement_date.min()} .. {elec.statement_date.max()})")
    print(f"gas:      {len(gas)} billing periods from "
          f"{gas.statement_date.nunique()} statements "
          f"({gas.statement_date.min()} .. {gas.statement_date.max()})")
    print(f"tou rows: {len(tou)}")
    print(f"summary window: electric {len(es)} periods, {es.days.sum()} days, "
          f"${es.current_charges.sum():,.2f}; gas {len(gs)} periods, "
          f"${gs.total_gas_service.sum():,.2f}")


if __name__ == "__main__":
    main()
