#!/usr/bin/env python3
"""Bill-validated NEM 2.0 billing model — monthly per-TOU-period netting, NBC on gross imports.

Accounting, matching the detailed bills' mechanics:
  * Energy charges (delivery UDC + generation + PCIA) are netted per (month, season,
    TOU-period): positive net kWh billed at the full energy rate; negative net credited
    at delivery + generation (PCIA and NBC are never credited).
  * Non-bypassable charges (NBC, ~$0.021/kWh incl. wildfire fund) are charged on GROSS
    imported kWh — they are not netted against exports (verified against bill line items:
    e.g. "Wildfire Fund Charge 308 kWh" on a period with 224 kWh net usage).
  * Base Services Charge $0.79343/day.

Validation: rates match the detailed bills to the penny. The model prices the whole year
at current 6/1/2026 tariffs, so its absolute total reads high against a historical year
billed largely on cheaper 2025 tariffs (rate vintage, not methodology — see report §10).
Use bills for absolute dollars; use this model for rankings and deltas.

Rates read off the actual bills (EV-TOU-5 + CEA Clean Impact Plus, eff. 6/1/2026).
"""
import pandas as pd, numpy as np, datetime as dt

CSV="usage.csv"  # SDG&E Green Button 15-min (skiprows=13)
import rates as R                                            # canonical TOU assignment
from rates import UDC, CEA, NBC, PCIA, BSC, energy, credit  # canonical (single source of truth)

def load():
    df=pd.read_csv(CSV,skiprows=13); df.columns=[c.strip() for c in df.columns]
    df["dt"]=pd.to_datetime(df["Date"]+" "+df["Start Time"],format="%m/%d/%Y %I:%M %p")
    for c in ["Consumption","Generation"]: df[c]=pd.to_numeric(df[c])
    df["hour"]=df.dt.dt.hour
    df["seas"]=np.where(df.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)),"S","W"); df["ym"]=df.dt.dt.to_period("M")
    # canonical date-aware assignment: derives the holiday rule itself
    df["p"]=[R.period_at(t) for t in df.dt]
    return df

def bill(frame, imp="Consumption", exp="Generation"):
    """Annual $: monthly per-period NEM netting + NBC on gross imports + daily BSC."""
    tot=0.0
    for _,m in frame.groupby("ym"):
        tot += m.dt.dt.date.nunique()*BSC + m[imp].sum()*NBC
        for s in ("S","W"):
            for p in ("on","off","sop"):
                sub=m[(m.seas==s)&(m.p==p)]
                net=sub[imp].sum()-sub[exp].sum()
                tot += net*(energy(s,p) if net>=0 else credit(s,p))
    return tot

if __name__=="__main__":
    d=load(); end=dt.datetime(2026,7,24); d=d[(d.dt>=end-dt.timedelta(days=365))&(d.dt<end)].copy()
    print("model baseline at 6/1/2026 rates: $%.0f/yr"%bill(d))
    print("(actual billed, 365-day audit largely on 2025 tariffs: $3,282)")
