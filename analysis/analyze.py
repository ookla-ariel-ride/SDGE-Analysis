#!/usr/bin/env python3
"""SDGE rate plan analysis, RELIEF-CREDIT VARIANT — superseded; see analyze_norelief.py.

The bills showed the CEA relief credit does not apply (CLAUDE.md section 0), so
analyze_norelief.py is the live model and owns the committed plan_results.csv /
hourly_profile.csv / monthly.csv. This variant is kept for the relief comparison
only and writes *_relief-suffixed outputs (gitignored) so the two scripts can
never overwrite each other's artifacts — they used to collide on all four names,
with whichever ran last winning silently.

SDGE rate plan analysis — single-family home, SDGE Inland climate zone.
Profile: CEA generation (Clean Impact assumed), NEM 2.0, currently EV-TOU-5.
Rates: SDGE UDC effective 6/1/2026; CEA generation effective 6/1/2026; PCIA 2023 vintage.
Input: Green Button 15-minute CSV (path below) — keep the raw CSV out of any public repo:
it contains name, service address, and account/meter numbers.
"""
import pandas as pd, numpy as np, json, datetime as dt

CSV = "usage.csv"   # the private/verify sandbox convention

import pathlib as _pl


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (_pl.Path.cwd(), _pl.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


_DATA = _repo_root() / "data"


# ---------- load ----------
df = pd.read_csv(CSV, skiprows=13, quotechar='"')
df.columns = [c.strip() for c in df.columns]
df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%m/%d/%Y %I:%M %p")
for c in ["Consumption","Generation","Net"]:
    df[c] = pd.to_numeric(df[c])
df = df.sort_values("dt").reset_index(drop=True)
print("rows", len(df), df.dt.min(), df.dt.max())
print("totals: cons %.1f gen %.1f net %.1f" % (df.Consumption.sum(), df.Generation.sum(), df.Net.sum()))

# Use last 365 full days ending 7/22/2026 (last full day 7/23 partial? data runs through 7/23 23:45 => full)
end = dt.datetime(2026,7,24)
start = end - dt.timedelta(days=365)
d = df[(df.dt >= start) & (df.dt < end)].copy()
import rates as _R   # coverage validation only; rate tables here stay published-table by design
_R.validate_interval_coverage(zip(d.dt.dt.date, d.dt.dt.hour + d.dt.dt.minute / 60),
                              start.date(), (end - dt.timedelta(days=1)).date())
print("analysis window:", d.dt.min(), d.dt.max(), "days:", (d.dt.max()-d.dt.min()))

# ---------- calendar ----------
def holidays(years):
    H=[]
    for y in years:
        H += [dt.date(y,1,1), dt.date(y,7,4), dt.date(y,11,11), dt.date(y,12,25)]
        # Presidents (3rd Mon Feb), Memorial (last Mon May), Labor (1st Mon Sep), Thanksgiving (4th Thu Nov)
        def nth_weekday(y,m,wd,n):
            c=dt.date(y,m,1); add=(wd-c.weekday())%7; c=c+dt.timedelta(days=add+7*(n-1)); return c
        H.append(nth_weekday(y,2,0,3)); H.append(nth_weekday(y,9,0,1)); H.append(nth_weekday(y,11,3,4))
        # memorial: last Monday of May
        c=dt.date(y,5,31)
        while c.weekday()!=0: c-=dt.timedelta(days=1)
        H.append(c)
    return set(H)
HOL = holidays([2025,2026])

d["date"]=d.dt.dt.date
d["hour"]=d.dt.dt.hour + d.dt.dt.minute/60
d["month"]=d.dt.dt.month
d["summer"]=d.month.isin([6,7,8,9,10])
d["wkend"]=(d.dt.dt.weekday>=5) | (d.date.isin(HOL))

# TOU period assignment (current windows eff. May 2026, same for SDGE & CEA 3-period plans)
def period3(row):
    h=row.hour
    if 16<=h<21: return "on"
    if row.wkend:
        if h<14: return "sop"
        return "off"   # 14-16, 21-24
    else:
        if h<6 or (10<=h<14): return "sop"
        return "off"
d["p3"]=d.apply(period3,axis=1)
d["p2"]=np.where((d.hour>=16)&(d.hour<21),"on","off")   # TOU-DR2

# ---------- rates ----------
WFNBC_DWR = 0.00591
PCIA = 0.02828           # 2023 vintage CCA
NBC  = 0.01515 + 0.00000 - 0.00007 + WFNBC_DWR   # PPP+ND+CTC+WF-NBC/DWR ~= 0.02099 (not offset by exports)
BSC  = 0.79343           # $/day all residential plans

# SDGE UDC totals (6/1/2026)
UDC = {
 "EV-TOU-5": {"S":{"on":0.31711,"off":0.31711,"sop":0.04114},"W":{"on":0.31711,"off":0.31711,"sop":0.04114}},
 "EV-TOU-2": {"S":{"on":0.30372,"off":0.30372,"sop":0.16275},"W":{"on":0.30372,"off":0.30372,"sop":0.16275}},
 "TOU-DR1":  {"S":{"on":0.32948,"off":0.32948,"sop":0.32948},"W":{"on":0.32948,"off":0.32948,"sop":0.32948}},
 "TOU-DR2":  {"S":{"on":0.33396,"off":0.32750},"W":{"on":0.32948,"off":0.32948}},
 "TOU-DR-P": {"S":{"on":0.32948,"off":0.32948,"sop":0.32948},"W":{"on":0.32948,"off":0.32948,"sop":0.32948}},
 "TOU-ELEC": {"S":{"on":0.25317,"off":0.25317,"sop":0.25317},"W":{"on":0.25317,"off":0.25317,"sop":0.25317}},
}
# SDGE bundled EECC (for the bundled-SDGE comparison) 6/1/2026
EECC = {
 "EV-TOU-5": {"S":{"on":0.47019,"off":0.17311,"sop":0.08147},"W":{"on":0.19990,"off":0.14337,"sop":0.07410}},
 "EV-TOU-2": {"S":{"on":0.47019,"off":0.17311,"sop":0.08147},"W":{"on":0.19990,"off":0.14337,"sop":0.07410}},
 "TOU-DR1":  {"S":{"on":0.34920,"off":0.12853,"sop":0.04121},"W":{"on":0.27475,"off":0.19304,"sop":0.10228}},
 "TOU-DR2":  {"S":{"on":0.34920,"off":0.08432},"W":{"on":0.27475,"off":0.13777}},
 "TOU-DR-P": {"S":{"on":0.19848,"off":0.15523,"sop":0.08247},"W":{"on":0.25057,"off":0.17606,"sop":0.09329}},
 "TOU-ELEC": {"S":{"on":0.45690,"off":0.12945,"sop":0.08637},"W":{"on":0.24311,"off":0.11774,"sop":0.07856}},
}
# CEA generation 6/1/2026 (Clean Impact); Rate Relief Credit applies to Clean Impact
CEA = {
 "EV-TOU-5": {"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}},
 "EV-TOU-2": {"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}},
 "TOU-ELEC": {"S":{"on":0.51684,"off":0.15975,"sop":0.04961},"W":{"on":0.24430,"off":0.15782,"sop":0.05187}},  # maps to EV-TOU
 "TOU-DR1":  {"S":{"on":0.55397,"off":0.22298,"sop":0.04914},"W":{"on":0.19791,"off":0.08433,"sop":0.05138}},
 "TOU-DR2":  {"S":{"on":0.53685,"off":0.14663},"W":{"on":0.19180,"off":0.06703}},
 "TOU-DR-P": {"S":{"on":0.38778,"off":0.15609,"sop":0.04914},"W":{"on":0.13854,"off":0.05903,"sop":0.05138}},  # CEA TOU-DR-1-PK
}
CEA_RELIEF = -0.03871   # per kWh, Clean Impact
BASELINE_CREDIT = -0.10663  # $/kWh up to 130% baseline (TOU-DR1, TOU-DR2, TOU-DR-P)
BASELINE = {"S":10.4,"W":9.6}  # Inland basic allowance kWh/day

PLANS = ["EV-TOU-5","EV-TOU-2","TOU-DR1","TOU-DR2","TOU-ELEC","TOU-DR-P"]
HAS_BASELINE = {"TOU-DR1","TOU-DR2","TOU-DR-P"}

d["seas"]=np.where(d.summer,"S","W")

def bill(plan, provider="CEA", relief=True):
    """Annual bill. provider CEA: UDC+PCIA+WFNBC+CEA gen; SDGE: UDC+WFNBC+EECC."""
    pcol = "p2" if plan=="TOU-DR2" else "p3"
    per = d[pcol]; seas=d.seas
    udc = np.array([UDC[plan][s][p] for s,p in zip(seas,per)])
    if provider=="CEA":
        gen = np.array([CEA[plan][s][p] for s,p in zip(seas,per)])
        if relief: gen = gen + CEA_RELIEF
        rate = udc + WFNBC_DWR + PCIA + gen
    else:
        gen = np.array([EECC[plan][s][p] for s,p in zip(seas,per)])
        rate = udc + WFNBC_DWR + gen
    imp = d.Consumption.values; exp = d.Generation.values
    charges = (imp*rate).sum()
    credits = (exp*np.maximum(rate-NBC,0)).sum()
    energy = charges - credits
    # baseline credit on net monthly consumption up to 130% baseline
    bl = 0.0
    if plan in HAS_BASELINE:
        m = d.groupby([d.dt.dt.to_period("M")]).apply(lambda g: pd.Series({
            "net": g.Net.sum(), "days": g.date.nunique(),
            "seas": "S" if g.summer.mean()>0.5 else "W"}), include_groups=False)
        for _,r in m.iterrows():
            cap = 1.3*BASELINE[r.seas]*r.days
            if r.net>0: bl += min(r.net,cap)*BASELINE_CREDIT
    fixed = BSC*365
    total = energy + bl + fixed
    return {"plan":plan,"provider":provider,"energy":round(energy,2),"baseline_credit":round(bl,2),
            "fixed":round(fixed,2),"total":round(total,2)}

results=[]
for p in PLANS:
    results.append(bill(p,"CEA"))
    results.append(bill(p,"SDGE"))
res = pd.DataFrame(results)
print(res.sort_values("total").to_string())

# ---------- usage stats ----------
stats={}
stats["total_import"]=round(d.Consumption.sum(),1); stats["total_export"]=round(d.Generation.sum(),1)
stats["net"]=round(d.Net.sum(),1)
by = d.groupby(["seas","p3"]).agg(imp=("Consumption","sum"),exp=("Generation","sum")).round(1)
print(by)
stats["by_period"]=by.reset_index().to_dict("records")
# on-peak import cost at current plan
seas=d.seas; per=d.p3
rate_ev5 = np.array([UDC["EV-TOU-5"][s][p] for s,p in zip(seas,per)]) + WFNBC_DWR + PCIA + \
           np.array([CEA["EV-TOU-5"][s][p] for s,p in zip(seas,per)]) + CEA_RELIEF
d["cost_ev5"]=d.Consumption*rate_ev5 - d.Generation*np.maximum(rate_ev5-NBC,0)
onpeak = d[d.p3=="on"]
stats["onpeak_import_kwh"]=round(onpeak.Consumption.sum(),1)
stats["onpeak_cost"]=round((onpeak.Consumption*rate_ev5[d.p3=="on"]).sum(),2)
# heavy overnight load = EV charging (>2kW sustained 12am-6am)
night = d[(d.hour<6)]
stats["night_import_kwh"]=round(night.Consumption.sum(),1)
# hourly profile
prof = d.groupby(d.dt.dt.hour).agg(imp=("Consumption","mean"),exp=("Generation","mean"),net=("Net","mean")).round(3)
print(prof)
_tmp = {n: _DATA / (n + ".tmp" + str(__import__("os").getpid())) for n in ("hourly_profile_relief.csv", "monthly_relief.csv", "plan_results_relief.csv", "stats_relief.json")}
prof.to_csv(_tmp["hourly_profile_relief.csv"])
# monthly
mon = d.groupby(d.dt.dt.to_period("M")).agg(imp=("Consumption","sum"),exp=("Generation","sum"),net=("Net","sum")).round(1)
print(mon)
mon.to_csv(_tmp["monthly_relief.csv"])
res.to_csv(_tmp["plan_results_relief.csv"],index=False)
with open(_tmp["stats_relief.json"], "w") as _fh:
    json.dump(stats, _fh, indent=1)
# every artifact fully serialized -- promote as a SET via the transactional
# protocol (analysis/publish.py): all four land, or the originals are restored.
# A bare per-file replace loop is atomic per file but not per set, and a kill
# between replacements would leave data/ mixing two runs.
import publish as _publish
_publish.promote_set({n: str(t) for n, t in _tmp.items()}, str(_DATA))
print(json.dumps(stats,indent=1))
