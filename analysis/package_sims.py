#!/usr/bin/env python3
"""Unified plan+battery+behavior packages simulation.
Answers: (a) does a battery change the best-plan answer? (plan x battery matrix)
(b) Low/Mid/High packages: annual cost, savings vs today, monthly bills, payback.
Baseline = current: EV-TOU-5, CEA (no relief credit), current behavior.
"""
import pandas as pd, numpy as np, json, datetime as dt

df=pd.read_csv("usage.csv",skiprows=13); df.columns=[c.strip() for c in df.columns]
df["dt"]=pd.to_datetime(df["Date"]+" "+df["Start Time"],format="%m/%d/%Y %I:%M %p")
for c in["Consumption","Generation"]: df[c]=pd.to_numeric(df[c])
end=dt.datetime(2026,7,24); d=df[(df.dt>=end-dt.timedelta(days=365))&(df.dt<end)].copy().reset_index(drop=True)
d["hour"]=d.dt.dt.hour+d.dt.dt.minute/60; d["wkend"]=d.dt.dt.weekday>=5
d["seas"]=np.where(d.dt.dt.month.isin([6,7,8,9,10]),"S","W")
def per(r):
    h=r.hour
    if 16<=h<21: return "on"
    if r.wkend: return "sop" if h<14 else "off"
    return "sop" if (h<6 or 10<=h<14) else "off"
d["p"]=d.apply(per,axis=1); d["date"]=d.dt.dt.date; d["month"]=d.dt.dt.to_period("M")
WFNBC=0.00591;PCIA=0.02828;NBC=0.01515-0.00007+WFNBC; BSC=0.79343
UDC={"EV-TOU-5":{"S":{"on":0.31711,"off":0.31711,"sop":0.04114},"W":{"on":0.31711,"off":0.31711,"sop":0.04114}},
     "EV-TOU-2":{"S":{"on":0.30372,"off":0.30372,"sop":0.16275},"W":{"on":0.30372,"off":0.30372,"sop":0.16275}},
     "TOU-DR1": {"S":{"on":0.32948,"off":0.32948,"sop":0.32948},"W":{"on":0.32948,"off":0.32948,"sop":0.32948}}}
