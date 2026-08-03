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
(always / has_solar / has_ev / has_gas / has_battery_interest / has_new_load_interest),
`where` to get it (the
portal walkthrough), and its `privacy` tier — `public-ok` (may appear in the published
report: system kW, climate zone, plan name, odometer readings), `private-only`
(street-level details, account and meter identifiers, monitoring account addresses, the
statement and invoice DOCUMENTS that carry all of those, billing-account context — lives
only in the gitignored `private/`), or `secret` (API keys and monitoring tokens). An agent running Phase A of
`reusable-prompt.md` drives the interview from these field ids, records answers in
`private/household.yaml` (schema template: `household.example.yaml` at the repo root), and
logs progress in `private/intake-status.md`. **No `private-only` or `secret` answer may
ever be written into any committed artifact — not the report, not `data/`, not scripts,
not commit messages. Secrets go ONLY into a gitignored `.env` file — never into
`household.yaml`.** Analysis scripts load the household file via `analysis/household.py`
and fail closed without it.

Legend: 📥 = file you download · 🔗 = link to note · ✍️ = value to write down · 🔒 = contains personal info, keep private.

### The `id` → `private/household.yaml` path contract

A field id is the name a tier is attached to; a yaml path is where the answer sits. Anything
that enforces the tiers has to get from one to the other, and this is the whole of how. The
steps are tried in order, and the first one that answers wins:

1. **The declared path-less list comes first.** An id listed at the end of this section
   stores no value in `household.yaml`, and no later step may invent one for it. The order
   matters: `gas_bill_pdfs` would otherwise derive to `gas.bill_pdfs` at step 3, a key that
   exists in no household's file.
2. **Then the override table.** If an id has a row there, that row is its path.
3. **Otherwise split the id at its first underscore.** Where the leading segment names a
   top-level block of `household.yaml` — `household`, `location`, `solar`, `charger`,
   `panel`, `monitoring`, `gas`, `misc` — the path is `<block>.<remainder>`:
   `panel_busbar_rating_a` → `panel.busbar_rating_a`, `solar_kw_dc` → `solar.kw_dc`,
   `charger_kw` → `charger.kw`, `gas_therm_allin_usd` → `gas.therm_allin_usd`,
   `monitoring_url` → `monitoring[].url`.
4. **Otherwise the id is itself a top-level key**: `vehicles` → `vehicles`,
   `cleaning_history` → `cleaning_history`.
5. **Lists.** `path[]` is the list itself; `path[].key` is that key inside every entry. A
   tier on `monitoring[].url` binds that key in every entry that carries it, including
   entries added later; entries without the key hold nothing to check. A tier on the list
   itself (`monitoring_feeds` → `monitoring[]`, `panel_schedule` → `panel.schedule`) binds
   the container: publishing the whole list publishes its private-only keys with it. A
   resolver that walks dotted keys through dictionaries alone reaches none of these
   `path[].key` fields, and skipping them silently is the failure mode this contract names.

**A tier belongs to the field, not to one household's answer.** `monitoring[].url` is
private-only because a monitoring site URL usually carries the site id that names the
account. A household whose entry happens to hold a bare dashboard link with no id in it is
still holding a private-only field.

**The contract.** Any tool that enforces these tiers:

- resolves every id in this file by steps 1–5 and, for each `private-only` and `secret` id,
  reads the value at that path;
- **fails loudly** when an id resolves to no path and is not declared path-less below. A tier
  whose subject cannot be located is a broken rule, and a gate that reports it clean is the
  exact failure this contract exists to prevent;
- **separates *checked* from *unchecked*** in what it reports. A path that resolves to a key
  absent from `private/household.yaml` means the household does not hold that answer, which
  several blocks here sanction in terms (`panel_main_breaker_position`,
  `panel_battery_breaker_position` and `panel_meter_socket_continuous_a` all say to leave the
  key out until someone has looked). That is not a failure and it is not a pass — a gate that
  prints a clean bill for a field it never had a value for is telling the reader something
  false;
- gets no new rows: a field added later SHOULD be given an id of the form `<block>_<key>` so
  that step 3 resolves it and the table below stops growing.

