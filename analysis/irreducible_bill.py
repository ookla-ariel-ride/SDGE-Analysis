#!/usr/bin/env python3
"""Issue #7: the irreducible bill -- the portion of the annual bill that no
battery, no behavior change and no bigger panel can ever remove.

THE QUESTION. Every payback this repo quotes (package_results.json's LOW/MID/
HIGH) is against a PROJECTED ANNUAL BILL. Some of that bill is charged no
matter what the household buys or does: a per-day fixed charge (Base Services
Charge, or the Monthly Service Fee it replaced 2025-10-01) and non-bypassable
charges (NBC) billed on GROSS imported kWh, both mandated regardless of net
usage. This script states that floor in dollars, as a percentage of the
12-month billed total, and as a fraction of each package's projected bill --
so a reader can see how much of a package's headline saving is actually
reachable.

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

THE FOUR BUCKETS, per period:
  1. fixed_daily          bill_periods_electric.csv's own fixed_charge_total
                           (whichever of Base Services Charge / Monthly
                           Service Fee that period actually billed).
  2. non_bypassable_gross  "Non-Bypassable Charges" + "Wildfire Fund Charge",
                           BOTH billed on GROSS imported kWh. Both are
                           necessary: on every period in this corpus, Total
                           Electric Charges only reconciles to the cent when
                           both lines are summed (proven by the independent
                           cross-check below on all 26 periods; omitting
                           Wildfire Fund Charge understates non_bypassable_gross
                           by exactly its own printed value on every period).
  3. taxes_and_fees        "Total Taxes & Fees on Electric Charges" -- mostly
                           proportional to the energy charge (franchise fee
                           surcharge, state regulatory fee), so it shrinks
                           with the energy charge and is NOT part of the
                           floor (see build_floor()'s docstring).
  4. netted_energy         the residual: current_charges - (1) - (2) - (3).
                           This is the ONLY bucket a battery, behavior change,
                           or bigger panel can ever reduce.

Bucket 4 being a residual proves nothing by construction (CLAUDE.md issue
text says so explicitly) -- the actual verification is an INDEPENDENT second
computation of the same number, from the printed TOU tables plus the printed
adjustment lines (PCIA, Incremental Procurement Cost Adjustment, Economic
Development Program Credit, Applied Generation Credit, and either the CCA
page's own charged total or, on a bundled period, the SDG&E generation
table). The two must agree to within the issue's own $0.50 tolerance; on
this corpus the worst period agrees to within $0.02 (rounding), so the
tolerance was never widened.

FLOOR = fixed_daily + non_bypassable_gross, summed over
parse_bills.SUMMARY_STATEMENTS_ELEC (this repo's own definition of "the most
recent 12 months of bills", reused rather than re-invented). Both components
are billed regardless of net usage: the fixed charge accrues per day no
matter what; NBC is billed on GROSS imported kWh, which persists even under
heavy solar/battery/behavior use as long as the household ever imports
anything (see build_floor()'s docstring for the vintage-mixing rule and
build_package_floor_fractions()'s docstring for why the floor is held
constant, not recomputed, across the LOW/MID/HIGH packages).
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
    """fixed_daily + non_bypassable_gross, summed over
    parse_bills.SUMMARY_STATEMENTS_ELEC -- this repo's own definition of "the
    most recent 12 months of bills" (12 statements; the 2025-10-31 statement
    carries two periods, both in-window by construction since they share its
    statement_date, so the window covers 13 periods).

    taxes_and_fees is NOT part of the floor: within the window its own two
    largest components (Franchise Fee Equivalent Surcharge, State Regulatory
    Fee) are levied ON the energy charge or on kWh already counted elsewhere,
    so they shrink roughly in proportion to the energy charge a purchase
    reduces. Bucket 2 does not shrink that way: NBC is billed on GROSS
    imported kWh regardless of the net position, so it survives any purchase
    that still leaves the household importing grid power at all.

    ONE RATE VINTAGE PER PERIOD, not one vintage for the whole sum (CLAUDE.md
    9's rule, applied to a floor rather than a projection): each period's
    fixed_daily_usd is that period's OWN ACTUALLY BILLED fixed charge --
    $16.00/month before 2025-10-01, $0.79343/day after -- never today's rate
    applied backward. The transition falls inside this window: of the 13
    periods, the ones below list which used which."""
    window_stmts = set(SUMMARY_STATEMENTS_ELEC)
    window_rows = [r for r in rows if r["statement_date"] in window_stmts]
    if not window_rows:
        raise SystemExit("no periods matched SUMMARY_STATEMENTS_ELEC -- window is empty")
    missing_stmts = window_stmts - {r["statement_date"] for r in window_rows}
    if missing_stmts:
        raise SystemExit(f"SUMMARY_STATEMENTS_ELEC names statement(s) with no row in "
                         f"bill_periods_electric.csv: {sorted(missing_stmts)}")

    floor_usd = _c(sum(r["fixed_daily_usd"] + r["non_bypassable_gross_usd"]
                       for r in window_rows))
    total_current_charges_usd = _c(sum(r["current_charges_usd"] for r in window_rows))
    if floor_usd > total_current_charges_usd + EPS:
        raise SystemExit("the floor exceeds the window's total current_charges -- "
                         "that cannot be a floor")
    pct = round(100.0 * floor_usd / total_current_charges_usd, 2)

    monthly_fee_periods = sorted(
        f"{r['statement_date']}/{r['period']}" for r in window_rows
        if r["fixed_charge_kind"] == "monthly_service_fee")
    bsc_periods = sorted(
        f"{r['statement_date']}/{r['period']}" for r in window_rows
        if r["fixed_charge_kind"] == "base_services_charge")

    return {
        "window_statements": sorted(window_stmts),
        "window_period_count": len(window_rows),
        "floor_usd": floor_usd,
        "total_current_charges_usd": total_current_charges_usd,
        "floor_pct_of_total": pct,
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
def build_package_floor_fractions(floor):
    """What fraction of each LOW/MID/HIGH package's projected annual bill
    (data/package_results.json, current-rate model) is this floor -- i.e. how
    much of the package's headline saving is even reachable, since a package
    cannot save money on a charge it cannot touch.

    METHOD: the floor is held CONSTANT at its historical 12-month dollar
    figure for every package, not recomputed from a package-specific gross-
    import kWh, for two reasons, both checked against the committed
    generators before choosing this:
      1. fixed_daily is a per-day charge unrelated to consumption -- it is
         exactly the same dollar amount under every package by definition,
         no approximation involved.
      2. non_bypassable_gross depends on GROSS imported kWh, which COULD
         change under a package. data/battery_dispatch_policies.json reports
         each dispatch policy's kwh_served (energy the battery moved from
         grid timing to load), not a resulting annual gross-import kWh --
         and data/behavior_rebuild.json's EV-shift scenarios likewise report
         kwh_moved, not a post-shift gross-import total. Neither artifact
         gives a package-specific annual gross-import figure to recompute
         NBC from, so it cannot be sharpened further without a new run of
         the dispatch/behavior pipeline, which is outside this issue.
      Directionally: EV-shift (LOW) moves WHEN energy is drawn, not the
      annual total, so gross imports -- and NBC -- are essentially
      unaffected. A grid-charged battery (MID/HIGH) can only shift timing
      further, and the dispatch engine's own notes record round-trip
      efficiency at 0.9 (analysis/battery_dispatch_policies.json's
      "notes.rte") -- serving a kWh of load from a battery that was charged
      from the grid requires importing MORE raw kWh than serving that load
      directly, not less. Holding the floor constant is therefore likely a
      slight UNDERSTATEMENT of the true floor for MID/HIGH, not an
      overstatement -- the reported fractions below are, if anything,
      conservative floors on the true package-floor fractions."""
    packages = json.loads(PACKAGE_JSON.read_text())["packages"]
    out = {}
    for name, pkg in packages.items():
        bill = pkg["projected_bill_current_rates_yr"]
        if bill <= 0:
            raise SystemExit(f"package {name}: non-positive projected bill {bill}")
        out[name] = {
            "projected_bill_current_rates_yr": bill,
            "floor_usd_held_constant": floor["floor_usd"],
            "floor_fraction_of_projected_bill": round(floor["floor_usd"] / bill, 4),
            "method": "constant (see build_package_floor_fractions docstring)",
        }
    return out


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
    (c) The provision only binds in a year the household is a NET GENERATOR.
        Tested here against every period in the 12-month window: net_kwh
        (per data/bill_periods_electric.csv) is POSITIVE in every one of
        them -- the household was a net IMPORTER throughout, so the
        provision's own trigger condition never held in this data."""
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
    ever_net_generator = any(m["is_net_generator_this_period"] for m in monthly_net_position)

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
        "ever_net_generator_in_window": ever_net_generator,
        "provision_triggered_in_this_data": ever_net_generator,
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
    "This floor is a LOWER BOUND on what a purchase can save, not a forecast of the "
    "household's future bill: it names two charge components (a per-day fixed charge, "
    "and non-bypassable charges billed on gross imported kWh) that are billed "
    "regardless of net usage, and sums their ACTUAL historical dollar figures over the "
    "most recent 12 months of statements. It does not prove any package will actually "
    "reach this floor -- a package's projected bill can sit well above it. The package "
    "floor fractions hold the floor's dollar figure CONSTANT across LOW/MID/HIGH "
    "because no committed artifact reports a package-specific annual gross-import kWh "
    "to recompute the non-bypassable component from (see "
    "build_package_floor_fractions()'s docstring); this is likely conservative "
    "(understates the true floor) for a grid-charged battery, given round-trip "
    "losses, never the reverse. The minimum-bill-provision test is against the NEM "
    "true-up 'Minimum Charge Adjustment' concept printed on this household's own "
    "pre-2025-10-01 statements, not against a dollar figure specific to EV-TOU-5 -- "
    "none was found. Every dollar figure here is read from the PDFs this repository "
    "already has committed derivatives of (data/bill_periods_electric.csv, "
    "data/bill_tou_detail.csv) or re-derived directly from statement text; nothing "
    "here is modeled or projected forward at a different rate vintage."
)


def build():
    periods = load_periods()
    rows = classify_periods(periods)
    rows.sort(key=lambda r: (r["statement_date"], r["period"]))
    worst = worst_residual(rows)
    floor = build_floor(rows)
    package_fractions = build_package_floor_fractions(floor)
    all_statements = sorted({r["statement_date"] for r in rows})
    min_bill = build_minimum_bill_provision(rows, all_statements)
    nbc_check = build_nbc_gross_check(rows)

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
    print(f"  12-month floor: ${floor['floor_usd']:.2f} "
          f"({floor['floor_pct_of_total']}% of ${floor['total_current_charges_usd']:.2f})")
    for name, pf in sorted(result["package_floor_fractions"].items()):
        print(f"  {name}: floor is {pf['floor_fraction_of_projected_bill']*100:.1f}% "
              f"of the projected ${pf['projected_bill_current_rates_yr']}/yr bill")
    w = result["worst_residual_period"]
    print(f"  worst per-period cross-check residual: ${w['cross_check_diff_usd']:+.2f} "
          f"({w['statement_date']} / {w['period']})")
    print(f"  NBC-on-gross re-verification: {result['nbc_gross_reverification']['confirmation']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
