#!/usr/bin/env python3
"""The bundled counterfactual: was switching to CEA (the CCA) a win? (issue #11, Phase 2)

Phase 1 (analysis/cca_rate_extraction.py, data/cca_generation_rates.csv) established what
CEA itself CHARGED, per season x TOU cell, on every one of the 19 CCA-era billing periods,
and found the charged rate never moved once in 18 months. This phase pairs that with what
SDG&E's own bundled generation would have cost on the SAME dates and the SAME usage, in BOTH
directions, so the comparison is not anchored to a single hand-picked pair of statements the
way analysis/bill_decomposition.py's provider_effect_whole_period() is (that function reads
this exact same trade-off for exactly one CCA statement, 2026-07-02, against exactly one
bundled statement, 2024-06-27 -- read as a methodology reference here, not reimplemented).

WHAT THIS SCRIPT DOES NOT DO. It does not re-derive bundled-side rates (RateSet.generation()
/ RateSet.generation_comparison_table(), imported read-only from analysis/rates_history.py)
and it does not re-extract CEA's per-TOU rates (imported read-only from the committed
data/cca_generation_rates.csv). Its only new extraction is two direct bill-line citations
(the bundled statement's own PCIA sentence and each era's franchise-fee line), read straight
off the same two anchor statements bill_decomposition.py already uses (BASE=2024-06-27,
CURRENT=2026-07-02), because no committed artifact carries either fact.

DIRECTION A -- the 19 CCA periods repriced at bundled rates (MODELED -- SAME-DATE BILL RATES).
For each CCA period's actual per-(season, TOU) kWh (data/cca_generation_rates.csv, already
bill-reconciled to the cent), this asks what SDG&E's OWN bundled-generation comparison table
printed for that exact date (RateSet.generation_comparison_table -- the same-statement
diagnostic bill_decomposition.py's module docstring documents at length: on a CCA date this
is SDG&E's bundled-generation (EECC) comparison, printed for reference, not a tariff charged,
and it is the only place a same-date bundled rate exists for a CCA-billed date).

CONFIDENCE LABEL, AND WHY IT CHANGED (second Codex review, issue #11). An earlier version of
this script labeled Direction A MEASURED. That overstated it: both multiplicands here are
real, bill-printed figures -- the household's own MEASURED kWh, and SDG&E's own real,
same-date PRINTED bundled-generation comparison rate -- but the dollar TOTAL this function
produces by multiplying them was never actually billed to anyone. It is this script's own
reconstruction of a bundled arrangement the household never had on this date, for a period it
was actually billed by CEA. CLAUDE.md section 9's confidence tiers distinguish an observed
fact (a meter reading, an actual bill line -- the MEASURED tier) from a validated computation
on real, current-for-that-date inputs (the MODELED tier); this is the latter, not the former,
however real its inputs are. The label is now MODELED, carrying the qualifier "same-date bill
rates" everywhere it is reported (this module's own confidence_detail field, index.html's
pills, TECHNICAL.md), because it is a materially stronger MODELED figure than one built from a
rate table with no date-specific verification (contrast index.html's whole-year,
one-current-rate reading, labeled plain "modeled -- current rates, whole year"): every input
here is the real bill-printed number for the actual date being priced, nothing is scaled or
extrapolated across dates. This was purely a labeling correction, not a computation change --
the dollar figures at the time were untouched by it (a separate, later fix DID change the
computation; see MIXED-SIGN CELLS below).

A priced cell is repriced at SEGMENT level (data/bill_tou_detail.csv's own segment/segment_days
columns), never at one representative date's rate applied to the whole period: a period whose
printed bundled comparison table itself changed mid-cycle (a rate revision landed inside a
billing cycle -- e.g. 1/28/25-2/26/25, four days at the old winter rate and 26 at the new one)
prints two segments at two different $/kWh, and both are priced at their own rate rather than
collapsed to one (Codex review, issue #11: the earlier single-representative-date approach
overstated the bundled comparison by $1.63 across four affected periods).

THIS FIGURE'S SCOPE IS NARROWER THAN "was switching to the CCA a win" -- it is the answer for
the net-import cells only. A materially larger, genuinely unpriceable net-export effect sits
outside it: 14 (period, cell) rows are billed as net exports and carry a real CEA credit of
several hundred dollars (over 4.5x the priced-cell delta), but SDG&E's bill prints no same-date
bundled comparison for a net-export bucket at all -- not a zero, a genuine absence, because
NEM 2.0 defers that dollar to the annual true-up (see direction_a's own
excluded_net_export_cca_credit_usd / excluded_net_export_note in the committed artifact, and
recommendation.text -- read the committed JSON or index.html for the current dollar figure
rather than this docstring, so a future regeneration cannot leave a stale number here).
Reconstructing the bundled-side counterfactual for that exported energy would mean rebuilding
the whole NEM annual true-up -- a materially larger undertaking, properly scoped as its own
follow-up, not attempted here (CLAUDE.md section 0: not determined, not guessed).

MIXED-SIGN CELLS (second Codex review, issue #11, confirmed broader than the single cell first
cited). A (period, season, TOU) cell's kWh in cca_generation_rates.csv is a PERIOD-TOTAL net
figure, but the underlying printed generation-comparison segments in bill_tou_detail.csv can
carry BOTH signs within that same period -- part of the period net-exported (SDG&E prints its
$0.00000/deferred-to-the-true-up sentinel), part net-imported (SDG&E prints a real per-kWh
comparison rate) -- even though the two sub-stretches net down to one signed total. An earlier
version of this script's segment-level pricing (added to fix the mid-cycle-rate-change defect
described above) summed a cell's segments' kWh and dollars together to price it whenever the
PERIOD-TOTAL happened to be positive, without checking whether the segments themselves agreed
in sign. Where they did not, the resulting per-cell "effective rate" (bundled_comparison_usd /
bundled_comparison_kwh) blended a real, same-date printed rate together with a $0 sentinel for
energy no bundled comparison exists for at all -- a rate no tariff SDG&E ever printed. Checked
directly against every generation-section row in data/bill_tou_detail.csv (not assumed, not
special-cased to the one cell a review first flagged): exactly 3 (period, season, TOU) cells in
the whole 66-cell CCA corpus carry mixed-sign segments, only one of which (the smallest swing)
had actually been reaching the old priced branch -- the other two were already routed to
excluded-absent by the (unrelated) representative-date existence check, but for the wrong
reason (misleadingly labeled a "genuine data absence" when a real comparison rate does exist
for part of the period). All 3 are now detected up front, before ANY disposition decision, by
checking the raw segments for mixed sign (see `_mixed_sign()`) regardless of which way the
period's own net kWh happens to fall.

RESOLUTION CHOSEN: exclude the WHOLE mixed-sign cell from Direction A's priced total (never
split or prorate its real CCA-side dollar between the two segments). The alternative -- price
only the net-import segment(s) and treat the net-export segment(s) like a whole-cell export --
was considered and rejected: CEA's own bill only prices the WHOLE cell's net kWh (no bill line
ever prices one sub-segment of a cell on its own), so recovering a segment-level CCA dollar
would require ASSUMING the flat charged rate applies exactly per sub-segment kWh -- which is
directionally reasonable (the flat-rate finding in data/cca_generation_rates.csv holds across
the whole corpus) but does not reconcile exactly to the cent the way every other figure in this
script does, because CEA's own per-period dollar already carries a documented half-kWh
unrounding tolerance (see cca_period_cells()'s own note) that a synthetic per-segment split
would silently inherit and compound. Excluding the whole cell keeps every dollar in the priced
total exactly bill-reconciled on both sides, at the cost of a small amount of information (the
real-priced sub-segment's own bundled-comparison value is disclosed on each excluded row for
transparency -- see import_segment_kwh / export_segment_kwh / segments in
excluded_mixed_sign_detail -- but is not summed into any headline). These cells are their own
disposition, separate from the whole-cell-export bucket above: unlike that bucket, a mixed-sign
cell is not necessarily billed as a net export overall (one of the 3 nets to a net IMPORT for
its period), so folding it into excluded_net_export_cca_credit_usd would misrepresent it as the
same phenomenon; see excluded_mixed_sign_cca_usd / excluded_mixed_sign_note instead.

Direction B (bundled_generation_cells(), below) is checked for the same theoretical risk and is
structurally immune today: it never derives an effective rate by dividing a summed dollar by a
summed kWh (an export segment's real $0 actual charge is genuinely correct there, not a
same-date-comparison sentinel), so a mixed-sign bundled cell would not reproduce this defect --
but it still fails closed if one is ever found, rather than assuming that reasoning forever (see
its own docstring).

Four-way disposition of each (period, season, TOU) cell for Direction A, decided by (1) whether
its printed generation-comparison segments carry mixed signs, checked first and unconditionally,
then (2) whether it was billed as a net import (kWh > 0) and (3) whether a same-date bundled
figure exists -- (2) and (3) mirror provider_effect_whole_period()'s own net-import/net-export
split (adapted here across all 19 periods rather than one):
  MIXED-SIGN (segments carry both signs)  excluded regardless of the period-total's own sign;
                                          see MIXED-SIGN CELLS above.
  PRICED (kWh > 0, comparison present, single-sign segments)   the only cells "energy-only"
                                          sums. If a net-import cell ever lacked a comparison
                                          this raises -- see bill_decomposition's own "no
                                          same-date SDG&E bundled comparison" refusal -- but
                                          empirically none does.
  ABSENT (kWh <= 0, comparison absent)   SDG&E's bundled table prints nothing because a
                                          net-export bucket settles at the annual true-up
                                          under NEM 2.0, not because its bundled rate is zero
                                          (the Settlement-is-not-a-price rule bill_decomposition.py
                                          enforces with a type). No counterfactual dollar exists.
  CARRIED-EXPORT (kWh <= 0, comparison PRESENT)  a genuine finding this repo's own single-pair
                                          analysis never encountered: three (period, cell) rows
                                          are billed as a net export in that specific CCA period
                                          YET a same-date bundled rate is still knowable, because
                                          it was CARRIED across the gap from flanking direct
                                          observations elsewhere in the corpus (rates_history's
                                          "carried" tier -- see its own module docstring). This is
                                          a real vintage rate, not a Settlement, but the ACTUAL
                                          bundled dollar for that specific export bucket is still
                                          not a real monthly charge (NEM 2.0 defers it), so these
                                          rows are excluded from the priced comparison too --
                                          reported separately, never silently folded into either
                                          side (the corpus check case_export_kwh_never_priced
                                          protects this).

DIRECTION B -- the 7 bundled periods repriced at CCA rates (MODELED).
For each bundled period's actual per-(season, TOU) generation kWh (summed from
data/bill_tou_detail.csv's section='generation' rows -- the printed TOU table SDG&E charged
during the bundled era), this asks what CEA's own flat charged rate (Phase 1's finding: one
value per season x TOU cell across the entire observed CCA era) would have billed. This is
MODELED, never measured: CEA did not serve this household in 2024, so applying its
2025-2026 rate to 2024 usage is an explicit, labeled assumption, not an observation. The
same net-import/net-export split applies, for the same reason (NEM 2.0 settlement, not
provider): the ACTUAL bundled dollar for a net-export bucket is a deferred non-price (the
bill_tou_detail.csv loader enforces this at the corpus level -- rates_history.py's own
_load() raises unless every non-positive-kWh generation row prints a literal $0.00000 rate),
so those buckets are excluded from Direction B's priced comparison, with the modeled CCA-side
credit reported separately rather than compared against a non-price.

Direction B publishes an ENERGY-ONLY figure only. A "whole period arrangement" figure (CEA's
product adders + CCA's unbundling riders against the whole bundled table, matching
provider_effect_whole_period()'s second scope) is NOT published for Direction B: it would
require a CCA rider figure (PCIA-equivalent / ICA / EDP credit) for 2024 usage that no
committed artifact carries and that CEA never actually charged, because CEA did not serve
this household then -- inventing one would be exactly the guess CLAUDE.md section 0 forbids.

PROVIDER EFFECT vs VINTAGE EFFECT (AC4), generalizing bill_decomposition.py's two-statement
identity (g1 - g0 = (s1 - g0) + (g1 - s1)) across every Direction-A-priced (period, cell):
  g0   the LAST bundled rate SDG&E actually CHARGED before the 12/27/24 CCA switch
       (RateSet.generation() at a date late in the bundled era for that season -- 2024-10-25
       for summer cells, 2024-12-20 for winter cells, both directly evidenced, pre-switch).
  s1   the same-date bundled comparison used by Direction A (SDG&E's own later printed
       estimate of what its bundled rate would be on this CCA-era date).
  g1   CEA's own charged rate on this CCA-era date (Phase 1's flat finding).
  vintage_usd  = kWh * (s1 - g0): how much SDG&E's OWN bundled rate moved from the pre-switch
                 baseline to this later date, holding the household's real usage fixed --
                 attributable to tariff vintage, not to the provider switch.
  provider_usd = kWh * (g1 - s1): CEA vs SDG&E's bundled rate on the SAME later date --
                 attributable to the provider switch, not to vintage. This sums to EXACTLY
                 Direction A's headline delta (an algebraic identity, checked in
                 case_provider_plus_vintage_equals_total_change).
The CCA side of this split is exactly zero by construction: Phase 1 found CEA's charged rate
never moved, so 100% of any drift in "what CEA would charge now vs then" is a bundled-side
(SDG&E) vintage phenomenon, not a CCA one. Two (season, TOU) cells -- summer/off_peak and
winter/off_peak -- have NO bundled-charged observation anywhere in the entire bundled era
(genuinely absent, not a parser gap: this household's off-peak buckets were net-export
whenever they were billed as bundled), so g0 does not exist for them; the affected
Direction-A rows (a handful of the priced rows -- see the committed artifact's
provider_vs_vintage_split.cells_with_no_vintage_baseline for the current count) still count
fully toward Direction A's own headline (which needs no g0), they are simply excluded from the
vintage/provider split and disclosed as such.

AC6 -- THE TWO DIRECTIONS DISAGREE BY WELL OVER 20%, AND THE REASON IS NAMED, NOT AVERAGED
AWAY. Direction A (modeled -- same-date bill rates, 547 days, spans two summers and two
winters) finds the CCA
premium at roughly $50/yr on the net-import cells it can price. Direction B (modeled, 216 days,
May-Dec 2024 only -- one summer and one partial winter, no Jan-Apr) finds about $136/yr,
roughly 2.7x larger (see the committed artifact for the current exact figures on both sides).
The provider/vintage split above supplies the mechanism: SDG&E's own
bundled generation rate ROSE substantially
across the observed CCA era (the printed comparison table documents summer on-peak alone
moving 0.38826 -> 0.40592 -> 0.47019 -- data/rate_vintages.csv), while CEA's charged rate did
not move at all. Direction A prices CEA against the SAME-DATE bundled rate, so this vintage
drift nets out of its headline by construction. Direction B has no such same-date anchor for
2024 usage -- there is no 2024 CCA rate to use, so it necessarily compares 2024's cheaper
bundled rate against CEA's rate AS OBSERVED IN 2025-2026, which is exactly the "one rate
vintage per projection" trap CLAUDE.md section 9 names elsewhere in this repo. Direction B's
larger figure is not wrong, it is answering a different, harder question ("what would 2024
usage have cost a CCA customer, priced at the only CCA rate ever observed") and that question
structurally bundles two years of bundled-side rate inflation into what looks like a provider
effect. Direction A is the like-for-like reading; Direction B is reported because AC2 asks
for the bidirectional counterfactual, labeled MODELED, and reconciled here rather than
averaged with Direction A.

Inputs (read-only, never re-derived):
  data/cca_generation_rates.csv       Phase 1: CEA's own per-(period, season, TOU) charged
                                      kWh/rate/usd for all 19 CCA periods, plus its product
                                      adders (Clean Impact Plus, State Surcharge Tax).
  data/bill_periods_electric.csv      the period universe, days, generation_provider.
  data/bill_tou_detail.csv            bundled-era per-(period, section, season, segment, TOU)
                                      kWh and printed rate -- Direction B's per-cell kWh.
  analysis/rates_history.py           RateSet.generation() / generation_comparison_table(),
                                      imported as a library, never reimplemented.
  private/1-raw-data/electric-bills/sdge_electric_2024-06-27.pdf   (BASE, bundled) and
  private/1-raw-data/electric-bills/sdge_electric_2026-07-02.pdf   (CURRENT, CCA) -- the two
                                      anchor statements already used by
                                      bill_decomposition.py, read directly here ONLY for the
                                      PCIA and franchise-fee bill-line citations no committed
                                      artifact carries.
Output: data/cca_bundled_counterfactual.json, written atomically; run twice -> byte-identical
        provided the private PDF archive is present (see the two anchor-statement citations
        above); the two directions' numeric results depend only on committed CSV/engine data
        and regenerate identically with or without the archive.

Run directly:  ./.venv/bin/python analysis/cca_bundled_counterfactual.py
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
    """Nearest ancestor holding both analysis/ and data/ (same convention as every other
    generator in this repo, so the private/verify sandbox pattern works unchanged)."""
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

sys.path.insert(0, str(ROOT / "analysis"))
import rates as RATES              # noqa: E402  -- canonical SUMMER_MONTHS, read-only
import rates_history as RH         # noqa: E402  -- canonical historical rate engine, read-only

SEASONS = ("winter", "summer")
TOU = ("on_peak", "off_peak", "super_off_peak")

# The two anchor statements bill_decomposition.py already uses for its own same-question
# comparison (its BASE / CURRENT). Reused here, not re-chosen, so the PCIA/franchise
# citations are drawn from statements this repo already treats as representative endpoints.
BASE_STMT = "2024-06-27"      # bundled era
CURRENT_STMT = "2026-07-02"   # CCA era

# The last BUNDLED-charged (pre-switch) date to query per season for the g0 vintage
# baseline -- both dates fall inside the bundled era (ends 2024-12-26) and inside a directly
# evidenced span for every cell that has one at all (verified against data/rate_vintages.csv:
# summer cells are last direct 2024-06-01..2024-10-31; winter cells 2024-11-01..2024-12-26).
G0_QUERY_DATE = {"summer": dt.date(2024, 10, 25), "winter": dt.date(2024, 12, 20)}

_NUM = r"[\d,]+\.\d+"


def _read_csv(path):
    if not path.exists():
        raise SystemExit(f"required artifact missing: {path}")
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _date(s):
    """'12/27/24' -> date(2024, 12, 27) -- the same M/D/YY format bill_periods_electric.csv
    and cca_generation_rates.csv both print their period bounds in."""
    m, d, y = s.strip().split("/")
    return dt.date(2000 + int(y), int(m), int(d))


def _season_of(day):
    """The tariff season calendar, read from rates.py -- never re-listed here."""
    return "summer" if day.month in RATES.SUMMER_MONTHS else "winter"


def _c(x):
    """Round to cents; +0.0 folds -0.0 to 0.0 so the artifact is stable."""
    return round(x, 2) + 0.0


def _r(x, n):
    return round(x, n) + 0.0


def _money(x):
    """'$74.07' or '-$378.06' -- the sign goes before the dollar sign, never after it
    (an f-string's own '${x}' on a negative float prints '$-378.06', which reads as a
    typo; this is the one formatting helper every prose string below uses for a dollar
    amount that can be negative)."""
    return f"-${abs(x):.2f}" if x < 0 else f"${x:.2f}"


# ---------------------------------------------------------------------------
# Phase 1 data: CEA's own per-period per-cell charges, and its flat rate
# ---------------------------------------------------------------------------
def cca_period_cells():
    """{(statement_date, period): {(season, tou_period): {kwh, rate, usd}}} from the
    committed, already-reconciled data/cca_generation_rates.csv."""
    out = {}
    for r in _read_csv(DATA / "cca_generation_rates.csv"):
        if r["tou_period"] not in TOU:
            continue
        key = (r["statement_date"], r["period"])
        out.setdefault(key, {})[(r["season"], r["tou_period"])] = {
            "kwh": float(r["kwh"]), "rate": float(r["rate_usd_per_kwh"]),
            "usd": float(r["usd"])}
    if not out:
        raise SystemExit("data/cca_generation_rates.csv carries no per-TOU CCA cells")
    return out


def cca_period_adders():
    """{(statement_date, period): {"clean_impact_plus": usd, "state_surcharge_tax": usd}}
    -- CEA's own period-level product adders, already reconciled by Phase 1."""
    out = {}
    for r in _read_csv(DATA / "cca_generation_rates.csv"):
        if r["tou_period"] not in ("clean_impact_plus", "state_surcharge_tax"):
            continue
        key = (r["statement_date"], r["period"])
        out.setdefault(key, {})[r["tou_period"]] = float(r["usd"])
    return out