**What a value scan can see.** Searching committed files for the literal value works where
the value is distinctive: a coordinate, a catalog number, a dollar amount, a meter class. It
cannot work for a boolean, a one-word enum, or a short phrase — `panel.tandem_density` holds a
single word such as `high`, and every artifact in the repo contains that word. Fields in that
class are held to a
different rule, which is greppable: **no committed artifact carries a key of that name, and
no committed script reads that path**. An artifact means JSON and YAML at any depth and a CSV
header row; a script means python — `hh.get("<path>")` / `HH.get("<path>")`, a read of any
container above it, or the path written as a bare string literal — and the shell and yaml this
repo also tracks, where the search is for the dotted path as a whole token outside comments.
`household.example.yaml` is exempt from the key half, declared in `KEY_RULE_EXEMPT` with the
reason: carrying every intake key is what makes it the schema. It stays subject to the value
rules.

**The class is derived, not listed.** `unsearchable_fields()` in `analysis/privacy_tiers.py`
takes every `private-only` or `secret` field whose declared `type` is one a literal scan can
never search — a `bool` — and, where `private/household.yaml` is present, widens that with any
field whose recorded answer is a scalar that produced no needle **the scanner can use**. That
last qualifier is read off the same floor constant the scanner applies, so the two cannot
disagree about which fields the value scan reaches: a needle exists for a bare string of any
length, but the scanner skips one below the floor in every file it cannot parse, and a field
answered with a short bare word would otherwise be claimed by neither half. A private-only
boolean added next year is covered without anyone editing a list, and a field whose answer
turns out to be searchable in fact (a long free-text note, say) stays with the value scan
rather than being swept in here. Both halves look for key names and dotted paths and never
for a value, so both run in CI as well as in the pre-commit hook. Reformatting and
derivation defeat a literal scan too; TECHNICAL.md §11 states those limits in full.

**Override table** — every id whose path steps 2–3 cannot derive:

| field id | `private/household.yaml` path | why the row exists |
|---|---|---|
| `climate_zone` | `household.climate_zone` | section-A ids drop the `household.` prefix |
| `utility` | `household.utility` | same |
| `cca` | `household.cca` | same |
| `nem_version` | `household.nem_version` | same |
| `pto_date` | `household.pto_date` | same |
| `has_ev` | `household.has_ev` | same |
| `has_gas` | `household.has_gas` | same |
| `has_new_load_interest` | `household.has_new_load_interest` | same |
| `rate_plan` | `household.plan` | the id and the key were named differently; the key is `plan` |
| `site_latitude` | `location.lat` | the block is `location`, the key is `lat` |
| `module_count` | `solar.module_count` | section-E ids drop the `solar.` prefix |
| `inverter_model` | `solar.inverter_model` | same |
| `install_invoice_usd` | `solar.install_invoice_usd` | same |
| `install_paid_date` | `solar.install_paid_date` | same |
| `itc_claimed` | `solar.itc_claimed` | same |
| `miles_per_year` | `misc.miles_per_year` | the block is `misc` |
| `supercharge_kwh_yr` | `misc.supercharge_kwh_yr` | same |
| `monitoring_feeds` | `monitoring[]` | the list itself has no key name of its own, and step 3 would otherwise read `feeds` as a key inside each entry |

**Declared path-less** — ids that store no value in `household.yaml` at all. A tool treats
these as resolved by declaration, which is a different outcome from failing to resolve:

- **The answer is a document** in gitignored `private/1-raw-data/`: `electric_interval_csv`,
  `electric_bill_pdfs`, `gas_bill_pdfs`, `gas_interval_csv`, `plan_comparison_capture`,
  `cca_rate_schedule`, `rate_table_pdfs`, `solar_hourly_consumption_export`,
  `solar_daily_production_export`, `ev_charge_stats`, `wall_charger_export`.
- **The answer is a source or a research finding** recorded in the report and its
  methodology: `tou_windows_source`, `baseline_allowance_source`, `rate_history_source`,
  `weather_temps_source`, `precip_source`, `grid_co2_source`, `reliability_reports`,
  `fuel_constants_source`, `battery_price_quotes`, `incentive_status`, `vpp_programs`,
  `fixed_charge_status`.
- **Applicability flags answered by the shape of the file**: `has_solar` (the `solar` block
  is present), `has_battery`, `has_battery_interest`.
- **Folded into another field, or carried in prose**: `appliance_fuels` (asked at intake,
  carried in the report's prose and in `gas.therm_allin_usd`'s scope), `metering_config`
  (recorded per feed as `monitoring[].measures` and `monitoring[].solar_cts_fitted`),
  `gas_rate_schedule` (the schedule name is read off the gas bill and carried in the report
  and TECHNICAL.md §9).
