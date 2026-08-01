#!/usr/bin/env python3
"""Issue #7: the irreducible bill -- the portion of the annual bill that no
battery, no behavior change and no bigger panel can ever remove.

A CONCEPTUAL CORRECTION (issue #7, third adversarial review). An earlier
version of this script summed TWO components -- the per-day fixed charge and
non-bypassable charges (NBC) billed on GROSS imported kWh -- into one
"floor... these packages cannot reach below." That conflated two different
properties. "Non-bypassable" is a regulatory term: SDG&E's non-bypassable
charges cannot be avoided by SWITCHING GENERATION PROVIDER (they follow the
delivery path, not the generation source). It does NOT mean the charge's
DOLLAR AMOUNT is fixed regardless of usage -- NBC is billed per gross-
imported kWh, so if gross imports fall (a bigger battery, more solar, a load
reduction), the dollar amount of NBC falls too, potentially toward zero in
the limit of eliminating grid imports entirely. This script's own
compute_package_gross_imports() proves exactly that: MID and HIGH reduce
gross imports relative to baseline (by roughly 244.0 kWh and 185.1 kWh --
see data/irreducible_bill.json for the current exact figures), and
build_package_floor_fractions() correctly recomputes a LOWER non_bypassable
dollar figure for those packages as a direct result -- a purchase visibly
moving a component the earlier prose called immovable.

Only the per-day fixed charge (Base Services Charge, or the Monthly Service
Fee it replaced 2025-10-01) is genuinely, unconditionally irreducible: it is
billed per day regardless of consumption, even at zero kWh imported, and no
purchase changes that. NBC is real, currently owed on whatever the household
does import, and cannot be avoided by switching generation provider or NEM
structure -- but it is NOT a fixed dollar floor. It is a rate applied to a
quantity purchases can and do move.

THE QUESTION. Every payback this repo quotes (package_results.json's LOW/MID/
HIGH) is against a PROJECTED ANNUAL BILL. This script states the STRICT,
unconditional floor (the fixed charge alone) in dollars and as a percentage
of the 12-month billed total and of each package's projected bill; it also
reports non-bypassable charges as a separate, clearly-labeled figure --
real money, currently owed at today's or each package's own modeled usage,
but not part of the "no purchase can ever remove this" claim -- so a reader
can see how much of a package's headline saving is actually reachable, and
how much of what's left is a movable-but-currently-real charge rather than
a structural floor.

STEP 1 FINDING (does the two-period 2025-10-31 statement need an allocation?).
No. Read directly from the PDF text (private/1-raw-data/electric-bills/
sdge_electric_2025-10-31.pdf): "Non-Bypassable Charges", "Wildfire Fund
Charge", and "Total Taxes & Fees on Electric Charges" are each printed TWICE,
once inside each period's own "Detail of Current Charges" block ("Billing
Period: 9/26/25 - 9/30/25 ... Total Electric Service $24.09" followed by
"Detail of Current Charges - Continued ... Billing Period: 10/1/25 -
10/27/25 ... Total Electric Service $83.71"), each closing with its own
"Total Electric Service $X" line. Even the CCA/CEA generation page repeats
its own header and "Total CCA Electric Generation Charges" line once per
period ($37.28 then $194.64, matching bill_periods_electric.csv's
cca_generation column exactly). The statement is STRUCTURALLY SEPARATED, not
commingled -- no allocation rule is needed. bill_decomposition.charge_lines()
cannot be reused as-is here: it reads the WHOLE statement and its own
docstring says so; on this statement that means two different values in the
same field ("Non-Bypassable Charges 6.57" and "... 13.21"), which its
conflict guard correctly refuses to resolve. This script instead scopes
extraction to each period's own text chunk, bounded by that period's
"Billing Period: ... Total Days: N" anchor and its own closing
"Total Electric Service $X" line (see _period_text_chunks()).

A SECOND, UNRELATED PARSING GAP FOUND WHILE BUILDING THIS (not fixed here --
bill_decomposition.py is owned by a sibling phase; noted for that phase).
bd._LINE_PATTERNS' per-kWh-rate lines ("Wildfire Fund Charge NNN kWh x $RATE
VALUE", "PCIA 2023 NNN kWh x $RATE VALUE", "Incremental Procurement Cost
Adjustment NNN kWh x $RATE VALUE") anchor on a literal "x $", with no
allowance for a minus sign PRINTED BEFORE the dollar sign. PCIA rates are
often negative and SDG&E prints them exactly that way: "PCIA 2023 802 kWh x
-$.03161 -25.35" (2025-03-04 statement). bd's pattern fails to match that
line at all, and bd.charge_lines() would silently return a PCIA total short
by whatever that dropped line was worth -- discovered here because this
script's independent cross-check (see below) came out $25.35 high on that
exact statement until the sign was allowed for. This script's own patterns
(_OWN_PATTERNS) fix this locally; bd.py itself is untouched per this issue's
file ownership.

ALSO DISCOVERED: bd._LINE_PATTERNS' per-kWh-rate lines can print MORE THAN
ONCE within a SINGLE period, not just across two periods -- a mid-cycle rate
change reprints Wildfire Fund Charge, PCIA, and/or Incremental Procurement
Cost Adjustment once per rate segment (confirmed: wildfire on 2025-03-04 and
2026-02-02; PCIA on 2025-03-04 and 2026-05-04). Those repeats are SUMMED here
(they are portions of one period's charge, not a conflict) -- bd.charge_lines
would raise on them if ever asked to parse these statements, which is exactly
why this script does not call it for that purpose.

THE FOUR BUCKETS, per period (extraction only -- see below for which of these
is actually the strict floor):
  1. fixed_daily          bill_periods_electric.csv's own fixed_charge_total
                           (whichever of Base Services Charge / Monthly
                           Service Fee that period actually billed). THE ONLY
                           bucket that is a genuine, unconditional floor: a
                           per-day charge with no usage dependence at all.
  2. non_bypassable_gross  "Non-Bypassable Charges" + "Wildfire Fund Charge",
                           BOTH billed on GROSS imported kWh. Both are
                           necessary: on every period in this corpus, Total
                           Electric Charges only reconciles to the cent when
                           both lines are summed (proven by the independent
                           cross-check below on all 26 periods; omitting
                           Wildfire Fund Charge understates non_bypassable_gross
                           by exactly its own printed value on every period).
                           REAL money, currently owed, and cannot be avoided
                           by switching generation provider -- but its DOLLAR
                           AMOUNT scales with gross imported kWh, so it is NOT
                           a fixed floor: a purchase that reduces gross
                           imports reduces this bucket too (see
                           build_package_floor_fractions()'s docstring).
  3. taxes_and_fees        "Total Taxes & Fees on Electric Charges" -- mostly
                           proportional to the energy charge (franchise fee
                           surcharge, state regulatory fee), so it shrinks
                           with the energy charge and is not part of the
                           strict floor either (see build_floor()'s
                           docstring).
  4. netted_energy         the residual: current_charges - (1) - (2) - (3).
                           The bucket a battery, behavior change, or bigger
                           panel can most directly reduce -- though (2) also
                           moves under those same purchases, just not to zero
                           while the household still imports anything.

Bucket 4 being a residual proves nothing by construction (CLAUDE.md issue
text says so explicitly) -- the actual verification is an INDEPENDENT second
computation of the same number, from the printed TOU tables plus the printed
adjustment lines (PCIA, Incremental Procurement Cost Adjustment, Economic
Development Program Credit, Applied Generation Credit, and either the CCA
page's own charged total or, on a bundled period, the SDG&E generation
table). The two must agree to within the issue's own $0.50 tolerance; on
this corpus the worst period agrees to within $0.02 (rounding), so the
tolerance was never widened.

STRICTLY_IRREDUCIBLE_USD = fixed_daily, summed over
parse_bills.SUMMARY_STATEMENTS_ELEC (this repo's own definition of "the most
recent 12 months of bills", reused rather than re-invented). This is the
ONLY figure this script calls a floor: it accrues per day no matter what,
even at zero kWh imported, and no purchase changes that.

non_bypassable_gross is reported ALONGSIDE it, not summed into it, as
non_bypassable_usd(_historical) -- real money, currently owed, billed on
GROSS imported kWh, which persists even under heavy solar/battery/behavior
use as long as the household ever imports anything, and which cannot be
avoided by switching generation provider. But it IS reduced by a purchase
that reduces gross imports (see build_package_floor_fractions()'s docstring
for how it is actually recomputed, package by package, from each package's
own modeled gross-import kWh -- an adversarial review of this script
(issue #7 follow-up) found an original "held constant... conservative" claim
was asserted, not computed, and the real direction turned out to be
case-by-case, not one-directional; a SECOND adversarial review then found
that fix had only made the non-bypassable term current-vintage, leaving the
fixed-charge term historical -- see build_package_floor_fractions()'s
docstring for that fix, which prices both terms of the per-package figures
at the same current rate vintage as the projected bill they are compared
against; a THIRD adversarial review then found that even after both terms
were vintage-consistent, the two terms were still being SUMMED into one
"floor" and both called irreducible -- fixed here by reporting them
separately and naming only the fixed-charge term a floor).
"""
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bill_decomposition as bd          # noqa: E402  -- read-only use
import rates as R                        # noqa: E402  -- read-only use
from parse_bills import SUMMARY_STATEMENTS_ELEC  # noqa: E402 -- reused, not re-declared
# read-only reuse for Finding 1 (issue #7 review): actually compute each
# package's own modified gross-import kWh instead of asserting a direction.
# Neither module is modified here, and neither of their own artifacts
# (behavior_rebuild.json, battery_dispatch_policies.json) is touched --
# compute_package_gross_imports() below only calls their pure functions.
import behavior_rebuild as br            # noqa: E402  -- read-only use
import battery_dispatch_policies as bdp  # noqa: E402  -- read-only use


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
PERIODS_CSV = DATA / "bill_periods_electric.csv"
TOU_CSV = DATA / "bill_tou_detail.csv"
PACKAGE_JSON = DATA / "package_results.json"
OUT = DATA / "irreducible_bill.json"

RECON_TOLERANCE_USD = 0.50  # the issue's own stated tolerance; never widened here
EPS = 1e-9