def cca_flat_rate():
    """{(season, tou_period): rate} -- CEA's own charged rate, asserted flat across the
    whole corpus (Phase 1's headline finding, re-checked here rather than assumed: this
    script fails closed if a future statement ever breaks that flatness, exactly like
    test_cca_rate_extraction.case_cca_rate_is_flat_across_the_whole_corpus)."""
    seen = {}
    for r in _read_csv(DATA / "cca_generation_rates.csv"):
        if r["tou_period"] not in TOU:
            continue
        key = (r["season"], r["tou_period"])
        seen.setdefault(key, set()).add(float(r["rate_usd_per_kwh"]))
    if len(seen) != 6:
        raise SystemExit(f"expected 6 season x TOU cells in cca_generation_rates.csv, "
                         f"found {sorted(seen)}")
    for cell, rates in seen.items():
        if len(rates) != 1:
            raise SystemExit(
                f"CEA's charged rate for {cell} is not flat across the corpus ({rates}) -- "
                "Direction B's single-rate modeling assumption no longer holds and must be "
                "revisited, not silently averaged")
    return {k: next(iter(v)) for k, v in seen.items()}


# ---------------------------------------------------------------------------
# Billing period universe
# ---------------------------------------------------------------------------
def periods_by_provider():
    """{"CCA": [period dict, ...], "bundled": [...]} from data/bill_periods_electric.csv,
    each carrying its own start/end dates and day count."""
    out = {"CCA": [], "bundled": []}
    for r in _read_csv(DATA / "bill_periods_electric.csv"):
        a, b = r["period"].split(" - ")
        prov = r["generation_provider"]
        if prov not in out:
            raise SystemExit(f"unknown generation_provider {prov!r} in bill_periods_electric.csv")
        out[prov].append({
            "statement_date": r["statement_date"], "period": r["period"],
            "start": _date(a), "end": _date(b), "days": int(float(r["days"]))})
    if not out["CCA"] or not out["bundled"]:
        raise SystemExit("bill_periods_electric.csv must carry both bundled and CCA periods "
                         "for a bidirectional counterfactual")
    return out


