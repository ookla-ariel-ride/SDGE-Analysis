#!/usr/bin/env python3
"""Historical rate engine — the tariff in force on any date in the bill corpus.

analysis/rates.py holds exactly ONE vintage (effective 6/1/2026, correct for
projections at constant current rates). Re-billing a historical period at that
vintage mixes tariff generations (CLAUDE.md §9, "one rate vintage per
projection"). This module answers the other question: what did the tariff say
on a given PAST date? Everything it returns is read out of the two committed
bill artifacts — never hand-transcribed:

    data/bill_periods_electric.csv   period dates, days, generation_provider,
                                     base_services_charge (period total)
    data/bill_tou_detail.csv         per statement × section × season × rate
                                     segment × TOU period: kWh and the printed
                                     "Rate/kWh" — the per-bill vintage evidence

WHAT SOURCES WHAT (requirement: name the artifact column behind each component)
  * delivery (UDC) $/kWh by season and TOU period
        bill_tou_detail.rate_per_kwh where section == "delivery". Matches
        rates.py's UDC table on the final vintage (verified by
        test_rates_history.py to the cent on all six season×TOU cells).
  * generation $/kWh by season and TOU period — BUNDLED PERIODS ONLY
        bill_tou_detail.rate_per_kwh where section == "generation" is what
        SDG&E's "Electricity Generation (Details below)" TOU table prints.
        THE TRUST BOUNDARY: during bundled periods (through 12/26/24) that
        printed table is the tariff SDG&E charged, and RateSet.generation()
        returns it. From 12/27/24 the same table is SDG&E's bundled-generation
        (EECC) COMPARISON printed beside a bill whose generation was charged by
        the CCA — summing kWh × printed rate does not reproduce
        bill_periods_electric.cca_generation (checked: 1/28/26–2/26/26 prints
        $116.09 of generation TOU lines against a $56.82 CCA charge). So
        RateSet.generation() REFUSES on a CCA date, naming the date, the
        period, the provider and the gap, and the printed comparison value is
        reachable only through the deliberately named diagnostic accessor
        RateSet.generation_comparison_table(), whose only legitimate use is
        reconstructing PRINTED statement lines (the re-billing, holdout and
        CCA-gap checks say so in their own docstrings). The CCA's own per-TOU
        rates appear only on the CCA pages, which parse_bills.py does not
        extract, so RateSet.cca_generation() fails closed too: on a CCA date
        this module has NO generation tariff to offer, by either name.

        THE SAME BOUNDARY HOLDS AT EVERY OTHER EXIT, not just the single-cell
        accessor. The aggregations that hand out many cells at once —
        RateSet.cells(), current_vintage() and the data/rate_vintages.csv
        writer — would otherwise leak exactly the number generation() refuses,
        under a column called "rate", to a caller who never typed the word
        "comparison". So each of them carries the AUTHORITY of every value it
        returns:
          - RateSet.cells() returns CellValue objects, not (rate, tier, note)
            tuples. On a CCA date a generation CellValue has authority
            "printed_comparison_only", .rate None, and the printed number in
            .comparison_rate. CellValue is deliberately not iterable, so the
            old tuple unpacking raises TypeError rather than binding a printed
            comparison rate to a variable named `rate`.
          - current_vintage() returns VintageEntry objects with the same two
            value slots and the same authority field. On this corpus, which ends
            inside the CCA era, EVERY generation entry is comparison-only:
            from bill evidence alone there is no current charged generation
            tariff, and the function says so instead of returning the last
            printed comparison number as one.
          - data/rate_vintages.csv splits every generation span at the provider
            break and publishes the CCA side under a different section name
            (generation_printed_comparison), with rate_per_kwh EMPTY and the
            printed value in its own comparison_rate_per_kwh column. A consumer
            reading rate_per_kwh cannot receive a comparison number.
        DELIVERY is unaffected at every exit: the UDC is the charged tariff in
        both provider eras (in bundled periods sdge_delivery additionally
        includes generation, which is a line-item fact about the bill, not a
        different delivery tariff).
  * provider in force ("bundled" / "CCA")
        bill_periods_electric.generation_provider for the covering period.
        Bundled through 12/26/24; CCA from 12/27/24. In bundled periods
        sdge_delivery INCLUDES generation.
  * BSC $/day
        bill_periods_electric.base_services_charge ÷ days for the covering
        period. The artifact holds only the period TOTAL (the printed daily
        unit rate is not captured by the parser), so the quotient carries the
        bill's rounding of the total. Populated only from the 10/1/25–10/27/25
        period onward; whether that charge is new, renamed from another line,
        or a parser gap is an OPEN question — before that date the engine
        returns a flagged absence that raises when read, never an invented 0.
  * PCIA and NBC
        genuinely NOT sourceable: bill_tou_detail carries only the TOU energy
        "Rate/kWh" lines; the PCIA and non-bypassable-charge bill lines are not
        parsed into any committed artifact. RateSet.pcia() / RateSet.nbc()
        fail closed and point at rates.py for the current-vintage values and
        the mechanics to preserve (PCIA on net kWh, NBC on gross imports,
        BSC per day).

HOW A CELL'S TIMELINE IS BUILT
  Each statement's season block is dated from the period dates and the tariff
  season calendar (rates.SUMMER_MONTHS), then split into rate segments by
  segment_days in bill order — a mid-cycle tariff change is printed as two
  segments with their own $/kWh, and those are kept piecewise, never collapsed.
  A row with kWh > 0 is a direct observation of that cell's rate over the
  segment's dates (a net-negative bucket prints "Rate/kWh $0.00000" and so
  carries NO rate evidence; the parser records 0.0 there). Every positive-kWh
  row must carry a finite, strictly positive $/kWh, and _load() refuses the
  corpus otherwise, naming the period and the cell: a zero, negative, NaN or
  infinite rate on a billed bucket is a parser regression, and accepting one
  would publish a directly-evidenced free (or negative) tariff. Observations are
  merged into maximal constant-rate spans. Gaps between spans:
    * flanked by the SAME rate on both sides → the value is carried across
      (tier "carried"): the corpus is continuous, so any change inside the gap
      would have had to change and change back, each time invisibly;
    * flanked by DIFFERENT rates → the change date inside the gap is not
      determinable from the artifacts → flagged ABSENT, raises when read;
    * before the first or after the last observation → flagged ABSENT.
  rates_on(date) never raises for an in-corpus date: it returns a RateSet in
  which every component is either a sourced value or a flagged absence, and
  reading an absent cell raises SystemExit naming the date and the cell.
  Outside the corpus (before 2024-05-25 or after 2026-06-26) rates_on itself
  raises — this engine never extrapolates.

RE-BILLING
  rebill_statement(period, mode=...) re-prices ONE statement's printed TOU
  energy lines from its own reported per-TOU kWh (net-negative buckets at the
  printed $0, matching every statement in the corpus — under NEM 2.0 in-period
  exports settle at true-up, not on the monthly statement). The modes and what
  each one can and cannot prove are in that function's docstring and under
  "THE RESIDUAL ARTIFACT" below.

  bill_nem(frame) / bill_nem_monthly(frame) mirror rates.bill_nem's signature
  so callers can swap engines, with these documented differences: rates vary by
  date (netting is per maximal constant-rate span within the month, which is
  exactly how the bills segment a mid-cycle change); the result is TOU ENERGY
  dollars only — PCIA, NBC and pre-10/25 BSC are not artifact-sourced, so no
  per-kWh adders or daily charges are included; and delivery + generation
  pricing exists only for BUNDLED dates. Both functions REFUSE, naming the
  date and the period, any frame that touches a CCA period (12/27/24 onward):
  there the printed generation TOU table is SDG&E's bundled-generation (EECC)
  comparison, not the CEA tariff that was charged, so delivery + generation
  would be a plausible-looking wrong number. delivery_only=True prices the UDC
  (delivery) component alone and works on any corpus date, CCA included — the
  result then contains NO generation at all and must be labeled that way.
  These are not general historical bill replacements: they price TOU energy
  under the stated restriction, nothing else.

  NOTE the caller supplies the TOU period labels, exactly as with
  rates.bill_nem. Historical statements were billed under historical WINDOWS
  (the weekday 10-14 super-off-peak window only exists from 2026-03-01 —
  rates.py's docstring); assigning period labels to historical intervals is
  the caller's problem, not this module's.

THE RESIDUAL ARTIFACT: A RECONSTRUCTION CHECK, NOT A DOLLAR TIE-OUT
  data/rate_rebilling_residuals.csv compares two quantities computed from the
  SAME bill rows. printed_tou_energy_usd is Σ kWh × the rate the statement
  itself printed on those lines — the reference is the statement's OWN printed
  TOU energy lines, nothing external. timeline_energy_usd prices those same kWh
  through the timeline built out of those same rows. Every observation lands
  inside its own span, so the difference is zero as an algebraic identity — on
  all 26 statements and for ANY rate values whatsoever. It proves the timeline
  reconstruction is self-consistent (segment dating, span merging, cell
  lookup); it CANNOT detect a parser that misread a rate, a column or a
  segment, and it is not an independent validation of dollars.
  No independent energy-only total exists in the committed artifacts to tie
  against: bill_periods_electric.sdge_delivery bundles the non-bypassable
  charges, PCIA and the base services charge together with delivery energy, and
  none of those three is sourceable from any committed artifact (RateSet.pcia /
  RateSet.nbc fail closed), so delivery energy cannot be separated back out of
  it. An independent per-statement dollar tie-out is therefore NOT available
  from committed data. Two checks that CAN move are reported beside the
  identity:
    * holdout_* — statement S's own kWh priced by a timeline rebuilt from every
      OTHER statement's rate rows. THE UNIT OF INDEPENDENCE IS THE STATEMENT (the
      file), NOT the billing period. One statement can print more than one
      billing period — the 2025-10-31 statement carries both 9/26/25-9/30/25 and
      10/1/25-10/27/25 — and sibling periods' rows come from one parse of one PDF
      down one extraction path, so they are not independent of each other. A
      holdout that dropped only the period under test would leave its sibling in
      the witness pool, and the sibling could mark the held-out lines
      corroborated: a parser error shared across that statement would corroborate
      itself while the artifact described the witness as another statement. So the
      holdout withholds EVERY rate row whose statement_date matches the held-out
      period's statement_date. Their segment DATES are still used (dates are
      calendar facts, and the held-out reconstruction needs them). Independent of
      S's printed rates, so a misread rate on S moves it. It can price only what
      the rest of the corpus independently witnesses, and the artifact says per
      statement exactly which printed lines those are (corroboration(), one row
      per positive-kWh (cell, segment) printed line):
        holdout_corroborated_rows / _agree_rows / _disagree_rows
              lines the other statements DO witness across the whole printed
              segment, and whether the witness rate equals the printed rate
              exactly. A disagreement is a finding: it means the corpus
              contradicts this statement's printed rate.
        holdout_uncorroborated_rows + holdout_uncorroborated_reasons
              lines no holdout witness can reach, WITH the reason. These are
              uncorroborated BY CONSTRUCTION, not failures and not passes: a
              rate whose first appearance in the corpus is the line under test
              cannot be corroborated by holding that line out, and neither can a
              line at the corpus edge. The reasons name which shape it is
              (no witness anywhere for the cell / first witness later / last
              witness earlier / flanking witnesses disagree so the change in the
              gap is undated / a witness span boundary falls inside the printed
              segment). This is why there is no aggregate coverage threshold:
              a corpus of stable repeated rates would clear one while a
              corrupted new vintage sat entirely outside it.
        split_differing_cells / split_differing_corroborated_rows /
        split_differing_uncorroborated_rows
              the mid-cycle change itself: the cells whose printed rate is NOT
              the same in both segments of a split statement, and how many of
              their lines are corroborated. On this corpus that count is zero on
              every split statement — the change is printed only on the statement
              that carries it — so those statements are recorded as
              uncorroborated rather than as validated.
      Note what the corroborating witness always IS here: a CARRIED span between
      two other statements that flank S's dates with the SAME printed rate. No
      other statement bills S's dates, so a direct witness is impossible by
      construction, on every one of these rows. Which statements those are is
      exposed per row (corroboration()'s witness_statements), so the provenance
      can be checked rather than assumed — in particular that no witness is a
      sibling period printed on the held-out statement.
    * cca_generation_gap_usd — Σ(printed generation kWh × printed rate) minus
      bill_periods_electric.cca_generation, for CCA statements. This is the
      measured evidence that the printed generation table is not the tariff
      charged during CCA periods (1/28/26-2/26/26: $116.09 printed against a
      $56.82 CCA charge).

Generator outputs (deterministic writers, atomic replace; run twice → identical):
    data/rate_vintages.csv              every cell's spans with the provider in
                                        force, the AUTHORITY of the value
                                        (charged_tariff / printed_comparison_only
                                        / not_a_rate) and the evidence tier
                                        (direct / carried / absent), plus the BSC
                                        and provider timelines. Charged tariffs
                                        are in rate_per_kwh; printed
                                        CCA-generation comparisons are in
                                        comparison_rate_per_kwh under section
                                        generation_printed_comparison, and the
                                        two columns are never both populated.
    data/rate_rebilling_residuals.csv   per-statement printed TOU energy, the
                                        timeline reconstruction of it, the
                                        holdout re-bill with its per-line
                                        corroboration status and the reason for
                                        every uncorroborated line, the two
                                        vintage-collapse counterfactuals and the
                                        CCA generation gap; worst statement
                                        flagged
"""
import csv
import datetime as dt
import math
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as _rates


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

