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

A SETTLEMENT $0 IS NOT A PRICE, AND THE CODE MAKES IT UNUSABLE AS ONE.
Under NEM 2.0 a TOU bucket billed as a net export prints "Rate/kWh $.00000" on
the SDG&E side and is charged $0: the export went to the annual true-up, so no
price was charged and none can be read off the statement. That is not the number
zero, and three successive review passes found it entering a published figure by
being coerced to one — first in the per-cell price split, then in the headline
provider figure, then in the price and quantity bounds themselves. Convention did
not stop it, so the type does. A cell whose rate is not observable carries a
Settlement object, not 0.0. Every arithmetic operator on Settlement raises
SettlementNotAPrice; so do float(), round() and bool(), which closes the
"rate or 0.0" idiom. The only ways to read one are observed_rate(), which refuses
it by name; is_observed(), which tests for it; and json_price(), which renders it
as null. Nothing in this module forms a rate or a rate-weighted dollar figure
except through those three.

WHAT IS PRICED, AND WHAT IS NOT. A cell is PRICED for this comparison when it was
billed as a net import in BOTH periods, so a rate is observable at both ends —
three of the six here. The other three flip between export and import (or export
in both), and no price index term can be computed on them at all. So:

  * the price effect, the quantity effect, the interaction, the scale/TOU-mix
    split, the published bounds and the delivery-vintage/supply-vintage/provider
    split are ALL computed over the priced cells and nothing else. Every rate in
    them is a tariff at both ends;
  * every flipped cell's COMPLETE dollar change (current - base) is carried as
    its own top-level component, netting_settlement_usd, outside the price and
    quantity figures rather than inside either of them. It is the change in what
    the netting regime billed, and it is published undecomposed.

    energy change = (price + quantity + interaction, over the priced cells)
                  +  netting/settlement          (the flipped cells, whole)

THE PROVIDER COMPARISON IS PUBLISHED TWICE, BECAUSE ITS TWO HALVES COVER
DIFFERENT SCOPES. Two consequences of the provider break are charged per period
rather than per TOU cell: the riders a CCA customer pays separately and a bundled
customer pays inside the generation rate (PCIA, the incremental procurement cost
adjustment, the economic development program credit), and CEA's own product
adders (Clean Impact Plus, its state surcharge). That the bundled rate carries
PCIA is on the face of the base statement — "$1.97 of your Electricity Generation
Charge is your bundled PCIA charge" — which is quoted into the artifact. Those
lines cannot be restricted to a cell set, so adding them to a cell-restricted CEA
figure and calling the result a comparison "over five cells" would compare two
different quantity scopes. The statements support no allocation of them to a cell
set, and this script will not invent one. So provider_effect_whole_period()
publishes two figures under decomposition.aggregate.provider_comparison, each
labelled with what it covers:

  energy_only_on_the_common_cells   CEA's per-TOU charges against SDG&E's printed
                                    same-date bundled table, over exactly the
                                    cells that table prices — same cells, same
                                    kWh, energy only, no riders on either side.
  whole_period_arrangement          everything the CCA arrangement charged for
                                    supply over the whole period (CEA's per-TOU
                                    charges on all six cells + the product adders
                                    + the CCA-only riders) against the whole
                                    printed bundled table. The two sides do not
                                    price identical energy, and the artifact says
                                    by how much and where.

The printed bundled table prices a cell only where the cell was billed as a net
import; on a current net-export cell it prints $0 because the export settled at
the annual true-up, while CEA still books a credit there. That $0 is a Settlement
here too, so it cannot be netted against CEA's credit by accident.

THE DECOMPOSITION.
Per priced cell c = (season, TOU period), with q the net kWh and p the effective
billed rate (charge / net kWh — the rate that actually produced the dollars), the
priced-cell change is split by the standard exact identities:

    Laspeyres price     sum q0 (p1 - p0)      quantity  sum p0 (q1 - q0)
    Paasche   price     sum q1 (p1 - p0)      quantity  sum p1 (q1 - q0)
    interaction         sum (p1 - p0)(q1 - q0)

    price_L + quantity_L + interaction  ==  price_L + quantity_P
                                        ==  price_P + quantity_L  ==  dPriced

NEITHER EFFECT IS PUBLISHED AS A POINT. Paasche price == Laspeyres price +
interaction, so quoting the Paasche figure as "the" price effect hands the entire
interaction to price while the note beside it says the interaction is jointly
owned and not allocated. Those two statements cannot both be true. This artifact
publishes each effect as the INTERVAL between its two readings, whose width is
exactly the interaction term, together with both exact pairings. The interaction
is published and never split; nothing here, and nothing in the report, states a
figure as the amount price or quantity "accounts for".

The quantity effect is split again into scale and TOU mix, exactly, with
Q = sum q over the priced cells and w = q / Q:

    scale  (Q1 - Q0) sum p0 w0        mix  Q1 sum p0 (w1 - w0)

Every p0 in that split is an observed tariff, because the split runs over the
priced cells only; there is no kWh in it valued at a settlement zero.

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


# ---------------------------------------------------------------------------
# A rate is either observed, or explicitly not a price. Never 0.0 by default.
# ---------------------------------------------------------------------------
class SettlementNotAPrice(SystemExit):
    """A settlement non-price was used where a price was required."""


class Settlement:
    """The value a cell carries INSTEAD of a rate when no rate is observable.

    Under NEM 2.0 a TOU bucket billed as a net export prints "Rate/kWh $.00000"
    and is charged $0: the in-period export settled at the annual true-up, so
    nothing was priced. The absence of a charged rate is not the number zero, and
    every published defect this module has had came from one being written down
    as one — q*(p1 - p0) with p0 coerced to 0.0 reconciles perfectly while
    measuring the export-to-import settlement change and printing it as a price.

    So the coercion is made impossible rather than discouraged. Arithmetic,
    float(), round(), bool() and comparison all raise SettlementNotAPrice, which
    kills the `rate or 0.0` idiom as well as q*(p1 - p0). The three deliberate
    readings are observed_rate() (refuse, naming the cell), is_observed() (test),
    and json_price() (render as null).
    """
    __slots__ = ("cell", "what")

    def __init__(self, cell, what):
        self.cell = cell
        self.what = what

    def __repr__(self):
        return f"Settlement({self.cell!r}, {self.what!r})"

    def _refuse(self, op):
        raise SettlementNotAPrice(
            f"({self.cell}) has no observable {self.what}: the cell was billed as a net "
            f"export, so the energy settled at the annual true-up and no rate was "
            f"charged — '{op}' on it would put a settlement outcome inside a price "
            "figure. Compute over the priced cells and carry this cell's whole dollar "
            "change as netting/settlement instead")