# The raw Green Button 15-min export, globbed rather than named outright
# because the committed filename embeds the export's own date range and
# changes on every refresh (see CLAUDE.md's own "cp ... usage.csv" command).
# Needed only by compute_package_gross_imports() (Finding 1, issue #7 review).
RAW_INTERVAL_GLOB = "Electric_15_Minute_*.csv"
# A package-specific gross-import figure sanity-checked against this must sit
# within this fraction of the household's actual historical gross-import
# total (see compute_package_gross_imports()) or something has gone wrong
# with the reused pipeline -- refuse rather than publish a bad recomputation.
GROSS_IMPORT_SANITY_FRACTION = 0.15

# The Wildfire Fund Charge evidence the docstring in rates.py cites (NBC on
# GROSS kWh, not net) -- re-verified independently in build_nbc_gross_check().
_NBC_EVIDENCE_STATEMENT = "2025-10-31"
_NBC_EVIDENCE_PERIOD = "9/26/25 - 9/30/25"


def _c(x):
    return round(float(x) + 0.0, 2)


def _f(x):
    return None if x in (None, "") else float(x)


# ---------------------------------------------------------------------------
# bill_periods_electric.csv, with the fields bd.periods() does not carry
# ---------------------------------------------------------------------------
def load_periods():
    rows = list(csv.DictReader(PERIODS_CSV.open()))
    if not rows:
        raise SystemExit(f"{PERIODS_CSV}: empty")
    out = []
    for r in rows:
        rec = {
            "statement_date": r["statement_date"],
            "period": r["period"],
            "days": int(r["days"]),
            "generation_provider": r["generation_provider"],
            "net_kwh": _f(r["net_kwh"]),
            "gross_kwh": _f(r["gross_kwh"]),
            "sdge_delivery": _f(r["sdge_delivery"]),
            "cca_generation": _f(r["cca_generation"]),
            "current_charges": _f(r["current_charges"]),
            "base_services_charge": _f(r["base_services_charge"]),
            "monthly_service_fee": _f(r["monthly_service_fee"]),
            "fixed_charge_total": _f(r["fixed_charge_total"]),
        }
        for req in ("current_charges", "fixed_charge_total", "net_kwh", "gross_kwh"):
            if rec[req] is None:
                raise SystemExit(
                    f"{PERIODS_CSV}: {rec['statement_date']} / {rec['period']} is "
                    f"missing required field {req!r} -- refusing to treat it as zero")
        out.append(rec)
    return out


def _statements_with_multiple_periods(periods):
    counts = {}
    for p in periods:
        counts.setdefault(p["statement_date"], []).append(p["period"])
    return {s: pers for s, pers in counts.items() if len(pers) > 1}


# ---------------------------------------------------------------------------
# Per-period text scoping (Step 1)
# ---------------------------------------------------------------------------
_ANCHOR = re.compile(r"Billing Period: ([0-9/]+ - [0-9/]+) Total Days: (\d+)")
_CLOSE = re.compile(r"Total Electric Service \$[\d,.]+")


def period_text_chunks(stmt, expected_periods):
    """Split a multi-period statement's text into each period's own
    Detail-of-Current-Charges block: from that period's own 'Billing Period:
    ... Total Days: N' anchor to its own closing 'Total Electric Service $X'
    line. Fails closed if the anchors found do not match the periods
    data/bill_periods_electric.csv says this statement carries -- a future
    statement that splits into a DIFFERENT number of periods, or a layout
    change that drops an anchor, must not be silently mis-scoped."""
    txt = bd.statement_text(stmt)
    anchors = list(_ANCHOR.finditer(txt))
    if len(anchors) != len(expected_periods):
        raise SystemExit(
            f"{stmt}: found {len(anchors)} 'Billing Period:' anchor(s) in the "
            f"statement text but data/bill_periods_electric.csv lists "
            f"{len(expected_periods)} period(s) for it: {expected_periods}")
    chunks = {}
    for m in anchors:
        start = m.start()
        close = _CLOSE.search(txt, start)
        if not close:
            raise SystemExit(f"{stmt}: no closing 'Total Electric Service $X' line "
                             f"after anchor at offset {start}")
        chunks[m.group(1)] = txt[start:close.end()]
    missing = set(expected_periods) - set(chunks)
    if missing:
        raise SystemExit(f"{stmt}: anchors found do not name the periods the CSV "
                         f"expects; missing {sorted(missing)}")
    return chunks


# ---------------------------------------------------------------------------
# Charge-line extraction, scoped to one period, tolerant of segment reprints
# ---------------------------------------------------------------------------
_SUM_ACROSS_SEGMENTS = {"wildfire_fund_charge", "pcia",
                        "incremental_procurement_cost_adjustment"}
_FIRST_HIT_ONLY = {"applied_generation_credit_energy"}

# bd._LINE_PATTERNS' per-kWh-rate patterns anchor "kWh x \$RATE" with no
# allowance for a minus sign printed BEFORE the dollar sign ("kWh x
# -$.03161"), which PCIA prints routinely. Our own patterns allow that sign
# on the (uncaptured) rate token while still capturing only the VALUE that
# follows it. See the module docstring for how this was found.
_OWN_PATTERNS = {
    "wildfire_fund_charge":
        rf"Wildfire Fund Charge\s+[\d,]+ kWh x [−-]?\${bd._NUM}\s+({bd._NUM})",
    "pcia":
        rf"PCIA \d+\s+[\d,]+ kWh x [−-]?\${bd._NUM}\s+({bd._NUM})",
    "incremental_procurement_cost_adjustment":
        rf"Incremental Procurement Cost Adjustment\s+[\d,]+ kWh x "
        rf"[−-]?\${bd._NUM}\s+({bd._NUM})",
}
_REQUIRED_LINES = ("non_bypassable_charges", "wildfire_fund_charge",
                   "total_taxes_and_fees")


def charge_lines_for_period(text_scope, label):
    """The named non-TOU-energy lines for ONE period, scoped to text_scope
    (the whole statement for a single-period statement, or that period's own
    chunk from period_text_chunks() otherwise). Per-kWh-rate lines that
    reprint once per mid-cycle rate segment (wildfire_fund_charge, pcia,
    incremental_procurement_cost_adjustment -- all proven to do this on this
    corpus) are summed; applied_generation_credit_energy prints twice per
    period regardless (energy block, then mirrored inside the tax block) and
    only the first (energy block) counts, matching bd.charge_lines()'s own
    documented reasoning. Any other repeated line with DIFFERING values is a
    genuine conflict and raises."""
    out = {}
    for name, pat in bd._LINE_PATTERNS:
        pat = _OWN_PATTERNS.get(name, pat)
        hits = re.findall(pat, text_scope)
        if not hits:
            continue
        if name in _FIRST_HIT_ONLY:
            out[name] = bd._f(hits[0])
            continue
        if name in _SUM_ACROSS_SEGMENTS:
            out[name] = sum(bd._f(h) for h in hits)
            continue
        if len(set(hits)) > 1:
            raise SystemExit(f"{label}: conflicting {name} within one period's "
                             f"text: {hits}")
        out[name] = bd._f(hits[0])
    for req in _REQUIRED_LINES:
        if req not in out:
            raise SystemExit(f"{label}: missing required line {req!r}")
    return out


# ---------------------------------------------------------------------------
# The independent cross-check: printed TOU tables + printed adjustment lines
# ---------------------------------------------------------------------------
def tou_sums(stmt, period):
    """(delivery $, generation $) for one period, from data/bill_tou_detail.csv,
    via bd.tou_detail(). A rate_per_kwh == 0 row is a NEM export-settlement
    convention (kwh <= 0, deferred to true-up), never a free-energy price --
    guarded explicitly rather than trusted, per the settlement-zero-is-not-
    a-price lesson tou_spread.py already carries."""
    detail = bd.tou_detail(stmt, period)
    for (section, season, tou), cell in detail.items():
        if cell["kwh"] > 0 and abs(cell["usd"]) < EPS:
            # a positive-kwh cell billing to exactly $0.00 needs its rate
            # checked directly -- tou_detail() does not expose the raw rate,
            # so re-derive it from the underlying rows.
            raise SystemExit(
                f"{stmt}/{period}: {section} {season} {tou} billed {cell['kwh']} "
                "positive kWh at $0.00 -- that is not a settlement zero, refusing "
                "to treat it as free energy")
    delivery = sum(v["usd"] for k, v in detail.items() if k[0] == "delivery")
    generation = sum(v["usd"] for k, v in detail.items() if k[0] == "generation")
    return _c(delivery), _c(generation)


def independent_netted_energy(period_rec, lines, delivery_sum, generation_sum):
    """The netted-energy bucket, computed WITHOUT touching current_charges or
    fixed/NBC/tax: printed delivery TOU charge + the actual charged supply
    (the CCA/CEA page total under CCA, or the SDG&E generation TOU table on a
    bundled period) + the adjustment lines that ride on top of supply (PCIA,
    Incremental Procurement Cost Adjustment, Economic Development Program
    Credit, Applied Generation Credit).

    On a CCA period SDG&E ALSO prints its own bundled-generation comparison
    table and a matching "Electricity Generation Credit" that cancels it to
    the cent (this is bd.py's own documented finding, re-checked per period
    here as generation_credit_cancel_usd) -- that pair is excluded from the
    supply term rather than included and subtracted, since it nets to zero
    by construction and the CCA page total is the actual charge."""
    is_cca = period_rec["generation_provider"] == "CCA"
    gen_credit = lines.get("electricity_generation_credit", 0.0)
    if is_cca:
        cancel = _c(generation_sum + gen_credit)
        supply = period_rec["cca_generation"]
    else:
        cancel = 0.0
        supply = generation_sum
        if gen_credit:
            raise SystemExit(
                f"{period_rec['statement_date']}/{period_rec['period']}: unexpected "
                "electricity_generation_credit on a bundled (non-CCA) period")
    total = (delivery_sum + supply
             + lines.get("pcia", 0.0)
             + lines.get("incremental_procurement_cost_adjustment", 0.0)
             + lines.get("economic_development_program_credit", 0.0)
             + lines.get("applied_generation_credit_energy", 0.0))
    return _c(total), cancel