def _rep_date(season, start, end):
    """A date inside `season`'s own block of [start, end]. A billing period straddles at
    most one season boundary (rates_history._season_blocks's own invariant), so either the
    period's end date falls in the target season (the period's SECOND block) or its start
    date does (its first, or its only, block) -- one of the two always matches."""
    return end if _season_of(end) == season else start


def _season_span(p):
    """The season(s) a period dict's own [start, end] range spans -- one season for a
    period wholly within it, two when it straddles the boundary (the same
    _season_blocks invariant _rep_date relies on)."""
    return {_season_of(p["start"]), _season_of(p["end"])}


def _expected_cells(p):
    """Every (season, TOU) cell a period is structurally expected to carry data for: all
    three TOU periods, for each season the period's own date range spans. Verified against
    the real corpus (both CCA and bundled eras) rather than assumed: every period on file,
    straddling a season boundary or not, carries all three TOU cells for every season it
    spans in both data/cca_generation_rates.csv and data/bill_tou_detail.csv, regardless of
    whether a given cell ever billed as a net import or a net export -- presence is
    unconditional; only PRICING (the kWh > 0 split) depends on the sign."""
    return {(season, tou) for season in _season_span(p) for tou in TOU}


def _require_full_period_coverage(periods, cells_by_period, source_csv, era_label):
    """Fail closed (Codex review, issue #11, defect 2) if `source_csv` is silently missing
    a whole period, or missing one of the (season, TOU) cells a period is structurally
    expected to carry (see _expected_cells). Both Direction A (cca_cells.get(key, {})) and
    Direction B (an implicit filter-join against bill_tou_detail.csv) read a per-period cell
    mapping built from a committed CSV; without this check, a partially regenerated,
    corrupted, or hand-edited CSV that silently dropped a period or cell would make either
    direction quietly price it as zero rows, while n_periods/days (read from
    bill_periods_electric.csv, not from what was actually priced) would still report full
    corpus coverage -- understating the headline dollar figure with no signal anything went
    wrong. `cells_by_period` is {period_key: set_of_(season, tou)_present}, keyed however the
    caller's own period dicts are keyed (Direction A: (statement_date, period); Direction B:
    period alone) -- the caller supplies a matching key alongside each period dict."""
    for key, p in periods:
        present = cells_by_period.get(key)
        if not present:
            raise SystemExit(
                f"{source_csv} carries no rows at all for {era_label} period "
                f"{p['period']} (key {key}) -- bill_periods_electric.csv expects this "
                "period to be priced; refusing to silently price it as zero rows")
        missing = sorted(_expected_cells(p) - set(present))
        if missing:
            raise SystemExit(
                f"{source_csv} is missing {missing} for {era_label} period {p['period']} "
                f"(key {key}) -- this period spans {sorted(_season_span(p))}, so all three "
                "TOU cells are expected for each season spanned; refusing to price a "
                "partial cell set")


def _generation_comparison_segments():
    """{(period, season, tou_period): [(kwh, rate), ...]} -- every printed generation-
    section row of data/bill_tou_detail.csv, segment-level, for EVERY period on file
    (bundled and CCA-era alike). On a CCA-era period this is SDG&E's own printed
    bundled-generation (EECC) comparison table, not a charge (RateSet.
    generation_comparison_table's own docstring), but it is printed segment-by-segment
    exactly like a bundled-era statement: a period whose bundled comparison table itself
    changed mid-cycle (a rate revision landed inside a billing cycle) prints two rows for
    the same cell at two different $/kWh, and both are kept here, never collapsed to one
    representative date's rate the way a single RH.rates_on(one date) lookup would.
    Verified against every CCA period on file: summing these segments' kWh for a given
    (period, season, tou_period) always reproduces data/cca_generation_rates.csv's own
    period-total kWh for that cell (checked below, per period, rather than assumed)."""
    out = {}
    for r in _read_csv(DATA / "bill_tou_detail.csv"):
        if r["section"] != "generation":
            continue
        key = (r["period"], r["season"], r["tou_period"])
        out.setdefault(key, []).append((float(r["kwh"]), float(r["rate_per_kwh"])))
    return out


def _mixed_sign(segs):
    """True iff `segs` (a list of (kwh, rate) pairs for one (period, season, TOU) cell)
    carries at least one net-IMPORT sub-segment (kwh > 0) AND at least one net-EXPORT
    sub-segment (kwh <= 0) within the same cell -- e.g. a mid-cycle rate change that also
    straddles a within-period export/import flip. See the module docstring's MIXED-SIGN
    CELLS section for why this must be checked before a cell's segments are ever summed
    into one dollar figure or one effective rate: an export sub-segment prints no real
    comparison rate (SDG&E defers it to the NEM annual true-up, sentinel-value $0.00000),
    so summing it alongside a real import sub-segment blends a genuine non-price into a
    real one."""
    if segs is None:
        return False
    return any(kwh > 0 for kwh, _rate in segs) and any(kwh <= 0 for kwh, _rate in segs)


