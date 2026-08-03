# Battery research notes (captured Jul 24, 2026; Powerwall 3 charge/discharge split added Aug 3, 2026 — issue #40)

## Products & typical installed prices (2026, pre-quote estimates)

| Product | Usable kWh | Continuous power (discharge) | Continuous power (charge) | Warranty | Est. installed |
|---|---|---|---|---|---|
| Enphase IQ Battery 5P | 5.0 | 3.84 kW (7.68 peak) | not recorded | 15 yr / 4,000 cycles | ~$8,500 |
| Enphase IQ Battery 10C | 10.0 | 7.08 kW (10.5 peak) | not recorded | 15 yr / 6,000 cycles | ~$13,000 |
| Tesla Powerwall 3 | 13.5 | 11.5 kW | 5 kW | 10 yr | ~$13,000–16,500 (unit ~$9.3–10.5k) |
| PW3 Expansion Pack | +13.5 | (shares PW3 inverter) | (shares PW3 inverter) | 10 yr | ~$5,900 |

Multi-unit notes: second PW3 incremental ~$7–9k; Tesla applies install-efficiency
discounts on multi-unit systems. PW3 supports up to 3 expansions (54 kWh max).
PW3 AC-couples alongside existing IQ7X microinverters; Enphase batteries need the
IQ System Controller for backup islanding.

### Powerwall 3 charge vs. discharge power (issue #40)

Tesla's own official 2025 Powerwall 3 Datasheet gives four DIFFERENT continuous
power ratings depending on direction and configuration. This project models one
bare Powerwall 3, on-grid, no expansion units — the row marked "modeled
configuration" below is the one every script in this repo should use.

| Rating | Value | Applies to this household's modeled configuration? |
|---|---|---|
| Maximum Continuous Discharge Power (Nominal Output Power AC), on-grid | 11.5 kW (top of 4 configurable levels: 5.8 / 7.6 / 10 / 11.5 kW), 48 A max continuous current | **Yes — modeled configuration.** This is the figure the repo's existing "11.5 kW" already correctly represents, for DISCHARGE. |
| Maximum Continuous Charge Power, Powerwall 3 only (single unit, no expansions) | **5 kW AC, 20.8 A** | **Yes — modeled configuration.** Previously uncited in this repo; this is the datum issue #40 asked for. |
| Maximum Continuous Discharge Power Off-Grid (PV only, -20°C to 25°C) | 15.4 kW | No. Gated on the on-grid rating being set to 11.5 kW plus an 80 A breaker and sized conductors; recorded here because it exists in the datasheet, not modeled. |
| Maximum Continuous Charge Power, Powerwall 3 with up to 3 Expansion units | 8 kW AC, 33.3 A | No. This household's recommendation is one bare unit with no expansions; recorded here because it exists in the datasheet, not modeled. |

Confirmed asymmetry: discharge continuous max 11.5 kW vs. charge continuous max
5 kW for a single unit — a real, ~2.3x difference.

## Incentives status (as of Jul 2026)
- Federal 25D residential 30% credit: **expired Dec 31, 2025** (no ITC for residential
  battery retrofits in 2026).
- CA SGIP general-market (~$200/kWh ≈ $2,700 on 13.5 kWh): **waitlisted 12–18 months**;
  join the waitlist anyway. RSSE (income-qualified) also waitlisted.
- NEM 2.0: adding storage does NOT affect grandfathering (20 yr from PTO 12/27/2019 →
  ~Dec 2039). Solar-charged storage retains retail export credits.

## Simulation results (see `data/battery_sim.json`, `data/backup_endurance.json`)
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
- Tesla's own official 2025 Powerwall 3 Datasheet (charge/discharge power split,
  issue #40): canonical URL
  https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/Datasheet/en-us/Powerwall-3-Datasheet.pdf
  — Tesla's own energylibrary.tesla.com domain blocks automated fetches, so this
  was retrieved 2026-08-03 via an identical third-party-hosted mirror at
  longhornsolar.com (https://longhornsolar.com/wp-content/uploads/2025/10/Powerwall-3-Datasheet.pdf),
  a copy of the same 2025-dated Tesla document. Exact table rows, page 2,
  "Powerwall 3 Technical Specifications" -> "System Technical Specifications":
  Nominal Output Power (AC) 5.8/7.6/10/11.5 kW at 24/31.7/41.7/48 A continuous;
  Maximum Continuous Discharge Power Off-Grid (PV only, -20C to 25C) 15.4 kW,
  gated on the 11.5 kW on-grid setting plus an 80 A breaker; Maximum Continuous
  Charge Current/Power, Powerwall 3 only, 20.8 A / 5 kW; Maximum Continuous
  Charge Current/Power, Powerwall 3 with up to 3 Expansion units, 33.3 A / 8 kW.