SECTIONS = ("delivery", "generation")
SEASONS = ("summer", "winter")
TOU_PERIODS = ("on_peak", "off_peak", "super_off_peak")
# rates.py's short period labels, read off its own rate table rather than
# re-declared here: the TOU-label AST guard in test_scripts_runnable.py treats
# a private copy of the short trio as a reimplemented window rule.
_SHORT = tuple(_rates.UDC["S"])
_LONG_FOR_SHORT = dict(zip(_SHORT, TOU_PERIODS))
_SEASON_FOR_SEAS = {"S": "summer", "W": "winter"}

_ABSENT_PCIA_NBC = (
    "bill_tou_detail.csv carries only the printed TOU energy 'Rate/kWh' lines; the "
    "{name} bill line is not parsed into any committed artifact, so its historical "
    "vintages are not determinable. rates.py holds the 6/1/2026 value and the "
    "mechanics to preserve ({mech}).")


def _date(s):
    m, d, y = s.split("/")
    return dt.date(2000 + int(y), int(m), int(d))


def _season_of(day):
    return "summer" if day.month in _rates.SUMMER_MONTHS else "winter"


def _season_blocks(a, b):
    """Contiguous (season, start, end) runs covering the period [a, b]."""
    out = []
    day = a
    while day <= b:
        s = _season_of(day)
        start = day
        while day < b and _season_of(day + dt.timedelta(days=1)) == s:
            day += dt.timedelta(days=1)
        out.append((s, start, day))
        day += dt.timedelta(days=1)
    if len(out) > 2:
        raise SystemExit(f"period {a}..{b} crosses more than one season boundary: {out}")
    return out


class _Span:
    """One constant-rate stretch of a cell's timeline.

    sources names the STATEMENTS (statement_date strings) whose printed lines
    evidence the rate: the statements that printed it on a direct span, and the
    two flanking statements that printed the same rate on a carried span. Empty on
    an absent span. This is the witness provenance corroboration() reports, so a
    caller can check WHICH statement corroborated a line instead of trusting the
    holdout's bookkeeping."""
    __slots__ = ("start", "end", "rate", "tier", "note", "sources")

    def __init__(self, start, end, rate, tier, note, sources=()):
        self.start, self.end = start, end
        self.rate, self.tier, self.note = rate, tier, note
        self.sources = tuple(sources)