# ---------------------------------------------------------------------------
# Per-period classification (Step 2)
# ---------------------------------------------------------------------------
def classify_periods(periods):
    multi = _statements_with_multiple_periods(periods)
    rows = []
    for p in periods:
        stmt, period = p["statement_date"], p["period"]
        label = f"{stmt}/{period}"
        if stmt in multi:
            chunks = period_text_chunks(stmt, multi[stmt])
            lines = charge_lines_for_period(chunks[period], label)
        else:
            lines = charge_lines_for_period(bd.statement_text(stmt), label)

        fixed_daily = p["fixed_charge_total"]
        non_bypassable_gross = _c(lines["non_bypassable_charges"]
                                  + lines["wildfire_fund_charge"])
        taxes_and_fees = lines["total_taxes_and_fees"]
        current_charges = p["current_charges"]
        netted_energy = _c(current_charges - fixed_daily - non_bypassable_gross
                          - taxes_and_fees)

        four_bucket_sum = _c(fixed_daily + non_bypassable_gross + taxes_and_fees
                            + netted_energy)
        four_bucket_diff = _c(four_bucket_sum - current_charges)

        delivery_sum, generation_sum = tou_sums(stmt, period)
        independent, cancel = independent_netted_energy(p, lines, delivery_sum,
                                                        generation_sum)
        cross_check_diff = _c(netted_energy - independent)

        rows.append({
            "statement_date": stmt,
            "period": period,
            "days": p["days"],
            "generation_provider": p["generation_provider"],
            "net_kwh": p["net_kwh"],
            "gross_kwh": p["gross_kwh"],
            "current_charges_usd": current_charges,
            "fixed_daily_usd": fixed_daily,
            "non_bypassable_gross_usd": non_bypassable_gross,
            "taxes_and_fees_usd": taxes_and_fees,
            "netted_energy_usd": netted_energy,
            "netted_energy_independent_usd": independent,
            "netted_energy_cross_check_diff_usd": cross_check_diff,
            "netted_energy_cross_check_pass": abs(cross_check_diff) <= RECON_TOLERANCE_USD,
            "four_bucket_sum_usd": four_bucket_sum,
            "four_bucket_diff_usd": four_bucket_diff,
            "four_bucket_check_pass": abs(four_bucket_diff) <= RECON_TOLERANCE_USD,
            "generation_credit_cancel_usd": cancel,
            # Finding 1 (issue #7 SECOND adversarial review, this pass): this
            # value used to be computed and stored but never gated on --
            # independent_netted_energy()'s docstring calls the CCA
            # generation/credit pair "nets to zero by construction," but
            # nothing ever checked that the printed pair actually DID cancel
            # on this period. Checked here, uniformly, on EVERY period (not
            # just CCA ones): on a bundled (non-CCA) period, cancel is
            # hardcoded to 0.0 above (there is no credit pair to cancel
            # there), so the check is trivially satisfied today -- but
            # checking it uniformly, rather than carving out a CCA-only
            # exemption, means a future change to that hardcoding would still
            # be caught by the same gate instead of needing a new one.
            "generation_credit_cancel_pass": abs(cancel) <= RECON_TOLERANCE_USD,
            "fixed_charge_kind": ("monthly_service_fee"
                                 if p["monthly_service_fee"] is not None
                                 else "base_services_charge"),
        })
    return rows


def worst_residual(rows):
    return max(rows, key=lambda r: abs(r["netted_energy_cross_check_diff_usd"]))


# ---------------------------------------------------------------------------
# Step 3: the 12-month floor
# ---------------------------------------------------------------------------
def build_floor(rows):
    """The STRICT, unconditional floor -- fixed_daily alone -- summed over
    parse_bills.SUMMARY_STATEMENTS_ELEC -- this repo's own definition of "the
    most recent 12 months of bills" (12 statements; the 2025-10-31 statement
    carries two periods, both in-window by construction since they share its
    statement_date, so the window covers 13 periods).

    strictly_irreducible_usd (bucket 1, fixed_daily) is the ONLY figure this
    function calls a floor: it is billed per day no matter what, even at
    zero kWh imported, and no purchase -- battery, behavior change, or bigger
    panel -- changes that.

    non_bypassable_usd_historical (bucket 2) is reported ALONGSIDE it, not
    summed into it. It is real money, actually billed over this same window,
    and it cannot be avoided by switching generation provider or NEM
    structure -- but its DOLLAR AMOUNT is billed on GROSS imported kWh, which
    a purchase that reduces gross imports DOES reduce (see
    build_package_floor_fractions() for the per-package proof that this
    actually happens, not just could). It is therefore NOT part of
    strictly_irreducible_usd; combined_usd_historical is kept as a
    reference figure (what these two components actually cost together over
    the last 12 real months) but is explicitly not itself called a floor.

    taxes_and_fees is not part of either figure: within the window its own
    two largest components (Franchise Fee Equivalent Surcharge, State
    Regulatory Fee) are levied ON the energy charge or on kWh already
    counted elsewhere, so they shrink roughly in proportion to the energy
    charge a purchase reduces.

    ONE RATE VINTAGE PER PERIOD, not one vintage for the whole sum (CLAUDE.md
    9's rule, applied to a floor rather than a projection): each period's
    fixed_daily_usd is that period's OWN ACTUALLY BILLED fixed charge --
    $16.00/month before 2025-10-01, $0.79343/day after -- never today's rate
    applied backward. The transition falls inside this window: of the 13
    periods, the ones below list which used which.

    SANITY-CHECKED, NOT bounded by, the window total (issue #7 THIRD
    adversarial review). combined_usd_historical (fixed_daily +
    non_bypassable_gross) is NOT guaranteed to sit below
    total_current_charges_usd in general -- that would require netted_energy
    (the signed NEM settlement bucket) to always be non-negative, which is
    only true for a net-IMPORTING household. In a net-EXPORTING window,
    export credits can legitimately push netted_energy negative enough that
    current_charges falls below fixed_daily + non_bypassable_gross alone.
    What this function DOES verify is the aggregate four-bucket identity --
    fixed_daily + non_bypassable_gross + taxes_and_fees + netted_energy ==
    total_current_charges_usd, the window-summed version of the same
    per-period check classify_periods() already performs -- which holds
    regardless of netted_energy's sign."""
    window_stmts = set(SUMMARY_STATEMENTS_ELEC)
    window_rows = [r for r in rows if r["statement_date"] in window_stmts]
    if not window_rows:
        raise SystemExit("no periods matched SUMMARY_STATEMENTS_ELEC -- window is empty")
    missing_stmts = window_stmts - {r["statement_date"] for r in window_rows}
    if missing_stmts:
        raise SystemExit(f"SUMMARY_STATEMENTS_ELEC names statement(s) with no row in "
                         f"bill_periods_electric.csv: {sorted(missing_stmts)}")

    strictly_irreducible_usd = _c(sum(r["fixed_daily_usd"] for r in window_rows))
    non_bypassable_usd_historical = _c(
        sum(r["non_bypassable_gross_usd"] for r in window_rows))
    combined_usd_historical = _c(strictly_irreducible_usd + non_bypassable_usd_historical)
    total_current_charges_usd = _c(sum(r["current_charges_usd"] for r in window_rows))

    # Sanity check on the AGGREGATE four-bucket identity (issue #7 THIRD
    # adversarial review; this replaces a PRIOR guard here that asserted
    # combined_usd_historical -- fixed_daily + non_bypassable_gross ALONE,
    # omitting netted_energy -- could never exceed total_current_charges_usd.
    # That was false in general: netted_energy (bucket 4) is a signed NEM
    # settlement term, and in a window where the household is a net EXPORTER
    # its export credits can make netted_energy negative enough that
    # current_charges legitimately falls BELOW fixed_daily +
    # non_bypassable_gross alone -- even though every period reconciles
    # exactly and nothing is wrong. This household is a net importer in
    # every period of the current corpus (net_kwh > 0 throughout), so the
    # old guard never fired here, but it would have wrongly refused to
    # regenerate the artifact the moment a net-export window appeared. The
    # actual invariant -- true regardless of netted_energy's sign -- is the
    # same four-bucket identity classify_periods() already checks per period
    # (four_bucket_check_pass), summed over the window: fixed_daily +
    # non_bypassable_gross + taxes_and_fees + netted_energy must equal
    # total_current_charges_usd. Computed here only to gate on, not stored
    # in the returned dict -- the window total is already fully described by
    # the fields below.
    taxes_and_fees_usd_historical = _c(
        sum(r["taxes_and_fees_usd"] for r in window_rows))
    netted_energy_usd_historical = _c(
        sum(r["netted_energy_usd"] for r in window_rows))
    four_bucket_sum_historical = _c(
        strictly_irreducible_usd + non_bypassable_usd_historical
        + taxes_and_fees_usd_historical + netted_energy_usd_historical)
    four_bucket_diff_historical = _c(four_bucket_sum_historical - total_current_charges_usd)
    if abs(four_bucket_diff_historical) > RECON_TOLERANCE_USD:
        raise SystemExit(
            "the aggregated four-bucket identity (fixed_daily + "
            "non_bypassable_gross + taxes_and_fees + netted_energy) does not "
            f"match the window's total current_charges: sum="
            f"${four_bucket_sum_historical} total=${total_current_charges_usd} "
            f"diff=${four_bucket_diff_historical} (tolerance "
            f"${RECON_TOLERANCE_USD}) -- that cannot be right")

    strict_pct = round(100.0 * strictly_irreducible_usd / total_current_charges_usd, 2)
    non_bypassable_pct = round(
        100.0 * non_bypassable_usd_historical / total_current_charges_usd, 2)
    combined_pct = round(100.0 * combined_usd_historical / total_current_charges_usd, 2)
    historical_gross_kwh_window = _c(sum(r["gross_kwh"] for r in window_rows))

    monthly_fee_periods = sorted(
        f"{r['statement_date']}/{r['period']}" for r in window_rows
        if r["fixed_charge_kind"] == "monthly_service_fee")
    bsc_periods = sorted(
        f"{r['statement_date']}/{r['period']}" for r in window_rows
        if r["fixed_charge_kind"] == "base_services_charge")

    return {
        "window_statements": sorted(window_stmts),
        "window_period_count": len(window_rows),
        "strictly_irreducible_usd": strictly_irreducible_usd,
        "strictly_irreducible_pct_of_total": strict_pct,
        "non_bypassable_usd_historical": non_bypassable_usd_historical,
        "non_bypassable_pct_of_total_historical": non_bypassable_pct,
        "combined_usd_historical": combined_usd_historical,
        "combined_pct_of_total_historical": combined_pct,
        "historical_gross_kwh_window": historical_gross_kwh_window,
        "total_current_charges_usd": total_current_charges_usd,
        "tariff_transition_note": (
            f"{len(monthly_fee_periods)} of {len(window_rows)} periods in this window "
            "billed the pre-2025-10-01 flat Monthly Service Fee ($16.00/month); "
            f"{len(bsc_periods)} billed the per-day Base Services Charge "
            "($0.79343/day) that replaced it 2025-10-01. Each period's own "
            "fixed_daily_usd is what THAT period actually billed -- the per-day "
            "rate is never applied backward onto a period billed the flat fee."),
        "periods_on_monthly_service_fee": monthly_fee_periods,
        "periods_on_base_services_charge": bsc_periods,
    }


