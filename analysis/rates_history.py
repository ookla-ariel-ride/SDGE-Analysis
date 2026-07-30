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
  * generation $/kWh by season and TOU period — AS PRINTED on the SDG&E bill
        bill_tou_detail.rate_per_kwh where section == "generation". Read the
        caveat below: this is what SDG&E's "Electricity Generation (Details
        below)" TOU table prints. During bundled periods it is what SDG&E
        actually billed. During CCA periods it is SDG&E's bundled-generation
        comparison table, NOT the CCA's tariff: summing kWh × printed rate
        does not reproduce bill_periods_electric.cca_generation (checked:
        1/28/26–2/26/26 prints $116.09 of generation TOU lines against a
        $56.82 CCA charge). The CCA's own per-TOU rates appear only on the
        CCA pages, which parse_bills.py does not extract, so
        RateSet.cca_generation() fails closed.
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
  carries NO rate evidence; the parser records 0.0 there). Observations are
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
    * holdout_* — statement S's own kWh priced by a timeline rebuilt from the
      OTHER 25 statements' rate rows. Independent of S's printed rates, so a
      misread rate on S moves it. It can price only the cells the rest of the
      corpus dates: holdout_priced_pct records the share of S's printed dollars
      that were priceable, and a mid-cycle change visible only on S's own bill
      (the five split statements) is by definition uncorroborated, which is why
      those rows carry the largest holdout residuals.
    * cca_generation_gap_usd — Σ(printed generation kWh × printed rate) minus
      bill_periods_electric.cca_generation, for CCA statements. This is the
      measured evidence that the printed generation table is not the tariff
      charged during CCA periods (1/28/26-2/26/26: $116.09 printed against a
      $56.82 CCA charge).

Generator outputs (deterministic writers, atomic replace; run twice → identical):
    data/rate_vintages.csv              every cell's spans with evidence tier
                                        (direct / carried / absent) + BSC and
                                        provider timelines
    data/rate_rebilling_residuals.csv   per-statement printed TOU energy, the
                                        timeline reconstruction of it, the
                                        holdout re-bill, the two vintage-
                                        collapse counterfactuals and the CCA
                                        generation gap; worst statement flagged
"""
import csv
import datetime as dt
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
    """One constant-rate stretch of a cell's timeline."""
    __slots__ = ("start", "end", "rate", "tier", "note")

    def __init__(self, start, end, rate, tier, note):
        self.start, self.end = start, end
        self.rate, self.tier, self.note = rate, tier, note


class _Engine:
    def __init__(self, periods, tou, exclude_period=None):
        self.periods = periods                      # chronological period dicts
        self.tou = tou                              # validated TOU detail rows
        self.start = periods[0]["start"]
        self.end = periods[-1]["end"]
        # exclude_period builds the HOLDOUT timeline: segment dates still come
        # from every statement (they are calendar facts), but the excluded
        # statement's rate observations are withheld, so pricing it against this
        # timeline is independent of its own printed rates.
        self.exclude_period = exclude_period
        self.segments = self._date_segments(periods, tou)
        self.timelines = self._timelines(
            [r for r in tou if r["period"] != exclude_period]
            if exclude_period is not None else tou)

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
                obs[cell].append((span[0], span[1], r["rate"], r["period"]))
            elif r["rate"] != 0.0:
                raise SystemExit(
                    f"[{r['period']}] {'/'.join(cell)}: a net-negative bucket "
                    f"({r['kwh']} kWh) carries a nonzero printed rate {r['rate']} — "
                    "the credit-lines-print-zero assumption is broken; re-derive.")
        timelines = {}
        for cell, o in obs.items():
            o.sort()
            merged = []                     # [[start, end, rate, [periods]], ...]
            for s, e, rate, period in o:
                if merged and merged[-1][1] >= s:
                    raise SystemExit(f"{'/'.join(cell)}: overlapping rate segments "
                                     f"at {s} — the corpus is not clean")
                if merged and merged[-1][2] == rate and \
                        merged[-1][1] + dt.timedelta(days=1) == s:
                    merged[-1][1] = e
                    merged[-1][3].append(period)
                else:
                    merged.append([s, e, rate, [period]])
            spans, prev = [], None
            name = "/".join(cell)
            # a gap means "no positive-kWh line here"; in a holdout timeline the
            # withheld statement is a second reason, and the note must say so
            gap_why = ("net-negative gap" if self.exclude_period is None else
                       f"gap with {self.exclude_period} held out")
            if not merged:
                if self.exclude_period is not None:
                    timelines[cell] = [_Span(
                        self.start, self.end, None, "absent",
                        "no positive-kWh line outside the held-out statement "
                        + self.exclude_period)]
                    continue
                raise SystemExit(f"{name}: no positive-kWh line anywhere in the corpus")
            if merged[0][0] > self.start:
                spans.append(_Span(self.start, merged[0][0] - dt.timedelta(days=1),
                                   None, "absent",
                                   "no positive-kWh line before "
                                   f"{merged[0][0]} (net-negative buckets print $0)"))
            for s, e, rate, srcs in merged:
                if prev is not None:
                    gap0 = prev.end + dt.timedelta(days=1)
                    if gap0 < s:
                        gap1 = s - dt.timedelta(days=1)
                        if prev.rate == rate:
                            spans.append(_Span(gap0, gap1, rate, "carried",
                                               f"{gap_why}; flanking observed "
                                               f"rates equal at {rate:.5f}"))
                        else:
                            spans.append(_Span(gap0, gap1, None, "absent",
                                               f"rate changed from {prev.rate:.5f} to "
                                               f"{rate:.5f} somewhere in this gap; no "
                                               "positive-kWh line dates the change"))
                prev = _Span(s, e, rate, "direct",
                             "billed on " + "; ".join(dict.fromkeys(srcs)))
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


