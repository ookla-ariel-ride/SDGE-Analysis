#!/usr/bin/env python3
"""What is this household's NEM 2.0 grandfathering actually worth? (issue #9, Phase 1)

Replaces the flat 3-cent/5-cent/8-cent export-price bracket in data/extra_results.json's
`nbt` block (gf_value: $1,772-$2,268/yr; out of scope here, tracked as issue #34 -- that
file is NOT touched by this script) with a REAL calculation built from SDG&E's own
published hourly Net Billing Tariff (NBT, "Solar Billing Plan") export rates, priced
against this household's own measured Green Button export profile.

WHAT CHANGED, AND WHY (CLAUDE.md 9 -- explain figure movement by mechanism, never
"replaces X"; this note stays in this docstring/TECHNICAL.md, never in report prose):
  The old bracket applied ONE flat cents-per-kWh export credit to every export kWh,
  all year, with the 3-8c width standing in for "we don't know the exact number."
  SDG&E's own published MIDAS export-pricing table (see RAW_FILES below) shows the
  REAL hourly export credit swings from ~$0.01/kWh (spring midday, when the Avoided
  Cost Calculator prices oversupplied solar at almost nothing) up to ~$1.08/kWh
  (August 6-9pm, the post-solar evening ramp) -- a >100x range within one calendar
  year, not a 3-8c band. Because this household's solar exports are concentrated at
  midday (production hours), applying the real hourly schedule to the real export
  shape yields a KWh-WEIGHTED realized credit of ~4.7c/kWh -- close to the simple
  midpoint of the old flat guess, but for a different, traceable reason: the old
  band's width was an assumed sensitivity range with no data behind it, while the
  new figure is a single computed number, reproducible from a committed public rate
  table and this household's own committed billing engine.

PROVENANCE (public utility data, safe to commit -- see data/nbt_export_rates_2026.csv):
  private/1-raw-data/sdge_nbt_export_rates/Current Year NBT Pricing Upload MIDAS.csv
    downloaded 2026-08-01 from https://www.sdge.com/solar/solar-billing-plan/export-pricing
    ("Current 2026 Export Pricing" link). Its own readme states dates/times are UTC;
    DayStart/DayEnd/ValueName are Pacific Prevailing Time; RIN containing "USCA-SDXX" is
    the Delivery export-rate component, "USCA-XXSD" is Generation; RateName "NBT00" is
    the no-lock-in / current-year vintage (only the current year's PST rates are the
    ACTUAL effective rates -- everything else in the file is illustrative only).
  private/1-raw-data/sdge_nbt_export_rates/LY2026 NBT Pricing Upload MIDAS.csv
    downloaded 2026-08-01 from the SAME page's "Legacy 2026 Export Pricing" link
    (https://www.sdge.com/sites/default/files/LY2026%20NBT%20Pricing%20Upload%20MIDAS.zip).
    FETCHED BY THIS SCRIPT'S AUTHOR DURING PHASE 1, not originally staged: the file that
    WAS staged ("Current Year...") turned out to contain ONLY RateName=NBT00 rows --
    zero NBT25, zero NBT26 -- confirmed by grep across the full 350,640-row file. SDG&E
    publishes each rate vintage as its own separate MIDAS download (a "current year"
    no-lock file, plus one "Legacy <year>" file per 9-year-lock vintage); they are not
    combined. This second file is 100% RateName=NBT26, the vintage issue #9's AC3 needs
    as the locked-in end of the uncertainty band. See PROVENANCE.txt in that directory
    for the full note.

GENUINE FINDING, FLAGGED RATHER THAN PAPERED OVER (materially affects AC3's "range"):
  NBT26's and NBT00's calendar-year-2026 rate tables are BYTE-IDENTICAL. Checked
  directly: merging both files' 2026 rows on (RIN-component, ValueName) gives 1,152
  matched cells with max|NBT00 - NBT26| = 0.0. SDG&E computes both vintages from the
  same year's Avoided Cost Calculator; NBT26 vintage customers then get today's table
  LOCKED for 9 years from PTO, while NBT00 customers get only THIS year's table
  guaranteed (next year resets to a new, not-yet-published table) -- the vintages
  differ in which FUTURE years are contractually guaranteed, not in this year's price
  level. Consequently, the "NBT26 vs NBT00" grandfathering-value band computed here for
  a ONE-YEAR snapshot has ZERO WIDTH: both ends are the same dollar figure. Widening it
  into a real band would require pricing multiple future years under each vintage's
  actual guarantee (NBT26 flat for 9 years vs NBT00's genuinely unknown annual resets)
  -- a multi-year model outside this phase's one-year-bill scope. Both vintages are
  still computed and published separately below (never collapsed into one to hide
  this), so a reader can see they coincide rather than being told they were assumed to.

EXPORT CREDIT FORMULA (this household is on a CCA, so SDG&E's own Generation-rate
component does not directly apply to it):
  Clean Energy Alliance's "Solar Impact" program page
  (https://thecleanenergyalliance.org/solar-impact/, fetched 2026-08-01) states:
  "The energy values are based on the same export credit pricing paid by San Diego
  Gas & Electric (SDG&E)" and "Solar Impact customers earn an additional $0.01 per
  kWh for energy they export to the grid. This $0.01 per kWh is in addition to the
  credit based on SDG&E's pricing." So this household's real NBT export credit =
  SDG&E Delivery (RIN "USCA-SDXX") + SDG&E Generation (RIN "USCA-XXSD", because CEA's
  own page says it uses "the same ... pricing paid by ... SDG&E") + a flat
  $0.01/kWh CEA bonus (CEA_BONUS_USD_PER_KWH below).

IMPORT-SIDE RATE (NEM 3.0/NBT customers under a CCA): rates.py does not define a
separate "NBT import rate" because none is needed -- what changes under NBT is the
NETTING STRUCTURE (no monthly per-period netting of imports against exports at
retail rate), not the per-kWh import PRICE. A CCA customer still pays the same TOU
delivery+generation+PCIA+NBC schedule per kWh of gross import regardless of NEM
version; rates.allin(season, period) already IS that gross per-kWh price. This
script reuses the SAME assumption extended_findings.py's existing bill_flat_export()
already encodes for its own (battery-marginal-only) NBT proxy: import billed GROSS
at rates.allin() per (month, season, TOU period), no netting; export credited
separately. This script's contribution is replacing that function's ONE flat export
credit with the real hourly schedule below, at the finer (month, day-type, hour)
resolution the Avoided Cost Calculator actually publishes.

MODELED vs ACTUAL (CLAUDE.md 0): both the NEM 2.0 and NEM 3.0/NBT figures below come
from the SAME canonical modeling approach (rates.bill_nem for NEM 2.0; the analogous
gross-import + hourly-export-credit engine here for NBT) applied to the SAME measured
365-day Green Button window -- so while each side's ABSOLUTE dollar figure runs high
relative to this household's actual audited bills (documented elsewhere: the interval
model overstates the annual bill roughly 40-50%), the DELTA between the two sides
(the grandfathering value) is far more robust, because the same systematic modeling
bias is present on both sides and largely cancels in the subtraction. The actual
billed total for the matching window (from the committed data/electric_bill_summary.csv)
is reported alongside for context, labeled ACTUAL, never substituted into the delta.

Fail-closed + atomic (CLAUDE.md 9): the export-rate lookup ABORTS (SystemExit) on any
(month, day-type, hour) bucket the committed rate table does not cover -- it is never
silently interpolated or zero-filled. The committed CSV and JSON are both written to
temp files and os.replace'd; a failed/partial run changes neither.

PHASE 3 ADDITION -- battery value under the REAL hourly NBT schedule, reconciled
with extended_findings.py's nbt_2039 (issue #9 AC6):
  extended_findings.py's own nbt_2039 block prices the price-aware battery's
  MARGINAL bill savings (no-battery bill minus with-battery bill) under three
  FLAT export-credit assumptions (3c/5c/8c: $2,540/$2,527/$2,506/yr) and, for
  comparison, under NEM 2.0 today ($2,329/yr) -- see data/extended_results.json
  -> nbt_2039, NOT reimplemented here, only read as a committed reference. This
  script adds the analogous figure priced against the REAL hourly NBT export
  schedule this file already builds (data/nbt_export_rates_2026.csv), reusing
  the SAME battery dispatch (battery_dispatch_policies.run_batt(d, imp0, gen0,
  13.5, "greedy")) so the physical battery behavior is identical across both
  figures -- only the export-pricing assumption differs. Both the no-battery
  and with-battery physical series are billed through THIS script's own
  bill_nbt(), for both NBT26 and NBT00 vintages, and the marginal (no-battery
  minus with-battery) is compared explicitly against each of the four nbt_2039
  reference figures (3c, 5c, 8c, NEM 2.0) -- the disagreement is stated in
  dollars, never averaged or hidden, per the issue's AC6.

Run from private/verify with usage.csv, behavior_rebuild.py and rates.py beside it
(repo paths resolve automatically, same convention as this repo's other generators):
  ../../.venv/bin/python nem3_grandfathering.py             # the Phase-1 comparison
  ../../.venv/bin/python nem3_grandfathering.py --build-rates
      # rebuilds data/nbt_export_rates_2026.csv from the raw MIDAS files in
      # private/1-raw-data/sdge_nbt_export_rates/ (needs that private archive; the
      # normal run above needs only the COMMITTED csv, not the 38 MB raw files)
"""
import json
import os
import pathlib
import re
import sys