# ---------------------------------------------------------------------------
# Step 4: the floor under each package
# ---------------------------------------------------------------------------
def _raw_interval_csv():
    """Locate the raw Green Button 15-min export under private/1-raw-data/ --
    the same file CLAUDE.md's own documented sandbox command copies to
    usage.csv. Globbed because the committed filename embeds the export's own
    date range and changes on every refresh. Fails closed on anything but
    exactly one match, rather than silently picking the wrong statement year
    or a stale leftover copy."""
    hits = sorted((ROOT / "private" / "1-raw-data").glob(RAW_INTERVAL_GLOB))
    if len(hits) != 1:
        raise SystemExit(
            f"expected exactly one {RAW_INTERVAL_GLOB!r} file under "
            f"private/1-raw-data/ to compute package-specific gross imports "
            f"(Finding 1, issue #7 review); found {len(hits)}: {hits}")
    return hits[0]


def compute_package_gross_imports():
    """Finding 1 (issue #7 adversarial review). The retired version of
    build_package_floor_fractions() held non_bypassable_gross CONSTANT at its
    historical dollar figure for every package, reasoning (one-directionally)
    that a grid-charged battery can only ADD gross imports via round-trip
    loss. That reasoning was incomplete: battery_dispatch_policies.run_batt()
    charges from solar surplus FIRST (decrementing exports, not imports) and
    only tops up from the grid during super-off-peak hours when surplus falls
    short; it also DISCHARGES during high-value windows, decreasing imports.
    Whether the net effect on GROSS imports goes up or down is a real
    empirical question, not a one-directional certainty -- so this function
    actually answers it instead of asserting a direction.

    METHOD: re-run the EXACT package definitions battery_dispatch_policies.py
    already committed to (its own post_behavior block), not a new
    configuration invented for this check:
      LOW  = behavior_rebuild.shift_ev(), the 100%-compliance EV-only shift
             (scenario a) -- the same call package_results.json's LOW is
             built from.
      MID  = battery_dispatch_policies.run_batt() on the EV-shifted series,
             13.5 kWh usable, policy "greedy" -- battery_dispatch_policies.
             json's post_behavior.mid.
      HIGH = the same, 27.0 kWh usable -- post_behavior.high.
    behavior_rebuild.py and battery_dispatch_policies.py are called as
    READ-ONLY libraries: neither is modified, and neither
    behavior_rebuild.json nor battery_dispatch_policies.json is written here
    -- those artifacts stay owned exclusively by their own generators. The
    raw interval CSV path is set on behavior_rebuild.CSV only for the
    duration of the load() call and restored immediately after, so this
    function works regardless of the caller's own working directory (unlike
    running behavior_rebuild.py itself, which expects a literal ./usage.csv
    in its cwd, per the private/verify sandbox convention).

    SANITY CHECKS (before any of this is trusted): the computed baseline
    (package-free) gross-import total must sit within
    GROSS_IMPORT_SANITY_FRACTION of the household's actual historical
    gross-import total (data/bill_periods_electric.csv, summed over the same
    12-month window build_floor() uses) -- a wildly different figure would
    mean the interval export, the household config, or the reused pipeline
    has drifted, not that the household's usage changed. LOW's gross imports
    must equal the baseline's EXACTLY: shift_ev() only moves WHEN energy is
    drawn (and _conserve() already refuses a run that drops or invents
    energy), so a 100%-EV-shift scenario cannot change the annual gross-
    import total at all -- if it ever does, something in the reused pipeline
    is not doing what its own docstring says, and this must fail closed
    rather than publish a wrong LOW figure.

    ALSO RETURNS annual_days: the exact number of distinct calendar days in
    the loaded frame (br.load() slices exactly WINDOW_END-365d..WINDOW_END
    and br.R.validate_interval_coverage() already fails closed on any gap, so
    this is not a guess -- it is the same day count rates.bill_nem_monthly()
    itself sums BSC over, one month at a time, when package_results.json's
    projected_bill_current_rates_yr was built). Finding 1 (issue #7 second
    adversarial review) uses this to price the per-package floor's fixed-
    charge component at the SAME current-rate vintage as everything else in
    that fraction, instead of the household's historical mixed-vintage
    fixed-charge total."""
    real_csv = br.CSV
    br.CSV = str(_raw_interval_csv())
    try:
        d = br.load()
    finally:
        br.CSV = real_csv
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    baseline_gross_kwh = float(imp0.sum())

    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions), sop_idx, sop_ts)
    low_gross_kwh = float(imp_sh.sum())
    if abs(low_gross_kwh - baseline_gross_kwh) > EPS:
        raise SystemExit(
            "compute_package_gross_imports: LOW's 100%-EV-shift gross imports "
            f"({low_gross_kwh:.3f} kWh) differ from the baseline "
            f"({baseline_gross_kwh:.3f} kWh) by more than floating-point "
            "rounding -- behavior_rebuild.shift_ev() is supposed to only move "
            "WHEN energy is drawn, never the annual total; refusing to "
            "publish a package-specific figure built on a pipeline that no "
            "longer matches its own documented invariant")

    mid_imp, _, mid_served, _ = bdp.run_batt(d, imp_sh, gen0, 13.5, "greedy")
    mid_gross_kwh = float(mid_imp.sum())

    high_imp, _, high_served, _ = bdp.run_batt(d, imp_sh, gen0, 27.0, "greedy")
    high_gross_kwh = float(high_imp.sum())

    annual_days = int(d.dt.dt.date.nunique())

    return {
        "baseline_gross_kwh": round(baseline_gross_kwh, 1),
        "LOW": {"gross_kwh": round(low_gross_kwh, 1)},
        "MID": {"gross_kwh": round(mid_gross_kwh, 1), "kwh_served": round(mid_served, 1)},
        "HIGH": {"gross_kwh": round(high_gross_kwh, 1), "kwh_served": round(high_served, 1)},
        "kwh_moved_ev_shift": round(moved, 1),
        "annual_days": annual_days,
        "method": (
            "behavior_rebuild.load() on the raw Green Button interval export "
            "(private/1-raw-data/" + RAW_INTERVAL_GLOB + "), behavior_rebuild."
            "shift_ev() for the 100%-compliance EV-shift scenario every "
            "package sits on top of, then battery_dispatch_policies.run_batt("
            "..., 'greedy') at 13.5 kWh (MID) and 27.0 kWh (HIGH) usable -- "
            "the same calls and package definitions battery_dispatch_policies."
            "json's own committed post_behavior block already uses. "
            "annual_days is the distinct-calendar-day count of that same "
            "365-day interval frame, used by build_package_floor_fractions() "
            "to price the fixed-charge component at the current rate "
            "vintage."),
    }


def _sanity_check_gross_imports(gross, floor):
    """Refuse to use compute_package_gross_imports()'s output if the
    baseline it computed does not resemble the household's actual historical
    gross-import total (data/bill_periods_electric.csv, the same 12-month
    window build_floor() sums) -- these are two different data sources (a
    365-day interval export vs a 12-month bill-statement window) so they are
    not expected to match exactly, only to agree in magnitude."""
    hist = floor["historical_gross_kwh_window"]
    baseline = gross["baseline_gross_kwh"]
    if hist <= 0:
        raise SystemExit("historical_gross_kwh_window is non-positive -- cannot sanity-check")
    rel_diff = abs(baseline - hist) / hist
    if rel_diff > GROSS_IMPORT_SANITY_FRACTION:
        raise SystemExit(
            f"compute_package_gross_imports: baseline gross imports "
            f"({baseline} kWh, interval export) differ from the historical "
            f"12-month bill total ({hist} kWh) by {rel_diff*100:.1f}%, more "
            f"than the {GROSS_IMPORT_SANITY_FRACTION*100:.0f}% sanity margin "
            "-- refusing to publish a package-specific NBC recomputation "
            "built on a figure this far from what the bills actually show")


