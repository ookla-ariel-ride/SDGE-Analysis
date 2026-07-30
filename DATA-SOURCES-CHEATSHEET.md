# Data Sources Cheatsheet — reproduce this analysis for your own home

Everything the analysis needs, with directions for where each item lives in your utility and
monitoring portals. Nothing here needs coding — it's all downloads and account logins. Items
marked **(required)** are the minimum; the rest make the analysis richer.

> **You don't have to work through this document alone — an assistant can walk you through
> it.** Paste [`reusable-prompt.md`](reusable-prompt.md) into a Claude Cowork or Claude Code
> session and it drives this cheatsheet section by section (A–H): asking each question in
> turn, telling you which portal page the answer is on, operating the browser while you
> handle the logins yourself, writing your answers into gitignored `private/household.yaml`,
> and tracking what's still outstanding in `private/intake-status.md`. It will not begin the
> analysis while a required field is unanswered. Reading it yourself works too — the
> questions and directions below are in gathering order.

**This document doubles as the machine-consumable intake-interview spec.** Every field below
carries a small fenced `yaml` block: a stable `id`, the homeowner-facing `question`, the
answer `type` (string / number / date / file / bool / list), when it's `required_if`
(always / has_solar / has_ev / has_gas / has_battery_interest), `where` to get it (the
portal walkthrough), and its `privacy` tier — `public-ok` (may appear in the published
report: system kW, climate zone, plan name, odometer readings), `private-only`
(street-level details, invoice amounts, billing-account context — lives only in the
gitignored `private/`), or `secret` (API keys and monitoring tokens). An agent running Phase A of
`reusable-prompt.md` drives the interview from these field ids, records answers in
`private/household.yaml` (schema template: `household.example.yaml` at the repo root), and
logs progress in `private/intake-status.md`. **No `private-only` or `secret` answer may
ever be written into any committed artifact — not the report, not `data/`, not scripts,
not commit messages. Secrets go ONLY into a gitignored `.env` file — never into
`household.yaml`.** Analysis scripts load the household file via `analysis/household.py`
and fail closed without it.

Legend: 📥 = file you download · 🔗 = link to note · ✍️ = value to write down · 🔒 = contains personal info, keep private.

---

## A. Your household basics ✍️ (required)

```yaml
id: climate_zone
question: "What climate zone is your home billed under? (It's printed on your detailed bill — don't guess from ZIP.)"
type: string
required_if: always
where: "ZIP / climate zone: read it off the DETAILED BILL — we wrongly assumed Inland once; the bill said Coastal."
privacy: public-ok
```

```yaml
id: utility
question: "Who is your electric utility?"
type: string
required_if: always
where: "Utility: ☐ SDG&E ☐ PG&E ☐ SCE ☐ other — the name on your electric bill."
privacy: public-ok
```

```yaml
id: cca
question: "Who supplies your generation — the utility (bundled), or a CCA? If a CCA, which one and which product/tier?"
type: string
required_if: always
where: "Generation provider: ☐ utility (bundled) ☐ CCA — which? product/tier? It's on the generation-charges page of the detailed bill."
privacy: public-ok
```

```yaml
id: nem_version
question: "What is your solar billing status — NEM 1.0, NEM 2.0, or NBT/Solar Billing Plan? And what month does your NEM year true up?"
type: string
required_if: has_solar
where: "NEM status: ☐ NEM 1.0 ☐ NEM 2.0 ☐ NBT/Solar Billing Plan · true-up month — on the bill's NEM year-to-date ledger page."
privacy: public-ok
```

```yaml
id: pto_date
question: "What date did your solar system get permission to operate (PTO)? (It starts your 20-year NEM clock.)"
type: date
required_if: has_solar
where: "PTO date: on the utility's PTO letter, or in your monitoring platform's system record (Enlighten: Devices/Details)."
privacy: public-ok
```

```yaml
id: rate_plan
question: "What rate plan are you on right now?"
type: string
required_if: always
where: "Current rate plan: printed at the top of the detailed bill (e.g. EV-TOU-5, TOU-DR1)."
privacy: public-ok
```

