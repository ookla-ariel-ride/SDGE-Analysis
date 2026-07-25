# Extended research notes — AB 205 / battery revenue / resilience / fuel constants
Captured 2026-07-25. Supports `analysis/extended_findings.py` → `data/extended_results.json`.

## A. AB 205 income-graduated fixed charge (Base Services Charge) — ALREADY IN EFFECT
- CPUC **D.24-05-028** (May 2024) adopted a flat 3-tier residential fixed charge; SDG&E
  implementation approved in **Resolution E-5355** (Dec 19, 2024; AL 4492-E), billed from
  **October 2025** as the **Base Services Charge (BSC)**: **$24.15/mo non-CARE**
  ($0.793/day), $12.08 FERA, $6.00 CARE. Non-bypassable; NEM credits cannot offset it.
- **EV-TOU-5's prior $16/mo Basic Service Fee was REPLACED, not added to** (net +$8.15/mo).
- Volumetric offset: one-time **equal-cents reduction (~5¢/kWh average, ~10%) to
  residential distribution rates across TOU periods** — with EV-TOU-5's super-off-peak
  distribution rate explicitly NOT reduced (left at $0.01496/kWh per D.21-07-010).
- Applies identically to NEM 2.0 and CCA customers; delivery-side only (CEA generation
  untouched).
- **Model status: rates.py `BSC = 0.79343/day` equals the adopted $24.15/mo exactly; the
  June 1, 2026 rates the report uses are post-restructure. No forecast scenario needed —
  the "structural threat" is already in the bills and in the model.**
- No adopted escalation of the BSC exists (CPUC shelved further income-graduation Jan 2025;
  repeal bills AB 1999 and AB 23 both failed, AB 23 on Feb 2, 2026). Sensitivity: every
  +$12/mo of future fixed charge = +$144/yr on every scenario equally; rankings and all
  marginal (behavior/battery) figures are unchanged by construction.
- Sources: CPUC Res E-5355 (docs.cpuc.ca.gov .../549864722.pdf); D.24-05-028
  (.../531686019.PDF); sdge.com/electric-billing (BSC FAQ); sdge.com/total-electric-rates
  (10/1/25 first post-BSC tables); calmatters.digitaldemocracy.org (AB 23, AB 1999).

## B. Battery revenue programs (PW3, SDG&E territory, CEA, NEM 2.0) — July 2026
| Program | Status | Realistic $/yr |
|---|---|---|
| **DSGS Option 3 via Tesla VPP** (app enrollment) | Active 2026 season (May–Oct); Option 1 $2/kWh emergency payments SUSPENDED for 2026; Option 3 = capacity $/kW-mo with 30% 2026 bonus; events 4–9pm, ≤2h, ≤35/summer | **$150–350** (Tesla cap "up to $350/Powerwall") |
| Tesla–SDG&E ELRP VPP ($2/kWh) | Closed to new enrollment since Apr 2024 | $0 |
| Tesla–SDCP VPP ($4,725 + 10¢/kWh) | Ineligible (CEA customer; new installs only) | $0 |
| SGIP general market | **Closed 12/31/2025** (supersedes earlier "waitlisted" status) | $0 |
| SGIP RSSE ($1.10/Wh) | Income-qualified only; fully reserved waitlist | $0 unless eligible |
| CEA storage programs | None exist (Battery Bonus Connect is income-qualified new-install) | $0 |
- DSGS explicitly permits TOU-arbitrage compensation alongside (rate-plan carve-out from
  the double-dip ban); events overlap the on-peak window the battery discharges anyway.
- NEM 2.0 risk: storage additions don't affect grandfathering; if interconnected
  **non-export**, VPP grid export needs a Rule 21 modification — confirm with SDG&E first.
- Open item: confirm in the Tesla app that 2026-season DSGS enrollment is being accepted.
- Sources: dsgs.olivineinc.com; CEC DSGS Guidelines 5th Ed.; tesla.com/support/energy/
  virtual-power-plant/{dsgs,sdge,sdcp}; sgipsd.org/incentives; cpuc.ca.gov/elrp;
  thecleanenergyalliance.org/programs.

## C. Outage exposure — coastal SDG&E circuit
- SDG&E system SAIDI (unplanned, excl. MED): 70.4 (2022), 70.6 (2023), 71.1 (2024)
  min/cust/yr; SAIFI ~0.54–0.61. 2024 incl.-MED spike (157.2) was the Dec 9–11 wind/PSPS
  event, concentrated in backcountry districts.
- Coastal districts 2024 SAIDI incl. MED: **Beach Cities 57.1, North Coast 41.5** vs
  Northeast 432 / Eastern 229 (HFTD backcountry). Coastal urban circuits sit outside the
  CPUC High Fire-Threat District tiers that drive PSPS; the Dec 2024 PSPS added ~3 min to
  Beach Cities vs ~350 min to Northeast.
- **Defensible coastal exposure: ~1–2.5 outage-hours/yr all-in (point estimate ~1.5 h/yr).**
  At any plausible household outage cost ($25–100/h) that is **~$40–250/yr of expected
  resilience value** — a real but small number; resilience remains a preference purchase,
  now bounded instead of $0-or-∞.
- Sources: SDG&E 2024 Electric System Reliability Annual Report (sdge.com; CPUC copy);
  CPUC PSPS post-event reports Dec 2024 / Jan 2025.

## D. Fuel constants for the electrification dividend
- CA regular gasoline, EIA monthly series (EMM_EPMR_PTE_SCA_DPG), Jun 2025–May 2026
  published 12-mo mean: **$4.65/gal** (calendar-2025 avg $4.41; spring-2026 spike to ~$5.95).
- Fleet fuel economy: **23.4 mpg** on-road light-duty actual (FHWA Highway Statistics
  VM-1, 2024); EPA MY2024 new-vehicle real-world 27.2 mpg. 23.4 is the conservative
  citable figure.
- Supercharger price **$0.45/kWh** is an estimate (typical CA $0.40–0.50), labeled as such.