# ---------------------------------------------------------------------------
# Direction A -- 19 CCA periods repriced at SDG&E's same-date bundled comparison
# (MODELED -- same-date bill rates; see the module docstring's CONFIDENCE LABEL section)
# ---------------------------------------------------------------------------
def direction_a(cca_cells):
    cca_periods = periods_by_provider()["CCA"]
    _require_full_period_coverage(
        [((p["statement_date"], p["period"]), p) for p in cca_periods],
        {key: set(cells) for key, cells in cca_cells.items()},
        "data/cca_generation_rates.csv", "CCA")

    gen_segments = _generation_comparison_segments()
    priced, absent, carried_export, mixed_sign = [], [], [], []
    for p in cca_periods:
        key = (p["statement_date"], p["period"])
        for (season, tou), c in cca_cells.get(key, {}).items():
            row = {"statement_date": p["statement_date"], "period": p["period"],
                   "season": season, "tou_period": tou, "kwh": c["kwh"],
                   "cca_rate": c["rate"], "cca_usd": c["usd"]}

            # Second Codex review (issue #11): checked BEFORE the kWh>0 disposition below,
            # against the raw segments, regardless of which way this cell's PERIOD-TOTAL
            # kWh nets -- a mixed-sign cell must never reach the summing logic further down,
            # whichever aggregate sign cca_generation_rates.csv happens to report for it (see
            # the module docstring's MIXED-SIGN CELLS section: this catches all 3 of the
            # corpus's mixed cells, not just the one whose aggregate happens to be positive).
            segs = gen_segments.get((p["period"], season, tou))
            if _mixed_sign(segs):
                seg_total = _r(sum(kwh for kwh, _rate in segs), 1)
                if abs(seg_total - c["kwh"]) > 1.0:
                    raise SystemExit(
                        f"{key} {season}/{tou}: bill_tou_detail.csv's generation-section "
                        f"kWh ({seg_total}) does not reconcile to cca_generation_rates.csv's "
                        f"period-total kWh ({c['kwh']}) for this mixed-sign cell -- refusing "
                        "to classify it against a kWh figure that does not match the CCA "
                        "charge's own kWh base")
                import_kwh = _r(sum(kwh for kwh, _rate in segs if kwh > 0), 1)
                export_kwh = _r(sum(kwh for kwh, _rate in segs if kwh <= 0), 1)
                row["segments"] = [{"kwh": _r(kwh, 1), "rate": rate} for kwh, rate in segs]
                row["import_segment_kwh"] = import_kwh
                row["export_segment_kwh"] = export_kwh
                row["why_excluded"] = (
                    "this cell's printed generation-comparison segments carry BOTH signs "
                    f"within the same billing period ({import_kwh} kWh net-imported at a "
                    f"real printed rate, {export_kwh} kWh net-exported at SDG&E's "
                    "$0.00000/deferred-to-the-annual-true-up sentinel) even though the "
                    f"period's own net kWh for this cell nets those two sub-stretches down "
                    f"to {c['kwh']}. Pricing the whole cell as one blended figure would "
                    "average a real per-kWh rate together with a genuine non-price into an "
                    "effective rate no tariff ever printed. Excluded from the priced total; "
                    "CEA's own real, whole-cell charge for this cell is disclosed as-is "
                    "(never split or prorated across the two segments -- no bill line "
                    "prices either segment on its own) in "
                    "excluded_mixed_sign_cca_usd / excluded_mixed_sign_note")
                mixed_sign.append(row)
                continue

            d = _rep_date(season, p["start"], p["end"])
            try:
                s1 = RH.rates_on(d).generation_comparison_table(season, tou)
                have_s1 = True
            except SystemExit:
                s1, have_s1 = None, False
            if c["kwh"] > 0:
                if not have_s1:
                    raise SystemExit(
                        f"{key} {season}/{tou}: billed as a net import (kWh={c['kwh']}) but "
                        "no same-date SDG&E bundled comparison exists -- Direction A has no "
                        "counterfactual for this cell and must not estimate one")
                # Segment-level pricing (issue #11 Codex review, defect 2): a period whose
                # PRINTED bundled comparison table changed mid-cycle carries two segments at
                # two different rates (data/bill_tou_detail.csv's own segment/segment_days
                # columns); pricing the whole period's kWh at one representative date's rate
                # (the old approach) misprices exactly those periods. This sums each
                # segment's own kWh at its own segment's rate instead -- for a period whose
                # comparison table never changed mid-cycle (nearly all of them) this is
                # numerically IDENTICAL to the old one-rate approach, since there is only one
                # segment to sum. `segs` was already fetched above (and confirmed NOT
                # mixed-sign) by the mixed-sign guard before this branch runs, so every
                # segment reaching this sum agrees in sign -- the second Codex review's
                # defect (blending an unpriced export segment into this sum) cannot occur here.
                if not segs:
                    raise SystemExit(
                        f"{key} {season}/{tou}: billed as a net import but "
                        "data/bill_tou_detail.csv carries no generation-section row for "
                        "this (period, season, TOU) -- cannot segment-price it")
                seg_kwh_total = sum(kwh for kwh, _rate in segs)
                if abs(seg_kwh_total - c["kwh"]) > 1.0:
                    raise SystemExit(
                        f"{key} {season}/{tou}: bill_tou_detail.csv's generation-section "
                        f"kWh ({seg_kwh_total}) does not reconcile to cca_generation_rates."
                        f"csv's period-total kWh ({c['kwh']}) for this cell -- refusing to "
                        "segment-price against a kWh figure that does not match the CCA "
                        "charge's own kWh base")
                bundled_usd_raw = sum(kwh * rate for kwh, rate in segs)
                row["bundled_comparison_usd"] = _c(bundled_usd_raw)
                # bundled_comparison_rate is now the EFFECTIVE (kWh-weighted) rate across
                # this period's segments -- identical to the single printed rate when there
                # is only one segment, and a genuine weighted average (not a re-estimate)
                # when there are two, since bundled_comparison_usd above is already the
                # segment-exact dollar total.
                row["bundled_comparison_rate"] = _r(bundled_usd_raw / seg_kwh_total, 5)
                row["bundled_comparison_n_segments"] = len(segs)
                row["provider_delta_usd"] = _c(row["cca_usd"] - row["bundled_comparison_usd"])
                priced.append(row)
            elif have_s1:
                # A real, "carried" vintage rate exists for this cell/date, but the
                # ACTUAL bundled dollar for a net-export bucket is not a monthly price
                # under NEM 2.0 (it settles at the annual true-up) -- so this is neither a
                # Settlement absence nor a priceable cell. Reported, never silently folded
                # into either side.
                row["bundled_comparison_rate"] = s1
                row["why_excluded"] = (
                    "billed as a net export in this period, so no monthly bundled dollar "
                    "would have been charged under NEM 2.0 (it defers to the annual "
                    "true-up) even though SDG&E's vintage rate for this cell on this date "
                    "is independently knowable (carried across the corpus)")
                carried_export.append(row)
            else:
                row["why_excluded"] = (
                    "billed as a net export in this period, and SDG&E's printed bundled "
                    "comparison table carries no rate for this cell on this date either "
                    "(a genuine data absence, not a parser gap)")
                absent.append(row)

    cca_usd = _c(sum(r["cca_usd"] for r in priced))
    bundled_usd = _c(sum(r["bundled_comparison_usd"] for r in priced))
    days = sum(p["days"] for p in periods_by_provider()["CCA"])
    delta = _c(cca_usd - bundled_usd)

    # Codex review (issue #11), defect 1: the priced total above is only the answer for
    # the net-import cells -- a materially larger, genuinely unpriceable net-export effect
    # sits outside it. This sums what CEA actually credited (a real, billed dollar figure,
    # live off the same committed CSV every other Direction-A figure reads, never
    # hardcoded) across BOTH excluded categories, so the headline can be reported beside
    # it rather than instead of it.
    excluded_all = absent + carried_export
    excluded_net_export_cca_credit_usd = _c(sum(r["cca_usd"] for r in excluded_all))
    excluded_vs_priced_ratio = (
        _r(abs(excluded_net_export_cca_credit_usd) / delta, 1) if delta else None)
    excluded_net_export_note = (
        f"these {len(excluded_all)} ({len(absent)} excluded-absent + "
        f"{len(carried_export)} excluded-carried-export) cells were billed as NET "
        "EXPORTS in their CCA period, so CEA's own bill shows a real credit here -- "
        f"{_money(excluded_net_export_cca_credit_usd)} total"
        + (f", {excluded_vs_priced_ratio}x the size of the {_money(delta)} priced-cell "
           "delta above" if excluded_vs_priced_ratio is not None else "") +
        ". But there is no same-date SDG&E bundled "
        "counterfactual to compare it against: under NEM 2.0 a net-export bucket's dollar "
        "value is deferred to the annual true-up, so SDG&E's bill prints no bundled "
        "comparison rate for these cells at all (not a zero -- see "
        "bill_decomposition.py's Settlement type for the same distinction on the bundled "
        "side). WHAT THE BUNDLED-SIDE COUNTERFACTUAL VALUE OF THIS EXPORTED ENERGY WOULD "
        "HAVE BEEN IS NOT DETERMINED here -- reconstructing it would mean reconstructing "
        "the whole NEM annual true-up (which nets exports against imports across the "
        "compensation year at rates this repo has not extracted for the export side), a "
        "materially larger undertaking properly scoped as its own follow-up, not "
        "estimated here. Because of this, the priced-cell delta above is NOT the full "
        "answer to whether switching to the CCA was a win for this household -- it is the "
        "answer for the subset of energy this analysis CAN price; see recommendation.text.")

    # Second Codex review (issue #11): a DIFFERENT excluded category from the net-export
    # bucket above -- a mixed-sign cell is not itself billed as a net export (case 3 below
    # nets to a positive, net-IMPORT kWh for its period), so folding it into
    # excluded_net_export_cca_credit_usd would misrepresent it as the same phenomenon.
    # Disclosed in its own bucket instead, one real (never split/prorated) CCA dollar per
    # cell, exactly the way the net-export bucket discloses its own real CCA dollar.
    excluded_mixed_sign_cca_usd = _c(sum(r["cca_usd"] for r in mixed_sign))
    excluded_mixed_sign_note = (
        f"these {len(mixed_sign)} (period, season, TOU) cells carry BOTH a net-import and a "
        "net-export sub-segment within the SAME billing period (see each row's own "
        "segments/import_segment_kwh/export_segment_kwh) -- a materially different, "
        "narrower phenomenon than the whole-cell net-export exclusion above. Pricing them "
        "as one blended cell would average a real, same-date SDG&E printed comparison rate "
        "(for the import sub-segment) together with a genuine non-price (SDG&E's "
        "$0.00000/deferred-to-the-annual-true-up sentinel for the export sub-segment) into "
        "an effective rate no tariff ever printed -- so they are excluded from the priced "
        "total entirely, not partially. CEA's own real, whole-cell charge for these cells "
        f"is {_money(excluded_mixed_sign_cca_usd)} total; it is NOT split or prorated "
        "across the two segments (no bill line prices either segment on its own, so any "
        "such split would be an invented figure, not a bill-reconciled one) -- see each "
        "row's own why_excluded for the specific segments and kWh involved."
    ) if mixed_sign else (
        "no (period, season, TOU) cell in this corpus carries both a net-import and a "
        "net-export sub-segment within the same billing period"
    )

    return {
        "confidence": "modeled",
        "confidence_detail": (
            "MODELED -- same-date bill rates, not measured, despite both multiplicands "
            "being real, bill-printed figures: the per-cell kWh is this household's own "
            "MEASURED CCA-era usage (already bill-reconciled, data/cca_generation_rates.csv), "
            "and the per-cell rate is SDG&E's own real, same-date PRINTED bundled-generation "
            "comparison (its bill's own reference figure, not invented or scaled from a rate "
            "table) -- but the dollar total produced by multiplying them is this script's own "
            "reconstruction of a bundled arrangement the household never actually had on this "
            "date. Nobody ever billed this household this number; CLAUDE.md's measured tier "
            "means an observed fact (a meter reading, an actual bill line), not a computed "
            "counterfactual built from real inputs, however strong those inputs are. It is a "
            "materially stronger MODELED figure than one built from a generic rate table with "
            "no date-specific verification -- every input here is the actual bill-printed "
            "number for the actual date being priced -- which is why it carries the qualifier "
            "'same-date bill rates' rather than a bare 'modeled' label."),
        "what": ("the household's actual per-(season, TOU) CCA-era kWh, priced once at "
                 "what CEA actually charged and once at SDG&E's own same-date printed "
                 "bundled-generation comparison -- both inputs are real numbers printed on "
                 "the household's own bills, but the dollar total produced by multiplying "
                 "them is this script's own computed reconstruction of a bundled "
                 "arrangement never actually billed here; see confidence_detail"),
        "n_periods": len(periods_by_provider()["CCA"]),
        "n_statements": len({p["statement_date"] for p in periods_by_provider()["CCA"]}),
        "days": days,
        "scope": ("energy-only, the net-import cells this analysis CAN price -- no "
                  "product adders, no riders, and excluding both a materially larger "
                  "net-export effect (see excluded_net_export_cca_credit_usd / "
                  "excluded_net_export_note) and a small number of mixed-sign cells that "
                  "cannot be priced as one blended figure (see "
                  "excluded_mixed_sign_cca_usd / excluded_mixed_sign_note)"),
        "priced_cells": len(priced),
        "excluded_absent_cells": len(absent),
        "excluded_carried_export_cells": len(carried_export),
        "excluded_mixed_sign_cells": len(mixed_sign),
        "cca_charged_usd": cca_usd,
        "bundled_comparison_usd": bundled_usd,
        "delta_usd": delta,
        "delta_positive_means": "the CCA arrangement cost MORE than bundled would have, same dates, same usage",
        "delta_usd_per_year": _annualize(delta, days),
        "excluded_net_export_cells": len(excluded_all),
        "excluded_net_export_cca_credit_usd": excluded_net_export_cca_credit_usd,
        "excluded_net_export_note": excluded_net_export_note,
        "excluded_mixed_sign_cca_usd": excluded_mixed_sign_cca_usd,
        "excluded_mixed_sign_note": excluded_mixed_sign_note,
        "excluded_absent_detail": absent,
        "excluded_carried_export_detail": carried_export,
        "excluded_mixed_sign_detail": mixed_sign,
        "priced_detail": priced,
    }