def build_package_floor_fractions(floor, gross):
    """What fraction of each LOW/MID/HIGH package's projected annual bill
    (data/package_results.json, current-rate model) is the STRICT floor
    (fixed charge alone), reported with full confidence -- and, SEPARATELY,
    what fraction is non-bypassable charges AT THIS PACKAGE'S OWN MODELED
    USAGE -- real money, currently owed, but NOT itself irreducible, since a
    different or larger purchase would move it further. combined_usd /
    combined_fraction_of_projected_bill is kept too (both added together)
    but is explicitly not itself the answer to "how much can never be
    removed" -- only strictly_irreducible_usd is.

    ONE RATE VINTAGE FOR THIS WHOLE COMPUTATION (Finding 1, issue #7 SECOND
    adversarial review -- CLAUDE.md 9's rule). The denominator,
    projected_bill_current_rates_yr, is a fully current-rate, 365-day MODELED
    bill (rates.bill_nem_monthly() at constant current rates, via
    data/package_results.json):
      1. strictly_irreducible_usd = annual_days x rates.BSC (the current
         per-day Base Services Charge), where annual_days is
         compute_package_gross_imports()'s own distinct-calendar-day count of
         the exact 365-day interval frame package_results.json's own model
         was built from -- i.e. the SAME construction bill_nem_monthly()
         itself uses (days.nunique() x BSC, one month at a time, summed).
         This term does NOT vary by package: a per-day charge does not depend
         on how much energy is imported or when.
      2. non_bypassable_usd = this package's own modeled gross-import kWh
         (compute_package_gross_imports()) x rates.NBC -- current-vintage,
         matching the denominator, and DIFFERENT for each package because
         gross imports differ by package.
    Both terms are CURRENT-rate-vintage, matching the denominator they are
    divided into. This is DELIBERATELY DIFFERENT from twelve_month_floor
    (build_floor()), which stays entirely historical on purpose -- that
    section answers "what did these components actually cost over the last
    12 real months," not "what fraction of a current-rate projected bill is
    each of them." The two sections answer different questions and must not
    be reconciled to each other.

    THE CONCEPTUAL POINT (issue #7 THIRD adversarial review): an earlier
    version of this function summed both terms into one "floor_usd" and
    called the whole thing irreducible. That is wrong for term 2:
    non_bypassable_usd is NOT held constant across packages here -- it is
    this package's own recomputation, and on this corpus MID and HIGH's
    gross imports (and therefore their non_bypassable_usd) come out LOWER
    than LOW's/baseline's, proving a purchase can and does move this
    component. Only strictly_irreducible_usd (term 1) is invariant across
    packages by construction, because a per-day charge cannot depend on
    usage -- that invariance is what a genuine floor requires, and it is
    what distinguishes term 1 from term 2."""
    _sanity_check_gross_imports(gross, floor)
    strictly_irreducible_usd = _c(gross["annual_days"] * R.BSC)
    packages = json.loads(PACKAGE_JSON.read_text())["packages"]
    out = {}
    for name, pkg in packages.items():
        bill = pkg["projected_bill_current_rates_yr"]
        if bill <= 0:
            raise SystemExit(f"package {name}: non-positive projected bill {bill}")
        pkg_gross_kwh = gross[name]["gross_kwh"]
        non_bypassable_usd = _c(pkg_gross_kwh * R.NBC)
        combined_usd = _c(strictly_irreducible_usd + non_bypassable_usd)
        out[name] = {
            "projected_bill_current_rates_yr": bill,
            "strictly_irreducible_usd": strictly_irreducible_usd,
            "strictly_irreducible_fraction_of_projected_bill":
                round(strictly_irreducible_usd / bill, 4),
            "annual_days": gross["annual_days"],
            "gross_import_kwh": pkg_gross_kwh,
            "gross_import_kwh_vs_baseline": _c(pkg_gross_kwh - gross["baseline_gross_kwh"]),
            "non_bypassable_usd": non_bypassable_usd,
            "non_bypassable_fraction_of_projected_bill":
                round(non_bypassable_usd / bill, 4),
            "combined_usd": combined_usd,
            "combined_fraction_of_projected_bill": round(combined_usd / bill, 4),
            "method": ("strictly_irreducible_usd = annual_days x rates.BSC "
                      "(current per-day Base Services Charge; invariant "
                      "across packages, a per-day charge does not depend on "
                      "how much energy is imported) -- the ONLY figure here "
                      "that is a genuine floor. non_bypassable_usd = this "
                      "package's own modeled gross-import kWh "
                      "(compute_package_gross_imports()) x rates.NBC "
                      "(current-vintage combined NBC + Wildfire Fund Charge "
                      "rate, $/kWh) -- real money at this package's own "
                      "usage, NOT held constant across packages, and NOT "
                      "itself irreducible (a different purchase would move "
                      "it further). combined_usd sums both for reference "
                      "only; it is not itself a floor."),
        }
    return out


def build_baseline_floor_fraction(gross):
    """Finding 1, issue #7 FOURTH adversarial review (a Codex pass over the
    already-committed script). §11's report prose compared
    model_baseline_current_rates (data/package_results.json -- a FULLY
    current-rate MODEL of the no-package/baseline scenario: the household's
    own historical interval data, priced at TODAY'S tariff throughout,
    including today's BSC and NBC) against twelve_month_floor's ACTUAL
    HISTORICAL 12-month split ($264.10 fixed / $479.76 non-bypassable -- a
    real mix of pre- and post-2025-10-01 tariffs, whatever each period
    actually billed). That is the exact CLAUDE.md 9 vintage-mixing violation
    the THIRD adversarial review already fixed for the LOW/MID/HIGH package
    fractions in build_package_floor_fractions() -- just missed here,
    because it is a different paragraph about a different scenario (no
    purchase at all, not a package) that this script had not yet priced at
    a consistent vintage.

    SAME CONSTRUCTION as build_package_floor_fractions(), at baseline (no
    purchase) instead of a package, so the split is priced at the SAME
    current-rate vintage as the $4,904/yr total it is compared against:
      1. strictly_irreducible_usd = annual_days x rates.BSC -- IDENTICAL to
         every package's fixed term (all four scenarios -- baseline, LOW,
         MID, HIGH -- sit on the same 365-day interval frame,
         gross['annual_days']; a per-day charge does not depend on usage).
      2. non_bypassable_usd = gross['baseline_gross_kwh'] (compute_package_
         gross_imports()'s own baseline figure -- already computed and
         sanity-checked there, not re-derived here) x rates.NBC.
    Both terms are current-rate-vintage, matching model_baseline_current_
    rates, the denominator they are divided into. This is DELIBERATELY
    SEPARATE from twelve_month_floor, which stays entirely historical on
    purpose (see build_floor()'s and build_package_floor_fractions()'s own
    docstrings for why the two sections are not reconciled to each other).

    Returned as its own top-level artifact entry (baseline_floor), parallel
    to but distinct from package_floor_fractions' LOW/MID/HIGH: the
    baseline is "no package" -- not one of the three purchase options -- so
    it does not belong inside that dict."""
    strictly_irreducible_usd = _c(gross["annual_days"] * R.BSC)
    non_bypassable_usd = _c(gross["baseline_gross_kwh"] * R.NBC)
    combined_usd = _c(strictly_irreducible_usd + non_bypassable_usd)
    pkg_doc = json.loads(PACKAGE_JSON.read_text())
    bill = pkg_doc["model_baseline_current_rates"]
    if bill <= 0:
        raise SystemExit(f"model_baseline_current_rates is non-positive: {bill}")
    return {
        "projected_bill_current_rates_yr": bill,
        "strictly_irreducible_usd": strictly_irreducible_usd,
        "strictly_irreducible_fraction_of_projected_bill":
            round(strictly_irreducible_usd / bill, 4),
        "annual_days": gross["annual_days"],
        "gross_import_kwh": gross["baseline_gross_kwh"],
        "non_bypassable_usd": non_bypassable_usd,
        "non_bypassable_fraction_of_projected_bill":
            round(non_bypassable_usd / bill, 4),
        "combined_usd": combined_usd,
        "combined_fraction_of_projected_bill": round(combined_usd / bill, 4),
        "method": (
            "the no-package baseline's own current-rate-vintage split, "
            "priced the same way as build_package_floor_fractions()'s "
            "LOW/MID/HIGH entries so it can be compared against the "
            "equally current-rate model_baseline_current_rates total (issue "
            "#7 fourth adversarial review, CLAUDE.md 9): "
            "strictly_irreducible_usd = annual_days x rates.BSC (same "
            "365-day frame as every package -- a per-day charge that does "
            "not depend on usage). non_bypassable_usd = "
            "gross['baseline_gross_kwh'] x rates.NBC -- the household's own "
            "modeled gross imports with no purchase applied. Both terms are "
            "current-vintage, matching the denominator "
            "(model_baseline_current_rates); this is a distinct question "
            "from twelve_month_floor's historical split, which stays "
            "historical on purpose and must not be compared against a "
            "current-rate total."),
    }


# ---------------------------------------------------------------------------
# Step 5: minimum-bill provision
# ---------------------------------------------------------------------------
_MIN_CHARGE_SENTENCE = re.compile(
    r"Minimum\s+Charge\s+Adjustment:\s+The\s+running\s+total\s+of\s+any\s+applicable\s+"
    r"minimum\s+charges\s+for\s+the\s+current\s+\"Relevant\s+Period\"\.\s+If\s+you\s+are\s+"
    r"a\s+net\s+generator\s+for\s+the\s+year,\s+these\s+basic\s+service\s+fees\s+and\s+any\s+"
    r"applicable\s+taxes\s+will\s+represent\s+all\s+you\s+have\s+to\s+pay\.")
_MIN_CHARGE_LINE_ITEM = re.compile(
    rf"Minimum Charge Adjustment\D{{0,20}}([−-]?\${bd._NUM})")

# A true-up year is a calendar year (365 days), or 366 across a February
# that includes the 29th -- used to decide whether a group of periods
# sharing one printed true-up date is a COMPLETE cycle or a partial slice.
_TRUE_UP_YEAR_MIN_DAYS = 365
_TRUE_UP_YEAR_MAX_DAYS = 366


def statement_true_up_date(stmt):
    """Finding 2 (issue #7 review). This statement's own printed 'True-Up
    Date:' field -- bd._TRUE_UP_FIELD, reused read-only, not re-derived as a
    new pattern. Confirmed against every statement in this household's
    corpus: the field prints on EVERY statement (25/25), not only the
    annual-settlement one, and survives the 2025-10-01 template change that
    dropped the deferral SENTENCE bd.classify_statement()'s accrual branch
    depends on -- so reading this field directly works uniformly across old
    and new template statements alike, where the heavier NEM-ledger
    classification needs different branches for each. bd._true_up_date() is
    NOT used here: it parses the deferral sentence's 'Mon Day, Year' form,
    whereas this field prints 'MM/DD/YYYY' directly, so the date is parsed
    with that format instead."""
    txt = bd.statement_text(stmt)
    m = bd._TRUE_UP_FIELD.search(txt)
    if not m:
        raise SystemExit(
            f"{stmt}: no printed 'True-Up Date:' field found -- cannot "
            "group this statement's period(s) into a true-up year")
    return dt.datetime.strptime(m.group(1), "%m/%d/%Y").date()


