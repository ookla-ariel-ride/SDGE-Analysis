#!/usr/bin/env python3
"""Battery dispatch policies — the report's battery economics (integrated engine).

Principle: a stored kWh costs ~8.4c (midday surplus: forgone super-off-peak export
credit / RTE) or ~13.9c (super-off-peak grid top-up / RTE); every import priced above
that is worth serving. Non-super-off-peak imports price at 51-87c, so the price-aware
policy discharges against ALL of them; super-off-peak imports (12.5c) are never served.

Three policies x two configurations (13.5 kWh Powerwall 3 / 27 kWh PW3+Expansion,
both 11.5 kW continuous discharge -- but DIFFERENT continuous charge rates: 5 kW
for the bare 13.5 kWh unit, 8 kW for the 27 kWh with-expansion unit -- Tesla's
own datasheet, see research/battery-research-notes.md -- 90% round-trip split
as sqrt-eta per direction):
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

run_batt() also takes an optional power_kw (default 11.5, matching every config
above and preserving this module's own output byte-for-byte) so it can be reused
as a shared dispatch primitive at other capacities/power caps — battery_sizing_
curve.py (issue #12) sweeps it across an energy and a power grid. Every call
asserts its own energy-conservation identity (final SOC must equal the initial
SOC plus charge throughput minus discharge, net of round-trip loss) and raises
SystemExit if it does not hold — a real invariant, not just a signature change.

power_kw is the DISCHARGE cap (what the unit delivers). run_batt() also takes an
optional charge_kw (issue #40), the CHARGE cap (what the unit draws, from solar
surplus or grid top-up), defaulting to reuse power_kw when not given so every
existing call site is byte-for-byte unchanged. Tesla's own official 2025
Powerwall 3 Datasheet gives these as genuinely different figures for a single
unit with no expansions — 11.5 kW continuous discharge vs. 5 kW continuous
charge (canonical URL:
https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/Datasheet/en-us/Powerwall-3-Datasheet.pdf,
retrieved 2026-08-03 via a third-party mirror; see research/battery-research-
notes.md for the full citation) — so a caller that wants the asymmetric,
datasheet-accurate limit passes charge_kw=5.0 explicitly; nothing here assumes
that value by default.

The unconditional discharge-window clause reads p[i] == "on" (the TOU period
column ANY caller's frame already carries), not a hardcoded clock-hour test
(issue #14 adversarial review, first pass): an earlier version tested
16 <= h[i] < 21 directly, which happens to equal p[i] == "on" for every frame
built from rates.period_at() (rates.py itself defines "on" as exactly that
window, unconditionally, before its own weekday/weekend branching) but silently
stopped tracking on-peak the moment a caller supplied a frame whose p column
encoded a DIFFERENT on-peak window -- exactly what tou_structure_stress.py
(issue #14) does to stress-test alternate tariff structures. Reading p[i]
directly is a no-op for every existing caller (verified: this module's own
committed artifact, and every downstream artifact that reuses run_batt --
battery_sizing_curve.json, battery_plan_matrix.json, perfect_foresight_
dispatch.json's greedy comparison, package_results.json, extended_results.json
-- all regenerate byte-identically) and makes the function genuinely portable
to a caller's own TOU structure, which its own module docstring already claimed
before this fix made the claim true.
"""
import numpy as np, pandas as pd, json
import behavior_rebuild as br
import rates as R

ETA = np.sqrt(0.90); PWRQ = 11.5 / 4

# Real, cited Maximum Continuous Charge Power for a BARE single Powerwall 3
# unit, no expansions (issue #40) -- 5 kW AC, vs. the 11.5 kW continuous
# DISCHARGE rating (PWRQ above), which is the SAME for bare and
# with-expansion configurations (discharge is not re-rated by adding
# expansion capacity). Tesla's own official 2025 Powerwall 3 Datasheet,
# canonical URL
# https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/Datasheet/en-us/Powerwall-3-Datasheet.pdf
# (retrieved 2026-08-03 via a third-party mirror; see research/battery-
# research-notes.md for the full citation). Applies ONLY to the base 13.5
# kWh unit with NO expansion packs -- see CHARGE_KW_WITH_EXPANSION
# immediately below for the 27 kWh (PW3 + 1 Expansion) configuration, which
# the SAME datasheet gives a materially higher, separately cited charge
# rating for (corrected: an earlier version of this constant incorrectly
# claimed the expansion pack "shares the base unit's inverter" and applied
# this bare-unit figure to the expansion configuration too, contradicting
# the very datasheet split research/battery-research-notes.md already
# recorded -- Codex adversarial review caught this). Used below as the
# production default for every run_batt() call that models this
# household's real bare-unit Powerwall 3 hardware; run_batt() itself keeps
# charge_kw=None as its own general-purpose default so it stays usable as a
# reusable primitive at other, uncited capacities/powers (battery_sizing_
# curve.py's sweep).
CHARGE_KW = 5.0