# ---------------------------------------------------------------------------
# Direction B -- 7 bundled periods repriced at CEA's flat charged rate (MODELED)
# ---------------------------------------------------------------------------
def bundled_generation_cells():
    """{(period, season, tou_period): {"kwh": net kWh, "actual_usd": what SDG&E actually
    charged}} for every bundled-era period, summed across bill_tou_detail.csv's segments
    (a mid-cycle rate change inside one period prints two segments for the same cell; both
    belong to the same net kWh bucket and are summed here). A net-export bucket's rows
    always print a literal $0.00000 rate (the corpus-wide invariant rates_history.py's own
    loader enforces) -- checked again here rather than assumed, since this reads the CSV
    directly rather than through that loader.

    MIXED-SIGN CELLS (second Codex review, issue #11): direction_a()'s analogous segment
    sum had to guard against a cell whose segments carry BOTH signs (part of the period
    net-exported, part net-imported), because dividing a summed dollar by a summed kWh
    there produces an effective RATE that blends a real price with a genuine non-price.
    This function never does that division -- actual_usd sums each segment's own REAL
    charged dollar (an export segment's real charge is a genuine, correctly-charged $0
    under NEM 2.0, not a sentinel standing in for "no rate available" the way a CCA-era
    printed COMPARISON table's $0.00000 is), and `kwh` is only ever used net, the same
    way every other bundled cell is treated by this whole direction. So a mixed-sign cell
    here would not reproduce Direction A's blended-rate defect. Still checked and failed
    closed below (not merely assumed) so a future reviewer does not have to re-derive this
    reasoning from scratch if the corpus ever changes shape; verified empirically today
    (case_bundled_generation_cells_has_no_mixed_sign_cells_today): zero of the 7
    bundled-era periods' cells mix signs."""
    bundled_periods = {p["period"] for p in periods_by_provider()["bundled"]}
    agg = {}
    seg_kwhs = {}
    for r in _read_csv(DATA / "bill_tou_detail.csv"):
        if r["section"] != "generation" or r["period"] not in bundled_periods:
            continue
        key = (r["period"], r["season"], r["tou_period"])
        kwh, rate = float(r["kwh"]), float(r["rate_per_kwh"])
        if kwh <= 0 and rate != 0.0:
            raise SystemExit(
                f"{key}: a net-export generation row carries a nonzero printed rate "
                f"{rate} -- the credit-lines-print-zero invariant is broken; re-derive "
                "rather than silently pricing it")
        a = agg.setdefault(key, {"kwh": 0.0, "actual_usd": 0.0})
        a["kwh"] += kwh
        a["actual_usd"] += kwh * rate
        seg_kwhs.setdefault(key, []).append(kwh)

    mixed = {key: kwhs for key, kwhs in seg_kwhs.items()
             if any(k > 0 for k in kwhs) and any(k <= 0 for k in kwhs)}
    if mixed:
        raise SystemExit(
            f"bundled-era cell(s) {sorted(mixed)} carry both a net-import and a "
            "net-export generation segment within the same billing period -- this "
            "function's docstring reasons that this would not reproduce Direction A's "
            "blended-effective-rate defect (no rate is ever derived here by dividing a "
            "summed dollar by a summed kWh), but that reasoning has not been re-verified "
            "against this specific corpus shape; refusing to silently proceed rather than "
            "assume the reasoning still holds")

    # Codex review (issue #11), defect 2 sweep: direction_b() has no per-period existence
    # check of its own on this mapping (unlike Direction A's cca_cells.get(key, {}), this is
    # an implicit filter-join against bill_tou_detail.csv above) -- the same silent-swallow
    # risk applies if a whole bundled period, or one of its expected cells, ever dropped out
    # of that CSV. Grouped here by period so the check reads like Direction A's.
    present_by_period = {}
    for (period, season, tou) in agg:
        present_by_period.setdefault(period, set()).add((season, tou))
    _require_full_period_coverage(
        [(p["period"], p) for p in periods_by_provider()["bundled"]],
        present_by_period, "data/bill_tou_detail.csv", "bundled")
    return agg