class _Engine:
    def __init__(self, periods, tou, exclude_statement=None):
        self.periods = periods                      # chronological period dicts
        self.tou = tou                              # validated TOU detail rows
        self.start = periods[0]["start"]
        self.end = periods[-1]["end"]
        # exclude_statement builds the HOLDOUT timeline. The unit is the STATEMENT
        # (statement_date — the file), not the billing period: one statement can
        # print two periods (the 2025-10-31 statement prints 9/26/25-9/30/25 and
        # 10/1/25-10/27/25), and both of those periods' rows come from one parse of
        # one PDF down one extraction path. Withholding only the period under test
        # would leave its sibling as a "witness" that shares every failure mode
        # with the line being tested, so a parser error on that statement could
        # corroborate itself. Segment DATES still come from every statement (they
        # are calendar facts, and the held-out statement's own reconstruction needs
        # its dates); only the rate observations are withheld.
        self.exclude_statement = exclude_statement
        self.segments = self._date_segments(periods, tou)
        self.timelines = self._timelines(
            [r for r in tou if r["statement_date"] != exclude_statement]
            if exclude_statement is not None else tou)

    # -- construction ------------------------------------------------------
    @staticmethod
    def _date_segments(periods, tou):
        """(period, section, season, segment) -> (start, end), from period dates,
        the season calendar and segment_days in bill order. Fails closed on any
        mismatch between the printed day counts and the calendar."""
        by_period = {p["period"]: p for p in periods}
        segdays = {}
        for r in tou:
            key = (r["period"], r["section"], r["season"])
            seg = segdays.setdefault(key, {})
            prev = seg.setdefault(r["segment"], r["segment_days"])
            if prev != r["segment_days"]:
                raise SystemExit(f"{key}: segment {r['segment']} appears with two "
                                 f"day counts ({prev}, {r['segment_days']})")
        out = {}
        for p in periods:
            blocks = _season_blocks(p["start"], p["end"])
            for section in SECTIONS:
                seasons_seen = {s for (per, sec, s) in segdays
                                if per == p["period"] and sec == section}
                if seasons_seen != {b[0] for b in blocks}:
                    raise SystemExit(
                        f"[{p['period']}] {section}: bill prints TOU blocks for "
                        f"{sorted(seasons_seen)} but the calendar says {blocks}")
                for season, s0, s1 in blocks:
                    seg = segdays[(p["period"], section, season)]
                    if sorted(seg) != list(range(len(seg))):
                        raise SystemExit(f"[{p['period']}] {section}/{season}: "
                                         f"non-contiguous segment numbers {sorted(seg)}")
                    if sum(seg.values()) != (s1 - s0).days + 1:
                        raise SystemExit(
                            f"[{p['period']}] {section}/{season}: segment_days "
                            f"{seg} do not sum to the {(s1 - s0).days + 1}-day block "
                            f"{s0}..{s1}")
                    cur = s0
                    for i in sorted(seg):
                        e = cur + dt.timedelta(days=seg[i] - 1)
                        out[(p["period"], section, season, i)] = (cur, e)
                        cur = e + dt.timedelta(days=1)
        return out

    def _timelines(self, tou):
        """cell -> chronological, corpus-covering list of _Span."""
        obs = {(sec, season, tp): [] for sec in SECTIONS
               for season in SEASONS for tp in TOU_PERIODS}
        for r in tou:
            cell = (r["section"], r["season"], r["tou_period"])
            if cell not in obs:
                raise SystemExit(f"unknown TOU cell in bill_tou_detail.csv: {cell}")
            span = self.segments[(r["period"], r["section"], r["season"], r["segment"])]
            if r["kwh"] > 0:
                obs[cell].append((span[0], span[1], r["rate"], r["period"],
                                  r["statement_date"]))
            elif r["rate"] != 0.0:
                raise SystemExit(
                    f"[{r['period']}] {'/'.join(cell)}: a net-negative bucket "
                    f"({r['kwh']} kWh) carries a nonzero printed rate {r['rate']} — "
                    "the credit-lines-print-zero assumption is broken; re-derive.")
        timelines = {}
        for cell, o in obs.items():
            o.sort()
            merged = []          # [[start, end, rate, [periods], [statements]], ...]
            for s, e, rate, period, stmt in o:
                if merged and merged[-1][1] >= s:
                    raise SystemExit(f"{'/'.join(cell)}: overlapping rate segments "
                                     f"at {s} — the corpus is not clean")
                if merged and merged[-1][2] == rate and \
                        merged[-1][1] + dt.timedelta(days=1) == s:
                    merged[-1][1] = e
                    merged[-1][3].append(period)
                    merged[-1][4].append(stmt)
                else:
                    merged.append([s, e, rate, [period], [stmt]])
            spans, prev = [], None
            name = "/".join(cell)
            # a gap means "no positive-kWh line here"; in a holdout timeline the
            # withheld statement is a second reason, and the note must say so
            gap_why = ("net-negative gap" if self.exclude_statement is None else
                       f"gap with the {self.exclude_statement} statement held out")
            if not merged:
                if self.exclude_statement is not None:
                    timelines[cell] = [_Span(
                        self.start, self.end, None, "absent",
                        "no positive-kWh line outside the held-out statement "
                        + self.exclude_statement)]
                    continue
                raise SystemExit(f"{name}: no positive-kWh line anywhere in the corpus")
            if merged[0][0] > self.start:
                spans.append(_Span(self.start, merged[0][0] - dt.timedelta(days=1),
                                   None, "absent",
                                   "no positive-kWh line before "
                                   f"{merged[0][0]} (net-negative buckets print $0)"))
            for s, e, rate, srcs, stmts in merged:
                if prev is not None:
                    gap0 = prev.end + dt.timedelta(days=1)
                    if gap0 < s:
                        gap1 = s - dt.timedelta(days=1)
                        if prev.rate == rate:
                            # the witnesses to a carried span are exactly the two
                            # flanking statements that printed the same rate
                            spans.append(_Span(
                                gap0, gap1, rate, "carried",
                                f"{gap_why}; flanking observed rates equal at "
                                f"{rate:.5f}",
                                dict.fromkeys(prev.sources + tuple(stmts))))
                        else:
                            spans.append(_Span(gap0, gap1, None, "absent",
                                               f"rate changed from {prev.rate:.5f} to "
                                               f"{rate:.5f} somewhere in this gap; no "
                                               "positive-kWh line dates the change"))
                # "printed on", not "billed on": the note records which
                # statements PRINTED this rate on a positive-kWh line, which is
                # the same thing as charged only where the authority column says
                # charged_tariff. On CCA generation lines the printed table is
                # SDG&E's bundled-generation comparison and nothing was billed
                # at it (see the module docstring's trust boundary).
                prev = _Span(s, e, rate, "direct",
                             "printed on " + "; ".join(dict.fromkeys(srcs)),
                             dict.fromkeys(stmts))
                spans.append(prev)
            if merged[-1][1] < self.end:
                spans.append(_Span(merged[-1][1] + dt.timedelta(days=1), self.end,
                                   None, "absent",
                                   f"no positive-kWh line after {merged[-1][1]}"))
            timelines[cell] = spans
        return timelines

    # -- lookups -----------------------------------------------------------
    def span_at(self, cell, day):
        for span in self.timelines[cell]:
            if span.start <= day <= span.end:
                return span
        raise SystemExit(f"{'/'.join(cell)}: no timeline span covers {day} — "
                         "engine construction bug")

    def period_covering(self, day):
        for p in self.periods:
            if p["start"] <= day <= p["end"]:
                return p
        raise SystemExit(f"no billing period covers {day} — engine construction bug")


_ENGINE = None
_HOLDOUT = {}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _HOLDOUT.clear()                 # never serve holdouts of a retired corpus
        _ENGINE = _Engine(*_load())
    return _ENGINE


def _holdout_engine(statement_date):
    """The engine with one STATEMENT's rate observations withheld (see _Engine).

    Keyed by statement_date, not by billing period, so the two periods printed on
    the 2025-10-31 statement share one holdout timeline and neither can witness the
    other."""
    eng = _engine()
    if statement_date not in _HOLDOUT:
        _HOLDOUT[statement_date] = _Engine(
            eng.periods, eng.tou, exclude_statement=statement_date)
    return _HOLDOUT[statement_date]


def _load():
    """Read and cross-validate the two artifacts. Fails closed on anything odd."""
    ppath = DATA / "bill_periods_electric.csv"
    tpath = DATA / "bill_tou_detail.csv"
    for path in (ppath, tpath):
        if not path.exists():
            raise SystemExit(f"missing committed artifact {path} — run parse_bills.py")
    periods = []
    for row in csv.DictReader(open(ppath, newline="")):
        a, b = [_date(x.strip()) for x in row["period"].split(" - ")]
        days = int(float(row["days"]))
        if (b - a).days + 1 != days:
            raise SystemExit(f"[{row['period']}]: {days} printed days but the dates "
                             f"span {(b - a).days + 1}")
        if row["generation_provider"] not in ("bundled", "CCA"):
            raise SystemExit(f"[{row['period']}]: unknown generation_provider "
                             f"{row['generation_provider']!r}")
        periods.append(dict(
            period=row["period"], statement_date=row["statement_date"],
            start=a, end=b, days=days, provider=row["generation_provider"],
            bsc_total=float(row["base_services_charge"])
            if row["base_services_charge"] else None,
            # the CCA generation charge actually billed for the period; the
            # column reads 0.0 on bundled statements, where there is none
            cca_total=float(row["cca_generation"])
            if row["cca_generation"] else None))
    periods.sort(key=lambda p: p["start"])
    for p1, p2 in zip(periods, periods[1:]):
        if p2["start"] != p1["end"] + dt.timedelta(days=1):
            raise SystemExit(f"billing periods are not continuous: {p1['period']} then "
                             f"{p2['period']} — the vintage timeline would have a hole")
    tou = []
    known = set()
    stmt_of_period = {p["period"]: p["statement_date"] for p in periods}
    for row in csv.DictReader(open(tpath, newline="")):
        kwh, rate = float(row["kwh"]), float(row["rate_per_kwh"])
        # the row's statement_date is the FILE it was parsed from, and the holdout's
        # unit of independence (_Engine.exclude_statement). If the two artifacts
        # disagree about which statement printed a period, that unit is ambiguous —
        # a sibling period could be held out with the wrong statement or left in the
        # witness pool — so refuse rather than pick one.
        want = stmt_of_period.get(row["period"])
        if want is None or row["statement_date"] != want:
            raise SystemExit(
                f"[{row['period']}] {row['section']}/{row['season']}/"
                f"{row['tou_period']}: bill_tou_detail.csv says statement_date "
                f"{row['statement_date']!r} but bill_periods_electric.csv says "
                f"{want!r} for that period. statement_date is the unit of "
                "independence for the holdout re-bill (one statement can print two "
                "billing periods), so the two artifacts must agree on it. Refusing "
                "the corpus — re-run parse_bills.py.")
        # A billed bucket's printed $/kWh is the module's only rate evidence, so
        # its shape is checked at the loader, before any timeline exists. Zero,
        # negative, NaN and infinite are parser regressions, not tariffs: a
        # positive-kWh row at rate 0 would otherwise become a "direct"
        # observation and publish an evidenced free rate. (kwh <= 0 rows legally
        # print $0.00000 — that case is checked in _timelines, which is where the
        # credit-line assumption lives.)
        if kwh > 0 and not (math.isfinite(rate) and rate > 0):
            raise SystemExit(
                f"[{row['period']}] {row['section']}/{row['season']}/"
                f"{row['tou_period']}: a billed bucket ({kwh} kWh) carries "
                f"rate_per_kwh {row['rate_per_kwh']!r} in bill_tou_detail.csv. A "
                "printed TOU energy line must carry a finite, strictly positive "
                "$/kWh; zero, negative, NaN and infinite are parser regressions, "
                "and treating one as a rate observation would publish a "
                "directly-evidenced free or negative tariff. Refusing the corpus "
                "— re-run parse_bills.py and re-derive.")
        tou.append(dict(period=row["period"], statement_date=row["statement_date"],
                        section=row["section"],
                        season=row["season"], segment=int(row["segment"]),
                        segment_days=int(row["segment_days"]),
                        tou_period=row["tou_period"], kwh=kwh, rate=rate))
        known.add(row["period"])
    missing = [p["period"] for p in periods if p["period"] not in known]
    if not tou or missing:
        raise SystemExit(f"bill_tou_detail.csv has no rows for: {missing or 'anything'}")
    return periods, tou