import numpy as np
import pandas as pd

import battery_dispatch_policies as bp  # reuse run_batt() -- same dispatch nbt_2039 uses
import behavior_rebuild as br     # reuse load() -- the measured 15-min year
import rates as R                 # canonical rate constants + NEM 2.0 billing engine


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
RAW_DIR = ROOT / "private" / "1-raw-data" / "sdge_nbt_export_rates"
RATE_CSV = DATA / "nbt_export_rates_2026.csv"          # committed, PUBLIC rate table
RESULTS_JSON = DATA / "nem3_grandfathering.json"        # committed results artifact
BILL_SUMMARY_CSV = DATA / "electric_bill_summary.csv"  # actual-billed anchor (context only)
EXTENDED_JSON = DATA / "extended_results.json"          # nbt_2039 reference (read-only)

# The MIDAS export-pricing vintages needed for AC3's uncertainty band: the 9-year
# lock-in vintage a hypothetical 2026 enrollment would receive (NBT26), and the
# no-lock-in / current-year vintage (NBT00). NBT25 is NOT needed -- this household
# is not evaluating a 2025 enrollment -- and is not present in either raw file used.
RAW_FILES = {
    "NBT00": RAW_DIR / "Current Year NBT Pricing Upload MIDAS.csv",
    "NBT26": RAW_DIR / "LY2026 NBT Pricing Upload MIDAS.csv",
}
# The applicable tariff year: BOTH vintages' rate tables are condensed from their
# calendar-year-2026 rows. This is an explicit choice, not a silent default -- see
# the "GENUINE FINDING" note above for why 2026 is also the ONLY year for which
# NBT00's table is that vintage's actual effective rate (everything past year 1 is
# illustrative-only per the source readme), and the year a hypothetical NBT26
# enrollment would lock in at PTO.
TARIFF_YEAR = 2026

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Clean Energy Alliance "Solar Impact" bonus -- see the docstring's EXPORT CREDIT
# FORMULA note for the exact quoted source text and fetch date.
CEA_BONUS_USD_PER_KWH = 0.01
CEA_SOURCE = ("https://thecleanenergyalliance.org/solar-impact/, fetched 2026-08-01: "
              "\"The energy values are based on the same export credit pricing paid "
              "by San Diego Gas & Electric (SDG&E)\" + \"Solar Impact customers earn "
              "an additional $0.01 per kWh for energy they export to the grid.\"")