- **Secret, so it is not in this file at all**: `pvoutput_api_key` lives only in a gitignored
  `.env`.

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
id: has_new_load_interest
question: "Are you thinking about adding a big new electrical load — a heat pump, a heat-pump water heater, a second EV charger, a battery? (Gates the panel section E3 and the service-headroom calculation.)"
type: bool
required_if: always
where: "Asked directly at intake — nothing to look up. False for a bill-only analysis."
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
where: "Enlighten: Devices/Details → inverter model & count; gives the kW AC ceiling (count × per-unit VA) and the clipping analysis its nameplate. The count and the resulting kW AC are separate stored answers — the two blocks below."
privacy: public-ok
```

```yaml
id: solar_inverter_count
question: "How many inverters (or microinverters) are installed?"
type: number
required_if: has_solar
where: "Enlighten: Devices → the microinverter count (a string system usually has one or two). With the per-unit AC rating it gives the array's AC nameplate, the field below."
privacy: public-ok
privacy_note: "Published today: data/service_headroom.json states the PV AC ceiling as '<count> x <model>, <kW> kW AC', and the headroom bound cannot be audited without it. Same class of fact as module_count and solar_kw_dc, both already public-ok."
```

```yaml
id: solar_kw_ac
question: "What is the array's AC nameplate in kW — inverter count × per-unit AC rating?"
type: number
required_if: has_solar
where: "Inverter count × the per-unit continuous AC rating from the inverter datasheet; cross-check against the 'Max System AC Current' figure on the interconnection placard. It is below the kW DC on almost every array, and it is the ceiling the array can physically put onto the service."
privacy: public-ok
privacy_note: "analysis/service_headroom.py requires it and has no default for it, and data/service_headroom.json publishes it as the bound credited to every hour the production record does not cover. A nameplate rating, of the same kind as the kW DC already published."
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
where: "Solar install invoice/contract 📥🔒 ✍️ — total installed price $______ — required for the lifetime-payback analysis. The invoice DOCUMENT stays in private/: it carries your name, address and account details. The price read off it does not."
privacy: public-ok
privacy_note: "The repo already publishes the array's kW DC, module count, inverter model and count, the PTO date to the day, the climate zone, the rate plan, the NEM vintage, a year of 15-minute metered consumption and twelve months of billed dollars. Against that, an install price is a market fact about a transaction, attached to no name, address, account number, meter number or coordinate. It is also recoverable to within a few dollars from the crossover fractions and cumulative-value series data/lifetime_payback.json publishes, so private-only was a tier the repo was not keeping and could not keep without removing the lifetime-payback audit trail CLAUDE.md §9 requires a script for. TECHNICAL.md §11 records the decision."
```

```yaml
id: install_paid_date
question: "When was the invoice paid?"
type: date
required_if: has_solar
where: "Date paid ______ — on the install invoice/contract. Month precision is enough; analysis/lifetime_payback.py prints it in its header and computes nothing from it."
privacy: public-ok
privacy_note: "Its year-month is the pto_date's year-month, and pto_date is public-ok and published to the day. Holding the paid month private while publishing the interconnection day was an inconsistency rather than a rule."
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

## E3. Electrical panel & service ✍️🔒 (if you're sizing a new load — heat pump, second EV charger, battery)

Interval data answers a question the usual load calculation can only guess at: how much of
your service the house actually uses. NEC 220.87 lets a metered maximum demand stand in for
a nameplate calculation, so a year of 15-minute data plus the panel's own labels is enough
to scope whether a new circuit fits. This section gathers the panel side of that.

> ⚠️ **Read the labels; leave the panel closed.** Every value below is printed where you can
> see it with the door open — the main breaker's handle, the rating label inside the door,
> the schedule card, the meter face. None of it needs the dead front (the inner cover the
> breaker handles poke through) taken off. If something you need is hidden behind the dead
> front, stop: that is an electrician's job, and the service conductors stay live with the
> main switched off. Photographing each label and reading the values off the photos
> afterwards works fine.
>
> **What comes out is a scoping estimate, not a permit calculation.** It tells you whether
> a heat pump or a second charger is plausible on this service before you pay anyone to
> look. A licensed electrician's load calculation and your building department's sign-off
> are what actually authorize a circuit.

