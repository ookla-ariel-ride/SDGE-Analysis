#!/usr/bin/env python3
"""Battery dispatch policies — the report's battery economics (integrated engine).

Principle: a stored kWh costs ~8.4c (midday surplus: forgone super-off-peak export
credit / RTE) or ~13.9c (super-off-peak grid top-up / RTE); every import priced above
that is worth serving. Non-super-off-peak imports price at 51-87c, so the price-aware
policy discharges against ALL of them; super-off-peak imports (12.5c) are never served.

Three policies x two configurations (13.5 kWh Powerwall 3 / 27 kWh PW3+Expansion,
both 11.5 kW, 90% round-trip split as sqrt-eta per direction):
  evening  discharge 16-21h only; overnight grid top-up to 60% capacity
  twowin   + 6-9am house load
  greedy   price-aware: any non-super-off-peak import; top-up toward full in any
           super-off-peak gap; solar surplus always charges first
EV exclusion: intervals >= 2.5 kW outside on-peak are EV spillover; the (free)
schedule fix moves that load, so the battery never serves it outside on-peak.

INTEGRATED VALUATION: the battery modifies the physical import/export series and the
modified year is re-billed with the canonical engine (rates.bill_nem: monthly
per-period NEM netting, NBC on gross imports) — the same engine used for the
behavior model. The post-behavior block runs the EV shift (behavior_rebuild
scenario a) FIRST, then the battery on the shifted load: one pipeline, one rate
set, no cross-model splicing.

Output: battery_dispatch_policies.json — savings per policy/config, kWh served,
cycles/day, summer hourly grid-import profiles, escalation ladder, post_behavior
package figures. This script fully regenerates the committed artifact.
"""
import numpy as np, pandas as pd, json
import behavior_rebuild as br
import rates as R

ETA = np.sqrt(0.90); PWRQ = 11.5 / 4

def run_batt(d, imp0, gen0, cap, policy):
    imp = imp0.copy(); exp = gen0.copy()
    soc = cap / 2; served = 0.0; thru = 0.0
    p = d.p.values; h = d.hour.values; kw = imp0 * 4
    for i in range(len(d)):
        if exp[i] > 0:
            c = min(exp[i], (cap - soc) / ETA, PWRQ)
            if c > 0: soc += c * ETA; exp[i] -= c; thru += c * ETA
            continue
        if p[i] == "sop":
            grid_ok = (policy == "greedy") or (h[i] < 6)
            lim = cap if policy == "greedy" else 0.6 * cap
            take = min(max((lim - soc) / ETA, 0), PWRQ) if grid_ok else 0
            if take > 0: soc += take * ETA; imp[i] += take; thru += take * ETA
            continue
        disch = (16 <= h[i] < 21) or \
                (policy == "twowin" and 6 <= h[i] < 9 and kw[i] < 2.5) or \
                (policy == "greedy" and kw[i] < 2.5)
        if disch:
            dd = min(imp[i], soc * ETA, PWRQ)
            if dd > 0: soc -= dd / ETA; imp[i] -= dd; served += dd
    return imp, exp, served, thru

def billed(d, imp, exp):
    f = d.copy(); f["I"] = imp; f["E"] = exp
    return R.bill_nem(f, "I", "E")

def summer_profile(d, imp):
    f = d.copy(); f["gi"] = imp; su = f[f.seas == "S"]
    return [round(float(su[su.hour.astype(int) == hh].groupby(
        su[su.hour.astype(int) == hh].dt.dt.date).gi.sum().mean()), 2) for hh in range(24)]

def escalation(save1, cost=14500, fade=0.01, disc=0.05):
    out = {}
    for esc in (0.03, 0.05, 0.08, 0.12):
        cum = 0; pay = None; npv = -cost
        for y in range(1, 16):
            sv = save1 * ((1 + esc) ** (y - 1)) * ((1 - fade) ** (y - 1)); cum += sv
            if pay is None and cum >= cost: pay = y - 1 + (cost - (cum - sv)) / sv
            if y <= 10: npv += sv / ((1 + disc) ** y)
        out[f"{int(esc*100)}%"] = {"payback": round(pay, 1), "npv10": round(npv)}
    return out

if __name__ == "__main__":
    d = br.load()
    imp0 = d.Consumption.values.astype(float); gen0 = d.Generation.values.astype(float)
    base = billed(d, imp0, gen0)
    out = {"baseline_bill_current_rates": round(base)}
    for cap, name in [(13.5, "pw3"), (27.0, "pw3x")]:
        row = {}
        for pol in ("evening", "twowin", "greedy"):
            i2, e2, served, thru = run_batt(d, imp0, gen0, cap, pol)
            row[pol] = {"save": round(base - billed(d, i2, e2)),
                        "kwh_served": round(served),
                        "cycles_per_day": round(thru / cap / 365, 2)}
        i2, e2, _, _ = run_batt(d, imp0, gen0, cap, "greedy")
        row["greedy_profile_S"] = summer_profile(d, i2)
        f = d.copy(); f["gi"] = i2
        row["onpeak_after_greedy"] = round(f[(f.hour >= 16) & (f.hour < 21)].gi.sum())
        out[name] = row
        print(name, {k: v for k, v in row.items() if isinstance(v, dict)})
    # post-behavior integrated package (EV shift scenario a, then battery)
    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions), sop_idx, sop_ts)
    b_sh = billed(d, imp_sh, gen0)
    pb = {"behavior_save": round(base - b_sh), "kwh_moved": round(moved)}
    for cap, name in [(13.5, "mid"), (27.0, "high")]:
        i3, e3, _, _ = run_batt(d, imp_sh, gen0, cap, "greedy")
        b2 = billed(d, i3, e3)
        pb[name] = {"battery_marginal": round(b_sh - b2),
                    "combined_save": round(base - b2), "bill": round(b2)}
    out["post_behavior"] = pb
    out["escalation_greedy_pw3"] = escalation(out["pw3"]["greedy"]["save"])
    out["notes"] = {"engine": "rates.bill_nem (monthly per-period NEM netting, NBC on gross imports)",
                    "ev_exclusion": ">=2.5 kW outside on-peak = EV spillover, never battery-served",
                    "rte": 0.9, "power_kw": 11.5, "requires": "multi-window time-based control"}
    json.dump(out, open("battery_dispatch_policies.json", "w"), indent=1)
    print("post_behavior:", pb)
    print("escalation:", out["escalation_greedy_pw3"])