_VALUE_NAME_RE = re.compile(r"^([A-Za-z]{3}) (Weekday|Weekend) HS(\d+)$")


# ------------------------------------------------------------ rate-table build
def build_rate_table():
    """Rebuild data/nbt_export_rates_2026.csv from the raw MIDAS files.

    Needs the private raw archive (private/1-raw-data/sdge_nbt_export_rates/); the
    normal analysis run (main(), below) does NOT need this function or the raw
    files -- it reads only the committed CSV this writes. Restricts each vintage
    to its TARIFF_YEAR rows (Sector="All"), splits Delivery ("USCA-SDXX" in RIN)
    from Generation ("USCA-XXSD"), and parses each row's own ValueName (e.g.
    "Jan Weekday HS0") into (month, day_type, hour) rather than trusting a
    reader's DateStart/TimeStart (those are UTC and repeat every matching weekday
    of the year; ValueName is already the Pacific-time (month, day-type, hour)
    label the source file's own readme documents). Fails closed on any parse
    failure, unexpected RIN, missing vintage-year rows, or a row count that does
    not match the exhaustive 12 month x 2 day-type x 24 hour x 2 component
    universe -- a mangled or truncated raw file must abort, not publish a
    partial table.
    """
    frames = []
    for rate_name, path in RAW_FILES.items():
        if not path.exists():
            raise SystemExit(
                f"build_rate_table: missing raw MIDAS file {path} for vintage "
                f"{rate_name}. This needs the private raw archive in "
                f"{RAW_DIR} (see that directory's PROVENANCE.txt).")
        df = pd.read_csv(path)
        df["DateStart_p"] = pd.to_datetime(df["DateStart"], format="%m/%d/%Y")
        sub = df[(df["DateStart_p"].dt.year == TARIFF_YEAR)
                 & (df["RateName"] == rate_name)
                 & (df["Sector"] == "All")].copy()
        if sub.empty:
            raise SystemExit(
                f"build_rate_table: {path} has no RateName={rate_name} rows for "
                f"{TARIFF_YEAR} -- the raw file's vintage/year assumptions this "
                "script relies on no longer hold; re-check it before proceeding.")
        parsed = sub["ValueName"].str.extract(_VALUE_NAME_RE)
        if parsed.isna().any().any():
            bad = sub.loc[parsed.isna().any(axis=1), "ValueName"].unique()
            raise SystemExit(
                f"build_rate_table: {path} has ValueName(s) that do not match "
                f"the expected '<Mon> <Weekday|Weekend> HS<hour>' pattern: "
                f"{list(bad)[:5]} -- refusing to guess their (month, day_type, "
                "hour) bucket.")
        sub["month"] = parsed[0]
        sub["day_type"] = parsed[1]
        sub["hour"] = parsed[2].astype(int)
        is_delivery = sub["RIN"].str.contains("USCA-SDXX")
        is_generation = sub["RIN"].str.contains("USCA-XXSD")
        if not (is_delivery | is_generation).all():
            bad = sub.loc[~(is_delivery | is_generation), "RIN"].unique()
            raise SystemExit(
                f"build_rate_table: {path} has RIN(s) that are neither a "
                f"Delivery (USCA-SDXX) nor Generation (USCA-XXSD) export "
                f"component: {list(bad)[:5]} -- refusing to classify them.")
        if (is_delivery & is_generation).any():
            raise SystemExit(f"build_rate_table: {path} has a RIN matching "
                             "BOTH Delivery and Generation patterns -- ambiguous.")
        sub["component"] = np.where(is_delivery, "delivery", "generation")
        sub["rate_name"] = rate_name
        frames.append(sub[["rate_name", "component", "month", "day_type", "hour",
                            "Value", "RIN", "ValueName"]])

    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"Value": "value_usd_per_kwh",
                              "RIN": "rate_lookup_id",
                              "ValueName": "source_value_name"})
    out["sector"] = "All"

    # Within TARIFF_YEAR, each (rate_name, component, month, day_type, hour) bucket
    # legitimately has MANY raw rows -- one per matching calendar day that month
    # (e.g. every January weekday gets its own "Jan Weekday HS0" row) -- but they
    # must all agree on the $/kWh value; if they don't, the year-1 assumption this
    # script relies on (one price per bucket, not one per calendar date) is wrong
    # and it must abort rather than silently pick one.
    key = ["rate_name", "component", "month", "day_type", "hour"]
    n_distinct_values = out.groupby(key)["value_usd_per_kwh"].nunique()
    inconsistent = n_distinct_values[n_distinct_values > 1]
    if len(inconsistent):
        raise SystemExit(
            f"build_rate_table: {len(inconsistent)} bucket(s) have more than one "
            f"distinct $/kWh value within {TARIFF_YEAR}, e.g. {inconsistent.index[:5].tolist()} "
            "-- the one-price-per-bucket assumption does not hold; refusing to "
            "silently pick one.")
    out = out.groupby(key, as_index=False).first()

    expected_rows = len(RAW_FILES) * 2 * 12 * 2 * 24   # vintages x components x months x day-types x hours
    if len(out) != expected_rows:
        raise SystemExit(
            f"build_rate_table: expected exactly {expected_rows} rows "
            f"({len(RAW_FILES)} vintages x 2 components x 12 months x 2 "
            f"day-types x 24 hours), got {len(out)} -- the raw file's calendar "
            "coverage for this tariff year is not what this script assumes.")

    month_order = {m: i for i, m in enumerate(MONTHS)}
    out["_mo"] = out["month"].map(month_order)
    out = out.sort_values(["rate_name", "component", "_mo", "day_type", "hour"]) \
             .drop(columns="_mo").reset_index(drop=True)

    tmp = f"{RATE_CSV}.tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, RATE_CSV)
    print(f"wrote {RATE_CSV} ({len(out)} rows, tariff year {TARIFF_YEAR}, "
          f"vintages {sorted(RAW_FILES)})")
    return out