def direction_b(flat_rate):
    agg = bundled_generation_cells()
    priced, excluded = [], []
    for (period, season, tou), a in agg.items():
        rate = flat_rate.get((season, tou))
        if rate is None:
            raise SystemExit(f"no CEA flat rate on file for ({season}, {tou})")
        hyp_usd = _c(a["kwh"] * rate)
        row = {"period": period, "season": season, "tou_period": tou, "kwh": _r(a["kwh"], 1),
               "actual_bundled_usd": _c(a["actual_usd"]), "hypothetical_cca_usd": hyp_usd,
               "cca_flat_rate": rate}
        if a["kwh"] > 0:
            row["delta_usd"] = _c(hyp_usd - row["actual_bundled_usd"])
            priced.append(row)
        else:
            row["why_excluded"] = (
                "billed as a net export in this bundled-era period, so SDG&E's actual "
                "charge for it is a deferred $0 under NEM 2.0 (not a real monthly price to "
                "compare against a modeled CCA credit)")
            excluded.append(row)

    actual_usd = _c(sum(r["actual_bundled_usd"] for r in priced))
    hyp_usd = _c(sum(r["hypothetical_cca_usd"] for r in priced))
    days = sum(p["days"] for p in periods_by_provider()["bundled"])
    delta = _c(hyp_usd - actual_usd)

    # The Direction B analogue of Direction A's excluded-export disclosure (issue #11
    # Codex review, defect 1). Here the ACTUAL bundled-side dollar for these cells really
    # is $0 (SDG&E genuinely charged nothing, deferred to the true-up -- not excluded from
    # a comparison that exists, unlike Direction A's CEA-side credit). What is excluded is
    # the MODELED hypothetical_cca_usd: what CEA's flat rate would have credited this
    # export energy, had CEA served this household in 2024 -- a hypothetical on top of a
    # hypothetical, since CEA never charged anything here at all. Reported for the same
    # reason as Direction A's figure -- so a reader can see the excluded scope is
    # materially larger than the priced delta -- but labeled modeled, not measured.
    excluded_net_export_hypothetical_cca_credit_usd = _c(
        sum(r["hypothetical_cca_usd"] for r in excluded))
    excluded_net_export_note = (
        f"these {len(excluded)} cells were billed as net exports in their bundled-era "
        "period, so SDG&E's actual charge for them is a genuine $0 (deferred to the "
        "annual true-up under NEM 2.0, not a comparison this analysis chose to omit). "
        "What CEA's flat charged rate would have credited this same exported energy, had "
        "CEA served this household in 2024, is a MODELED figure -- "
        f"{_money(excluded_net_export_hypothetical_cca_credit_usd)} total, larger than "
        f"the {_money(delta)} priced-cell delta above -- but it is a hypothetical on top of a "
        "hypothetical (CEA never charged anything in 2024) and is not compared against a "
        "real bundled-side price, because none exists for a deferred export bucket. Not "
        "folded into the priced total; see direction_a's analogous, measured disclosure "
        "for the same scope caveat on the recommendation basis.")

    return {
        "confidence": "modeled",
        "what": ("the household's actual per-(season, TOU) bundled-era (2024) kWh, priced "
                 "once at what SDG&E actually charged and once at CEA's own flat charged "
                 "rate (Phase 1's finding, applied here as an explicit assumption: CEA did "
                 "not serve this household in 2024, so this is a hypothetical, not an "
                 "observation)"),
        "n_periods": len(periods_by_provider()["bundled"]),
        "days": days,
        "scope": ("energy-only, the net-import cells this analysis can price -- no "
                  "product adders, no riders (see excluded_net_export_note for the "
                  "excluded net-export scope)"),
        "why_no_whole_period_scope": (
            "a whole-period-arrangement figure (matching bill_decomposition.py's second "
            "scope) would need CEA's 2024 product adders and CCA-only unbundling riders "
            "(PCIA-equivalent, ICA, EDP credit); CEA charged none of these in 2024 because "
            "it did not serve this household then, and no committed artifact or bill "
            "carries a value to assume in their place -- not determined, not invented"),
        "priced_cells": len(priced),
        "excluded_net_export_cells": len(excluded),
        "excluded_net_export_hypothetical_cca_credit_usd":
            excluded_net_export_hypothetical_cca_credit_usd,
        "excluded_net_export_note": excluded_net_export_note,
        "actual_bundled_usd": actual_usd,
        "hypothetical_cca_usd": hyp_usd,
        "delta_usd": delta,
        "delta_positive_means": "the CCA arrangement would have cost MORE than bundled did, same dates, same usage",
        "delta_usd_per_year": _annualize(delta, days),
        "excluded_detail": excluded,
        "priced_detail": priced,
    }


# ---------------------------------------------------------------------------
# Annualization (AC5) -- stated explicitly, one convention, used by both directions
# ---------------------------------------------------------------------------
def _annualize(delta_usd, days):
    """$/day over the sample, scaled to a 365.25-day year -- the same 365.25 convention
    extended_findings.py already uses for its own per-day-to-annual scaling
    (BSC_MONTH * 12 / 365.25). Both directions' sample periods have uneven day counts (32,
    30, 29, 5, 27, ...), so a per-period average would over-weight short periods; pricing
    the WHOLE sample's dollars against the WHOLE sample's days avoids that."""
    if days <= 0:
        raise SystemExit(f"cannot annualize a {days}-day sample")
    return _c(delta_usd / days * 365.25)


# ---------------------------------------------------------------------------
# AC4 -- provider effect vs vintage effect, generalizing bill_decomposition.py's identity
# ---------------------------------------------------------------------------
def provider_vs_vintage(direction_a_result):
    g0 = {}
    for season in SEASONS:
        for tou in TOU:
            try:
                g0[(season, tou)] = RH.rates_on(G0_QUERY_DATE[season]).generation(season, tou)
            except SystemExit:
                g0[(season, tou)] = None

    vintage_usd = 0.0
    no_g0 = []
    for row in direction_a_result["priced_detail"]:
        cell = (row["season"], row["tou_period"])
        g0v = g0[cell]
        if g0v is None:
            no_g0.append({"statement_date": row["statement_date"], "period": row["period"],
                          "season": row["season"], "tou_period": row["tou_period"],
                          "kwh": row["kwh"]})
            continue
        vintage_usd += row["kwh"] * (row["bundled_comparison_rate"] - g0v)
    vintage_usd = _c(vintage_usd)
    provider_usd = direction_a_result["delta_usd"]

    # The identity check: over the SAME reduced subset (rows with a valid g0), vintage +
    # provider must equal the total change (g1 - g0), to the cent. Computed from the SAME
    # unrounded per-row terms as vintage_usd above (rather than from provider_delta_usd,
    # which is independently rounded to cents per row) so the identity is not defeated by
    # accumulated per-row rounding.
    shared_rows = [row for row in direction_a_result["priced_detail"]
                  if g0[(row["season"], row["tou_period"])] is not None]
    shared_provider = _c(sum(
        row["kwh"] * (row["cca_rate"] - row["bundled_comparison_rate"]) for row in shared_rows))
    shared_total = _c(sum(
        row["kwh"] * (row["cca_rate"] - g0[(row["season"], row["tou_period"])])
        for row in shared_rows))
    identity_residual = _c(shared_total - (vintage_usd + shared_provider))
    if abs(identity_residual) > 0.02:
        raise SystemExit(
            f"provider ({shared_provider}) + vintage ({vintage_usd}) != total change "
            f"({shared_total}) over the shared subset, residual {identity_residual} -- "
            "the decomposition is not a clean identity and must not be published")

    return {
        "g0_bundled_charged_rate": {"/".join(k): v for k, v in g0.items()},
        "g0_query_dates": {k: str(v) for k, v in G0_QUERY_DATE.items()},
        "g0_note": ("the last BUNDLED-charged rate observed before the 12/27/24 CCA switch, "
                   "per season x TOU cell (RateSet.generation() at a date late in the "
                   "bundled era). summer/off_peak and winter/off_peak have NO bundled-"
                   "charged observation anywhere in the whole bundled era -- genuinely "
                   "absent (this household's off-peak buckets were net-export whenever "
                   "billed as bundled), not a parser gap"),
        "provider_effect_usd": provider_usd,
        "provider_effect_note": (
            "CEA vs SDG&E's bundled rate, SAME date -- exactly Direction A's headline "
            "delta, since Direction A already prices at the same-date comparison"),
        "vintage_effect_usd": vintage_usd,
        "vintage_effect_note": (
            "how much SDG&E's OWN bundled generation rate moved between the pre-switch "
            "baseline and each later CCA-era date, holding the household's real usage "
            "fixed -- attributable to tariff vintage, not to the provider switch"),
        "cells_with_no_vintage_baseline": no_g0,
        "cca_side_vintage_effect_usd": 0.0,
        "cca_side_vintage_effect_note": (
            "exactly zero, by construction: Phase 1 found CEA's own charged rate never "
            "moved once across all 18 statements on file, so none of the observed drift "
            "in what CEA-vs-bundled costs over time comes from the CCA side"),
        "identity_check": {
            "shared_subset_provider_usd": shared_provider,
            "shared_subset_vintage_usd": vintage_usd,
            "shared_subset_total_change_usd": shared_total,
            "residual_usd": identity_residual,
            "note": ("provider_usd + vintage_usd == total change (g1 - g0), to the cent, "
                     "over the subset of priced cells where a vintage baseline exists")},
    }