def group_periods_by_true_up_year(rows, true_up_date_by_stmt):
    """Finding 2 (issue #7 review). Group periods (dicts carrying at least
    statement_date, period, days, net_kwh) by the true-up date the printed
    field on their OWN statement names -- NOT by
    parse_bills.SUMMARY_STATEMENTS_ELEC's rolling 12-statement billing
    window, which was found to straddle two different true-up years on this
    corpus: its first 6 periods print true-up date 12/26/2025 and its last
    6 print 12/28/2026 -- two different actual settlement years, previously
    summed together as if they were one "annual" figure.

    true_up_date_by_stmt maps each row's statement_date to an already-
    resolved dt.date (statement_true_up_date() in real use, reading the
    printed field off a real PDF; supplied directly in a test using
    synthetic statement dates with no backing PDF -- this function itself
    never reads a PDF, so it is unit-testable without the private archive).

    For each true-up-date group, reports whether the corpus covers a
    COMPLETE true-up CYCLE for it -- not just enough periods to sum to
    something -- by requiring all three:
      1. contiguous: the group's periods, ordered by start date, must each
         start the day after the previous one ends (no gap, no overlap).
      2. ends_on_true_up_date: the LAST period must end exactly ON the
         group's true-up date -- i.e. the settlement period itself is in
         the corpus, not just periods still accruing toward it.
      3. total_days is a real year: 365, or 366 across a leap-year
         February (_TRUE_UP_YEAR_MIN_DAYS/_MAX_DAYS).
    A group failing any of these is an incomplete slice: the tail or head
    of a year this corpus does not fully carry, or a year still accruing
    (has not yet reached its own true-up date)."""
    by_true_up = {}
    for r in rows:
        tud = true_up_date_by_stmt[r["statement_date"]]
        by_true_up.setdefault(tud, []).append(r)

    out = []
    for tud, group in sorted(by_true_up.items()):
        ordered = sorted(group, key=lambda r: bd._period_dates(r["period"])[0])
        spans = [bd._period_dates(r["period"]) for r in ordered]
        gaps_days = [(spans[i + 1][0] - spans[i][1]).days
                    for i in range(len(spans) - 1)]
        contiguous = all(g == 1 for g in gaps_days)
        total_days = sum(r["days"] for r in ordered)
        coverage_start, coverage_end = spans[0][0], spans[-1][1]
        ends_on_true_up_date = coverage_end == tud
        complete = (contiguous and ends_on_true_up_date
                   and _TRUE_UP_YEAR_MIN_DAYS <= total_days <= _TRUE_UP_YEAR_MAX_DAYS)
        annual_net_kwh_sum = _c(sum(r["net_kwh"] for r in ordered))
        out.append({
            "true_up_date": tud.isoformat(),
            "statements": sorted({r["statement_date"] for r in ordered}),
            "period_count": len(ordered),
            "total_days": total_days,
            "coverage_start": coverage_start.isoformat(),
            "coverage_end": coverage_end.isoformat(),
            "contiguous": contiguous,
            "ends_on_true_up_date": ends_on_true_up_date,
            "complete_true_up_cycle": complete,
            "annual_net_kwh_sum": annual_net_kwh_sum,
            "annual_net_generator": annual_net_kwh_sum < 0,
        })
    return out


def _select_true_up_years(true_up_years):
    """Finding 2 (issue #7 SECOND adversarial review, this pass). Given the
    true-up-year groups group_periods_by_true_up_year() returns, decide which
    ones the annual minimum-bill trigger is actually evaluated against
    (chosen_years), on what basis (annual_trigger_basis /
    annual_trigger_limitation), and the most recent one of those
    (most_recent_year).

    Pulled out of build_minimum_bill_provision() as its own pure function --
    no PDF reads, no statement text -- specifically so it is unit-testable
    against a synthetic multi-complete-year corpus without needing the
    private bill-PDF archive (see test_irreducible_bill.py's
    case_two_complete_true_up_cycles_are_both_reported).

    THE BUG THIS FIXES: the retired call site picked
    'chosen_years[0] if len(chosen_years) == 1 else None' -- correct only
    because this household's corpus today has exactly one complete true-up
    cycle. The moment a second complete cycle appears (which will happen
    naturally as more billing data accumulates -- not a hypothetical edge
    case), that collapsed to None and the caller's three singular fields
    (annual_trigger_true_up_date / annual_net_kwh_sum / annual_net_generator)
    went null, even though annual_trigger_basis would still correctly say
    'complete_true_up_cycle' and provision_triggered_in_this_data (computed
    separately, via any() over the FULL chosen_years list) would still report
    a real, aggregated answer -- an internally inconsistent artifact.

    FIX: most_recent_year is always populated whenever chosen_years is
    non-empty (picked by true_up_date, which sorts correctly as an ISO
    string), never nulled out just because there happen to be two or more
    chosen years. The caller keeps reporting the FULL chosen_years list
    (every year's own detail, not just one) as the primary structure, and
    uses most_recent_year only for the backward-compatible singular
    fields."""
    complete_years = [g for g in true_up_years if g["complete_true_up_cycle"]]

    if complete_years:
        annual_trigger_basis = "complete_true_up_cycle"
        annual_trigger_limitation = None
        chosen_years = complete_years
    elif true_up_years:
        annual_trigger_basis = "closest_available_approximation"
        chosen_years = [max(true_up_years, key=lambda g: g["total_days"])]
        annual_trigger_limitation = (
            "no true-up year in this corpus is fully covered by a "
            "contiguous run of periods from the day after the prior "
            "true-up date through a period ending exactly on this one -- "
            f"reporting the closest available true-up year "
            f"({chosen_years[0]['true_up_date']}, {chosen_years[0]['total_days']} "
            "of a real year's days covered) instead, with this limitation "
            "stated rather than silently treated as a complete year")
    else:
        annual_trigger_basis = "no_true_up_date_found"
        chosen_years = []
        annual_trigger_limitation = (
            "no true-up date could be established for any period in this data")

    most_recent_year = (max(chosen_years, key=lambda g: g["true_up_date"])
                        if chosen_years else None)
    return annual_trigger_basis, chosen_years, annual_trigger_limitation, most_recent_year