def load_rate_table():
    """The committed public rate table -> {(rate_name, month, day_type, hour):
    total export credit $/kWh}, one dict per rate_name, PLUS a second,
    delivery-only dict for the sensitivity check below. Fails closed if the
    committed CSV is missing, malformed, or incomplete -- this is the ONLY
    place a household-facing consumer of this table needs to look, and it
    never silently fills a gap.

    GENERATION-COMPONENT AMBIGUITY, disclosed rather than silently resolved
    (an adversarial review finding): this household takes generation service
    from a CCA (Clean Energy Alliance), not SDG&E's own bundled generation.
    SDG&E's own export-pricing methodology page states its Generation
    component is "applicable only to bundled customers" (fetched 2026-08-01,
    https://www.sdge.com/solar/solar-billing-plan/export-pricing) -- i.e. NOT
    to CCA customers like this household, at least as SDG&E constructs ITS
    OWN rate table. Against that, Clean Energy Alliance's own "Solar Impact"
    program page states, unhedged: "The energy values are based on the same
    export credit pricing paid by San Diego Gas & Electric (SDG&E)" plus the
    $0.01/kWh bonus (same source as CEA_SOURCE above) -- a direct, first-party
    statement from the entity that actually generates this household's export
    credit, not merely SDG&E's own internal construction note. CEA's own
    published "Adopted Residential Rates" schedule (fetched 2026-08-01,
    https://thecleanenergyalliance.org/wp-content/uploads/2024/09/CEA-Adopted-
    Residential-Rate-Schedule-Effective-2024_11_01.pdf) lists a flat $0.06/kWh
    "Net Surplus Compensation" for "Personal Impact/Solar Impact" -- but this
    is NEM's ANNUAL leftover-surplus true-up mechanism (a different program
    from NBT's ONGOING hourly export credit this script models), so it does
    not resolve the question either way; no CEA document describing an NBT-
    specific generation-component rate, separate from SDG&E's, was found.
    This script uses Delivery + Generation + CEA bonus as its PRIMARY figure
    (CEA's own direct customer-facing statement is the most specific evidence
    for what this household's account actually receives), but reports a
    Delivery-only + CEA-bonus SENSITIVITY figure alongside it in the results
    (see main()'s "generation_component_sensitivity" section) so a reader can
    see the range this genuine, unresolved ambiguity creates rather than one
    number presented with unwarranted certainty.
    """
    if not RATE_CSV.exists():
        raise SystemExit(
            f"{RATE_CSV} is missing. Run this script with --build-rates first "
            "(needs the private raw MIDAS archive), or restore the committed CSV.")
    tab = pd.read_csv(RATE_CSV)
    required_cols = {"rate_name", "component", "month", "day_type", "hour",
                      "value_usd_per_kwh"}
    missing_cols = required_cols - set(tab.columns)
    if missing_cols:
        raise SystemExit(f"{RATE_CSV}: missing column(s) {sorted(missing_cols)}")

    lookups = {}
    delivery_only_lookups = {}
    for rate_name, g in tab.groupby("rate_name"):
        piv = g.pivot_table(index=["month", "day_type", "hour"],
                            columns="component", values="value_usd_per_kwh")
        if not {"delivery", "generation"}.issubset(piv.columns):
            raise SystemExit(
                f"{RATE_CSV}: rate_name={rate_name} is missing a delivery or "
                f"generation component (has {list(piv.columns)})")
        expected = 12 * 2 * 24
        if len(piv) != expected:
            missing = expected - len(piv)
            raise SystemExit(
                f"{RATE_CSV}: rate_name={rate_name} covers {len(piv)}/{expected} "
                f"(month, day_type, hour) buckets -- {missing} missing. Refusing "
                "to interpolate; rebuild the table with --build-rates.")
        if piv[["delivery", "generation"]].isna().any().any():
            raise SystemExit(
                f"{RATE_CSV}: rate_name={rate_name} has a NaN delivery or "
                "generation value in an otherwise-present bucket.")
        credit = piv["delivery"] + piv["generation"] + CEA_BONUS_USD_PER_KWH
        lookups[rate_name] = credit.to_dict()
        delivery_only_lookups[rate_name] = (
            piv["delivery"] + CEA_BONUS_USD_PER_KWH).to_dict()
    missing_vintages = set(RAW_FILES) - set(lookups)
    if missing_vintages:
        raise SystemExit(f"{RATE_CSV}: missing vintage(s) {sorted(missing_vintages)}")
    return lookups, delivery_only_lookups


