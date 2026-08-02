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

DIRECTION A -- the 19 CCA periods repriced at bundled rates (MEASURED).
For each CCA period's actual per-(season, TOU) kWh (data/cca_generation_rates.csv, already
bill-reconciled to the cent), this asks what SDG&E's OWN bundled-generation comparison table
printed for that exact date (RateSet.generation_comparison_table -- the same-statement
diagnostic bill_decomposition.py's module docstring documents at length: on a CCA date this
is SDG&E's bundled-generation (EECC) comparison, printed for reference, not a tariff charged,
and it is the only place a same-date bundled rate exists for a CCA-billed date). This is
MEASURED, not modeled: SDG&E prints this exact comparison figure on the household's own bill,
for the household's own usage, on the household's own date -- nothing here is projected.

Three-way disposition of each (period, season, TOU) cell, decided ONLY by whether it was
billed as a net import (kWh > 0) and whether a same-date bundled figure exists, exactly
mirroring provider_effect_whole_period()'s own net-import/net-export split (adapted here
across all 19 periods rather than one):
  PRICED (kWh > 0, comparison present)   the only cells "energy-only" sums. If a net-import
                                          cell ever lacked a comparison this raises -- see
                                          bill_decomposition's own "no same-date SDG&E bundled
                                          comparison" refusal -- but empirically none does.
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
Direction-A rows (6 of the 50 priced rows) still count fully toward Direction A's own
headline (which needs no g0), they are simply excluded from the vintage/provider split and
disclosed as such.

AC6 -- THE TWO DIRECTIONS DISAGREE BY WELL OVER 20%, AND THE REASON IS NAMED, NOT AVERAGED
AWAY. Direction A (measured, 547 days, spans two summers and two winters) finds the CCA
premium at about $48/yr. Direction B (modeled, 216 days, May-Dec 2024 only -- one summer and
one partial winter, no Jan-Apr) finds about $136/yr, roughly 2.8x larger. The provider/vintage
split above supplies the mechanism: SDG&E's own bundled generation rate ROSE substantially
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