CEA={"EV-TOU-5":{"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}},
     "EV-TOU-2":{"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}},
     "TOU-DR1": {"S":{"on":0.55397,"off":0.22298,"sop":0.04914},"W":{"on":0.19791,"off":0.08433,"sop":0.05138}}}
BASELINE_CREDIT=-0.10663; BASELINE={"S":10.4,"W":9.6}

def rates_for(plan):
    return np.array([UDC[plan][s][p]+WFNBC+PCIA+CEA[plan][s][p] for s,p in zip(d.seas,d.p)])

def behavior_adjust(cons):
    """Shift discretionary load: on-peak excess >2.5kW moved to SOP; 6-9am off-peak
    high-power excess moved to overnight SOP. Returns adjusted consumption array and
    kWh-moved (charged later at SOP rate)."""
    c=cons.copy(); moved=0.0
    cap=0.625  # 2.5 kW * 0.25 h
    onmask=(d.p=="on")&(c>cap)
    moved+= (c[onmask]-cap).sum(); c[onmask]=cap
    spill=(d.p=="off")&(d.hour>=6)&(d.hour<9)&(c>cap)
    moved+=(c[spill]-cap).sum(); c[spill]=cap
    return c, moved

def battery_dispatch(cons, gen, rate, cap, pwr, eff=0.90):
    """Returns (onpeak_offset_kwh_value, forgone_credit, grid_charge_cost) per year
    and per-month cost deltas. Greedy: charge from exports/SOP, discharge on-peak."""
    soc=0.0; offset=np.zeros(len(cons)); forgone=np.zeros(len(cons)); grid=np.zeros(len(cons))
    P=d.p.values; H=d.hour.values
    for i in range(len(cons)):
        step=0.25
        if P[i]!="on" and gen[i]>0 and soc<cap:
            ch=min(gen[i],pwr*step,cap-soc); soc+=ch; forgone[i]=ch*max(rate[i]-NBC,0)
        elif P[i]=="sop" and H[i]<6 and soc<cap*0.6:
            ch=min(pwr*step,cap*0.6-soc); soc+=ch; grid[i]=ch*rate[i]
        if P[i]=="on" and cons[i]>0 and soc>0:
            di=min(cons[i],pwr*step,soc*eff); soc-=di/eff; offset[i]=di*rate[i]
    return offset,forgone,grid

def annual_cost(plan, cons, gen, battery=None, moved_kwh=0.0):
    rate=rates_for(plan)
    charges=cons*rate; credits=gen*np.clip(rate-NBC,0,None)
    off=for_=gr=np.zeros(len(cons))
    if battery: off,for_,gr=battery_dispatch(cons,gen,rate,*battery)
    # moved load billed at SOP rate of its plan (weekday overnight)
    sop_rate={"S":UDC[plan]["S"]["sop"]+WFNBC+PCIA+CEA[plan]["S"]["sop"],
              "W":UDC[plan]["W"]["sop"]+WFNBC+PCIA+CEA[plan]["W"]["sop"]}
    moved_cost=moved_kwh*np.mean([sop_rate["S"],sop_rate["W"]])
    interval_net=charges-credits-off+for_+gr
    monthly=pd.Series(interval_net).groupby(d.month.values).sum()
    days=pd.Series(1,index=d.dt).resample("D").first().groupby(lambda x:pd.Period(x,"M")).count()
    monthly=monthly+days*BSC
    total=float(interval_net.sum()+moved_cost+365*BSC)
    # baseline credit for TOU-DR1
    if plan=="TOU-DR1":
        g=pd.DataFrame({"net":cons-gen,"m":d.month,"s":d.seas,"date":d.date}).groupby("m")
        bl=sum(min(max(r.net.sum(),0),1.3*BASELINE["S" if r.s.mode()[0]=="S" else "W"]*r.date.nunique())*BASELINE_CREDIT for _,r in g)
        total+=bl
    return total, monthly, moved_cost

cons0=d.Consumption.values.astype(float); gen0=d.Generation.values.astype(float)
consB,moved=behavior_adjust(cons0)
PW3=(13.5,11.5); PW3X=(27.0,11.5)

out={}
# (a) plan x battery matrix (current behavior)
matrix={}
for plan in["EV-TOU-5","EV-TOU-2","TOU-DR1"]:
    nb,_,_=annual_cost(plan,cons0,gen0)
    wb,_,_=annual_cost(plan,cons0,gen0,battery=PW3)
    matrix[plan]={"no_battery":round(nb),"with_PW3":round(wb),"battery_value":round(nb-wb)}
out["plan_battery_matrix"]=matrix
# (b) packages (all on EV-TOU-5)
base,mon_base,_=annual_cost("EV-TOU-5",cons0,gen0)
low,mon_low,mc  =annual_cost("EV-TOU-5",consB,gen0,moved_kwh=moved)
mid,mon_mid,_   =annual_cost("EV-TOU-5",consB,gen0,battery=PW3,moved_kwh=moved)
high,mon_high,_ =annual_cost("EV-TOU-5",consB,gen0,battery=PW3X,moved_kwh=moved)
def pkg(name,total,cost_hw,mon):
    sav=base-total
    return {"name":name,"annual_cost":round(total),"annual_savings":round(sav),
            "hw_cost":cost_hw,"payback_yr":round(cost_hw/sav,1) if cost_hw>0 and sav>0 else 0,
            "avg_monthly_bill":round(total/12),
            "monthly_min":round(float(mon.min())),"monthly_max":round(float(mon.max()))}
out["baseline"]={"annual_cost":round(base),"avg_monthly_bill":round(base/12),
                 "monthly_min":round(float(mon_base.min())),"monthly_max":round(float(mon_base.max()))}
out["moved_kwh"]=round(float(moved))
out["packages"]=[pkg("LOW — behavior only ($0)",low,0,mon_low),
                 pkg("MID — behavior + 1x Powerwall 3 (~$14,500)",mid,14500,mon_mid),
                 pkg("HIGH — behavior + PW3 + Expansion (~$20,400)",high,20400,mon_high)]
# battery-only marginal value on top of behavior (for honesty about interaction)
out["battery_marginal_after_behavior"]={"PW3":round(low-mid),"PW3X":round(low-high)}
json.dump(out,open("package_results.json","w"),indent=1)
print(json.dumps(out,indent=1))
