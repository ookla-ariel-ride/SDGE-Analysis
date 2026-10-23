#!/usr/bin/env python3
"""NEM 2.0 loader and compatibility entrypoint for the canonical rates engine.

bill() delegates to rates.bill_nem: monthly energy netting, positive physical
interval-net NBC, and daily fixed charge. rates.py documents bill evidence,
June 2026 rate provenance and the limits of historical bill replication.
The raw Consumption and Generation loader and fixed native year stay intact.
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

def bill(frame, imp="Consumption", exp="Generation", *, physical_exp=None):
    """Canonical bill; exp is credited exports, physical_exp is physical exports."""
    return R.bill_nem(frame, imp, exp, physical_exp=physical_exp)

if __name__=="__main__":
    d=load(); end=dt.datetime(2026,7,24); d=d[(d.dt>=end-dt.timedelta(days=365))&(d.dt<end)].copy()
    R.validate_interval_coverage(zip(d.dt.dt.date, d.dt.dt.hour + d.dt.dt.minute / 60),
                                 (end - dt.timedelta(days=365)).date(),
                                 (end - dt.timedelta(days=1)).date())
    print("model baseline at 6/1/2026 rates: $%.0f/yr"%bill(d))
    print("(actual billed, 365-day audit largely on 2025 tariffs: $3,282)")