This section is tiered field by field, not as a block. The bare equipment **ratings** —
amps, kA, slot counts, and which end of the bus a breaker sits on — are `public-ok`: they
come from a short list of standard values that millions of dwellings share, and the
published headroom and 705.12(B)(3)(2) verdicts cannot be audited without them. The
**identifying detail** is `private-only`: catalog numbers, the enclosure description, the
meter class, the breaker family, and the whole circuit schedule, whose `device` markings
and door-legend `label`s describe the inside of one particular house. Each field says which
it is and why in its own `privacy_note`.

```yaml
id: panel_service_rating_a
question: "What is your main breaker rated at, in amps?"
type: number
required_if: has_new_load_interest
where: "Stamped on the main breaker's own handle — the large one at the top or bottom of the stack, often placarded SERVICE DISCONNECT. Read the handle, not the meter and not the panel's rating label: a panel can be fitted with a main smaller than its bus, sometimes with a placard saying not to enlarge it."
privacy: public-ok
privacy_note: "A bare service size — 100, 125, 150, 175, 200 A — shared by millions of dwellings and narrowing nobody down; the catalog number of the breaker carrying it is equipment-specific and stays private-only."
```

```yaml
id: panel_main_breaker_catalog
question: "What is the catalog (or style) number printed on the main breaker?"
type: string
required_if: has_new_load_interest
where: "On the face of the main breaker, beside the amp stamp. It identifies the breaker type, which the panel's rating label refers back to when it states a short-circuit rating per main type."
privacy: private-only
privacy_note: "A catalog number names one specific device in one specific panel, which is what the bare rating does not — it stays out of every committed artifact."
```

```yaml
id: panel_busbar_rating_a
question: "What is the panel's busbar rated at, in amps?"
type: number
required_if: has_new_load_interest
where: "On the rating label inside the door, usually as '___ AMPS MAX', often repeated on the wiring diagram printed beside it. The busbar is the bar the breakers clip onto, and it is what the 120% solar-backfeed rule is measured against. If the label covers a family of catalog numbers with different bus ratings and none is marked, record what the label actually says instead of picking one."
privacy: public-ok
privacy_note: "A bus is rated at one of a few standard values (100/125/150/200/225 A); publishing which one says nothing about whose panel it is, and the 120% arithmetic cannot be shown without it."
```

```yaml
id: panel_enclosure_catalog
question: "What is the catalog number of the panel enclosure itself?"
type: string
required_if: has_new_load_interest
where: "On the same rating label inside the door, and often on a sticker on the outside of the can. It is what a supply house needs to sell you a matching breaker or cover."
privacy: private-only
privacy_note: "Same reason as the main breaker's catalog number: it names one enclosure model rather than a rating, so it stays out of committed artifacts."
```

```yaml
id: panel_spaces
question: "How many physical breaker spaces does the panel have?"
type: number
required_if: has_new_load_interest
where: "Count the slots with the door open — occupied, blanked off, and empty alike. Space runs out before ampacity does in plenty of houses, so this is a separate question from the load calculation."
privacy: public-ok
privacy_note: "A count of slots in a stock load centre; the free-space verdict cannot be published without it, and what those slots FEED is the private part."
```

```yaml
id: panel_max_circuits
question: "What is the panel's maximum circuit count with tandem breakers?"
type: number
required_if: has_new_load_interest
where: "On the rating label, written like '12 SPACES / 24 CIRCUITS'. Tandems only fit where the panel is listed to accept them, so take the label's ceiling rather than doubling the space count."
privacy: public-ok
privacy_note: "The other half of the same stock-enclosure geometry as panel_spaces, and identifying in the same degree: not at all."
```

```yaml
id: panel_enclosure_type
question: "What type of enclosure is it — indoor or outdoor, what NEMA rating, and is the meter in the same box?"
type: string
required_if: has_new_load_interest
where: "The rating label gives the NEMA type (NEMA 1 indoor, NEMA 3R rainproof, and so on). Note whether it is a meter-main combination, with meter and main breaker in one outdoor enclosure, because that adds the socket rating below."
privacy: private-only
privacy_note: "Free text describing where on the building the gear sits and how it is arranged — a physical description of one house, not a rating, so it stays private-only. The socket rating it implies is published on its own."
```