# ---------------------------------------------------------------------------
# AC6 -- reconciliation
# ---------------------------------------------------------------------------
def reconciliation(a, b, pv):
    ann_a, ann_b = a["delta_usd_per_year"], b["delta_usd_per_year"]
    pct_diff = _r(100.0 * (ann_b - ann_a) / ann_a, 1) if ann_a else None
    agree = pct_diff is not None and abs(pct_diff) <= 20.0
    explanation = None
    if not agree:
        explanation = (
            f"Direction A (modeled -- same-date bill rates, {a['days']} days spanning two "
            f"summers and two winters) finds ${ann_a}/yr; Direction B (modeled, {b['days']} days, "
            "May-Dec 2024 only -- one summer, a partial winter, no Jan-Apr) finds "
            f"${ann_b}/yr, {pct_diff:+.1f}% different. Rate vintage is a REAL, LARGE, and "
            "LIKELY MAJOR contributor to this gap: SDG&E's own bundled generation rate rose "
            "substantially across the observed span (see data/rate_vintages.csv's "
            "generation_printed_comparison rows -- summer on-peak alone moved 0.38826 -> "
            "0.40592 -> 0.47019) while CEA's charged rate never moved (Phase 1's flat-rate "
            "finding), and this script's own provider_vs_vintage split measures that vintage "
            f"effect at ${pv['vintage_effect_usd']} over the Direction-A sample -- more than "
            f"double the ${pv['provider_effect_usd']} provider effect over the same sample. "
            "Direction A nets this vintage drift out by construction, because it always "
            "compares CEA against the bundled rate ON THE SAME DATE; Direction B cannot, "
            "because there is no 2024 CCA rate to anchor to, so it necessarily compares "
            "2024's cheaper bundled rate against CEA's rate as observed in 2025-2026. "
            "WHAT THIS DOES NOT ESTABLISH: the "
            f"${pv['vintage_effect_usd']} vintage-effect figure is computed entirely from "
            f"Direction A's own {a['n_periods']}-period, {a['days']}-day sample and its own "
            "priced cells -- there is no mathematical identity tying it to Direction B's "
            "own result, because Direction A and Direction B differ in more than rate "
            f"vintage alone: different periods ({a['n_periods']} vs {b['n_periods']}), "
            "different seasonal composition (two summers and two winters vs one summer and "
            "a partial winter missing Jan-Apr), different usage weights, and a partly "
            "different set of included cells -- none of this has been separately "
            "decomposed, so it is NOT RULED OUT as a contributor to the "
            f"{pct_diff:+.1f}% gap. So: the vintage effect is verified, real, and large "
            "enough relative to the provider effect to be the most plausible dominant "
            "driver of the gap -- but the gap is NOT PROVEN to be fully attributable to "
            "rate vintage alone; a rigorous common-weight, common-cell decomposition across "
            "Direction A and Direction B has not been performed. This does not change the "
            "recommendation basis: Direction A's same-date comparison avoids vintage drift "
            "by construction, while Direction B has no 2024 CCA rate to anchor to and "
            "necessarily mixes multiple effects, and that reasoning holds on its own terms "
            "whether or not the full gap is ever decomposed. Direction B's larger figure is "
            "not averaged with Direction A's.")
    return {
        "direction_a_annualized_usd": ann_a,
        "direction_b_annualized_usd": ann_b,
        "pct_difference_b_vs_a": pct_diff,
        "agree_within_20pct": agree,
        "explanation_if_disagreeing": explanation,
    }


# ---------------------------------------------------------------------------
# AC3 -- PCIA and franchise fee, both sides, with direct bill-line citations
# ---------------------------------------------------------------------------
_TEXT_CACHE = {}


def _statement_text(stmt):
    if stmt not in _TEXT_CACHE:
        path = ELEC_DIR / f"sdge_electric_{stmt}.pdf"
        if not path.exists():
            raise SystemExit(
                f"statement {stmt} not found at {path} -- the PCIA and franchise-fee bill "
                "lines appear only on the statement PDFs, not in any committed artifact, "
                "so this citation cannot proceed without it")
        with pdfplumber.open(path) as pdf:
            _TEXT_CACHE[stmt] = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    return _TEXT_CACHE[stmt]


_BUNDLED_PCIA = re.compile(
    r"\$(" + _NUM + r")\s+of\s+your\s+Electricity\s+Generation\s+Charge\s+is\s+your\s+"
    r"bundled\s+PCIA\s+charge")
_FRANCHISE = re.compile(
    r"(Franchise Fee[A-Za-z ]*?)\s+(" + _NUM + r")\s*x\s*([\d.]+)%\s*(?:Others\s*)?"
    r"(\.\d+|\d+\.\d+)")


def _franchise_line(stmt):
    flat = re.sub(r"\s+", " ", _statement_text(stmt))
    m = _FRANCHISE.search(flat)
    if m is None:
        raise SystemExit(f"{stmt}: no franchise-fee line found on this statement -- the "
                         "franchise-fee treatment claim is unevidenced without it")
    return {"label": m.group(1).strip(), "base_usd": float(m.group(2).replace(",", "")),
            "pct": float(m.group(3)), "result_usd": float(m.group(4)),
            "quoted": re.sub(r"\s+", " ", m.group(0))}


def pcia_and_franchise_evidence():
    m = _BUNDLED_PCIA.search(_statement_text(BASE_STMT))
    if m is None:
        raise SystemExit(
            f"{BASE_STMT}: the bundled-PCIA sentence is not on this statement, so the claim "
            "that the bundled generation rate carries PCIA is unevidenced -- refusing to "
            "publish AC3 on an assumption")
    bundled_pcia_sentence = re.sub(r"\s+", " ", m.group(0))
    bundled_pcia_usd = float(m.group(1).replace(",", ""))

    base_franchise = _franchise_line(BASE_STMT)
    current_franchise = _franchise_line(CURRENT_STMT)

    return {
        "pcia": {
            "what_pcia_is": ("Power Charge Indifference Adjustment -- a per-kWh CCA-only "
                             "rider, $0.02828/kWh on NET kWh per analysis/rates.py, charged "
                             "as its own line item by SDG&E during CCA service (not by CEA "
                             "-- PCIA appears on the SDG&E side of the bill even in a CCA "
                             "period, e.g. \"PCIA 2023 1,001 kWh x $.02828 28.31\" on the "
                             f"{CURRENT_STMT} statement)"),
            "bundled_pcia_statement": BASE_STMT,
            "bundled_pcia_sentence_quoted": bundled_pcia_sentence,
            "bundled_pcia_usd_this_statement": bundled_pcia_usd,
            "finding": (
                "bundled customers do not see a separate PCIA line, but the base statement "
                "says on its own face that PCIA is baked into the bundled generation "
                "charge -- confirming provider_effect_whole_period()'s own "
                "why_the_riders_belong_on_this_side note, independently re-verified here "
                "against the raw PDF rather than assumed from that module's prior finding"),
            "does_this_resolve_rates_pys_net_negative_ambiguity": (
                "NO -- rates.py documents PCIA-on-net-kWh as untested for a net-negative "
                "period because no bundled statement in this household's own bundled-era "
                "corpus (2024-06-27 .. 2024-12-30) ever went net-negative for the WHOLE "
                "period; this statement's sentence establishes only THAT bundled service "
                "carries a PCIA-equivalent component inside its generation rate, not HOW "
                "that component behaves in a net-negative month, because the bundled "
                "generation charge is not itemized per-kWh anywhere on the bill (it is a "
                "single dollar figure, \"$1.97 of your Electricity Generation Charge\", not "
                "a rate applied to a kWh quantity) -- not determined; the bundled side is "
                "genuinely more opaque than the CCA side here, not merely unobserved"),
        },
        "franchise_fee": {
            "bundled": {"statement": BASE_STMT, **base_franchise},
            "cca": {"statement": CURRENT_STMT, **current_franchise},
            "finding": (
                "the franchise-fee LINE ITEM survives the provider switch under both names, "
                "but its BASE changed: on the bundled statement the fee is 1.10% of the "
                f"Wildfire Fund Charge alone (${base_franchise['base_usd']:.2f} on "
                f"{BASE_STMT}, a small non-bypassable per-kWh charge on gross imports), "
                "while on the CCA statement it is 1.10% of a much larger figure "
                f"(${current_franchise['base_usd']:.2f} on {CURRENT_STMT}) under the "
                "renamed line \"Franchise Fee Equivalent Surcharge\" -- consistent with "
                "California CCAs collecting a franchise-fee-equivalent surcharge on "
                "generation revenue specifically because that revenue no longer flows "
                "through the utility once a customer enrolls in a CCA, so cities would "
                "otherwise lose the franchise fee on it"),
            "what_the_189_93_base_is": (
                "not determined from the bill text alone: it is neither the CCA Electric "
                "Generation Charges total, nor Total Electric Charges, nor any single "
                "printed subtotal on this statement that this script could reconcile to "
                "the cent -- disclosed as not-determined rather than guessed"),
            "does_franchise_fee_need_its_own_provider_term": (
                "the dollar difference is small on these two anchor statements "
                f"(${base_franchise['result_usd']:.2f} bundled vs "
                f"${current_franchise['result_usd']:.2f} CCA, "
                f"${current_franchise['result_usd'] - base_franchise['result_usd']:+.2f}) "
                "but the underlying BASE is not proportional to the same quantity in both "
                "eras, so the CLAUDE.md-suggested shortcut (franchise fee tracks whatever "
                "moves gross imports, so it needs no separate term) does not hold as "
                "stated: it is real, provider-attributable, and not captured by Direction "
                "A/B's per-TOU-cell repricing (which prices generation energy only). No "
                "committed artifact extracts this line for all 26 periods, so its "
                "systematic dollar contribution across the whole corpus is not determined "
                "here; on these two anchor statements it is a few dollars per period, far "
                "smaller than the generation-rate terms above"),
        },
    }


