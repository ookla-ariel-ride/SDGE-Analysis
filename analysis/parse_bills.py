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
    private/household.yaml          REQUIRED. household.has_gas is read through the
                                    analysis/household.py loader, which fails closed
                                    (SystemExit pointing at the intake interview) when
                                    the file or the key is missing — parse_bills now
                                    requires the intake file like the other analysis
                                    scripts. The flag is the SINGLE authority on gas
                                    applicability (see envelope item 6).
    private/1-raw-data/electric-bills/sdge_electric_<statement-date>.pdf
    private/1-raw-data/gas-bills/sdge_gas_<statement-date>.pdf  (household.has_gas: true)
    Filenames carry the STATEMENT date. Periods are read from the PDF text, never
    inferred from the filename: one statement can contain two billing periods when a
    rate change splits it mid-cycle (CLAUDE.md §1 — parse periods, not files).
    private/1-raw-data/electric_billing_history_2024-2026.csv  OPTIONAL. SDG&E's own
                                    billing-history export. When it is staged it
                                    DEFINES the electric corpus boundary — see
                                    THE CORPUS BOUNDARY below.

OUTPUTS (committed, de-identified)
    data/bill_periods_electric.csv  one row per electric billing period
    data/bill_periods_gas.csv       one row per gas billing period. baseline_rate/
                                    nonbaseline_rate/gas_energy_charge_rate are each a
                                    DAY-WEIGHTED BLEND across however many rate segments
                                    the period has (1 or 2); other_fees_rate (Public
                                    Purpose Programs + State Regulatory Fee combined) is
                                    a THERM-WEIGHTED blend, since that charge type splits
                                    by therm count, not days (issue #98 Codex review) —
                                    see bill_gas_detail.csv below for the segment-level
                                    detail these collapse.
    data/bill_tou_detail.csv        long format: per period × section × season × TOU
                                    period → kWh and $/kWh as printed on the bill
    data/bill_gas_detail.csv        long format: per period × charge_type × segment →
                                    the segment's own day/therm count and $/therm
                                    rate(s), for "Gas Service" (baseline/nonbaseline
                                    tiers, day-based segments), "Gas Energy Charge"
                                    (flat, untiered, day-based segments), and
                                    "other_fees" (Public Purpose Programs + State
                                    Regulatory Fee combined, flat, untiered, THERM-based
                                    segments — issue #98 Codex review) rows. The three
                                    charge types have INDEPENDENT segment counts within
                                    the same period (a mid-cycle rate change in one does
                                    not always land in the same bill, or split into the
                                    same number of segments, as a mid-cycle change in
                                    another, even though Gas Service/Gas Energy Charge
                                    split on the identical day when both split, and
                                    Public Purpose Programs/State Regulatory Fee split
                                    into identical therm counts when both split) —
                                    collapsing them into bill_periods_gas.csv's single
                                    blended row would discard exactly what a true
                                    marginal-tier rebilling needs. Schema: statement_date,
                                    period, charge_type ("gas_service"|"gas_energy"|
                                    "other_fees"), segment (0-based), segment_days,
                                    segment_therms, baseline_rate, nonbaseline_rate,
                                    energy_rate, other_fees_rate — only the columns
                                    relevant to `charge_type` are populated, the rest are
                                    empty. A consumer reconstructs the true marginal rate
                                    for any therm by combining a period's gas_energy and
                                    other_fees segments (both flat, every therm) with its
                                    gas_service segments (tiered against
                                    baseline_allowance_therms) — see
                                    heat_pump_conversion.py's gas_savings_by_period() for
                                    the reference implementation.
    data/electric_bill_summary.csv  regenerated (same schema as the original)
    data/gas_bill_summary.csv       regenerated (same schema as the original)
    data/bill_corpus_boundary.json  where the electric corpus stops and why — the
                                    export that defines it, every statement parsed
                                    but not published, what would end each
                                    exclusion, and the resulting day coverage of
                                    the analysis window. Published in the SAME
                                    atomic set as the six artifacts above, because
                                    it describes exactly the corpus they contain;
                                    written on every run, with an empty
                                    excluded_statements list when the export covers
                                    the whole corpus — or, when no export was staged
                                    to derive the boundary from at all, an extra
                                    boundary_not_derived block saying so, so that the
                                    empty list is never read as an all-clear. It also
                                    carries a gas_corpus block, because the boundary
                                    governs ELECTRIC only: it names what governs the
                                    gas artifacts instead, records whether the
                                    SUMMARY_STATEMENTS_GAS presence check actually ran
                                    on this corpus (it does not on a fork's), and
                                    counts the published gas days that fall inside an
                                    excluded electric period. See THE CORPUS BOUNDARY.
    When household.has_gas is false the three gas artifacts (bill_periods_gas.csv,
    bill_gas_detail.csv, gas_bill_summary.csv) are still published — as HEADER-ONLY
    CSVs (same headers, zero rows), in the same atomic set as the electric ones, so a
    fork can never keep another corpus's stale gas data sitting next to its own fresh
    electric data.

THE CORPUS BOUNDARY — which statements get published, and why it is derived
    A statement PDF alone is not enough to publish. Everything downstream that
    reconciles the bills against SDG&E's own books (bill_decomposition.py's
    export_reconciliation(), which explains every statement's export
    current_charges line by line) needs a matching row in the billing-history
    export. So the reconcilable corpus is defined, not declared:

        a statement is INSIDE the corpus iff the billing-history export also
        carries a row for it.

    A statement whose PDF is on disk but which the export does not cover is
    outside the corpus and is not emitted into any artifact. That is a real,
    deliberate restriction of the published evidence, so it is made three ways
    visible rather than silent (CLAUDE.md §1: coverage is counted in DAYS, and a
    hidden gap once understated this household's annual baseline by ~9%):
      - main() prints the excluded statements, the reason, and the resulting
        day coverage of the analysis window;
      - data/bill_corpus_boundary.json records the same facts in the same atomic
        publish set as the artifacts it describes;
      - the day-coverage shortfall is stated as a NUMBER (days covered, days
        missing, the missing date ranges), computed against the analysis window
        read from data/behavior_rebuild.json — never implied.

    WHY DERIVED, NOT A DATE CONSTANT. The exclusion ends by itself: stage an
    export that covers the statement, re-run, and it is published with nobody
    remembering to delete an exclusion. WHICH export that is depends on the
    direction — see A LEADING EXCLUSION IS NOT A TRAILING ONE below; what is true
    either way is that nothing in this file or in the artifact has to be edited to
    end an exclusion. A hardcoded cutoff date would have to be found and
    removed by hand, and would go on silently truncating the corpus until someone
    did — the same rot a stale hand-maintained list has already produced twice in
    this repository.

    FAIL CLOSED ON THE OTHER DIRECTION. The asymmetry is deliberate. A PDF the
    export does not cover can be handled honestly by narrowing the published
    corpus. An export row with NO PDF cannot: it is positive evidence that a
    statement exists which this parser has never read, i.e. the corpus is missing
    a bill it would claim to reconcile. That raises SystemExit and publishes
    nothing. So does an export hole in the MIDDLE of the corpus (which would
    punch a gap into an otherwise contiguous published series) and an export that
    covers none of the parsed statements at all.

    A LEADING EXCLUSION IS NOT A TRAILING ONE. Both are absorbed by narrowing the
    corpus, but they end differently and the record says so per statement. A
    TRAILING exclusion (the PDF is newer than the export's last row) means the
    export has not caught up: re-pull it and the statement is published. A LEADING
    one (the PDF is older than the export's first row) is the opposite — SDG&E's
    billing-history export is a rolling window, so re-pulling it starts no earlier
    and may drop MORE statements off the front. Its remedy is an export whose date
    range reaches back to the statement, and telling the operator to "re-pull the
    export" there would be false. Both directions cost days off the analysis
    window, and _window_coverage() counts those days either way — missing_ranges
    shows which end they came off (issue #154).

    ELECTRIC ONLY, AND SAID SO. The export's rows are ELECTRIC statements. Gas
    statements are billed on their own cycle and carry their own dates, most of
    which appear in no row of it (18 of this corpus's 25 do not); the ones that do
    coincide are days a consolidated statement billed both fuels, which is not
    export corroboration of the gas bill. So the derivation above cannot be applied
    to gas — it would exclude gas statements on evidence that was never about
    them, deleting most of a corpus the export could never corroborate either way.
    The gas artifacts are
    therefore published unrestricted by this boundary — governed by _validate()'s
    duplicate/continuity/cross-foot checks, and by the SUMMARY_STATEMENTS_GAS
    presence check ONLY on a corpus that list documents — and the record's
    gas_corpus block states that, names what governs gas instead, and counts (in
    DAYS, per CLAUDE.md §1) the published gas days that fall inside an excluded
    electric period. A gas corpus wider than the electric one is then visible in
    the artifact rather than silent (issue #159).

    A FORK'S GAS CORPUS IS UNVERIFIED, AND SAID SO. That presence check is the one
    check here that can decline to run: a corpus sharing none of the pinned dates
    is a fork, and check 1 is skipped (see the comment on the lists). Gas has no
    export to corroborate it, so nothing then establishes that the gas corpus is
    COMPLETE — the remaining checks all pass on a corpus truncated off either end,
    because the periods that remain still tile. That run publishes, with the
    reason: refusing would refuse every fork on its first run, before it could
    possibly pin its own list. What it does not do is claim a guard that did not
    run. gas_corpus.summary_statements_presence_check records, from _validate()'s
    own report of the run, whether the check applied; when it did not, it says the
    gas corpus's completeness is unverified, why, and what makes the check apply.
    See _gas_corpus_scope() for the full reasoning on why this does not fail closed.

    NO EXPORT STAGED. The boundary is then underivable, so nothing is excluded:
    every parsed statement is published, the run says so, and
    data/bill_corpus_boundary.json records under boundary_not_derived that the
    published corpus was never tested against the export, plus what would end that.
    An empty excluded_statements list therefore never has to be read as "all fine".
    Mirrors bill_decomposition.py, which also treats a missing export as "cannot
    check" rather than "nothing to check".

    AN UNREADABLE EXPORT IS NOT A MISSING ONE. A file that is present but yields no
    statement_date value (truncated download, renamed column, layout drift) is an
    error, not a configuration: reading it as "no boundary is derivable" would let a
    broken export widen the published corpus to precisely the statements it cannot
    corroborate. That raises SystemExit and publishes nothing
    (export_statement_dates()).

APPLICABILITY ENVELOPE — what this parser assumes. Read this before pointing it at
any other account's bills; every assumption below is load-bearing in a regex.
    1. SDG&E CONSOLIDATED statements: electric (and optionally gas) on one account,
       with the "Detail of Current Charges" / "Total Electric Service" /
       "Total Gas Service" section layout SDG&E prints.
    2. NEM billing: every electric period must print BOTH a "Total Usage" (net) line
       and a "Non Bypassable Charges Usage" (gross) line. A non-NEM bill has no NBC
       usage line and fails closed.
    3. A 3-period TOU plan: "SUMMER USAGE" / "WINTER USAGE" tables anchored on an
       "On-Peak" header, with exactly three columns (On-Peak, Off-Peak,
       Super Off-Peak) in the "kWh used" and "Rate/kWh" rows. Flat-rate plans and
       plans with a different period count fail closed.
    4. CCA generation pages are OPTIONAL and recognized by name only: the literal
       strings "CLEAN ENERGY ALLIANCE" and the generic "CCA Electric Generation" /
       "Total CCA Electric Generation Charges" headings. Bundled (non-CCA) SDG&E
       generation is also handled. A different CCA whose pages omit the generic
       heading needs its printed name added to the detection regex.
    5. Statement-date file naming: sdge_electric_YYYY-MM-DD.pdf and
       sdge_gas_YYYY-MM-DD.pdf (the date is the STATEMENT date, not the period).
    6. Gas applicability comes from the household.has_gas flag in
       private/household.yaml (intake spec: gas fields are required_if has_gas) —
       NEVER from the presence of the gas-bills/ directory. A missing directory is
       indistinguishable from an incompletely staged or lost corpus, so directory
       presence is never inferred as (no-)gas service; the flag is the single
       authority. has_gas TRUE: gas-bills/ AND a parseable corpus are REQUIRED —
       a missing directory or zero statements is staging/corpus loss and fails
       closed. has_gas FALSE: a gas-bills/ directory must NOT exist (the
       contradiction fails closed), and the three gas artifacts are published as
       header-only CSVs in the same atomic set as the electric ones (see OUTPUTS).

    WHAT A FORK MUST CHANGE:
    - SUMMARY_STATEMENTS_ELEC / SUMMARY_STATEMENTS_GAS document THIS repository's
      corpus. A corpus sharing none of the listed dates is detected as a fork: the
      reproduction gate (check 1) is skipped with a notice, and the summary
      artifacts are built from every statement the fork actually parsed instead of
      being filtered to nothing. Replace the lists with your own statement dates
      once your corpus is stable (see the comment on the lists for the exact
      semantics).
    - If your bills are not SDG&E NEM 3-period-TOU consolidated statements, the
      extraction regexes in parse_electric()/parse_gas() must be rewritten for your
      layout — the fail-closed errors will name this docstring when they trip.
    - If your CCA is not Clean Energy Alliance, extend the CCA detection regex with
      the name printed inside your charge detail.
    - Rename your PDFs to the sdge_<fuel>_YYYY-MM-DD.pdf convention (or change
      _statement_date() and the glob patterns in main()).

PRIVACY (CLAUDE.md §4)
    The PDFs contain name, service address, account number, meter number, and the
    CCA service-delivery-point id. NONE of those are extracted. Outputs carry only
    dates, quantities, rates, and dollar amounts. How the household pays its bills
    (arrangements, schedules, balances owed) is private-tier and never emitted.

FAIL-CLOSED
    Every period must yield the required fields. A statement that parses to a missing
    period, day count, usage figure, or charge total raises SystemExit rather than
    emitting a zero — a silently-zeroed row would corrupt every downstream sum.

CHARGE-LINE CROSS-FOOT (issue #27, chosen over the other two options there)
    rates_history.py's holdout gate corroborates a printed rate_per_kwh line by
    checking whether OTHER statements' rate_per_kwh lines agree — so a parser
    regression that shifts every occurrence of one repeated historical rate by the
    same amount is invisible to it: the held-out line and both flanking witnesses
    would all carry the identical wrong value and agree with each other (rates_
    history.py's module docstring names this gap explicitly). That check cannot see
    the bug because it never leaves the rate_per_kwh column.
    This module closes that gap upstream, where the raw bill text is still available:
    every TOU block's "[N Days ]Charge $a + $b + $c = total" line prints SDG&E's own
    per-TOU-period dollar amount, INDEPENDENTLY of the Rate/kWh row two lines above it
    (different regex anchor, different printed line on the bill). Before a row is
    appended, kWh x the extracted rate_per_kwh is cross-footed against that printed
    charge (CHARGE_CROSSFOOT_TOLERANCE_USD, measured empirically at $0.01, against an
    observed corpus-wide maximum residual of $0.005 from cent rounding on each side).
    A rate misread — however many statements repeat it — fails this cross-foot on
    EVERY statement that prints it, independent of what any other statement says,
    because each one is checked against its own printed dollar figure rather than
    against a neighbor's rate. This is verified to actually catch the failure mode:
    test_parse_bills.py's case injects a uniform rate_per_kwh shift into every
    occurrence of one repeated vintage (the delivery/summer/on_peak $0.26438 rate
    printed on five 2024 statements) and confirms the parser now refuses, naming the
    file, period, cell and residual — the exact scenario rates_history.py's holdout
    gate is structurally unable to catch. No hand-transcribed rate is introduced
    anywhere in this check: both sides of the cross-foot are read off the same PDF
    the rate itself came from (issue #2 requirement 1 stands).
    The other two options considered (see issue #27) were: (1) a second independent
    extraction of rate_per_kwh via charge/kWh on the SAME line — rejected, because
    the charge-line cross-foot below is strictly more informative (it is a genuine
    THIRD number, not a re-derivation of the same two numbers already read), is
    already available with no new PDF anchor, and needs no committed-artifact schema
    change; (3) documenting the gap and doing nothing — rejected, because option 2
    turned out to be fully available on this corpus (all 26 statements, all 72 TOU
    blocks print a parseable charge line, verified against the raw PDFs) at low
    implementation cost, so accepting the blind spot would have been leaving real,
    cheap protection on the table.
"""
import contextlib
import csv
import datetime as dt
import fcntl
import json
import os
import shutil
import pathlib
import re
import sys

import pandas as pd
import pdfplumber

import household as hh  # gas applicability flag; fails closed without the intake yaml


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
# SDG&E's own billing-history export. Optional on disk; when present it DEFINES the
# electric corpus boundary (see THE CORPUS BOUNDARY in the module docstring). Same
# path bill_decomposition.py reads, so both scripts derive the boundary from one file
# rather than each declaring its own idea of where the corpus stops.
HISTORY_CSV = ROOT / "private" / "1-raw-data" / "electric_billing_history_2024-2026.csv"
# The analysis window every published figure is written against. Read from the
# committed artifact rather than restated here, so the day-coverage figures in
# bill_corpus_boundary.json move with the window instead of drifting behind it.
WINDOW_ARTIFACT = DATA / "behavior_rebuild.json"

# WHY THESE LISTS EXIST: the corpus on disk runs longer than the analysis year, so the
# two summary artifacts need a pinned window or they would silently widen every time
# another statement is downloaded — and the reproduction gate would stop being a
# byte-for-byte match against the committed originals. These dates ARE that window: the
# statements the original committed summaries covered. They serve two purposes, both in
# terms of the same overlap test: selecting the summary rows (_summary_frame) and
# asserting the statements are all present (_validate check 1).
#
# REPLACE-ME SEMANTICS (forks): these lists document THIS repository's corpus — they are
# an expectation about the statements on disk, not part of the parser. The corpus is
# compared against them three ways:
#   - full overlap            -> the gate runs as normal and the summaries are filtered
#                                to this window (this repo);
#   - PARTIAL overlap         -> fail closed: some documented statements are missing,
#                                which is real corpus loss, never a fork;
#   - ZERO overlap            -> the list is not-applicable (you are a fork running on
#                                your own statements); check 1 is skipped with a loud
#                                notice, and the summaries are built from every
#                                statement the fork parsed rather than filtered to
#                                nothing. Nothing then checks that your corpus is
#                                COMPLETE — the other checks all pass on a corpus
#                                missing statements off either end — so the run says
#                                so and bill_corpus_boundary.json records it for gas
#                                (gas_corpus.summary_statements_presence_check).
#                                Once your corpus is stable, replace these
#                                dates with your own statement dates so the gate starts
#                                protecting YOUR corpus and your summary window is
#                                pinned the same way.
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

# The charge-line cross-foot's tolerance (issue #27): kWh x printed Rate/kWh vs the
# statement's own printed per-TOU-period charge. Both sides are rounded to the cent
# independently before printing, so a clean statement can differ by up to half a
# cent; measured empirically across the full corpus (26 statements, 72 TOU blocks)
# the observed maximum residual is $0.005 exactly. Set with headroom above that
# measured ceiling, not guessed.
CHARGE_CROSSFOOT_TOLERANCE_USD = 0.01


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
                        f"may have changed — fix the parser rather than defaulting a "
                        f"value — or this statement is not an SDG&E NEM 3-period-TOU "
                        f"consolidated bill; see the applicability envelope in the "
                        f"module docstring.")
                return None
            return _f(hit.group(1))

        net = one(r"Total Usage:\s*(" + _NUM + r")", "net usage")
        gross = one(r"Non Bypassable Charges Usage:\s*(" + _NUM + r")", "gross (NBC) usage")
        delivery = one(r"Total Electric Service\s*\$(" + _NUM + r")", "Total Electric Service")
        bsc = one(r"Base Services Charge\s*\$[\d.]+\s*x\s*\d+\s*days\s*(" + _NUM + r")",
                  "Base Services Charge", required=False)
        # SDG&E replaced the flat "Monthly Service Fee" ($16.00/month) with a per-day
        # "Base Services Charge" ($0.79343/day) at the 2025-10-01 billing boundary
        # (CPUC Resolution E-5355). One statement in this corpus (2025-10-31) prints
        # BOTH labels because the tariff change lands mid-statement: the pre-10/1 stub
        # period carries "Monthly Service Fee" and the post-10/1 stub period carries
        # "Base Services Charge" for the first time. Kept as its own column rather
        # than merged into base_services_charge — they are different rate structures
        # (flat-per-month vs. per-day-prorated) and conflating them would misprice a
        # partial period. required=False mirrors base_services_charge: NaN on
        # statements that don't carry this label (post-transition periods), exactly
        # as base_services_charge is NaN on pre-transition periods.
        msf = one(r"Monthly Service Fee\s+(" + _NUM + r")",
                  "Monthly Service Fee", required=False)
        # The reconciled column any downstream consumer (e.g. an irreducible-bill
        # floor) should read instead of knowing about the tariff transition itself:
        # base_services_charge where present, else monthly_service_fee, else fail
        # closed — a period naming NEITHER label is a real gap (layout change, or a
        # tariff regime this parser has never seen) and must stop the run rather than
        # silently emit a zero or NaN floor. Mirrors the fallback order in
        # bill_decomposition.py (`lines.get("base_services_charge",
        # lines.get("monthly_service_fee"))`). The one shape that reference pattern
        # doesn't anticipate — BOTH printed on the same period — never occurs in this
        # corpus (the transition is a one-way, one-time swap), so the same "prefer
        # base_services_charge" tie-break is used deterministically here too, rather
        # than treating an occurrence the real bills never produce as a second
        # fail-closed reason.
        if bsc is not None:
            fixed_charge_total = bsc
        elif msf is not None:
            fixed_charge_total = msf
        else:
            raise SystemExit(
                f"{path.name} [{period}]: neither a 'Base Services Charge' nor a "
                f"'Monthly Service Fee' line was found for this period — the "
                f"fixed-charge floor cannot be computed. Fix the parser, or this "
                f"statement predates/postdates the tariff labels this parser knows "
                f"about; see the applicability envelope in the module docstring.")
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
            monthly_service_fee=msf,
            fixed_charge_total=fixed_charge_total,
        ))

        # TOU detail. Anchor on the "<SEASON> USAGE On-Peak ..." headers: the phrase
        # "kWh used" also appears in the usage graphic and the glossary boilerplate, so
        # scanning for it directly picks up rows that are not TOU tables. The PDF is
        # two-column, so extract_text() interleaves unrelated right-column charge lines
        # between a block's "kWh used" and "Rate/kWh" rows; both are therefore searched
        # within a bounded window after the header rather than on adjacent lines.
        #
        # A period can hold SEVERAL blocks for the same season: when a tariff change
        # lands mid-cycle the bill splits the period into rate segments ("3 Days Charge
        # ..." then "26 Days Charge ..."), each with its own $/kWh. Those segments are
        # the per-bill evidence of a rate vintage change, so they are kept as separate
        # rows and distinguished by `segment` (0-based, in bill order) plus the segment's
        # day count — collapsing them would discard exactly what a year-over-year price
        # comparison needs.
        gen_marker = re.search(r"Electricity Generation \(Details below\)", chunk)
        gen_at = gen_marker.start() if gen_marker else len(chunk)
        WINDOW = 600  # chars after a header within which its data rows appear
        seg_seen = {}

        for h in re.finditer(r"(SUMMER|WINTER) USAGE\s+On-Peak", chunk):
            win = chunk[h.end():h.end() + WINDOW]
            u = re.search(r"kWh used\s+(" + _NUM + r")\s+(" + _NUM + r")\s+(" + _NUM + r")", win)
            r_row = re.search(
                r"Rate/kWh\s+\$(" + _NUM + r")\s+\$(" + _NUM + r")\s+\$(" + _NUM + r")", win)
            if not u or not r_row:
                raise SystemExit(
                    f"{path.name} [{period}]: a '{h.group(1)} USAGE' header has no "
                    f"kWh/rate rows within {WINDOW} chars — bill layout changed, or "
                    f"this statement is not an SDG&E NEM 3-period-TOU consolidated "
                    f"bill; see the applicability envelope in the module docstring.")
            section = "generation" if h.start() >= gen_at else "delivery"
            season = h.group(1).lower()
            key = (section, season)
            segment = seg_seen.get(key, 0)
            seg_seen[key] = segment + 1
            # "[<N> Days ]Charge $a + $b + $c = total" follows the rate row: the day
            # count is absent on bills whose period is not split, where the segment
            # covers every day, but the three dollar amounts are always printed (26/26
            # statements, all 72 TOU blocks, verified against the raw corpus — issue
            # #27). $a/$b/$c are SDG&E's own on/off/super-off-peak dollar totals for
            # this segment, printed independently of the Rate/kWh row above (a
            # different number, on a different line) — see CROSS-FOOT below.
            c_row = re.search(
                r"(?:(\d+)\s*Days\s*)?Charge\s*\$(" + _NUM + r")\s*\+\s*\$(" + _NUM +
                r")\s*\+\s*\$(" + _NUM + r")\s*=\s*(" + _NUM + r")", win[r_row.end():])
            if not c_row:
                raise SystemExit(
                    f"{path.name} [{period}]: no '[N Days ]Charge $a + $b + $c = "
                    f"total' line after the {h.group(1)} USAGE Rate/kWh row — bill "
                    f"layout changed; see the applicability envelope in the module "
                    f"docstring.")
            seg_days = int(c_row.group(1)) if c_row.group(1) else days
            charges = [_f(c_row.group(i)) for i in (2, 3, 4)]
            printed_total = _f(c_row.group(5))
            if abs(sum(charges) - printed_total) > CHARGE_CROSSFOOT_TOLERANCE_USD:
                raise SystemExit(
                    f"{path.name} [{period}] {section}/{season} segment {segment}: "
                    f"printed per-period charges {charges} sum to "
                    f"${sum(charges):.2f}, but the statement's own printed total is "
                    f"${printed_total:.2f} — the charge line's columns are "
                    f"misaligned or misread; fix the parser, do not hand-correct.")
            for j, tp in enumerate(("on_peak", "off_peak", "super_off_peak")):
                kwh_j, rate_j = _f(u.group(1 + j)), _f(r_row.group(1 + j))
                # CROSS-FOOT (issue #27): kWh x the printed Rate/kWh must reproduce
                # the printed per-TOU-period charge, an in-bill arithmetic identity
                # independent of how rate_per_kwh itself was extracted — the charge
                # comes from a DIFFERENT regex anchored on a DIFFERENT printed line.
                # This is what closes rates_history.py's blind spot to a parser
                # regression that shifts every occurrence of one repeated historical
                # rate identically: a holdout gate built only from rate_per_kwh values
                # would see every witness agree (see rates_history.py's module
                # docstring, "THE HOLDOUT GATE'S BLIND SPOT"), but this check catches
                # the SAME misread on every single statement that prints it, because
                # each one is checked against its own independently-printed dollar
                # figure, not against another statement's rate.
                expect = kwh_j * rate_j
                resid = charges[j] - expect
                if abs(resid) > CHARGE_CROSSFOOT_TOLERANCE_USD:
                    raise SystemExit(
                        f"{path.name} [{period}] {section}/{season} segment "
                        f"{segment} {tp}: printed Rate/kWh ${rate_j:.5f} x kWh "
                        f"{kwh_j:g} = ${expect:.2f}, but the statement's own printed "
                        f"charge line says ${charges[j]:.2f} (residual "
                        f"${resid:+.4f}, tolerance "
                        f"${CHARGE_CROSSFOOT_TOLERANCE_USD:.3f}). The printed rate "
                        f"and the statement's own printed charge disagree — a parser "
                        f"regression on rate_per_kwh, or a misread charge line. Fix "
                        f"the parser; never hand-correct rate_per_kwh to make this "
                        f"pass (CLAUDE.md, issue #2 requirement 1).")
                tou.append(dict(
                    statement_date=stmt, period=period, section=section,
                    season=season, segment=segment, segment_days=seg_days,
                    tou_period=tp, kwh=kwh_j, rate_per_kwh=rate_j,
                ))
    return rows, tou


# ---------------------------------------------------------------------------
# Gas
# ---------------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Column schemas of the three gas artifacts — shared by the real writers and the
# header-only "retired" writers (household.has_gas false) so the two can never drift.
GAS_PERIOD_COLS = ["statement_date", "period", "period_end_month", "therms",
                   "total_gas_service", "billed_amount", "baseline_rate",
                   "nonbaseline_rate", "baseline_allowance_therms",
                   "gas_energy_charge_rate", "other_fees_rate"]
GAS_SUMMARY_COLS = ["file_month", "therms", "total_gas_service",
                    "baseline_rate", "nonbaseline_rate"]
# Long format (issue #98), mirroring bill_tou_detail.csv's precedent: one row per
# charge-type segment. "Gas Service" (tiered baseline/nonbaseline) and "Gas Energy
# Charge" (flat, untiered) have INDEPENDENT segment counts within the same period, so
# charge_type is the discriminator and only the columns relevant to it are populated —
# see the module docstring's OUTPUTS entry for the full schema rationale.
GAS_DETAIL_COLS = ["statement_date", "period", "charge_type", "segment",
                   "segment_days", "segment_therms", "baseline_rate",
                   "nonbaseline_rate", "energy_rate", "other_fees_rate"]

# Each gas charge-type segment is anchored on its own "<label> (Details below) N
# Therms" header line — printed once per rate segment, whether or not the period
# actually split (a single-segment period still prints exactly one). Two shapes
# follow the header, both matched by GAS_SEGMENT_RE below:
#   - single-column ("Baseline" only, or Gas Energy Charge's own only column):
#       Therms used 11
#       Rate/Therm $1.55659
#       Charge $17.12 = 17.12
#   - two-column (Gas Service only, once usage crosses the baseline allowance):
#       Therms used 6 9
#       Rate/Therm $1.56901 $1.87417
#       5 of 32 Days $9.41 +$16.87 = 26.28
# A segment that does NOT split covers the whole period and prints the literal word
# "Charge" with no day count; a split segment prints "N of M Days" (N = this segment's
# own days, M = the period's total days) instead of the word "Charge". Verified
# against the full 25-bill real corpus (private/1-raw-data/gas-bills/): every segment
# of both charge types matches one of these two shapes, with no unmatched blocks.
GAS_SERVICE_HEADER_RE = r"Gas Service \(Details below\)\s+(" + _NUM + r") Therms"
GAS_ENERGY_HEADER_RE = r"Gas Energy Charge \(Details below\)\s+(" + _NUM + r") Therms"
GAS_SEGMENT_RE = re.compile(
    r"Therms used\s+(" + _NUM + r")(?:\s+(" + _NUM + r"))?"
    r".*?Rate/Therm\s+\$(" + _NUM + r")(?:\s+\$(" + _NUM + r"))?"
    r".*?(?:(\d+)\s*of\s*\d+\s*Days|Charge)\s*\$(" + _NUM + r")(?:\s*\+\$(" + _NUM + r"))?"
    r"\s*=\s*(" + _NUM + r")", re.S)


def _gas_segments(txt, header_re, path, period, label, total_days, stop_at):
    """Return one dict per rate segment for a gas charge type ('Gas Service' or
    'Gas Energy Charge'), anchored on that type's own 'Details below' header.

    Each segment's own window is bounded by the NEXT header of the SAME type, or by
    `stop_at` (the 'Total Gas Charges' line's offset — printed once, after both
    charge types, in every statement in this corpus) for the final segment. A fixed
    WINDOW size (parse_electric's approach) is not used here because the two-column
    PDF layout can interleave a page break's boilerplate or the pie-chart's own text
    between a header and its data — an observed gap of up to ~1250 characters in this
    corpus, versus ~70-140 when nothing intervenes — so bounding by the next real
    anchor is the robust choice.
    """
    headers = list(re.finditer(header_re, txt))
    segs = []
    for i, h in enumerate(headers):
        stops = [p for p in (stop_at,) if p is not None and p > h.end()]
        if i + 1 < len(headers):
            stops.append(headers[i + 1].start())
        end = min(stops) if stops else len(txt)
        win = txt[h.end():end]
        m = GAS_SEGMENT_RE.search(win)
        if not m:
            raise SystemExit(
                f"{path.name} [{period}]: a '{label} (Details below)' header has no "
                f"Therms used/Rate/Therm/[Days ]Charge block in its window — bill "
                f"layout changed; see the applicability envelope in the module "
                f"docstring.")
        therms1 = _f(m.group(1))
        therms2 = _f(m.group(2)) if m.group(2) else None
        rate1 = _f(m.group(3))
        rate2 = _f(m.group(4)) if m.group(4) else None
        seg_days = int(m.group(5)) if m.group(5) else total_days
        charge1 = _f(m.group(6))
        charge2 = _f(m.group(7)) if m.group(7) else None
        printed_total = _f(m.group(8))

        # CROSS-FOOT, same reasoning as parse_electric's charge-line cross-foot
        # (issue #27): each segment's own printed charge is a THIRD number,
        # independent of how the rate was read, so a misread rate is caught
        # against the bill's own arithmetic rather than trusted on its own.
        # CHARGE_CROSSFOOT_TOLERANCE_USD is reused as-is: verified against the
        # full 25-bill gas corpus, the observed maximum residual is $0.005,
        # the identical ceiling measured for the electric TOU cross-foot.
        expect1 = therms1 * rate1
        if abs(expect1 - charge1) > CHARGE_CROSSFOOT_TOLERANCE_USD:
            raise SystemExit(
                f"{path.name} [{period}] {label} segment {i}: {therms1:g} therms x "
                f"${rate1:.5f} = ${expect1:.2f}, but the statement's own printed "
                f"charge says ${charge1:.2f} (residual ${expect1 - charge1:+.4f}) — "
                f"fix the parser, never hand-correct the rate.")
        if therms2 is not None:
            expect2 = therms2 * rate2
            if abs(expect2 - charge2) > CHARGE_CROSSFOOT_TOLERANCE_USD:
                raise SystemExit(
                    f"{path.name} [{period}] {label} segment {i} (non-baseline): "
                    f"{therms2:g} therms x ${rate2:.5f} = ${expect2:.2f}, but the "
                    f"statement's own printed charge says ${charge2:.2f} (residual "
                    f"${expect2 - charge2:+.4f}) — fix the parser, never hand-correct "
                    f"the rate.")
        printed_sum = charge1 + (charge2 or 0.0)
        if abs(printed_sum - printed_total) > CHARGE_CROSSFOOT_TOLERANCE_USD:
            raise SystemExit(
                f"{path.name} [{period}] {label} segment {i}: printed charges "
                f"{[charge1, charge2]} sum to ${printed_sum:.2f}, but the "
                f"statement's own printed total is ${printed_total:.2f}.")

        segs.append(dict(segment=i, segment_days=seg_days, rate1=rate1, rate2=rate2))
    return segs


def _day_weighted_blend(segs, field):
    """Day-weighted average of `field` (rate1/rate2) across segments that printed a
    value for it — segments that printed only a single (baseline / flat) column carry
    None for rate2, and are excluded from the rate2 blend rather than treated as a
    zero rate. This is the pragmatic, period-level convenience figure
    bill_periods_gas.csv publishes; bill_gas_detail.csv keeps every segment's own
    rate and day count for a consumer that needs true marginal-tier precision."""
    num = sum(s["segment_days"] * s[field] for s in segs if s[field] is not None)
    den = sum(s["segment_days"] for s in segs if s[field] is not None)
    return round(num / den, 5) if den else None


# Codex review, issue #98, pass 1: baseline_rate + nonbaseline_rate + energy_rate
# alone do NOT reproduce total_gas_service -- every real bill also levies "Public
# Purpose Programs" and a "State Regulatory Fee", two further FLAT, untiered
# $/therm charges (2025-12-30: $.115410/therm and $.002500/therm respectively, on
# every one of that period's 61 therms), printed one line each under "TAXES & FEES
# ON GAS CHARGES":
#   Public Purpose Programs 61 Therms x $.115410 7.04
#   State Regulatory Fee 61 Therms x $.002500 .15
# Omitting them understated the true marginal cost of a therm by their combined
# rate (observed $.116-$.156/therm across this corpus) -- exactly the OLD blended-
# average approach's one virtue (it WAS all-in) that the new true-marginal-tier
# engine had silently dropped.
#
# Unlike Gas Service/Gas Energy Charge, a mid-period rate change here splits by
# THERM COUNT, not by day: e.g. 2025-01-29 (97 total therms) prints "15 Therms x
# $.101330" then "82 Therms x $.115410" -- 15+82=97, the therms billed before/after
# the rate-change date, not a day fraction of the period. Verified against the
# full 25-bill corpus: Public Purpose Programs and State Regulatory Fee always
# split into the IDENTICAL therm counts when both split (2025-01-29: 15/82 for
# both; 2026-01-29: 11/61 for both), and each segment's own therms x rate
# reproduces its own printed dollar amount to the cent in every case -- so the two
# fees are combined into one per-segment "other_fees_rate" here (summed, since
# both are simple additive per-therm charges on the SAME therm segmentation), and
# blended by THERM COUNT (not days) across however many segments exist.
GAS_OTHER_FEE_RE = re.compile(
    r"(Public Purpose Programs|State Regulatory Fee)\s+(" + _NUM +
    r")\s+Therms x\s+\$(" + _NUM + r")\s+(" + _NUM + r")")


def _gas_other_fees(txt, path, period, total_therms):
    """Public Purpose Programs + State Regulatory Fee, one dict per therm-count
    segment (see the module-level comment above GAS_OTHER_FEE_RE for the format
    and the therm-based, not day-based, segmentation this charge type uses)."""
    by_label = {"Public Purpose Programs": [], "State Regulatory Fee": []}
    for m in GAS_OTHER_FEE_RE.finditer(txt):
        label, seg_therms, rate, amount = m.group(1), _f(m.group(2)), _f(m.group(3)), _f(m.group(4))
        expect = seg_therms * rate
        if abs(expect - amount) > CHARGE_CROSSFOOT_TOLERANCE_USD:
            raise SystemExit(
                f"{path.name} [{period}] {label}: {seg_therms:g} therms x ${rate:.6f} "
                f"= ${expect:.2f}, but the statement's own printed amount says "
                f"${amount:.2f} (residual ${expect - amount:+.4f}) — fix the parser, "
                f"never hand-correct the rate.")
        by_label[label].append((seg_therms, rate))
    for label, segs in by_label.items():
        if not segs:
            raise SystemExit(f"{path.name} [{period}]: no '{label}' line found — "
                              f"bill layout changed; see the applicability envelope "
                              f"in the module docstring.")
    pp_segs, sr_segs = by_label["Public Purpose Programs"], by_label["State Regulatory Fee"]
    if [t for t, _ in pp_segs] != [t for t, _ in sr_segs]:
        raise SystemExit(
            f"{path.name} [{period}]: Public Purpose Programs and State Regulatory "
            f"Fee split into different therm segments ({pp_segs} vs {sr_segs}) — "
            f"the two fees are assumed to share identical segmentation whenever "
            f"this corpus splits either; verified true on all 25 real bills, but "
            f"not true here — the combined other_fees_rate below would be wrong.")
    total_seg_therms = sum(t for t, _ in pp_segs)
    if abs(total_seg_therms - total_therms) > 1e-6:
        raise SystemExit(
            f"{path.name} [{period}]: Public Purpose Programs/State Regulatory Fee "
            f"segments sum to {total_seg_therms:g} therms, not the period's own "
            f"{total_therms:g} billed therms — a segment is missing or double-counted.")
    return [dict(segment=i, segment_therms=t, rate=pp_rate + sr_rate)
            for i, ((t, pp_rate), (_, sr_rate)) in enumerate(zip(pp_segs, sr_segs))]


def _therm_weighted_blend(segs):
    """Therm-weighted average other_fees rate across segments -- Public Purpose
    Programs/State Regulatory Fee split by THERM COUNT, not days (see
    _gas_other_fees's own docstring), so the natural weight is each segment's own
    therm count, not segment_days."""
    num = sum(s["segment_therms"] * s["rate"] for s in segs)
    den = sum(s["segment_therms"] for s in segs)
    return round(num / den, 5) if den else None


def parse_gas(path):
    txt = _text(path)
    stmt = _statement_date(path)

    m = re.search(r"Gas\s+([A-Z][a-z]{2} \d{1,2}, \d{4})\s*-\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s+"
                  r"(" + _NUM + r")\s+Therms\s+(" + _NUM + r")", txt)
    if not m:
        raise SystemExit(f"{path.name}: could not find the gas billing-period summary line")
    start_s, end_s, therms, amount = m.group(1), m.group(2), _f(m.group(3)), _f(m.group(4))
    period = f"{start_s} - {end_s}"
    total_days = (pd.to_datetime(end_s, format="%b %d, %Y")
                  - pd.to_datetime(start_s, format="%b %d, %Y")).days + 1

    tot = re.search(r"Total Gas Service\s*\$(" + _NUM + r")", txt)
    if not tot:
        raise SystemExit(f"{path.name}: could not find 'Total Gas Service'")
    total_service = _f(tot.group(1))

    # Baseline Allowance (issue #98): a single period-level constant, not segmented
    # (printed exactly once per bill). Anchored on the "Rate: GR-Residential Baseline
    # Allowance:" phrasing specifically — "Baseline Allowance" also appears, with no
    # number, in the glossary boilerplate on the bill's definitions page, and a bare
    # "Baseline Allowance:" anchor would need extra work to reject that false match.
    ba = re.search(r"Rate: GR-Residential Baseline Allowance:\s+(" + _NUM + r") Therms", txt)
    if not ba:
        raise SystemExit(
            f"{path.name}: could not find 'Rate: GR-Residential Baseline Allowance:' — "
            f"bill layout changed; see the applicability envelope in the module "
            f"docstring.")
    baseline_allowance = _f(ba.group(1))

    # Gas Service (baseline/nonbaseline tiers) and Gas Energy Charge (flat, untiered)
    # segments (issue #98): each charge type is anchored and windowed independently
    # (see _gas_segments) because their segment counts are independent within one
    # period — a mid-cycle Gas Service rate change and a mid-cycle Gas Energy Charge
    # rate change do not always both occur, or split the same number of ways, even
    # though when BOTH occur in the same bill they land on the identical day.
    stop_at = None
    tgc = re.search(r"Total Gas Charges", txt)
    if tgc:
        stop_at = tgc.start()
    gs_segs = _gas_segments(txt, GAS_SERVICE_HEADER_RE, path, period,
                            "Gas Service", total_days, stop_at)
    ge_segs = _gas_segments(txt, GAS_ENERGY_HEADER_RE, path, period,
                            "Gas Energy Charge", total_days, stop_at)
    if not gs_segs:
        raise SystemExit(f"{path.name} [{period}]: no 'Gas Service (Details below)' "
                          f"segments found")
    if not ge_segs:
        raise SystemExit(f"{path.name} [{period}]: no 'Gas Energy Charge (Details "
                          f"below)' segments found")

    # bill_periods_gas.csv's single-row-per-period convenience figures: a day-weighted
    # blend across however many segments each charge type has (fixes the pre-existing
    # bug where a single global regex only ever matched the FIRST Gas Service segment,
    # or matched nothing at all on a single-tier, single-$-value period — issue #98).
    baseline = _day_weighted_blend(gs_segs, "rate1")
    nonbaseline = _day_weighted_blend(gs_segs, "rate2")
    energy_rate = _day_weighted_blend(ge_segs, "rate1")

    # Public Purpose Programs + State Regulatory Fee (Codex review, issue #98, pass
    # 1): a further flat, untiered $/therm charge total_gas_service includes that
    # baseline_rate/nonbaseline_rate/energy_rate alone do not reproduce -- see
    # _gas_other_fees's own module-level comment.
    of_segs = _gas_other_fees(txt, path, period, therms)
    other_fees_rate = _therm_weighted_blend(of_segs)

    detail_rows = [
        dict(statement_date=stmt, period=period, charge_type="gas_service",
             segment=s["segment"], segment_days=s["segment_days"], segment_therms=None,
             baseline_rate=s["rate1"], nonbaseline_rate=s["rate2"], energy_rate=None,
             other_fees_rate=None)
        for s in gs_segs
    ] + [
        dict(statement_date=stmt, period=period, charge_type="gas_energy",
             segment=s["segment"], segment_days=s["segment_days"], segment_therms=None,
             baseline_rate=None, nonbaseline_rate=None, energy_rate=s["rate1"],
             other_fees_rate=None)
        for s in ge_segs
    ] + [
        dict(statement_date=stmt, period=period, charge_type="other_fees",
             segment=s["segment"], segment_days=None, segment_therms=s["segment_therms"],
             baseline_rate=None, nonbaseline_rate=None, energy_rate=None,
             other_fees_rate=s["rate"])
        for s in of_segs
    ]

    mm, dd, yyyy = re.match(r"([A-Za-z]{3})\w* (\d{1,2}), (\d{4})", end_s).groups()
    file_month = f"{mm.lower()} {yyyy}"

    period_row = dict(
        statement_date=stmt, period=period,
        period_end_month=file_month, therms=therms,
        total_gas_service=total_service, billed_amount=amount,
        baseline_rate=baseline, nonbaseline_rate=nonbaseline,
        baseline_allowance_therms=baseline_allowance,
        gas_energy_charge_rate=energy_rate,
        other_fees_rate=other_fees_rate,
    )
    return period_row, detail_rows


# ---------------------------------------------------------------------------
# The corpus boundary — derived from the billing-history export, never declared
# ---------------------------------------------------------------------------
def export_statement_dates(path=None):
    """The statement dates SDG&E's billing-history export covers.

    None means "no boundary is derivable here", and is reserved for ONE situation:
    no export is staged at that path. That is a legitimate configuration — a fork
    with bill PDFs and no export must still be able to publish (see NO EXPORT
    STAGED in the module docstring) — and it is recorded as such, not silently.

    An export that EXISTS but yields no statement_date value is a different thing
    entirely: a truncated download, a renamed or reordered column, SDG&E layout
    drift. It cannot mean "this export legitimately covers nothing" (an export with
    rows has dates), so collapsing it into the None case would take a BROKEN export
    and publish every parsed statement as though nothing needed checking — widening
    the corpus to exactly the statements that cannot be corroborated. It raises
    instead, before anything is written. bill_decomposition.py draws the same split
    on the same file."""
    path = path or HISTORY_CSV
    if not path.exists():
        return None
    with open(path, newline="") as fh:
        rdr = csv.DictReader(fh)
        rows = list(rdr)
        columns = rdr.fieldnames or []
    seen = {(r.get("statement_date") or "").strip() for r in rows}
    seen.discard("")
    if not seen:
        raise SystemExit(
            f"{path.name} is staged but carries no statement_date value: "
            f"{len(rows)} data row(s), columns [{', '.join(columns) or 'none'}]. An "
            f"export that exists but cannot be read is not the same as no export at "
            f"all: treating it as 'the boundary is not derivable' would publish every "
            f"parsed statement as if SDG&E's own books had corroborated it, which is "
            f"the one claim this file exists to support. Re-pull the billing-history "
            f"export (DATA-SOURCES-CHEATSHEET.md §D) so it carries a statement_date "
            f"column with dates in it — or, if there genuinely is no export for this "
            f"account, remove {path.name} entirely: a missing export is a documented "
            f"configuration, an unreadable one is not.")
    return seen


def _elec_period_bounds(period):
    """('5/25/24 - 6/25/24') -> (date(2024, 5, 25), date(2024, 6, 25)), inclusive."""
    s, e = period.split(" - ")
    return (dt.datetime.strptime(s.strip(), "%m/%d/%y").date(),
            dt.datetime.strptime(e.strip(), "%m/%d/%y").date())


def _gas_period_bounds(period):
    """('May 25, 2024 - Jun 25, 2024') -> (date(2024, 5, 25), date(2024, 6, 25)).

    Gas periods print in a different format from electric ones on the same
    statement layout, which is why _validate() carries a per-fuel format string."""
    s, e = period.split(" - ")
    return (dt.datetime.strptime(s.strip(), "%b %d, %Y").date(),
            dt.datetime.strptime(e.strip(), "%b %d, %Y").date())


def _excluded_detail(elec, statement_dates, kept):
    """One record per statement being left outside the corpus, carrying everything a
    reader needs to judge the exclusion: which periods it would have contributed,
    how many billed days those cover, the calendar span, why it is out, and the one
    thing that would put it back in.

    The remedy DEPENDS ON THE DIRECTION (issue #154), so `kept` — the statements the
    export does cover — is needed to tell them apart. A statement newer than the
    export's last row is waiting for the export to catch up, and re-pulling it is
    the whole remedy. A statement OLDER than its first row is the opposite case: the
    export is a rolling window that has aged past this statement, so a re-pull starts
    no earlier and can drop more statements off the front. Writing the trailing
    remedy on a leading exclusion would send the operator to do the one thing that
    cannot work, so each direction states what is true of it. An interior hole never
    reaches here — _restrict_to_reconcilable_corpus() has already refused it."""
    out = []
    first = kept[0]             # anything not older than this is a trailing exclusion
    for stmt in sorted(statement_dates):
        grp = elec[elec.statement_date == stmt]
        periods = list(grp.period)
        spans = [_elec_period_bounds(p) for p in periods]
        leading = stmt < first
        if leading:
            reason = (
                f"{HISTORY_CSV.name} carries no row for this statement: it is older "
                f"than the export's earliest row ({first}), so the billing-history "
                f"export reconciliation cannot cover it; publishing it would claim "
                f"coverage SDG&E's own export does not corroborate")
            ends_when = (
                "a billing-history export that reaches BACK to this statement is "
                "staged. Re-pulling the export is NOT the remedy in this direction: "
                f"it is a rolling window, so a fresh pull starts no earlier than "
                f"{first} and may start later still, dropping more statements off the "
                f"front. Widen the export's date range to include this statement (or "
                f"keep the earlier pull that did). The boundary is re-derived from "
                f"whatever export is staged on every parse_bills.py run — no cutoff "
                f"date is stored anywhere, so nothing has to be edited or remembered "
                f"when an export that reaches back arrives")
        else:
            reason = (
                f"{HISTORY_CSV.name} carries no row for this statement, so the "
                f"billing-history export reconciliation cannot cover it; publishing "
                f"it would claim coverage SDG&E's own export does not corroborate")
            ends_when = (
                "the billing-history export is re-pulled so that it covers this "
                "statement. The boundary is re-derived from the export on every "
                "parse_bills.py run — no cutoff date is stored anywhere, so nothing "
                "has to be edited or remembered when the export catches up")
        out.append({
            "statement_date": stmt,
            "periods": periods,
            "billed_days": int(grp.days.sum()),
            "period_span": [min(s for s, _ in spans).isoformat(),
                            max(e for _, e in spans).isoformat()],
            "reason": reason,
            "exclusion_ends_when": ends_when,
        })
    return out


def _restrict_to_reconcilable_corpus(elec, tou):
    """Drop the electric statements the billing-history export does not cover.

    Returns (elec, tou, export_info, excluded_records). See THE CORPUS BOUNDARY in
    the module docstring for why the rule is derived from the export rather than
    written down as a date, and why the opposite direction (an export row with no
    PDF) is fatal instead of absorbed.

    Runs BEFORE _validate() on purpose: the published frame is what has to tile its
    window with no gap or overlap, so an exclusion that would break continuity is
    caught by the same gate a missing PDF is. The interior-hole check below fires
    first only so the operator gets the true diagnosis (an export hole) instead of
    check 3's "a statement is missing from the corpus"."""
    export = export_statement_dates()
    parsed = sorted(set(elec.statement_date))
    if export is None:
        print(f"NOTICE: no billing-history export at {HISTORY_CSV} — the corpus "
              f"boundary is not derivable, so nothing is excluded and all "
              f"{len(parsed)} parsed statement(s) are published. Stage the export "
              f"to have the published corpus checked against SDG&E's own books.")
        return elec, tou, None, []

    kept = [s for s in parsed if s in export]
    excluded = [s for s in parsed if s not in export]
    # Zero overlap first, and with its own diagnosis: it is the shape the
    # SUMMARY_STATEMENTS_* lists already recognise as "this file documents someone
    # else's corpus". The orphan check below would fire on it too, but would send the
    # operator off to download 1990's statements instead of looking at the export.
    if not kept:
        raise SystemExit(
            f"{HISTORY_CSV.name} covers none of the {len(parsed)} statement(s) parsed "
            f"from {ELEC_DIR} (export: {min(export)} .. {max(export)}; corpus: "
            f"{parsed[0]} .. {parsed[-1]}). The export documents a different account "
            f"or a different corpus — restricting to the reconcilable statements would "
            f"publish nothing at all. Stage the matching export, or remove it.")

    orphan = sorted(export - set(parsed))
    if orphan:
        raise SystemExit(
            f"{len(orphan)} statement(s) appear in {HISTORY_CSV.name} but no PDF was "
            f"parsed for them: {orphan}. That is the dangerous direction of the "
            f"corpus boundary: the export is positive evidence that these statements "
            f"exist, so the corpus is missing a bill it would claim to reconcile — "
            f"totals would be understated with nothing to reveal it. Download the "
            f"missing statement(s) into {ELEC_DIR} (DATA-SOURCES-CHEATSHEET.md §D). "
            f"This is never absorbed by narrowing the corpus, unlike a PDF the export "
            f"does not cover.")

    interior = [s for s in excluded if kept[0] < s < kept[-1]]
    if interior:
        raise SystemExit(
            f"{HISTORY_CSV.name} has a hole in the MIDDLE of the corpus: it covers "
            f"{kept[0]} .. {kept[-1]} but carries no row for {interior}, whose PDFs "
            f"are present. Excluding those statements would punch a gap into the "
            f"published series (annual totals understated); publishing them would "
            f"claim export coverage they do not have. Re-pull the export so it covers "
            f"the whole corpus — this one cannot be resolved by narrowing it.")

    records = _excluded_detail(elec, excluded, kept)
    export_info = {
        "path": str(HISTORY_CSV.relative_to(ROOT)),
        "statements": len(export),
        "span": [min(export), max(export)],
    }
    if excluded:
        elec = elec[elec.statement_date.isin(kept)].copy()
        tou = tou[tou.statement_date.isin(kept)].copy()
    return elec, tou, export_info, records


def _analysis_window():
    """(start_date, end_date) of the analysis window, from the committed
    behavior_rebuild.json. None when that artifact is absent or does not carry a
    window — parse_bills runs UPSTREAM of it, so a fresh fork that has not built it
    yet must still be able to publish its bills; the boundary artifact then records
    window_coverage as null with the reason, rather than inventing a window."""
    if not WINDOW_ARTIFACT.exists():
        return None
    try:
        w = json.loads(WINDOW_ARTIFACT.read_text())["window"]
        return (dt.date.fromisoformat(str(w["start"])[:10]),
                dt.date.fromisoformat(str(w["end"])[:10]))
    except (ValueError, TypeError, KeyError):
        return None


def _contiguous_ranges(days):
    """[date, ...] -> [[first, last], ...] over each contiguous run."""
    out = []
    for d in sorted(days):
        if out and d - dt.date.fromisoformat(out[-1][1]) == dt.timedelta(days=1):
            out[-1][1] = d.isoformat()
        else:
            out.append([d.isoformat(), d.isoformat()])
    return out


def _window_coverage(elec, excluded):
    """How much of the analysis window the PUBLISHED corpus actually covers, in days.

    CLAUDE.md §1: coverage is counted in days, never in files — "12 months of bills"
    once meant 338 days with a hidden 27-day hole. _validate()'s continuity check has
    already proved the published periods tile without gap or overlap, so the corpus
    covers exactly [first period start .. last period end]; the shortfall against the
    window is that interval's complement inside it.

    THE `window` KEY IS PROVENANCE, NOT DECORATION (issue #155). It is the window this
    block was computed against, recorded VERBATIM from window_source's own artifact
    (dates only). parse_bills.py cannot run without the private bill corpus, so it
    cannot run in CI — but every figure here is a pure function of two COMMITTED,
    de-identified files (that window and bill_periods_electric.csv) plus the excluded
    statements recorded alongside. So recording the window makes the staleness that
    nothing else could catch — behavior_rebuild.json regenerated onto a new window
    and committed while this artifact still reports day coverage against the retired
    one — a recomputable mismatch between two committed files. Keep this key equal to
    the source artifact's window: test_parse_bills.py's
    case_committed_boundary_is_current_with_the_committed_window recomputes the whole
    block from those files and is the CI gate on it."""
    win = _analysis_window()
    if win is None:
        return None
    w_start, w_end = win
    bounds = [_elec_period_bounds(p) for p in elec.period]
    c_start, c_end = min(s for s, _ in bounds), max(e for _, e in bounds)
    window_days = [w_start + dt.timedelta(days=i)
                   for i in range((w_end - w_start).days + 1)]
    covered = [d for d in window_days if c_start <= d <= c_end]
    missing = [d for d in window_days if not (c_start <= d <= c_end)]
    supplied = set()
    for rec in excluded:
        a, b = (dt.date.fromisoformat(x) for x in rec["period_span"])
        supplied |= {d for d in missing if a <= d <= b}
    return {
        "window": [w_start.isoformat(), w_end.isoformat()],
        "window_source": f"data/{WINDOW_ARTIFACT.name}:window",
        "window_days": len(window_days),
        "days_covered": len(covered),
        "days_missing": len(missing),
        "missing_ranges": _contiguous_ranges(missing),
        "days_the_excluded_statements_would_supply": len(supplied),
    }


def _gas_corpus_scope(gas, excluded, presence):
    """What governs the GAS artifacts, and by how many days they can disagree with
    the electric corpus (issue #159).

    The boundary above restricts electric only, and it cannot honestly do otherwise:
    the export's rows are ELECTRIC statements, and gas bills on its own cycle under
    its own dates, so applying the same rule to gas would exclude gas statements on
    evidence that was never about them — deleting most of a corpus the export could
    not corroborate either way. What is NOT acceptable is leaving that asymmetry
    unstated: a reader comparing bill_periods_gas.csv with bill_periods_electric.csv
    would otherwise find gas covering billed days the electric corpus deliberately
    excludes, with nothing anywhere saying why. So this block names what governs gas
    instead, and counts the overlap in DAYS (CLAUDE.md §1 — a coverage claim is a
    number or it is not a claim). Zero on a corpus whose gas periods end before the
    first excluded electric period, which is the only reason this repository has
    never seen it.

    `presence` is _validate()'s own report of what its check 1 did for gas on THIS
    run (None when no gas corpus was validated), and the reason it is threaded in
    rather than restated here is the defect this argument fixes. This block used to
    say, unconditionally, that the gas artifacts are governed by "the
    SUMMARY_STATEMENTS_GAS presence check". That is true of this repository, whose
    statement dates ARE the pinned list, and false of the audience this file is
    written for: a fork's corpus shares none of those dates, so _validate() prints
    its fork notice and skips check 1 entirely. The fork's artifact then claimed a
    completeness guard that never ran — and nothing else in _validate() can catch a
    gas corpus truncated at either END, since the periods that remain still tile.

    WHY THIS DOES NOT FAIL CLOSED. The skipped state is, by construction, every
    fork's first run: the pinned list can only be written from the statement dates a
    successful run reports, so refusing to publish until the list matches would
    refuse the correct caller before it could possibly satisfy the demand. A guard
    that refuses correct callers is one that gets switched off. Nor is a skip
    evidence of a defect — it is evidence of an UNKNOWN, and this file already has a
    settled way of handling exactly that: `boundary_not_derived` records "the export
    was never staged, so nothing was checked" instead of refusing to run. Same shape,
    same treatment. What the skip owes is not a refusal but a record: the operator
    gets the NOTICE _validate() prints (with what the skip costs), and the committed
    artifact gets summary_statements_presence_check below, so "not verified" can
    never be read as "verified, nothing wrong". The remedy is real and stated in
    both: replace SUMMARY_STATEMENTS_GAS with your own statement dates.

    ELECTRIC needs no equivalent field, and the asymmetry is not an oversight. The
    electric corpus's completeness is established POSITIVELY and independently of
    check 1: every published statement has a billing-history export row, and an
    export row with no PDF fails the run closed — and on the one run where that
    cannot happen, no export staged, `boundary_not_derived` already records it.
    Electric's completeness state is therefore already in the record in both states.
    Gas has no export to corroborate it, so in the skipped state it has nothing at
    all — which is precisely why this is where the field is owed."""
    if gas is not None and presence is None:
        # Fail closed rather than fall through to the "no gas corpus was parsed"
        # wording, which would be false — and false in exactly the direction this
        # whole block exists to prevent: a record describing a check state that is
        # not the run's. The only legitimate None is a no-gas household.
        raise SystemExit(
            "_gas_corpus_scope: a gas corpus was parsed but nothing was passed for "
            "what _validate()'s check 1 did with it. Pass _validate()'s return "
            "value — the record must state the run's own check state, never a "
            "default.")
    if presence is None:
        checked = {
            "list": "SUMMARY_STATEMENTS_GAS",
            "applied": None,
            "what_it_means": (
                "no gas corpus was parsed on this run, so _validate()'s check 1 had "
                "no gas statements to test and neither ran nor was skipped"),
        }
    elif presence["applied"]:
        # WHAT THE CHECK COVERS IS THE STATEMENTS THE LIST NAMES — not a date window.
        # Check 1 requires every LISTED date to be present; it never requires a
        # present date to be listed, so a published statement the list does not name
        # is one the check did not look at. Saying "the gas corpus is complete" would
        # be an over-claim in miniature, so those statements are counted (CLAUDE.md
        # §1) and named as what the check does not speak for.
        #
        # THE COUNT IS A SET DIFFERENCE, AND THE FIELD IS NAMED FOR THAT. It used to
        # be called ..._outside_that_window and explained as statements falling past
        # the list's end, which is true of THIS corpus (all of them do) and false of
        # the fork audience this file is written for: a gas statement sitting INSIDE
        # the list's span and simply absent from the list is equally unchecked and
        # lands in this same count. Computing it against the list's min/max span
        # instead would have made the old name true at the cost of dropping those
        # statements from the count — publishing a narrower "unchecked" figure than
        # the check actually leaves, i.e. the over-claim this field exists to
        # prevent. So the count stays the honest one and the name follows it.
        unnamed = sorted(set(gas.statement_date) - set(SUMMARY_STATEMENTS_GAS)) \
            if gas is not None and not gas.empty else []
        checked = {
            "list": presence["list"],
            "applied": True,
            "statements_listed": presence["statements_listed"],
            "statements_listed_and_present": presence["statements_listed_and_present"],
            "statements_published_the_list_does_not_name": len(unnamed),
            "what_it_means": (
                f"_validate()'s check 1 ran on this corpus: all "
                f"{presence['statements_listed']} gas statement date(s) "
                f"{presence['list']} documents are present, so the published gas "
                f"corpus is complete over the statements that list names — one "
                f"missing from it would have failed the run closed rather than "
                f"publishing a shorter corpus. The check speaks for those statements "
                f"and no others: the {len(unnamed)} published gas statement(s) the "
                f"list does not name are covered by no completeness check, whether "
                f"they fall past its last date (the list is also what pins the "
                f"summary window, and that deliberately stops at the analysis year) "
                f"or sit inside its span and are simply absent from it"),
        }
    else:
        checked = {
            "list": presence["list"],
            "applied": False,
            "statements_listed": presence["statements_listed"],
            "statements_listed_and_present": presence["statements_listed_and_present"],
            "what_it_means": (
                f"SKIPPED on this run. The gas corpus on disk shares none of the "
                f"{presence['statements_listed']} statement date(s) {presence['list']} "
                f"documents, so _validate() treated the list as another household's "
                f"and skipped reproduction-gate check 1 for gas (the run prints a "
                f"NOTICE saying so). Nothing on this run compared the gas statements "
                f"on disk against any documented list, so the COMPLETENESS of the "
                f"published gas corpus is UNVERIFIED: the checks that did run — no "
                f"duplicate periods, periods tiling with no gap or overlap, charge "
                f"cross-foot — all pass on a corpus missing statements off either "
                f"END, because the periods that remain still tile. The gas figures in "
                f"these artifacts are therefore the totals of the statements that "
                f"were staged, which may be fewer than the statements that exist"),
            "check_is_applied_when": (
                f"{presence['list']} at the top of parse_bills.py is replaced with "
                f"this household's own gas statement dates and the parser is re-run. "
                f"Check 1 then protects this corpus and this block records it as "
                f"applied — nothing else has to be edited or remembered"),
        }
    # ONE prose site, with the only clause that varies computed from what the run
    # did. Writing the applied and skipped sentences out separately would put the
    # same paragraph in two places that can drift apart — the reason `rule` above
    # carries only what is true in both directions.
    if checked["applied"]:
        tail = (
            "governed by parse_bills.py's _validate() checks (no duplicate periods, "
            "periods tiling with no gap or overlap, charge cross-foot) and by the "
            "SUMMARY_STATEMENTS_GAS presence check, which ran on this corpus, not by "
            "this boundary")
    else:
        tail = (
            "governed on this run by parse_bills.py's _validate() checks (no "
            "duplicate periods, periods tiling with no gap or overlap, charge "
            "cross-foot) ALONE — not by this boundary, and not by the "
            "SUMMARY_STATEMENTS_GAS presence check, which did not run on this "
            "corpus. See summary_statements_presence_check below for what that "
            "leaves unverified")
    governs = (
        f"nothing in this record. {HISTORY_CSV.name} is the ELECTRIC billing-history "
        f"export: its rows are electric statements, and gas is billed on its own "
        f"cycle under its own statement dates, so a gas date that coincides with a "
        f"row is a consolidated statement billing both fuels that day, not export "
        f"corroboration of the gas bill. The rule above therefore cannot be applied "
        f"to the gas corpus without excluding gas statements on evidence that was "
        f"never about them. The gas artifacts publish "
        f"every gas statement parsed, {tail}")
    if gas is None or gas.empty:
        return {
            "restricted_by_this_boundary": False,
            "what_governs_it_instead": (
                "no gas corpus is published in this set (household.has_gas is false, "
                "so the three gas artifacts are header-only), so there is no gas "
                "coverage for this boundary to disagree with"
                if gas is None else governs),
            "summary_statements_presence_check": checked,
            "statements_published": 0,
            "published_period_span": None,
            "days_published_inside_an_excluded_electric_period": 0,
            "periods_inside_an_excluded_electric_period": [],
        }
    bounds = {(r.statement_date, r.period): _gas_period_bounds(r.period)
              for r in gas.itertuples()}
    overlaps, days = [], set()
    for rec in excluded:
        a, b = (dt.date.fromisoformat(x) for x in rec["period_span"])
        for (stmt, period), (g_start, g_end) in sorted(bounds.items()):
            lo, hi = max(a, g_start), min(b, g_end)
            if lo > hi:
                continue
            overlaps.append({
                "gas_statement_date": stmt,
                "gas_period": period,
                "excluded_electric_statement_date": rec["statement_date"],
                "days": (hi - lo).days + 1,
                "span": [lo.isoformat(), hi.isoformat()],
            })
            days |= {lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)}
    return {
        "restricted_by_this_boundary": False,
        "what_governs_it_instead": governs,
        "summary_statements_presence_check": checked,
        "statements_published": int(gas.statement_date.nunique()),
        "published_period_span": [
            min(s for s, _ in bounds.values()).isoformat(),
            max(e for _, e in bounds.values()).isoformat()],
        "days_published_inside_an_excluded_electric_period": len(days),
        "periods_inside_an_excluded_electric_period": overlaps,
    }


def _boundary_record(elec, export_info, excluded, gas, presence):
    """The committed statement of where the corpus stops and why.

    `gas` and `presence` CARRY NO DEFAULTS, and that is the guard, not an oversight.
    _gas_corpus_scope() below fails closed on (gas set, presence unset) because a
    record must state the run's own check state and never a default — but a default
    of None on `gas` opened the hole underneath that guard: a caller that simply
    forgot the argument reached the no-gas branch, and the published artifact then
    asserted "no gas corpus is published in this set (household.has_gas is false)" —
    a positive, false claim about a run that parsed a gas corpus, which is exactly
    the failure the SystemExit beside it exists to prevent. An omitted argument is
    not evidence of a no-gas household; only a caller that passed gas=None is. So
    both are required and the distinction is one the caller has to make.

    Written on EVERY run, including the runs that exclude nothing: it is the corpus
    boundary, not an exception log. An empty excluded_statements list is the positive
    evidence that the export covered everything on that run — which is also what makes
    the boundary self-healing and visible in one diff: the next run with an export that
    covers the statement publishes it, and the diff of this file says so.

    The `rule` field states only what holds in BOTH directions. The remedy is
    direction-dependent (issue #154) and lives per statement, in
    excluded_statements[].exclusion_ends_when — see the comment on `rule` below.

    UNLESS THERE WAS NO EXPORT TO DERIVE IT FROM, in which case an empty
    excluded_statements list is evidence of nothing at all: no statement was tested.
    That run adds a boundary_not_derived block saying so — where the export was looked
    for, what the consequence is for the published corpus, and what ends it — because
    a reader of this artifact must never be able to mistake "not checked" for
    "checked, nothing excluded". A console notice does not survive into the committed
    evidence; this does. The key exists ONLY on those runs, so a repository with an
    export staged serialises exactly as it did before."""
    published = sorted(set(elec.statement_date))
    bounds = [_elec_period_bounds(p) for p in elec.period]
    record = {
        "generated_by": "analysis/parse_bills.py",
        # DIRECTION-NEUTRAL ON PURPOSE. What ends an exclusion depends on which end
        # of the export the statement falls off, and it is stated ONCE — per
        # statement, in excluded_statements[].exclusion_ends_when (_excluded_detail).
        # Restating either branch here would put the remedy in two prose sites that
        # can drift apart, and the trailing branch stated unconditionally is false of
        # a leading exclusion: it would send the operator to re-pull a rolling window,
        # which starts no earlier and can drop more statements off the front. So this
        # field carries only what is true in BOTH directions — the boundary is
        # re-derived every run, nothing here is ever edited to end an exclusion — and
        # names the field that carries the direction-dependent part.
        "rule": (
            "A statement is inside the reconcilable electric bill corpus if and only "
            "if SDG&E's billing-history export also carries a row for it. Statements "
            "with a PDF but no export row are listed under excluded_statements and "
            "appear in no artifact. The boundary is derived from the export on every "
            "run — no cutoff date is stored — so nothing here is ever edited to end an "
            "exclusion: stage an export that covers the statement and the next run "
            "publishes it. WHICH export that is depends on which end of the export's "
            "range the statement falls outside, and is stated per statement in "
            "excluded_statements[].exclusion_ends_when. An export row with no PDF is "
            "the opposite case and fails the run closed instead."),
        "export": export_info,
        "statements_parsed": len(published) + len(excluded),
        "statements_published": len(published),
        "published_statement_span": [published[0], published[-1]],
        "published_period_span": [min(s for s, _ in bounds).isoformat(),
                                  max(e for _, e in bounds).isoformat()],
        "excluded_statements": excluded,
        "window_coverage": _window_coverage(elec, excluded),
        # The boundary is electric-only, so it says so and says what governs gas —
        # see _gas_corpus_scope() and THE CORPUS BOUNDARY, "ELECTRIC ONLY".
        # `presence` is _validate()'s report of what its check 1 actually did for
        # gas on this run; the block records that rather than asserting it.
        "gas_corpus": _gas_corpus_scope(gas, excluded,
                                        (presence or {}).get("gas")),
    }
    if export_info is None:
        record["boundary_not_derived"] = {
            "export_looked_for": str(HISTORY_CSV.relative_to(ROOT)),
            "reason": (
                "no billing-history export is staged at that path, so the rule above "
                "had nothing to derive the corpus boundary from on this run; the "
                "empty excluded_statements list records that nothing WAS excluded, "
                "not that nothing needed to be"),
            "consequence": (
                f"every one of the {len(published)} parsed statement(s) was published "
                f"unchecked: the published corpus is NOT corroborated by SDG&E's "
                f"billing-history export, and neither a statement it does not cover "
                f"nor one it covers with no PDF here could be detected"),
            "boundary_is_derived_when": (
                "the billing-history export is staged at that path and parse_bills.py "
                "is re-run (DATA-SOURCES-CHEATSHEET.md §D). The boundary is derived "
                "from the export on every run — no cutoff date is stored anywhere, so "
                "nothing has to be edited or remembered when the export arrives"),
        }
    return record


def _announce_boundary(record):
    """Say out loud what was left out. A silent restriction is the failure CLAUDE.md
    §1 is written about, so the run reports the statements, the reason, the remedy and
    the day-coverage shortfall whether or not anyone reads the artifact.

    A run with no export staged excluded nothing, but it also CHECKED nothing, so it
    gets its own announcement rather than silence — the same facts the artifact's
    boundary_not_derived block carries."""
    excluded = record["excluded_statements"]
    exp = record["export"]
    if exp is None:
        nd = record["boundary_not_derived"]
        print(f"corpus boundary: NOT DERIVED — {nd['reason']}.")
        print(f"  consequence: {nd['consequence']}.")
        print(f"  ends when:   {nd['boundary_is_derived_when']}.")
        print(f"  recorded in data/bill_corpus_boundary.json")
        return
    print(f"corpus boundary: {record['statements_published']} of "
          f"{record['statements_parsed']} parsed statement(s) published; the "
          f"reconcilable corpus is what {exp['path']} covers "
          f"({exp['statements']} statement(s), {exp['span'][0]} .. {exp['span'][1]}).")
    for rec in excluded:
        print(f"  NOTICE: EXCLUDED {rec['statement_date']} — period(s) "
              f"{', '.join(rec['periods'])}, {rec['billed_days']} billed day(s), "
              f"{rec['period_span'][0]} .. {rec['period_span'][1]}. "
              f"It is parsed from its PDF but published in no artifact.")
        print(f"    why:      {rec['reason']}.")
        print(f"    ends when: {rec['exclusion_ends_when']}.")
    gasc = record["gas_corpus"]
    if gasc["days_published_inside_an_excluded_electric_period"]:
        names = ", ".join(sorted({o["gas_statement_date"] for o in
                                  gasc["periods_inside_an_excluded_electric_period"]}))
        print(f"  NOTICE: GAS IS WIDER — this boundary restricts the ELECTRIC corpus "
              f"only, and the gas artifacts publish "
              f"{gasc['days_published_inside_an_excluded_electric_period']} billed "
              f"day(s) that fall inside an excluded electric period (gas statement(s) "
              f"{names}). {gasc['what_governs_it_instead']}.")
    cov = record["window_coverage"]
    if cov is None:
        print(f"    day coverage against the analysis window: not stated — "
              f"data/{WINDOW_ARTIFACT.name} carries no readable window.")
        return
    gaps = ", ".join(f"{a} .. {b}" for a, b in cov["missing_ranges"]) or "none"
    print(f"  day coverage: the published corpus covers {cov['days_covered']} of the "
          f"analysis window's {cov['window_days']} days ({cov['window'][0]} .. "
          f"{cov['window'][1]}); {cov['days_missing']} day(s) missing: {gaps}. "
          f"The excluded statement(s) would supply "
          f"{cov['days_the_excluded_statements_would_supply']} of them.")
    print(f"  recorded in data/bill_corpus_boundary.json")


# ---------------------------------------------------------------------------
# Corpus-level validation and transactional publication
# ---------------------------------------------------------------------------
def _validate(elec, gas, tou, gas_detail=None):
    """Refuse to publish anything unless the whole parsed corpus is sound.

    Parsing every PDF that happens to be present is not enough: a statement that is
    absent, misnamed, or unreadable would simply not appear, and the artifacts would be
    rewritten a few periods short with no error. Every check below runs BEFORE any file
    is touched.

    `gas` and `gas_detail` may both be None: a no-gas household (household.has_gas
    false — see the applicability envelope in the module docstring). All gas checks are
    then skipped.

    RETURNS what check 1 actually did, per fuel — {label: {"list", "applied",
    "statements_listed", "statements_listed_and_present"}}. Check 1 is the only check
    here that can decline to run (the fork skip below), so it is the only one whose
    outcome is not implied by "this function returned". _boundary_record() writes that
    outcome into data/bill_corpus_boundary.json rather than restating it as a constant,
    because the record has to say what happened on THIS run: the alternative is a
    committed artifact claiming a guard that never ran, which is the failure this
    return value exists to prevent."""
    fuels = [("electric", elec, SUMMARY_STATEMENTS_ELEC,
              "SUMMARY_STATEMENTS_ELEC", "%m/%d/%y")]
    if gas is not None:
        fuels.append(("gas", gas, SUMMARY_STATEMENTS_GAS,
                      "SUMMARY_STATEMENTS_GAS", "%b %d, %Y"))

    # 1. Every statement the committed summaries are built from must be present. This is
    #    what stops a thinned corpus from quietly truncating the published evidence.
    #    Fork detection (see the comment on the SUMMARY_STATEMENTS_* lists): if the
    #    corpus shares NONE of the listed dates, the list documents someone else's
    #    corpus — skip this check with a notice instead of demanding statements the
    #    fork can never have. Any PARTIAL overlap still fails closed: that is this
    #    corpus with statements missing, i.e. real corpus loss.
    presence = {}
    for label, df, want_list, list_name, _fmt in fuels:
        have, want = set(df.statement_date), set(want_list)
        presence[label] = {
            "list": list_name,
            "applied": bool(have & want),
            "statements_listed": len(want),
            "statements_listed_and_present": len(have & want),
        }
        if not have & want:
            # The skip's COST is stated with the skip. "Check 1 was skipped" tells an
            # operator nothing actionable on its own; what it costs is that no check
            # left in this function can see a corpus truncated at either END, because
            # what remains still tiles. Stated about THIS validation only — whether
            # that leaves the fuel's completeness unverified depends on evidence
            # _validate() does not hold: electric also has the billing-history export
            # behind it, gas has nothing. That fuel-specific verdict belongs to the
            # boundary record, which knows about the export, and is written there.
            # WHERE TO LOOK IS FUEL-SPECIFIC, and this notice is printed for BOTH
            # fuels. Gas has a field for exactly this question — the boundary
            # record's summary_statements_presence_check — precisely because a
            # skipped check leaves the gas corpus with nothing else behind it.
            # Electric has no such field and deliberately needs none (see
            # _gas_corpus_scope's docstring): its completeness rests on the
            # billing-history export instead. So an electric reader sent to that
            # block would open the file, find no electric equivalent, and be left
            # worse off than before — a dead end, not an omission. Each fuel is sent
            # to the evidence that actually answers it.
            where = ("data/bill_corpus_boundary.json's gas_corpus."
                     "summary_statements_presence_check records, for this run, that "
                     "the check did not apply and what that leaves unverified"
                     if label == "gas" else
                     "the electric corpus is corroborated by the billing-history "
                     "export instead: data/bill_corpus_boundary.json's export and "
                     "excluded_statements say which statements it covers, and its "
                     "boundary_not_derived block appears when no export was staged")
            print(f"NOTICE: none of the {len(want)} statement dates in {list_name} are "
                  f"present in the {label} corpus on disk — treating the list as "
                  f"not-applicable (a FORK running on its own statements) and skipping "
                  f"reproduction-gate check 1 for {label}. Nothing else in this "
                  f"validation compares the {label} statements on disk against a "
                  f"documented list: the remaining checks all pass on a corpus missing "
                  f"statements off either end, because what is left still tiles "
                  f"({where}). "
                  f"Once your corpus is stable, "
                  f"replace the SUMMARY_STATEMENTS_* lists at the top of parse_bills.py "
                  f"with your own statement dates so the gate protects your corpus.")
            continue
        missing = sorted(want - have)
        if missing:
            raise SystemExit(
                f"{label}: {len(missing)} statement(s) required by the committed summary "
                f"are missing from the corpus: {missing}. Restore the PDFs before "
                f"regenerating — writing now would publish a truncated artifact.")

    # 2. Duplicate billing periods would double-count a year (CLAUDE.md §1).
    for label, df, *_ in fuels:
        dupes = df.period[df.period.duplicated()].tolist()
        if dupes:
            raise SystemExit(f"duplicate {label} billing periods parsed: {dupes}")

    # 3. Periods must tile their window with no gap, for BOTH fuels. A statement missing
    #    from the middle of the corpus passes check 1 whenever it falls outside the
    #    summary window, so continuity is the only thing that catches it — and the two
    #    fuels bill on different cycles, so each needs its own check. (Electric periods
    #    print as m/d/yy, gas as "Jun 26, 2024".)
    #    "Tile" means exactly one day between a period's inclusive end and the next
    #    period's start: any more is a missing statement (totals understated), any less
    #    is an overlap that would double-count days, kWh/therms and dollars. Checking
    #    only for gaps would let an overlap through, and overlapping period STRINGS are
    #    distinct so the duplicate check above does not see them either.
    for label, df, _w, _n, fmt in fuels:
        d = df.copy()
        d["start"] = pd.to_datetime(d.period.str.split(" - ").str[0], format=fmt)
        d["end"] = pd.to_datetime(d.period.str.split(" - ").str[1], format=fmt)
        d = d.sort_values("start")
        adjacent = list(zip(d.period[:-1], d.period[1:],
                            (d.start.shift(-1) - d.end).dt.days[:-1]))
        gaps = [(p, q, int(n)) for p, q, n in adjacent if n > 1]
        if gaps:
            raise SystemExit(
                f"gap between consecutive {label} billing periods: "
                f"{[(p, q, f'{n - 1} day(s) missing') for p, q, n in gaps]}. A statement "
                f"is missing from the corpus; annual totals would be understated.")
        overlaps = [(p, q, int(n)) for p, q, n in adjacent if n < 1]
        if overlaps:
            raise SystemExit(
                f"overlapping {label} billing periods: "
                f"{[(p, q, f'{1 - n} day(s) counted twice') for p, q, n in overlaps]}. "
                f"Days, usage and charges would be double-counted.")

    # 4. Every period needs its TOU detail. Requiring rows per period (not merely
    #    erroring when a season header fails to parse) is what catches a layout change
    #    that stops the headers matching altogether — that would otherwise yield an
    #    empty, silently incomplete bill_tou_detail.csv.
    if tou.empty:
        raise SystemExit(
            "no TOU detail parsed for any period — bill layout changed, or these "
            "statements are not SDG&E NEM 3-period-TOU consolidated bills; see the "
            "applicability envelope in the module docstring.")
    have_tou = set(tou.period)
    missing_tou = sorted(set(elec.period) - have_tou)
    if missing_tou:
        raise SystemExit(
            f"{len(missing_tou)} billing period(s) produced no TOU rows: {missing_tou}. "
            f"The season headers stopped matching — fix the parser.")
    key_cols = ["period", "section", "season", "segment", "tou_period"]
    dup_keys = tou[tou.duplicated(key_cols)]
    if not dup_keys.empty:
        raise SystemExit(
            f"duplicate TOU keys parsed ({'/'.join(key_cols)}): "
            f"{dup_keys[key_cols].to_dict('records')}")

    # Every period must carry delivery detail; generation detail too whenever the bill
    # prints a generation section (it does for both bundled and CCA periods here).
    for period, grp in tou.groupby("period"):
        if "delivery" not in set(grp.section):
            raise SystemExit(f"[{period}]: TOU rows parsed but none for delivery")

    # 5. Delivery TOU kWh must reconcile against the period's net usage — an independent
    #    read of the same bill, so a mis-scoped regex shows up here rather than shipping.
    d = tou[tou.section == "delivery"].groupby("period").kwh.sum()
    for period, net in elec.set_index("period").net_kwh.items():
        if period not in d:
            raise SystemExit(f"[{period}]: no delivery TOU rows parsed")
        if abs(d[period] - net) > 1.0:
            raise SystemExit(
                f"[{period}]: delivery TOU kWh {d[period]:,.0f} does not reconcile with "
                f"net usage {net:,.0f} — the TOU blocks and the usage total disagree.")

    # 6. bill_gas_detail.csv (issue #98): every period needs all THREE charge types'
    #    segments, no duplicate (period, charge_type, segment) keys. gas_service/
    #    gas_energy's own segment days must sum to exactly the period's calendar
    #    days — the same "tile with no gap or overlap" property check 3 enforces
    #    across periods, applied within a period across its own rate segments.
    #    other_fees splits by THERM COUNT, not days (see _gas_other_fees's own
    #    docstring), so its own segments are checked against the period's billed
    #    therms instead.
    if gas_detail is not None:
        if gas_detail.empty:
            raise SystemExit(
                "no gas charge-segment detail parsed for any period — bill layout "
                "changed; see the applicability envelope in the module docstring.")
        dup_keys = ["period", "charge_type", "segment"]
        dup = gas_detail[gas_detail.duplicated(dup_keys)]
        if not dup.empty:
            raise SystemExit(
                f"duplicate gas detail keys parsed ({'/'.join(dup_keys)}): "
                f"{dup[dup_keys].to_dict('records')}")
        period_days = {}
        period_therms = dict(zip(gas.period, gas.therms))
        for period in gas.period:
            s, e = period.split(" - ")
            sd = pd.to_datetime(s, format="%b %d, %Y")
            ed = pd.to_datetime(e, format="%b %d, %Y")
            period_days[period] = (ed - sd).days + 1
        day_based = gas_detail[gas_detail.charge_type.isin(["gas_service", "gas_energy"])]
        for (period, charge_type), grp in day_based.groupby(["period", "charge_type"]):
            total = int(grp.segment_days.sum())
            want = period_days[period]
            if total != want:
                raise SystemExit(
                    f"[{period}] {charge_type}: segment days sum to {total}, but the "
                    f"period covers {want} calendar days — a segment's day count is "
                    f"missing or misread.")
        other_fees = gas_detail[gas_detail.charge_type == "other_fees"]
        for period, grp in other_fees.groupby("period"):
            total = float(grp.segment_therms.sum())
            want = period_therms[period]
            if abs(total - want) > 1e-6:
                raise SystemExit(
                    f"[{period}] other_fees: segment therms sum to {total:g}, but "
                    f"the period billed {want:g} therms — a segment's therm count "
                    f"is missing or misread.")
        have_types = gas_detail.groupby("period").charge_type.apply(set)
        for period in gas.period:
            missing_types = {"gas_service", "gas_energy", "other_fees"} - have_types.get(period, set())
            if missing_types:
                raise SystemExit(
                    f"[{period}]: no {sorted(missing_types)} segments parsed into "
                    f"bill_gas_detail.csv.")

    return presence


def _summary_frame(df, want_list, list_name, label):
    """Select the statements the summary artifact for `label` is built from.

    Normally that is the pinned window the SUMMARY_STATEMENTS_* list names (see the
    comment on the lists): this repo's corpus is longer than its analysis year, and
    the summary has to keep reproducing the committed original byte for byte.

    A FORK's corpus shares NONE of those dates — the same zero-overlap test
    _validate() uses to skip reproduction-gate check 1. Filtering it by another
    household's statement dates would select nothing and publish a header-only
    summary, throwing away the billing summary the fork just parsed. So the fork's
    window is every statement it actually parsed for this fuel: the same frame the
    periods artifact is built from. That is the honest choice — it is exactly what
    the list selects for the corpus the list was written for — and it holds only
    until the fork replaces the list with its own dates, at which point the summary
    narrows to the fork's chosen window and check 1 starts protecting it.

    Partial overlap never reaches here: _validate() fails closed on it first,
    because that is real corpus loss rather than a fork.
    """
    have = set(df.statement_date)
    if have & set(want_list):
        return df[df.statement_date.isin(want_list)]
    print(f"NOTICE: the {label} summary window is the FULL parsed corpus — "
          f"{len(have)} statement(s), {min(have)} .. {max(have)} — because "
          f"{list_name} shares no dates with it and therefore documents another "
          f"corpus. Replace {list_name} in parse_bills.py with your own statement "
          f"dates to pin your summary window.")
    return df


@contextlib.contextmanager
def _publication_lock(directory):
    """Serialize publication across processes.

    Without this the leftover-backup check below is an unguarded check-then-act: two
    runs could both pass it, then fight over the shared staging and backup files —
    one consuming or deleting the other's, which can leave a half-published set with
    no recovery copy. The lock is held across the whole publish/rollback/cleanup
    sequence, not just the check.

    flock is used because the kernel releases it automatically if the process dies, so
    a crashed run cannot leave a lock nobody can clear. (POSIX only; this pipeline
    targets macOS and Linux.)"""
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".parse_bills.lock"
    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(
                f"another parse_bills run is publishing to {directory} (lock held on "
                f"{lock_path.name}). Publication is serialized so concurrent runs cannot "
                f"corrupt each other's staging and backup files — wait for it to finish, "
                f"then re-run.")
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def _write_all_atomically(writes):
    """Publish an artifact SET so it advances together or not at all.

    Runs under an exclusive lock (see _publication_lock) and stages through paths that
    carry this process's pid, so a second run can neither race the leftover-backup check
    nor consume or delete files this one is using.

    Each file is staged to a .tmp, then swapped in. If any swap fails, the files already
    swapped are restored from their backups.

    The backups are the only copy of the previous evidence while a rollback is in
    progress, so they are deleted ONLY once the set is known to be consistent: either
    every file published, or every file restored. If a restore itself fails, the
    surviving .bak files are deliberately left on disk and the operator is told exactly
    which artifacts are stale and where their previous contents are — deleting them
    would turn a partial publication into unrecoverable evidence loss.

    Because those .bak files ARE the only remaining copy, this function refuses to run
    at all while any of them exists: re-running would back the (stale) artifact up over
    its own recovery copy and destroy the previous evidence for good. Recovery is a
    deliberate manual step, not something a retry should silently paper over."""
    if not writes:
        return
    with _publication_lock(writes[0][0].parent):
        _publish_locked(writes)


def _publish_locked(writes):
    """The body of _write_all_atomically; assumes the publication lock is held."""
    leftover = [path.with_name(path.name + ".bak") for path, _ in writes
                if path.with_name(path.name + ".bak").exists()]
    if leftover:
        raise SystemExit(
            "refusing to publish: a previous run failed to roll back and left recovery "
            f"backups in place ({', '.join(b.name for b in leftover)}). Those .bak files "
            "hold the only copy of the previous artifacts — re-running now would "
            "overwrite them with the stale ones. Restore each .bak over its artifact "
            "(or delete the .bak if the current artifact is already correct), then "
            "re-run.")

    staged, backups, done = [], {}, []
    try:
        for path, writer in writes:
            # pid-qualified so two runs can never share a staging file
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            writer(tmp)
            staged.append((path, tmp))
        for path, tmp in staged:
            if path.exists():
                bak = path.with_name(path.name + ".bak")
                shutil.copy2(path, bak)
                backups[path] = bak
            os.replace(tmp, path)
            done.append(path)
    except BaseException:
        # Roll every published file back INDEPENDENTLY: one failure must not abort the
        # remaining restores, or files nobody tried to restore would stay overwritten.
        unrestored = []
        for path in done:
            bak = backups.get(path)
            try:
                if bak is None:
                    path.unlink()          # file did not exist before this run
                else:
                    os.replace(bak, path)  # consumes the .bak
                    backups.pop(path, None)
            except OSError as exc:
                unrestored.append((path, bak, exc))
        for _, tmp in staged:              # best-effort cleanup of unswapped temps
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        if unrestored:
            detail = "; ".join(
                f"{p.name} is STALE, previous contents in {b.name if b else '(none)'} ({e})"
                for p, b, e in unrestored)
            raise SystemExit(
                "publication failed AND rollback could not fully restore the previous "
                f"artifacts. Backups have been LEFT IN PLACE for manual recovery: {detail}. "
                "Restore each .bak over its artifact by hand, then re-run.")
        for bak in backups.values():       # rollback complete; backups now redundant
            if bak.exists():
                bak.unlink()
        raise
    for bak in backups.values():           # published cleanly; drop the backups
        if bak.exists():
            bak.unlink()


def main():
    # Gas applicability is decided by the household.has_gas intake flag, NEVER by
    # directory presence: a missing gas-bills/ is indistinguishable from an
    # incompletely staged or lost corpus (see the applicability envelope). The
    # loader fails closed without private/household.yaml — intended: parse_bills
    # requires the intake file like the other analysis scripts.
    has_gas = hh.get("household.has_gas")
    if not isinstance(has_gas, bool):
        raise SystemExit(
            f"household.has_gas in private/household.yaml must be true or false "
            f"(got {has_gas!r}) — it is the single authority on gas applicability; "
            f"see the intake interview in DATA-SOURCES-CHEATSHEET.md.")

    if not ELEC_DIR.is_dir():
        raise SystemExit(
            f"missing {ELEC_DIR} — download the statements first "
            f"(DATA-SOURCES-CHEATSHEET.md §D describes the bulk-download method)")

    gas_pdfs = None
    if has_gas:
        if not GAS_DIR.is_dir():
            raise SystemExit(
                f"household.has_gas is true but {GAS_DIR} does not exist — that is "
                f"staging loss, not a no-gas household. Restore/download the gas "
                f"statements (DATA-SOURCES-CHEATSHEET.md §D), or set "
                f"household.has_gas: false in private/household.yaml if this "
                f"household truly has no gas service.")
        gas_pdfs = sorted(GAS_DIR.glob("sdge_gas_*.pdf"))
        if not gas_pdfs:
            raise SystemExit(
                f"household.has_gas is true but {GAS_DIR} contains no sdge_gas_*.pdf "
                f"statements — that is corpus loss, not a no-gas household. Restore "
                f"the gas PDFs before regenerating, or set household.has_gas: false "
                f"in private/household.yaml if this household truly has no gas "
                f"service.")
    else:
        if GAS_DIR.exists():
            raise SystemExit(
                f"household.has_gas is false but {GAS_DIR} exists — contradiction. "
                f"If this household HAS gas service, set household.has_gas: true in "
                f"private/household.yaml; if it does not, remove the gas-bills "
                f"directory. The flag is the single authority — directory presence "
                f"is never inferred as gas service.")
        print("NOTICE: household.has_gas is false — gas artifacts retired to "
              "header-only CSVs in this publish set; any committed copies carried "
              "over from another household's corpus are replaced, never left in "
              "place next to fresh electric data.")

    elec_rows, tou_rows = [], []
    for f in sorted(ELEC_DIR.glob("sdge_electric_*.pdf")):
        r, t = parse_electric(f)
        elec_rows.extend(r)
        tou_rows.extend(t)
    if not elec_rows:
        raise SystemExit("no electric statements parsed — is the corpus staged?")

    gas = None
    gas_detail = None
    if has_gas:
        gas_rows, gas_detail_rows = [], []
        for f in gas_pdfs:
            period_row, detail_rows = parse_gas(f)
            gas_rows.append(period_row)
            gas_detail_rows.extend(detail_rows)
        gas = pd.DataFrame(gas_rows).sort_values("statement_date")[GAS_PERIOD_COLS]
        gas_detail = pd.DataFrame(gas_detail_rows)[GAS_DETAIL_COLS]
        # segment_days is only ever populated for gas_service/gas_energy rows and
        # segment_therms only for other_fees rows, so each column mixes real integer
        # counts with nulls from the OTHER charge types' rows -- pandas silently
        # upcasts a column with any null to float64, reformatting every real day/
        # therm count as "4.0" instead of "4" in the published CSV. Cast to the
        # nullable Int64 dtype (blank stays blank, real counts stay integer-
        # formatted) -- issue #98, Codex review; this bill corpus never prints a
        # fractional day or therm count for these fields.
        gas_detail["segment_days"] = gas_detail["segment_days"].astype("Int64")
        gas_detail["segment_therms"] = gas_detail["segment_therms"].astype("Int64")

    elec = pd.DataFrame(elec_rows)
    # Sort chronologically by the period's start date. Sorting on the period STRING
    # would put "10/1/25 - 10/27/25" before "9/26/25 - 9/30/25".
    elec["_start"] = pd.to_datetime(elec.period.str.split(" - ").str[0], format="%m/%d/%y")
    elec = elec.sort_values("_start").drop(columns="_start")
    tou = pd.DataFrame(tou_rows)

    # The corpus boundary comes first: _validate()'s continuity and presence checks
    # must run on the frame that is actually published, not on a wider one that
    # happens to tile. See THE CORPUS BOUNDARY in the module docstring.
    elec, tou, export_info, excluded = _restrict_to_reconcilable_corpus(elec, tou)

    # _validate() reports what its check 1 did per fuel — it is the one check that
    # can decline to run (the fork skip) — so the boundary record can state what
    # happened on this run instead of what usually happens.
    presence = _validate(elec, gas, tou, gas_detail)

    boundary = _boundary_record(elec, export_info, excluded, gas, presence)

    es = _summary_frame(elec, SUMMARY_STATEMENTS_ELEC,
                        "SUMMARY_STATEMENTS_ELEC", "electric")
    es = es[["period", "days", "net_kwh", "gross_kwh",
             "sdge_delivery", "cca_generation", "current_charges"]]

    # The artifacts are one evidence set: publish them together or not at all.
    # electric_bill_summary.csv and gas_bill_summary.csv predate this script and were
    # committed with CRLF line endings; they keep CRLF so the reproduction gate stays a
    # byte-for-byte match against the known-good originals. New artifacts use LF.
    # ALL SIX artifacts are always in the set: when household.has_gas is false the
    # three gas files are published as header-only CSVs (empty frames, same schemas),
    # so stale gas data from another corpus is replaced rather than left in place.
    if gas is not None:
        gs = _summary_frame(gas, SUMMARY_STATEMENTS_GAS,
                            "SUMMARY_STATEMENTS_GAS", "gas").copy()
        gs = gs.rename(columns={"period_end_month": "file_month"})
        gs = gs[GAS_SUMMARY_COLS].sort_values("file_month")
        gas_periods_out, gas_summary_out, gas_detail_out = gas, gs, gas_detail
    else:
        gas_periods_out = pd.DataFrame(columns=GAS_PERIOD_COLS)
        gas_summary_out = pd.DataFrame(columns=GAS_SUMMARY_COLS)
        gas_detail_out = pd.DataFrame(columns=GAS_DETAIL_COLS)
    writes = [
        (DATA / "bill_periods_electric.csv", lambda p: elec.to_csv(p, index=False)),
        (DATA / "bill_periods_gas.csv",
         lambda p: gas_periods_out.to_csv(p, index=False)),
        (DATA / "bill_tou_detail.csv", lambda p: tou.to_csv(p, index=False)),
        (DATA / "bill_gas_detail.csv",
         lambda p: gas_detail_out.to_csv(p, index=False)),
        (DATA / "electric_bill_summary.csv",
         lambda p: es.to_csv(p, index=False, lineterminator="\r\n")),
        (DATA / "gas_bill_summary.csv",
         lambda p: gas_summary_out.to_csv(p, index=False, lineterminator="\r\n")),
        # In the SAME atomic set as the six above: it states which corpus those six
        # contain, so it can never be left describing a corpus that was rolled back.
        # indent=1 and a trailing newline match the other committed JSON artifacts
        # (data/bill_decomposition.json), so a reader diffing them sees one format.
        (DATA / "bill_corpus_boundary.json",
         lambda p: p.write_text(json.dumps(boundary, indent=1, sort_keys=False) + "\n")),
    ]
    _write_all_atomically(writes)

    _announce_boundary(boundary)
    print(f"electric: {len(elec)} billing periods from "
          f"{elec.statement_date.nunique()} statements "
          f"({elec.statement_date.min()} .. {elec.statement_date.max()})")
    if gas is not None:
        print(f"gas:      {len(gas)} billing periods from "
              f"{gas.statement_date.nunique()} statements "
              f"({gas.statement_date.min()} .. {gas.statement_date.max()})")
    else:
        print("gas:      not applicable (household.has_gas: false) — "
              "artifacts published header-only")
    print(f"tou rows: {len(tou)}")
    print(f"gas detail rows: {len(gas_detail_out)}")
    summary = (f"summary window: electric {len(es)} periods, {es.days.sum()} days, "
               f"${es.current_charges.sum():,.2f}")
    if gas is not None:
        summary += f"; gas {len(gs)} periods, ${gs.total_gas_service.sum():,.2f}"
    print(summary)


if __name__ == "__main__":
    main()