# ------------------------------------------------------------ billing engines
def bill_nbt(d, credit_lookup):
    """NEM 3.0 / NBT counterfactual annual bill for one export-rate vintage.

    Import side: gross-billed at rates.allin(season, TOU period) per (month,
    season, TOU period) bucket -- the same assumption extended_findings.py's
    bill_flat_export() already uses for its own NBT proxy (no monthly netting;
    NBC is already inside allin()); see the module docstring's IMPORT-SIDE RATE
    note for why this needs no NBT-specific import rate.

    Export side: THIS is what differs from bill_flat_export() -- each 15-min
    interval's export kWh is bucketed to the ACC's own (month, day_type, hour)
    resolution (day_type from rates.off_peak_day() via d.wkend, which already
    folds tariff holidays into the weekend rule -- exactly how the source MIDAS
    file treats holidays too, confirmed: every DayStart=8 (holiday) row in the
    raw file carries a "Weekend" ValueName label) and credited at the REAL
    published rate for that bucket, aggregated first (sum of exports per
    bucket) before pricing, matching AC3's "aggregate to the ACC's hourly
    resolution" instruction. Fails closed (SystemExit) if any bucket the
    household's data touches is not in credit_lookup -- never interpolated.

    Returns (annual_bill_usd, total_export_credit_usd, exports_by_bucket).
    """
    month = np.asarray(MONTHS)[d["dt"].dt.month.values - 1]
    day_type = np.where(d["wkend"].values, "Weekend", "Weekday")
    hour = d["dt"].dt.hour.values
    bucket = pd.DataFrame({"month": month, "day_type": day_type, "hour": hour,
                           "exp": d["exp"].values})
    by_bucket = bucket.groupby(["month", "day_type", "hour"])["exp"].sum()

    missing = [k for k in by_bucket.index if k not in credit_lookup]
    if missing:
        raise SystemExit(
            f"bill_nbt: {len(missing)} (month, day_type, hour) bucket(s) in the "
            f"household's data are not in the rate table, e.g. {missing[:5]} -- "
            "refusing to interpolate or zero-fill; check TARIFF_YEAR coverage.")
    credit_rate = by_bucket.index.map(credit_lookup)
    export_credit_total = float((by_bucket.values * np.asarray(credit_rate)).sum())

    tot = 0.0
    for _ym, m in d.groupby("ym"):
        days = m["dt"].dt.date.nunique()
        tot += days * R.BSC
        for s in ("S", "W"):
            for p in ("on", "off", "sop"):
                sub = m[(m["seas"] == s) & (m["p"] == p)]
                tot += sub["imp"].sum() * R.allin(s, p)
    tot -= export_credit_total
    return tot, export_credit_total, by_bucket


def actual_billed_anchor():
    """Sum of current_charges over the committed electric bill summary, for
    CONTEXT only (CLAUDE.md 0: label modeled vs actual). Never substituted into
    the grandfathering delta, which uses the same model on both sides so that
    systematic modeling bias cancels rather than being anchored away."""
    if not BILL_SUMMARY_CSV.exists():
        return None
    tab = pd.read_csv(BILL_SUMMARY_CSV)
    return {"total_usd": round(float(tab["current_charges"].sum()), 2),
            "window": f"{tab['period'].iloc[0]} .. {tab['period'].iloc[-1]}",
            "n_bill_periods": int(len(tab)),
            "source": str(BILL_SUMMARY_CSV.relative_to(ROOT))}