def _refusing(op):
    def _op(self, *_args):
        return self._refuse(op)
    return _op


for _name, _op in (("add", "+"), ("radd", "+"), ("sub", "-"), ("rsub", "-"),
                   ("mul", "*"), ("rmul", "*"), ("truediv", "/"), ("rtruediv", "/"),
                   ("pow", "**"), ("rpow", "**"), ("neg", "-x"), ("pos", "+x"),
                   ("abs", "abs()"), ("round", "round()"), ("float", "float()"),
                   ("int", "int()"), ("bool", "truth-testing"), ("lt", "<"),
                   ("le", "<="), ("gt", ">"), ("ge", ">="), ("eq", "=="), ("ne", "!=")):
    setattr(Settlement, f"__{_name}__", _refusing(_op))
del _name, _op


def is_observed(value):
    """True when value is a rate that was actually charged and can be read."""
    return not isinstance(value, Settlement) and value is not None


def observed_rate(value, cell, what):
    """The float behind an observed effective rate — the ONLY way a rate becomes an
    ordinary number in this module.

    Refuses three things by name rather than returning a usable number: a
    Settlement (the cell was billed as an export, so no rate exists), a missing
    rate (the statement did not print the comparison at all), and a $0 on a cell
    that WAS billed as an import (on an import cell a $0 is a settlement leak or a
    parsing artefact, never a tariff)."""
    if isinstance(value, Settlement):
        raise SettlementNotAPrice(
            f"({cell}) is billed as a net import in both periods but its {what} is a "
            "settlement non-price — that combination means the parse is wrong, and "
            "using it would publish a settlement outcome as a tariff; refusing")
    if value is None:
        raise SystemExit(
            f"({cell}): the current period carries no {what}, so the provider effect "
            "cannot be separated from the vintage effect for this cell — refusing to "
            "estimate one")
    if value == 0:
        raise SystemExit(
            f"({cell}) is billed as a net import in both periods but its {what} is $0 — "
            "on an import cell a $0 is not a tariff, and attributing this cell to a "
            "vintage or to the provider would publish that $0 as a price; refusing to "
            "estimate one")
    return float(value)


def json_price(value, digits=5):
    """A rate as the artifact publishes it: the rounded number, or null when the cell
    was billed as an export and no rate was charged."""
    return _r(value, digits) if is_observed(value) else None


def _effective_rate(usd, kwh, cell, what):
    """charge / net kWh on a cell billed as a net import; an explicit Settlement on a
    cell billed as a net export, where SDG&E printed $.00000 and charged nothing
    because the energy settled at the annual true-up."""
    return usd / kwh if kwh > 0 else Settlement(cell, what)


def _counterfactual_usd(usd, kwh, cell):
    """The same-date bundled comparison in DOLLARS. On a cell billed as a net export
    the printed $0 is deferred settlement, not a bundled tariff of zero, so it is a
    Settlement and cannot be netted against what CEA booked there. A net-export cell
    carrying a NON-zero printed comparison would be a genuine observed bundled export
    counterfactual, so it stays an ordinary number and the caller decides."""
    if kwh > 0 or usd != 0.0:
        return usd
    return Settlement(cell, "same-date bundled comparison in dollars")


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
# What a true-up settlement statement says about itself, and the true-up date it
# prints in its own Net Energy Metering Summary header.
_TRUE_UP_BILL = re.compile(r"Net Energy Metering Annual True-Up Bill")
_SETTLED = re.compile(
    r"Your account has been settled and all applicable generation credits have been "
    r"applied\.")
_TRUE_UP_FIELD = re.compile(r"True-Up Date:\s*(\d{1,2}/\d{1,2}/\d{4})")


def _true_up_date(text):
    """'Dec 26, 2024' -> date. The deferral sentences print this form."""
    return dt.datetime.strptime(text, "%b %d, %Y").date()


def _period_end(period):
    return _period_dates(period)[1]