# ---------------------------------------------------------------------------
# The authority of a value: was it CHARGED, or only PRINTED for comparison?
# ---------------------------------------------------------------------------
# Delivery (UDC) is the charged tariff in both provider eras. Generation printed
# on a bundled statement is also what SDG&E charged. Generation printed on a CCA
# statement is SDG&E's bundled-generation (EECC) comparison table beside a bill
# the CCA charged at rates no committed artifact carries — a number, but not a
# tariff. Every aggregation in this module labels which of the two it is holding,
# so no caller can read the second as the first.
CHARGED_TARIFF = "charged_tariff"
COMPARISON_ONLY = "printed_comparison_only"
NOT_A_RATE = "not_a_rate"                 # provider-timeline rows: no $/kWh at all
AUTHORITIES = (CHARGED_TARIFF, COMPARISON_ONLY, NOT_A_RATE)

# The serialized section name for the CCA side of a generation span. Deliberately
# NOT "generation": a consumer filtering section == "generation" out of
# data/rate_vintages.csv gets charged tariffs only.
COMPARISON_SECTION = "generation_printed_comparison"

_COMPARISON_NOTE = (
    "SDG&E's bundled-generation (EECC) COMPARISON printed beside a statement whose "
    "generation the CCA charged — NOT a tariff and NOT what was billed; the CCA's "
    "own per-TOU rates are in no committed artifact (RateSet.generation and "
    "RateSet.cca_generation both refuse here, and cca_generation_gap measures how "
    "far this printed table is from the charge actually billed)")


def _authority_on(section, provider):
    """The authority of one (section, provider) pair — the whole rule, in one place."""
    if section == "generation" and provider == "CCA":
        return COMPARISON_ONLY
    return CHARGED_TARIFF


class _Valued:
    """Shared behaviour of CellValue and VintageEntry.

    Deliberately NOT iterable, indexable or unpackable: the previous shapes were
    plain tuples, and `rate, tier, note = cells[cell]` would silently bind a
    printed CCA-generation comparison number to a variable called `rate`. Any
    such caller now gets a TypeError and has to look at .authority.

      rate             the CHARGED tariff $/kWh, or None. None whenever the
                       authority is not charged_tariff, and None on an absent
                       span. NEVER a comparison number.
      comparison_rate  the PRINTED comparison $/kWh, populated only when the
                       authority is printed_comparison_only. NEVER a tariff.
      authority        one of AUTHORITIES.
      provider         the generation provider in force ("bundled" / "CCA").
      tier             evidence tier of the underlying printed observation:
                       "direct" (a positive-kWh line printed it), "carried"
                       (identical flanking observations across a gap) or
                       "absent" (no bill-sourced value; both value slots None).
    """
    __slots__ = ()

    @property
    def is_charged_tariff(self):
        """True only for a value the utility or CCA actually charged."""
        return self.authority == CHARGED_TARIFF and self.rate is not None

    @property
    def classification(self):
        """The four-way label: direct | carried | absent | comparison.

        "comparison" swallows the tier on purpose — a directly-observed printed
        comparison rate is still not a tariff, so it must not be counted beside
        the directly-observed tariffs."""
        if self.tier == "absent":
            return "absent"
        return "comparison" if self.authority == COMPARISON_ONLY else self.tier

    def _value_repr(self):
        if self.rate is not None:
            return f"rate={self.rate:.5f}"
        if self.comparison_rate is not None:
            return f"comparison_rate={self.comparison_rate:.5f} (NOT a tariff)"
        return "no bill-sourced value"


class CellValue(_Valued):
    """One rate cell on one DATE, with the authority of its value (see _Valued)."""
    __slots__ = ("section", "season", "tou_period", "date", "provider",
                 "authority", "rate", "comparison_rate", "tier", "note")

    def __init__(self, section, season, tou_period, date, provider, authority,
                 rate, comparison_rate, tier, note):
        self.section, self.season, self.tou_period = section, season, tou_period
        self.date, self.provider, self.authority = date, provider, authority
        self.rate, self.comparison_rate = rate, comparison_rate
        self.tier, self.note = tier, note

    def __repr__(self):
        return (f"<CellValue {self.section}/{self.season}/{self.tou_period} "
                f"{self.date} provider={self.provider} {self.authority} "
                f"tier={self.tier} {self._value_repr()}>")


class VintageEntry(_Valued):
    """One cell's last directly observed value and the DATES it was printed over,
    with the authority of that value (see _Valued)."""
    __slots__ = ("section", "season", "tou_period", "start", "end", "provider",
                 "authority", "rate", "comparison_rate", "tier", "note")

    def __init__(self, section, season, tou_period, start, end, provider,
                 authority, rate, comparison_rate, tier, note):
        self.section, self.season, self.tou_period = section, season, tou_period
        self.start, self.end, self.provider = start, end, provider
        self.authority = authority
        self.rate, self.comparison_rate = rate, comparison_rate
        self.tier, self.note = tier, note

    def __repr__(self):
        return (f"<VintageEntry {self.section}/{self.season}/{self.tou_period} "
                f"{self.start}..{self.end} provider={self.provider} "
                f"{self.authority} tier={self.tier} {self._value_repr()}>")


def _provider_runs(eng):
    """[(provider, start, end)] — maximal constant-provider runs over the corpus,
    merged from bill_periods_electric.generation_provider. The authority of a
    generation value changes exactly at these boundaries, so anything that
    reports a DATE RANGE has to respect them."""
    runs = []
    for p in eng.periods:
        if runs and runs[-1][0] == p["provider"]:
            runs[-1][2] = p["end"]
        else:
            runs.append([p["provider"], p["start"], p["end"]])
    return [tuple(r) for r in runs]