# ---------------------------------------------------------------------------
# AC5 -- recommendation
# ---------------------------------------------------------------------------
def recommendation(a, b, recon):
    ann_a = a["delta_usd_per_year"]
    excl_usd = a["excluded_net_export_cca_credit_usd"]
    excl_n = a["excluded_net_export_cells"]
    mixed_usd = a["excluded_mixed_sign_cca_usd"]
    mixed_n = a["excluded_mixed_sign_cells"]
    return {
        "headline_basis": "direction_a (modeled -- same-date bill rates)",
        "why_not_direction_b": (
            "Direction B mixes two years of bundled-side rate inflation into what looks "
            "like a provider effect (see reconciliation.explanation_if_disagreeing); "
            "Direction A's same-date comparison does not, and covers a much longer, more "
            "representative sample (547 days vs 216)"),
        "annual_dollar_result_usd": ann_a,
        "confidence": "modeled",
        "confidence_detail": (
            "same tier and reasoning as direction_a_cca_repriced_at_bundled."
            "confidence_detail: this figure is computed by multiplying this household's own "
            "measured billed kWh by SDG&E's own real, same-date printed bundled-generation "
            "comparison rate, so both inputs are genuine bill figures, but the annual dollar "
            "result is this analysis's own reconstruction, not an amount anyone was ever "
            "actually billed -- MODELED, qualified 'same-date bill rates', not MEASURED"),
        "scope_caveat": (
            "this figure prices ONLY the net-import cells (the ones where both a CEA "
            "charge and a same-date SDG&E bundled comparison exist and agree in sign); "
            f"it excludes {excl_n} net-export cells worth {_money(excl_usd)} in actual CEA "
            "export credits that this analysis cannot price on the bundled side at all "
            "(NEM 2.0 defers a net-export bucket's dollar value to the annual true-up, so "
            "SDG&E prints no same-date bundled comparison to compare against -- see "
            f"direction_a.excluded_net_export_note), plus {mixed_n} further cells worth "
            f"{_money(mixed_usd)} whose printed comparison segments carry BOTH signs "
            "within the same billing period and so cannot be priced as one blended "
            "figure (direction_a.excluded_mixed_sign_note). The whole-household answer "
            "to 'was switching to the CCA a win' is therefore NOT FULLY DETERMINED by "
            "this analysis."),
        "text": (
            "On the same-date, energy-only comparison (Direction A, modeled from this "
            "household's own billed kWh and SDG&E's own same-date printed bundled "
            f"comparison rate), staying on CEA's CCA would have cost this household about "
            f"${ann_a}/yr more than bundled SDG&E generation -- but ONLY on the net-import "
            "cells this analysis can price. That priced-cell answer is well-evidenced and "
            "it is small (individual periods in this sample range from a net CCA saving of "
            "roughly $14 to a net CCA cost of roughly $27 -- see direction_a.priced_detail "
            "for the per-period provider_delta_usd figures). It is NOT the full answer to "
            f"whether switching to the CCA was a win for this household: {excl_n} cells "
            f"were billed as net exports and carry a real CEA credit of {_money(excl_usd)} -- "
            "several times the size of the priced-cell delta -- and SDG&E's bill prints "
            "no same-date bundled comparison for those cells at all, because a "
            "net-export bucket's dollar value defers to the annual true-up under NEM "
            "2.0 rather than pricing monthly (direction_a.excluded_net_export_note). "
            "Reconstructing what the bundled side would have credited for that exported "
            "energy would mean rebuilding the whole NEM annual true-up, which is out of "
            f"scope here. A further {mixed_n} cells, worth {_money(mixed_usd)} in real CEA "
            "charges, are excluded for a different reason: their printed comparison "
            "segments carry BOTH signs within the same billing period (part of the "
            "period net-exported, part net-imported), so pricing the whole cell as one "
            "blended figure would average a real per-kWh rate together with a genuine "
            "non-price into an effective rate no tariff ever printed -- see "
            "direction_a.excluded_mixed_sign_note. So: the priced-cell answer "
            f"(${ann_a}/yr, CCA costs slightly "
            "more) is a modeled, same-date-bill-rate figure with strong evidentiary "
            "backing, but the LARGER, unresolved net-export effect (and the smaller, "
            "unpriceable mixed-sign one) means the true "
            "whole-household comparison is NOT FULLY DETERMINED by this analysis -- "
            f"stated honestly rather than letting the ${ann_a}/yr figure stand in for a "
            "conclusion it does not support. Two smaller facts bear on the PRICED "
            "portion of the decision, neither of which changes that number: (1) CEA's "
            "own charged rate has not moved once in 18 months on file, while SDG&E's "
            "bundled generation rate has moved up substantially over the same span "
            "(provider_vs_vintage_split.vintage_effect_usd) -- if that pattern "
            "continues, the small current CCA premium on the priced cells would likely "
            "shrink or reverse, but this is a directional observation about the past, "
            "not a projection; (2) the franchise-fee base changed with the provider "
            "switch in a way this script cannot fully price (see "
            "pcia_and_franchise_fee.franchise_fee), so the priced-cell gap could be "
            "marginally larger than stated, by an amount too small on the evidence "
            "available to change this reading."),
    }


# ---------------------------------------------------------------------------
# Assembly and the committed artifact
# ---------------------------------------------------------------------------
def build():
    cca_cells = cca_period_cells()
    cca_adders = cca_period_adders()
    flat_rate = cca_flat_rate()

    a = direction_a(cca_cells)
    b = direction_b(flat_rate)
    pv = provider_vs_vintage(a)
    recon = reconciliation(a, b, pv)
    pcia_franchise = pcia_and_franchise_evidence()
    rec = recommendation(a, b, recon)

    cip_total = _c(sum(v.get("clean_impact_plus", 0.0) for v in cca_adders.values()))
    sst_total = _c(sum(v.get("state_surcharge_tax", 0.0) for v in cca_adders.values()))

    return {
        "generated_by": "analysis/cca_bundled_counterfactual.py",
        "question": ("was switching from bundled SDG&E generation to the CCA (Clean Energy "
                     "Alliance) a win, priced both directions on this household's own "
                     "actual usage"),
        "phase_1_inputs": {
            "data/cca_generation_rates.csv": "CEA's own per-period per-cell charges (issue #11 Phase 1)",
            "data/bill_periods_electric.csv": "the period universe, days, generation_provider",
            "data/bill_tou_detail.csv": "bundled-era per-cell kWh for Direction B",
            "analysis/rates_history.py": "RateSet.generation() / generation_comparison_table(), read-only",
        },
        "direction_a_cca_repriced_at_bundled": a,
        "direction_b_bundled_repriced_at_cca": b,
        "provider_vs_vintage_split": pv,
        "reconciliation": recon,
        "pcia_and_franchise_fee": pcia_franchise,
        "cca_product_adders_over_the_whole_corpus": {
            "clean_impact_plus_usd": cip_total,
            "state_surcharge_tax_usd": sst_total,
            "note": ("CEA's own period-level product adders, actual and already-reconciled "
                     "(Phase 1), summed over all 19 CCA periods -- small next to the "
                     "generation-rate terms above and not part of either direction's "
                     "energy-only headline; no bundled-side equivalent exists because "
                     "Clean Impact Plus is CEA's own optional product tier"),
        },
        "recommendation": rec,
        "not_determined": [
            "a whole-period-arrangement figure for Direction B (CEA's 2024 product adders "
            "and CCA-only unbundling riders on 2024 usage) -- CEA did not serve this "
            "household in 2024, so no rider value exists to apply",
            "PCIA / Incremental Procurement Cost Adjustment / Economic Development Program "
            "credit values for all 19 CCA periods (only the one hardcoded CURRENT statement "
            "in bill_decomposition.py has these extracted) -- so a full whole-period-"
            "arrangement scope, matching provider_effect_whole_period()'s second figure, "
            "cannot be computed systematically here",
            "whether rates.py's PCIA-on-net-kWh-in-a-net-negative-period ambiguity is "
            "resolved by the bundled side -- it is not; see pcia_and_franchise_fee.pcia."
            "does_this_resolve_rates_pys_net_negative_ambiguity",
            "the exact dollar base of the CCA-era franchise-fee-equivalent surcharge "
            "($189.93 on the 2026-07-02 statement) -- could not be reconciled to any "
            "single printed subtotal on the statement",
            "the provider/vintage split for summer/off_peak and winter/off_peak "
            f"({len(pv['cells_with_no_vintage_baseline'])} of the {a['priced_cells']} "
            "Direction-A-priced rows) -- no bundled-charged rate exists anywhere in the "
            "bundled era for these two cells to serve as a vintage baseline",
            "the bundled-side counterfactual dollar value of the "
            f"{a['excluded_net_export_cells']} net-export cells excluded from Direction A "
            f"({_money(a['excluded_net_export_cca_credit_usd'])} in actual CEA export credits) "
            "and of Direction B's analogous excluded cells -- NEM 2.0 defers a net-export "
            "bucket's dollar value to the annual true-up, so no same-date bundled "
            "comparison is printed to price it against; reconstructing it would mean "
            "rebuilding the whole NEM annual true-up, out of scope here (see "
            "direction_a.excluded_net_export_note, direction_b.excluded_net_export_note). "
            "Because of this, the whole-household answer to whether switching to the CCA "
            "was a win is NOT FULLY DETERMINED by this analysis -- only the priced "
            "net-import subset is (see recommendation.text)",
            "the bundled-side counterfactual dollar value of the "
            f"{a['excluded_mixed_sign_cells']} cells excluded from Direction A because "
            f"their printed comparison segments carry both signs within the same billing "
            f"period ({_money(a['excluded_mixed_sign_cca_usd'])} in actual CEA charges) -- "
            "the real-priced sub-segment's own value is not separately determined here "
            "either, since no bill line prices it apart from the export sub-segment it "
            "shares a cell with (see direction_a.excluded_mixed_sign_note)",
        ],
    }


def write(dest_dir=None):
    dest = pathlib.Path(dest_dir or DATA)
    out = build()
    path = dest / "cca_bundled_counterfactual.json"
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
    a = out["direction_a_cca_repriced_at_bundled"]
    b = out["direction_b_bundled_repriced_at_cca"]
    recon = out["reconciliation"]
    print(f"wrote {path}")
    print(f"Direction A (modeled -- same-date bill rates): "
          f"${a['delta_usd_per_year']}/yr over {a['days']} days")
    print(f"Direction B (modeled):  ${b['delta_usd_per_year']}/yr over {b['days']} days")
    print(f"agree within 20%: {recon['agree_within_20pct']} "
          f"({recon['pct_difference_b_vs_a']}% difference)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