# ---------------------------------------------------------------------------
# Direction A -- 19 CCA periods repriced at SDG&E's same-date bundled comparison (MEASURED)
# ---------------------------------------------------------------------------
def direction_a(cca_cells):
    priced, absent, carried_export = [], [], []
    for p in periods_by_provider()["CCA"]:
        key = (p["statement_date"], p["period"])
        for (season, tou), c in cca_cells.get(key, {}).items():
            d = _rep_date(season, p["start"], p["end"])
            try:
                s1 = RH.rates_on(d).generation_comparison_table(season, tou)
                have_s1 = True
            except SystemExit:
                s1, have_s1 = None, False
            row = {"statement_date": p["statement_date"], "period": p["period"],
                   "season": season, "tou_period": tou, "kwh": c["kwh"],
                   "cca_rate": c["rate"], "cca_usd": c["usd"]}
            if c["kwh"] > 0:
                if not have_s1:
                    raise SystemExit(
                        f"{key} {season}/{tou}: billed as a net import (kWh={c['kwh']}) but "
                        "no same-date SDG&E bundled comparison exists -- Direction A has no "
                        "counterfactual for this cell and must not estimate one")
                row["bundled_comparison_rate"] = s1
                row["bundled_comparison_usd"] = _c(c["kwh"] * s1)
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
    return {
        "confidence": "measured",
        "what": ("the household's actual per-(season, TOU) CCA-era kWh, priced once at "
                 "what CEA actually charged and once at SDG&E's own same-date printed "
                 "bundled-generation comparison -- both numbers are printed on the "
                 "household's real bills; nothing here is projected"),
        "n_periods": len(periods_by_provider()["CCA"]),
        "n_statements": len({p["statement_date"] for p in periods_by_provider()["CCA"]}),
        "days": days,
        "scope": "energy-only, the common (net-import) cells -- no product adders, no riders",
        "priced_cells": len(priced),
        "excluded_absent_cells": len(absent),
        "excluded_carried_export_cells": len(carried_export),
        "cca_charged_usd": cca_usd,
        "bundled_comparison_usd": bundled_usd,
        "delta_usd": delta,
        "delta_positive_means": "the CCA arrangement cost MORE than bundled would have, same dates, same usage",
        "delta_usd_per_year": _annualize(delta, days),
        "excluded_absent_detail": absent,
        "excluded_carried_export_detail": carried_export,
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
    directly rather than through that loader."""
    bundled_periods = {p["period"] for p in periods_by_provider()["bundled"]}
    agg = {}
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
    return {
        "confidence": "modeled",
        "what": ("the household's actual per-(season, TOU) bundled-era (2024) kWh, priced "
                 "once at what SDG&E actually charged and once at CEA's own flat charged "
                 "rate (Phase 1's finding, applied here as an explicit assumption: CEA did "
                 "not serve this household in 2024, so this is a hypothetical, not an "
                 "observation)"),
        "n_periods": len(periods_by_provider()["bundled"]),
        "days": days,
        "scope": "energy-only, the common (net-import) cells -- no product adders, no riders",
        "why_no_whole_period_scope": (
            "a whole-period-arrangement figure (matching bill_decomposition.py's second "
            "scope) would need CEA's 2024 product adders and CCA-only unbundling riders "
            "(PCIA-equivalent, ICA, EDP credit); CEA charged none of these in 2024 because "
            "it did not serve this household then, and no committed artifact or bill "
            "carries a value to assume in their place -- not determined, not invented"),
        "priced_cells": len(priced),
        "excluded_net_export_cells": len(excluded),
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
            f"Direction A (measured, {a['days']} days spanning two summers and two "
            f"winters) finds ${ann_a}/yr; Direction B (modeled, {b['days']} days, "
            "May-Dec 2024 only -- one summer, a partial winter, no Jan-Apr) finds "
            f"${ann_b}/yr, {pct_diff:+.1f}% different. The gap is a rate-vintage effect, "
            "not a disagreement about the provider comparison: SDG&E's own bundled "
            "generation rate rose substantially across the observed span (see "
            "data/rate_vintages.csv's generation_printed_comparison rows -- summer "
            "on-peak alone moved 0.38826 -> 0.40592 -> 0.47019) while CEA's charged rate "
            "never moved (Phase 1's flat-rate finding). Direction A nets this out because "
            "it always compares CEA against the BUNDLED RATE ON THE SAME DATE. Direction B "
            "cannot: there is no 2024 CCA rate to anchor to, so it necessarily compares "
            "2024's cheaper bundled rate against CEA's rate as observed in 2025-2026 -- "
            f"exactly the vintage effect this script's own provider_vs_vintage split "
            f"measures at ${pv['vintage_effect_usd']} over the Direction-A sample "
            "(against a provider effect of only "
            f"${pv['provider_effect_usd']} over the same sample). Direction B's larger "
            "figure is not averaged with Direction A's: it answers a structurally "
            "different, vintage-mixed question, and Direction A is the like-for-like "
            "reading.")
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
    return {
        "headline_basis": "direction_a (measured)",
        "why_not_direction_b": (
            "Direction B mixes two years of bundled-side rate inflation into what looks "
            "like a provider effect (see reconciliation.explanation_if_disagreeing); "
            "Direction A's same-date comparison does not, and covers a much longer, more "
            "representative sample (547 days vs 216)"),
        "annual_dollar_result_usd": ann_a,
        "confidence": "measured",
        "text": (
            f"On the measured, same-date, energy-only comparison (Direction A), staying on "
            f"CEA's CCA has cost this household about ${ann_a}/yr more than bundled SDG&E "
            "generation would have, on the cells where both are priceable. That is a small "
            "amount relative to this household's typical per-period electric bill "
            "swings (individual periods in this sample range from a net CCA saving of "
            "roughly $14 to a net CCA cost of roughly $27 -- see direction_a.priced_detail "
            "for the per-period provider_delta_usd figures), and it does not by itself "
            "justify the effort of opting out of CCA back to bundled service. Two "
            "additional facts bear on the decision, neither of which changes the headline "
            "number: (1) CEA's own charged rate has not moved once in 18 months on file, "
            "while SDG&E's bundled generation rate has moved up substantially over the "
            "same span (provider_vs_vintage_split.vintage_effect_usd) -- if that pattern "
            "continues, the small current CCA premium would likely shrink or reverse, but "
            "this is a directional observation about the past, not a projection; (2) the "
            "franchise-fee base changed with the provider switch in a way this script "
            "cannot fully price (see pcia_and_franchise_fee.franchise_fee), so the true "
            "gap could be marginally larger than the headline states, by an amount too "
            "small on the evidence available to change the recommendation."),
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
            "the provider/vintage split for summer/off_peak and winter/off_peak (6 of the "
            "50 Direction-A-priced rows) -- no bundled-charged rate exists anywhere in the "
            "bundled era for these two cells to serve as a vintage baseline",
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
    print(f"Direction A (measured): ${a['delta_usd_per_year']}/yr over {a['days']} days")
    print(f"Direction B (modeled):  ${b['delta_usd_per_year']}/yr over {b['days']} days")
    print(f"agree within 20%: {recon['agree_within_20pct']} "
          f"({recon['pct_difference_b_vs_a']}% difference)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