def build_minimum_bill_provision(rows, statements):
    """What the tariff/bills say about a minimum-bill provision, and whether
    it was ever actually triggered in this household's own 12-month window.

    (a) This household's own statements (2024-06-27 through 2025-09-02; the
        template changed 2025-10-01 and dropped the sentence) carry, in the
        "Understanding Your Net Metering Summary" glossary block, a
        "Minimum Charge Adjustment" concept: "the running total of any
        applicable minimum charges for the current 'Relevant Period'. If you
        are a net generator for the year, these basic service fees and any
        applicable taxes will represent all you have to pay." Note this
        names basic service fees + taxes, not NBC by name -- NBC is billed
        on gross imports regardless of net position, so it can apply even in
        a year this provision would otherwise floor the bill at fees+taxes.
        No statement in the corpus ever prints this as a dollar LINE ITEM
        (only the glossary sentence) -- confirmed by regex scan of every
        statement's text, not by inspection of one.
    (b) research/rates-reference.md (~line 63) names a different figure,
        $0.413/day minimum bill, for the "EV-TOU (separately metered
        legacy)" variant -- NOT this household's actual EV-TOU-5 BUNDLED-
        meter plan. No EV-TOU-5-specific minimum-bill dollar figure was
        found in the bills or in research/rates-reference.md / TECHNICAL.md.
    (c) The provision only binds in a year the household is a NET GENERATOR
        -- and "the year" means the ANNUAL SUM of net_kwh over the whole
        "Relevant Period" (per the sentence quoted in (a)), not any single
        month, and not any arbitrary 12-statement window either. (Finding 2,
        issue #7 second adversarial review: an earlier version of this
        function tested any(net_kwh < 0) across the 13 window periods and
        reported that as "the year" -- fixed by summing first, then testing
        the sign. A FOURTH adversarial review then found that "the window"
        being summed was itself wrong: parse_bills.SUMMARY_STATEMENTS_ELEC
        is a rolling 12-statement BILLING window, not the utility's own
        annual NEM TRUE-UP year, which is anchored to this household's PTO
        date (12/27/2019) and prints its own "True-Up Date:" field on every
        statement. On this corpus SUMMARY_STATEMENTS_ELEC's 13 periods
        straddle TWO different true-up years -- printed true-up date
        12/26/2025 on its first 6 periods, 12/28/2026 on its last 6 -- so
        summing across the whole window mixed partial data from two
        different actual settlement years, not "net generator for the
        year" in the sense the provision means it.

        FIXED: statement_true_up_date() reads each statement's own printed
        True-Up Date field (bd._TRUE_UP_FIELD, reused read-only) and
        group_periods_by_true_up_year() groups ALL periods in the corpus
        (not just the SUMMARY_STATEMENTS_ELEC window -- a true-up year can,
        and on this corpus does, sit partly or wholly outside that rolling
        window) by which true-up year they accrue toward, then checks
        whether the corpus covers a COMPLETE cycle for each: contiguous
        periods from the day after the prior true-up date through a period
        ending exactly on this one, totalling a real year's days. The
        annual trigger (annual_net_kwh_sum / annual_net_generator /
        provision_triggered_in_this_data below) is now evaluated against
        the true-up year(s) the corpus actually completes, not the rolling
        billing window -- see true_up_years for the full per-year detail
        and annual_trigger_basis/annual_trigger_limitation for whether a
        complete cycle was found or the closest available approximation is
        being reported instead, with that limitation stated rather than
        silently treated as a complete year.

        The per-period breakdown (monthly_net_position_window) and its
        any_period_net_generator_in_window flag are kept as before --
        useful, explicitly month-level, informational context over the
        rolling billing window -- but they were never the trigger and still
        are not; only the true-up-year-grouped annual figures are.

        FINDING 2, issue #7 SECOND adversarial review (this pass): the
        annual_trigger_true_up_date / annual_net_kwh_sum / annual_net_generator
        fields below are singular by name but must NOT collapse to null just
        because the corpus completes more than one true-up cycle -- see
        _select_true_up_years()'s docstring. annual_trigger_years now carries
        every chosen year's own full detail (the primary structure), and
        most_recent_true_up_year names, explicitly, which one the singular
        fields are sourced from."""
    sentence_statements = []
    line_item_statements = []
    for stmt in statements:
        txt = bd.statement_text(stmt)
        if _MIN_CHARGE_SENTENCE.search(txt):
            sentence_statements.append(stmt)
        m = _MIN_CHARGE_LINE_ITEM.search(txt)
        if m:
            line_item_statements.append({"statement": stmt, "printed": m.group(1)})

    window_stmts = set(SUMMARY_STATEMENTS_ELEC)
    window_rows = sorted(
        (r for r in rows if r["statement_date"] in window_stmts),
        key=lambda r: (r["statement_date"], r["period"]))
    monthly_net_position = [
        {"statement_date": r["statement_date"], "period": r["period"],
         "net_kwh": r["net_kwh"], "is_net_generator_this_period": r["net_kwh"] < 0}
        for r in window_rows]
    # Month-level only, over the rolling billing window -- informative
    # (which periods were individually net-negative) but explicitly NOT the
    # tariff's own trigger condition, and NOT the true-up year either
    # (Finding 2, issue #7 second and fourth adversarial reviews).
    any_period_net_generator_in_window = any(
        m["is_net_generator_this_period"] for m in monthly_net_position)

    # THE actual trigger condition (Finding 2, fourth adversarial review):
    # grouped by each period's own printed True-Up Date, over the WHOLE
    # corpus (rows), not the SUMMARY_STATEMENTS_ELEC rolling window -- a
    # true-up year need not align with that window at all.
    true_up_date_by_stmt = {stmt: statement_true_up_date(stmt) for stmt in statements}
    true_up_years = group_periods_by_true_up_year(rows, true_up_date_by_stmt)
    (annual_trigger_basis, chosen_years, annual_trigger_limitation,
     most_recent_year) = _select_true_up_years(true_up_years)
    complete_years = [g for g in true_up_years if g["complete_true_up_cycle"]]

    provision_triggered_in_this_data = any(g["annual_net_generator"] for g in chosen_years)

    return {
        "sentence_found_in_statements": sentence_statements,
        "sentence_absent_after": "2025-10-01 (statement template changed; the "
                                 "Net Metering Summary glossary block that carried "
                                 "it is not printed on statements from 2025-10-01 on)",
        "dollar_line_item_ever_printed": line_item_statements,
        "legacy_separately_metered_ev_tou_min_bill_usd_per_day": 0.413,
        "legacy_figure_applicable_to_this_household": False,
        "legacy_figure_reason": ("research/rates-reference.md's $0.413/day figure "
                                 "is documented there as the separately-metered "
                                 "legacy EV-TOU variant; this household is billed "
                                 "under EV-TOU-5 with a bundled (single) meter, "
                                 "confirmed on every statement's Detail of Current "
                                 "Charges header ('Rate: Time of Use - "
                                 "EVTOU5-Residential')."),
        "ev_tou_5_specific_min_bill_found": False,
        "monthly_net_position_window": monthly_net_position,
        "any_period_net_generator_in_window": any_period_net_generator_in_window,
        "true_up_years": true_up_years,
        "complete_true_up_years": [g["true_up_date"] for g in complete_years],
        "annual_trigger_basis": annual_trigger_basis,
        "annual_trigger_limitation": annual_trigger_limitation,
        # Finding 2 (issue #7 SECOND adversarial review, this pass): report
        # EVERY chosen year's own full detail here, rather than collapsing to
        # one nullable "the" year -- see _select_true_up_years()'s docstring
        # for why collapsing broke the moment a second complete true-up cycle
        # appears in the corpus. chosen_years is the SAME list
        # provision_triggered_in_this_data (above) already aggregates over,
        # so this field can never disagree with that one about how many
        # years were actually used.
        "annual_trigger_years": chosen_years,
        # Always populated whenever chosen_years is non-empty (never null
        # just because there happen to be two or more) -- the one
        # explicitly-named "pick one" field a consumer that wants a single
        # year, rather than the full list, can read.
        "most_recent_true_up_year": most_recent_year,
        # Backward-compatible singular fields, kept for existing consumers --
        # now sourced from most_recent_year (the latest of chosen_years) so
        # they stay populated even when chosen_years grows past one entry,
        # instead of nulling out as they did before this fix.
        "annual_trigger_true_up_date": most_recent_year["true_up_date"] if most_recent_year else None,
        "annual_net_kwh_sum": most_recent_year["annual_net_kwh_sum"] if most_recent_year else None,
        "annual_net_generator": most_recent_year["annual_net_generator"] if most_recent_year else None,
        "provision_triggered_in_this_data": provision_triggered_in_this_data,
    }


# ---------------------------------------------------------------------------
# Step 6: NBC-on-gross re-verification
# ---------------------------------------------------------------------------
_WILDFIRE_LINE = re.compile(rf"Wildfire Fund Charge\s+([\d,]+) kWh x \$({bd._NUM})\s+({bd._NUM})")


def build_nbc_gross_check(rows):
    """Fresh re-derivation of rates.py's documented claim -- 'NBC on gross
    imported kWh, never netted (bill evidence: wildfire charged on 308 gross
    kWh vs 224 net)' -- from THIS script's own regex over the statement text,
    not a citation of that docstring. Locates the exact statement/period,
    re-reads the printed Wildfire Fund Charge kWh figure, and compares it
    against that period's gross_kwh AND net_kwh from
    data/bill_periods_electric.csv."""
    stmt, period = _NBC_EVIDENCE_STATEMENT, _NBC_EVIDENCE_PERIOD
    row = next((r for r in rows if r["statement_date"] == stmt and r["period"] == period),
              None)
    if row is None:
        raise SystemExit(f"{stmt}/{period}: not found in bill_periods_electric.csv -- "
                         "the NBC-on-gross evidence statement cited by rates.py is "
                         "missing from this corpus")
    # This statement carries two periods; scope to this one exactly as
    # classify_periods() does, so the same text this script actually billed
    # against is what gets re-checked here.
    periods_for_stmt = [period, "10/1/25 - 10/27/25"]
    chunks = period_text_chunks(stmt, periods_for_stmt)
    text_scope = chunks[period]
    m = _WILDFIRE_LINE.search(text_scope)
    if not m:
        raise SystemExit(f"{stmt}/{period}: no 'Wildfire Fund Charge ... kWh x $...' "
                         "line found in this period's own text -- cannot re-verify")
    printed_kwh = float(m.group(1).replace(",", ""))
    printed_rate = float(m.group(2))
    printed_usd = float(m.group(3))
    matches_gross = abs(printed_kwh - row["gross_kwh"]) < EPS
    matches_net = abs(printed_kwh - row["net_kwh"]) < EPS
    return {
        "statement": stmt,
        "period": period,
        "printed_wildfire_line": m.group(0).strip(),
        "printed_kwh": printed_kwh,
        "printed_rate_usd_per_kwh": printed_rate,
        "printed_usd": printed_usd,
        "csv_gross_kwh": row["gross_kwh"],
        "csv_net_kwh": row["net_kwh"],
        "printed_kwh_matches_gross_kwh": matches_gross,
        "printed_kwh_matches_net_kwh": matches_net,
        "confirmation": ("CONFIRMED: the printed Wildfire Fund Charge is billed on "
                        "GROSS imported kWh, not net -- it matches gross_kwh and "
                        "differs from net_kwh"
                        if matches_gross and not matches_net else
                        "NOT CONFIRMED -- printed kWh does not match gross_kwh as "
                        "rates.py's docstring claims; re-examine"),
    }