# Real, cited Maximum Continuous Charge Power for a Powerwall 3 WITH UP TO 3
# EXPANSION UNITS (issue #40 correction) -- 8 kW AC, 33.3 A, from the SAME
# Tesla datasheet cited above (see research/battery-research-notes.md: "PW3
# with up to 3 Expansion units, 33.3 A / 8 kW"). This household's 27 kWh
# "PW3 + 1 Expansion" configuration has one expansion pack, which is within
# this "up to 3" bracket, so it takes this figure, NOT the bare-unit
# CHARGE_KW above. The discharge rating (11.5 kW) is unchanged by adding
# expansion capacity, so only the charge constant differs by configuration.
CHARGE_KW_WITH_EXPANSION = 8.0

def run_batt(d, imp0, gen0, cap, policy, power_kw=11.5, charge_kw=None, soc0=None):
    """power_kw is the discharge cap; charge_kw is the charge cap (solar-surplus
    and grid-top-up charging both use it), defaulting to power_kw so a caller
    that does not pass charge_kw gets the prior symmetric behavior byte-for-
    byte. See the module docstring for the datasheet citation behind the real
    asymmetric figures (11.5 kW discharge / 5 kW charge, single unit)."""
    imp = imp0.copy(); exp = gen0.copy()
    pwrq_dis = power_kw / 4
    pwrq_chg = (power_kw if charge_kw is None else charge_kw) / 4
    soc0 = cap / 2 if soc0 is None else soc0
    soc = soc0; served = 0.0; thru = 0.0
    p = d.p.values; h = d.hour.values; kw = imp0 * 4
    for i in range(len(d)):
        disch_win = (p[i] == "on") or \
                    (policy == "twowin" and 6 <= h[i] < 9 and kw[i] < 2.5) or \
                    (policy == "greedy" and p[i] != "sop" and kw[i] < 2.5)
        if exp[i] > 0 and not (disch_win and imp[i] > 0):
            # charge from surplus — unless this interval also has import inside a
            # discharge window (6.3% of intervals carry both flows; serving a
            # 51-87c import beats storing surplus worth ~8c)
            c = min(exp[i], (cap - soc) / ETA, pwrq_chg)
            if c > 0: soc += c * ETA; exp[i] -= c; thru += c * ETA
            continue
        if p[i] == "sop":
            grid_ok = (policy == "greedy") or (h[i] < 6)
            lim = cap if policy == "greedy" else 0.6 * cap
            take = min(max((lim - soc) / ETA, 0), pwrq_chg) if grid_ok else 0
            if take > 0: soc += take * ETA; imp[i] += take; thru += take * ETA
            continue
        if disch_win:
            dd = min(imp[i], soc * ETA, pwrq_dis)
            if dd > 0: soc -= dd / ETA; imp[i] -= dd; served += dd
    # energy-conservation identity (CLAUDE.md 1b): every joule leaving the pack
    # equals a joule that entered it, net of round-trip loss — soc0 + thru
    # (AC-in, already at pack-side value) - served/ETA (AC-out, back to pack-side)
    # must equal the final soc to the float epsilon.
    if abs(soc - (soc0 + thru - served / ETA)) > 1e-6:
        raise SystemExit("run_batt: energy-conservation identity failed — "
                          "soc drifted from the charge/discharge throughput")
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
    # Serviceable-load inputs behind the report's §6 sentence. Period assignment is
    # rates.period_at, which applies the eight tariff holidays that tou_audit.py
    # confirmed against the bills, so this now agrees with the holiday-as-weekend
    # convention the legacy ranking model has always used. The two bases used to
    # disagree by ~40 kWh of off-peak import; that gap is closed.
    _p = d.p.values; _kw = imp0 * 4
    inp = {"nonsop_import_kwh": round(float(imp0[_p != "sop"].sum())),
           "onpeak_import_kwh": round(float(imp0[_p == "on"].sum())),
           "servable_offpeak_house_kwh": round(float(imp0[(_p == "off") & (_kw < 2.5)].sum()))}
    inp["serviceable_total_kwh"] = inp["onpeak_import_kwh"] + inp["servable_offpeak_house_kwh"]
    inp["note"] = ("canonical rates.period_at assignment, including the eight "
                   "bill-confirmed tariff holidays as weekend days")
    out["inputs"] = inp
    # charge_kw is BARE-UNIT 5 kW for the 13.5 kWh "pw3" config, WITH-EXPANSION
    # 8 kW for the 27 kWh "pw3x" (PW3 + 1 Expansion) config -- issue #40
    # correction: these are genuinely different, cited datasheet figures, not
    # one number applied uniformly across configurations.
    for cap, name, chg_kw in [(13.5, "pw3", CHARGE_KW), (27.0, "pw3x", CHARGE_KW_WITH_EXPANSION)]:
        row = {}
        for pol in ("evening", "twowin", "greedy"):
            i2, e2, served, thru = run_batt(d, imp0, gen0, cap, pol, charge_kw=chg_kw)
            row[pol] = {"save": round(base - billed(d, i2, e2)),
                        "kwh_served": round(served),
                        "cycles_per_day": round(thru / cap / 365, 2)}
        i2, e2, _, _ = run_batt(d, imp0, gen0, cap, "greedy", charge_kw=chg_kw)
        row["greedy_profile_S"] = summer_profile(d, i2)
        f = d.copy(); f["gi"] = i2
        row["onpeak_after_greedy"] = round(f[(f.hour >= 16) & (f.hour < 21)].gi.sum())
        row["charge_kw"] = chg_kw
        out[name] = row
        print(name, {k: v for k, v in row.items() if isinstance(v, dict)})
    # post-behavior integrated package (EV shift scenario a, then battery)
    ev, sessions = br.detect_sessions(d)
    sop_idx, sop_ts = br.build_sop_index(d)
    imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions), sop_idx, sop_ts)
    b_sh = billed(d, imp_sh, gen0)
    pb = {"behavior_save": round(base - b_sh), "kwh_moved": round(moved)}
    for cap, name, chg_kw in [(13.5, "mid", CHARGE_KW), (27.0, "high", CHARGE_KW_WITH_EXPANSION)]:
        i3, e3, _, _ = run_batt(d, imp_sh, gen0, cap, "greedy", charge_kw=chg_kw)
        b2 = billed(d, i3, e3)
        pb[name] = {"battery_marginal": round(b_sh - b2),
                    "combined_save": round(base - b2), "bill": round(b2),
                    "charge_kw": chg_kw}
    out["post_behavior"] = pb
    out["escalation_greedy_pw3_post_behavior"] = escalation(pb["mid"]["battery_marginal"])
    out["escalation_note"] = "seeded from the post-EV-fix battery marginal (the decision-relevant figure)"
    out["notes"] = {"engine": "rates.bill_nem (monthly per-period NEM netting, NBC on gross imports)",
                    "ev_exclusion": ">=2.5 kW outside on-peak = EV spillover, never battery-served",
                    "rte": 0.9, "power_kw": 11.5,
                    "charge_kw_bare_unit": CHARGE_KW,
                    "charge_kw_with_expansion": CHARGE_KW_WITH_EXPANSION,
                    "charge_kw_note": ("13.5 kWh pw3/mid configs use the bare-unit charge "
                                       "rate; 27 kWh pw3x/high configs use the with-"
                                       "expansion rate -- see CHARGE_KW/"
                                       "CHARGE_KW_WITH_EXPANSION in this module"),
                    "requires": "multi-window time-based control"}
    json.dump(out, open("battery_dispatch_policies.json", "w"), indent=1)
    print("post_behavior:", pb)
    print("escalation:", out["escalation_greedy_pw3_post_behavior"])
