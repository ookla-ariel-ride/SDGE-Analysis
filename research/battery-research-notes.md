# Battery research notes (captured Jul 24, 2026)

## Products & typical installed prices (2026, pre-quote estimates)

| Product | Usable kWh | Continuous power | Warranty | Est. installed |
|---|---|---|---|---|
| Enphase IQ Battery 5P | 5.0 | 3.84 kW (7.68 peak) | 15 yr / 4,000 cycles | ~$8,500 |
| Enphase IQ Battery 10C | 10.0 | 7.08 kW (10.5 peak) | 15 yr / 6,000 cycles | ~$13,000 |
| Tesla Powerwall 3 | 13.5 | 11.5 kW | 10 yr | ~$13,000–16,500 (unit ~$9.3–10.5k) |
| PW3 Expansion Pack | +13.5 | (shares PW3 inverter) | 10 yr | ~$5,900 |

Multi-unit notes: second PW3 incremental ~$7–9k; Tesla applies install-efficiency
discounts on multi-unit systems. PW3 supports up to 3 expansions (54 kWh max).
PW3 AC-couples alongside existing IQ7X microinverters; Enphase batteries need the
IQ System Controller for backup islanding.

## Incentives status (as of Jul 2026)
- Federal 25D residential 30% credit: **expired Dec 31, 2025** (no ITC for residential
  battery retrofits in 2026).
- CA SGIP general-market (~$200/kWh ≈ $2,700 on 13.5 kWh): **waitlisted 12–18 months**;
  join the waitlist anyway. RSSE (income-qualified) also waitlisted.
- NEM 2.0: adding storage does NOT affect grandfathering (20 yr from PTO 12/27/2019 →
  ~Dec 2039). Solar-charged storage retains retail export credits.

## Simulation results (see 3-analysis/battery_sim.json, backup_endurance.json)
Arbitrage (per-interval, 90% RTE, charge from would-be exports + SOP grid top-up,
discharge on-peak): 5P $779/yr · 10C $1,401 · PW3 $1,669 · 3×5P $1,737 ·
2×10C $1,889 · PW3+Exp $2,032.
Simple paybacks: PW3 ~8.7 yr best; 10C ~9.3 yr; others 10–13 yr.

Backup endurance (hourly sim, 6pm outage start, solar recharge, 14-day cap):
- Tier 1 essentials (~17 kWh/day @0.7 kW): PW3 median 14 d (p10 89 h); 10C 36 h (13 h)
- Tier 2 house minus EV (~46 kWh/day): PW3 7 h (3 h); PW3+Exp 30 h (9 h)
- Tier 3 incl. EV (~82 kWh/day): not practical; charge EV midday from array in outages.

Recommendation recorded in report §6: 1× PW3 (or IQ 10C for ecosystem/warranty);
expansion pack only if untrimmed Tier-2 overnight backup is required; do the free
load-shifting first — it competes for the same on-peak kWh.

## Sources
- solarreviews.com (PW3 cost guide 2026), nuwattenergy.com (IQ 5P/10C, PW3 reviews),
  smartenergyusa.com (PW3 pricing), energyscout.org & solarwithwatts.com (SGIP 2026),
  exspenditure.com (NEM 2.0 + battery rules), teslamotorsclub/diysolarforum threads
  (AC-coupling with existing Enphase).