CAVEAT = (
    "strictly_irreducible_usd, in BOTH twelve_month_floor and "
    "package_floor_fractions, is the ONLY figure in this artifact that is a "
    "genuine, unconditional floor: the per-day fixed charge, billed no matter "
    "what the household buys or does, even at zero kWh imported. "
    "non_bypassable_usd_historical / non_bypassable_usd is a SEPARATE figure, "
    "not summed into the floor: non-bypassable charges (NBC), billed on GROSS "
    "imported kWh. NBC cannot be avoided by switching generation provider or "
    "NEM structure -- but its DOLLAR AMOUNT is not fixed; it scales with gross "
    "imports, and a purchase that reduces gross imports reduces it too. "
    "combined_usd_historical / combined_usd sums both components for "
    "reference (what they actually cost together) but is explicitly NOT "
    "itself called a floor -- only strictly_irreducible_usd is. "
    "twelve_month_floor states what these two components ACTUALLY cost over "
    "the most recent 12 months of statements: every dollar figure in that "
    "section is read from the PDFs this repository already has committed "
    "derivatives of (data/bill_periods_electric.csv, data/bill_tou_detail.csv) "
    "or re-derived directly from statement text; nothing in it is modeled or "
    "projected forward at a different rate vintage. package_floor_fractions "
    "answers a DIFFERENT question: what share of a fully CURRENT-rate "
    "projected bill (data/package_results.json) is each component. Both of "
    "its components are current-rate-vintage, matching the denominator they "
    "are divided into -- non_bypassable_usd is a per-package RECOMPUTATION "
    "(see build_package_floor_fractions()'s and "
    "compute_package_gross_imports()'s docstrings) built by re-running "
    "behavior_rebuild.py's and battery_dispatch_policies.py's own committed "
    "package definitions against the raw interval export, then pricing the "
    "result at rates.NBC; strictly_irreducible_usd is annual_days x "
    "rates.BSC, the same construction rates.bill_nem_monthly() itself uses "
    "to build projected_bill_current_rates_yr. package_floor_fractions is "
    "deliberately NOT reconciled against twelve_month_floor's historical "
    "figures -- the two sections answer different questions (what actually "
    "happened over 12 real months, vs. what fraction of a current-rate "
    "projected bill each component is) and mixing their vintages was "
    "exactly Finding 1 of this script's second adversarial review. A THIRD "
    "adversarial review found that, even after both terms were vintage-"
    "consistent, they were still being summed into one 'floor' and both "
    "called irreducible -- non_bypassable_usd is a per-package "
    "recomputation, and on this corpus MID and HIGH's own modeled gross "
    "imports (and therefore their non_bypassable_usd) come out LOWER than "
    "LOW's/the baseline's, proving a purchase moves this component; it is "
    "reported separately here rather than summed into the floor. "
    "baseline_floor (issue #7 fourth adversarial review) answers the SAME "
    "current-rate-vintage question as package_floor_fractions but for the "
    "no-package baseline: what share of model_baseline_current_rates (the "
    "no-solar-purchase, fully current-rate model in data/package_results.json) "
    "is the strict floor vs. non-bypassable charges at baseline usage -- "
    "priced the same way, so it can be safely compared against that same "
    "current-rate total, unlike twelve_month_floor's historical split. The "
    "minimum-bill-provision test is against the NEM true-up 'Minimum Charge "
    "Adjustment' concept printed on this household's own pre-2025-10-01 "
    "statements, not against a dollar figure specific to EV-TOU-5 -- none "
    "was found. Its trigger condition (Finding 2, fourth adversarial review) "
    "is the ANNUAL sum of net_kwh over the true-up year(s) the corpus "
    "actually completes (true_up_years, grouped by each statement's own "
    "printed 'True-Up Date:' field, not by parse_bills.SUMMARY_STATEMENTS_ELEC's "
    "rolling billing window, which was found to straddle two different "
    "true-up years on this corpus) -- annual_net_kwh_sum/annual_net_generator "
    "now refer to that true-up-year figure, and annual_trigger_basis/"
    "annual_trigger_limitation say whether a complete cycle was found. "
    "monthly_net_position_window/any_period_net_generator_in_window remain "
    "separate, informational, month-level detail over the rolling billing "
    "window -- never the trigger. A SECOND adversarial review then found "
    "that annual_trigger_true_up_date/annual_net_kwh_sum/annual_net_generator "
    "collapsed to null the moment more than one complete true-up cycle was "
    "chosen -- not a problem in this corpus today (exactly one complete "
    "cycle), but a near-certain future one as this repo's own bills "
    "accumulate. Fixed by reporting annual_trigger_years (every chosen "
    "year's own full detail, never collapsed) as the primary structure, and "
    "most_recent_true_up_year (always populated whenever any year was "
    "chosen) as the explicitly-named single-year pick the singular fields "
    "are sourced from -- so no field reads as null while a sibling field "
    "reports a real, aggregated answer from the same underlying data. "
    "generation_credit_cancel_usd is also now gated: it used to be computed "
    "and stored per period but never checked before build() returned, "
    "exactly the 'computed a check, never asked it anything' gap the first "
    "adversarial review already fixed for netted_energy_cross_check_pass "
    "and four_bucket_check_pass -- generation_credit_cancel_pass closes it, "
    "checked on every period (trivially satisfied on non-CCA periods, where "
    "it is hardcoded to 0.0, but checked uniformly rather than exempted)."
)


def _assert_periods_reconcile(rows):
    """Finding 2 (issue #7 adversarial review): classify_periods() computes
    netted_energy_cross_check_pass and four_bucket_check_pass per period but,
    before that fix, nothing ever CHECKED them before build() returned --
    main() would write whatever came back, failed reconciliation or not. A
    parser regression, a corrupted CSV row, or a future statement with
    unexpected formatting could produce a period that fails its own
    reconciliation, and the artifact would still be generated and atomically
    replace the committed one, with the failed boolean sitting quietly
    inside it.

    THREE conditions are checked, not two (Finding 1, issue #7 SECOND
    adversarial review, this pass): generation_credit_cancel_pass was added
    alongside netted_energy_cross_check_pass and four_bucket_check_pass, but
    was never added to THIS gate -- the same "computed a check, never asked
    it anything" failure the first two conditions were already fixed for.
    independent_netted_energy()'s docstring calls the CCA generation-charge /
    generation-credit pair "nets to zero by construction" and uses that as
    license to exclude both from the independent supply term rather than
    include-and-subtract them; if the generation-credit regex ever silently
    stopped matching a real credit line (a bill template change, a corrupted
    PDF, a future statement with different wording), gen_credit would fall
    back to lines.get(..., 0.0) = 0.0, the cancellation would come out
    nonzero, and -- before this fix -- nothing would catch it.

    Called from build(), before anything downstream is computed or returned,
    so it cannot be bypassed by calling build() directly."""
    bad_recon = [r for r in rows
                if not r["netted_energy_cross_check_pass"]
                or not r["four_bucket_check_pass"]]
    bad_cancel = [r for r in rows if not r["generation_credit_cancel_pass"]]
    if bad_recon or bad_cancel:
        parts = []
        if bad_recon:
            parts.append(
                f"{len(bad_recon)} period(s) failed their own reconciliation: " +
                "; ".join(
                    f"{r['statement_date']}/{r['period']} "
                    f"(cross_check_diff=${r['netted_energy_cross_check_diff_usd']:+.2f}, "
                    f"four_bucket_diff=${r['four_bucket_diff_usd']:+.2f})"
                    for r in bad_recon))
        if bad_cancel:
            parts.append(
                f"{len(bad_cancel)} period(s) failed generation-credit "
                "cancellation (docstring claims this pair nets to zero by "
                "construction; it did not on this period): " +
                "; ".join(
                    f"{r['statement_date']}/{r['period']} "
                    f"(generation_credit_cancel_usd=${r['generation_credit_cancel_usd']:+.2f})"
                    for r in bad_cancel))
        raise SystemExit("; ".join(parts) + " -- cannot publish")


def _assert_nbc_gross_check_confirmed(nbc_check):
    """Finding 2's second half (Codex's addition, confirmed worth doing): the
    NBC-on-gross re-verification exists to PROVE the documented NBC-on-gross
    treatment is real, re-derived from statement text rather than cited from
    rates.py's docstring. A run where it silently fails to confirm (a bill
    template change, a corrupted statement, a regex that stopped matching)
    must not publish either -- the artifact would otherwise carry an
    unconfirmed claim that reads exactly like a confirmed one everywhere else
    it is used."""
    if not nbc_check["confirmation"].startswith("CONFIRMED"):
        raise SystemExit(
            "NBC-on-gross re-verification did not confirm the documented "
            f"treatment: {nbc_check['confirmation']} (statement "
            f"{nbc_check['statement']}/{nbc_check['period']}) -- refusing to "
            "publish an artifact whose NBC-on-gross proof did not check out")


def build():
    periods = load_periods()
    rows = classify_periods(periods)
    rows.sort(key=lambda r: (r["statement_date"], r["period"]))
    _assert_periods_reconcile(rows)
    worst = worst_residual(rows)
    floor = build_floor(rows)
    gross = compute_package_gross_imports()
    package_fractions = build_package_floor_fractions(floor, gross)
    baseline_floor = build_baseline_floor_fraction(gross)
    all_statements = sorted({r["statement_date"] for r in rows})
    min_bill = build_minimum_bill_provision(rows, all_statements)
    nbc_check = build_nbc_gross_check(rows)
    _assert_nbc_gross_check_confirmed(nbc_check)

    tou_row_count = sum(1 for _ in csv.DictReader(TOU_CSV.open()))

    return {
        "periods": rows,
        "period_count": len(rows),
        "worst_residual_period": {
            "statement_date": worst["statement_date"],
            "period": worst["period"],
            "cross_check_diff_usd": worst["netted_energy_cross_check_diff_usd"],
        },
        "reconciliation_tolerance_usd": RECON_TOLERANCE_USD,
        "twelve_month_floor": floor,
        "package_floor_fractions": package_fractions,
        "baseline_floor": baseline_floor,
        "minimum_bill_provision": min_bill,
        "nbc_gross_reverification": nbc_check,
        "caveat": CAVEAT,
        "provenance": {
            "bill_periods_electric_csv": {"file": "data/bill_periods_electric.csv",
                                          "rows": len(periods)},
            "bill_tou_detail_csv": {"file": "data/bill_tou_detail.csv",
                                    "rows": tou_row_count},
            "package_results_json": {"file": "data/package_results.json"},
            "electric_statements_scanned": len(all_statements),
            "summary_statements_elec_window": sorted(SUMMARY_STATEMENTS_ELEC),
            "no_pii": ("account/meter numbers, name, and address appear on the "
                      "source PDFs but are never read into or written to this "
                      "artifact -- only dollar figures, kWh quantities, dates, "
                      "and period labels are extracted"),
        },
    }


def main():
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(OUT.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(result, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, OUT)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"wrote {OUT.relative_to(ROOT)}")
    floor = result["twelve_month_floor"]
    print(f"  12-month strict floor (fixed charge only): "
          f"${floor['strictly_irreducible_usd']:.2f} "
          f"({floor['strictly_irreducible_pct_of_total']}% of "
          f"${floor['total_current_charges_usd']:.2f})")
    print(f"  12-month non-bypassable charges (real, but usage-dependent): "
          f"${floor['non_bypassable_usd_historical']:.2f} "
          f"({floor['non_bypassable_pct_of_total_historical']}%)")
    for name, pf in sorted(result["package_floor_fractions"].items()):
        print(f"  {name}: strict floor is "
              f"{pf['strictly_irreducible_fraction_of_projected_bill']*100:.1f}%, "
              f"+ non-bypassable {pf['non_bypassable_fraction_of_projected_bill']*100:.1f}%, "
              f"of the projected ${pf['projected_bill_current_rates_yr']}/yr bill")
    bf = result["baseline_floor"]
    print(f"  baseline (no package), current rates: strict floor is "
          f"{bf['strictly_irreducible_fraction_of_projected_bill']*100:.1f}%, "
          f"+ non-bypassable {bf['non_bypassable_fraction_of_projected_bill']*100:.1f}%, "
          f"of the projected ${bf['projected_bill_current_rates_yr']}/yr bill")
    mb = result["minimum_bill_provision"]
    print(f"  minimum-bill annual trigger basis: {mb['annual_trigger_basis']} "
          f"({mb['annual_trigger_true_up_date']}): "
          f"triggered={mb['provision_triggered_in_this_data']}")
    w = result["worst_residual_period"]
    print(f"  worst per-period cross-check residual: ${w['cross_check_diff_usd']:+.2f} "
          f"({w['statement_date']} / {w['period']})")
    print(f"  NBC-on-gross re-verification: {result['nbc_gross_reverification']['confirmation']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