def _providers_over(eng, a, b):
    """The distinct providers in force across [a, b], chronologically."""
    return tuple(prov for prov, s, e in _provider_runs(eng) if s <= b and e >= a)


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------
class RateSet:
    """The tariff in force on one date. Every component is either a bill-sourced
    value or a flagged absence that raises (naming the date and the cell) when
    read — never an invented number."""

    def __init__(self, eng, date):
        self.date = date
        self._eng = eng
        self._period = eng.period_covering(date)
        self.provider = self._period["provider"]

    def _cell(self, section, season, tou_period):
        key = (section, season, tou_period)
        if season not in SEASONS or tou_period not in TOU_PERIODS:
            raise SystemExit(f"unknown rate cell {'/'.join(key)} (seasons: {SEASONS}; "
                             f"TOU periods: {TOU_PERIODS})")
        span = self._eng.span_at(key, self.date)
        if span.tier == "absent":
            raise SystemExit(
                f"no bill-sourced rate for {'/'.join(key)} on {self.date}: {span.note}. "
                "Refusing to interpolate (issue #2 requirement 5).")
        return span.rate

    def delivery(self, season, tou_period):
        """UDC $/kWh (bill_tou_detail section='delivery')."""
        return self._cell("delivery", season, tou_period)

    def generation(self, season, tou_period):
        """Generation $/kWh SDG&E CHARGED (bill_tou_detail section='generation').

        BUNDLED dates only. On a CCA date this REFUSES: the generation TOU table
        printed on a CCA statement is SDG&E's bundled-generation (EECC)
        comparison, not the tariff that was charged, so there is no charged
        generation rate for this module to return (cca_generation() refuses for
        the complementary reason — the CCA's per-TOU rates are not in any
        committed artifact). To reconstruct the PRINTED line instead of the
        charged tariff, ask for that by name: generation_comparison_table()."""
        if self.provider == "CCA":
            billed = self._period["cca_total"]
            raise SystemExit(
                f"generation/{season}/{tou_period} on {self.date}: period "
                f"{self._period['period']} was billed by a CCA "
                f"(generation_provider={self.provider}), so the generation TOU "
                "table printed on that statement is SDG&E's bundled-generation "
                "(EECC) COMPARISON, not the tariff charged — the CCA charged "
                + (f"${billed:.2f}" if billed else
                   "an amount bill_periods_electric.csv does not carry")
                + " of generation for the period, and its per-TOU rates appear "
                "only on the CCA bill pages, which parse_bills.py does not "
                "extract (RateSet.cca_generation refuses for that reason; "
                "cca_generation_gap measures how far the printed table is from "
                "the charge). Returning the printed comparison rate as the tariff "
                "would be a plausible-looking wrong number, so this refuses. To "
                "reconstruct the PRINTED statement line deliberately, call "
                "generation_comparison_table(season, tou_period).")
        return self._cell("generation", season, tou_period)

    def generation_comparison_table(self, season, tou_period):
        """DIAGNOSTIC, NOT A TARIFF: the generation $/kWh this date's statement
        PRINTED in SDG&E's "Electricity Generation (Details below)" TOU table.

        On a BUNDLED date that is also what SDG&E charged, and generation()
        returns the same number. On a CCA date it is SDG&E's bundled-generation
        (EECC) comparison, printed for reference beside a bill whose generation
        the CCA charged at rates no committed artifact carries: Σ kWh × these
        rates does not reproduce the CCA charge, and cca_generation_gap()
        measures by how much (1/28/26-2/26/26 prints $116.09 against a $56.82
        charge). The only legitimate use is reconstructing PRINTED statement
        lines — which is what the re-billing, holdout and CCA-gap checks do, and
        they say so. Never read this as the tariff in force; for that,
        generation() refuses on CCA dates and so does cca_generation()."""
        return self._cell("generation", season, tou_period)

    def cca_generation(self, season, tou_period):
        raise SystemExit(
            f"cca_generation/{season}/{tou_period} on {self.date}: the CCA's own "
            "per-TOU rates appear only on the CCA bill pages, which parse_bills.py "
            "does not extract — only the period total (bill_periods_electric."
            "cca_generation) is committed. Not sourceable; refusing to guess.")

    def pcia(self):
        raise SystemExit("PCIA on " + str(self.date) + ": " + _ABSENT_PCIA_NBC.format(
            name="PCIA", mech="PCIA applies to NET kWh"))

    def nbc(self):
        raise SystemExit("NBC on " + str(self.date) + ": " + _ABSENT_PCIA_NBC.format(
            name="non-bypassable-charge", mech="NBC applies to GROSS imported kWh"))

    def bsc_per_day(self):
        """Base Services Charge $/day = the covering period's printed total ÷ days."""
        total = self._period["bsc_total"]
        if total is None:
            raise SystemExit(
                f"base_services_charge on {self.date}: bill_periods_electric.csv "
                f"carries no Base Services Charge for period {self._period['period']} "
                "— the column is populated only from the 10/1/25–10/27/25 period "
                "onward, and whether the charge is new, renamed from another line, "
                "or a parser gap is an OPEN question (issue #2). Refusing to invent "
                "a value.")
        return total / self._period["days"]

    def cells(self):
        """{(section, season, tou_period): CellValue} — all 12 cells, for reporting.

        The aggregation of _cell()/generation(), and it carries the SAME trust
        boundary rather than reading values straight off the timeline. On a CCA
        date the three generation cells come back with authority
        "printed_comparison_only", .rate None and SDG&E's printed
        bundled-generation comparison in .comparison_rate — so a report that
        loops over cells() and prints .rate cannot publish a comparison number as
        a charged generation tariff, which is the number RateSet.generation()
        refuses to hand out one cell at a time.

        Delivery cells are charged tariffs on every corpus date, CCA included.

        CellValue is not a tuple and not iterable: the old
        `rate, tier, note = cells[cell]` raises TypeError instead of quietly
        binding a comparison rate to `rate`. Use .rate for charged tariffs,
        .comparison_rate for the printed comparison, .authority to tell them
        apart and .classification for the four-way direct/carried/absent/
        comparison label."""
        out = {}
        for sec in SECTIONS:
            for season in SEASONS:
                for tp in TOU_PERIODS:
                    span = self._eng.span_at((sec, season, tp), self.date)
                    auth = _authority_on(sec, self.provider)
                    absent = span.tier == "absent"
                    charged = auth == CHARGED_TARIFF
                    out[(sec, season, tp)] = CellValue(
                        sec, season, tp, self.date, self.provider, auth,
                        None if absent or not charged else span.rate,
                        None if absent or charged else span.rate,
                        span.tier,
                        span.note if charged else span.note + "; " + _COMPARISON_NOTE)
        return out


def rates_on(date):
    """RateSet in force on `date`. Raises outside the corpus — never extrapolates."""
    eng = _engine()
    if not (eng.start <= date <= eng.end):
        raise SystemExit(
            f"rates_on({date}): outside the bill corpus {eng.start}..{eng.end} — "
            "the committed artifacts cannot source a tariff there and this engine "
            "never extrapolates. For projections at the current vintage use rates.py.")
    return RateSet(eng, date)


def coverage():
    """(first, last) covered dates, derived from the artifacts."""
    eng = _engine()
    return eng.start, eng.end


def current_vintage():
    """{(section, season, tou_period): VintageEntry} — each cell's LAST directly
    observed value, the dates the statements printed it over, and its AUTHORITY.

    What 'the current vintage' means from bill evidence alone — but only for the
    components the bills actually charged at the printed rate. Delivery entries
    are charged tariffs in both provider eras and carry .rate; compare them
    against rates.py's UDC (test_rates_history.py does, and they match exactly).

    A GENERATION entry whose printed window touches a CCA period is not a tariff:
    SDG&E printed it as the bundled-generation (EECC) comparison beside a bill the
    CCA charged. Those entries come back with authority
    "printed_comparison_only", .rate None and the printed number in
    .comparison_rate — the same refusal RateSet.generation() makes per cell,
    applied here rather than returning the last direct observation for every cell
    regardless of who charged it. A window that straddles the provider break is
    classified comparison-only in full: a single 'current vintage' entry cannot be
    half charged, and failing closed is the honest half.

    On this corpus, which ends inside the CCA era, that is all six generation
    cells: from bill evidence alone there is NO current charged generation tariff.

    VintageEntry is not a tuple and not iterable (see CellValue) — the old
    `rate, start, end = cv[cell]` unpacking raises TypeError."""
    eng = _engine()
    out = {}
    for cell, spans in eng.timelines.items():
        last = [s for s in spans if s.tier == "direct"][-1]
        section = cell[0]
        providers = _providers_over(eng, last.start, last.end)
        auth = (CHARGED_TARIFF
                if all(_authority_on(section, prov) == CHARGED_TARIFF
                       for prov in providers)
                else COMPARISON_ONLY)
        charged = auth == CHARGED_TARIFF
        out[cell] = VintageEntry(
            section, cell[1], cell[2], last.start, last.end, "+".join(providers),
            auth, last.rate if charged else None,
            None if charged else last.rate, last.tier,
            last.note if charged else last.note + "; " + _COMPARISON_NOTE)
    return out


# ---------------------------------------------------------------------------
# Re-billing
# ---------------------------------------------------------------------------
MODES = ("piecewise", "vintage_collapse", "netting_collapse")


def _printed_line_span(eng, cell, day):
    """The timeline span the PRINTED-line reconstruction should use for one cell.

    Everything under "Re-billing" reconstructs the statements' own printed TOU
    energy lines, so for section='generation' on a CCA date the value here is
    SDG&E's bundled-generation comparison table and NOT the CEA tariff that was
    charged (RateSet.generation refuses to hand that out as a tariff; see
    RateSet.generation_comparison_table). Callers are reconstructing what the
    paper said, which is the only thing these rows support."""
    return eng.span_at(cell, day)


def _printed_line_rate(rs, section, season, tou_period):
    """The $/kWh the statement covering rs.date PRINTED for one TOU cell.

    Deliberately routed through RateSet.generation_comparison_table for
    generation, because a printed-line reconstruction wants the printed number
    even on a CCA date, where it is SDG&E's comparison table rather than the
    tariff charged. Raises (naming the date and the cell) on an absent cell."""
    if section == "generation":
        return rs.generation_comparison_table(season, tou_period)
    if section == "delivery":
        return rs.delivery(season, tou_period)
    raise SystemExit(f"_printed_line_rate: unknown section {section!r} "
                     f"(expected {SECTIONS})")


def _block_end(eng, period, section, season):
    """Last day of a statement's (section, season) block — the vintage a
    single-vintage re-bill of that block would wrongly use throughout."""
    return max(e for (per, sec, sea, i), (s, e) in eng.segments.items()
               if per == period and sec == section and sea == season)