```yaml
id: panel_meter_socket_continuous_a
question: "If the meter shares the panel enclosure, what continuous amp rating is printed for the meter socket?"
type: number
required_if: has_new_load_interest
where: "On the rating label of a meter-main combination, worded like 'METER SOCKET RATED ___ AMPS CONTINUOUS'. It is a third limit, separate from the bus and from the main, and usually the tightest one. Set it to null if you looked and the meter sits in its own enclosure or no such rating is printed — that is an answer, and the headroom is then reported against the main alone. Leave the key out entirely if you have not looked; the headroom is then published as an upper limit that a socket rating could tighten, rather than as though the constraint did not exist."
privacy: public-ok
privacy_note: "A printed continuous rating, same class of fact as the main and the bus; it is the binding constraint in the published headroom, which cannot be shown without it."
```

```yaml
id: panel_assembly_sccr_ka
question: "What short-circuit current rating (SCCR, in kA) does the rating label give for the assembly?"
type: number
required_if: has_new_load_interest
where: "On the rating label, often conditioned on which main is installed ('___ AMPS WITH TYPE __ MAIN'). Take the assembly figure matching your installed main; it can be lower than the rating marked on the breaker by itself."
privacy: public-ok
privacy_note: "A kA rating off a label, drawn from the same short standard list (10/22/25 kA) as every other panel of its type."
```

```yaml
id: panel_meter_class
question: "What class is the utility meter (e.g. CL10, CL100, CL200, CL320)?"
type: string
required_if: has_new_load_interest
where: "On the meter face or its nameplate, alongside the form and voltage. A meter class is a socket rating, not a service rating — record it, and don't let it stand in for the main breaker."
privacy: private-only
privacy_note: "Read off the utility's own metering equipment and recorded alongside the meter's form, model and AMI type, which together describe one installed meter — it stays private-only, and no committed artifact may quote it."
```

```yaml
id: panel_pv_backfeed_a
question: "If solar or a battery is connected to the panel, what is its backfeed breaker rated at?"
type: number
required_if: has_new_load_interest
where: "The breaker the solar feeds through, usually at the far end of the stack from the main and often under a red PV or DO-NOT-RELOCATE sticker. Read the handle stamp, then cross-check it against the 'Max System AC Current' figure on the interconnection placard beside the panel. Set it to null if you looked at the panel and nothing backfeeds it — that is an answer, and it lets the 120% busbar check resolve. Leave the key out entirely if you have not looked; the check then reports not_determined instead of crediting a zero nobody verified."
privacy: public-ok
privacy_note: "A breaker rating, no more identifying than the main's; the repo already publishes this array's kW DC, module count and PTO date, all of which say far more about the house."
```

```yaml
id: panel_breaker_family
question: "What breaker family does the panel take?"
type: string
required_if: has_new_load_interest
where: "Read the type letters off any existing branch breaker (BR, QO, HOM, THQL…) and check the rating label, which lists the classified types the panel accepts. Any new circuit has to use a compatible breaker."
privacy: private-only
privacy_note: "A manufacturer and product line rather than a rating; it belongs with the catalog numbers it is read off, and stays private-only for the same reason."
```

```yaml
id: panel_pv_breaker_position
question: "Which end of the breaker stack is the solar backfeed breaker at, top or bottom — and which end is the main at?"
type: string
required_if: has_new_load_interest
where: "NEC 705.12(B)(3)(2) is two conditions, not one: the 120% arithmetic, and the backfeed breaker sitting at the opposite end of the busbar from the main. The check compares the two ends, so record both — the backfeed breaker's as panel.pv_breaker_position and the main's as panel.main_breaker_position, each 'top' or 'bottom'. With either one missing the position condition is reported as not determined rather than assumed, and the arithmetic alone is never read as a compliant verdict. Null if nothing backfeeds the panel. This field describes the breaker ALREADY on the bus; a proposed battery breaker gets its own answer, panel.battery_breaker_position, below. The main's end is a separate answer with its own block, panel_main_breaker_position, immediately below."
privacy: public-ok
privacy_note: "Which end of a busbar a breaker sits on is one bit — top or bottom — and it is what makes the published 705.12(B)(3)(2) verdict checkable."
```

```yaml
id: panel_main_breaker_position
question: "Which end of the breaker stack does the main supply land on, top or bottom?"
type: string
required_if: has_new_load_interest
where: "The main is the large breaker at one end of the stack, usually placarded SERVICE DISCONNECT. Record which end as panel.main_breaker_position, 'top' or 'bottom'. It is the second half of NEC 705.12(B)(3)(2)'s position condition — every backfeed breaker's end is judged against it — so with this missing the condition reports not determined for every source, however the 120% arithmetic came out. Leave the key out until someone has looked; nothing is inferred from where the PV breaker sits."
privacy: public-ok
privacy_note: "One bit, top or bottom, on a stock enclosure; without it no position verdict in the artifact can be audited."
```

