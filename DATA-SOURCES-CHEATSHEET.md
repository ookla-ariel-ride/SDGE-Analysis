# Data Sources Cheatsheet — reproduce this analysis for your own home

Fill in the blanks below, gather the files into a folder, then hand them to Claude Cowork
with `reusable-prompt.md`. Nothing here needs coding — it's all downloads and account logins.
Items marked **(required)** are the minimum; the rest make the analysis richer.

Legend: 📥 = a file you download · 🔗 = a link/URL to note · ✍️ = a value to write down · 🔒 = contains personal info, keep private.

---

## A. Your household basics ✍️ (required)
- ZIP / climate zone: ____________
- Utility: ☐ SDG&E ☐ PG&E ☐ SCE ☐ other: ________
- Generation provider: ☐ utility (bundled) ☐ CCA — which? ________ and product/tier ________
- NEM status: ☐ NEM 1.0 ☐ NEM 2.0 ☐ NBT/Solar Billing Plan · PTO/interconnection date ______ · annual true-up month ______
- Current rate plan: ____________
- Have: ☐ rooftop solar (kW DC ____) ☐ battery ☐ EV(s) ____ ☐ gas service ☐ pool ☐ electric vs gas: water heater ____ / heating ____ / cooking ____

## B. Electric usage — interval data 📥🔒 (required)
- **SDG&E:** My Energy Center → Usage → **Green Button Download** → date range = last 13 months → format **CSV**.
  🔗 https://myenergycenter.com  (Usage tab)
- Other utilities: look for "Green Button" / "Download my data" (PG&E SmartMeter, SCE, etc.).
- File looks like `Electric_15_Minute_<range>.csv` — 15-minute imports/exports. **Contains name/address/account — private.**

## C. Rate schedules 🔗📥 (required — current PDFs, they change Jan 1 & Jun 1)
- **SDG&E Total Rates Tables** (one per plan): 🔗 https://www.sdge.com/rates-and-regulations → "Total Electric Rates", or direct:
  `https://www.sdge.com/sites/default/files/regulatory/<M-D-YY> Schedule <PLAN> Total Rates Table.pdf`
  Plans to pull: EV-TOU-5, EV-TOU-2, TOU-DR1, TOU-DR2, TOU-DR-P, TOU-ELEC, DR.
- **TOU time windows:** 🔗 https://www.sdge.com/residential/pricing-plans
- **Baseline allowances (kWh/day by climate zone):** in the SDG&E Schedule DR PDF.
- **If on a CCA (e.g. Clean Energy Alliance):** 🔗 residential rate schedule + SDG&E–CCA Joint Rate Comparison (e.g. thecleanenergyalliance.org → Rates). Note any per-kWh credits.

## D. Detailed monthly bills 📥🔒 (highly recommended — this is where the truth is)
- My Energy Center → Billing → **Billing History** → expand each month → **"View Your Detailed Bill PDF"**.
- Download **~12 months of ELECTRIC** and **~12 months of GAS** detailed PDFs.
- Name them simply, e.g. `elec jun 2026.pdf`, `gas jun 2026.pdf`.
- These validate the modeled rates to the penny, confirm your exact product/credits/climate zone,
  and reconcile modeled vs actual billed cost. **Contain full account details — private.**
- Also: My Energy Center → Billing → Pricing Plans → run the utility's own plan-comparison tool and screenshot its per-plan estimates.

## E. Solar production 📥🔒 (required if you have solar — the utility can't see self-consumed solar)
- **Enphase Enlighten** (🔗 https://enlighten.enphaseenergy.com):
  - Reports → **"SAM 8760"** → one per calendar year you need (emailed CSV; hourly whole-home consumption).
  - Reports → **"Site Energy Production"** → daily production for your window.
  - Devices / System Details → note inverter model & count, array kW DC, metering config, PTO date.
- **Other monitoring** (SolarEdge, Tesla, SMA): export daily + (ideally) 15-min/hourly production.
- **PVOutput** (if you publish there, 🔗 https://pvoutput.org): your public daily list is scrapable;
  donors can use the API for per-year stats (degradation) and 5-min power (clipping). ✍️ note your System ID; only share a **read-only** API key and revoke it after.

## F. Gas usage 📥🔒 (if you have gas)
- My Energy Center → switch to the **gas account** → Usage → **Green Button Download** → last 13 months → CSV (daily therms).
- Note the gas rate schedule name (on the detailed gas bill, e.g. "GR-Residential").

## G. Weather (auto — no action needed)
- Claude pulls daily temps free from Open-Meteo's archive API for your lat/long. Just give your ZIP or city.

## H. Battery / incentive research (auto — Claude web-searches current data)
- Current installed prices for candidate batteries (Enphase IQ 5P/10C, Tesla Powerwall 3, etc.).
- Incentive status: federal residential ITC, CA SGIP (both change — do not assume they exist).

---

## Minimum to get started
**B** (electric interval CSV) + **A** (your basics). Claude can do a plan-comparison from just those.

## For the full report (plan + solar + battery + gas + bill audit)
Add **C**, **D**, **E**, and **F**. That's what produced this repo's report.

## Privacy reminder 🔒
Files in **B, D, E, F** contain your name, address, and account/meter numbers. Keep them in a
`private/` folder that is gitignored. Only de-identified aggregates (monthly totals, per-period
kWh, rate tables) belong in a public repo. Claude runs a PII audit before any commit — hold it to that.