def rebill_statement(period, mode="piecewise"):
    """Re-price one statement's printed TOU energy lines from its own per-TOU kWh.

    A PRINTED-LINE reconstruction throughout: on a CCA statement the generation
    rows here are SDG&E's bundled-generation comparison table, not the CEA tariff
    charged (the values come from RateSet.generation_comparison_table /
    _printed_line_span by name, since RateSet.generation refuses on CCA dates).
    Nothing in this function is a claim about the CCA's tariff.

    printed   = Σ kWh × the rate THIS STATEMENT printed on those lines, over its
                positive TOU buckets. This is the reference, and it is the
                statement's own printed table — not an independent dollar total
                (see the module docstring: none exists in committed data).
                Net-negative buckets print $0 — NEM 2.0 in-period exports settle
                at true-up, not on the statement.
    rebilled  = the same kWh priced through a rate timeline:
        mode="piecewise"         each segment at the rates the engine holds for
                                 that segment's dates (as billed). Since the
                                 timeline is built from these very rows, the
                                 residual is zero by identity — a reconstruction
                                 check, not a validation. rebill_holdout() is the
                                 version that can move.
        mode="vintage_collapse"  the RATE-ONLY counterfactual: identical segment
                                 kWh and the identical positive-only rule, the
                                 ONLY change being the vintage — every row priced
                                 at the rates in force on the LAST day of its
                                 (section, season) block. Any residual is
                                 attributable to collapsing vintages and nothing
                                 else. This is the piecewise-billing proof. Rows
                                 whose block-end cell is a flagged absence cannot
                                 be re-priced at all (the collapsed-to vintage is
                                 not bill-sourced there); they are dropped from
                                 BOTH sides and counted in unpriced_rows, so the
                                 residual stays a like-for-like comparison
                                 against printed_basis rather than an invented
                                 rate.
        mode="netting_collapse"  a NETTING counterfactual, kept separate because
                                 it changes two things at once: it sums signed
                                 kWh across the printed segments, re-applies the
                                 positive-only rule to that sum, and then also
                                 prices at the block's last day.
    Returns {period, statement_date, days, provider, n_segments, printed,
    printed_basis, unpriced_rows, rebilled, residual, residual_pct}, where
    printed_basis is the printed dollars the mode could actually re-price
    (== printed except in vintage_collapse) and the residual is against it.
    """
    if mode not in MODES:
        raise SystemExit(f"rebill_statement: unknown mode {mode!r} (expected {MODES})")
    eng = _engine()
    p = next((x for x in eng.periods if x["period"] == period), None)
    if p is None:
        raise SystemExit(f"rebill_statement: no billing period {period!r} in the corpus")
    rows = [r for r in eng.tou if r["period"] == period]
    printed = sum(r["kwh"] * r["rate"] for r in rows if r["kwh"] > 0)
    # rate segments per season block (2 = a mid-cycle tariff change split the bill)
    per_block = {}
    for r in rows:
        per_block.setdefault((r["section"], r["season"]), set()).add(r["segment"])
    n_segments = max(len(v) for v in per_block.values())
    basis, unpriced = printed, 0
    if mode == "netting_collapse":
        net = {}
        for r in rows:
            key = (r["section"], r["season"], r["tou_period"])
            net[key] = net.get(key, 0.0) + r["kwh"]
        rebilled = 0.0
        for (section, season, tp), kwh in sorted(net.items()):
            if kwh <= 0:
                continue
            end = _block_end(eng, period, section, season)
            rebilled += kwh * _printed_line_rate(RateSet(eng, end), section, season, tp)
    elif mode == "vintage_collapse":
        rebilled = basis = 0.0
        for r in rows:
            if r["kwh"] <= 0:
                continue
            cell = (r["section"], r["season"], r["tou_period"])
            span = _printed_line_span(
                eng, cell, _block_end(eng, period, r["section"], r["season"]))
            if span.tier == "absent":
                unpriced += 1
                continue
            basis += r["kwh"] * r["rate"]
            rebilled += r["kwh"] * span.rate
    else:
        rebilled = 0.0
        for r in rows:
            if r["kwh"] <= 0:
                continue
            cell = (r["section"], r["season"], r["tou_period"])
            s, e = eng.segments[(period, r["section"], r["season"], r["segment"])]
            first = _printed_line_span(eng, cell, s)
            last = _printed_line_span(eng, cell, e)
            if first is not last:
                raise SystemExit(f"[{period}] {'/'.join(cell)}: the engine's timeline "
                                 f"changes rate inside bill segment {s}..{e} — the "
                                 "vintage table disagrees with the bill's segmentation")
            if first.tier == "absent":
                raise SystemExit(f"[{period}] {'/'.join(cell)}: a billed positive "
                                 f"bucket sits on an absent span — construction bug")
            rebilled += r["kwh"] * first.rate
    residual = rebilled - basis
    return dict(period=period, statement_date=p["statement_date"], days=p["days"],
                provider=p["provider"], n_segments=n_segments, printed=printed,
                printed_basis=basis, unpriced_rows=unpriced, rebilled=rebilled,
                residual=residual,
                # None, never a division, if the mode could price nothing at all
                residual_pct=(100.0 * residual / basis) if basis else None)


# Why a printed line can have no independent witness. These are properties of the
# corpus, not verdicts on the rate: none of them can be repaired by trying harder,
# and none of them may be reported as agreement.
UNCORROBORATED_REASONS = (
    "no_witness_anywhere_for_this_cell",                 # cell billed only here
    "first_witness_is_later_than_this_statement",         # corpus edge (start)
    "last_witness_is_earlier_than_this_statement",        # corpus edge (end)
    "flanking_witnesses_disagree_the_change_is_undated",  # 1st appearance of a rate
    "witness_span_boundary_inside_the_printed_segment",   # holdout dates a change here
)


def corroboration(period):
    """Per-printed-line corroboration status for one statement: what the REST of the
    corpus can and cannot independently say about the rates this statement printed.

    One row per positive-kWh TOU energy line (a (cell, segment) pair, priced over
    that segment's dates). The timeline rebuilt WITHOUT the rate rows of the
    STATEMENT that printed this period is asked for the same cell over the same
    dates. The unit is the statement, not the period: one statement can print two
    billing periods (the 2025-10-31 statement prints 9/26/25-9/30/25 and
    10/1/25-10/27/25), and a sibling period parsed out of the same file shares every
    failure mode with the line under test, so it is withheld too rather than allowed
    to corroborate it (_Engine, and the module docstring's holdout section):

      status "corroborated"    the holdout timeline holds ONE rate across the whole
            printed segment — an independent witness exists. `agrees` is True only
            if that witness rate equals the printed rate EXACTLY. A disagreement is
            a finding: the rest of the corpus contradicts this statement's line.
            The witness is always a CARRIED span, i.e. two other statements flanking
            these dates that printed the SAME rate; no other statement bills these
            dates, so a DIRECT witness is impossible by construction and `tier`
            records which it was rather than implying the stronger one.
            `witness_statements` names those statements, so the provenance can be
            checked — in particular that the held-out statement itself (which may
            print a sibling period of the period under test) never appears there.
      status "uncorroborated"  no such witness exists, and `reason` (one of
            UNCORROBORATED_REASONS) says which shape of hole it is.

    This is the honesty constraint the coverage percentage could not express: a rate
    whose FIRST appearance in the corpus is the line under test cannot be
    corroborated by holding that line out — the corpus simply has no second opinion
    — and the same is true at the corpus edges. Those lines are reported as
    uncorroborated BY CONSTRUCTION. They are neither failures nor passes, and they
    are never folded into an aggregate that a stable repeated rate could carry.

    Each corroborated row also carries `witness_statements` (the statement_dates
    whose printed lines evidence the witness rate) and `held_out_statement`.

    Returns {period, held_out_statement, rows, corroborated_rows, agree_rows,
    disagree_rows,
    uncorroborated_rows, reasons {reason: count}, printed_usd,
    corroborated_printed_usd, corroborated_witness_usd, direct_witness_rows,
    differing_cells, differing_corroborated_rows, differing_uncorroborated_rows},
    where differing_* covers the cells whose printed rate is NOT the same in both
    segments of a split statement — the mid-cycle change itself, which is exactly
    the thing a holdout of that statement cannot see.

    Every row is a PRINTED line, and printed_rate / witness_rate are printed
    numbers by definition — on a CCA statement's generation rows they are SDG&E's
    bundled-generation comparison, not the tariff charged. Each row carries
    `authority` (CHARGED_TARIFF / COMPARISON_ONLY) so that is visible per row and
    not only in this docstring.
    """
    eng = _engine()
    p = next((x for x in eng.periods if x["period"] == period), None)
    if p is None:
        raise SystemExit(f"corroboration: no billing period {period!r} in the corpus")
    hold = _holdout_engine(p["statement_date"])
    billed = [r for r in eng.tou if r["period"] == period and r["kwh"] > 0]
    # the mid-cycle change on a split statement: cells printing two rates
    printed_rates = {}
    for r in billed:
        printed_rates.setdefault(
            (r["section"], r["season"], r["tou_period"]), set()).add(r["rate"])
    differing = {cell for cell, rs in printed_rates.items() if len(rs) > 1}
    rows, reasons = [], {}
    for r in billed:
        cell = (r["section"], r["season"], r["tou_period"])
        s, e = eng.segments[(period, r["section"], r["season"], r["segment"])]
        first, last = hold.span_at(cell, s), hold.span_at(cell, e)
        one_span = first is last and first.tier != "absent"
        reason = ""
        if not one_span:
            if not any(sp.tier == "direct" for sp in hold.timelines[cell]):
                reason = "no_witness_anywhere_for_this_cell"
            elif first is not last:
                reason = "witness_span_boundary_inside_the_printed_segment"
            elif first.start == hold.start:
                reason = "first_witness_is_later_than_this_statement"
            elif first.end == hold.end:
                reason = "last_witness_is_earlier_than_this_statement"
            else:
                reason = "flanking_witnesses_disagree_the_change_is_undated"
            reasons[reason] = reasons.get(reason, 0) + 1
        rows.append(dict(
            cell=cell, segment=r["segment"], start=s, end=e, kwh=r["kwh"],
            authority=_authority_on(r["section"], p["provider"]),
            printed_rate=r["rate"], printed_usd=r["kwh"] * r["rate"],
            status="corroborated" if one_span else "uncorroborated",
            reason=reason, differing_cell=cell in differing,
            witness_tier=first.tier if one_span else "",
            # which STATEMENTS evidence the witness — never the held-out one
            witness_statements=first.sources if one_span else (),
            witness_rate=first.rate if one_span else None,
            witness_usd=r["kwh"] * first.rate if one_span else None,
            agrees=(first.rate == r["rate"]) if one_span else None))
    corr = [x for x in rows if x["status"] == "corroborated"]
    unc = [x for x in rows if x["status"] == "uncorroborated"]
    return dict(
        period=period, held_out_statement=p["statement_date"], rows=rows,
        corroborated_rows=len(corr),
        agree_rows=sum(1 for x in corr if x["agrees"]),
        disagree_rows=sum(1 for x in corr if not x["agrees"]),
        uncorroborated_rows=len(unc), reasons=reasons,
        direct_witness_rows=sum(1 for x in corr if x["witness_tier"] == "direct"),
        printed_usd=sum(x["printed_usd"] for x in rows),
        corroborated_printed_usd=sum(x["printed_usd"] for x in corr),
        corroborated_witness_usd=sum(x["witness_usd"] for x in corr),
        differing_cells=len(differing),
        differing_corroborated_rows=sum(1 for x in corr if x["differing_cell"]),
        differing_uncorroborated_rows=sum(1 for x in unc if x["differing_cell"]))