def _holdout_engine(period):
    """The engine with `period`'s rate observations withheld (see _Engine)."""
    eng = _engine()
    if period not in _HOLDOUT:
        _HOLDOUT[period] = _Engine(eng.periods, eng.tou, exclude_period=period)
    return _HOLDOUT[period]


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
    for row in csv.DictReader(open(tpath, newline="")):
        tou.append(dict(period=row["period"], section=row["section"],
                        season=row["season"], segment=int(row["segment"]),
                        segment_days=int(row["segment_days"]),
                        tou_period=row["tou_period"], kwh=float(row["kwh"]),
                        rate=float(row["rate_per_kwh"])))
        known.add(row["period"])
    missing = [p["period"] for p in periods if p["period"] not in known]
    if not tou or missing:
        raise SystemExit(f"bill_tou_detail.csv has no rows for: {missing or 'anything'}")
    return periods, tou


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
        """Generation $/kWh AS PRINTED on the SDG&E bill (section='generation').
        Bundled periods: what SDG&E billed. CCA periods: SDG&E's comparison
        table, NOT the CCA tariff — see the module docstring and cca_generation()."""
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
        """{(section, season, tou_period): (rate|None, tier, note)} — for reporting."""
        out = {}
        for sec in SECTIONS:
            for season in SEASONS:
                for tp in TOU_PERIODS:
                    span = self._eng.span_at((sec, season, tp), self.date)
                    out[(sec, season, tp)] = (span.rate, span.tier, span.note)
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
    """{(section, season, tou_period): (rate, start, end)} — each cell's LAST
    directly observed rate and the dates the bills show it billed on. This is
    what 'the current vintage' means from bill evidence alone; compare delivery
    against rates.py's UDC (test_rates_history.py does, to the cent)."""
    eng = _engine()
    out = {}
    for cell, spans in eng.timelines.items():
        last = [s for s in spans if s.tier == "direct"][-1]
        out[cell] = (last.rate, last.start, last.end)
    return out


# ---------------------------------------------------------------------------
# Re-billing
# ---------------------------------------------------------------------------
MODES = ("piecewise", "vintage_collapse", "netting_collapse")


def _block_end(eng, period, section, season):
    """Last day of a statement's (section, season) block — the vintage a
    single-vintage re-bill of that block would wrongly use throughout."""
    return max(e for (per, sec, sea, i), (s, e) in eng.segments.items()
               if per == period and sec == section and sea == season)


