#!/usr/bin/env python3
"""Deep-dive analyses: TOU-DR-P+battery, phantom load, EV sessions, vacations, Monte Carlo."""
import pandas as pd, numpy as np, json, datetime as dt
import sys, pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
import rates as R   # canonical TOU assignment (holiday rule included)

df=pd.read_csv("usage.csv",skiprows=13); df.columns=[c.strip() for c in df.columns]
df["dt"]=pd.to_datetime(df["Date"]+" "+df["Start Time"],format="%m/%d/%Y %I:%M %p")
for c in["Consumption","Generation"]: df[c]=pd.to_numeric(df[c])
end=dt.datetime(2026,7,24); d=df[(df.dt>=end-dt.timedelta(days=365))&(df.dt<end)].copy().reset_index(drop=True)
d["hour"]=d.dt.dt.hour+d.dt.dt.minute/60
d["seas"]=np.where(d.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)),"S","W")
# canonical date-aware assignment: derives the holiday rule itself
d["p"]=[R.period_at(t) for t in d.dt]; d["date"]=d.dt.dt.date
WFNBC=0.00591;PCIA=0.02828;NBC=0.01515-0.00007+WFNBC;BSC=0.79343
out={}

# ---------- 1. TOU-DR-P + battery wildcard ----------
# TOU-DR-P rates (CEA TOU-DR-PK gen + SDGE UDC), RYU events: assume 15 events/yr, 4-9pm,
# hottest summer days, +$1.16/kWh adder. Battery dodges by discharging through events.
UDCP={"S":{"on":0.32948,"off":0.32948,"sop":0.32948},"W":{"on":0.32948,"off":0.32948,"sop":0.32948}}
CEAP={"S":{"on":0.38778,"off":0.15609,"sop":0.04914},"W":{"on":0.13854,"off":0.05903,"sop":0.05138}}
UDC5={"S":{"on":0.31711,"off":0.31711,"sop":0.04114},"W":{"on":0.31711,"off":0.31711,"sop":0.04114}}
CEA5={"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}}
def rates(U,C): return np.array([U[s][p]+WFNBC+PCIA+C[s][p] for s,p in zip(d.seas,d.p)])
# pick 15 event days = highest on-peak import summer days
onp=d[(d.p=="on")&(d.seas=="S")].groupby("date").Consumption.sum().sort_values(ascending=False)
events=set(onp.head(15).index)
d["event"]=d["date"].isin(events)&(d.p=="on")
def cost_with_batt(U,C,adder_on_events, cap=13.5,pwr=11.5,eff=0.90):
    r=rates(U,C)+np.where(d.event & adder_on_events,1.16,0)
    soc=0;offv=np.zeros(len(d));forg=np.zeros(len(d));grid=np.zeros(len(d))
    P=d.p.values;H=d.hour.values;G=d.Generation.values;Cn=d.Consumption.values
    for i in range(len(d)):
        if P[i]!="on" and G[i]>0 and soc<cap:
            ch=min(G[i],pwr*.25,cap-soc);soc+=ch;forg[i]=ch*max(r[i]-NBC,0)
        elif P[i]=="sop" and H[i]<6 and soc<cap*.6:
            ch=min(pwr*.25,cap*.6-soc);soc+=ch;grid[i]=ch*r[i]
        if P[i]=="on" and Cn[i]>0 and soc>0:
            di=min(Cn[i],pwr*.25,soc*eff);soc-=di/eff;offv[i]=di*r[i]
    energy=(Cn*r).sum()-(G*np.clip(r-NBC,0,None)).sum()-offv.sum()+forg.sum()+grid.sum()
    return energy+365*BSC
drp_batt=cost_with_batt(UDCP,CEAP,True)
ev5_batt=cost_with_batt(UDC5,CEA5,False)
drp_nobatt_energy=(d.Consumption.values*(rates(UDCP,CEAP)+np.where(d.event,1.16,0))).sum() - (d.Generation.values*np.clip(rates(UDCP,CEAP)-NBC,0,None)).sum()
out["wildcard"]={"TOU-DR-P + PW3 (15 events dodged)":round(drp_batt),
                 "EV-TOU-5 + PW3":round(ev5_batt),
                 "TOU-DR-P no battery (events hit)":round(drp_nobatt_energy+365*BSC)}

# ---------- 2. Phantom / baseload ----------
# non-EV floor: 3-5am intervals excluding EV charging (kW>2) days-hours
night=d[(d.hour>=3)&(d.hour<5)]
clean=night[night.Consumption<=0.5]  # exclude EV-charging intervals
base_kw=clean.Consumption.quantile(0.25)*4
r5=rates(UDC5,CEA5)
avg_sop=0.1257  # blended SOP rate
out["phantom"]={"baseload_kw":round(float(base_kw),2),
  "annual_kwh":round(float(base_kw*8760)),
  "annual_cost_at_blend":round(float(base_kw*8760*0.20)),  # blended across periods ~20c
  "note":"25th-pct 3-5am non-EV draw; cost blends SOP/off/on exposure"}

# ---------- 3. EV sessions ----------
d["kw"]=d.Consumption*4
ev=d.kw>6.5   # EV charger signature ~7-11.5 kW
d["evblk"]=(ev!=ev.shift()).cumsum()
sess=[]
for k,g in d[ev].groupby(d.evblk[ev]):
    kwh=g.Consumption.sum()-0.4*len(g)*0.25  # subtract house base
    if kwh<3: continue
    r=r5[g.index]
    cost=(g.Consumption.values*r).sum()
    sess.append({"start":g.dt.iloc[0],"h":len(g)*0.25,"kwh":kwh,"cost":cost,
                 "on_kwh":g[g.p=="on"].Consumption.sum(),"off_kwh":g[g.p=="off"].Consumption.sum()})
S=pd.DataFrame(sess)
out["ev_sessions"]={"count":len(S),"kwh_total":round(S.kwh.sum()),"cost_total":round(S.cost.sum()),
  "avg_kwh":round(S.kwh.mean(),1),"sessions_touching_onpeak":int((S.on_kwh>1).sum()),
  "onpeak_kwh_in_sessions":round(S.on_kwh.sum()),"offpeak_kwh_in_sessions":round(S.off_kwh.sum()),
  "cost_if_all_sop":round(float(S.kwh.sum()*0.1257)),
  "wasted_vs_perfect":round(float(S.cost.sum()-S.kwh.sum()*0.1257))}

# ---------- 4. Vacation detection ----------
daily=d.groupby("date").agg(load_proxy=("Consumption","sum"),ev_kwh=("Consumption",lambda x:0))
# better: use SAM load
b=pd.read_csv("samB.csv").iloc[:,0].values; a=pd.read_csv("samA.csv").iloc[:,0].values
idx25=pd.date_range("2025-01-01",periods=8760,freq="h"); idx26=pd.date_range("2026-01-01",periods=8760,freq="h")
load=pd.concat([pd.Series(b,index=idx25),pd.Series(a,index=idx26)])["2025-07-24":"2026-07-23 23:00"]
dl=load.groupby(load.index.date).sum()
nonev_dl=load[load<7].groupby(load[load<7].index.date).sum()  # crude non-EV daily
thresh=nonev_dl.quantile(0.10)
away=nonev_dl[nonev_dl<max(thresh,20)]
out["vacation"]={"non_ev_daily_median":round(float(nonev_dl.median()),1),
  "away_day_threshold":round(float(max(thresh,20)),1),"away_days_detected":int(len(away)),
  "note":"days with non-EV load in bottom decile; away-mode savings depend on what stays on"}

# ---------- 5. Monte Carlo battery ROI (MID package marginal battery) ----------
rng=np.random.default_rng(42)
N=5000
esc=rng.uniform(0.00,0.10,N)          # annual rate escalation
fade=rng.uniform(0.005,0.025,N)       # battery capacity fade/yr
price=rng.uniform(12500,17000,N)      # installed cost
base_save=1347                         # marginal battery savings after behavior (yr-1)
payback=np.full(N,np.nan); npv10=np.zeros(N)
for i in range(N):
    cum=0; s=base_save
    for yr in range(1,26):
        s_yr=base_save*((1+esc[i])**(yr-1))*((1-fade[i])**(yr-1))
        cum+=s_yr
        if np.isnan(payback[i]) and cum>=price[i]: payback[i]=yr-1+(price[i]-(cum-s_yr))/s_yr
        if yr<=10: npv10[i]+=s_yr/(1.04**yr)
out["monte_carlo"]={"payback_median":round(float(np.nanmedian(payback)),1),
  "payback_p10":round(float(np.nanpercentile(payback,10)),1),
  "payback_p90":round(float(np.nanpercentile(payback,90)),1),
  "prob_payback_within_warranty10yr":round(float(np.mean(payback<=10)),3),
  "npv10_at_4pct_median":round(float(np.median(npv10)-np.median(price)))}
json.dump(out,open("deep_results.json","w"),indent=1,default=str)
print(json.dumps(out,indent=1,default=str))
