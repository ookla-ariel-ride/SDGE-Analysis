#!/usr/bin/env python3
"""Lifetime solar payback: cumulative value of actual production vs the install invoice.

Method:
  1. Blended $/kWh TODAY = (no-solar counterfactual bill - with-solar bill) / annual
     production, both computed with the canonical engine (rates.bill_nem: monthly
     per-period NEM netting, NBC on gross imports) at current rates. The production
     denominator comes from the same rolling window as the two bills, by energy
     balance over the same series: whole-home load (samA/samB) - grid imports +
     grid exports (usage.csv). Computed under BOTH
     TOU structures: the pre-2026 windows (super-off-peak midnight-6am, plus 10am-2pm in
     Mar/Apr only) for historical years, and the current windows for the present year.
     The no-solar load series is the hourly whole-home consumption (solar-monitoring
     consumption meter, two calendar-year files stitched to a rolling 365 days),
     re-billed as if all imported.
  2. Each historical year's value = that year's ACTUAL metered production x the
     old-structure blended $/kWh x (that year's utility average residential rate /
     the current average rate). Rate index from published SDG&E average-rate history.
  3. Crossovers: cumulative value vs invoice gross, and vs invoice x 0.70 (30% federal
     ITC for 2019 systems).

If the raw inputs (usage.csv + samA.csv/samB.csv) are present in the working directory,
BLENDED_OLD / BLENDED_NEW are DERIVED by derive_blended(); otherwise the last derived
values are used as fallbacks (and labeled as such). Derived reference values:
no-solar counterfactual $9,853/yr, blended new-TOU 0.3009, old-TOU 0.4869.

When the values are derived (not fallback), the results are also written atomically
to data/lifetime_payback.json -- the committed artifact the report's lifetime-payback
section cites. The block used to be hand-recorded in extra_results.json and drifted
as the archive moved; owning an artifact puts it under the section-9 regeneration
gate like every other headline figure.

Caveat: the rate index is approximate; crossover dates carry roughly +/-10% (a few
months). Inputs: yearly production (monitoring records), install invoice total + date.

Per-house inputs (invoice, install/PTO dates) come from private/household.yaml via
analysis/household.py (fails closed without it — run the intake interview in
DATA-SOURCES-CHEATSHEET.md). Whether the ITC was claimed is treated as a SCENARIO:
both crossovers (gross invoice, and invoice x 0.70) are always reported, so a null
solar.itc_claimed is fine. The production-per-year table below is measured DATA
(monitoring records), not household config — it stays in the script.
"""
import os
import numpy as np
import pandas as pd
import household as hh

INVOICE = float(hh.get("solar.install_invoice_usd"))
PAID = str(hh.get("solar.install_paid_date"))       # month the invoice was paid
PTO = str(hh.get("household.pto_date"))             # permission-to-operate date
ITC = 0.30   # federal residential ITC rate for 2019-vintage systems (public constant)

# ---- REPLACE FOR YOUR HOUSE: per-year measured production, kWh -------------------
# Provenance: this house's solar-monitoring yearly production records. ALWAYS used
# (it is the value table the payback is computed over); the final partial year is
# production to date. 2026 = Jan 1 - Jul 23.
PROD = {2020: 17373, 2021: 17421, 2022: 17749, 2023: 15570, 2024: 16654,
        2025: 16509, 2026: 9893}

# ---- REPLACE FOR YOUR UTILITY: average residential rate index, cents/kWh ---------
# Provenance: published SDG&E average residential rate history (approximate).
# ALWAYS used, to deflate today's blended $/kWh back to each historical year.
# Must contain every year in PROD, including the window-end year it normalizes to.
RATE_IDX = {2020: 32, 2021: 34, 2022: 37, 2023: 41, 2024: 44, 2025: 46, 2026: 48}

# ---- REPLACE FOR YOUR HOUSE: fallback blended values, $/kWh ----------------------
# Provenance: the last derive_blended() run on this house's raw data. Used ONLY
# when usage.csv + samA.csv + samB.csv are absent; when they are present the
# blended values are derived from them and these constants are ignored.
FALLBACK_OLD = 0.4869   # pre-2026 TOU structure
FALLBACK_NEW = 0.3009   # current TOU structure

# ---- REPLACE FOR YOUR HOUSE: analysis-window end (exclusive), YYYY-MM-DD ---------
# Provenance: day after the last full day in this house's Green Button export.
# ALWAYS used: it sets the rolling-365-day derivation window, and its year is the
# "current year" the rate index normalizes to and the year billed on the current
# TOU structure.
WINDOW_END = "2026-07-24"

# Year SDG&E's TOU structure changed (public tariff fact, not house-specific):
# years before it are valued at the old-structure blended $/kWh, years from it on
# at the current-structure value.
TOU_CHANGE_YEAR = 2026

def _per_old(hour, month):
    if 16 <= hour < 21: return "on"
    if hour < 6: return "sop"
    if month in (3, 4) and 10 <= hour < 14: return "sop"
    return "off"