def rebill_statement(period, mode="piecewise"):
    """Re-price one statement's printed TOU energy lines from its own per-TOU kWh.

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
            rebilled += kwh * RateSet(eng, end)._cell(section, season, tp)
    elif mode == "vintage_collapse":
        rebilled = basis = 0.0
        for r in rows:
            if r["kwh"] <= 0:
                continue
            cell = (r["section"], r["season"], r["tou_period"])
            span = eng.span_at(cell, _block_end(eng, period, r["section"], r["season"]))
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
            first, last = eng.span_at(cell, s), eng.span_at(cell, e)
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


def rebill_holdout(period):
    """Re-price one statement against a timeline built WITHOUT its own rate rows.

    The one per-statement check here that can move: the prices come from the
    other 25 statements, so a misread rate, column or segment on this statement
    shows up as a residual instead of cancelling out. Rows the rest of the corpus
    cannot date (an absent holdout span, or a span boundary falling inside the
    printed segment — which is exactly what a mid-cycle change visible only on
    this bill looks like) are not priced; they are counted, not guessed.

    Returns {period, printed_priced, rebilled, residual, residual_pct|None,
    priced_pct, unpriced_rows} where the residual is over the PRICED subset and
    printed_priced is that subset's printed dollars.
    """
    eng = _engine()
    hold = _holdout_engine(period)
    rows = [r for r in eng.tou if r["period"] == period]
    if not rows:
        raise SystemExit(f"rebill_holdout: no billing period {period!r} in the corpus")
    printed = sum(r["kwh"] * r["rate"] for r in rows if r["kwh"] > 0)
    priced = rebilled = 0.0
    unpriced = 0
    for r in rows:
        if r["kwh"] <= 0:
            continue
        cell = (r["section"], r["season"], r["tou_period"])
        s, e = eng.segments[(period, r["section"], r["season"], r["segment"])]
        first, last = hold.span_at(cell, s), hold.span_at(cell, e)
        if first is not last or first.tier == "absent":
            unpriced += 1
            continue
        priced += r["kwh"] * r["rate"]
        rebilled += r["kwh"] * first.rate
    residual = rebilled - priced
    return dict(period=period, printed_priced=priced, rebilled=rebilled,
                residual=residual,
                residual_pct=(100.0 * residual / priced) if priced else None,
                priced_pct=(100.0 * priced / printed) if printed else 0.0,
                unpriced_rows=unpriced)


def cca_generation_gap(period):
    """Printed generation TOU dollars vs the CCA generation charge actually billed.

    Σ(printed generation kWh × printed rate) − bill_periods_electric.cca_generation
    for a CCA statement. The two are not the same tariff, and this measures by how
    much: it is the evidence that RateSet.cca_generation() must fail closed rather
    than reuse the printed comparison table. Returns None for bundled statements,
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
    eng = _engine()
    rows = []
    for cell in sorted(eng.timelines):
        for span in eng.timelines[cell]:
            rows.append([cell[0], cell[1], cell[2], str(span.start), str(span.end),
                         "" if span.rate is None else f"{span.rate:.5f}",
                         span.tier, span.note])
    for p in eng.periods:                # BSC: per period, absence made explicit
        rows.append(["base_services_charge", "", "per_day",
                     str(p["start"]), str(p["end"]),
                     "" if p["bsc_total"] is None
                     else f"{p['bsc_total'] / p['days']:.5f}",
                     "absent" if p["bsc_total"] is None else "derived",
                     "no base_services_charge line parsed for this period — new "
                     "charge, renamed line, or parser gap is an OPEN question"
                     if p["bsc_total"] is None else
                     f"period total {p['bsc_total']:.2f} / {p['days']} days "
                     f"(the printed daily unit rate is not captured by the parser)"])
    for p in eng.periods:                # provider timeline
        rows.append(["provider", "", "", str(p["start"]), str(p["end"]),
                     "", "direct", p["provider"]])
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
                        cca_generation_gap(per)))
    # worst = largest |timeline_residual_pct|. That column is zero by identity
    # (module docstring), so in practice the tie breaks on the holdout residual —
    # the per-statement check that can actually move — and then on the statement
    # date, so the label stays stable as the corpus grows.
    worst = sorted(results, key=lambda t: (-abs(t[0]["residual_pct"]),
                                           -abs(t[3]["residual_pct"] or 0.0),
                                           t[0]["statement_date"]))[0][0]["period"]
    rows = []
    for piece, vint, netc, hold, gap in results:
        rows.append([piece["statement_date"], piece["period"], piece["days"],
                     piece["provider"], piece["n_segments"],
                     f"{piece['printed']:.2f}",
                     f"{piece['rebilled']:.2f}",
                     _fmt_money(piece["residual"]),
                     _fmt_money(piece["residual_pct"], 4),
                     f"{hold['printed_priced']:.2f}",
                     f"{hold['rebilled']:.2f}",
                     _fmt_money(hold["priced_pct"], 2),
                     hold["unpriced_rows"],
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
        (dest_dir / "rate_vintages.csv",
         ["section", "season", "tou_period", "vintage_start", "vintage_end",
          "rate_per_kwh", "evidence", "note"], _vintage_rows()),
        # Column names say what the reference is. printed_tou_energy_usd is the
        # statement's OWN printed TOU energy lines; timeline_* re-prices those
        # same kWh through the timeline built from those same rows, so
        # timeline_residual_* is a reconstruction identity, not a dollar
        # validation (module docstring). holdout_* withholds the statement's own
        # rates and CAN move; cca_generation_gap_* is the one independent
        # comparison the committed artifacts support.
        (dest_dir / "rate_rebilling_residuals.csv",
         ["statement_date", "period", "days", "provider", "n_segments",
          "printed_tou_energy_usd", "timeline_energy_usd",
          "timeline_residual_usd", "timeline_residual_pct",
          "holdout_printed_priced_usd", "holdout_energy_usd",
          "holdout_priced_pct", "holdout_unpriced_rows",
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
    for p in paths:
        print(f"wrote {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