def load_nbt_2039_reference():
    """Read-only load of extended_findings.py's committed nbt_2039 battery-marginal
    figures (data/extended_results.json) -- NEVER recomputed here (issue #34 owns
    that generator). Fails closed if the artifact or the expected keys are
    missing: a stale/absent reference must abort the reconciliation, not silently
    compare against nothing."""
    if not EXTENDED_JSON.exists():
        raise SystemExit(
            f"load_nbt_2039_reference: {EXTENDED_JSON} is missing -- run "
            "extended_findings.py first (or restore the committed artifact); "
            "the battery reconciliation needs its nbt_2039 figures as a reference.")
    ext = json.loads(EXTENDED_JSON.read_text())
    if "nbt_2039" not in ext:
        raise SystemExit(f"{EXTENDED_JSON} has no nbt_2039 key -- cannot reconcile.")
    nbt2039 = ext["nbt_2039"]
    nem2_marginal = nbt2039.get("battery_marginal_under_nem2")
    flat = nbt2039.get("battery_marginal_under_nbt", {})
    if nem2_marginal is None or not flat:
        raise SystemExit(
            f"{EXTENDED_JSON}: nbt_2039 is missing battery_marginal_under_nem2 "
            "or battery_marginal_under_nbt -- cannot reconcile.")
    missing_credits = {"3c", "5c", "8c"} - set(flat)
    if missing_credits:
        raise SystemExit(
            f"{EXTENDED_JSON}: nbt_2039.battery_marginal_under_nbt is missing "
            f"credit bucket(s) {sorted(missing_credits)} -- cannot reconcile.")
    return {
        "battery_marginal_under_nem2_usd_yr": nem2_marginal,
        "battery_marginal_under_flat_nbt_usd_yr": {
            k: flat[k]["battery_marginal_yr"] for k in ("3c", "5c", "8c")},
        "source": "data/extended_results.json -> nbt_2039 (extended_findings.py; "
                  "read-only, not recomputed here)",
    }


def battery_marginal_under_real_nbt(d, credit_lookups):
    """The price-aware battery's marginal bill savings priced against the REAL
    hourly NBT export schedule, for each vintage -- reusing the SAME dispatch
    (bp.run_batt) extended_findings.py's nbt_2039 already runs, so only the
    export-PRICING assumption differs between this figure and nbt_2039's flat
    3c/5c/8c figures, never the physical battery behavior.

    Bills both the no-battery series (imp0/gen0) and the with-battery series
    (i2/e2) through THIS script's own bill_nbt(), for every vintage in
    RAW_FILES, then reconciles the resulting marginal explicitly against
    extended_findings.py's committed nbt_2039 reference (both the NEM 2.0 and
    the 3c/5c/8c flat-credit figures) -- the numeric disagreement is stated in
    dollars for each, never averaged or hidden (issue #9 AC6).
    """
    imp0 = d["imp"].values.astype(float)
    gen0 = d["exp"].values.astype(float)
    i2, e2, served_kwh, thru_kwh = bp.run_batt(d, imp0, gen0, 13.5, "greedy")

    d_batt = d.copy()
    d_batt["imp"] = i2
    d_batt["exp"] = e2

    per_vintage = {}
    for rate_name in sorted(RAW_FILES):
        credit_lookup = credit_lookups[rate_name]
        bill_no_batt, _, _ = bill_nbt(d, credit_lookup)
        bill_with_batt, _, _ = bill_nbt(d_batt, credit_lookup)
        per_vintage[rate_name] = {
            "annual_bill_no_battery_usd": round(bill_no_batt, 2),
            "annual_bill_with_battery_usd": round(bill_with_batt, 2),
            "battery_marginal_usd_yr": round(bill_no_batt - bill_with_batt, 2),
        }

    reference = load_nbt_2039_reference()
    nem2_ref = reference["battery_marginal_under_nem2_usd_yr"]
    flat_ref = reference["battery_marginal_under_flat_nbt_usd_yr"]
    flat_lo, flat_hi = min(flat_ref.values()), max(flat_ref.values())

    disagreement = {}
    for rate_name, v in per_vintage.items():
        real = v["battery_marginal_usd_yr"]
        vs = {"vs_nem2_usd": round(real - nem2_ref, 2)}
        for credit_name, flat_val in flat_ref.items():
            vs[f"vs_flat_{credit_name}_usd"] = round(real - flat_val, 2)
        inside_bracket = flat_lo <= real <= flat_hi
        vs["real_within_flat_3c_8c_bracket"] = bool(inside_bracket)
        vs["gap_from_flat_bracket_usd"] = (
            0.0 if inside_bracket else
            round(min(abs(real - flat_lo), abs(real - flat_hi)), 2))
        disagreement[rate_name] = vs

    return {
        "method": ("Same battery physical dispatch as nbt_2039 "
                  "(bp.run_batt(d, imp0, gen0, 13.5, 'greedy')); both the "
                  "no-battery and with-battery series billed through this "
                  "script's own bill_nbt() (real hourly NBT export schedule) "
                  "per vintage, marginal = no-battery bill - with-battery bill."),
        "served_kwh_yr": round(float(served_kwh), 1),
        "throughput_kwh_yr": round(float(thru_kwh), 1),
        "battery_marginal_real_hourly_usd_yr": per_vintage,
        "reference_nbt_2039": reference,
        "disagreement_vs_reference": disagreement,
    }


