#!/usr/bin/env python3
"""Bill-validated NEM 2.0 monthly-netting model (replaces the annual-netting approximation).

Why this exists: the original interval model priced exports too conservatively and used an
annual-netting frame, overstating the ABSOLUTE annual bill (~$4.7-4.9k modeled vs ~$3.0k
actually billed). This version does proper NEM 2.0 monthly per-TOU-period netting and was
validated against 12 actual SDG&E bills: winter/spring months reproduce within ±$25; summer
months still run high (under-credited solar exports / NEM annual-true-up accounting that
can't be fully resolved without the true-up statement).

KEY FINDING: plan RANKINGS and behavior/battery SAVINGS are robust because they're driven by
on-peak (4-9pm) import arbitrage, which is priced correctly at full retail (~$0.87/kWh summer)
in every model. Only the absolute baseline and projected-bill levels needed re-anchoring to
the actual bills. Report absolute dollars against the real bill ($3,004/yr, ~$250/mo);
trust the model for differences (savings) and rankings.

Rates are the ACTUAL rates read off the detailed bills (EV-TOU-5 + CEA Clean Impact Plus,
6/1/2026): delivery UDC, CEA generation, NBC ~0.021/kWh (on gross imports), PCIA 0.02828,
Base Services Charge 0.79343/day. Exports credited at delivery+generation (NBC/PCIA not
credited). No CEA relief credit (confirmed absent on bills).
"""
import pandas as pd, numpy as np, datetime as dt

CSV="usage.csv"  # SDG&E Green Button 15-min (skiprows=13)
UDC={"S":{"on":0.30203,"off":0.30203,"sop":0.02606},"W":{"on":0.31174,"off":0.31174,"sop":0.02606}}
CEA={"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}}
NBC=0.021; PCIA=0.02828; BSC=0.79343
retail=lambda s,p: UDC[s][p]+CEA[s][p]+NBC+PCIA
credit=lambda s,p: UDC[s][p]+CEA[s][p]

def load():
    df=pd.read_csv(CSV,skiprows=13); df.columns=[c.strip() for c in df.columns]
    df["dt"]=pd.to_datetime(df["Date"]+" "+df["Start Time"],format="%m/%d/%Y %I:%M %p")
    for c in ["Consumption","Generation"]: df[c]=pd.to_numeric(df[c])
    df["hour"]=df.dt.dt.hour+df.dt.dt.minute/60; df["wkend"]=df.dt.dt.weekday>=5
    df["seas"]=np.where(df.dt.dt.month.isin([6,7,8,9,10]),"S","W"); df["ym"]=df.dt.dt.to_period("M")
    def per(r):
        h=r.hour
        if 16<=h<21: return "on"
        if r.wkend: return "sop" if h<14 else "off"
        return "sop" if (h<6 or 10<=h<14) else "off"
    df["p"]=df.apply(per,axis=1); return df

def bill(frame, imp="imp", exp="exp"):
    """Annual $ via monthly per-TOU-period NEM netting."""
    tot=0.0
    for _,m in frame.groupby("ym"):
        tot += m.dt.dt.date.nunique()*BSC
        for s in ("S","W"):
            for p in ("on","off","sop"):
                sub=m[(m.seas==s)&(m.p==p)]
                net=sub[imp].sum()-sub[exp].sum()
                tot += net*(retail(s,p) if net>=0 else credit(s,p))
    return tot

if __name__=="__main__":
    d=load(); end=dt.datetime(2026,7,24); d=d[(d.dt>=end-dt.timedelta(days=365))&(d.dt<end)].copy()
    d["imp"]=d.Consumption; d["exp"]=d.Generation
    print("model baseline: $%.0f/yr  (actual billed ~$3,004)"%bill(d))