```yaml
id: panel_battery_breaker_position
question: "If you are considering a battery, has a position been surveyed for its backfeed breaker — which end of the busbar would it land on, top or bottom?"
type: string
required_if: has_new_load_interest
where: "This is a SEPARATE answer from panel.pv_breaker_position, and the existing PV breaker's end is never reused for it: a panel can satisfy NEC 705.12(B)(3)(2) for the breaker already installed and have nowhere at that end to land another. Answer only from a survey of the panel — two adjacent full-size spaces at the end of the bus opposite the main, and which end that is. Null until someone has looked, which keeps the battery's position condition at 'not determined' instead of borrowing a compliant-looking answer from a different breaker."
privacy: public-ok
privacy_note: "Same one bit as the other two positions, about a breaker that does not exist yet."
```

```yaml
id: panel_schedule
question: "List every breaker in the panel: the device marking, its pole count, its amp rating(s), and what the door legend says it feeds."
type: list
required_if: has_new_load_interest
where: "Two sources of different quality. Device markings are read straight off each breaker (catalog number and amp stamp) and are firm. Circuit descriptions come from the hand-lettered legend on the door and get matched to devices by position, which is weaker — record that mapping as provisional and note any legend entry you can't pair with a device. Photograph the stack top to bottom in overlapping frames so no position falls between shots. A tandem or quad device carries one amp value per pole group."
privacy: private-only
privacy_note: "The strongest private-only field in this section: `device` gives catalog numbers, and `label` and any note transcribe a door legend describing what the inside of one particular house runs. No committed artifact may carry a device string, a label string or per-device detail — only aggregates over the schedule (device count, spaces and pole positions used and free, twin-density count, the branch-OCPD sum and the largest branch OCPD), plus one derived single-device figure: the ampere rating of the branch overcurrent device serving the existing air conditioning, published as `noncoincident_loads.existing_ac_ocpd_a` alongside the count of schedule entries that matched. It is admitted for the same reason the bare service, busbar and backfeed ratings are public-ok — a standard NEC 240.6(A) ampere size shared by millions of dwellings — and it is load-bearing: the NEC 220.60 noncoincident credit bound is 125% of it. The label that selected it, and the words searched for, stay private."
```

```yaml
id: panel_schedule_confidence
question: "How firm is the schedule you just recorded — which parts are read directly, and which are inferred?"
type: string
required_if: has_new_load_interest
where: "Your own note on the two halves of the schedule: device markings read off each breaker are firm, and the door-legend-to-device mapping is inferred from position. Name the positions you could not pair with a legend entry."
privacy: private-only
privacy_note: "A qualifier on the private-only schedule, and meaningless apart from it. Nothing computes from it: where a published aggregate needs a caveat about the mapping, analysis/service_headroom.py derives its own wording from the schedule rather than quoting this text."
```

```yaml
id: panel_tandem_density
question: "How heavily does the panel already use tandem (twin-density) breakers?"
type: string
required_if: has_new_load_interest
where: "Count the twin-density devices as you record the schedule. A panel near its labelled circuit ceiling has less room to consolidate than its free-space count suggests."
privacy: public-ok
privacy_note: "An aggregate over the schedule, and a coarser one than the artifact already carries: data/service_headroom.json publishes the exact twin_density_devices count that service_headroom.py derives from the same list. Keeping a summary of a published count private would be a rule the repo is not keeping."
```

```yaml
id: panel_no_dryer_or_water_heater_circuit
question: "Does the panel have no dryer circuit and no water-heater circuit — that is, are both of those appliances non-electric here?"
type: bool
required_if: has_new_load_interest
where: "Read it off the door legend while recording the schedule, then confirm it against appliance_fuels rather than the legend alone: a legend can be years out of date."
privacy: private-only
privacy_note: "A claim about what one house's circuit legend does not contain, derived from the private-only schedule and read by no script. The same fact is available publicly from appliance_fuels, which is where a committed artifact takes it from. The rule issue #6 settled admits a schedule-derived value only where it is load-bearing and a standard NEC rating; this is neither."
```

