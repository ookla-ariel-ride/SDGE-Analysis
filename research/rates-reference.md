# Rate data used in the SDGE plan analysis (captured July 24, 2026)

All figures are $/kWh unless noted. These were transcribed from official sources on the
capture date; utilities revise rates at least annually (SDGE typically Jan 1 / Jun 1,
CEA Feb 1 / Jun 1). Re-verify before reusing.

## Sources
- SDGE Total Rates Tables eff. 6/1/2026 (and 1/1/2026 where noted):
  `https://www.sdge.com/sites/default/files/regulatory/6-1-26 Schedule <PLAN> Total Rates Table.pdf`
  (PLAN ∈ TOU-DR1, DR, "EV-TOU & EV-TOU-2"; EV-TOU-5 from 1-1-26 table with 6-1-26 UDC deltas)
- CEA Adopted Residential Rates eff. 6/1/2026: thecleanenergyalliance.org → Residential Rates
- SDGE–CEA Joint Rate Comparison 2/1/2026:
  thecleanenergyalliance.org/wp-content/uploads/2026/04/CEA_SDGE-JRC-02.01.2026_Final.pdf
- Baseline allowances: SDGE Schedule DR, Special Condition 3
- TOU windows: sdge.com/residential/pricing-plans (post-May 2026 definitions)

## TOU windows (all 3-period plans, post-May 2026; CEA aligned April 2026)
- On-peak: 4–9 p.m. every day, year-round
- Super off-peak: weekdays midnight–6 a.m. AND 10 a.m.–2 p.m.; weekends/holidays midnight–2 p.m.
- Off-peak: all other hours
- Seasons: Summer = Jun 1–Oct 31; Winter = Nov 1–May 31
- TOU-DR2: two periods only (on-peak 4–9 p.m., off-peak otherwise)

## SDGE UDC (delivery) totals, eff. 6/1/2026
| Plan | Season | On | Off | Super-Off |
|---|---|---|---|---|
| EV-TOU-5 | both | 0.31711 | 0.31711 | 0.04114 |
| EV-TOU-2 | both | 0.30372 | 0.30372 | 0.16275 |
| TOU-DR1 / TOU-DR-P | both | 0.32948 | 0.32948 | 0.32948 |
| TOU-DR2 | S / W | 0.33396 / 0.32948 | 0.32750 / 0.32948 | — |
| TOU-ELEC | both | 0.25317 | 0.25317 | 0.25317 |

(1/1/2026 EV-TOU-5 UDC for reference: 0.32322 on/off, 0.03676 SOP.)

## SDGE bundled generation (EECC), eff. 6/1/2026
| Plan | Summer on/off/sop | Winter on/off/sop |
|---|---|---|
| EV-TOU-5, EV-TOU-2 | 0.47019 / 0.17311 / 0.08147 | 0.19990 / 0.14337 / 0.07410 |
| TOU-DR1 | 0.34920 / 0.12853 / 0.04121 | 0.27475 / 0.19304 / 0.10228 |
| TOU-DR2 | 0.34920 / 0.08432 / — | 0.27475 / 0.13777 / — |
| TOU-DR-P | 0.19848 / 0.15523 / 0.08247 | 0.25057 / 0.17606 / 0.09329 |
| TOU-ELEC | 0.45690 / 0.12945 / 0.08637 | 0.24311 / 0.11774 / 0.07856 |

## CEA generation (Clean Impact), eff. 6/1/2026
| Plan (CEA maps EV/ELEC plans together) | Summer on/off/sop | Winter on/off/sop |
|---|---|---|
| EV-TOU-5 / EV-TOU-2 / TOU-ELEC | 0.51684 / 0.15975 / 0.04961 | 0.24430 / 0.15782 / 0.05187 |
| TOU-DR1 | 0.55397 / 0.22298 / 0.04914 | 0.19791 / 0.08433 / 0.05138 |
| TOU-DR2 | 0.53685 / 0.14663 / — | 0.19180 / 0.06703 / — |
| TOU-DR-PK (≈TOU-DR-P) | 0.38778 / 0.15609 / 0.04914 | 0.13854 / 0.05903 / 0.05138 |

- Clean Impact Residential Rate Relief Credit: −0.03871/kWh (application to this account
  unconfirmed; July 2026 bill reconstruction matched the NO-credit case within 6%)
- CEA net-surplus compensation at true-up: ~$0.06/kWh

## Adders for CCA (CEA) service
- WF-NBC + DWR-BC: 0.00591
- PCIA (2023 vintage, per JRC for this account): 0.02828 (6/1/26 tables; 0.02823 on 1/1/26)
- Non-bypassable charges not offset by NEM exports (PPP + ND + CTC + WF-NBC/DWR): ≈ 0.02099

## Fixed charges
- Base Services Charge (all residential plans, incl. EV-TOU-5): $0.79343/day (≈$24.15/mo)
- DR/FERA/CARE variants: $0.39688/day; EV-TOU (separately metered legacy): min bill $0.413/day

## Baseline (Schedule DR, kWh/day) — used for TOU-DR1/DR2/DR-P 130% baseline credit (−$0.10663/kWh, 6/1/26)
| Zone | Summer basic | Winter basic | Summer all-elec | Winter all-elec |
|---|---|---|---|---|
| **Coastal (this home)** | **9.0** | **9.2** | 8.3 | 13.5 |
| Inland | 10.4 | 9.6 | 10.1 | 15.8 |
| Mountain | 13.6 | 12.9 | 16.5 | 26.0 |
| Desert | 15.9 | 10.9 | 18.5 | 20.0 |

## Account facts captured from My Energy Center (Jul 24, 2026)
- Current plan: EV-TOU-5 (Time of Use — EVTOU5-Residential); NEM 2.0; CEA generation
- Jul 2, 2026 bill: generation $169.63 (CEA) + delivery $156.63 (SDGE) + fees/taxes $72.30
- YTD NEM balance $398.56; 6 months to true-up; remaining credits $0
- PCIA vintage 2023 (per JRC listing for CEA)

## Modeling notes
- NEM 2.0 modeled per-interval: imports × full rate − exports × (rate − NBC);
  verified equivalent to monthly per-period netting within 0.3%
- DR (tiered) excluded: NEM 2.0 requires a TOU rate
- TOU-DR-P Reduce-Your-Use event surcharge ($1.16/kWh, up to ~18 events/yr) not simulated
- Holidays treated as weekends: New Year's, Presidents, Memorial, July 4, Labor,
  Veterans, Thanksgiving, Christmas