def rebill_holdout(period):
    """Re-price one statement against a timeline built WITHOUT its own rate rows.

    Every rate row of the statement that printed this period is withheld — the
    unit is the statement (the file), not the billing period, so a sibling period
    printed on the same statement cannot witness this one (see corroboration()).

    The one per-statement check here that can move: the prices come from the
    other statements, so a misread rate, column or segment on this statement
    shows up as a residual instead of cancelling out. It prices exactly the lines
    corroboration() calls corroborated, and counts — never guesses — the rest,
    which are uncorroborated BY CONSTRUCTION (that function's docstring lists the
    five shapes; a mid-cycle change printed only on this bill is one of them).
    A PRINTED-LINE reconstruction: on a CCA statement the generation lines are
    SDG&E's bundled-generation comparison table, not the CEA tariff charged.

    Returns {period, printed_priced, rebilled, residual, residual_pct|None,
    priced_pct, uncorroborated_rows} where the residual is over the CORROBORATED
    subset and printed_priced is that subset's printed dollars. priced_pct is
    descriptive only: it is NOT a gate, because a corpus of stable repeated rates
    would clear any threshold on it while an uncorroborated new vintage sat
    entirely outside it (see corroboration()).
    """
    c = corroboration(period)
    priced, rebilled = c["corroborated_printed_usd"], c["corroborated_witness_usd"]
    residual = rebilled - priced
    return dict(period=period, printed_priced=priced, rebilled=rebilled,
                residual=residual,
                residual_pct=(100.0 * residual / priced) if priced else None,
                priced_pct=(100.0 * priced / c["printed_usd"])
                if c["printed_usd"] else 0.0,
                uncorroborated_rows=c["uncorroborated_rows"])


def cca_generation_gap(period):
    """Printed generation TOU dollars vs the CCA generation charge actually billed.

    Σ(printed generation kWh × printed rate) − bill_periods_electric.cca_generation
    for a CCA statement. The left side is a deliberate reconstruction of the PRINTED
    lines — SDG&E's bundled-generation comparison table, the thing
    RateSet.generation_comparison_table() hands out and RateSet.generation()
    refuses to call a tariff. The two sides are not the same tariff, and this
    measures by how much: it is the evidence that RateSet.generation() and
    RateSet.cca_generation() must both fail closed rather than reuse the printed
    comparison table. Returns None for bundled statements,
    where SDG&E's printed generation table IS what it billed and no CCA charge
    exists.
    """
    eng = _engine()
    p = next((x for x in eng.periods if x["period"] == period), None)
    if p is None:
        raise SystemExit(f"cca_generation_gap: no billing period {period!r} in the corpus")
    if p["provider"] != "CCA":
        return None
    printed = sum(r["kwh"] * r["rate"] for r in eng.tou
                  if r["period"] == period and r["section"] == "generation"
                  and r["kwh"] > 0)
    billed = p["cca_total"]
    if not billed:
        raise SystemExit(
            f"[{period}] is a CCA statement but bill_periods_electric.csv carries no "
            "cca_generation charge for it — the printed-vs-billed generation gap "
            "cannot be measured; refusing to report a gap against a missing total.")
    return dict(period=period, printed_generation=printed, cca_billed=billed,
                gap=printed - billed, gap_pct=100.0 * (printed - billed) / billed)


def _refuse_cca_dates(frame, caller):
    """Fail closed if any date in the frame falls in a CCA period.

    delivery + generation is a real tariff only while SDG&E was the bundled
    provider. From 12/27/24 the generation TOU table on the statement is SDG&E's
    bundled-generation (EECC) COMPARISON, not the CEA rates that were charged;
    the committed artifacts carry only the CCA period TOTAL
    (bill_periods_electric.cca_generation), never per-TOU CCA rates, which is why
    RateSet.cca_generation() also refuses. Also raises (via rates_on) on any date
    outside the corpus."""
    for day in sorted({d for d in frame["dt"].dt.date.unique()}):
        rs = rates_on(day)                              # off-corpus raises here
        if rs.provider == "CCA":
            p = rs._period
            raise SystemExit(
                f"{caller}: {day} falls in the CCA period {p['period']} (provider "
                f"{rs.provider}) — the generation TOU table printed on a CCA "
                "statement is SDG&E's bundled-generation comparison, not the CEA "
                "tariff that was charged, and the committed artifacts carry only "
                "that period's CCA generation TOTAL (bill_periods_electric."
                f"cca_generation = ${p['cca_total']:.2f}), not per-TOU CCA rates "
                "(RateSet.cca_generation refuses for the same reason). Adding "
                "printed delivery + printed generation "
                "here would return a plausible-looking wrong number, so this engine "
                "refuses. Bundled dates (through 2024-12-26) price normally; pass "
                "delivery_only=True to price the UDC delivery component alone on "
                "any date, and label the result as containing no generation.")


def bill_nem_monthly(frame, imp="Consumption", exp="Generation", delivery_only=False):
    """{month: $} of TOU ENERGY charges at the historical vintages — the engine-swap
    counterpart of rates.bill_nem_monthly (same signature plus delivery_only; frame
    needs the same columns: dt, seas 'S'/'W', p 'on'/'off'/'sop', ym, imp, exp).

    NOT a general historical bill replacement. Differences, all documented in the
    module docstring: netting is per maximal constant-rate span within the month
    (how the bills segment a mid-cycle change), net-negative buckets contribute $0
    (in-period NEM exports settle at true-up), and NO PCIA/NBC/BSC is added —
    those are not artifact-sourced.

    Default (delivery_only=False) prices delivery + generation and therefore works
    only on BUNDLED dates: a frame touching any CCA period (12/27/24 onward) is
    refused, naming the date and the period, because the printed generation table
    is then SDG&E's comparison rather than the CEA tariff charged. delivery_only=
    True prices the UDC delivery component ALONE, on any corpus date — the result
    then excludes all generation and must be labeled that way.
    Raises on any date outside the corpus or any absent cell (fail closed)."""
    if not delivery_only:
        _refuse_cca_dates(frame, "bill_nem_monthly")
    out = {}
    for ym, m in frame.groupby("ym"):
        tot = 0.0
        for (seas, p_short), sub in m.groupby(["seas", "p"]):
            season = _SEASON_FOR_SEAS.get(seas)
            long_tp = _LONG_FOR_SHORT.get(p_short)
            if season is None or long_tp is None:
                raise SystemExit(f"bill_nem_monthly: unknown season/period labels "
                                 f"({seas!r}, {p_short!r}) — rates.py conventions "
                                 "expected")
            def rate_pair(day):
                rs = rates_on(day)
                if delivery_only:
                    return rs.delivery(season, long_tp)
                return (rs.delivery(season, long_tp) + rs.generation(season, long_tp))
            spans = {}
            for day, dsub in sub.groupby(sub.dt.dt.date):
                rate = rate_pair(day)
                spans[rate] = spans.get(rate, 0.0) + dsub[imp].sum() - dsub[exp].sum()
            for rate, net in sorted(spans.items()):
                if net > 0:
                    tot += net * rate
        out[str(ym)] = tot
    return out


def bill_nem(frame, imp="Consumption", exp="Generation", delivery_only=False):
    """Annual $ (sum of bill_nem_monthly) — signature-compatible with
    rates.bill_nem, and subject to every restriction in bill_nem_monthly's
    docstring: TOU energy only, and delivery + generation on bundled dates only
    (CCA dates refused unless delivery_only=True, which drops generation)."""
    return sum(bill_nem_monthly(frame, imp, exp, delivery_only).values())


# ---------------------------------------------------------------------------
# Generator: the committed artifacts
# ---------------------------------------------------------------------------
def _fmt_money(v, places=6):
    s = f"{v:.{places}f}"
    return s.lstrip("-") if float(s) == 0 else s     # never print "-0.000000"