```yaml
id: panel_existing_ac_nameplate_rla_a
question: "What is the existing air conditioner's or heat pump condenser's nameplate rated-load amps (RLA), in amps? (RLA specifically, not MCA — see 'where'.)"
type: number
required_if: has_new_load_interest
where: "On the condenser's own rating plate — the outdoor unit, not the panel — usually printed as 'RLA' (rated-load amps) next to the compressor's electrical data. Record RLA specifically, not the nameplate's separate MCA (minimum circuit ampacity) figure: MCA already has a 125% margin on the largest motor built into it (NEC 440.32/440.33), and analysis/service_headroom.py applies its own, independently-justified 125% on top of whatever this field holds, so reading MCA here would compound two different margins and overstate the credit. This is also a DIFFERENT fact from panel_schedule's `A/C`-labelled entry above: that is the branch breaker's rating, and NEC 440.22(A) permits that breaker to be sized well above the equipment's own RLA (up to 175%, or 225% where 175% will not hold the starting current), so the breaker is routinely larger than what the condenser itself draws. A heat pump that replaces this unit on its own circuit needs the equipment's own RLA, not the breaker's rating, to bound the credit for the load it physically removes. Set it to null if you have read the plate and no RLA figure is legible (only MCA is present) — that is an answer. Leave the key out entirely if you have not looked; the replacement case then reports itself not determined on the credit rather than assuming one, per CLAUDE.md's evidence rule."
privacy: public-ok
privacy_note: "A bare nameplate ampere rating on one piece of HVAC equipment — the same class of fact as solar.kw_ac and charger.kw, both already public-ok, and no more identifying than panel_service_rating_a or the existing_ac_ocpd_a this repo already publishes from the same circuit. analysis/service_headroom.py's heat_pump_replaces_ac case requires it to show its noncoincident-credit arithmetic at all; a private-only tier here would make that arithmetic unpublishable and the case would have nothing to show."
```

## E4. Household energy-monitoring feeds ✍️🔒 (optional — a second, independent meter)

A monitoring feed is a second measurement of the same house: a solar platform's production
and consumption records, a whole-home monitor at the mains, a public PVOutput history. It
earns its place by being able to contradict the utility meter, which is worth more than
another model built on the same data. Record every feed that exists, including ones this
analysis does not read yet — the inventory is what tells a later reader which cross-check was
available and which was not.

Tiers here are per field, as in E3. What a feed MEASURES and how finely is a property of a
product, shared by everyone who owns one, and `public-ok`. What ADDRESSES the account — the
site URL, the access method, the machine that runs the poller — is `private-only`: CLAUDE.md
§4 names utility, solar and PVOutput account ids as PII, and a monitoring URL is usually
built around one. Credentials are neither tier: an API key, password, token or session cookie
is `secret` and belongs ONLY in a gitignored `.env`, never in `household.yaml`.

```yaml
id: monitoring_feeds
question: "What energy-monitoring feeds does the household have, beyond the utility meter? One entry per feed. An empty list is a complete answer."
type: list
required_if: always
where: "Your own inventory: the solar monitoring platform (Enlighten, SolarEdge, Tesla, SMA), any whole-home monitor at the mains (Sense, Emporia, IotaWatt), any public feed you publish to (PVOutput). One entry per feed, carrying the keys defined by the blocks below."
privacy: private-only
privacy_note: "The container holds the private-only address keys (url, api, owned_by) alongside the public capability keys, so the list as a whole stays in private/. The capability facts are public-ok on their own, one key at a time, as their blocks say."
```

```yaml
id: monitoring_source
question: "For each feed: what is it — the product or platform name?"
type: string
required_if: always
where: "The product's own name (Enphase Enlighten, Sense, Emporia Vue, PVOutput). Answer once per entry in the monitoring list; nothing to answer where the list is empty."
privacy: public-ok
privacy_note: "A product name that every installation of it shares; the repo already names the solar monitoring platform throughout TECHNICAL.md §2."
```

```yaml
id: monitoring_measures
question: "For each feed: what does it actually measure — gross production, whole-home load at the mains, per-circuit, or a mix?"
type: string
required_if: always
where: "The device's documentation, checked against what its dashboard shows. Say which quantity is METERED rather than derived: a platform that computes consumption from production plus net metering is not measuring it."
privacy: public-ok
privacy_note: "A capability of the product, identical for every unit of it."
```

```yaml
id: monitoring_resolution
question: "For each feed: what time resolution does it record, and does its history keep that resolution?"
type: string
required_if: always
where: "The export or API documentation. Live resolution and STORED resolution differ often — a monitor that samples at 1 Hz may keep only daily totals."
privacy: public-ok
privacy_note: "A product capability."
```

