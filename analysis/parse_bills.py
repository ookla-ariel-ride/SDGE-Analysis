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

OUTPUTS (committed, de-identified)
    data/bill_periods_electric.csv  one row per electric billing period
    data/bill_periods_gas.csv       one row per gas billing period
    data/bill_tou_detail.csv        long format: per period × section × season × TOU
                                    period → kWh and $/kWh as printed on the bill
    data/electric_bill_summary.csv  regenerated (same schema as the original)
    data/gas_bill_summary.csv       regenerated (same schema as the original)
    When household.has_gas is false the two gas artifacts are still published — as
    HEADER-ONLY CSVs (same headers, zero rows), in the same atomic set as the
    electric ones, so a fork can never keep another corpus's stale gas data
    sitting next to its own fresh electric data.

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
       contradiction fails closed), and the two gas artifacts are published as
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
"""
import contextlib
import fcntl
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
#                                nothing. Once your corpus is stable, replace these
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
            # "<N> Days Charge $a + $b + $c = total" follows the rate row; it is absent
            # on bills whose period is not split, where the segment covers every day.
            dm = re.search(r"(\d+)\s*Days Charge", win[r_row.end():])
            seg_days = int(dm.group(1)) if dm else days
            for j, tp in enumerate(("on_peak", "off_peak", "super_off_peak")):
                tou.append(dict(
                    statement_date=stmt, period=period, section=section,
                    season=season, segment=segment, segment_days=seg_days,
                    tou_period=tp,
                    kwh=_f(u.group(1 + j)), rate_per_kwh=_f(r_row.group(1 + j)),
                ))
    return rows, tou


# ---------------------------------------------------------------------------
# Gas
# ---------------------------------------------------------------------------
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# Column schemas of the two gas artifacts — shared by the real writers and the
# header-only "retired" writers (household.has_gas false) so the two can never drift.
GAS_PERIOD_COLS = ["statement_date", "period", "period_end_month", "therms",
                   "total_gas_service", "billed_amount", "baseline_rate",
                   "nonbaseline_rate"]
GAS_SUMMARY_COLS = ["file_month", "therms", "total_gas_service",
                    "baseline_rate", "nonbaseline_rate"]


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
# Corpus-level validation and transactional publication
# ---------------------------------------------------------------------------
def _validate(elec, gas, tou):
    """Refuse to publish anything unless the whole parsed corpus is sound.

    Parsing every PDF that happens to be present is not enough: a statement that is
    absent, misnamed, or unreadable would simply not appear, and the artifacts would be
    rewritten a few periods short with no error. Every check below runs BEFORE any file
    is touched.

    `gas` may be None: a no-gas household (household.has_gas false — see the
    applicability envelope in the module docstring). All gas checks are then skipped."""
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
    for label, df, want_list, list_name, _fmt in fuels:
        have, want = set(df.statement_date), set(want_list)
        if not have & want:
            print(f"NOTICE: none of the {len(want)} statement dates in {list_name} are "
                  f"present in the {label} corpus on disk — treating the list as "
                  f"not-applicable (a FORK running on its own statements) and skipping "
                  f"reproduction-gate check 1 for {label}. Once your corpus is stable, "
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
    if has_gas:
        gas_rows = [parse_gas(f) for f in gas_pdfs]
        gas = pd.DataFrame(gas_rows).sort_values("statement_date")[GAS_PERIOD_COLS]

    elec = pd.DataFrame(elec_rows)
    # Sort chronologically by the period's start date. Sorting on the period STRING
    # would put "10/1/25 - 10/27/25" before "9/26/25 - 9/30/25".
    elec["_start"] = pd.to_datetime(elec.period.str.split(" - ").str[0], format="%m/%d/%y")
    elec = elec.sort_values("_start").drop(columns="_start")
    tou = pd.DataFrame(tou_rows)

    _validate(elec, gas, tou)

    es = _summary_frame(elec, SUMMARY_STATEMENTS_ELEC,
                        "SUMMARY_STATEMENTS_ELEC", "electric")
    es = es[["period", "days", "net_kwh", "gross_kwh",
             "sdge_delivery", "cca_generation", "current_charges"]]

    # The artifacts are one evidence set: publish them together or not at all.
    # electric_bill_summary.csv and gas_bill_summary.csv predate this script and were
    # committed with CRLF line endings; they keep CRLF so the reproduction gate stays a
    # byte-for-byte match against the known-good originals. New artifacts use LF.
    # ALL FIVE artifacts are always in the set: when household.has_gas is false the
    # two gas files are published as header-only CSVs (empty frames, same schemas),
    # so stale gas data from another corpus is replaced rather than left in place.
    if gas is not None:
        gs = _summary_frame(gas, SUMMARY_STATEMENTS_GAS,
                            "SUMMARY_STATEMENTS_GAS", "gas").copy()
        gs = gs.rename(columns={"period_end_month": "file_month"})
        gs = gs[GAS_SUMMARY_COLS].sort_values("file_month")
        gas_periods_out, gas_summary_out = gas, gs
    else:
        gas_periods_out = pd.DataFrame(columns=GAS_PERIOD_COLS)
        gas_summary_out = pd.DataFrame(columns=GAS_SUMMARY_COLS)
    writes = [
        (DATA / "bill_periods_electric.csv", lambda p: elec.to_csv(p, index=False)),
        (DATA / "bill_periods_gas.csv",
         lambda p: gas_periods_out.to_csv(p, index=False)),
        (DATA / "bill_tou_detail.csv", lambda p: tou.to_csv(p, index=False)),
        (DATA / "electric_bill_summary.csv",
         lambda p: es.to_csv(p, index=False, lineterminator="\r\n")),
        (DATA / "gas_bill_summary.csv",
         lambda p: gas_summary_out.to_csv(p, index=False, lineterminator="\r\n")),
    ]
    _write_all_atomically(writes)

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
    summary = (f"summary window: electric {len(es)} periods, {es.days.sum()} days, "
               f"${es.current_charges.sum():,.2f}")
    if gas is not None:
        summary += f"; gas {len(gs)} periods, ${gs.total_gas_service.sum():,.2f}"
    print(summary)


if __name__ == "__main__":
    main()
