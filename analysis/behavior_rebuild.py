#!/usr/bin/env python3
"""Session-based behavior-savings rebuild (replaces the crude "cap at 2.5 kW" shift).

What the old approach did wrong: it clipped every 15-min import above 2.5 kW and repriced
the clipped energy at a year-average super-off-peak rate. >60% of what it moved was house
load (HVAC/cooking) that is not obviously movable, and it never physically placed the
energy, so destination-period costs and monthly NEM netting were ignored.

This model:
  1. Detects EV charging sessions explicitly:
       - baseline = centered rolling 24 h (96-interval) 20th-percentile of import power
         (tracks the always-on house floor, immune to multi-hour charge blocks);
       - candidate intervals: import power >= baseline + 2.5 kW;
       - a session = a contiguous candidate run lasting >= 30 min whose PEAK excess is
         >= 8 kW (this EV charges at ~11.5 kW; nothing else in the house sustains 8 kW);
       - EV energy per interval = clip(excess, 0, 11.5 kW) * 0.25 h, capped at the
         interval's actual import.
  2. Scenario ladder: physically REMOVES shifted kWh from source intervals and ADDS them
     to super-off-peak intervals starting at the next midnight (overnight 0-6 window,
     spilling into later SOP windows if full), honoring an 11.5 kW charger cap net of any
     EV charging already present in the destination interval.
  3. Re-bills the modified year with the bill-validated monthly per-TOU-period NEM netting
     model (rates read off actual bills, EV-TOU-5 + CEA Clean Impact Plus, 6/1/2026).
  4. Re-runs the battery simulation ON TOP of scenario (a) so behavior and battery savings
     are not double-counted (13.5 kWh usable, 11.5 kW, charge from would-be exports outside
     on-peak then overnight SOP top-up, discharge on-peak, 90% round-trip efficiency).

Absolute model dollars run high vs the audited bills ($3,282/yr actual over these 365 days);
use the DELTAS, which are driven by correctly-priced on-peak arbitrage.
"""
import json
import datetime as dt

import numpy as np
import pandas as pd

import household as hh

CSV = "usage.csv"  # SDG&E Green Button 15-min (skiprows=13)

# ---- rates: identical to billing_model_nem.py (actual bills, 6/1/2026) ----
import rates as R                                          # canonical module
from rates import UDC, CEA, NBC, PCIA, BSC, energy, credit  # canonical bill-derived rates
retail = energy  # netted energy rate (NBC applied on gross imports in bill_monthly)

# ---- detection / shifting parameters ----
BASE_WIN = 96          # rolling-baseline window (24 h of 15-min intervals)
BASE_Q = 0.20          # baseline percentile
EXCESS_KW = 2.5        # candidate threshold above baseline
PEAK_KW = 8.0          # session must peak >= this above baseline (EV signature)
MIN_INTERVALS = 2      # >= 30 min sustained
# destination charger power cap — per-house hardware, from private/household.yaml
# (analysis/household.py; fails closed — run the intake interview in
# DATA-SOURCES-CHEATSHEET.md)
CHARGER_KW = float(hh.get("charger.kw"))
CAP_KWH = CHARGER_KW * 0.25   # max EV kWh per 15-min interval

# ---- battery parameters (Powerwall 3 hardware spec, NOT household config) ----
BATT_KWH = 13.5        # usable
BATT_KW = 11.5
BATT_STEP = BATT_KW * 0.25
ETA = np.sqrt(0.90)    # one-way efficiency (90% round-trip)


def load():
    df = pd.read_csv(CSV, skiprows=13)
    df.columns = [c.strip() for c in df.columns]
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"],
                              format="%m/%d/%Y %I:%M %p")
    for c in ["Consumption", "Generation"]:
        df[c] = pd.to_numeric(df[c])
    end = dt.datetime(2026, 7, 24)
    df = df[(df.dt >= end - dt.timedelta(days=365)) & (df.dt < end)]
    df = df.sort_values("dt").reset_index(drop=True)
    df["hour"] = df.dt.dt.hour + df.dt.dt.minute / 60
    # Weekend windows also apply on the eight tariff holidays; rates.off_peak_day
    # is the single source of that rule (confirmed against the bills, see
    # analysis/tou_audit.py). A bare weekday test silently drops it.
    df["wkend"] = df.dt.dt.date.map(R.off_peak_day)
    df["seas"] = np.where(df.dt.dt.month.isin([6, 7, 8, 9, 10]), "S", "W")
    df["ym"] = df.dt.dt.to_period("M")

    # TOU assignment comes from the canonical module, not a local copy of the rule.
    df["p"] = [R.period_at(t) for t in df.dt]
    return df