def classify_statement(stmt, txt, periods_on_statement, expected_true_up):
    """One statement's billing mode, established from its own text or refused.

    Two rules, both fail-closed, because "Payment Required This Month" alone is a
    yes/no flag that a wording change or a genuine billing-mode change could flip
    without this script noticing:

      "No"  must carry one of the two recognised deferral sentences, which name the
            true-up date the charge is accruing towards. A statement that says
            payment is not required but never says why is not evidence of accrual,
            so it raises rather than being counted as an accruing month.
      "Yes" must prove it is the annual settlement rather than an ordinary payable
            bill: it has to say so ("Net Energy Metering Annual True-Up Bill", "Your
            account has been settled ..."), and its period end has to be the true-up
            date the PREVIOUS statements were accruing towards. A payable statement
            that is not a true-up settlement means the account stopped accruing, and
            that must stop the run instead of being relabelled a settlement.

    expected_true_up is the date the last accruing statement named, or None before
    any has been seen. Returns the scan row."""
    payreq = _PAY_REQUIRED.search(txt)
    if payreq is None:
        raise SystemExit(f"{stmt}: no 'Payment Required This Month' line — the "
                         "Net Energy Metering Summary page is what settles the "
                         "billing mode and it is not on this statement")
    defer = _NEM_DEFER.search(txt)
    accrue = _ACCRUES.search(txt)
    row = {
        "statement_date": stmt,
        "nem_ledger_block_printed": _NEM_LEDGER in txt,
        "payment_required_this_month": payreq.group(1),
    }
    if payreq.group(1) == "No":
        if defer:
            quote = ("*Payment not required for NEM charges. Your account will true "
                     f"up on {defer.group(1)}")
            true_up = defer.group(1)
        elif accrue:
            quote = ("Payment is not required at this time. Your account will "
                     f"true-up on {accrue.group(1)}.")
            true_up = accrue.group(1)
        else:
            raise SystemExit(
                f"{stmt}: prints 'Payment Required This Month: No' but carries neither "
                "recognised true-up deferral sentence ('*Payment not required for NEM "
                "charges. Your account will true up on ...' / 'Payment is not required "
                "at this time. Your account will true-up on ...'). A payment flag on its "
                "own does not establish that the energy charge accrued to the annual "
                "true-up — the wording may have changed, or the account may have stopped "
                "accruing. Read the statement and extend the patterns; this script will "
                "not assume accrual")
        row.update({"true_up_date": true_up, "establishing_quote": quote,
                    "billing_mode": "accrues to the annual true-up",
                    "annual_settlement": False, "settlement_evidence": None})
    else:
        settlement = [m.group(0) for m in (_TRUE_UP_BILL.search(txt), _SETTLED.search(txt))
                      if m]
        if len(settlement) != 2:
            raise SystemExit(
                f"{stmt}: prints 'Payment Required This Month: Yes' but does not say it "
                "is the annual settlement — 'Net Energy Metering Annual True-Up Bill' "
                "and/or 'Your account has been settled and all applicable generation "
                "credits have been applied.' is missing. A payable statement that is not "
                "a true-up settlement means the energy charge stopped accruing, which "
                "this analysis's cost series depends on; refusing to label it a "
                "settlement on the payment flag alone")
        if expected_true_up is None:
            raise SystemExit(
                f"{stmt}: claims to be an annual settlement, but no earlier statement "
                "named a true-up date for it to settle — there is nothing to match its "
                "period end against, so the claim is unverified")
        ends = sorted(_period_end(p) for p in periods_on_statement)
        if expected_true_up not in ends:
            raise SystemExit(
                f"{stmt}: claims to be the annual settlement, but none of its billing "
                f"periods ends on {expected_true_up.strftime('%m/%d/%Y')}, the true-up "
                "date the preceding statements printed. Its period(s) end "
                + ", ".join(e.strftime("%m/%d/%Y") for e in ends)
                + " — the settlement does not line up with the accrual it is supposed to "
                "close, and this script will not assert that it does")
        printed = _TRUE_UP_FIELD.search(txt)
        if printed is None or dt.datetime.strptime(printed.group(1),
                                                   "%m/%d/%Y").date() != expected_true_up:
            raise SystemExit(
                f"{stmt}: claims to be the annual settlement for "
                f"{expected_true_up.strftime('%m/%d/%Y')}, but its own 'True-Up Date:' "
                "field reads "
                f"{printed.group(1) if printed else 'nothing'}")
        row.update({
            "true_up_date": None,
            "establishing_quote": settlement[0],
            "billing_mode": "annual true-up settlement",
            "annual_settlement": True,
            "settlement_evidence": {
                "quotes": settlement,
                "printed_true_up_date": printed.group(1),
                "settles_the_true_up_date_previously_printed":
                    expected_true_up.strftime("%b ") + str(expected_true_up.day) + ", "
                    + str(expected_true_up.year),
                "matching_period_end": next(
                    p for p in periods_on_statement
                    if _period_end(p) == expected_true_up),
            },
        })
    month = _TOTAL_MONTH.findall(txt)
    credit = _CLIMATE_CREDIT.findall(txt)
    row.update({
        "total_charges_this_month_usd": _f(month[0]) if month else None,
        "california_climate_credit_usd":
            _c(sum(_f(c) for c in dict.fromkeys(credit))) if credit else 0.0,
        "periods_on_statement": sorted(periods_on_statement),
    })
    return row


def billing_mode_scan():
    """Read every statement and record, per statement, whether the energy charge was
    payable that month or accrued to the annual true-up — and which printed sentence
    says so. This runs before any arithmetic, over the whole corpus, because the
    answer decides which column is the cost series.

    Statements are read in date order so each claimed annual settlement can be
    matched against the true-up date the statements before it were accruing towards;
    classify_statement() refuses anything it cannot establish that way."""
    rows = []
    per = periods()
    by_stmt = {}
    for (stmt, _), p in per.items():
        by_stmt.setdefault(stmt, []).append(p)
    expected_true_up = None
    for stmt in statement_dates():
        on_stmt = [p["period"] for p in by_stmt.get(stmt, [])]
        row = classify_statement(stmt, statement_text(stmt), on_stmt, expected_true_up)
        row["period_accrual_usd"] = _c(sum(p["current_charges"]
                                           for p in by_stmt.get(stmt, [])))
        if row["true_up_date"]:
            expected_true_up = _true_up_date(row["true_up_date"])
        elif row["annual_settlement"]:
            # that true-up is now closed; the next settlement must be justified by a
            # true-up date printed AFTER it, not by this one again
            expected_true_up = None
        rows.append(row)
    return rows