def _vintage_rows():
    """Rows of data/rate_vintages.csv — charged tariffs and printed comparisons in
    separate sections and separate value columns.

    A generation span is SPLIT at every provider boundary, because the authority of
    the printed number changes there while the number itself does not (the winter
    on-peak 0.16516 span 2024-11-01..2025-01-31 is a charged SDG&E tariff through
    12/26/24 and a printed comparison from 12/27/24). The CCA side is serialized
    under section=generation_printed_comparison with rate_per_kwh EMPTY and the
    printed value in comparison_rate_per_kwh, so a consumer reading rate_per_kwh —
    or filtering section == "generation" — receives charged tariffs only. Delivery
    spans are not split: the UDC is the charged tariff in both eras, so their
    provider column reads "bundled+CCA" where the span straddles the break.

    Note wording follows the same rule: a direct span's note says which statements
    PRINTED the rate, and the authority column says whether printing it and
    charging it were the same thing. Nothing claims a CCA-era generation rate was
    "billed on" anything."""
    eng = _engine()
    runs = _provider_runs(eng)
    rows = []
    for cell in sorted(eng.timelines):
        section = cell[0]
        for span in eng.timelines[cell]:
            # generation: one row per provider run the span overlaps; delivery:
            # one row, with every provider it spans named
            pieces = ([(prov, max(s, span.start), min(e, span.end))
                       for prov, s, e in runs
                       if s <= span.end and e >= span.start]
                      if section == "generation"
                      else [("+".join(_providers_over(eng, span.start, span.end)),
                             span.start, span.end)])
            for provider, start, end in pieces:
                auth = _authority_on(section, provider)
                charged = auth == CHARGED_TARIFF
                value = "" if span.rate is None else f"{span.rate:.5f}"
                note = span.note
                if (start, end) != (span.start, span.end):
                    # the note's statement list belongs to the whole observed
                    # span, so say which slice of it this row is
                    note += (f"; this row is the {provider} portion of the observed "
                             f"span {span.start}..{span.end}")
                rows.append([
                    section if charged else COMPARISON_SECTION,
                    cell[1], cell[2], str(start), str(end), provider, auth,
                    value if charged else "",
                    "" if charged else value,
                    span.tier,
                    note if charged else note + "; " + _COMPARISON_NOTE])
    for p in eng.periods:                # BSC: per period, absence made explicit
        rows.append(["base_services_charge", "", "per_day",
                     str(p["start"]), str(p["end"]), p["provider"], CHARGED_TARIFF,
                     "" if p["bsc_total"] is None
                     else f"{p['bsc_total'] / p['days']:.5f}", "",
                     "absent" if p["bsc_total"] is None else "derived",
                     "no base_services_charge line parsed for this period — new "
                     "charge, renamed line, or parser gap is an OPEN question"
                     if p["bsc_total"] is None else
                     f"period total {p['bsc_total']:.2f} / {p['days']} days "
                     f"(the printed daily unit rate is not captured by the parser)"])
    for p in eng.periods:                # provider timeline: a label, not a rate
        rows.append(["provider", "", "", str(p["start"]), str(p["end"]),
                     p["provider"], NOT_A_RATE, "", "", "direct", p["provider"]])
    return rows


def _residual_rows():
    eng = _engine()
    results = []
    for p in eng.periods:
        per = p["period"]
        results.append((rebill_statement(per),
                        rebill_statement(per, "vintage_collapse"),
                        rebill_statement(per, "netting_collapse"),
                        rebill_holdout(per),
                        cca_generation_gap(per),
                        corroboration(per)))
    # worst = largest |timeline_residual_pct|. That column is zero by identity
    # (module docstring), so in practice the tie breaks on the holdout residual —
    # the per-statement check that can actually move — and then on the statement
    # date, so the label stays stable as the corpus grows.
    worst = sorted(results, key=lambda t: (-abs(t[0]["residual_pct"]),
                                           -abs(t[3]["residual_pct"] or 0.0),
                                           t[0]["statement_date"]))[0][0]["period"]
    rows = []
    for piece, vint, netc, hold, gap, corr in results:
        rows.append([piece["statement_date"], piece["period"], piece["days"],
                     piece["provider"], piece["n_segments"],
                     f"{piece['printed']:.2f}",
                     f"{piece['rebilled']:.2f}",
                     _fmt_money(piece["residual"]),
                     _fmt_money(piece["residual_pct"], 4),
                     f"{hold['printed_priced']:.2f}",
                     f"{hold['rebilled']:.2f}",
                     _fmt_money(hold["priced_pct"], 2),
                     corr["corroborated_rows"], corr["agree_rows"],
                     corr["disagree_rows"], corr["uncorroborated_rows"],
                     ";".join(f"{k}:{v}" for k, v in sorted(corr["reasons"].items())),
                     corr["differing_cells"],
                     corr["differing_corroborated_rows"],
                     corr["differing_uncorroborated_rows"],
                     _fmt_money(hold["residual"]),
                     "" if hold["residual_pct"] is None
                     else _fmt_money(hold["residual_pct"], 4),
                     f"{vint['printed_basis']:.2f}",
                     f"{vint['rebilled']:.2f}",
                     vint["unpriced_rows"],
                     "" if vint["residual_pct"] is None
                     else _fmt_money(vint["residual_pct"], 4),
                     f"{netc['rebilled']:.2f}",
                     _fmt_money(netc["residual_pct"], 4),
                     "" if gap is None else f"{gap['printed_generation']:.2f}",
                     "" if gap is None else f"{gap['cca_billed']:.2f}",
                     "" if gap is None else _fmt_money(gap["gap"], 2),
                     "" if gap is None else _fmt_money(gap["gap_pct"], 2),
                     "worst" if piece["period"] == worst else ""])
    return rows


def _write_artifacts(dest_dir):
    """Both artifacts, deterministically, via tempfile + os.replace (atomic on the
    same filesystem). Returns the two paths."""
    dest_dir = pathlib.Path(dest_dir)
    targets = [
        # rate_per_kwh holds CHARGED tariffs only. A printed CCA-generation
        # comparison rate — a number SDG&E printed for reference beside a bill the
        # CCA charged — is published under section generation_printed_comparison in
        # comparison_rate_per_kwh, with authority=printed_comparison_only, and the
        # two value columns are never both populated (_vintage_rows).
        (dest_dir / "rate_vintages.csv",
         ["section", "season", "tou_period", "vintage_start", "vintage_end",
          "provider", "authority", "rate_per_kwh", "comparison_rate_per_kwh",
          "evidence", "note"], _vintage_rows()),
        # Column names say what the reference is. printed_tou_energy_usd is the
        # statement's OWN printed TOU energy lines; timeline_* re-prices those
        # same kWh through the timeline built from those same rows, so
        # timeline_residual_* is a reconstruction identity, not a dollar
        # validation (module docstring). holdout_* withholds the statement's own
        # rates and CAN move; holdout_corroborated_*/holdout_uncorroborated_*
        # say per statement which printed lines the rest of the corpus
        # independently witnesses and, for the rest, why it cannot
        # (corroboration()); split_differing_* isolates the mid-cycle change
        # itself. cca_generation_gap_* is the one independent comparison the
        # committed artifacts support.
        (dest_dir / "rate_rebilling_residuals.csv",
         ["statement_date", "period", "days", "provider", "n_segments",
          "printed_tou_energy_usd", "timeline_energy_usd",
          "timeline_residual_usd", "timeline_residual_pct",
          "holdout_printed_priced_usd", "holdout_energy_usd",
          "holdout_priced_pct",
          "holdout_corroborated_rows", "holdout_corroborated_agree_rows",
          "holdout_corroborated_disagree_rows", "holdout_uncorroborated_rows",
          "holdout_uncorroborated_reasons", "split_differing_cells",
          "split_differing_corroborated_rows",
          "split_differing_uncorroborated_rows",
          "holdout_residual_usd", "holdout_residual_pct",
          "vintage_collapse_basis_usd", "vintage_collapse_energy_usd",
          "vintage_collapse_unpriced_rows", "vintage_collapse_residual_pct",
          "netting_collapse_energy_usd", "netting_collapse_residual_pct",
          "printed_generation_usd", "cca_billed_generation_usd",
          "cca_generation_gap_usd", "cca_generation_gap_pct",
          "worst_residual"], _residual_rows()),
    ]
    for path, header, rows in targets:
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", newline="") as fh:
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(header)
                w.writerows(rows)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return [t[0] for t in targets]


def main():
    eng = _engine()
    paths = _write_artifacts(DATA)
    directs = sum(1 for spans in eng.timelines.values()
                  for s in spans if s.tier == "direct")
    absents = sum(1 for spans in eng.timelines.values()
                  for s in spans if s.tier == "absent")
    print(f"corpus: {eng.start}..{eng.end} "
          f"({(eng.end - eng.start).days + 1} days, {len(eng.periods)} periods)")
    print(f"cells: {len(eng.timelines)} × timeline "
          f"({directs} direct spans, {absents} flagged-absent spans)")
    vint = _vintage_rows()
    print("rate_vintages rows by authority: "
          + ", ".join(f"{a} {sum(1 for r in vint if r[6] == a)}"
                      for a in AUTHORITIES)
          + f" — of which {sum(1 for r in vint if r[6] == COMPARISON_ONLY)} are "
            "printed CCA-generation comparisons carrying no charged rate")
    corr = [corroboration(p["period"]) for p in eng.periods]
    reasons = {}
    for c in corr:
        for k, v in c["reasons"].items():
            reasons[k] = reasons.get(k, 0) + v
    print(f"holdout corroboration of the printed lines: "
          f"{sum(c['corroborated_rows'] for c in corr)} corroborated "
          f"({sum(c['disagree_rows'] for c in corr)} disagreeing), "
          f"{sum(c['uncorroborated_rows'] for c in corr)} uncorroborated by "
          f"construction — "
          + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    for p in paths:
        print(f"wrote {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