from rates import bill_nem_monthly as _bnm

def bill_monthly(frame, imp="imp", exp="exp"):
    """Monthly per-TOU-period NEM netting -> {month: $} (canonical engine)."""
    return _bnm(frame, imp, exp)


def bill(frame, imp="imp", exp="exp"):
    return sum(bill_monthly(frame, imp, exp).values())


# ------------------------------------------------------------------ detection
def detect_sessions(d):
    kW = d.Consumption.values * 4.0
    base = pd.Series(kW).rolling(BASE_WIN, center=True, min_periods=24) \
                        .quantile(BASE_Q).values
    exc = kW - base
    cand = exc >= EXCESS_KW
    ev = np.zeros(len(d))
    sessions = []
    i, n = 0, len(d)
    while i < n:
        if not cand[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and cand[j + 1]:
            j += 1
        if (j - i + 1) >= MIN_INTERVALS and exc[i:j + 1].max() >= PEAK_KW:
            e = np.clip(exc[i:j + 1], 0, CHARGER_KW) * 0.25
            e = np.minimum(e, d.Consumption.values[i:j + 1])
            ev[i:j + 1] = e
            sessions.append((i, j, e.sum()))
        i = j + 1
    return ev, sessions


# ------------------------------------------------------------------ shifting
def build_sop_index(d):
    """Chronological array of SOP interval indices + their timestamps."""
    idx = np.where(d.p.values == "sop")[0]
    return idx, d.dt.values[idx]


def place_energy(add, amount, start_ts, sop_idx, sop_ts, headroom_base):
    """Pour `amount` kWh into SOP intervals at/after start_ts, respecting the
    11.5 kW charger cap net of EV charging already present. Mutates `add`.
    Returns unplaced remainder (0 unless we run off the end of the data; then
    we retry from the latest available overnight window)."""
    k = np.searchsorted(sop_ts, np.datetime64(start_ts))
    for q in range(k, len(sop_idx)):
        if amount <= 1e-9:
            return 0.0
        i = sop_idx[q]
        room = CAP_KWH - headroom_base[i] - add[i]
        if room > 1e-9:
            put = min(room, amount)
            add[i] += put
            amount -= put
    if amount > 1e-9:  # sessions near the end of the data window: fill backward
        for q in range(len(sop_idx) - 1, -1, -1):
            if amount <= 1e-9:
                break
            i = sop_idx[q]
            room = CAP_KWH - headroom_base[i] - add[i]
            if room > 1e-9:
                put = min(room, amount)
                add[i] += put
                amount -= put
    return amount


def shift_ev(d, ev, sessions, session_mask, sop_idx, sop_ts):
    """Move on-peak + off-peak EV energy of selected sessions to SOP starting
    at the next midnight. Returns (new_imp, kwh_moved)."""
    imp = d.Consumption.values.astype(float).copy()
    add = np.zeros(len(d))
    p = d.p.values
    moved = 0.0
    for sel, (a, b, _tot) in zip(session_mask, sessions):
        if not sel:
            continue
        sl = slice(a, b + 1)
        movable = np.where(np.isin(p[sl], ["on", "off"]), ev[sl], 0.0)
        amt = movable.sum()
        if amt <= 0:
            continue
        imp[sl] -= movable
        start = (pd.Timestamp(d.dt.values[a]).normalize() + pd.Timedelta(days=1))
        left = place_energy(add, amt, start, sop_idx, sop_ts, ev)
        moved += amt - left
    return imp + add, moved


def shift_house(d, imp_in, ev, frac, sop_idx, sop_ts):
    """Move `frac` of remaining (non-EV) on-peak import, per day, to the next
    overnight SOP window (spread by the same fill rule)."""
    imp = imp_in.copy()
    add = np.zeros(len(d))
    p = d.p.values
    onpk = (p == "on")
    take = np.where(onpk, np.clip(imp - ev, 0, None) * frac, 0.0)
    # note: imp_in already has EV session energy removed in scenarios c/d, and
    # `ev` is zeroed at moved intervals before calling (see below), so `take`
    # never double-taxes EV energy.
    moved = 0.0
    days = pd.Series(d.dt.dt.normalize())
    for day, grp in pd.DataFrame({"day": days, "i": np.arange(len(d))}).groupby("day"):
        ii = grp.i.values
        amt = take[ii].sum()
        if amt <= 1e-9:
            continue
        imp[ii] -= take[ii]
        left = place_energy(add, amt, day + pd.Timedelta(days=1), sop_idx, sop_ts, ev)
        moved += amt - left
    return imp + add, moved


# ------------------------------------------------------------------ battery
def battery_sim(d, imp_in, exp_in):
    """13.5 kWh / 11.5 kW, 90% RTE. Charge from would-be exports outside on-peak,
    top up from grid during the overnight (0-6) SOP window, discharge on-peak."""
    imp = imp_in.copy(); exp = exp_in.copy()
    p = d.p.values; hour = d.hour.values
    soc = 0.0
    for i in range(len(d)):
        step = BATT_STEP
        if p[i] == "on":
            out = min(imp[i], step, soc * ETA)
            imp[i] -= out
            soc -= out / ETA
        else:
            if exp[i] > 0 and soc < BATT_KWH:
                ch = min(exp[i], step, (BATT_KWH - soc) / ETA)
                exp[i] -= ch
                soc += ch * ETA
                step -= ch
            if p[i] == "sop" and hour[i] < 6 and soc < BATT_KWH and step > 0:
                ch = min(step, (BATT_KWH - soc) / ETA)
                imp[i] += ch
                soc += ch * ETA
    return imp, exp


# ------------------------------------------------------------------ main
def main():
    d = load()
    d["imp"] = d.Consumption.astype(float)
    d["exp"] = d.Generation.astype(float)
    base_monthly = bill_monthly(d)
    base_bill = sum(base_monthly.values())

    ev, sessions = detect_sessions(d)
    d["ev"] = ev
    p = d.p.values
    ev_tot = ev.sum()
    ev_on = ev[p == "on"].sum()
    ev_off = ev[p == "off"].sum()
    ev_sop = ev[p == "sop"].sum()
    sop_idx, sop_ts = build_sop_index(d)

    def run(imp_new, label):
        f = d.copy()
        f["imp"] = imp_new
        monthly = bill_monthly(f)
        tot = sum(monthly.values())
        return dict(label=label,
                    bill=round(tot, 2),
                    saved=round(base_bill - tot, 2),
                    month_min=round(min(monthly.values()), 2),
                    month_max=round(max(monthly.values()), 2))

    results = {"window": {"start": str(d.dt.min()), "end": str(d.dt.max()),
                          "days": int(d.dt.dt.date.nunique())},
               "baseline": {"model_bill": round(base_bill, 2),
                            "actual_billed": 3282,
                            "month_min": round(min(base_monthly.values()), 2),
                            "month_max": round(max(base_monthly.values()), 2),
                            "imports_kwh": round(float(d.imp.sum()), 1),
                            "exports_kwh": round(float(d.exp.sum()), 1),
                            "onpeak_import_kwh": round(float(d.imp[p == "on"].sum()), 1)},
               "detection": {"rule": ("power >= rolling-24h-20th-pct baseline + 2.5 kW, "
                                      ">=30 min contiguous, session peak excess >= 8 kW; "
                                      "EV kWh = clip(excess,0,11.5kW)*0.25h, <= interval import"),
                             "sessions": len(sessions),
                             "ev_kwh_total": round(float(ev_tot), 1),
                             "ev_kwh_expected": 13100,
                             "ev_kwh_onpeak": round(float(ev_on), 1),
                             "ev_kwh_offpeak": round(float(ev_off), 1),
                             "ev_kwh_sop_already": round(float(ev_sop), 1),
                             "avg_session_kwh": round(float(ev_tot) / max(len(sessions), 1), 1)},
               "scenarios": {}}

    all_mask = [True] * len(sessions)
    rng = np.random.default_rng(42)
    mask80 = [bool(rng.random() < 0.80) for _ in sessions]

    # (a) EV-only, 100 %
    impA, movedA = shift_ev(d, ev, sessions, all_mask, sop_idx, sop_ts)
    a = run(impA, "a: EV-only, 100% compliance"); a["kwh_moved"] = round(movedA, 1)
    results["scenarios"]["a"] = a

    # (b) EV-only, 80 %
    impB, movedB = shift_ev(d, ev, sessions, mask80, sop_idx, sop_ts)
    b = run(impB, "b: EV-only, 80% compliance (seeded)")
    b["kwh_moved"] = round(movedB, 1)
    b["sessions_moved"] = int(sum(mask80))
    results["scenarios"]["b"] = b

    # remaining-EV map after scenario (a) moves: EV left only in SOP intervals
    ev_left = np.where(np.isin(p, ["on", "off"]), 0.0, ev)

    # (c) EV 100 % + 25 % of remaining on-peak non-EV load
    impC, movedC_house = shift_house(d, impA, ev_left, 0.25, sop_idx, sop_ts)
    c = run(impC, "c: EV + 25% flexible house load")
    c["kwh_moved"] = round(movedA + movedC_house, 1)
    c["house_kwh_moved"] = round(movedC_house, 1)
    results["scenarios"]["c"] = c

    # (d) stretch: EV 100 % + 50 % of remaining on-peak non-EV load
    impD, movedD_house = shift_house(d, impA, ev_left, 0.50, sop_idx, sop_ts)
    sd = run(impD, "d: stretch - EV + 50% house load")
    sd["kwh_moved"] = round(movedA + movedD_house, 1)
    sd["house_kwh_moved"] = round(movedD_house, 1)
    results["scenarios"]["d"] = sd

    # battery marginal: on baseline (for reference) and after scenario (a)
    bi, be = battery_sim(d, d.imp.values.copy(), d.exp.values.copy())
    f = d.copy(); f["imp"], f["exp"] = bi, be
    batt_alone = base_bill - bill(f)

    bi2, be2 = battery_sim(d, impA, d.exp.values.copy())
    f2 = d.copy(); f2["imp"], f2["exp"] = bi2, be2
    batt_after_a = a["bill"] - bill(f2)

    results["battery"] = {
        "spec": "13.5 kWh usable, 11.5 kW, 90% RTE, export-charge + overnight SOP top-up, on-peak discharge",
        "marginal_on_baseline": round(batt_alone, 2),
        "marginal_after_scenario_a": round(batt_after_a, 2),
        "double_count_avoided": round(batt_alone - batt_after_a, 2)}

    results["note"] = ("Model absolute bills run high vs audited $3,282/yr; report DELTAS. "
                       "Old crude 2.5 kW-cap method claimed ~$1,330/yr; see scenarios for "
                       "honest session-based range.")

    with open("behavior_rebuild.json", "w") as fh:
        json.dump(results, fh, indent=1)

    print("model baseline: $%.0f/yr (actual billed $3,282; use deltas)" % base_bill)
    print("EV sessions: %d, %.0f kWh/yr (expected ~13,100) | on-peak %.0f, off-peak %.0f, already-SOP %.0f"
          % (len(sessions), ev_tot, ev_on, ev_off, ev_sop))
    print("%-38s %10s %10s %12s %12s" % ("scenario", "kWh moved", "$ saved/yr", "mo bill min", "mo bill max"))
    for k in "abcd":
        s = results["scenarios"][k]
        print("%-38s %10.0f %10.0f %12.0f %12.0f"
              % (s["label"], s["kwh_moved"], s["saved"], s["month_min"], s["month_max"]))
    print("battery marginal: $%.0f/yr on baseline, $%.0f/yr after scenario (a) [double-count avoided: $%.0f]"
          % (batt_alone, batt_after_a, batt_alone - batt_after_a))


if __name__ == "__main__":
    main()