```yaml
id: has_solar
question: "Do you have rooftop solar? If yes, what's the registered kW DC?"
type: bool
required_if: always
where: "Have: ☐ rooftop solar (kW DC ____) — this flag gates every solar field (sections E / E-extra)."
privacy: public-ok
```

```yaml
id: has_battery
question: "Do you already have a home battery?"
type: bool
required_if: always
where: "Have: ☐ battery — existing storage changes the modeling baseline."
privacy: public-ok
```

```yaml
id: has_battery_interest
question: "Are you considering buying a battery? (Gates the battery-hardware and incentive research in section H.)"
type: bool
required_if: always
where: "Asked directly at intake — nothing to look up."
privacy: public-ok
```

```yaml
id: has_ev
question: "Do you have one or more EVs? How many? Any remaining gas (ICE) vehicles?"
type: bool
required_if: always
where: "Have: ☐ EV(s) ____ — this flag gates section E2. Note any ICE vehicles (or that there are none)."
privacy: public-ok
```

```yaml
id: has_gas
question: "Do you have natural-gas service?"
type: bool
required_if: always
where: "Have: ☐ gas service — this flag gates sections F and the gas-decomposition analysis."
privacy: public-ok
```

```yaml
id: appliance_fuels
question: "What fuels your major appliances — pool? water heater? space heating? cooking?"
type: string
required_if: always
where: "Have: ☐ pool · water heater ____ / heating ____ / cooking ____ — determines the electrification ladder."
privacy: public-ok
```

## B. Electric usage — interval data 📥🔒 (required)

```yaml
id: electric_interval_csv
question: "Download your 15-minute electric interval data (Green Button CSV, last 13 months)."
type: file
required_if: always
where: "SDG&E: My Energy Center (🔗 https://myenergycenter.com) → Usage → Green Button Download → last 13 months → CSV. Other utilities: look for 'Green Button' / 'Download my data'. File looks like Electric_15_Minute_<range>.csv — 15-minute imports/exports. Contains name/address/account — private."
privacy: private-only
```