def derive_blended(usage_csv="usage.csv", sam_full_year="samB.csv",
                   sam_partial_year="samA.csv", window_end=WINDOW_END):
    """Compute (blended_old, blended_new, nosolar_bill, annual_production_kwh)
    from raw data via rates.bill_nem. Everything, including the production
    denominator, comes from the same rolling 365-day window ending window_end.

    Fails closed (SystemExit) unless BOTH input series fully cover that exact
    window and the energy-balance production is physically plausible, so a
    truncated-but-contiguous input can never shrink the basis silently."""
    import rates as R
    end = pd.Timestamp(window_end)
    win_start = end - pd.Timedelta(days=365)
    # with-solar year (Green Button)
    df = pd.read_csv(usage_csv, skiprows=13); df.columns = [c.strip() for c in df.columns]
    # First pass, whole file against its OWN extent: the export spans eras rather
    # than the fixed pipeline year, and this catches interior gaps and malformed
    # days anywhere in the file, including outside the analysis window.
    _dts = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%m/%d/%Y %I:%M %p")
    R.validate_interval_coverage(zip(_dts.dt.date, _dts.dt.hour + _dts.dt.minute / 60),
                                 _dts.dt.date.min(), _dts.dt.date.max())
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%m/%d/%Y %I:%M %p")
    for c in ("Consumption", "Generation"): df[c] = pd.to_numeric(df[c])
    df = df[(df.dt >= win_start) & (df.dt < end)].copy()
    # Second pass, sliced frame against the CONFIGURED rolling-365-day calendar:
    # the whole-file check above accepts a truncated-but-contiguous export, which
    # would silently shrink the "rolling 365 days ending WINDOW_END" basis the
    # artifact claims. Validate against exactly that window, not the file extent.
    R.validate_interval_coverage(
        zip(df.dt.dt.date, df.dt.dt.hour + df.dt.dt.minute / 60),
        win_start.date(), (end - pd.Timedelta(days=1)).date())
    df["seas"] = np.where(df.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    df["ym"] = df.dt.dt.to_period("M")
    hh = df.dt.dt.hour + df.dt.dt.minute / 60
    df["p"] = [R.period_at(t) for t in df.dt]   # canonical: holiday rule included
    df["p_old"] = [_per_old(int(h), m) for h, m in zip(hh, df.dt.dt.month)]
    # no-solar counterfactual (hourly whole-home load, all imported, zero exports)
    a = pd.read_csv(sam_full_year)["kWh"].values     # full prior calendar year
    b = pd.read_csv(sam_partial_year)["kWh"].values  # current partial year
    y0 = end.year - 1
    s = pd.concat([pd.Series(a, index=pd.date_range(f"{y0}-01-01", periods=len(a), freq="h")),
                   pd.Series(b, index=pd.date_range(f"{end.year}-01-01", periods=len(b), freq="h"))])
    s = s[(s.index >= win_start) & (s.index < end)]
    # The stitched SAM series must cover the window exactly. Each half is built
    # from a gap-free date_range starting Jan 1, so a truncated file cannot leave
    # an interior hole -- it leaves a SHORT slice (missing hours at the stitch
    # seam or at the window edges). Length plus both endpoints therefore pin the
    # coverage completely.
    exp_hours = int((end - win_start) / pd.Timedelta(hours=1))
    if (len(s) != exp_hours or s.index[0] != win_start
            or s.index[-1] != end - pd.Timedelta(hours=1)):
        jan1 = pd.Timestamp(f"{end.year}-01-01")
        want_b = int((jan1 - win_start) / pd.Timedelta(hours=1))
        want_a = exp_hours - want_b
        got_b = int((s.index < jan1).sum())
        got_a = len(s) - got_b
        short = []
        if got_b != want_b:
            short.append(f"{sam_full_year} supplies {got_b} of {want_b} window hours")
        if got_a != want_a:
            short.append(f"{sam_partial_year} supplies {got_a} of {want_a} window hours")
        raise SystemExit(
            f"SAM hourly series does not cover the rolling window "
            f"{win_start}..{end} (exclusive): got {len(s)} of {exp_hours} hours; "
            + ("; ".join(short) if short else "window endpoints do not match"))
    if s.isna().any():
        raise SystemExit(
            f"SAM hourly series has {int(s.isna().sum())} NaN hour(s) inside the "
            f"window -- blank kWh cells in {sam_full_year}/{sam_partial_year}")
    L = pd.DataFrame({"Consumption": s, "Generation": 0.0})
    L["dt"] = L.index
    L["seas"] = np.where(L.index.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    L["ym"] = L.index.to_period("M")
    L["p"] = [R.period_at(t) for t in L.index]   # canonical: holiday rule included
    L["p_old"] = [_per_old(t.hour, t.month) for t in L.index]
    # Production over the SAME window, derived from the same series the two bills
    # use, by energy balance: production = whole-home load - grid imports + grid
    # exports. (The numerator is the bill difference between importing the whole
    # load and the actual import/export year, so this is the production that
    # earned it.)
    load = float(s.sum())
    imports = float(df["Consumption"].sum())
    exports = float(df["Generation"].sum())
    annual_production = load - imports + exports
    # Sanity-gate the energy balance before any caller writes an artifact from it.
    # By construction production = load - imports + exports, so the checkable
    # physical invariants are: production positive; production <= load + exports
    # (violated only by negative total imports); production >= exports, i.e.
    # load >= imports (a home cannot consume less than it imported).
    if annual_production <= 0:
        raise SystemExit(
            f"energy balance gives non-positive annual production "
            f"({annual_production:,.0f} kWh = load {load:,.0f} - imports "
            f"{imports:,.0f} + exports {exports:,.0f}); check that usage.csv and "
            f"the SAM files describe the same house and window")
    if annual_production > load + exports or annual_production < exports:
        raise SystemExit(
            f"energy balance is physically impossible: production "
            f"{annual_production:,.0f} kWh vs load {load:,.0f}, imports "
            f"{imports:,.0f}, exports {exports:,.0f} (requires negative grid "
            f"import or load below imports); the inputs are inconsistent")
    new = (R.bill_nem(L), R.bill_nem(df))
    old_L, old_d = L.copy(), df.copy()
    old_L["p"], old_d["p"] = old_L["p_old"], old_d["p_old"]
    old = (R.bill_nem(old_L), R.bill_nem(old_d))
    return ((old[0] - old[1]) / annual_production,
            (new[0] - new[1]) / annual_production, new[0], annual_production)

def _repo_root():
    import pathlib
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains analysis/ and data/")


if __name__ == "__main__":
    # Presence of the raw files only selects the derivation PATH; derived=True
    # holds beyond this point only because derive_blended() fails closed
    # (SystemExit) on any window-coverage or energy-balance violation, so an
    # invalid input set exits here -- before the artifact write below -- and can
    # neither rewrite nor truncate data/lifetime_payback.json.
    derived = all(os.path.exists(f) for f in ("usage.csv", "samA.csv", "samB.csv"))
    if derived:
        BLENDED_OLD, BLENDED_NEW, nosolar, ann_prod = derive_blended()
        print(f"derived from data: no-solar ${nosolar:,.0f}/yr | blended old-TOU "
              f"{BLENDED_OLD:.4f} new-TOU {BLENDED_NEW:.4f} "
              f"(production {ann_prod:,.0f} kWh over the window)")
    else:
        BLENDED_OLD, BLENDED_NEW = FALLBACK_OLD, FALLBACK_NEW
        print("raw inputs not present - using last-derived fallback blended values")
    cur_year = pd.Timestamp(WINDOW_END).year
    cum = 0.0
    print(f"invoice ${INVOICE:,.0f} paid {PAID} | PTO {PTO} (private/household.yaml)")
    print(f"{'year':<6}{'prod kWh':>10}{'value $':>10}{'cum $':>10}")
    years, crossings = [], {}
    for y in sorted(PROD):
        bl = BLENDED_NEW if y >= TOU_CHANGE_YEAR else BLENDED_OLD
        v = PROD[y] * bl * RATE_IDX[y] / RATE_IDX[cur_year]
        cum += v
        marks = []
        for name, target in (("net_itc", INVOICE * (1 - ITC)), ("gross", INVOICE)):
            if cum - v < target <= cum:
                crossings[name] = {"year": y, "fraction_through_year":
                                   round((target - (cum - v)) / v, 2)}
                marks.append(f"<- crosses {name.replace('_', '-of-')} ${target:,.0f}")
        years.append({"year": y, "production_kwh": PROD[y], "value_usd": round(v),
                      "cumulative_usd": round(cum)})
        print(f"{y:<6}{PROD[y]:>10,}{v:>10,.0f}{cum:>10,.0f}  {' '.join(marks)}")

    if derived:
        # Committed artifact: only a genuinely derived run may write it -- a
        # fallback run would just launder the previous run's values through a
        # fresh timestamp. Written atomically, section-9 style.
        import json
        out = {
            "basis": ("blended $/kWh derived from usage.csv + samA/samB over the "
                      f"rolling 365 days ending {WINDOW_END}; historical years "
                      "deflated by the published average-rate index; production "
                      "table from monitoring records"),
            "blended_old_tou": round(BLENDED_OLD, 4),
            "blended_new_tou": round(BLENDED_NEW, 4),
            "nosolar_bill_usd": round(nosolar),
            "annual_production_kwh": round(ann_prod),
            "invoice_usd": round(INVOICE),
            "years": years,
            "crossover": crossings,
        }
        path = _repo_root() / "data" / "lifetime_payback.json"
        tmp = path.with_suffix(f".json.tmp{os.getpid()}")
        with open(tmp, "w") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
        print(f"wrote {path}")
