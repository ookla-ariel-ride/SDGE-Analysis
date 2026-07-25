# Data Sources Cheatsheet — reproduce this analysis for your own home

Fill in the blanks, gather the files into one folder, then hand them to Claude Cowork with
`reusable-prompt.md`. Nothing here needs coding — it's all downloads and account logins.
Items marked **(required)** are the minimum; the rest make the analysis richer.

Legend: 📥 = file you download · 🔗 = link to note · ✍️ = value to write down · 🔒 = contains personal info, keep private.

---

## A. Your household basics ✍️ (required)
- ZIP / climate zone: ____________
- Utility: ☐ SDG&E ☐ PG&E ☐ SCE ☐ other: ________
- Generation provider: ☐ utility (bundled) ☐ CCA — which? ________ product/tier ________
- NEM status: ☐ NEM 1.0 ☐ NEM 2.0 ☐ NBT/Solar Billing Plan · PTO date ______ · true-up month ______
- Current rate plan: ____________
- Have: ☐ rooftop solar (kW DC ____) ☐ battery ☐ EV(s) ____ ☐ gas service ☐ pool · water heater ____ / heating ____ / cooking ____

## B. Electric usage — interval data 📥🔒 (required)
- **SDG&E:** My Energy Center (🔗 https://myenergycenter.com) → Usage → **Green Button Download** → last 13 months → **CSV**.
- Other utilities: look for "Green Button" / "Download my data".
- File looks like `Electric_15_Minute_<range>.csv` — 15-minute imports/exports. **Contains name/address/account — private.**

## C. Rate schedules 🔗📥 (required — current PDFs, they change Jan 1 & Jun 1)
- **SDG&E Total Rates Tables** (one per plan): 🔗 https://www.sdge.com/rates-and-regulations → "Total Electric Rates", or direct:
  `https://www.sdge.com/sites/default/files/regulatory/<M-D-YY> Schedule <PLAN> Total Rates Table.pdf`
  Plans: EV-TOU-5, EV-TOU-2, TOU-DR1, TOU-DR2, TOU-DR-P, TOU-ELEC, DR.
- **TOU windows:** 🔗 https://www.sdge.com/residential/pricing-plans · **Baseline allowances:** in the Schedule DR PDF.
- **If on a CCA:** its residential rate schedule + the SDG&E–CCA Joint Rate Comparison. Note any per-kWh credits.

## D. Detailed monthly bills 📥🔒 (highly recommended — this is where the truth is)
- My Energy Center → Billing → **Billing History** → each month → **"View Your Detailed Bill PDF"**. ~12 months of **electric** and **gas**.
- These validate modeled rates to the penny, confirm product/credits/climate zone, and anchor absolute dollars. **Private.**
- Also run the utility's own plan-comparison tool (Billing → Pricing Plans) and screenshot it.

## E. Solar — production, install, cleaning 📥🔒 (required if you have solar)
- **Enphase Enlighten** (🔗 https://enlighten.enphaseenergy.com): Reports → **"SAM 8760"** (one per calendar year, emailed; hourly whole-home consumption) and **"Site Energy Production"** (daily). Devices/Details → inverter model & count, kW DC, metering config, PTO date.
- **Other monitoring** (SolarEdge, Tesla, SMA): export daily + (ideally) 15-min/hourly production.
- **PVOutput** (🔗 https://pvoutput.org, if you publish there): public daily list is scrapable; donors get the API — per-year stats (degradation), 5-min power (clipping), and **multi-year daily windows via `getoutput`** (needed for the panel-cleaning diff-in-diff). Share only a **read-only** API key and revoke it after.
- **Solar install invoice/contract** 📥🔒 ✍️ — total installed price $______, date paid ______, federal ITC claimed? ☐ — required for the lifetime-payback analysis.
- **Panel-cleaning history** ✍️ — date(s) ______ and cost per cleaning $______ — enables the measured cleaning-effect study and the cleaning-cadence model.

## F. Gas usage 📥🔒 (if you have gas)
- My Energy Center → gas account → Usage → **Green Button Download** → 13 months CSV (daily therms). Note the gas rate schedule name (on the detailed gas bill).

### Also for section E (lifetime payback + size verification)
- ✍️ **Utility average-rate history** (for back-casting the payback curve): any published multi-year average residential ¢/kWh series (e.g. a state-auditor or public-power rate-history chart) — record the source URL.
- ✍️ **Panel model and wattage + module count** (size verification: modules × watts should equal registered kW DC).

## E2. EV charging telemetry 📥🔒 (if you have EVs — validates the meter-side analysis)
- **Tesla app → Charge Stats** (per car): trailing-12-month energy by location (home/Supercharger/other) and TOU bucket — screenshot or note Home + Supercharging kWh. Battery-side kWh (≈ wall × 0.88–0.92).
- **Wall charger export**: networked chargers (Tesla Wall Connector, etc.) export per-day delivered kWh (wall-side) — the gold standard for validating EV session detection. Watch for batched-upload lag in the final rows.
- ✍️ Odometer + in-service date per car (annualized miles cross-check). Note any ICE vehicles (or that there are none).

## G. Weather & grid data (auto — no action needed; all free, no API keys)
- **Daily temperatures:** Open-Meteo archive API (for the cooling regression).
- **Daily precipitation:** NOAA/RCC ACIS (`data.rcc-acis.org`, nearest airport gauge) — for the soiling/rain-recovery study; also the fallback when Open-Meteo is unreachable.
- **Grid CO2 (carbon timing):** CAISO Today's Outlook history CSVs — `caiso.com/outlook/history/YYYYMMDD/co2.csv` + `demand.csv`.
- **Grid CO2 — bulk sampling:** the same CAISO history endpoints accept any `YYYYMMDD` — sample as many days as your fetch channel allows (~2 per calendar month beats 4 seasonal days), interpolate the rest by month-hour means, and label the result by coverage.
- **Reliability / resilience:** 🔗 the utility's **Electric System Reliability Annual Report** (SAIDI/SAIFI, by district where published — SDG&E's is on sdge.com with a CPUC copy) + **CPUC PSPS post-event reports** for your district — turns "what is backup worth?" into expected outage-hours/yr.
- **Fuel constants (electrification dividend):** ✍️ EIA state gasoline monthly price series (eia.gov) + FHWA Highway Statistics **VM-1** on-road fleet mpg — the cited constants for the gasoline counterfactual. Record source URLs and capture dates.

## H. Battery / incentive research (auto — Claude web-searches current data)
- Current installed prices for candidate batteries (Enphase IQ 5P/10C, Tesla Powerwall 3, etc.).
- Incentive status: federal residential ITC, CA SGIP (both change — do not assume they exist).
- **Battery revenue programs** (count only what you can actually enroll in TODAY): the CEC **DSGS** program page (energy.ca.gov; administrator portal dsgs.olivineinc.com) + your battery vendor's VPP pages (e.g. tesla.com/support/energy/virtual-power-plant) + your CCA's program list. Record closed/ineligible programs at $0 with the reason; note whether VPP export needs a Rule 21 interconnection modification and whether TOU-arbitrage stacking is permitted.
- **Fixed-charge status:** CPUC **D.24-05-028** + your utility's implementation resolution (SDG&E: **Resolution E-5355**) — check whether the income-graduated fixed charge is ALREADY on your bills (and in your rates module) before modeling it as a future scenario.

---

## Minimum to get started
**B** (electric interval CSV) + **A** (your basics). Claude can do a plan comparison from just those.

## For the full report
Add **C**, **D**, **E**, **F**. For lifetime payback you need the install invoice; for the
measured cleaning effect, cleaning dates + a multi-year daily production history (section E).

## Privacy reminder 🔒
Files in **B, D, E, F** (including the install invoice) contain your name, address, and
account/meter numbers. Keep them in a gitignored `private/` folder. Only de-identified
aggregates belong in a public repo. Claude runs a PII audit before any commit — hold it to that.