⚠️ **The 13 months is a hard ceiling, and what falls off it is gone.** (That figure is
SDG&E's portal, verified on this account — other utilities retain more or less; find your
portal's ceiling the same way and treat it with the same urgency.) Verified
2026-07-27: the Green Button date picker greys out every date before the ceiling and
offers no way past it. This is a *different, shorter* window than the ~2 years of
statements in section D — bills and interval data age out on separate clocks, so a
period can have a parsed statement with no meter data left to check it against.

**Re-pull monthly and keep every export.** Each file overlaps the last, so the archive
is what gives you history beyond 13 months; nothing else can reconstruct it. Keep
superseded exports in `private/1-raw-data/superseded/` rather than overwriting, and
leave exactly one `Electric_15_Minute_*.csv` at the top level, since the documented
sandbox step (`cp ../1-raw-data/Electric_15_Minute_*.csv usage.csv`) takes a glob.

UI path, verified 2026-07-27: Usage → **Green Button Download** (the link below the
chart, not the Excel one) → set **From**/**To** → `.csv` → Download. The date fields are
read-only and driven by a picker: click its title to jump month view → year view instead
of stepping back a month at a time. The file lands in `~/Downloads` with no Save-As
dialog. Before trusting it, check `Reading Start`/`Reading End` in the header and that
the final day is not the all-zeros placeholder these exports sometimes carry.

## C. Rate schedules 🔗📥 (required — current PDFs, they change Jan 1 & Jun 1)

```yaml
id: rate_table_pdfs
question: "Download the utility's official Total Rates Table PDF for every plan you could be on."
type: list
required_if: always
where: "SDG&E Total Rates Tables (one per plan): 🔗 https://www.sdge.com/rates-and-regulations → 'Total Electric Rates', or direct: https://www.sdge.com/sites/default/files/regulatory/<M-D-YY> Schedule <PLAN> Total Rates Table.pdf — plans: EV-TOU-5, EV-TOU-2, TOU-DR1, TOU-DR2, TOU-DR-P, TOU-ELEC, DR."
privacy: public-ok
```

```yaml
id: tou_windows_source
question: "Note where the current TOU window definitions are published."
type: string
required_if: always
where: "TOU windows: 🔗 https://www.sdge.com/residential/pricing-plans."
privacy: public-ok
```

```yaml
id: baseline_allowance_source
question: "Note where your climate zone's baseline allowances are published."
type: string
required_if: always
where: "Baseline allowances: in the Schedule DR PDF."
privacy: public-ok
```

```yaml
id: cca_rate_schedule
question: "If you're on a CCA: download its residential rate schedule and the utility–CCA joint rate comparison; note any per-kWh credits."
type: file
required_if: always
where: "If on a CCA: its residential rate schedule + the SDG&E–CCA Joint Rate Comparison. Note any per-kWh credits. Skip (mark n/a) if utility-bundled."
privacy: public-ok
```

## D. Detailed monthly bills 📥🔒 (highly recommended — this is where the truth is)

```yaml
id: electric_bill_pdfs
question: "Download ~12 months of DETAILED electric bill PDFs."
type: list
required_if: always
where: "My Energy Center → Billing → Billing History → each month → 'View Your Detailed Bill PDF'. These validate modeled rates to the penny, confirm product/credits/climate zone, and anchor absolute dollars. Private."
privacy: private-only
```

```yaml
id: gas_bill_pdfs
question: "Download ~12 months of detailed gas bill PDFs."
type: list
required_if: has_gas
where: "Same Billing History flow on the gas account — ~12 months of gas statements. Private."
privacy: private-only
```

> **Bulk-downloading the statements (SDG&E My Energy Center, verified 2026-07-27 — a
> worked example, not the procedure: other utilities' portals differ; what you need from
> yours is the same outcome, every statement PDF for the retention window).**
> Clicking each row's "View Your Detailed Bill PDF" opens a viewer tab one bill at a time,
> which is slow for a 2-year pull. The portal keeps roughly **25 statements per account**
> (about 2 years back), and each is fetchable directly at
> `/portal/BillingHistory/DownloadBillPdf?invoiceId=<ISU…>`.
>
> The reliable method, run from the browser's own session (no cookie handling, no `curl`):
> 1. Open Billing → Billing History and expand nothing; the invoice ids are already in the
>    DOM as row attributes. Harvest id-to-statement-date pairs by walking each `<tr>` that
>    carries an `ISU…` attribute value and reading the date text from that row (falling back
>    to the previous sibling row when a row is an expanded detail panel).
> 2. For each id, `fetch(url, {credentials:'same-origin'})`, then save the blob with a
>    temporary `<a download="sdge_electric_YYYY-MM-DD.pdf">` click. Chrome writes straight to
>    the download folder with no native Save-As dialog, which is what makes this automatable.
> 3. **Pace it.** The endpoint rate-limits: bursts start returning HTTP 500 and then 403 with
>    a ~581-byte error body instead of a PDF. Keep a delay of about a second between files,
>    stop at roughly 25 files, and pause a minute or two before switching accounts. Treat any
>    response under ~10 KB as a failure and retry it later rather than saving it.
> 4. Switch accounts (electric ↔ gas) with the "Accounts:" selector at the top right, then
>    re-harvest ids — they differ per account.
> 5. Verify every file before trusting the pull: header starts `%PDF-`, size is a couple
>    hundred KB, and the `/Count` page total is sane (8–10 pages for these statements).
>    Then move them into gitignored `private/1-raw-data/` (never the repo root).
>
> Batching note for agent-driven runs: browser JavaScript calls time out after about 45
> seconds, so download in chunks of about five files per call rather than one long loop.
>
> **Retention limit (checked 2026-07-27):** the portal holds about 25 statements per
> account, roughly two years. Three independent checks agreed on the same floor: the
> billing-history table, the Excel export at
> `/portal/BillingHistory/GetHistoryDownload?type=Bill`, and the Usage page's date picker,
> which greys out earlier months and hides its back arrow. There is no pagination or date
> filter that reaches further. Anything older has to come from your own archive (saved
> PDFs, paperless-billing emails) or from the utility directly by phone. Pull your history
> before you need it: each month you wait, the oldest one drops off.

```yaml
id: plan_comparison_capture
question: "Run the utility's own plan-comparison tool and screenshot the result."
type: file
required_if: always
where: "My Energy Center → Billing → Pricing Plans — screenshot the utility's own recommendation (it shows account context, so keep it private)."
privacy: private-only
```

## E. Solar — production, install, cleaning 📥🔒 (required if you have solar)

```yaml
id: solar_hourly_consumption_export
question: "Export hourly whole-home consumption from your solar monitoring (one file per calendar year)."
type: file
required_if: has_solar
where: "Enphase Enlighten (🔗 https://enlighten.enphaseenergy.com): Reports → 'SAM 8760' (one per calendar year, emailed; hourly whole-home consumption). Other monitoring (SolarEdge, Tesla, SMA): export hourly consumption if metered."
privacy: private-only
```

```yaml
id: solar_daily_production_export
question: "Export daily (ideally also 15-min/hourly) gross production from your monitoring platform."
type: file
required_if: has_solar
where: "Enlighten: Reports → 'Site Energy Production' (daily). Other monitoring: export daily + (ideally) 15-min/hourly production."
privacy: private-only
```

```yaml
id: solar_kw_dc
question: "What is the registered system size in kW DC?"
type: number
required_if: has_solar
where: "Enlighten: Devices/Details → kW DC; also on your interconnection paperwork. Verify: module count × panel watts should equal it."
privacy: public-ok
```

```yaml
id: module_count
question: "How many modules (panels), and what model/wattage?"
type: number
required_if: has_solar
where: "Panel model and wattage + module count (size verification: modules × watts should equal registered kW DC) — Devices list or the install contract."
privacy: public-ok
```

```yaml
id: inverter_model
question: "What inverter(s) — model and count?"
type: string
required_if: has_solar
where: "Enlighten: Devices/Details → inverter model & count; gives the kW AC ceiling (count × per-unit VA) and the clipping analysis its nameplate."
privacy: public-ok
```

```yaml
id: metering_config
question: "What does your monitoring meter — production only, or production + consumption?"
type: string
required_if: has_solar
where: "Enlighten: Devices/Details → metering config. Determines whether whole-home load is measured or must be derived (load = production − exports + imports)."
privacy: public-ok
```

```yaml
id: pvoutput_api_key
question: "If you publish to PVOutput and have donor API access: paste a READ-ONLY API key (revoke it after the analysis)."
type: string
required_if: has_solar
where: "PVOutput (🔗 https://pvoutput.org, if you publish there): public daily list is scrapable; donors get the API — per-year stats (degradation), 5-min power (clipping), and multi-year daily windows via getoutput (needed for the panel-cleaning diff-in-diff). Share only a read-only API key and revoke it after."
privacy: secret
```

```yaml
id: install_invoice_usd
question: "What was the total installed price on your solar invoice/contract?"
type: number
required_if: has_solar
where: "Solar install invoice/contract 📥🔒 ✍️ — total installed price $______ — required for the lifetime-payback analysis. The invoice document itself stays in private/."
privacy: private-only
```

```yaml
id: install_paid_date
question: "When was the invoice paid?"
type: date
required_if: has_solar
where: "Date paid ______ — on the install invoice/contract."
privacy: private-only
```

```yaml
id: itc_claimed
question: "Was the federal ITC claimed on the system? (If you don't know, say so — the payback script reports both the gross and net-of-ITC crossovers as scenarios.)"
type: bool
required_if: has_solar
where: "Federal ITC claimed? ☐ — your tax records. Null/unknown is acceptable: analysis/lifetime_payback.py always computes BOTH crossovers."
privacy: private-only
```

```yaml
id: site_latitude
question: "Site latitude in decimal degrees (2 decimals ≈ 1 km is plenty — it only drives solar-geometry calculations)."
type: number
required_if: has_solar
where: "Any map app — long-press your roof ✍️ lat ______ — feeds the soiling study's clear-sky model (analysis/soiling_analysis.py reads location.lat). Coordinates are PII (CLAUDE.md §4): they live ONLY in private/household.yaml, never in any committed artifact."
privacy: private-only
```

```yaml
id: cleaning_history
question: "Have the panels ever been professionally cleaned? List each PAST cleaning: date and cost."
type: list
required_if: has_solar
where: "Panel-cleaning history ✍️ — date(s) and cost per cleaning $______ — enables the measured cleaning-effect study and the cleaning-cadence model. PAST events only (CLAUDE.md §0): never record a scheduled or planned cleaning."
privacy: public-ok
```

### Also for section E (lifetime payback + size verification)

```yaml
id: rate_history_source
question: "Find a published multi-year average residential ¢/kWh series for your utility (for back-casting the payback curve) and record the source URL."
type: string
required_if: has_solar
where: "Utility average-rate history ✍️: any published multi-year average residential ¢/kWh series (e.g. a state-auditor or public-power rate-history chart) — record the source URL."
privacy: public-ok
```

## E2. EV charging telemetry 📥🔒 (if you have EVs — validates the meter-side analysis)

```yaml
id: ev_charge_stats
question: "Capture each car's 12-month charging summary (energy by location and TOU bucket)."
type: file
required_if: has_ev
where: "Tesla app → Charge Stats (per car): trailing-12-month energy by location (home/Supercharger/other) and TOU bucket — screenshot or note Home + Supercharging kWh. Battery-side kWh (≈ wall × 0.88–0.92)."
privacy: private-only
```

```yaml
id: supercharge_kwh_yr
question: "How many kWh/yr do you DC-fast-charge (Supercharge), all cars combined?"
type: number
required_if: has_ev
where: "From the same Charge Stats screens — the Supercharging bucket, summed across cars. Stored in household.yaml (misc.supercharge_kwh_yr) for the electrification-dividend analysis."
privacy: public-ok
```

```yaml
id: wall_charger_export
question: "If your wall charger is networked, export its per-day delivered kWh."
type: file
required_if: has_ev
where: "Wall charger export: networked chargers (Tesla Wall Connector, etc.) export per-day delivered kWh (wall-side) — the gold standard for validating EV session detection. Watch for batched-upload lag in the final rows."
privacy: private-only
```

```yaml
id: charger_kw
question: "What is your home charger's maximum power (kW)?"
type: number
required_if: has_ev
where: "Charger spec plate or app (e.g. Tesla Wall Connector 11.5 kW). This is the behavior model's destination cap (household.yaml charger.kw)."
privacy: public-ok
```

```yaml
id: vehicles
question: "For each EV: make/model, in-service date, and current odometer reading."
type: list
required_if: has_ev
where: "✍️ Odometer + in-service date per car (annualized miles cross-check). Note any ICE vehicles (or that there are none)."
privacy: public-ok
```

```yaml
id: miles_per_year
question: "Roughly how many miles per year does the household drive, all EVs combined? (Derived from the odometers and in-service dates.)"
type: number
required_if: has_ev
where: "Computed at intake from the vehicles list: Σ odometer ÷ years in service. The annualized aggregate is what the analysis uses (household.yaml misc.miles_per_year)."
privacy: public-ok
```

## F. Gas usage 📥🔒 (if you have gas)

```yaml
id: gas_interval_csv
question: "Download 13 months of daily gas usage (Green Button CSV)."
type: file
required_if: has_gas
where: "My Energy Center → gas account → Usage → Green Button Download → 13 months CSV (daily therms)."
privacy: private-only
```

```yaml
id: gas_rate_schedule
question: "What is your gas rate schedule called?"
type: string
required_if: has_gas
where: "Note the gas rate schedule name (on the detailed gas bill)."
privacy: public-ok
```

```yaml
id: gas_therm_allin_usd
question: "What is your all-in $/therm? (Total gas dollars ÷ total therms across the 12 detailed bills.)"
type: number
required_if: has_gas
where: "Computed from the section-D gas bills: sum every bill's total charges, divide by total therms. Stored in household.yaml (gas.therm_allin_usd) for the electrification analysis."
privacy: public-ok
```

## G. Weather & grid data (auto — no action needed; all free, no API keys)

```yaml
id: weather_temps_source
question: "Nothing to gather — confirm the agent may fetch daily temperatures."
type: string
required_if: always
where: "Daily temperatures: Open-Meteo archive API (for the cooling regression)."
privacy: public-ok
```

```yaml
id: precip_source
question: "Nothing to gather — confirm the agent may fetch daily precipitation."
type: string
required_if: always
where: "Daily precipitation: NOAA/RCC ACIS (data.rcc-acis.org, nearest airport gauge) — for the soiling/rain-recovery study; also the fallback when Open-Meteo is unreachable."
privacy: public-ok
```

```yaml
id: grid_co2_source
question: "Nothing to gather — confirm the agent may fetch grid CO2 history."
type: string
required_if: always
where: "Grid CO2 (carbon timing): CAISO Today's Outlook history CSVs — caiso.com/outlook/history/YYYYMMDD/co2.csv + demand.csv. Bulk sampling: the same endpoints accept any YYYYMMDD — sample as many days as your fetch channel allows (~2 per calendar month beats 4 seasonal days), interpolate the rest by month-hour means, and label the result by coverage."
privacy: public-ok
```

```yaml
id: reliability_reports
question: "Nothing to gather — confirm the agent may pull outage-reliability reports for resilience pricing."
type: list
required_if: has_battery_interest
where: "Reliability / resilience: 🔗 the utility's Electric System Reliability Annual Report (SAIDI/SAIFI, by district where published — SDG&E's is on sdge.com with a CPUC copy) + CPUC PSPS post-event reports for your district — turns 'what is backup worth?' into expected outage-hours/yr."
privacy: public-ok
```

```yaml
id: fuel_constants_source
question: "Nothing to gather — confirm the agent may cite public fuel constants for the gasoline counterfactual."
type: string
required_if: has_ev
where: "Fuel constants (electrification dividend) ✍️: EIA state gasoline monthly price series (eia.gov) + FHWA Highway Statistics VM-1 on-road fleet mpg — the cited constants for the gasoline counterfactual. Record source URLs and capture dates."
privacy: public-ok
```

## H. Battery / incentive research (auto — Claude web-searches current data)

```yaml
id: battery_price_quotes
question: "Nothing to gather (research task) — current installed prices for candidate batteries."
type: list
required_if: has_battery_interest
where: "Current installed prices for candidate batteries (Enphase IQ 5P/10C, Tesla Powerwall 3, etc.)."
privacy: public-ok
```

```yaml
id: incentive_status
question: "Nothing to gather (research task) — current incentive status; do not assume programs exist."
type: string
required_if: has_battery_interest
where: "Incentive status: federal residential ITC, CA SGIP (both change — do not assume they exist)."
privacy: public-ok
```

```yaml
id: vpp_programs
question: "Nothing to gather (research task) — battery revenue programs you can actually enroll in TODAY."
type: string
required_if: has_battery_interest
where: "Battery revenue programs (count only what you can actually enroll in TODAY): the CEC DSGS program page (energy.ca.gov; administrator portal dsgs.olivineinc.com) + your battery vendor's VPP pages (e.g. tesla.com/support/energy/virtual-power-plant) + your CCA's program list. Record closed/ineligible programs at $0 with the reason; note whether VPP export needs a Rule 21 interconnection modification and whether TOU-arbitrage stacking is permitted."
privacy: public-ok
```

```yaml
id: fixed_charge_status
question: "Nothing to gather (research task) — is the income-graduated fixed charge already on your bills?"
type: string
required_if: always
where: "Fixed-charge status: CPUC D.24-05-028 + your utility's implementation resolution (SDG&E: Resolution E-5355) — check whether the income-graduated fixed charge is ALREADY on your bills (and in your rates module) before modeling it as a future scenario."
privacy: public-ok
```

---

## Minimum to get started
**B** (electric interval CSV) + **A** (your basics). Claude can do a plan comparison from just those.

## For the full report
Add **C**, **D**, **E**, **F**. For lifetime payback you need the install invoice; for the
measured cleaning effect, cleaning dates + a multi-year daily production history (section E).

## Privacy reminder 🔒
Files in **B, D, E, F** (including the install invoice) contain your name, address, and
account/meter numbers. Keep them in a gitignored `private/` folder. Only de-identified
aggregates belong in a public repo. Every intake field above carries a `privacy` tier:
`private-only` and `secret` answers must NEVER appear in a committed artifact, and
secrets (the PVOutput API key, any monitoring token) go ONLY into a gitignored `.env` —
never into `household.yaml`. Claude runs a PII audit before any commit — hold it to that.
