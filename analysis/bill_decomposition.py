#!/usr/bin/env python3
"""Decompose the year-over-year change in one electric bill into its causes.

THE QUESTION. Two comparable early-summer billing periods sit at opposite ends of
the statement corpus, and the second is nearly eight times the first:

    5/25/24 - 6/25/24   32 days   bundled   net  346 kWh   $48.25
    5/29/26 - 6/26/26   29 days   CCA       net  987 kWh   $398.56

Four things moved at once between them: the tariff vintage, the quantity of
energy taken from the grid, its distribution across TOU periods, and the
generation provider (SDG&E bundled -> Clean Energy Alliance from 12/27/24). This
script separates them, from the statements, and reports what it cannot separate.

THE TRAP, HANDLED FIRST: "AMOUNT DUE" IS NOT A COST SERIES.
private/1-raw-data/electric_billing_history_2024-2026.csv - SDG&E's own billing
history export - reports current_charges of $0.00 for the 5/25/24-6/25/24 period,
and for every statement through 2025-04-02. A decomposition built on that column
would be comparing $0.00 against $398.56. It is not that those months were free:
under NEM 2.0 the energy component accrues against an annual true-up instead of
being billed monthly, so the payment column and the cost series are different
things. billing_mode_scan() establishes this FROM STATEMENT TEXT before any
arithmetic runs, over the whole corpus, and export_reconciliation() explains the
export's number for every statement in terms of the statement's own lines. The
cost series used here is the per-period accrual, which is what
data/bill_periods_electric.csv's current_charges column already holds.

WHAT PRICES WHAT (the CCA authority boundary from rates_history.py is binding).
  delivery      SDG&E's printed "Electricity Delivery" TOU table, both periods.
                The UDC is the charged tariff in both provider eras.
  supply 2024   SDG&E's printed "Electricity Generation" TOU table. During a
                BUNDLED period that table is the tariff SDG&E charged.
  supply 2026   the CEA per-TOU lines on the statement's Community Choice
                Aggregation page ("Generation On-Peak Summer 205 kWh X $0.51684").
                This is the only place the charged CCA rate appears; parse_bills.py
                does not extract it, so rates_history.cca_generation() fails closed
                and this script reads it from the PDF itself.
  NOT supply    on the 2026 statement SDG&E ALSO prints an "Electricity Generation"
                TOU table. That is its bundled-generation (EECC) comparison, not a
                charge: the statement cancels it to the cent on the next line
                ("Electricity Generation Credit -180.46" against $180.46 of printed
                table), and it differs from what CEA actually charged by -6.8% to
                +104.3% cell by cell. It is never priced as supply here. It is used
                for exactly one purpose, under its own name: as the SAME-DATE
                bundled counterfactual that separates the provider effect from the
                vintage effect, which is the one question it can answer, because it
                is SDG&E's own bundled rate on the 2026 date.

PROVIDER EFFECT vs VINTAGE EFFECT (both moved inside the window).
For each cell the charged supply rate went from g0 (SDG&E bundled, 2024) to g1
(CEA, 2026). Both providers' rates are observable on the 2026 statement, so the
change splits with no estimation:

    g1 - g0  =  (s1 - g0)   supply VINTAGE: SDG&E bundled, 2024 -> 2026
             +  (g1 - s1)   PROVIDER: CEA vs SDG&E bundled, same date

where s1 is the printed bundled comparison on the 2026 statement. Delivery is
SDG&E in both periods, so its whole change is vintage.

THAT SPLIT IS PUBLISHED ONLY WHERE IT MEANS WHAT IT SAYS. It needs a base rate
that is a tariff, and on a cell billed as a net export the base rate is $0 by
settlement — the export went to the annual true-up — not by tariff. Running
q(s1 - g0) there would measure the export-to-import regime change and print it
as a supply vintage; the algebra would still balance, which is exactly what
makes it easy to miss. So delivery_vintage, supply_vintage and provider are
computed ONLY over the cells billed as net imports in BOTH periods (three of
six here), and every other cell's whole price movement q(p1 - p0) is published
under its own name, netting_regime_usd: the change in what the netting regime
billed, attributed to neither a tariff vintage nor the provider. Four terms,
summing exactly to the price effect on each weight basis:

    price effect  =  delivery_vintage + supply_vintage + provider
                  +  netting_regime            (the cells with no base tariff)

Two further consequences of the provider break are NOT in that per-cell term, and
are carried as their own named lines in non_energy_bridge, because the statements
charge them per period rather than per TOU cell: the riders a CCA customer pays
separately and a bundled customer pays inside the generation rate (PCIA, the
incremental procurement cost adjustment, the economic development program
credit), and CEA's own product adders (Clean Impact Plus, its state surcharge).
That the bundled rate carries PCIA is on the face of the base statement itself -
"$1.97 of your Electricity Generation Charge is your bundled PCIA charge" - which
is quoted into the artifact. decomposition.aggregate.provider_effect_read_whole
therefore adds the per-cell provider term to those two lines and states the
comparison directly: what CEA plus the CCA-only riders charged, against what
SDG&E's own bundled table on the SAME statement would have charged for the SAME
kWh. That figure exists on current quantities only - the riders are billed on the
period's own kWh, so there is no base-quantity counterfactual for them.

THE SAME RESTRICTION BINDS THAT WHOLE-PERIOD FIGURE, and for the same reason. The
printed bundled table prices a cell only where the cell was billed as a net
import; on a current net-export cell it prints $0 because the export settled at
the annual true-up, while CEA still books a credit there. Netting those two would
put a settlement outcome inside a number the report calls a provider PRICE effect
- the identical defect the per-cell split avoids - so the whole-period comparison
runs over the CURRENT net-import cells only, and the export cells' difference is
published beside it as unallocated_netting_settlement_usd, named so it cannot be
read as a price. The riders and the CEA product adders are period-level lines
charged once on the period's kWh, not per cell, so they cannot be restricted to a
cell set and stay whole on the CEA side; the artifact says so in scope.

PRICES HERE ARE EFFECTIVE BILLED RATES, NOT TARIFF RATES.
Under NEM 2.0 a net-negative TOU bucket prints "Rate/kWh $.00000" on the SDG&E
side: in-period exports settle at true-up, not on the monthly statement. So a
cell's price is charge / net kWh, which is the rate that actually produced the
dollars being decomposed, and on an export cell it is $0 on the SDG&E side even
though a tariff rate exists. Three of the six cells are net-import in BOTH
periods; those are the only cells where the price change is a like-for-like
tariff comparison, and like_for_like reports them separately (issue #3's
"restrict the comparison to like-for-like and say so"). The other three flip
between export and import, and that flip lands - correctly and visibly - in the
interaction term, which is why it is reported and never allocated away.

THE DECOMPOSITION.
Per cell c = (season, TOU period), with q the net kWh and p the effective billed
rate, the energy change is split by the standard exact identities:

    Laspeyres price     sum q0 (p1 - p0)      quantity  sum p0 (q1 - q0)
    Paasche   price     sum q1 (p1 - p0)      quantity  sum p1 (q1 - q0)
    interaction         sum (p1 - p0)(q1 - q0)

    price_L + quantity_L + interaction  ==  price_L + quantity_P
                                        ==  price_P + quantity_L  ==  dEnergy

NEITHER EFFECT IS PUBLISHED AS A POINT. Paasche price == Laspeyres price +
interaction, so quoting "price accounts for $109.94" (the Paasche figure) hands
the entire interaction to price while the note beside it says the interaction is
jointly owned and not allocated. Those two statements cannot both be true. This
artifact publishes each effect as the INTERVAL between its two readings —
price -$257.22 to +$109.94, quantity +$68.99 to +$436.15 — whose width is
exactly the interaction term, together with both exact pairings. The interaction
is published and never split; nothing here, and nothing in the report, states a
figure as the amount price or quantity "accounts for".

The quantity effect is split again into scale and TOU mix, exactly, with
Q = sum q and w = q / Q:

    scale  (Q1 - Q0) sum p0 w0        mix  Q1 sum p0 (w1 - w0)

NOTE that Q here is NET kWh (346 and 987): NEM nets exports against imports
inside each cell, so a cell's q can be negative and a share w is not bounded by
[0, 1]. The scale/mix split is an exact accounting identity on those shares, not
a behavioural share of consumption, and the artifact says so.

Everything outside the TOU energy lines is carried as its own named term rather
than spread over the cells: the fixed charge (a flat $16.00/month Monthly Service
Fee in 2024, a $0.79343/day Base Services Charge in 2026), non-bypassable charges
and the wildfire fund charge, the CCA unbundling riders, taxes, the CEA product
adders, and the applied NEM generation credit - which is the largest single term
in this comparison and belongs to neither price nor quantity: the 5/25/24-6/25/24
statement applied $128.39 of credit against a $128.39 energy charge, cancelling
it exactly and leaving $48.25 of fixed and non-bypassable charges as the whole
bill. The 5/29/26-6/26/26 statement applied $5.06.

Everything reconciles to the cent, and the artifact publishes the residual.

Inputs   data/bill_periods_electric.csv          period totals (the cost series)
         data/bill_tou_detail.csv                printed SDG&E TOU lines
         private/1-raw-data/electric-bills/*.pdf statement text, CEA TOU lines
         private/1-raw-data/electric_billing_history_2024-2026.csv  the export
Output   data/bill_decomposition.json            written atomically; run twice ->
                                                 byte-identical
"""
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile

import pdfplumber


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (matches the other
    scripts, so the private/verify sandbox convention works unchanged)."""
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
HISTORY_CSV = ROOT / "private" / "1-raw-data" / "electric_billing_history_2024-2026.csv"

# The two periods being compared, and why these two: the first and last statements
# in the corpus, both early-summer (so both straddle the 6/1 winter->summer tariff
# boundary and carry the same seasonal mix of weather and daylight), 24 months
# apart, on opposite sides of the 12/27/24 provider break.
BASE = {"statement": "2024-06-27", "period": "5/25/24 - 6/25/24"}
CURRENT = {"statement": "2026-07-02", "period": "5/29/26 - 6/26/26"}

SEASONS = ("winter", "summer")
TOU = ("on_peak", "off_peak", "super_off_peak")
CELLS = [(s, t) for s in SEASONS for t in TOU]

# Printed negatives are U+2212 MINUS SIGN, and rates print without a leading zero.
_NUM = r"[−-]?(?:[\d,]*\.\d+|[\d,]+)"
_PRINTED_TOU = {"On-Peak": "on_peak", "Off-Peak": "off_peak",
                "Super Off-Peak": "super_off_peak"}


def _f(s):
    """'1,904' -> 1904.0 ; '−140' (U+2212 minus) -> -140.0"""
    return float(str(s).replace(",", "").replace("−", "-").replace("$", ""))


def _c(x):
    """Round to cents. Every printed number is already at cent precision; this
    keeps derived sums from carrying binary-float dust into the artifact."""
    return round(x, 2) + 0.0        # + 0.0 folds -0.0 to 0.0 so the artifact is stable


def _r(x, n):
    return round(x, n) + 0.0


def _period_dates(period):
    """'5/25/24 - 6/25/24' -> (date, date)."""
    def one(s):
        m, d, y = (int(x) for x in s.strip().split("/"))
        return dt.date(2000 + y, m, d)
    a, b = period.split(" - ")
    return one(a), one(b)


def _years_apart(base_period, current_period):
    """Calendar distance between the two periods' midpoints, in years. Both periods
    are early-summer, so this is the horizon the price index is annualised over."""
    def mid(p):
        a, b = _period_dates(p)
        return a + (b - a) / 2
    return (mid(current_period) - mid(base_period)).days / 365.25


def _statement_path(stmt):
    p = ELEC_DIR / f"sdge_electric_{stmt}.pdf"
    if not p.exists():
        raise SystemExit(
            f"statement {stmt} not found at {p} — this analysis reads the bill PDFs "
            "directly (the CEA per-TOU generation rates appear nowhere else, and the "
            "billing-mode question has to be settled from statement text)")
    return p


_TEXT_CACHE = {}


def statement_text(stmt):
    if stmt not in _TEXT_CACHE:
        with pdfplumber.open(_statement_path(stmt)) as pdf:
            _TEXT_CACHE[stmt] = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    return _TEXT_CACHE[stmt]


def statement_dates():
    """Every statement in the corpus, in date order."""
    dates = sorted(m.group(1) for m in
                   (re.search(r"(\d{4}-\d{2}-\d{2})\.pdf$", str(p))
                    for p in ELEC_DIR.glob("sdge_electric_*.pdf")) if m)
    if not dates:
        raise SystemExit(f"no electric statements found in {ELEC_DIR}")
    return dates


# ---------------------------------------------------------------------------
# The committed bill artifacts — the cost series and the printed SDG&E TOU lines
# ---------------------------------------------------------------------------
def _read_csv(path):
    if not path.exists():
        raise SystemExit(f"required artifact missing: {path}")
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def periods():
    """data/bill_periods_electric.csv keyed by (statement_date, period)."""
    out = {}
    for r in _read_csv(DATA / "bill_periods_electric.csv"):
        out[(r["statement_date"], r["period"])] = {
            "statement_date": r["statement_date"],
            "period": r["period"],
            "days": int(r["days"]),
            "generation_provider": r["generation_provider"],
            "net_kwh": _f(r["net_kwh"]),
            "gross_kwh": _f(r["gross_kwh"]),
            "sdge_delivery": _f(r["sdge_delivery"]),
            "cca_generation": _f(r["cca_generation"]),
            "current_charges": _f(r["current_charges"]),
        }
    return out


def tou_detail(stmt, period):
    """The printed SDG&E TOU lines for one period, summed over rate segments:
    {(section, season, tou_period): {"kwh": .., "usd": ..}}. Segments are summed
    because the two periods split their seasons differently (7/25 days in 2024,
    3/26 in 2026) and the comparison is per season x TOU cell."""
    out = {}
    for r in _read_csv(DATA / "bill_tou_detail.csv"):
        if r["statement_date"] != stmt or r["period"] != period:
            continue
        key = (r["section"], r["season"], r["tou_period"])
        kwh, rate = _f(r["kwh"]), _f(r["rate_per_kwh"])
        cell = out.setdefault(key, {"kwh": 0.0, "usd": 0.0})
        cell["kwh"] += kwh
        cell["usd"] += kwh * rate
    for cell in out.values():
        cell["usd"] = _c(cell["usd"])
    if not out:
        raise SystemExit(f"no bill_tou_detail rows for {stmt} / {period}")
    return out


# ---------------------------------------------------------------------------
# Statement text: the printed line items
# ---------------------------------------------------------------------------
_BLOCK_HEAD = re.compile(r"Electricity (Delivery|Generation) \(Details below\)")
_USAGE_HEAD = re.compile(r"(WINTER|SUMMER) USAGE On-Peak Off-Peak Super Off-Peak")
_KWH_USED = re.compile(rf"kWh used\s+({_NUM})\s+({_NUM})\s+({_NUM})")
_RATE_LINE = re.compile(rf"Rate/kWh\s+\$({_NUM})\s+\$({_NUM})\s+\$({_NUM})")
_DAYS_CHARGE = re.compile(
    rf"(\d+) Days Charge\s*\$({_NUM})\s*\+\s*\$({_NUM})\s*\+\s*\$({_NUM})\s*=\s*({_NUM})")


def printed_tou_blocks(stmt):
    """Every printed SDG&E TOU energy block on a statement.

    Line-oriented rather than one multi-line regex: on the 2026 statements the
    right-hand "Breakdown of Current Charges" column is interleaved into the same
    extracted lines ("kWh used 31 −14 28 Distribution $116.93"), so each field is
    found by its own anchor and only the first three numbers on a line are taken.
    """
    lines = statement_text(stmt).splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        head = _BLOCK_HEAD.search(lines[i])
        if not head:
            i += 1
            continue
        section = head.group(1).lower()
        found = {}
        j = i + 1
        while j < len(lines) and len(found) < 4:
            if _BLOCK_HEAD.search(lines[j]):
                break
            for name, pat in (("season", _USAGE_HEAD), ("kwh", _KWH_USED),
                              ("rate", _RATE_LINE), ("charge", _DAYS_CHARGE)):
                if name in found:
                    continue
                m = pat.search(lines[j])
                if m:
                    found[name] = m
            j += 1
        if len(found) != 4:
            raise SystemExit(
                f"{stmt}: an 'Electricity {section.title()} (Details below)' block at "
                f"line {i} is missing {sorted(set(['season','kwh','rate','charge']) - set(found))} "
                "— the statement layout changed and this parser must not guess")
        season = found["season"].group(1).lower()
        kwh = [_f(x) for x in found["kwh"].groups()]
        rate = [_f(x) for x in found["rate"].groups()]
        amt = [_f(x) for x in found["charge"].groups()[1:4]]
        total = _f(found["charge"].group(5))
        if _c(sum(amt)) != _c(total):
            raise SystemExit(f"{stmt}: printed {section} {season} block does not add "
                             f"up: {amt} != {total}")
        blocks.append({"section": section, "season": season,
                       "days": int(found["charge"].group(1)),
                       "cells": {TOU[k]: {"kwh": kwh[k], "rate": rate[k],
                                          "usd": amt[k]} for k in range(3)},
                       "total_usd": total})
        i = j
    if not blocks:
        raise SystemExit(f"{stmt}: no 'Electricity ... (Details below)' TOU blocks found")
    return blocks


_CCA_LINE = re.compile(
    rf"Generation (On-Peak|Off-Peak|Super Off-Peak) (Summer|Winter)\s+({_NUM}) kWh X "
    rf"\$({_NUM})\s+({_NUM})")


def cca_block(stmt):
    """The CEA page: the charged CCA generation, per season x TOU cell, plus the
    product adders and the printed total. Returns None on a bundled statement.

    This is the ONLY source for the charged CCA rate anywhere in the repository —
    rates_history.cca_generation() fails closed precisely because parse_bills.py
    does not extract these lines."""
    txt = statement_text(stmt)
    start = txt.find("Community Choice Aggregation (CCA) Electric Generation Charges")
    if start < 0:
        return None
    end = txt.find("Total CCA Electric Generation Charges", start)
    if end < 0:
        raise SystemExit(f"{stmt}: CCA section has no 'Total CCA Electric Generation "
                         "Charges' line")
    body = txt[start:end]
    tail = txt[end:]
    cells = {}
    for m in _CCA_LINE.finditer(body):
        key = (m.group(2).lower(), _PRINTED_TOU[m.group(1)])
        if key in cells:
            raise SystemExit(f"{stmt}: CCA cell {key} printed twice")
        kwh, rate, usd = _f(m.group(3)), _f(m.group(4)), _f(m.group(5))
        # CEA prices unrounded kWh and prints the quantity rounded to whole kWh, so
        # kWh x rate reproduces the printed amount only to within half a kWh of rate
        # (205 x $0.51684 = $105.95 against a printed $106.02, i.e. 205.13 kWh). The
        # tolerance is exactly that, so a genuinely wrong rate or amount still fails.
        if abs(kwh * rate - usd) > abs(rate) * 0.5 + 0.015:
            raise SystemExit(f"{stmt}: CCA line {key} does not multiply out to within "
                             f"the printed kWh rounding: {kwh} x {rate} != {usd}")
        cells[key] = {"kwh": kwh, "rate": rate, "usd": usd}
    if not cells:
        raise SystemExit(f"{stmt}: CCA section carries no per-TOU generation lines")
    adders = {}
    m = re.search(rf"Clean Impact Plus\s+([\d,]+) kWh X \$({_NUM})\s+({_NUM})", body)
    if m:
        adders["clean_impact_plus"] = {"kwh": _f(m.group(1)), "rate": _f(m.group(2)),
                                       "usd": _f(m.group(3))}
    m = re.search(rf"State Surcharge Tax\s+({_NUM})", body)
    if m:
        adders["state_surcharge_tax"] = {"usd": _f(m.group(1))}
    total = _f(re.search(rf"Total CCA Electric Generation Charges \$({_NUM})",
                         tail).group(1))
    named = sum(c["usd"] for c in cells.values()) + sum(a["usd"] for a in adders.values())
    if _c(named) != _c(total):
        raise SystemExit(
            f"{stmt}: the named CCA lines sum to {_c(named)} against a printed total of "
            f"{total} — an unnamed line was added to the CEA page and this script must "
            "not silently absorb it")
    return {"cells": cells, "adders": adders, "total_usd": total}


# Every non-TOU-energy line this analysis knows how to name, in the order the
# statements print them. A line the statements print and this table does not name
# breaks the reconciliation below, by construction: nothing is absorbed silently.
_LINE_PATTERNS = [
    ("monthly_service_fee", rf"Monthly Service Fee\s+({_NUM})"),
    ("base_services_charge", rf"Base Services Charge \$(?:{_NUM}) x \d+ days\s+({_NUM})"),
    ("non_bypassable_charges", rf"Non-Bypassable Charges\s+({_NUM})"),
    ("wildfire_fund_charge",
     rf"Wildfire Fund Charge\s+[\d,]+ kWh x \${_NUM}\s+({_NUM})"),
    ("electricity_generation_credit", rf"Electricity Generation Credit\s+({_NUM})"),
    ("pcia", rf"PCIA \d+\s+[\d,]+ kWh x \$?({_NUM}\s+{_NUM})"),
    ("incremental_procurement_cost_adjustment",
     rf"Incremental Procurement Cost Adjustment\s+[\d,]+ kWh x \${_NUM}\s+({_NUM})"),
    ("economic_development_program_credit",
     rf"Economic Development Program Credit\s+({_NUM})"),
    ("applied_generation_credit_energy", rf"Applied Generation Credit\s+({_NUM})"),
    ("total_electric_charges", rf"Total Electric Charges \$({_NUM})"),
    ("total_taxes_and_fees", rf"Total Taxes & Fees on Electric Charges \$({_NUM})"),
    ("total_electric_service", rf"Total Electric Service \$({_NUM})"),
]


def charge_lines(stmt):
    """The named non-TOU-energy lines on a statement's Detail of Current Charges.

    Absent lines are absent, not zero: the caller decides which are required, so a
    parser regression shows up as a missing key rather than as a silent $0.

    These patterns read the WHOLE statement, which is right for the two single-period
    statements this analysis compares and wrong for a statement carrying two billing
    periods (2025-10-31 does). That case is not silently mishandled: a repeated line
    with two different values raises here, and printed_tou_blocks() would hand
    period_ledger() both periods' TOU blocks, which fails its cross-check against
    data/bill_tou_detail.csv for the single period under test."""
    txt = statement_text(stmt)
    out = {}
    for name, pat in _LINE_PATTERNS:
        hits = re.findall(pat, txt)
        if not hits:
            continue
        if name == "pcia":                       # "PCIA 2023 1,001 kWh x $.02828 28.31"
            out[name] = _f(hits[0].split()[-1])
            continue
        if name == "applied_generation_credit_energy":
            # printed twice: once against the energy block, once inside the tax
            # block. The tax block's own printed total already carries the second,
            # so only the first is taken here.
            out[name] = _f(hits[0])
            continue
        if len(set(hits)) > 1:
            raise SystemExit(f"{stmt}: '{name}' printed with conflicting values {hits}")
        out[name] = _f(hits[0])
    return out


# ---------------------------------------------------------------------------
# 1. The billing-mode question, from statement text
# ---------------------------------------------------------------------------
_NEM_LEDGER = "Net Metering Account Summary"
_NEM_DEFER = re.compile(
    r"\*Payment not required for NEM charges\. Your account will true up on "
    r"([A-Z][a-z]{2} \d{1,2}, \d{4})")
_ACCRUES = re.compile(
    r"Payment is not required at this time\.\s*\n?\s*Your account will true-up on "
    r"([A-Z][a-z]{2} \d{1,2}, \d{4})\.")
_PAY_REQUIRED = re.compile(r"Payment Required This Month:\s*(Yes|No)")
_CLIMATE_CREDIT = re.compile(rf"California Climate Credit\s+(-{_NUM})")
_TOTAL_MONTH = re.compile(rf"Total Charges this Month \$({_NUM})")


def billing_mode_scan():
    """Read every statement and record, per statement, whether the energy charge was
    payable that month or accrued to the annual true-up — and which printed sentence
    says so. This runs before any arithmetic, over the whole corpus, because the
    answer decides which column is the cost series."""
    rows = []
    per = periods()
    by_stmt = {}
    for (stmt, _), p in per.items():
        by_stmt.setdefault(stmt, []).append(p)
    for stmt in statement_dates():
        txt = statement_text(stmt)
        ledger = _NEM_LEDGER in txt
        defer = _NEM_DEFER.search(txt)
        accrue = _ACCRUES.search(txt)
        payreq = _PAY_REQUIRED.search(txt)
        month = _TOTAL_MONTH.findall(txt)
        credit = _CLIMATE_CREDIT.findall(txt)
        if payreq is None:
            raise SystemExit(f"{stmt}: no 'Payment Required This Month' line — the "
                             "Net Energy Metering Summary page is what settles the "
                             "billing mode and it is not on this statement")
        if defer:
            quote = ("*Payment not required for NEM charges. Your account will true "
                     f"up on {defer.group(1)}")
            true_up = defer.group(1)
        elif accrue:
            quote = ("Payment is not required at this time. Your account will "
                     f"true-up on {accrue.group(1)}.")
            true_up = accrue.group(1)
        else:
            quote = f"Payment Required This Month: {payreq.group(1)}"
            true_up = None
        rows.append({
            "statement_date": stmt,
            "nem_ledger_block_printed": ledger,
            "payment_required_this_month": payreq.group(1),
            "true_up_date": true_up,
            "deferral_sentence": quote,
            "total_charges_this_month_usd": _f(month[0]) if month else None,
            "california_climate_credit_usd":
                _c(sum(_f(c) for c in dict.fromkeys(credit))) if credit else 0.0,
            "period_accrual_usd": _c(sum(p["current_charges"]
                                         for p in by_stmt.get(stmt, []))),
            "periods_on_statement": sorted(p["period"] for p in by_stmt.get(stmt, [])),
        })
    return rows


def billing_mode_finding(scan):
    """Turn the scan into the answer, naming the statements that establish it."""
    accruing = [r for r in scan if r["payment_required_this_month"] == "No"]
    payable = [r for r in scan if r["payment_required_this_month"] == "Yes"]
    ledger = [r for r in scan if r["nem_ledger_block_printed"]]
    no_ledger = [r for r in scan if not r["nem_ledger_block_printed"]]
    if not ledger or not no_ledger:
        raise SystemExit("the corpus no longer contains both presentation styles — "
                         "the mode-change date cannot be located")
    last_ledger = max(r["statement_date"] for r in ledger)
    first_without = min(r["statement_date"] for r in no_ledger)
    if any(r["statement_date"] > last_ledger for r in ledger) or \
            any(r["statement_date"] < first_without for r in no_ledger):
        raise SystemExit("the NEM-ledger presentation is not a single contiguous run")
    return {
        "question": ("does a 2024 statement bill the energy component monthly, or "
                     "accrue it to the annual NEM true-up?"),
        "answer": "accrues to the annual true-up",
        "answer_holds_for_both_compared_periods": True,
        "established_by": [
            {"statement_date": BASE["statement"],
             "role": "the base period of this comparison",
             "quote": next(r["deferral_sentence"] for r in scan
                           if r["statement_date"] == BASE["statement"]),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": CURRENT["statement"],
             "role": "the current period of this comparison",
             "quote": next(r["deferral_sentence"] for r in scan
                           if r["statement_date"] == CURRENT["statement"]),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": last_ledger,
             "role": "last statement printing a separate Net Metering Account Summary",
             "quote": next(r["deferral_sentence"] for r in scan
                           if r["statement_date"] == last_ledger),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": first_without,
             "role": "first statement without it — the presentation change",
             "quote": next(r["deferral_sentence"] for r in scan
                           if r["statement_date"] == first_without),
             "net_energy_metering_summary": "Payment Required This Month: No"},
        ],
        "annual_settlement_statements": [
            {"statement_date": r["statement_date"],
             "true_up_period_ends": r["periods_on_statement"],
             "net_energy_metering_summary": "Payment Required This Month: Yes"}
            for r in payable],
        "statements_accruing": len(accruing),
        "statements_payable": len(payable),
        "what_changed_and_when": {
            "the_billing_mode_did_not_change": (
                "every statement in the corpus except the two annual settlement "
                "statements prints Payment Required This Month: No, and both compared "
                "periods are accruing statements. The energy component has accrued to "
                "the annual true-up throughout."),
            "the_presentation_changed_on": first_without,
            "presentation_change": (
                f"through {last_ledger} the accrued charge sat in a separate "
                f"'{_NEM_LEDGER}' block and the Account Summary's Current Charges line "
                f"read $0.00; from {first_without} that block is gone and the same "
                "accrued charge appears on the Account Summary line, still not payable. "
                "The billing-history export follows the Account Summary line, which is "
                "why its current_charges column steps off $0.00 on that date without "
                "anything about the billing having changed."),
        },
    }


def export_reconciliation(scan):
    """Explain the billing-history export's current_charges for EVERY statement in
    terms of the statement's own lines, so the discrepancy is resolved rather than
    noted. The identity, verified per statement:

        export current_charges
          = 0                                        while the NEM ledger block is
                                                     printed (the accrual sits in the
                                                     ledger, off the Account Summary)
          + the account-level California Climate Credit
          + the period accrual sum                   once the ledger block is gone
    """
    if not HISTORY_CSV.exists():
        raise SystemExit(
            f"required input missing: {HISTORY_CSV} — issue #3 asks for the export's "
            "$0.00 rows to be reconciled against the statements, which needs the export")
    export = {r["statement_date"]: _f(r["current_charges"])
              for r in _read_csv(HISTORY_CSV)}
    rows, unexplained = [], []
    for r in scan:
        stmt = r["statement_date"]
        if stmt not in export:
            raise SystemExit(f"statement {stmt} has no row in {HISTORY_CSV.name}")
        deferred = r["period_accrual_usd"] if r["nem_ledger_block_printed"] else 0.0
        expected = _c(r["period_accrual_usd"] - deferred
                      + r["california_climate_credit_usd"])
        row = {
            "statement_date": stmt,
            "export_current_charges_usd": export[stmt],
            "period_accrual_usd": r["period_accrual_usd"],
            "deferred_to_nem_ledger_usd": _c(deferred),
            "california_climate_credit_usd": r["california_climate_credit_usd"],
            "explained_usd": expected,
            "residual_usd": _c(export[stmt] - expected),
        }
        rows.append(row)
        if row["residual_usd"] != 0.0:
            unexplained.append(f"{stmt}: {row['residual_usd']:+.2f}")
    if unexplained:
        raise SystemExit(
            "the billing-history export does not reconcile to the statements on: "
            + "; ".join(unexplained))
    return rows


# ---------------------------------------------------------------------------
# 2. One period, taken apart
# ---------------------------------------------------------------------------
def period_ledger(spec):
    """Every dollar of one billing period, split into TOU energy cells and named
    non-energy terms, cross-checked against the committed artifacts and reconciled
    to the printed period total to the cent."""
    stmt, period = spec["statement"], spec["period"]
    per = periods()
    if (stmt, period) not in per:
        raise SystemExit(f"{stmt} / {period} is not in data/bill_periods_electric.csv")
    meta = per[(stmt, period)]
    detail = tou_detail(stmt, period)
    lines = charge_lines(stmt)
    cca = cca_block(stmt)
    is_cca = meta["generation_provider"] == "CCA"
    if is_cca != (cca is not None):
        raise SystemExit(
            f"{stmt}: bill_periods_electric says provider={meta['generation_provider']} "
            f"but the statement {'has' if cca else 'has no'} a CCA generation page")

    # (a) the printed SDG&E TOU tables, cross-checked against bill_tou_detail
    printed = {}
    for blk in printed_tou_blocks(stmt):
        for tou, v in blk["cells"].items():
            key = (blk["section"], blk["season"], tou)
            agg = printed.setdefault(key, {"kwh": 0.0, "usd": 0.0, "rates": []})
            agg["kwh"] += v["kwh"]
            agg["usd"] += v["usd"]
            agg["rates"].append(v["rate"])
    for key, agg in printed.items():
        agg["usd"] = _c(agg["usd"])
        if key not in detail:
            raise SystemExit(f"{stmt}: printed {key} has no bill_tou_detail row")
        if abs(agg["kwh"] - detail[key]["kwh"]) > 1e-9 or \
                agg["usd"] != detail[key]["usd"]:
            raise SystemExit(
                f"{stmt}: printed {key} ({agg['kwh']} kWh, ${agg['usd']}) disagrees with "
                f"data/bill_tou_detail.csv ({detail[key]['kwh']} kWh, "
                f"${detail[key]['usd']}) — the committed artifact and the PDF must agree")

    # (b) the charged cells: delivery is SDG&E either way; supply is the bundled
    #     generation table in 2024 and the CEA page under a CCA contract.
    cells = {}
    for season, tou in CELLS:
        d = printed.get(("delivery", season, tou))
        if d is None:
            continue
        if is_cca:
            sup = cca["cells"].get((season, tou))
            if sup is None:
                raise SystemExit(f"{stmt}: delivery cell ({season}, {tou}) has no CEA "
                                 "generation line")
            if abs(sup["kwh"] - d["kwh"]) > 1e-9:
                raise SystemExit(
                    f"{stmt}: CEA bills {sup['kwh']} kWh in ({season}, {tou}) against "
                    f"{d['kwh']} kWh of delivery — the two pages disagree on quantity")
            supply = {"usd": sup["usd"], "rate": sup["rate"], "source": "cea_cca_page"}
            comparison = printed.get(("generation", season, tou))
            if comparison is None:
                raise SystemExit(f"{stmt}: no printed bundled generation comparison for "
                                 f"({season}, {tou})")
            # effective, like every other rate here: comparison dollars / net kWh, so
            # the provider term is a difference of two dollar figures on the same kWh
            comparison_rate = (comparison["usd"] / d["kwh"]) if d["kwh"] else None
            comparison_usd = comparison["usd"]
        else:
            g = printed.get(("generation", season, tou))
            if g is None:
                raise SystemExit(f"{stmt}: delivery cell ({season}, {tou}) has no "
                                 "generation line")
            supply = {"usd": g["usd"], "rate": None, "source": "sdge_bundled_table"}
            comparison_rate = None
            comparison_usd = None
        q = d["kwh"]
        usd = _c(d["usd"] + supply["usd"])
        cells[(season, tou)] = {
            "kwh": q,
            "delivery_usd": d["usd"],
            "supply_usd": supply["usd"],
            "supply_source": supply["source"],
            "usd": usd,
            # effective billed rates: charge / net kWh. On the SDG&E side a
            # net-negative bucket prints $.00000 and is charged $0 (exports settle
            # at true-up), so the effective rate is 0 there by fact, not by rounding.
            "delivery_rate_effective": d["usd"] / q if q else None,
            "supply_rate_effective": supply["usd"] / q if q else None,
            "rate_effective": usd / q if q else None,
            "sdge_bundled_comparison_rate": comparison_rate,
            # the same-date bundled counterfactual in DOLLARS. On a cell billed as a
            # net export this is $0 by settlement (the export went to the annual
            # true-up), not because SDG&E's bundled tariff for that cell is zero, so
            # provider_effect_whole_period() refuses to net it against what CEA
            # charged there — see that function.
            "sdge_bundled_comparison_usd": comparison_usd,
        }
    if len(cells) != len(CELLS):
        raise SystemExit(f"{stmt} / {period}: only {len(cells)} of {len(CELLS)} "
                         "season x TOU cells were printed")

    energy_delivery = _c(sum(c["delivery_usd"] for c in cells.values()))
    energy_supply = _c(sum(c["supply_usd"] for c in cells.values()))

    # (c) the named non-energy terms
    fixed = lines.get("base_services_charge", lines.get("monthly_service_fee"))
    if fixed is None:
        raise SystemExit(f"{stmt}: neither a Monthly Service Fee nor a Base Services "
                         "Charge line was found")
    nbc = _c(lines.get("non_bypassable_charges", 0.0)
             + lines.get("wildfire_fund_charge", 0.0))
    unbundling = _c(lines.get("pcia", 0.0)
                    + lines.get("incremental_procurement_cost_adjustment", 0.0)
                    + lines.get("economic_development_program_credit", 0.0))
    nem_credit = lines.get("applied_generation_credit_energy", 0.0)
    taxes = lines.get("total_taxes_and_fees", 0.0)
    # The statement's own three printed subtotals, checked against each other and
    # against the committed artifact before any of the lines above are trusted.
    for name in ("total_electric_charges", "total_taxes_and_fees",
                 "total_electric_service"):
        if name not in lines:
            raise SystemExit(f"{stmt}: no '{name.replace('_', ' ')}' subtotal printed")
    if _c(lines["total_electric_charges"] + taxes) != _c(lines["total_electric_service"]):
        raise SystemExit(
            f"{stmt}: printed subtotals do not add up: "
            f"{lines['total_electric_charges']} + {taxes} != "
            f"{lines['total_electric_service']}")
    if _c(lines["total_electric_service"]) != _c(meta["sdge_delivery"]):
        raise SystemExit(
            f"{stmt}: printed Total Electric Service ${lines['total_electric_service']} "
            f"disagrees with data/bill_periods_electric.csv's sdge_delivery "
            f"${meta['sdge_delivery']}")
    cca_adders = _c(sum(a["usd"] for a in cca["adders"].values())) if is_cca else 0.0

    # On a CCA statement the printed SDG&E generation table is cancelled to the cent
    # by the Electricity Generation Credit on the next line. Assert it, then carry
    # the pair at zero: it must not enter the decomposition as supply.
    printed_bundled = _c(sum(v["usd"] for k, v in printed.items()
                             if k[0] == "generation")) if is_cca else 0.0
    gen_offset = lines.get("electricity_generation_credit", 0.0)
    if is_cca and _c(printed_bundled + gen_offset) != 0.0:
        raise SystemExit(
            f"{stmt}: the printed bundled generation table (${printed_bundled}) is not "
            f"cancelled by the Electricity Generation Credit (${gen_offset}) — on a CCA "
            "statement it must net to zero, or it is not a comparison")

    ledger = {
        "energy_delivery": energy_delivery,
        "energy_supply": energy_supply,
        "fixed_charge": fixed,
        "non_bypassable": nbc,
        "unbundling_riders": unbundling,
        "applied_nem_generation_credit": nem_credit,
        "taxes_and_fees": taxes,
        "cca_product_adders": cca_adders,
        "printed_bundled_comparison_net_of_its_credit":
            _c(printed_bundled + gen_offset),
    }
    total = _c(sum(ledger.values()))
    if total != _c(meta["current_charges"]):
        raise SystemExit(
            f"{stmt} / {period}: the named lines sum to ${total} against the period's "
            f"${meta['current_charges']} in data/bill_periods_electric.csv — a line on "
            "this statement is not named by this script, and absorbing it silently is "
            "exactly the failure mode this reconciliation exists to catch")
    return {"meta": meta, "cells": cells, "ledger": ledger, "total_usd": total,
            "printed_bundled_comparison_usd": printed_bundled if is_cca else None}


# ---------------------------------------------------------------------------
# 3. The decomposition
# ---------------------------------------------------------------------------
def _key(season, tou):
    return f"{season}.{tou}"


def decompose(base, current):
    """Laspeyres, Paasche and the interaction residual, per season x TOU cell and
    in aggregate, plus the split of the price effect into delivery vintage, supply
    vintage, provider and netting regime.

    The first three of those are causal attributions and are computed only over the
    cells billed as net imports in BOTH periods, which are the only cells with an
    observable base tariff. Every other cell's price movement goes to the fourth,
    netting_regime, which names the export-to-import settlement change rather than
    dressing it up as a vintage. Price and quantity are published as intervals, not
    points, because the interaction term is reported and never allocated."""
    per_cell = []
    agg = {k: 0.0 for k in ("price_l", "price_p", "quantity_l", "quantity_p",
                            "interaction", "delivery_vintage_l", "supply_vintage_l",
                            "provider_l", "netting_regime_l", "delivery_vintage_p",
                            "supply_vintage_p", "provider_p", "netting_regime_p",
                            "base_usd", "current_usd")}
    q0_total = sum(c["kwh"] for c in base["cells"].values())
    q1_total = sum(c["kwh"] for c in current["cells"].values())
    scale = mix = 0.0
    for season, tou in CELLS:
        b, c = base["cells"][(season, tou)], current["cells"][(season, tou)]
        q0, q1 = b["kwh"], c["kwh"]
        p0 = b["rate_effective"] or 0.0
        p1 = c["rate_effective"] or 0.0
        d0 = b["delivery_rate_effective"] or 0.0
        d1 = c["delivery_rate_effective"] or 0.0
        g0 = b["supply_rate_effective"] or 0.0
        g1 = c["supply_rate_effective"] or 0.0
        s1 = c["sdge_bundled_comparison_rate"]
        if s1 is None:
            raise SystemExit(
                f"({season}, {tou}): the current period carries no SDG&E bundled "
                "comparison rate, so the provider effect cannot be separated from the "
                "vintage effect for this cell — refusing to estimate one")
        # The price effect, split by cause — but only where a base tariff exists.
        # Delivery is SDG&E in both periods, so all of its movement is vintage, and
        # the supply movement splits at the same-date bundled comparison s1. On a
        # cell billed as a net export in either period, d0/g0 (or d1/g1) are $0
        # because the energy settled at true-up, not because a tariff said so, so
        # the whole of that cell's price movement is published as netting_regime
        # and none of it is attributed to a vintage or to the provider.
        like_for_like_cell = q0 > 0 and q1 > 0
        if like_for_like_cell:
            # A cell that is a net import in both periods should carry three real
            # tariffs. If any of them prints $0 anyway, that $0 is a settlement or a
            # parsing artefact rather than a price, and every term built on it —
            # q(s1 - g0), q(d1 - d0), and like_for_like's percentages, which divide by
            # these — would be a settlement figure wearing a tariff's name. Refuse.
            zero = [n for n, v in (("base delivery rate", d0), ("base supply rate", g0),
                                   ("same-date bundled comparison rate", s1)) if v == 0]
            if zero:
                raise SystemExit(
                    f"({season}, {tou}) is billed as a net import in both periods but its "
                    + " and ".join(zero) + " is $0 — on an import cell a $0 is not a "
                    "tariff, and attributing this cell to a vintage or to the provider "
                    "would publish that $0 as a price; refusing to estimate one")
            split_l = {"delivery_vintage_l": q0 * (d1 - d0),
                       "supply_vintage_l": q0 * (s1 - g0),
                       "provider_l": q0 * (g1 - s1),
                       "netting_regime_l": 0.0}
            split_p = {"delivery_vintage_p": q1 * (d1 - d0),
                       "supply_vintage_p": q1 * (s1 - g0),
                       "provider_p": q1 * (g1 - s1),
                       "netting_regime_p": 0.0}
        else:
            split_l = {"delivery_vintage_l": 0.0, "supply_vintage_l": 0.0,
                       "provider_l": 0.0, "netting_regime_l": q0 * (p1 - p0)}
            split_p = {"delivery_vintage_p": 0.0, "supply_vintage_p": 0.0,
                       "provider_p": 0.0, "netting_regime_p": q1 * (p1 - p0)}
        terms = {
            "price_l": q0 * (p1 - p0),
            "price_p": q1 * (p1 - p0),
            "quantity_l": p0 * (q1 - q0),
            "quantity_p": p1 * (q1 - q0),
            "interaction": (p1 - p0) * (q1 - q0),
            **split_l,
            **split_p,
            "base_usd": b["usd"],
            "current_usd": c["usd"],
        }
        for k, v in terms.items():
            agg[k] += v
        w0 = q0 / q0_total if q0_total else 0.0
        w1 = q1 / q1_total if q1_total else 0.0
        mix += q1_total * p0 * (w1 - w0)
        scale += (q1_total - q0_total) * p0 * w0
        per_cell.append({
            "cell": _key(season, tou),
            "season": season,
            "tou_period": tou,
            "base_kwh": q0,
            "current_kwh": q1,
            "base_usd": b["usd"],
            "current_usd": c["usd"],
            "change_usd": _c(c["usd"] - b["usd"]),
            "base_rate_effective": _r(p0, 5),
            "current_rate_effective": _r(p1, 5),
            "base_delivery_rate": _r(d0, 5),
            "current_delivery_rate": _r(d1, 5),
            "base_supply_rate": _r(g0, 5),
            "current_supply_rate": _r(g1, 5),
            "sdge_bundled_comparison_rate_current_date": _r(s1, 5),
            "both_periods_net_import": like_for_like_cell,
            "base_net_import": q0 > 0,
            "current_net_import": q1 > 0,
            "laspeyres_price_usd": _c(terms["price_l"]),
            "laspeyres_quantity_usd": _c(terms["quantity_l"]),
            "paasche_price_usd": _c(terms["price_p"]),
            "paasche_quantity_usd": _c(terms["quantity_p"]),
            "interaction_usd": _c(terms["interaction"]),
            "delivery_vintage_usd_laspeyres": _c(terms["delivery_vintage_l"]),
            "supply_vintage_usd_laspeyres": _c(terms["supply_vintage_l"]),
            "provider_usd_laspeyres": _c(terms["provider_l"]),
            "netting_regime_usd_laspeyres": _c(terms["netting_regime_l"]),
            "delivery_vintage_usd_paasche": _c(terms["delivery_vintage_p"]),
            "supply_vintage_usd_paasche": _c(terms["supply_vintage_p"]),
            "provider_usd_paasche": _c(terms["provider_p"]),
            "netting_regime_usd_paasche": _c(terms["netting_regime_p"]),
            "price_split_attributed": like_for_like_cell,
        })
    energy_change = _c(agg["current_usd"] - agg["base_usd"])
    # The Laspeyres quantity term — and therefore the scale/mix split of it — values
    # every kWh at the BASE effective price, which on a base-export cell is $0 by
    # settlement. That is arithmetically what the base bill charged, and it is why
    # this end of the quantity interval is the low one; but a reader must not take
    # $0 there for a base tariff, so the cells and the kWh it prices at zero are
    # named. The Paasche end prices the same kWh at the current tariff, and the gap
    # between the two ends is the published, unallocated interaction.
    zero_base_price_cells = [r["cell"] for r in per_cell
                             if not r["base_net_import"]]
    zero_base_price_kwh = _r(sum(r["current_kwh"] - r["base_kwh"] for r in per_cell
                                 if not r["base_net_import"]), 1)
    attributed_cells = [r["cell"] for r in per_cell if r["price_split_attributed"]]
    unattributed_cells = [r["cell"] for r in per_cell if not r["price_split_attributed"]]
    if not attributed_cells:
        raise SystemExit(
            "no cell is billed as a net import in both periods, so no delivery/supply "
            "vintage or provider term is observable at all — refusing to publish a "
            "price split whose every term would be the netting regime under another name")
    out = {
        "per_cell": per_cell,
        "aggregate": {
            "base_energy_usd": _c(agg["base_usd"]),
            "current_energy_usd": _c(agg["current_usd"]),
            "energy_change_usd": energy_change,
            "laspeyres": {"price_usd": _c(agg["price_l"]),
                          "quantity_usd": _c(agg["quantity_l"])},
            "paasche": {"price_usd": _c(agg["price_p"]),
                        "quantity_usd": _c(agg["quantity_p"])},
            "interaction_usd": _c(agg["interaction"]),
            "quantity_split_laspeyres_basis": {
                "scale_usd": _c(scale),
                "tou_mix_usd": _c(mix),
                "base_net_kwh": q0_total,
                "current_net_kwh": q1_total,
                "note": ("shares are of NET kWh, which NEM lets go negative in a cell, "
                         "so a share is not bounded by [0,1]; this is an exact "
                         "accounting identity on those shares, not a share of "
                         "consumption"),
                "cells_priced_at_a_settlement_zero_base_rate": zero_base_price_cells,
                "net_kwh_change_valued_at_zero": zero_base_price_kwh,
                "settlement_zero_caveat": (
                    "scale and mix split the LASPEYRES end of the quantity interval, so "
                    "they value every kWh at the base period's effective price. On the "
                    "cells named above that price is $0 because the 2024 export settled "
                    "at the annual true-up, not because a tariff said $0, so the "
                    f"{zero_base_price_kwh} kWh of net swing in those cells is carried "
                    "here at zero. That is what the base bill charged and it is why this "
                    "is the LOW end of the interval; it is not a statement that the "
                    "energy was worth nothing. The Paasche end prices the same kWh at "
                    "the current tariff, and the whole gap between the two ends is the "
                    "interaction term, published and allocated to neither side"),
            },
            # The causal split of the price effect. delivery_vintage, supply_vintage
            # and provider are computed ONLY over the cells with an observable base
            # tariff; netting_regime carries the rest, named for what it is.
            "price_split_scope": {
                "attributed_terms": ["delivery_vintage_usd", "supply_vintage_usd",
                                     "provider_usd"],
                "attributed_cells": attributed_cells,
                "unattributed_term": "netting_regime_usd",
                "unattributed_cells": unattributed_cells,
                "why": (
                    "delivery_vintage, supply_vintage and provider are differences of "
                    "two tariffs, so they are computed only on the cells billed as net "
                    "imports in BOTH periods. On the other cells the base (or current) "
                    "effective price is $0 because the energy settled at the annual "
                    "true-up rather than because a tariff said $0, so attributing their "
                    "movement to a tariff vintage or to the provider would publish the "
                    "export-to-import settlement change under someone else's name — and "
                    "the algebra would still balance. Their whole price movement is "
                    "netting_regime_usd instead: measured, reconciled, and not "
                    "decomposed further, because these statements do not observe a "
                    "like-for-like base tariff for them"),
                "identity": ("delivery_vintage + supply_vintage + provider + "
                             "netting_regime == the price effect, on each weight basis"),
            },
            "price_split_laspeyres_basis": {
                "delivery_vintage_usd": _c(agg["delivery_vintage_l"]),
                "supply_vintage_usd": _c(agg["supply_vintage_l"]),
                "provider_usd": _c(agg["provider_l"]),
                "netting_regime_usd": _c(agg["netting_regime_l"]),
            },
            "price_split_paasche_basis": {
                "delivery_vintage_usd": _c(agg["delivery_vintage_p"]),
                "supply_vintage_usd": _c(agg["supply_vintage_p"]),
                "provider_usd": _c(agg["provider_p"]),
                "netting_regime_usd": _c(agg["netting_regime_p"]),
            },
            # Price and quantity are published as INTERVALS, not as points. Paasche
            # price == Laspeyres price + interaction, so naming either endpoint as
            # "the" price effect silently allocates the whole interaction to it while
            # claiming not to. The interval width IS the interaction term.
            "reading": {
                "convention": "bounds — neither effect is published as a point",
                "basis": (
                    "the price effect and the quantity effect are each reported as the "
                    "interval between their Laspeyres (base-weight) and Paasche "
                    "(current-weight) readings. Both intervals are exactly as wide as "
                    "the interaction term, which is published and allocated to neither "
                    "side; no figure here is the amount price or quantity 'accounts "
                    "for'. The two endpoints pair exactly, one from each side, and both "
                    "pairings are stated below"),
                "price_usd_low": _c(min(agg["price_l"], agg["price_p"])),
                "price_usd_high": _c(max(agg["price_l"], agg["price_p"])),
                "quantity_usd_low": _c(min(agg["quantity_l"], agg["quantity_p"])),
                "quantity_usd_high": _c(max(agg["quantity_l"], agg["quantity_p"])),
                "interval_width_usd": _c(agg["interaction"]),
                "exact_pairings": [
                    {"price_basis": "laspeyres", "price_usd": _c(agg["price_l"]),
                     "quantity_basis": "paasche", "quantity_usd": _c(agg["quantity_p"]),
                     "sum_usd": _c(agg["price_l"] + agg["quantity_p"])},
                    {"price_basis": "paasche", "price_usd": _c(agg["price_p"]),
                     "quantity_basis": "laspeyres", "quantity_usd": _c(agg["quantity_l"]),
                     "sum_usd": _c(agg["price_p"] + agg["quantity_l"])},
                ],
                "quantity_split_basis": (
                    "the scale and TOU-mix figures below split the Laspeyres end of the "
                    "quantity interval; they do not apply to the Paasche end"),
                "of_which_scale_usd": _c(scale),
                "of_which_tou_mix_usd": _c(mix),
                "interaction_usd": _c(agg["interaction"]),
                "interaction_note": (
                    "the spread between the two readings, published and never split. It "
                    "is concentrated in the three cells that flip between net export and "
                    "net import: on an export cell SDG&E prints $.00000 and charges $0, "
                    "so the effective price is 0, and the whole of that cell's change is "
                    "a joint movement of price and quantity that neither term owns"),
            },
        },
        "identities": {
            "laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd":
                _c(agg["price_l"] + agg["quantity_l"] + agg["interaction"]),
            "laspeyres_price_plus_paasche_quantity_usd":
                _c(agg["price_l"] + agg["quantity_p"]),
            "paasche_price_plus_laspeyres_quantity_usd":
                _c(agg["price_p"] + agg["quantity_l"]),
            "scale_plus_mix_usd": _c(scale + mix),
            "laspeyres_quantity_usd": _c(agg["quantity_l"]),
            "price_split_sum_usd": _c(agg["delivery_vintage_l"]
                                      + agg["supply_vintage_l"] + agg["provider_l"]
                                      + agg["netting_regime_l"]),
            "laspeyres_price_usd": _c(agg["price_l"]),
            "price_split_sum_paasche_usd": _c(agg["delivery_vintage_p"]
                                              + agg["supply_vintage_p"]
                                              + agg["provider_p"]
                                              + agg["netting_regime_p"]),
            "paasche_price_usd": _c(agg["price_p"]),
            "attributed_price_split_sum_usd": _c(agg["delivery_vintage_l"]
                                                 + agg["supply_vintage_l"]
                                                 + agg["provider_l"]),
            "attributed_cells_laspeyres_price_usd": _c(
                sum(r["laspeyres_price_usd"] for r in per_cell
                    if r["price_split_attributed"])),
        },
    }
    checks = [
        ("Laspeyres price + Laspeyres quantity + interaction",
         out["identities"]["laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd"],
         energy_change),
        ("Laspeyres price + Paasche quantity",
         out["identities"]["laspeyres_price_plus_paasche_quantity_usd"], energy_change),
        ("Paasche price + Laspeyres quantity",
         out["identities"]["paasche_price_plus_laspeyres_quantity_usd"], energy_change),
        ("scale + TOU mix", out["identities"]["scale_plus_mix_usd"],
         out["identities"]["laspeyres_quantity_usd"]),
        ("delivery vintage + supply vintage + provider + netting regime (Laspeyres)",
         out["identities"]["price_split_sum_usd"],
         out["identities"]["laspeyres_price_usd"]),
        ("delivery vintage + supply vintage + provider + netting regime (Paasche)",
         out["identities"]["price_split_sum_paasche_usd"],
         out["identities"]["paasche_price_usd"]),
        # the attributed terms cover the like-for-like cells and nothing else
        ("the attributed vintage/provider terms over the like-for-like cells",
         out["identities"]["attributed_price_split_sum_usd"],
         out["identities"]["attributed_cells_laspeyres_price_usd"]),
    ]
    for name, got, want in checks:
        if abs(got - want) > 0.01:
            raise SystemExit(f"decomposition identity broken: {name} = {got} != {want}")
    return out


def provider_effect_whole_period(cells, cca_product_adders_usd, unbundling_riders_usd):
    """The provider effect read whole: what the CCA arrangement charged for supply over
    the current period, against what SDG&E's own bundled table on the SAME statement
    would have charged for the SAME kWh.

    RESTRICTED TO THE CURRENT PERIOD'S NET-IMPORT CELLS, for the same reason the
    per-cell split is. On a cell billed as a net export the printed bundled comparison
    is $0 — the export settled at the annual true-up rather than being priced — while
    CEA still books a credit against it. Netting those two would put a settlement
    outcome inside a figure the report calls a provider PRICE effect, and the
    arithmetic would balance while doing it. So the export cells' difference is
    published separately, under its own name, as unallocated netting/settlement.

    The riders and the CEA product adders are NOT restricted, because they are not
    per-cell quantities: the statements charge them once per period on the period's
    kWh. They stay whole on the CEA side, which the returned dict says out loud.

    A net-export cell whose bundled comparison is NOT $0 would be an observed bundled
    export counterfactual — a real comparison, which this restriction must not throw
    away silently — so it raises instead."""
    attributed, unallocated = [], []
    for season, tou in CELLS:
        c = cells[(season, tou)]
        cf = c.get("sdge_bundled_comparison_usd")
        if cf is None:
            raise SystemExit(
                f"({season}, {tou}): no same-date SDG&E bundled comparison in dollars, so "
                "the whole-period provider effect has no counterfactual for this cell — "
                "refusing to estimate one")
        row = {
            "cell": _key(season, tou),
            "current_kwh": c["kwh"],
            "current_net_import": c["kwh"] > 0,
            "cea_charged_usd": _c(c["supply_usd"]),
            "sdge_bundled_same_date_usd": _c(cf),
            "difference_usd": _c(c["supply_usd"] - cf),
        }
        if c["kwh"] > 0:
            attributed.append(row)
        else:
            if _c(cf) != 0.0:
                raise SystemExit(
                    f"({season}, {tou}) is billed as a net export yet carries a printed "
                    f"bundled comparison of ${_c(cf)} — that is an OBSERVED bundled "
                    "export counterfactual, which this restriction was written to "
                    "exclude only because it does not exist; it must not be discarded "
                    "silently, so the exclusion rule has to be revisited")
            unallocated.append(row)
    if not attributed:
        raise SystemExit(
            "no cell is billed as a net import in the current period, so SDG&E's printed "
            "bundled table prices nothing and there is no provider counterfactual at all "
            "— refusing to publish a provider effect whose whole base is a settlement $0")
    cea_supply = _c(sum(r["cea_charged_usd"] for r in attributed))
    counterfactual = _c(sum(r["sdge_bundled_same_date_usd"] for r in attributed))
    if counterfactual <= 0:
        raise SystemExit(
            f"the same-date bundled counterfactual over the net-import cells is "
            f"${counterfactual} — a provider effect cannot be expressed against it")
    paid = _c(cea_supply + cca_product_adders_usd + unbundling_riders_usd)
    unallocated_usd = _c(sum(r["difference_usd"] for r in unallocated))
    return {
        "basis": ("current quantities only, over the cells billed as net imports in the "
                  "current period: the riders are charged on this period's kWh, so there "
                  "is no base-quantity counterfactual for them"),
        "scope": {
            "attributed_cells": [r["cell"] for r in attributed],
            "excluded_cells": [r["cell"] for r in unallocated],
            "why": (
                "SDG&E's printed bundled table prices a cell only where that cell was "
                "billed as a net import. On a current net-export cell it prints $0 "
                "because the export settled at the annual true-up, not because the "
                "bundled tariff for that cell is zero — so subtracting it from what CEA "
                "booked there would measure the netting regime and publish it as a "
                "provider price effect. Those cells are excluded and their difference is "
                "reported below as unallocated_netting_settlement_usd, which is a "
                "settlement figure and not a provider price effect"),
            "riders_are_not_restricted": (
                "cea_product_adders_usd and cca_unbundling_riders_usd are period-level "
                "lines billed once on the period's own kWh, not per TOU cell, so they "
                "cannot be restricted to a cell set; they are carried whole on the CEA "
                "side of the comparison"),
        },
        "cea_charged_supply_usd": cea_supply,
        "cea_product_adders_usd": _c(cca_product_adders_usd),
        "cca_unbundling_riders_usd": _c(unbundling_riders_usd),
        "total_paid_for_supply_usd": paid,
        "sdge_bundled_same_date_counterfactual_usd": counterfactual,
        "provider_effect_usd": _c(paid - counterfactual),
        "provider_effect_pct": _r(100.0 * (paid / counterfactual - 1.0), 1),
        "unallocated_netting_settlement_usd": unallocated_usd,
        "unallocated_netting_settlement_note": (
            "the current net-export cells' difference between what CEA booked and the $0 "
            "SDG&E printed. The $0 is deferred export settlement, not a bundled tariff of "
            "zero, so this is neither a provider price effect nor a tariff vintage; it is "
            "published here undecomposed"),
        # The unrestricted difference, published only so the restriction is auditable:
        # it is the whole-provider figure plus the excluded settlement cells, and it is
        # NOT a provider price effect, which is why it is not named as one.
        "all_cells_including_settlement_difference_usd":
            _c(paid - counterfactual + unallocated_usd),
        "all_cells_note": (
            "what the comparison would read if the current net-export cells were netted "
            "in against the $0 SDG&E printed for them. Published for audit only: that $0 "
            "is deferred export settlement, so this figure mixes a provider price effect "
            "with a settlement outcome and is not quoted as a provider effect anywhere"),
        "per_cell": attributed + unallocated,
    }


def like_for_like(cells, years_apart):
    """The subset of cells that are net-import in BOTH periods — the only cells whose
    price change is a like-for-like tariff comparison rather than a change of NEM
    regime. Reported separately, as issue #3 asks.

    The aggregate here is a FIXED-WEIGHT price index, never a blended $/kWh: the TOU
    mix moved hard between these two periods (on-peak is 5.3% of the base kWh and
    25.7% of the current), so a ratio of blended rates would read as a price move
    when most of it is mix. Laspeyres holds base quantities, Paasche holds current
    quantities, Fisher is their geometric mean."""
    rows = []
    for row in cells:
        if not row["both_periods_net_import"]:
            continue
        rows.append({
            "cell": row["cell"],
            "base_kwh": row["base_kwh"],
            "current_kwh": row["current_kwh"],
            "base_rate_effective": row["base_rate_effective"],
            "current_rate_effective": row["current_rate_effective"],
            "change_pct": _r(100.0 * (row["current_rate_effective"]
                                      / row["base_rate_effective"] - 1.0), 1),
            "delivery_change_pct": _r(100.0 * (row["current_delivery_rate"]
                                               / row["base_delivery_rate"] - 1.0), 1),
            "supply_vintage_change_pct": _r(
                100.0 * (row["sdge_bundled_comparison_rate_current_date"]
                         / row["base_supply_rate"] - 1.0), 1),
            "provider_change_pct": _r(
                100.0 * (row["current_supply_rate"]
                         / row["sdge_bundled_comparison_rate_current_date"] - 1.0), 1),
        })
    if not rows:
        raise SystemExit("no cell is net-import in both periods — there is no "
                         "like-for-like price comparison to report")
    src = {row["cell"]: row for row in cells}
    q0p0 = sum(r["base_kwh"] * r["base_rate_effective"] for r in rows)
    q0p1 = sum(r["base_kwh"] * r["current_rate_effective"] for r in rows)
    q1p0 = sum(r["current_kwh"] * r["base_rate_effective"] for r in rows)
    q1p1 = sum(r["current_kwh"] * r["current_rate_effective"] for r in rows)
    lasp, paas = q0p1 / q0p0, q1p1 / q1p0
    fisher = (lasp * paas) ** 0.5
    split = {}
    for basis in ("laspeyres", "paasche"):
        split[basis] = {
            f"{term}_usd": _c(sum(src[r["cell"]][f"{term}_usd_{basis}"] for r in rows))
            for term in ("delivery_vintage", "supply_vintage", "provider")}
    return {
        "cells": rows,
        "excluded_cells": [row["cell"] for row in cells
                           if not row["both_periods_net_import"]],
        "exclusion_reason": ("net-export in at least one period, where SDG&E prints "
                             "$.00000 and charges $0 because the export settles at "
                             "true-up — no tariff price is observable"),
        "base_kwh": sum(r["base_kwh"] for r in rows),
        "current_kwh": sum(r["current_kwh"] for r in rows),
        "price_index": {
            "laspeyres_pct": _r(100.0 * (lasp - 1.0), 1),
            "paasche_pct": _r(100.0 * (paas - 1.0), 1),
            "fisher_pct": _r(100.0 * (fisher - 1.0), 1),
            "years_apart": _r(years_apart, 2),
            "is_total_change_not_a_rate": True,
            "no_annual_rate_path": (
                "every percentage here is the TOTAL change between two matched "
                f"endpoints {_r(years_apart, 2)} years apart, and nothing here is a "
                "per-year figure. Two endpoints cannot establish an annual rate path: "
                "there is no matched observation between them — these are the first and "
                "last statements in the corpus, and no intermediate pair of periods is "
                "comparable on season, provider and TOU mix at once. Any per-year number "
                "derived from these by compounding would be a transformation of two "
                "points, not an observed yearly change, so none is published"),
            "note": ("the Laspeyres and Paasche readings straddle zero because the "
                     "price structure ROTATED rather than shifted — on-peak up, "
                     "super-off-peak down — and the quantity mix rotated the same way"),
        },
        "price_effect_usd": {
            "laspeyres": _c(q0p1 - q0p0),
            "paasche": _c(q1p1 - q1p0),
        },
        "price_effect_split_usd": split,
        "price_effect_split_note": (
            "these are the same dollars as decomposition.aggregate's "
            "delivery_vintage/supply_vintage/provider terms — to within a cent of "
            "rounding, since these sum the per-cell rows — because those terms are "
            "computed over exactly these cells and nowhere else. The excluded cells' "
            "price movement is published there as netting_regime_usd"),
    }


def non_energy_bridge(base, current):
    """Everything outside the TOU energy lines, term by term, so the whole observed
    change reconciles."""
    terms = []
    for name, label in (
            ("fixed_charge", "fixed charge (Monthly Service Fee -> Base Services Charge)"),
            ("non_bypassable", "non-bypassable charges + wildfire fund charge"),
            ("unbundling_riders", "CCA unbundling riders (PCIA, ICA, EDP credit)"),
            ("applied_nem_generation_credit", "applied NEM generation credit"),
            ("taxes_and_fees", "taxes and fees"),
            ("cca_product_adders", "CEA product adders (Clean Impact Plus, surcharge)"),
            ("printed_bundled_comparison_net_of_its_credit",
             "printed bundled generation comparison, net of the credit that cancels it"),
    ):
        terms.append({
            "term": name,
            "label": label,
            "base_usd": base["ledger"][name],
            "current_usd": current["ledger"][name],
            "change_usd": _c(current["ledger"][name] - base["ledger"][name]),
        })
    return terms


def build():
    base = period_ledger(BASE)
    current = period_ledger(CURRENT)
    scan = billing_mode_scan()
    mode = billing_mode_finding(scan)
    export = export_reconciliation(scan)
    dec = decompose(base, current)
    bridge = non_energy_bridge(base, current)

    # The provider effect read whole: what the CCA arrangement charged for supply in
    # the current period, against what SDG&E's own bundled table on the SAME statement
    # would have charged for the SAME kWh. Current quantities only — the riders are
    # billed on this period's kWh, so no base-quantity counterfactual exists for them.
    if current["printed_bundled_comparison_usd"] is None:
        raise SystemExit("the current period is not a CCA period, so there is no "
                         "provider effect to separate")
    whole = provider_effect_whole_period(current["cells"],
                                         current["ledger"]["cca_product_adders"],
                                         current["ledger"]["unbundling_riders"])
    # printed with a line break inside it, so match on whitespace rather than spaces
    bundled_pcia = re.search(
        rf"\$({_NUM})\s+of\s+your\s+Electricity\s+Generation\s+Charge\s+is\s+your\s+"
        r"bundled\s+PCIA\s+charge", statement_text(BASE["statement"]))
    if bundled_pcia is None:
        raise SystemExit(
            f"{BASE['statement']}: the bundled-PCIA sentence is not on the statement, so "
            "the claim that the bundled generation rate carries PCIA is unevidenced — "
            "refusing to publish the whole-provider comparison on an assumption")
    whole["why_the_riders_belong_on_this_side"] = (
        "SDG&E's bundled generation charge carries PCIA, which the base statement "
        "says on its own face: " + re.sub(r"\s+", " ", bundled_pcia.group(0)))
    whole["per_cell_term_paasche_usd"] = \
        dec["aggregate"]["price_split_paasche_basis"]["provider_usd"]
    whole["per_cell_term_scope"] = (
        "the per-cell provider term covers only the cells billed as net imports in "
        "BOTH periods (" + ", ".join(
            dec["aggregate"]["price_split_scope"]["attributed_cells"]) + "), so it is "
        "not comparable in scope to the whole-period figure above, which is a same-date "
        "comparison on the current period's kWh over the cells billed as net imports in "
        "the CURRENT period (" + ", ".join(whole["scope"]["attributed_cells"]) + ")")
    dec["aggregate"]["provider_effect_read_whole"] = whole

    observed = _c(current["total_usd"] - base["total_usd"])
    components = _c(dec["aggregate"]["energy_change_usd"]
                    + sum(t["change_usd"] for t in bridge))
    residual = _c(observed - components)
    if abs(residual) > 1.0:
        raise SystemExit(
            f"the components sum to ${components} against an observed change of "
            f"${observed} (residual ${residual}) — issue #3 requires reconciliation "
            "within $1 per period")

    return {
        "generated_by": "analysis/bill_decomposition.py",
        "question": ("what turned a $48.25 early-summer electric bill into $398.56 two "
                     "years later"),
        "periods": {
            "base": {k: base["meta"][k] for k in
                     ("statement_date", "period", "days", "generation_provider",
                      "net_kwh", "gross_kwh", "current_charges")},
            "current": {k: current["meta"][k] for k in
                        ("statement_date", "period", "days", "generation_provider",
                         "net_kwh", "gross_kwh", "current_charges")},
            "why_these_two": ("first and last statements in the corpus; both early-summer "
                              "periods straddling the 6/1 winter->summer tariff boundary; "
                              "24 months and one provider break apart"),
            "days_differ": {"base_usd_per_day": round(base["total_usd"]
                                                      / base["meta"]["days"], 2),
                            "current_usd_per_day": round(current["total_usd"]
                                                         / current["meta"]["days"], 2)},
        },
        "billing_mode": {
            "finding": mode,
            "per_statement": scan,
            "billing_history_export_reconciliation": {
                "identity": ("export current_charges = period accrual - the part deferred "
                             "into the Net Metering Account Summary + the account-level "
                             "California Climate Credit"),
                "max_abs_residual_usd": max(abs(r["residual_usd"]) for r in export),
                "rows": export,
            },
        },
        "period_ledgers": {
            "base": {"total_usd": base["total_usd"], "terms": base["ledger"]},
            "current": {"total_usd": current["total_usd"], "terms": current["ledger"]},
        },
        "decomposition": dec,
        "non_energy_bridge": bridge,
        "like_for_like": like_for_like(
            dec["per_cell"],
            _years_apart(BASE["period"], CURRENT["period"])),
        "reconciliation": {
            "observed_change_usd": observed,
            "energy_change_usd": dec["aggregate"]["energy_change_usd"],
            "non_energy_change_usd": _c(sum(t["change_usd"] for t in bridge)),
            "components_sum_usd": components,
            "residual_usd": residual,
            "tolerance_usd": 1.0,
        },
        "confidence": {
            "label": "measured",
            "basis": ("every figure is a printed line on a committed statement or an "
                      "exact identity over those lines; no rate is modelled, scaled or "
                      "extrapolated"),
            "not_measured": ("nothing here extends beyond the two compared periods — the "
                             "corpus starts 5/25/24, so no bill-derived price change is "
                             "available for any earlier year"),
        },
    }


def write(dest_dir=None):
    dest = pathlib.Path(dest_dir or DATA)
    out = build()
    path = dest / "bill_decomposition.json"
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path, out


def main():
    path, out = write()
    r = out["reconciliation"]
    d = out["decomposition"]["aggregate"]
    print(f"{out['periods']['base']['period']} ${out['periods']['base']['current_charges']}"
          f"  ->  {out['periods']['current']['period']} "
          f"${out['periods']['current']['current_charges']}")
    print(f"observed change ${r['observed_change_usd']}  "
          f"= energy ${r['energy_change_usd']} + non-energy ${r['non_energy_change_usd']}"
          f"  (residual ${r['residual_usd']})")
    rd, ps = d["reading"], d["price_split_paasche_basis"]
    print(f"energy ${d['energy_change_usd']}: price ${rd['price_usd_low']} to "
          f"${rd['price_usd_high']}, quantity ${rd['quantity_usd_low']} to "
          f"${rd['quantity_usd_high']} (bounds, not points; the "
          f"${rd['interval_width_usd']} width is the unallocated interaction). "
          f"Laspeyres quantity splits scale ${rd['of_which_scale_usd']}, TOU mix "
          f"${rd['of_which_tou_mix_usd']}")
    print(f"price splits (Paasche): delivery vintage ${ps['delivery_vintage_usd']} + "
          f"supply vintage ${ps['supply_vintage_usd']} + provider ${ps['provider_usd']} "
          f"over {len(d['price_split_scope']['attributed_cells'])} like-for-like cells, "
          f"+ netting regime ${ps['netting_regime_usd']} over "
          f"{len(d['price_split_scope']['unattributed_cells'])}; provider read whole "
          f"${d['provider_effect_read_whole']['provider_effect_usd']} over "
          f"{len(d['provider_effect_read_whole']['scope']['attributed_cells'])} "
          f"current-import cells, plus "
          f"${d['provider_effect_read_whole']['unallocated_netting_settlement_usd']} "
          "unallocated netting/settlement")
    print(f"billing mode: {out['billing_mode']['finding']['answer']}; presentation "
          f"changed on "
          f"{out['billing_mode']['finding']['what_changed_and_when']['the_presentation_changed_on']}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
