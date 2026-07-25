#!/usr/bin/env python3
"""Canonical rate constants and billing engine — single source of truth.

Provenance: every value below was read off the detailed SDG&E bills
(EV-TOU-5 delivery + CEA "Clean Impact Plus" generation, effective 6/1/2026)
and matches the billed line items to the penny. Import THIS module; do not
re-declare rate constants in analysis scripts. (analyze.py, the legacy
cross-plan ranking model, retains published rate-table values for all plans —
fine for RANKING, not for absolute dollars; see TECHNICAL.md.)

Billing mechanics (NEM 2.0, matching the bills):
  * energy charges netted per (month, season, TOU period): positive net billed
    at UDC+CEA+PCIA; negative net credited at UDC+CEA
  * PCIA is applied to NET kWh — bill evidence: the same statement charges
    "PCIA 2023 224 kWh x rate" on a period with 224 kWh NET usage while charging
    "Wildfire Fund Charge 308 kWh" (gross imports) — so netting PCIA inside the
    energy rate is bill-correct. Exports offset PCIA only within net-positive
    buckets; every month of the audit year was net-positive, so the treatment of
    PCIA in a net-NEGATIVE period is untested against a bill (not determinable
    from available statements; bounded ambiguity only if a month goes negative)
  * non-bypassable charges (NBC) on GROSS imported kWh — never netted
    (bill evidence: wildfire charged on 308 gross kWh vs 224 net)
  * Base Services Charge per day
TOU windows (year-round, post-June-2026): on-peak 16-21 daily;
super-off-peak 0-6 + 10-14 weekdays, 0-14 weekends; off-peak otherwise.
"""
UDC = {"S": {"on": 0.30203, "off": 0.30203, "sop": 0.02606},
       "W": {"on": 0.31174, "off": 0.31174, "sop": 0.02606}}
CEA = {"S": {"on": 0.51684, "off": 0.15975, "sop": 0.04961},
       "W": {"on": 0.24430, "off": 0.15782, "sop": 0.05187}}
NBC = 0.021; PCIA = 0.02828; BSC = 0.79343
SUMMER_MONTHS = {6, 7, 8, 9, 10}

energy = lambda s, p: UDC[s][p] + CEA[s][p] + PCIA      # netted energy rate
credit = lambda s, p: UDC[s][p] + CEA[s][p]             # export credit
allin  = lambda s, p: UDC[s][p] + CEA[s][p] + PCIA + NBC  # marginal gross import

def period(hour_frac, is_weekend):
    if 16 <= hour_frac < 21: return "on"
    if is_weekend: return "sop" if hour_frac < 14 else "off"
    return "sop" if (hour_frac < 6 or 10 <= hour_frac < 14) else "off"

def bill_nem_monthly(frame, imp="Consumption", exp="Generation"):
    """{month: $} via monthly per-period NEM netting + NBC on gross imports + BSC.
    frame needs columns: dt, seas ('S'/'W'), p ('on'/'off'/'sop'), ym, imp, exp."""
    out = {}
    for ym, m in frame.groupby("ym"):
        tot = m.dt.dt.date.nunique() * BSC + m[imp].sum() * NBC
        for s in ("S", "W"):
            for p in ("on", "off", "sop"):
                sub = m[(m.seas == s) & (m.p == p)]
                net = sub[imp].sum() - sub[exp].sum()
                tot += net * (energy(s, p) if net >= 0 else credit(s, p))
        out[str(ym)] = tot
    return out

def bill_nem(frame, imp="Consumption", exp="Generation"):
    """Annual $ (sum of bill_nem_monthly)."""
    return sum(bill_nem_monthly(frame, imp, exp).values())