# ------------------------------------------------------------------------ main
def main():
    d = br.load()
    d["imp"] = d["Consumption"].astype(float)
    d["exp"] = d["Generation"].astype(float)

    nem2_model_bill = R.bill_nem(d)
    credit_lookups, delivery_only_lookups = load_rate_table()

    nbt_bills = {}
    for rate_name in sorted(RAW_FILES):
        bill, credit_total, by_bucket = bill_nbt(d, credit_lookups[rate_name])
        weighted_avg = credit_total / d["exp"].sum() if d["exp"].sum() else None
        nbt_bills[rate_name] = {
            "annual_bill_usd": round(bill, 2),
            "export_credit_total_usd": round(credit_total, 2),
            "export_credit_weighted_avg_usd_per_kwh": (
                round(weighted_avg, 5) if weighted_avg is not None else None),
            "grandfathering_value_usd": round(bill - nem2_model_bill, 2),
        }

    gf_values = [v["grandfathering_value_usd"] for v in nbt_bills.values()]
    identical_vintages = bool((max(gf_values) - min(gf_values)) < 0.005)

    # GENERATION-COMPONENT SENSITIVITY (see load_rate_table()'s docstring): the
    # PRIMARY figures above assume this household's CCA generation credit
    # equals SDG&E's own Delivery+Generation export rate (CEA's own stated
    # policy). This computes the alternative -- Delivery-only, if SDG&E's
    # "Generation applies only to bundled customers" caveat means CCA
    # customers should NOT receive that component -- so the genuine,
    # unresolved ambiguity is visible as a range, not hidden behind one number.
    delivery_only_bills = {}
    for rate_name in sorted(RAW_FILES):
        bill, _, _ = bill_nbt(d, delivery_only_lookups[rate_name])
        delivery_only_bills[rate_name] = {
            "annual_bill_usd": round(bill, 2),
            "grandfathering_value_usd": round(bill - nem2_model_bill, 2),
        }

    # simple (hour-equal-weighted) stats for the docstring's mechanism narrative,
    # computed from the committed table, not restated from memory
    piv_all = pd.read_csv(RATE_CSV).pivot_table(
        index=["rate_name", "month", "day_type", "hour"],
        columns="component", values="value_usd_per_kwh")
    piv_all["credit"] = (piv_all["delivery"] + piv_all["generation"]
                         + CEA_BONUS_USD_PER_KWH)

    old_bracket = {"low_usd_yr": 1772, "high_usd_yr": 2268,
                  "source": "data/extra_results.json nbt.gf_value (flat 3c/5c/8c "
                            "export credit; NOT modified by this script, see "
                            "issue #34)"}

    results = {
        "window": {"start": str(d["dt"].min()), "end": str(d["dt"].max()),
                  "days": int(d["dt"].dt.date.nunique())},
        "household": {
            "cca": "Clean Energy Alliance -- Clean Impact Plus",
            "nem_version": "NEM 2.0 (grandfathered, PTO 2019-12-27)",
            "plan": "EV-TOU-5",
        },
        "actual_billed_anchor": actual_billed_anchor(),
        "nem2": {
            "annual_bill_usd_modeled": round(nem2_model_bill, 2),
            "label": "modeled (rates.bill_nem, the canonical NEM 2.0 engine; see "
                     "actual_billed_anchor for the audited total over the same "
                     "window -- the model runs high, use the DELTA below)",
        },
        "nbt_counterfactual": nbt_bills,
        "grandfathering_value_range_usd_per_yr": {
            "low": round(min(gf_values), 2),
            "high": round(max(gf_values), 2),
            "vintages_priced": sorted(RAW_FILES),
            "band_has_zero_width": identical_vintages,
        },
        "why_the_band_has_zero_width": (
            "SDG&E's NBT26 and NBT00 calendar-year-2026 rate tables are "
            "byte-identical (max|NBT00-NBT26| = 0.0 across all 1,152 rate cells "
            "in data/nbt_export_rates_2026.csv) -- both vintages price this "
            "household's measured export shape at the same dollar figure this "
            "year. The vintages differ in which FUTURE years are contractually "
            "guaranteed (NBT26 locks this table for 9 years from PTO; NBT00 "
            "guarantees only the current year and resets annually to a table "
            "not yet published), not in this year's price level -- widening "
            "this into a real multi-year band is out of this phase's one-year "
            "snapshot scope." if identical_vintages else
            "NBT26 and NBT00 price this household's export shape differently "
            "in this tariff year; see nbt_counterfactual for each vintage's "
            "own figure."),
        "export_credit_shape": {
            "cea_bonus_usd_per_kwh": CEA_BONUS_USD_PER_KWH,
            "cea_bonus_source": CEA_SOURCE,
            "hour_equal_weighted_mean_usd_per_kwh": round(float(piv_all["credit"].mean()), 5),
            "hour_equal_weighted_min_usd_per_kwh": round(float(piv_all["credit"].min()), 5),
            "hour_equal_weighted_max_usd_per_kwh": round(float(piv_all["credit"].max()), 5),
            "note": ("the equal-weighted range spans ~1c/kWh (spring midday "
                     "oversupply hours) to ~$1.08/kWh (August evening "
                     "post-solar ramp); this household's exports concentrate "
                     "at midday, so its OWN kWh-weighted realized credit "
                     "(export_credit_weighted_avg_usd_per_kwh, per vintage "
                     "above) sits well below the simple hour-equal-weighted "
                     "mean."),
        },
        "generation_component_sensitivity": {
            "primary_assumption": "delivery + generation + CEA bonus (see load_rate_table docstring)",
            "alternative_assumption": "delivery + CEA bonus only (no SDG&E Generation component)",
            "alternative_bills": delivery_only_bills,
            "note": ("SDG&E's own export-pricing methodology page states its "
                     "Generation component is 'applicable only to bundled "
                     "customers' -- this household is on CCA (Clean Energy "
                     "Alliance) generation, not SDG&E bundled generation. "
                     "Against that, CEA's own 'Solar Impact' program page "
                     "states unhedged that its export credit equals SDG&E's "
                     "own published pricing plus the $0.01/kWh bonus, with no "
                     "component breakdown. No CEA document describing an "
                     "NBT-specific generation-component rate, separate from "
                     "SDG&E's, was found. This is a genuine, unresolved "
                     "public-data ambiguity, not a computational error -- "
                     "the PRIMARY figures above use CEA's direct statement; "
                     "this section discloses what the Delivery-only "
                     "alternative would be instead, so the reader can see "
                     "the range rather than one figure presented as certain."),
        },
        "old_bracket_for_context": old_bracket,
        "mechanism_of_change_from_old_bracket": (
            "The old bracket applied ONE flat export credit (3c/5c/8c, an "
            "assumed sensitivity range) to every export kWh all year. The real "
            "SDG&E-published hourly schedule varies far more (see "
            "export_credit_shape) and is lowest exactly when this household's "
            "solar produces (midday) and highest when it does not (evening "
            "peak) -- so pricing the REAL export shape against the REAL "
            "hourly schedule yields a single computed figure, traceable to a "
            "committed public rate table, rather than an assumed range."),
        "battery_marginal_reconciliation_2039": battery_marginal_under_real_nbt(
            d, credit_lookups),
    }

    assert results["nem2"]["annual_bill_usd_modeled"] > 0
    for rn, v in nbt_bills.items():
        assert v["annual_bill_usd"] > 0, rn
    for k in ("window", "nem2", "nbt_counterfactual",
              "grandfathering_value_range_usd_per_yr", "export_credit_shape",
              "battery_marginal_reconciliation_2039",
              "generation_component_sensitivity"):
        assert k in results, f"results section missing: {k}"
    for rn, v in delivery_only_bills.items():
        assert v["annual_bill_usd"] > 0, rn
    for rn, v in results["battery_marginal_reconciliation_2039"][
            "battery_marginal_real_hourly_usd_yr"].items():
        assert v["battery_marginal_usd_yr"] > 0, (
            f"{rn}: battery marginal under the real hourly NBT schedule is not "
            "positive -- a battery that costs the household money to run makes "
            "no economic sense and would indicate a units/sign bug")

    tmp = f"{RESULTS_JSON}.tmp"
    with open(tmp, "w") as fh:
        json.dump(results, fh, indent=1)
    os.replace(tmp, RESULTS_JSON)

    print(f"NEM 2.0 modeled bill: ${nem2_model_bill:,.2f}/yr "
          f"(actual billed anchor: {results['actual_billed_anchor']})")
    for rn, v in nbt_bills.items():
        print(f"{rn}: bill ${v['annual_bill_usd']:,.2f}/yr, export credit "
              f"${v['export_credit_total_usd']:,.2f} "
              f"(weighted avg {v['export_credit_weighted_avg_usd_per_kwh']} $/kWh), "
              f"grandfathering value ${v['grandfathering_value_usd']:,.2f}/yr")
    print(f"grandfathering value range: ${min(gf_values):,.2f}-${max(gf_values):,.2f}/yr "
          f"(old bracket for context: ${old_bracket['low_usd_yr']:,}-"
          f"${old_bracket['high_usd_yr']:,}/yr)")

    recon = results["battery_marginal_reconciliation_2039"]
    ref = recon["reference_nbt_2039"]
    print(f"\nbattery marginal reconciliation (2039 NBT transition):")
    print(f"  nbt_2039 reference: NEM 2.0 ${ref['battery_marginal_under_nem2_usd_yr']:,}/yr, "
          f"flat-credit {ref['battery_marginal_under_flat_nbt_usd_yr']}")
    for rn, v in recon["battery_marginal_real_hourly_usd_yr"].items():
        dis = recon["disagreement_vs_reference"][rn]
        print(f"  {rn} real-hourly marginal: ${v['battery_marginal_usd_yr']:,.2f}/yr "
              f"(vs NEM2 {dis['vs_nem2_usd']:+.2f}, vs flat 3c/5c/8c "
              f"{dis['vs_flat_3c_usd']:+.2f}/{dis['vs_flat_5c_usd']:+.2f}/"
              f"{dis['vs_flat_8c_usd']:+.2f}; within flat bracket: "
              f"{dis['real_within_flat_3c_8c_bracket']}, gap "
              f"${dis['gap_from_flat_bracket_usd']:,.2f})")


if __name__ == "__main__":
    if "--build-rates" in sys.argv:
        build_rate_table()
    else:
        main()