def billing_mode_finding(scan):
    """Turn the scan into the answer, naming the statements that establish it.

    Every count and every sentence below is derived from the scan rows, which
    classify_statement() has already refused to produce for any statement whose
    billing mode is not established by its own text. Nothing here is a constant: if
    the corpus gains a third settlement, or an accruing statement stops carrying its
    deferral sentence, the scan raises or these figures move — the prose cannot go on
    asserting uninterrupted accrual on its own."""
    accruing = [r for r in scan if r["billing_mode"] == "accrues to the annual true-up"]
    payable = [r for r in scan if r["annual_settlement"]]
    if len(accruing) + len(payable) != len(scan):
        raise SystemExit("a statement was neither classified as accruing nor as an "
                         "annual settlement — the scan let something through")
    if not accruing:
        raise SystemExit("no statement in the corpus accrues to the annual true-up, so "
                         "the per-period accrual is not the cost series here")
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
             "quote": next(r["establishing_quote"] for r in scan
                           if r["statement_date"] == BASE["statement"]),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": CURRENT["statement"],
             "role": "the current period of this comparison",
             "quote": next(r["establishing_quote"] for r in scan
                           if r["statement_date"] == CURRENT["statement"]),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": last_ledger,
             "role": "last statement printing a separate Net Metering Account Summary",
             "quote": next(r["establishing_quote"] for r in scan
                           if r["statement_date"] == last_ledger),
             "net_energy_metering_summary": "Payment Required This Month: No"},
            {"statement_date": first_without,
             "role": "first statement without it — the presentation change",
             "quote": next(r["establishing_quote"] for r in scan
                           if r["statement_date"] == first_without),
             "net_energy_metering_summary": "Payment Required This Month: No"},
        ],
        "annual_settlement_statements": [
            {"statement_date": r["statement_date"],
             "true_up_period_ends": r["periods_on_statement"],
             "net_energy_metering_summary": "Payment Required This Month: Yes",
             "proved_by": r["settlement_evidence"]}
            for r in payable],
        "how_each_statement_was_established": (
            "an accruing statement has to carry one of the two recognised true-up "
            "deferral sentences; the payment flag alone is not accepted. A settlement "
            "statement has to say it is one AND have a billing period ending on the "
            "true-up date the preceding statements printed, matching its own "
            "'True-Up Date:' field. Anything else stops the run"),
        "statements_scanned": len(scan),
        "statements_accruing": len(accruing),
        "statements_payable": len(payable),
        "what_changed_and_when": {
            "the_billing_mode_did_not_change": (
                f"{len(accruing)} of the {len(scan)} statements in the corpus print "
                "Payment Required This Month: No and carry a true-up deferral sentence "
                "naming the date they accrue towards; the other "
                + (f"{len(payable)} is an annual settlement closing a true-up"
                   if len(payable) == 1
                   else f"{len(payable)} are annual settlements, each closing a true-up")
                + " the earlier statements named. Both compared "
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
            comparison_rate = _effective_rate(comparison["usd"], d["kwh"],
                                              _key(season, tou),
                                              "same-date bundled comparison rate")
            comparison_usd = _counterfactual_usd(comparison["usd"], d["kwh"],
                                                 _key(season, tou))
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
            # Effective billed rates: charge / net kWh on a cell billed as a net
            # import. On a net-export cell SDG&E prints $.00000 and charges nothing
            # because the export settles at true-up, so there is no rate to read and
            # the cell carries a Settlement — arithmetic on which raises rather than
            # quietly yielding 0.0.
            "delivery_rate_effective": _effective_rate(d["usd"], q, _key(season, tou),
                                                       "delivery rate"),
            "supply_rate_effective": _effective_rate(supply["usd"], q,
                                                     _key(season, tou), "supply rate"),
            "rate_effective": _effective_rate(usd, q, _key(season, tou),
                                              "effective billed rate"),
            "sdge_bundled_comparison_rate": comparison_rate,
            # the same-date bundled counterfactual in DOLLARS, a Settlement wherever
            # the printed $0 is deferred export settlement rather than a tariff.
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
    """Split the TOU energy change into a priced-cell index decomposition and a
    netting/settlement component, with nothing crossing between them.

    A cell is PRICED when it was billed as a net import in BOTH periods, which is
    the only condition under which a rate is observable at both ends. Laspeyres,
    Paasche, the interaction, the scale/TOU-mix split, the published bounds and the
    delivery-vintage/supply-vintage/provider split are computed over those cells and
    nothing else — every rate in them is a tariff.

    Every other cell flips between export and import (or exports in both), so at
    least one end of its price is a Settlement: SDG&E printed $.00000 and charged
    nothing because the energy went to the annual true-up. No index term exists for
    such a cell, and the type refuses to fabricate one. Its COMPLETE dollar change
    is carried as netting_settlement_usd, a top-level component beside the price and
    quantity figures rather than a sub-key inside either:

        energy change = (price + quantity + interaction over the priced cells)
                      +  netting/settlement

    Price and quantity are published as intervals, not points, because the
    interaction term is reported and never allocated."""
    per_cell = []
    agg = {k: 0.0 for k in ("price_l", "price_p", "quantity_l", "quantity_p",
                            "interaction", "delivery_vintage_l", "supply_vintage_l",
                            "provider_l", "delivery_vintage_p", "supply_vintage_p",
                            "provider_p", "priced_base_usd", "priced_current_usd",
                            "base_usd", "current_usd", "settlement_change")}
    priced_keys = [(s, t) for s, t in CELLS
                   if base["cells"][(s, t)]["kwh"] > 0
                   and current["cells"][(s, t)]["kwh"] > 0]
    q0_total = sum(base["cells"][k]["kwh"] for k in priced_keys)
    q1_total = sum(current["cells"][k]["kwh"] for k in priced_keys)
    scale = mix = 0.0
    for season, tou in CELLS:
        cell = _key(season, tou)
        b, c = base["cells"][(season, tou)], current["cells"][(season, tou)]
        q0, q1 = b["kwh"], c["kwh"]
        agg["base_usd"] += b["usd"]
        agg["current_usd"] += c["usd"]
        row = {
            "cell": cell,
            "season": season,
            "tou_period": tou,
            "base_kwh": q0,
            "current_kwh": q1,
            "base_usd": b["usd"],
            "current_usd": c["usd"],
            "change_usd": _c(c["usd"] - b["usd"]),
            "base_net_import": q0 > 0,
            "current_net_import": q1 > 0,
            "priced": (season, tou) in priced_keys,
            # nulls here are the statements' own answer, not a missing value: on a
            # cell billed as a net export no rate was charged.
            "base_rate_effective": json_price(b["rate_effective"]),
            "current_rate_effective": json_price(c["rate_effective"]),
            "base_delivery_rate": json_price(b["delivery_rate_effective"]),
            "current_delivery_rate": json_price(c["delivery_rate_effective"]),
            "base_supply_rate": json_price(b["supply_rate_effective"]),
            "current_supply_rate": json_price(c["supply_rate_effective"]),
            "sdge_bundled_comparison_rate_current_date":
                json_price(c["sdge_bundled_comparison_rate"]),
        }
        if not row["priced"]:
            # No index term exists for this cell. Reading a rate off it would raise;
            # nothing here tries. Its whole dollar change is the settlement component.
            row["netting_settlement_usd"] = row["change_usd"]
            row["why_not_priced"] = (
                "billed as a net export in "
                + ("both periods" if q0 <= 0 and q1 <= 0
                   else "the base period" if q0 <= 0 else "the current period")
                + ", so SDG&E printed $.00000 and charged nothing there — the energy "
                "settled at the annual true-up and no rate is observable to compare")
            agg["settlement_change"] += c["usd"] - b["usd"]
            per_cell.append(row)
            continue

        # Every rate below is read through observed_rate(), which refuses a
        # Settlement, a missing comparison and a $0-on-an-import-cell by name.
        p0 = observed_rate(b["rate_effective"], cell, "base effective billed rate")
        p1 = observed_rate(c["rate_effective"], cell, "current effective billed rate")
        d0 = observed_rate(b["delivery_rate_effective"], cell, "base delivery rate")
        d1 = observed_rate(c["delivery_rate_effective"], cell, "current delivery rate")
        g0 = observed_rate(b["supply_rate_effective"], cell, "base supply rate")
        g1 = observed_rate(c["supply_rate_effective"], cell, "current supply rate")
        s1 = observed_rate(c["sdge_bundled_comparison_rate"], cell,
                           "same-date bundled comparison rate")
        # Delivery is SDG&E in both periods, so all of its movement is vintage; the
        # supply movement splits at the same-date bundled comparison s1.
        terms = {
            "price_l": q0 * (p1 - p0),
            "price_p": q1 * (p1 - p0),
            "quantity_l": p0 * (q1 - q0),
            "quantity_p": p1 * (q1 - q0),
            "interaction": (p1 - p0) * (q1 - q0),
            "delivery_vintage_l": q0 * (d1 - d0),
            "supply_vintage_l": q0 * (s1 - g0),
            "provider_l": q0 * (g1 - s1),
            "delivery_vintage_p": q1 * (d1 - d0),
            "supply_vintage_p": q1 * (s1 - g0),
            "provider_p": q1 * (g1 - s1),
            "priced_base_usd": b["usd"],
            "priced_current_usd": c["usd"],
        }
        for k, v in terms.items():
            agg[k] += v
        w0 = q0 / q0_total if q0_total else 0.0
        w1 = q1 / q1_total if q1_total else 0.0
        mix += q1_total * p0 * (w1 - w0)
        scale += (q1_total - q0_total) * p0 * w0
        row.update({
            "laspeyres_price_usd": _c(terms["price_l"]),
            "laspeyres_quantity_usd": _c(terms["quantity_l"]),
            "paasche_price_usd": _c(terms["price_p"]),
            "paasche_quantity_usd": _c(terms["quantity_p"]),
            "interaction_usd": _c(terms["interaction"]),
            "delivery_vintage_usd_laspeyres": _c(terms["delivery_vintage_l"]),
            "supply_vintage_usd_laspeyres": _c(terms["supply_vintage_l"]),
            "provider_usd_laspeyres": _c(terms["provider_l"]),
            "delivery_vintage_usd_paasche": _c(terms["delivery_vintage_p"]),
            "supply_vintage_usd_paasche": _c(terms["supply_vintage_p"]),
            "provider_usd_paasche": _c(terms["provider_p"]),
        })
        per_cell.append(row)

    priced_cells = [r["cell"] for r in per_cell if r["priced"]]
    settlement_cells = [r["cell"] for r in per_cell if not r["priced"]]
    if not priced_cells:
        raise SystemExit(
            "no cell is billed as a net import in both periods, so no price is "
            "observable at both ends anywhere and there is no price or quantity "
            "effect to compute at all — refusing to publish an index whose every "
            "term would be the netting regime under another name")
    energy_change = _c(agg["current_usd"] - agg["base_usd"])
    priced_change = _c(agg["priced_current_usd"] - agg["priced_base_usd"])
    settlement_change = _c(agg["settlement_change"])
    scope_note = (
        "PRICED CELLS are the ones billed as net imports in BOTH periods, the only "
        "cells where a rate is observable at both ends. Every figure under "
        "priced_cells — the price effect, the quantity effect, the interaction, the "
        "published bounds, the scale/TOU-mix split and the vintage/provider split — "
        "covers those cells and nothing else. SETTLEMENT CELLS were billed as net "
        "exports in at least one period: SDG&E printed $.00000 and charged nothing "
        "because the energy settled at the annual true-up, so no rate exists to "
        "compare and no index term is defined. Their whole dollar change is carried "
        "under netting_settlement, outside both the price and the quantity figures "
        "rather than inside either of them.")
    out = {
        "per_cell": per_cell,
        "aggregate": {
            "base_energy_usd": _c(agg["base_usd"]),
            "current_energy_usd": _c(agg["current_usd"]),
            "energy_change_usd": energy_change,
            "scope": {
                "priced_cells": priced_cells,
                "settlement_cells": settlement_cells,
                "what_each_figure_covers": scope_note,
            },
            "priced_cells": {
                "cells": priced_cells,
                "base_usd": _c(agg["priced_base_usd"]),
                "current_usd": _c(agg["priced_current_usd"]),
                "change_usd": priced_change,
                "laspeyres": {"price_usd": _c(agg["price_l"]),
                              "quantity_usd": _c(agg["quantity_l"])},
                "paasche": {"price_usd": _c(agg["price_p"]),
                            "quantity_usd": _c(agg["quantity_p"])},
                "interaction_usd": _c(agg["interaction"]),
                # Price and quantity are published as INTERVALS, not as points.
                # Paasche price == Laspeyres price + interaction, so naming either
                # endpoint as "the" price effect silently allocates the whole
                # interaction to it while claiming not to. The width IS the
                # interaction term.
                "reading": {
                    "convention": "bounds — neither effect is published as a point",
                    "covers": ("the " + str(len(priced_cells)) + " priced cells ("
                               + ", ".join(priced_cells) + ") and nothing else; the "
                               "settlement cells are outside these figures entirely"),
                    "basis": (
                        "the price effect and the quantity effect are each reported as "
                        "the interval between their Laspeyres (base-weight) and Paasche "
                        "(current-weight) readings. Both intervals are exactly as wide "
                        "as the interaction term, which is published and allocated to "
                        "neither side; no figure here is the amount price or quantity "
                        "'accounts for'. The two endpoints pair exactly, one from each "
                        "side, and both pairings are stated below"),
                    "price_usd_low": _c(min(agg["price_l"], agg["price_p"])),
                    "price_usd_high": _c(max(agg["price_l"], agg["price_p"])),
                    "quantity_usd_low": _c(min(agg["quantity_l"], agg["quantity_p"])),
                    "quantity_usd_high": _c(max(agg["quantity_l"], agg["quantity_p"])),
                    "interval_width_usd": _c(agg["interaction"]),
                    "exact_pairings": [
                        {"price_basis": "laspeyres", "price_usd": _c(agg["price_l"]),
                         "quantity_basis": "paasche",
                         "quantity_usd": _c(agg["quantity_p"]),
                         "sum_usd": _c(agg["price_l"] + agg["quantity_p"])},
                        {"price_basis": "paasche", "price_usd": _c(agg["price_p"]),
                         "quantity_basis": "laspeyres",
                         "quantity_usd": _c(agg["quantity_l"]),
                         "sum_usd": _c(agg["price_p"] + agg["quantity_l"])},
                    ],
                    "sums_to": "priced_cells.change_usd",
                    "quantity_split_basis": (
                        "the scale and TOU-mix figures below split the Laspeyres end of "
                        "the quantity interval; they do not apply to the Paasche end"),
                    "of_which_scale_usd": _c(scale),
                    "of_which_tou_mix_usd": _c(mix),
                    "interaction_usd": _c(agg["interaction"]),
                    "interaction_note": (
                        "the spread between the two readings, published and never "
                        "split. It is the part of the priced cells' change that moved "
                        "because price and quantity moved together, owned jointly by "
                        "both terms"),
                },
                "quantity_split_laspeyres_basis": {
                    "scale_usd": _c(scale),
                    "tou_mix_usd": _c(mix),
                    "base_net_kwh": _r(q0_total, 1),
                    "current_net_kwh": _r(q1_total, 1),
                    "every_base_price_here_is_an_observed_tariff": (
                        "scale and mix value every kWh at the base period's effective "
                        "price, so they are computed over the priced cells only. Both "
                        "ends of every rate in this split were charged on an import "
                        "cell; no kWh here is valued at a settlement $0, and none can "
                        "be — a settlement non-price raises rather than multiplying"),
                    "kwh_note": (
                        "these totals are the priced cells' net kWh, not the periods' "
                        "346 and 987: the settlement cells are outside this split"),
                },
                # The causal split of the priced cells' price effect. Delivery is
                # SDG&E in both periods, so all of its movement is vintage; supply
                # splits at the same-date bundled comparison.
                "price_split_laspeyres_basis": {
                    "delivery_vintage_usd": _c(agg["delivery_vintage_l"]),
                    "supply_vintage_usd": _c(agg["supply_vintage_l"]),
                    "provider_usd": _c(agg["provider_l"]),
                },
                "price_split_paasche_basis": {
                    "delivery_vintage_usd": _c(agg["delivery_vintage_p"]),
                    "supply_vintage_usd": _c(agg["supply_vintage_p"]),
                    "provider_usd": _c(agg["provider_p"]),
                },
                "price_split_identity": (
                    "delivery_vintage + supply_vintage + provider == the price effect "
                    "over the priced cells, on each weight basis. There is no fourth "
                    "term: the cells that would have needed one are not in this figure"),
            },
            "netting_settlement": {
                "cells": settlement_cells,
                "change_usd": settlement_change,
                "per_cell": [{"cell": r["cell"], "base_kwh": r["base_kwh"],
                              "current_kwh": r["current_kwh"], "base_usd": r["base_usd"],
                              "current_usd": r["current_usd"],
                              "change_usd": r["change_usd"],
                              "why_not_priced": r["why_not_priced"]}
                             for r in per_cell if not r["priced"]],
                "what_this_is": (
                    "the complete dollar change of every cell billed as a net export in "
                    "at least one period — what the netting regime billed, published "
                    "whole and undecomposed. It is neither a price effect nor a "
                    "quantity effect and is not inside either of them: on these cells "
                    "SDG&E charged $0 because the energy settled at the annual true-up, "
                    "so there is no rate at one or both ends to build an index term "
                    "from. Splitting it would require a base tariff these statements do "
                    "not observe"),
            },
            "energy_identity": {
                "identity": ("energy change = priced cells (price + quantity + "
                             "interaction) + netting/settlement"),
                "priced_cells_change_usd": priced_change,
                "netting_settlement_change_usd": settlement_change,
                "energy_change_usd": energy_change,
            },
        },
        "identities": {
            "priced_laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd":
                _c(agg["price_l"] + agg["quantity_l"] + agg["interaction"]),
            "priced_laspeyres_price_plus_paasche_quantity_usd":
                _c(agg["price_l"] + agg["quantity_p"]),
            "priced_paasche_price_plus_laspeyres_quantity_usd":
                _c(agg["price_p"] + agg["quantity_l"]),
            "priced_cells_change_usd": priced_change,
            "scale_plus_mix_usd": _c(scale + mix),
            "priced_laspeyres_quantity_usd": _c(agg["quantity_l"]),
            "price_split_sum_laspeyres_usd": _c(agg["delivery_vintage_l"]
                                                + agg["supply_vintage_l"]
                                                + agg["provider_l"]),
            "priced_laspeyres_price_usd": _c(agg["price_l"]),
            "price_split_sum_paasche_usd": _c(agg["delivery_vintage_p"]
                                              + agg["supply_vintage_p"]
                                              + agg["provider_p"]),
            "priced_paasche_price_usd": _c(agg["price_p"]),
            "priced_change_plus_netting_settlement_usd":
                _c(priced_change + settlement_change),
            "energy_change_usd": energy_change,
        },
    }
    ids = out["identities"]
    checks = [
        ("Laspeyres price + Laspeyres quantity + interaction, over the priced cells",
         ids["priced_laspeyres_price_plus_laspeyres_quantity_plus_interaction_usd"],
         priced_change),
        ("Laspeyres price + Paasche quantity, over the priced cells",
         ids["priced_laspeyres_price_plus_paasche_quantity_usd"], priced_change),
        ("Paasche price + Laspeyres quantity, over the priced cells",
         ids["priced_paasche_price_plus_laspeyres_quantity_usd"], priced_change),
        ("scale + TOU mix", ids["scale_plus_mix_usd"],
         ids["priced_laspeyres_quantity_usd"]),
        ("delivery vintage + supply vintage + provider (Laspeyres)",
         ids["price_split_sum_laspeyres_usd"], ids["priced_laspeyres_price_usd"]),
        ("delivery vintage + supply vintage + provider (Paasche)",
         ids["price_split_sum_paasche_usd"], ids["priced_paasche_price_usd"]),
        ("priced-cell change + netting/settlement",
         ids["priced_change_plus_netting_settlement_usd"], energy_change),
    ]
    for name, got, want in checks:
        if abs(got - want) > 0.01:
            raise SystemExit(f"decomposition identity broken: {name} = {got} != {want}")
    return out


def provider_effect_whole_period(cells, cca_product_adders_usd, unbundling_riders_usd):
    """What the CCA arrangement charged for supply, against SDG&E's own bundled table
    on the SAME statement — published as TWO comparisons, because the two things a
    reader wants to know cover different scopes and one figure cannot carry both.

    THE SCOPE PROBLEM, stated plainly. SDG&E's printed bundled table prices a cell
    only where that cell was billed as a net import; on a current net-export cell it
    prints $0 because the export settled at the annual true-up, while CEA still books
    a credit there. That $0 is a Settlement, so it cannot be netted against CEA's
    credit — the arithmetic raises rather than balancing. Meanwhile the CCA-only
    riders and CEA's product adders are charged ONCE PER PERIOD on the period's own
    kWh, not per TOU cell. The statements print no allocation of them to a cell set
    and this script will not invent one. So a single figure would either compare
    cell-restricted CEA supply against cell-restricted bundled supply while carrying
    whole-period riders on one side, or compare whole-period against cell-restricted.
    Both are mixed-scope. Two figures are published instead:

      energy_only_on_the_common_cells   CEA's per-TOU charges against the printed
                                        bundled table, over exactly the cells that
                                        table prices. Same cells, same kWh, energy on
                                        both sides, no riders on either. This is the
                                        one that is a like-for-like supply-price
                                        comparison.
      whole_period_arrangement          everything the CCA arrangement charged for
                                        supply over the period (CEA on all cells +
                                        product adders + CCA-only riders) against the
                                        whole printed bundled table, whose riders sit
                                        inside its generation rate. The two sides do
                                        not price identical energy; the dict names the
                                        gap and its dollar size rather than hiding it.

    A net-export cell whose bundled comparison is NOT a Settlement would be an
    observed bundled export counterfactual — a real comparison this restriction must
    not throw away silently — so it raises instead."""
    common, unpriced = [], []
    cea_all_cells = 0.0
    for season, tou in CELLS:
        c = cells[(season, tou)]
        cf = c.get("sdge_bundled_comparison_usd")
        cea_all_cells += c["supply_usd"]
        row = {
            "cell": _key(season, tou),
            "current_kwh": c["kwh"],
            "current_net_import": c["kwh"] > 0,
            "cea_charged_usd": _c(c["supply_usd"]),
        }
        if c["kwh"] > 0:
            if cf is None:
                raise SystemExit(
                    f"({season}, {tou}): no same-date SDG&E bundled comparison in "
                    "dollars, so the whole-period provider effect has no counterfactual "
                    "for this cell — refusing to estimate one")
            if isinstance(cf, Settlement):
                raise SettlementNotAPrice(
                    f"({season}, {tou}) is billed as a net import yet its same-date "
                    "bundled comparison is a settlement non-price — that combination "
                    "means the parse is wrong; refusing")
            row["sdge_bundled_same_date_usd"] = _c(cf)
            row["difference_usd"] = _c(c["supply_usd"] - cf)
            common.append(row)
        else:
            if not isinstance(cf, Settlement):
                raise SystemExit(
                    f"({season}, {tou}) is billed as a net export yet carries a printed "
                    f"bundled comparison of ${_c(cf) if cf is not None else None} — that "
                    "is an OBSERVED bundled export counterfactual, which this "
                    "restriction was written to exclude only because it does not exist; "
                    "it must not be discarded silently, so the exclusion rule has to be "
                    "revisited")
            row["sdge_bundled_same_date_usd"] = None
            row["difference_usd"] = None
            row["why_the_bundled_side_is_absent"] = (
                "SDG&E printed $0 here because the export settled at the annual "
                "true-up, not because its bundled tariff for this cell is zero, so "
                "there is no counterfactual dollar figure to subtract")
            unpriced.append(row)
    if not common:
        raise SystemExit(
            "no cell is billed as a net import in the current period, so SDG&E's printed "
            "bundled table prices nothing and there is no provider counterfactual at all "
            "— refusing to publish a provider effect whose whole base is a settlement $0")
    cea_common = _c(sum(r["cea_charged_usd"] for r in common))
    counterfactual = _c(sum(r["sdge_bundled_same_date_usd"] for r in common))
    if counterfactual <= 0:
        raise SystemExit(
            f"the same-date bundled counterfactual over the net-import cells is "
            f"${counterfactual} — a provider effect cannot be expressed against it")
    cea_all_cells = _c(cea_all_cells)
    cea_outside_common = _c(cea_all_cells - cea_common)
    arrangement = _c(cea_all_cells + cca_product_adders_usd + unbundling_riders_usd)
    common_names = [r["cell"] for r in common]
    unpriced_names = [r["cell"] for r in unpriced]
    return {
        "why_two_figures": (
            "the two sides of this comparison are not charged on the same footing. "
            "SDG&E's printed bundled table prices only the cells billed as net imports; "
            "the CCA-only riders and CEA's product adders are charged once per period on "
            "the period's own kWh and the statements support no allocation of them to a "
            "cell set. A single number would mix a cell-restricted quantity scope with a "
            "whole-period one, so both readings are published and each says what it "
            "covers"),
        "energy_only_on_the_common_cells": {
            "covers": (
                "energy only, both sides, over the " + str(len(common_names))
                + " cells the printed bundled table prices (" + ", ".join(common_names)
                + "): CEA's per-TOU generation charges against SDG&E's same-date bundled "
                "table on the same kWh. No riders and no product adders on either side"),
            "excluded_cells": unpriced_names,
            "why_excluded": (
                "billed as a net export in the current period, so the printed bundled "
                "table shows $0 by deferred settlement rather than a tariff. Subtracting "
                "that $0 from what CEA booked there would measure the netting regime and "
                "publish it as a provider price effect"),
            "cea_charged_supply_usd": cea_common,
            "sdge_bundled_same_date_usd": counterfactual,
            "difference_usd": _c(cea_common - counterfactual),
            "difference_pct": _r(100.0 * (cea_common / counterfactual - 1.0), 1),
        },
        "whole_period_arrangement": {
            "covers": (
                "the whole billing period, both sides, supply only: everything the CCA "
                "arrangement charged for generation against the whole printed bundled "
                "table. This is what the household paid for supply under one arrangement "
                "against the other, not a per-kWh price comparison"),
            "cca_side": {
                "cea_charged_supply_all_cells_usd": cea_all_cells,
                "cea_product_adders_usd": _c(cca_product_adders_usd),
                "cca_unbundling_riders_usd": _c(unbundling_riders_usd),
                "total_usd": arrangement,
                "note": ("the riders (PCIA, the incremental procurement cost adjustment, "
                         "the economic development program credit) and the product adders "
                         "are period-level lines charged on the period's own kWh"),
            },
            "bundled_side": {
                "sdge_bundled_same_date_table_usd": counterfactual,
                "total_usd": counterfactual,
                "note": ("no rider is added: under bundled service PCIA is charged inside "
                         "the generation rate, which the base statement says on its own "
                         "face — see why_the_riders_belong_on_this_side"),
            },
            "difference_usd": _c(arrangement - counterfactual),
            "difference_pct": _r(100.0 * (arrangement / counterfactual - 1.0), 1),
            "the_two_sides_do_not_price_identical_energy": (
                "the CEA side covers all " + str(len(CELLS)) + " cells; the bundled table "
                "prices only the " + str(len(common_names)) + " billed as net imports. "
                "The whole of that gap is the " + ", ".join(unpriced_names) + " cell(s), "
                "where CEA booked $" + f"{cea_outside_common:,.2f}" + " and SDG&E printed "
                "nothing because the export settled at the annual true-up. That amount is "
                "stated here rather than netted away, and it is the reason this figure is "
                "labelled an arrangement comparison and not a price effect"),
            "cea_booked_on_the_cells_the_bundled_table_does_not_price_usd":
                cea_outside_common,
        },
        "per_cell": common + unpriced,
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
        if not row["priced"]:
            continue
        # These are the artifact's published rates, already rounded; every one of them
        # is non-null because the row is priced, and observed_rate() refuses anything
        # else rather than letting a null or a settlement zero into a denominator.
        rates = {name: observed_rate(row[name], row["cell"], name.replace("_", " "))
                 for name in ("base_rate_effective", "current_rate_effective",
                              "base_delivery_rate", "current_delivery_rate",
                              "base_supply_rate", "current_supply_rate",
                              "sdge_bundled_comparison_rate_current_date")}
        rows.append({
            "cell": row["cell"],
            "base_kwh": row["base_kwh"],
            "current_kwh": row["current_kwh"],
            "base_rate_effective": rates["base_rate_effective"],
            "current_rate_effective": rates["current_rate_effective"],
            "change_pct": _r(100.0 * (rates["current_rate_effective"]
                                      / rates["base_rate_effective"] - 1.0), 1),
            "delivery_change_pct": _r(100.0 * (rates["current_delivery_rate"]
                                               / rates["base_delivery_rate"] - 1.0), 1),
            "supply_vintage_change_pct": _r(
                100.0 * (rates["sdge_bundled_comparison_rate_current_date"]
                         / rates["base_supply_rate"] - 1.0), 1),
            "provider_change_pct": _r(
                100.0 * (rates["current_supply_rate"]
                         / rates["sdge_bundled_comparison_rate_current_date"] - 1.0), 1),
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
        "excluded_cells": [row["cell"] for row in cells if not row["priced"]],
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
            "these are the same dollars as decomposition.aggregate.priced_cells' "
            "delivery_vintage/supply_vintage/provider terms — to within a cent of "
            "rounding, since these sum the per-cell rows — because those terms are "
            "computed over exactly these cells and nowhere else. The excluded cells "
            "carry no price figure at all; their whole dollar change is published "
            "there as netting_settlement"),
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

    # The provider comparison, published as two figures with different scopes: an
    # energy-only supply-price comparison over the cells SDG&E's printed bundled table
    # actually prices, and a whole-period arrangement comparison that carries the
    # period-level riders and adders. Neither is presented as the other.
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
        dec["aggregate"]["priced_cells"]["price_split_paasche_basis"]["provider_usd"]
    whole["per_cell_term_scope"] = (
        "the per-cell provider term inside the price split covers a THIRD scope again: "
        "only the cells billed as net imports in BOTH periods ("
        + ", ".join(dec["aggregate"]["scope"]["priced_cells"]) + "), on 2026 weights, "
        "because it is one term of a two-period index. It is not comparable to either "
        "figure above, both of which are same-date readings on the current period's kWh")
    dec["aggregate"]["provider_comparison"] = whole

    energy = dec["aggregate"]
    priced_l = energy["priced_cells"]["laspeyres"]
    observed = _c(current["total_usd"] - base["total_usd"])
    non_energy = _c(sum(t["change_usd"] for t in bridge))
    # The top-level identity, stated as its terms rather than as a total: the priced
    # cells' index decomposition, the settlement component that is outside it, and the
    # non-energy bridge. Nothing crosses between the three.
    components = _c(priced_l["price_usd"] + priced_l["quantity_usd"]
                    + energy["priced_cells"]["interaction_usd"]
                    + energy["netting_settlement"]["change_usd"] + non_energy)
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
            "identity": (
                "observed change = (price + quantity + interaction, over the priced "
                "cells) + netting/settlement + non-energy bridge"),
            "identity_note": (
                "the price and quantity terms here are the Laspeyres pairing, one of the "
                "two exact pairings the reading publishes; the Paasche pairing "
                "reconciles to the same total. Both cover the priced cells only. The "
                "settlement cells enter as their whole dollar change and are inside "
                "neither term"),
            "observed_change_usd": observed,
            "priced_cells_laspeyres_price_usd": priced_l["price_usd"],
            "priced_cells_laspeyres_quantity_usd": priced_l["quantity_usd"],
            "priced_cells_interaction_usd": energy["priced_cells"]["interaction_usd"],
            "priced_cells_change_usd": energy["priced_cells"]["change_usd"],
            "netting_settlement_usd": energy["netting_settlement"]["change_usd"],
            "energy_change_usd": energy["energy_change_usd"],
            "non_energy_change_usd": non_energy,
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
    print(f"observed change ${r['observed_change_usd']} = priced cells "
          f"${r['priced_cells_change_usd']} + netting/settlement "
          f"${r['netting_settlement_usd']} + non-energy ${r['non_energy_change_usd']}"
          f"  (residual ${r['residual_usd']})")
    pc = d["priced_cells"]
    rd, ps = pc["reading"], pc["price_split_paasche_basis"]
    print(f"priced cells ({', '.join(d['scope']['priced_cells'])}) "
          f"${pc['change_usd']}: price ${rd['price_usd_low']} to "
          f"${rd['price_usd_high']}, quantity ${rd['quantity_usd_low']} to "
          f"${rd['quantity_usd_high']} (bounds, not points; the "
          f"${rd['interval_width_usd']} width is the unallocated interaction). "
          f"Laspeyres quantity splits scale ${rd['of_which_scale_usd']}, TOU mix "
          f"${rd['of_which_tou_mix_usd']}")
    print(f"price splits (Paasche): delivery vintage ${ps['delivery_vintage_usd']} + "
          f"supply vintage ${ps['supply_vintage_usd']} + provider ${ps['provider_usd']}")
    print(f"netting/settlement ${d['netting_settlement']['change_usd']} over "
          f"{', '.join(d['netting_settlement']['cells'])} — whole dollar change, "
          "outside both the price and the quantity figures")
    pv = d["provider_comparison"]
    eo, wp = pv["energy_only_on_the_common_cells"], pv["whole_period_arrangement"]
    print(f"provider, energy only over "
          f"{len(pv['per_cell']) - len(eo['excluded_cells'])} common cells: "
          f"${eo['cea_charged_supply_usd']} CEA vs ${eo['sdge_bundled_same_date_usd']} "
          f"bundled = ${eo['difference_usd']} ({eo['difference_pct']}%); whole-period "
          f"arrangement: ${wp['cca_side']['total_usd']} vs "
          f"${wp['bundled_side']['total_usd']} = ${wp['difference_usd']} "
          f"({wp['difference_pct']}%)")
    print(f"billing mode: {out['billing_mode']['finding']['answer']}; presentation "
          f"changed on "
          f"{out['billing_mode']['finding']['what_changed_and_when']['the_presentation_changed_on']}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
