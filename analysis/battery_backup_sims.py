#!/usr/bin/env python3
"""Battery arbitrage + backup endurance simulations (as run Jul 24, 2026).
Inputs: usage.csv (SDGE Green Button 15-min), samA.csv/samB.csv (Enphase SAM 8760
hourly consumption 2026/2025). Rates: EV-TOU-5 + CEA eff. 6/1/2026, PCIA 2023 vintage.
Outputs: battery_sim.json (arbitrage), backup_endurance.json (outage endurance).
"""
import pandas as pd, numpy as np, json, datetime as dt
import sys, pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import rates as R   # canonical TOU assignment (holiday rule included)

# ---- load SDGE 15-min ----
df=pd.read_csv("usage.csv",skiprows=13); df.columns=[c.strip() for c in df.columns]
df["dt"]=pd.to_datetime(df["Date"]+" "+df["Start Time"],format="%m/%d/%Y %I:%M %p")
for c in["Consumption","Generation"]: df[c]=pd.to_numeric(df[c])
end=dt.datetime(2026,7,24); d=df[(df.dt>=end-dt.timedelta(days=365))&(df.dt<end)].copy()
d["hour"]=d.dt.dt.hour+d.dt.dt.minute/60
d["wkend"]=d.dt.dt.date.map(R.off_peak_day)
d["seas"]=np.where(d.dt.dt.month.isin([6,7,8,9,10]),"S","W")
# canonical date-aware assignment: derives the holiday rule itself
d["p"]=[R.period_at(t) for t in d.dt]; d["date"]=d.dt.dt.date
WFNBC=0.00591;PCIA=0.02828;NBC=0.01515-0.00007+WFNBC
UDC={"S":{"on":0.31711,"off":0.31711,"sop":0.04114},"W":{"on":0.31711,"off":0.31711,"sop":0.04114}}
CEA={"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}}
d["rate"]=[UDC[s][p]+WFNBC+PCIA+CEA[s][p] for s,p in zip(d.seas,d.p)]

# ---- arbitrage sim: charge from would-be exports (forgone credit) + overnight SOP top-up,
#      discharge against on-peak imports; 90% round-trip ----
def sim(cap,pwr,name,eff=0.90):
    soc=0.0; forgone=grid=offset=cyc=0.0
    for row in d.itertuples():
        step=0.25
        if row.p in("sop","off") and row.Generation>0 and soc<cap:
            c=min(row.Generation,pwr*step,cap-soc); soc+=c; forgone+=c*max(row.rate-NBC,0)
        elif row.p=="sop" and soc<cap*0.6 and row.hour<6:
            c=min(pwr*step,cap*0.6-soc); soc+=c; grid+=c*row.rate
        if row.p=="on" and row.Consumption>0 and soc>0:
            disc=min(row.Consumption,pwr*step,soc*eff); soc-=disc/eff; offset+=disc*row.rate; cyc+=disc/cap
    return {"config":name,"usable_kwh":cap,"power_kw":pwr,"onpeak_offset_value":round(offset),
            "forgone_export_credits":round(forgone),"grid_charge_cost":round(grid),
            "net_annual_savings":round(offset-forgone-grid),"equiv_full_cycles":round(cyc)}
configs=[(5.0,3.84,"1x Enphase IQ 5P"),(10.0,7.08,"1x Enphase IQ 10C"),(13.5,11.5,"1x Tesla Powerwall 3"),
         (15.0,7.68,"3x Enphase IQ 5P"),(20.0,7.08,"2x Enphase IQ 10C"),(27.0,11.5,"PW3 + 1 Expansion")]
json.dump([sim(*c) for c in configs],open("battery_sim.json","w"),indent=1)

# ---- backup endurance: stitched hourly load (SAM 8760) + derived hourly production ----
b=pd.read_csv("samB.csv").iloc[:,0].values; a=pd.read_csv("samA.csv").iloc[:,0].values
idx25=pd.date_range("2025-01-01",periods=8760,freq="h"); idx26=pd.date_range("2026-01-01",periods=8760,freq="h")
load=pd.concat([pd.Series(b,index=idx25),pd.Series(a,index=idx26)])["2025-07-24":"2026-07-23 23:00"]
h=df.set_index("dt").resample("h")[["Consumption","Generation"]].sum()["2025-07-24":"2026-07-23 23:00"]
m=pd.DataFrame({"load":load,"imp":h.Consumption,"exp":h.Generation}).dropna()
m["prod"]=(m["load"]-m["imp"]+m["exp"]).clip(lower=0)
ev=np.where(m["load"]>7,m["load"]-1.5,0)          # EV heuristic: >7 kWh/h hours
m["nonev"]=m["load"]-ev
m["t1"]=np.minimum(m["nonev"],0.7)                 # essentials tier: 0.7 kW cap
m["t2"]=m["nonev"]                                 # whole house minus EV
def endurance(cap,pwr,tier):
    hrs=[]; L=m[tier].values; P=m["prod"].values; idx=m.index
    for s in [i for i,ts in enumerate(idx) if ts.hour==18]:
        soc=cap;t=0;i=s
        while i<len(L)-1 and t<24*14:
            net=min(L[i],pwr)-P[i]
            if net>0:
                if soc>=net*1.05: soc-=net*1.05
                else: break
            else: soc=min(cap,soc-net*0.9)
            t+=1;i+=1
        hrs.append(t)
    return round(float(np.median(hrs))), round(float(np.percentile(hrs,10)))
out={}
for cfg,cap,pwr in [("IQ 5P",5,3.84),("IQ 10C",10,7.08),("PW3",13.5,11.5),("PW3+Exp",27,11.5)]:
    for tier in["t1","t2"]:
        med,p10=endurance(cap,pwr,tier); out[f"{cfg}|{tier}"]={"median_h":med,"p10_h":p10}
json.dump(out,open("backup_endurance.json","w"),indent=1)
print("done")
