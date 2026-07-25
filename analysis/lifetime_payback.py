#!/usr/bin/env python3
"""Lifetime solar payback: cumulative value of actual production vs the install invoice.

Method:
  1. Blended $/kWh TODAY = (no-solar counterfactual bill - with-solar bill) / annual
     production, both computed with the canonical engine (rates.bill_nem: monthly
     per-period NEM netting, NBC on gross imports) at current rates. Computed under BOTH
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
no-solar counterfactual $9,876/yr, blended new-TOU 0.3025, old-TOU 0.4866.

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
PROD = {2020: 17373, 2021: 17421, 2022: 17749, 2023: 15570, 2024: 16654,
        2025: 16509, 2026: 9893}                    # 2026 = Jan-Jul 23
RATE_IDX = {2020: 32, 2021: 34, 2022: 37, 2023: 41, 2024: 44, 2025: 46, 2026: 48}
FALLBACK_OLD = 0.4866   # $/kWh, pre-2026 TOU structure (derived; see derive_blended)
FALLBACK_NEW = 0.3025   # $/kWh, current TOU structure
ANNUAL_PRODUCTION = 16502.0   # CT-meter production, rolling 365d

def _per_old(hour, month):
    if 16 <= hour < 21: return "on"
    if hour < 6: return "sop"
    if month in (3, 4) and 10 <= hour < 14: return "sop"
    return "off"

def derive_blended(usage_csv="usage.csv", sam_full_year="samB.csv",
                   sam_partial_year="samA.csv", window_end="2026-07-24"):
    """Compute (blended_old, blended_new, nosolar_bill) from raw data via rates.bill_nem."""
    import rates as R
    end = pd.Timestamp(window_end)
    # with-solar year (Green Button)
    df = pd.read_csv(usage_csv, skiprows=13); df.columns = [c.strip() for c in df.columns]
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%m/%d/%Y %I:%M %p")
    for c in ("Consumption", "Generation"): df[c] = pd.to_numeric(df[c])
    df = df[(df.dt >= end - pd.Timedelta(days=365)) & (df.dt < end)].copy()
    df["seas"] = np.where(df.dt.dt.month.isin([6, 7, 8, 9, 10]), "S", "W")
    df["ym"] = df.dt.dt.to_period("M")
    hh = df.dt.dt.hour + df.dt.dt.minute / 60
    wk = df.dt.dt.weekday >= 5
    df["p"] = [R.period(h, w) for h, w in zip(hh, wk)]
    df["p_old"] = [_per_old(int(h), m) for h, m in zip(hh, df.dt.dt.month)]
    # no-solar counterfactual (hourly whole-home load, all imported, zero exports)
    a = pd.read_csv(sam_full_year)["kWh"].values     # full prior calendar year
    b = pd.read_csv(sam_partial_year)["kWh"].values  # current partial year
    y0 = end.year - 1
    s = pd.concat([pd.Series(a, index=pd.date_range(f"{y0}-01-01", periods=len(a), freq="h")),
                   pd.Series(b, index=pd.date_range(f"{end.year}-01-01", periods=len(b), freq="h"))])
    s = s[(s.index >= end - pd.Timedelta(days=365)) & (s.index < end)]
    L = pd.DataFrame({"Consumption": s, "Generation": 0.0})
    L["dt"] = L.index
    L["seas"] = np.where(L.index.month.isin([6, 7, 8, 9, 10]), "S", "W")
    L["ym"] = L.index.to_period("M")
    L["p"] = [R.period(t.hour, t.weekday() >= 5) for t in L.index]
    L["p_old"] = [_per_old(t.hour, t.month) for t in L.index]
    new = (R.bill_nem(L), R.bill_nem(df))
    old_L, old_d = L.copy(), df.copy()
    old_L["p"], old_d["p"] = old_L["p_old"], old_d["p_old"]
    old = (R.bill_nem(old_L), R.bill_nem(old_d))
    return ((old[0] - old[1]) / ANNUAL_PRODUCTION,
            (new[0] - new[1]) / ANNUAL_PRODUCTION, new[0])

if __name__ == "__main__":
    if all(os.path.exists(f) for f in ("usage.csv", "samA.csv", "samB.csv")):
        BLENDED_OLD, BLENDED_NEW, nosolar = derive_blended()
        print(f"derived from data: no-solar ${nosolar:,.0f}/yr | blended old-TOU "
              f"{BLENDED_OLD:.4f} new-TOU {BLENDED_NEW:.4f}")
    else:
        BLENDED_OLD, BLENDED_NEW = FALLBACK_OLD, FALLBACK_NEW
        print("raw inputs not present - using last-derived fallback blended values")
    cum = 0.0
    print(f"invoice ${INVOICE:,.0f} paid {PAID} | PTO {PTO} (private/household.yaml)")
    print(f"{'year':<6}{'prod kWh':>10}{'value $':>10}{'cum $':>10}")
    for y in sorted(PROD):
        bl = BLENDED_NEW if y == 2026 else BLENDED_OLD
        v = PROD[y] * bl * RATE_IDX[y] / RATE_IDX[2026]
        cum += v
        marks = []
        if cum - v < INVOICE * (1 - ITC) <= cum: marks.append(f"<- crosses net-of-ITC ${INVOICE*(1-ITC):,.0f}")
        if cum - v < INVOICE <= cum: marks.append(f"<- crosses gross ${INVOICE:,.0f}")
        print(f"{y:<6}{PROD[y]:>10,}{v:>10,.0f}{cum:>10,.0f}  {' '.join(marks)}")