```yaml
id: monitoring_status
question: "For each feed: is it ingested into this analysis, and what did probing it show it can and cannot answer?"
type: string
required_if: always
where: "Your own record of what you tested. Write the measured capability rather than the impression: which quantity, over which window, at which interval. This field carries no account identifier and no credential — those have their own keys below."
privacy: public-ok
privacy_note: "A finding about the feed's usefulness. Public-ok on the condition stated in `where`: an account id or a credential written into this free text is a private-only or secret value sitting in a public-ok field, which the tier does not sanction."
```

```yaml
id: monitoring_solar_cts_fitted
question: "For each feed: are consumption current transformers actually fitted, or is only production metered?"
type: bool
required_if: always
where: "The platform's device list, or look for the CT clamps in the combiner. It decides whether whole-home load is measured or derived (load = production − exports + imports) — the same question metering_config asks of the solar platform."
privacy: public-ok
privacy_note: "A hardware configuration fact of the same kind as metering_config, which is already public-ok and which the load reconstruction cannot be read without."
```

```yaml
id: monitoring_live_since
question: "For each feed: what date did it start recording?"
type: date
required_if: always
where: "The earliest date the platform's own history returns. It bounds every window the feed can support, so record it before planning an analysis around the feed."
privacy: public-ok
privacy_note: "A date of the same kind as pto_date, which the report publishes to the day."
```

```yaml
id: monitoring_history_depth_verified
question: "For each feed: how far back did history actually return when you probed it, and at what interval?"
type: string
required_if: always
where: "Probe it — request a window a year older than you expect and see what comes back. Documented retention and observed retention differ, and the observed one is what an analysis can spend."
privacy: public-ok
privacy_note: "A measured retention depth. It names a date the feed reaches back to, which says no more about the household than the published PTO date does."
```

```yaml
id: monitoring_finest_history_interval
question: "For each feed: what is the finest interval its HISTORY returns, as opposed to its live view?"
type: string
required_if: always
where: "From the same probe: DAY, HOUR, 15MIN, 5MIN. A monitor that streams at 1 Hz and stores daily totals answers DAY, and that is the answer an analysis has to plan against."
privacy: public-ok
privacy_note: "A product capability, and the one that decides whether a feed can cross-check 15-minute meter data at all."
```

```yaml
id: monitoring_url
question: "For each feed: what URL is its dashboard or site page?"
type: string
required_if: always
where: "The address you land on when you open the feed. It stays in private/household.yaml and goes into no committed artifact."
privacy: private-only
privacy_note: "A monitoring site URL usually carries the system or site id inside it (Enlighten /systems/<id>, PVOutput ?sid=<id>), which is the 'utility/solar/PVOutput account id' CLAUDE.md §4 keeps out of committed artifacts. The tier follows what the field can hold, so a bare dashboard link with no id in it is private-only too."
```

```yaml
id: monitoring_api
question: "For each feed: how is it read programmatically — which API or client library, and which .env variable names hold its credentials?"
type: string
required_if: always
where: "The platform's API documentation, or the client library you use. Record the ACCESS METHOD and the NAMES of the .env variables. A key, token, password or session cookie written here is a secret in the wrong file — move it to the gitignored .env and leave the name behind."
privacy: private-only
privacy_note: "An access path into one household's account. Private-only rather than secret because the field holds the method and the variable names; the values those names stand for are secret and never enter household.yaml."
```

```yaml
id: monitoring_owned_by
question: "For each feed: where does the code that polls it live?"
type: string
required_if: always
where: "The repo or path running the poller, where it is not this one. Null where the feed is read by hand."
privacy: private-only
privacy_note: "A filesystem path or repo name on the operator's own machine, which can carry a username or a directory layout. It describes the setup rather than the energy data, and no analysis script reads it."
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
account/meter numbers. Keep them in a gitignored `private/` folder. A document's tier and
the tier of a value read off it are separate questions — the install invoice stays private
while the price on it is `public-ok`, and the detailed bills stay private while the climate
zone and rate plan printed on them are published. Only de-identified
aggregates belong in a public repo. Every intake field above carries a `privacy` tier:
`private-only` and `secret` answers must NEVER appear in a committed artifact, and
secrets (the PVOutput API key, any monitoring token) go ONLY into a gitignored `.env` —
never into `household.yaml`. Claude runs a PII audit before any commit — hold it to that.
