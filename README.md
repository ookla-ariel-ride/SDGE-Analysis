# SDG&E Rate Plan Analysis

[![gitleaks](https://github.com/ookla-ariel-ride/SDGE-Analysis/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/ookla-ariel-ride/SDGE-Analysis/actions/workflows/gitleaks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## [View the live report](https://ookla-ariel-ride.github.io/SDGE-Analysis/)

**https://ookla-ariel-ride.github.io/SDGE-Analysis/**

That page *is* `index.html`, served by GitHub Pages. The charts render on their own; you
don't install anything. (You can also clone or download this repo and double-click
`index.html`, which is fully self-contained with data inlined and Chart.js from CDN. GitHub's
own file viewer shows the HTML *source*, not the rendered report, so use the link above
instead.)

**Choose your path:**
- Just want to see the report? [Read it live](https://ookla-ariel-ride.github.io/SDGE-Analysis/).
- Want to run this on your own home? Jump to [Reproduce this for your own home](#reproduce-this-for-your-own-home--start-here).
- Auditing the methods? Start with [TECHNICAL.md](TECHNICAL.md).

> **How this report was produced:** generated with **Claude Cowork (Fable 5)**, independently
> reviewed with **Claude Code (Fable 5)** and adversarially reviewed with **Codex (GPT-5.6 Sol)**,
> then re-worked in Claude Cowork to incorporate the findings of both reviews.

An interactive, evidence-based report for a solar home with two EVs (all-electric transportation) in the SDG&E Coastal climate zone (NEM 2.0, CCA generation), built from 365 days of 15-minute Green Button interval data, a full-year detailed-bill audit, six years of production records, per-vehicle charging telemetry, and real weather + grid data. It was created for one household, with every personal identifier removed. The repo also carries the machinery behind it (scripts, data schemas, report template, intake interview, privacy gates) so anyone can run the same analysis on their own data.

**Companion documents:**
[**TECHNICAL.md**](TECHNICAL.md) — the full methods documentation: every script, data schema, algorithm, and validation chain, written so the analysis can be audited or rebuilt ·
[**GLOSSARY.md**](GLOSSARY.md) — every term (NEM, TOU, PCIA, CAISO, phantom load…) in plain English ·
[**DATA-SOURCES-CHEATSHEET.md**](DATA-SOURCES-CHEATSHEET.md) — the intake interview: every data source you need, field by field, to run this on your own home ·
[**reusable-prompt.md**](reusable-prompt.md) — the AI prompt that rebuilds the entire analysis.

**In this README:**
[What the report covers](#what-the-report-covers) ·
[Reproduce this for your own home](#reproduce-this-for-your-own-home--start-here) ·
[Publish with GitHub Pages](#publish-with-github-pages) ·
[Repository layout](#repository-layout) ·
[The private inputs](#the-private-inputs--and-how-to-obtain-your-own) ·
[Refreshing this analysis](#refreshing-this-analysis-same-house-new-data)

## What the report covers

One interactive page that works through a single home's energy economics end to end. At a
high level, it covers:

- how the raw data was gathered and cross-checked: utility interval meter, solar
  production, whole-home load, a year of actual bills, weather, and grid data
- which rate plan fits the measured usage, including CCA vs bundled generation
- what EV-charging behavior costs and what changing it would be worth
- whether a battery makes financial sense, how dispatch policy changes that answer, and
  how long each configuration could carry the house through an outage
- the health of the existing solar array: degradation, clipping, soiling and cleaning
  economics, and whether expansion is worthwhile
- how the modeled bills reconcile against the year of actual statements
- grid-carbon timing, NEM economics, and long-run rate-escalation exposure
- a closing implementation appendix and a methodology section documenting every model,
  source, caveat, and the validation chain

The report reaches specific verdicts and dollar figures on each of these; read it for the
answers. Every figure in it is labeled measured, modeled, or estimated, and traces to a
committed script and data artifact in this repo.

> **Note on solar monitoring:** this analysis happened to pull production data from **Enphase
> Enlighten**, but the method is vendor-agnostic. SolarEdge, Tesla, SMA, Fronius, PVOutput, and
> other platforms all expose equivalent production feeds (gross generation + system specs).
> `DATA-SOURCES-CHEATSHEET.md` describes the *data* you need, not one vendor's menu; substitute
> your own monitoring platform's export.

## Reproduce this for your own home — start here

The machinery — the validators, the billing engine's structure, the audit method, the report
shell — is reusable; the numbers are not. The committed `data/*` files and `index.html` are
**this house's results**, `analysis/rates.py` carries this house's bill-derived tariff, and a
handful of per-house parameters live as labeled constants in the scripts (each is marked at
the point of use; the manual route below names the ones you must change). Yours get
regenerated from your own data, so replace them rather than editing them.

**0 · Blank slate.** Clone the machinery, drop the history and results, protect yourself:

```bash
git clone https://github.com/ookla-ariel-ride/SDGE-Analysis.git my-energy-analysis
cd my-energy-analysis
rm -rf .git && git init                 # fresh repo: keep the tooling, not this house's history
mkdir -p private/1-raw-data             # your raw exports land here (gitignored, never pushed)
brew install gitleaks                   # or see gitleaks docs for other platforms
git config core.hooksPath .githooks     # secret/PII scan now blocks every commit
```

**1 · Run the intake interview.** Work through [`DATA-SOURCES-CHEATSHEET.md`](DATA-SOURCES-CHEATSHEET.md),
a per-field interview spec (`id` / `question` / `type` / `required_if` / `where` /
`privacy` tier). You can read it yourself, or hand `reusable-prompt.md` to an assistant and
have it walk you through the questions section by section, operating the portals while you
handle the logins. Raw files (interval export, 12 months of detailed bills, production records,
rate tables) go in `private/1-raw-data/`; per-house facts go in `private/household.yaml`
(copy `household.example.yaml` and replace every placeholder); log progress per field id in
`private/intake-status.md`. Secrets (API keys, monitoring tokens) go ONLY into a gitignored
`.env`. Analysis may not start while any required field is missing: the scripts fail closed
without `household.yaml`. (The "private inputs" section below shows what the withheld files
look like.)

**2 · Add your personal PII patterns.** Create `private/pii-rules.toml` with your own name,
address, and account/meter numbers (see `CLAUDE.md` §4 for the format and the rule chain).
It stays local; the pre-commit hook picks it up automatically and blocks any commit
containing those values.

**3 · Run the analysis.** Two routes:
- **AI route (how this repo was built):** paste [`reusable-prompt.md`](reusable-prompt.md)
  into a Claude Cowork or Claude Code session and hand it your gathered files. `CLAUDE.md` is
  the operating manual the agent follows; `report-template.html` is the report shell it fills.
- **Manual route:** `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`;
  make sure `private/household.yaml` exists (step 1; the scripts fail closed without it);
  update `analysis/rates.py` from **your** bills (it is the single source of truth; non-SDG&E
  users replace the TOU windows and rate tables wholesale). **Check the billing vintage
  first:** the engine implements NEM 2.0 monthly retail netting. If your solar interconnected
  after April 2023 you are on NEM 3.0 / the Solar Billing Plan (hourly netting, avoided-cost
  export credits) and the committed engine does not model your bills — the structure in
  `rates.bill_nem_monthly` must be replaced, not just the constants. Then: **re-point the
  analysis window** — the pipeline anchors on `WINDOW_END` in `analysis/behavior_rebuild.py`
  (and the matching anchors TECHNICAL.md §3 lists in the other scripts) at the end of YOUR
  export; the coverage validator fails closed until the window matches your data. Place your
  Green Button CSV as `usage.csv` next to the scripts (`CLAUDE.md` "Commands" shows the
  `private/verify/` sandbox pattern); run the pipeline **in dependency order**
  (`analyze_norelief.py` first — `battery_plan_matrix.py` ties out against its
  `plan_results.csv` — then `behavior_rebuild.py`, `battery_dispatch_policies.py`,
  `battery_plan_matrix.py`, `package_results.py`, `extended_findings.py`,
  `carbon_fullyear.py`, plus `soiling_analysis.py`, `billing_model_nem.py`, and
  `lifetime_payback.py` as applicable); then fill `report-template.html`'s `{{TOKEN}}`s from
  your regenerated `data/*.json`.

**4 · Validate before you trust it.** The gates in `CLAUDE.md` §9, in order: your billing
model must reproduce your actual bills before you quote any absolute dollar; every committed
artifact must regenerate from its committed script; report deltas, not levels. These gates
have actually been run end-to-end: from a fresh clone of this repo, with staged private
inputs and a new venv, the pipeline regenerated every committed artifact byte-identically.

**5 · Publish (optional).** Follow the GitHub Pages section below, after reading the
privacy note.

## Publish with GitHub Pages

```bash
cd my-energy-analysis                    # the repo you initialized in step 0
git add . && git commit -m "My energy analysis"
# create a repo on github.com (private recommended - see privacy note), then:
git remote add origin https://github.com/<you>/my-energy-analysis.git
git push -u origin main
```

Then on GitHub: **Settings → Pages → Source: Deploy from a branch → Branch: `main` / root → Save.**
Your report will be live at `https://<you>.github.io/my-energy-analysis/` within a minute or two.

> ### ⚠️ Privacy note
> All sensitive material (raw Green Button CSV with name/address/account number, Enphase
> exports, rate-research notes containing account details) lives in **`private/`**, which is
> excluded by `.gitignore` along with defensive filename patterns. **Never `git add -f`
> anything under `private/`.** Enforcement is mechanical, not manual: a gitleaks pre-commit
> hook (`git config core.hooksPath .githooks`) blocks commits containing secrets or account
> data, `.github/workflows/gitleaks.yml` re-scans full history on every push
> (`.github/workflows/tests.yml` runs the parser's fail-closed guards alongside it), and
> person-specific patterns live in the local-only `private/pii-rules.toml` (see `CLAUDE.md`
> §4). Belt-and-suspenders sanity check before any push:
> `git status --ignored` and `git ls-files | grep -i -E "private|electric_15|sam_8760"`
> (the second command should return nothing). The published report mentions city/climate
> zone only. A **private repo + GitHub Pages** requires GitHub Pro; on a free account,
> Pages means the site (and repo) are public.

## Repository layout

| Path | Pushed to GitHub? | Contents |
|---|---|---|
| `index.html`, `report-template.html`, `README.md`, `TECHNICAL.md`, `GLOSSARY.md`, `CLAUDE.md`, `reusable-prompt.md`, `DATA-SOURCES-CHEATSHEET.md`, `household.example.yaml`, `requirements.txt`, `stage-private-data.sh`, `LICENSE` | ✅ yes | Report, template, docs, config schema, reproduction tooling, and license (PII-free) |
| `data/`, `analysis/`, `research/` | ✅ yes | Data, scripts, and rate research (PII-free) |
| `.githooks/`, `.gitleaks.toml`, `.github/` | ✅ yes | The mechanical privacy enforcement (pre-commit gitleaks hook, generic scan rules, CI full-history re-scan) and the issue templates |
| `private/1-raw-data/` | ❌ gitignored | Raw SDGE Green Button CSV (contains name/address/account/meter); Enphase SAM 8760 hourly consumption (no identifiers, but reveals household occupancy patterns); CAISO raw day-cache |
| `private/household.yaml`, `private/intake-status.md` | ❌ gitignored | Per-house config written by the intake interview (invoice, dates, vehicle specs…) + the per-field gathered/skipped log that gates Phase B |
| `.env` | ❌ gitignored | Secrets only (PVOutput API key, monitoring tokens) — never in `household.yaml`, never committed |
| `private/3-analysis-extras/` | ❌ gitignored | As-run script copy with personal header |
| `private/README.md` | ✅ yes (placeholder) | Map of the private archive — the one file under `private/` that is committed, so the repo documents what's withheld |

The complete file-by-file inventory follows; `TECHNICAL.md` documents every artifact's
schema and pipeline in depth.

<details>
<summary><strong>Report &amp; documentation</strong></summary>

| File | What it is |
|---|---|
| `index.html` | The interactive report (plan comparison, charts, behavior findings, SDG&E-tool comparison, battery deep-dive) |
| `report-template.html` | De-personalized report skeleton (`{{TOKEN}}` placeholders) — start here when regenerating `index.html` (see `reusable-prompt.md` Phase D and `CLAUDE.md` §10) |
| `TECHNICAL.md` | **Full technical/reproducibility documentation** — every script, data schema, algorithm, and chart pipeline, methods-section style |
| `CLAUDE.md` | Operating rules for AI-assisted reruns (evidence-based mandate, validation order, privacy gates, known pitfalls) |
| `reusable-prompt.md` | Full prompt to reproduce this entire analysis (plan + solar + battery + gas + bill audit) in Claude Cowork |
| `DATA-SOURCES-CHEATSHEET.md` | Per-field intake interview spec: every data source needed for your own home, with links and which PDFs/exports to gather |
| `GLOSSARY.md` | Plain-English definitions of every term of art (NEM, PCIA, CAISO, phantom load, dispatch policy…), with links to authoritative sources |
| `requirements.txt` | Python dependencies for the analysis scripts (pandas, numpy, pyyaml, pdfplumber) |
| `household.example.yaml` | Commented schema template for the per-house config — copy to gitignored `private/household.yaml` and replace every placeholder (the intake interview in `DATA-SOURCES-CHEATSHEET.md` walks each field) |
| `stage-private-data.sh` | Stages the gitignored private inputs (`household.yaml`, interval/SAM/gas exports) from an existing working copy into a fresh clone so the `private/verify` regeneration flow can run there — the script behind the clean-room verification |

</details>

<details>
<summary><strong>Data artifacts</strong> (this house's results; regenerate, don't edit)</summary>

| File | What it is |
|---|---|
| `data/plan_results.csv` | Modeled annual cost per plan (CEA and SDG&E-bundled scenarios) |
| `data/report_data.json` | All computed statistics used by the report |
| `data/hourly_profile.csv`, `data/monthly.csv` | Aggregated usage profiles |
| `data/battery_sim.json` | Battery arbitrage simulation results (6 configurations) |
| `data/backup_endurance.json` | Outage-endurance simulation (config × backup tier) |
| `data/threeway_production_validation.csv` | Daily solar production: Enphase meter vs PVOutput |
| `data/pvoutput_daily.csv` | PVOutput daily generation (public record), Jul 2025–Jul 2026 |
| `data/enphase_daily_production.csv` | Enphase daily production (CT meter), Jul 2025–Jul 2026 |
| `data/pvoutput_5min_sample.csv` | PVOutput 5-minute production sample day |
| `data/behavior_rebuild.json` | Session-based EV/behavior shift scenarios + battery-after-behavior marginals |
| `data/electric_bill_summary.csv` | De-identified per-period totals parsed from the 12 detailed electric bills |
| `data/cleaning_study_daily.csv` | Multi-year daily production windows around the 2024 panel cleaning (diff-in-diff inputs) |
| `data/soiling_results.json` | Soiling/rain-recovery study results (rain events, regressions, annual economics) |
| `data/carbon_results.json` | Grid-carbon timing results (CAISO hourly intensity, household footprint, EV-timing deltas) — the retired 4-day study, kept as a workpaper; its stored cost note predates the canonical-engine rebase (`TECHNICAL.md` §3.10) |
| `data/carbon_fullyear_results.json` | Expanded carbon results: 28 sampled CAISO days + month-hour-mean interpolation (the report's §13 carbon basis) |
| `data/caiso_hourly_intensity.csv` | Per-day hourly CAISO CO₂ intensity table behind the 28-day carbon sampling |
| `data/extra_results.json` | Phantom-baseload decomposition, rate-escalation ladder, marginal price map, NBT re-billing, cleaning-cadence model |
| `data/extended_results.json` | Extended findings: AB 205 fixed-charge status, electrification dividend, away-days, supercharging/weekend-SOP shifts, gas HDD decomposition, 2039 NBT strategy, battery-payback tornado |
| `data/package_results.json` | LOW/MID/HIGH package figures from the integrated pipeline — savings, honest asset-alone paybacks (regenerated by `analysis/package_results.py`) |
| `data/deep_results.json` | Deep-dive outputs: TOU-DR-P wildcard, phantom baseload, EV sessions, vacation detection, Monte Carlo battery ROI |
| `data/gas_monthly_therms.csv`, `data/gas_bill_summary.csv` | Monthly gas usage and aggregated gas bill summary (electrification analysis inputs) |
| `data/weather_daily_tmean.csv`, `data/weather_results.json` | Open-Meteo daily temperatures + weather-normalized cooling results |
| `data/pvoutput_yearly_2020-2025.csv` | PVOutput per-year production stats, 2020–2025 (degradation analysis input) |
| `data/cleaning_study_peaks_2024.csv` | Peak-day production windows around the 2024 cleaning (diff-in-diff companion) |
| `data/wall_charger_daily.csv` | Tesla Wall Connector daily delivered kWh (wall-side) — the independent cross-check of the EV session detector |
| `data/battery_dispatch_policies.json` | Dispatch-policy results: savings, kWh served, cycles/day, hourly profiles, escalation ladder, §6 serviceable-load inputs |
| `data/battery_plan_matrix.json` | The §4 battery × plan matrix: no-battery / with-battery / battery-value per top-3 plan (table rates, cross-plan ranking; canonical-engine cross-check included) |
| `data/bill_periods_electric.csv` | One row per electric billing period across the whole downloaded corpus: days, net vs gross kWh, delivery/generation charges, generation provider (regenerated by `analysis/parse_bills.py`) |
| `data/bill_periods_gas.csv` | One row per gas billing period: therms, total gas service, baseline/non-baseline $/therm |
| `data/bill_tou_detail.csv` | Per period × section × season × rate segment: the kWh and $/kWh exactly as printed on each bill — the per-bill rate evidence |

</details>

<details>
<summary><strong>Analysis scripts &amp; research</strong></summary>

| File | What it is |
|---|---|
| `analysis/analyze.py` | **Legacy** cross-plan ranking model (table rates, kept labeled — `CLAUDE.md` §9); current models import `analysis/rates.py` instead |
| `analysis/analyze_norelief.py` | Legacy variant: prices CEA generation without the Rate Relief Credit |
| `analysis/rates.py` | **Canonical rate constants + billing engine** (bill-derived; imported by all current models) |
| `analysis/billing_model_nem.py` | Bill-validated NEM 2.0 monthly per-TOU-period netting model |
| `analysis/behavior_rebuild.py` | Session-based EV/behavior shift model — physically moves kWh and re-bills (supersedes the crude cap approach) |
| `analysis/battery_backup_sims.py` | Battery arbitrage + backup endurance simulations |
| `analysis/soiling_analysis.py` | Soiling from rain-recovery events + days-since-rain regression (NOAA/RCC ACIS precipitation) |
| `analysis/carbon_timing.py` | Grid-carbon timing from CAISO Today's Outlook history data (CO2 + demand) — original 4-day study |
| `analysis/carbon_fullyear.py` | Expanded carbon sampling: 28 CAISO days + month-hour-mean interpolation → `data/carbon_fullyear_results.json` |
| `analysis/extended_findings.py` | Extended-findings computations (AB 205, electrification dividend, gas HDD decomposition, 2039 strategy, tornado) → `data/extended_results.json` |
| `analysis/deep_analyses.py` | Deep-dive script: TOU-DR-P wildcard, phantom load, EV sessions, vacation detection, Monte Carlo |
| `analysis/battery_dispatch_policies.py` | Battery dispatch-policy comparison — evening-only vs two-window vs price-aware (the report's battery basis) |
| `analysis/battery_plan_matrix.py` | Battery × plan matrix (§4): the price-aware PW3 dispatch billed under each top-3 plan's rate-table values → `data/battery_plan_matrix.json` |
| `analysis/package_results.py` | Composes `data/package_results.json` from the behavior + dispatch artifacts (no new computation) |
| `analysis/lifetime_payback.py` | Lifetime solar payback: cumulative production value vs install invoice, with crossover dates |
| `research/rates-reference.md` | Every rate figure used: SDG&E UDC + EECC per plan, CEA generation, PCIA, fixed charges, baselines, TOU windows — with sources |
| `research/battery-research-notes.md` | 2026 battery prices/specs, incentive status, simulation summary |
| `research/extended-research-notes.md` | AB 205 / DSGS-VPP / outage-exposure / fuel-constant research (sources + captured figures) backing the extended findings |
| `research/sdge-plan-comparison-capture.md` | SDG&E's own plan-tool output vs this model |
| `analysis/parse_bills.py` | Parses the detailed bill PDFs into the per-period and TOU artifacts, and regenerates the two legacy bill summaries as its own reproduction gate |
| `analysis/test_parse_bills.py` | Negative tests proving `parse_bills.py` fails closed: missing statements, corpus gaps, TOU layout drift, mid-write failure, and concurrent publication |
| `analysis/household.py` | Loader for `private/household.yaml` — analysis scripts read per-house facts (invoice, dates, charger kW, vehicle specs…) through it and **fail closed** with a run-the-intake-interview message if the file or a required key is missing |

</details>

## The private inputs — and how to obtain your own

Only three input datasets are withheld (plus the small `private/household.yaml` config the
intake interview writes; its schema is public in `household.example.yaml`). Your own
equivalents come straight from your utility and monitoring portals:

**1. Utility 15-minute interval export** (`Electric_15_Minute_<range>.csv`)
- SDG&E customers: My Energy Center (myenergycenter.com) → Usage → **Green Button Download** →
  set date range (13 months recommended) → format `.csv`. Other utilities: look for
  "Green Button" or "interval data" download in your usage portal. The standard is
  industry-wide, though column layouts vary slightly.
- Format: 13 metadata lines (name, address, account, meter; this is why it's private),
  then a header row and one row per 15-minute interval:
  `Meter Number, Date (M/D/YYYY), Start Time (h:mm AM/PM), Duration (15), Consumption (kWh imported), Generation (kWh exported), Net`.
- `analysis/analyze.py` reads it with `skiprows=13`.

**2. Hourly whole-home consumption, one year**: your total electrical *load*, which is not
the same as grid imports when you have solar. It powers the backup-endurance simulation, the
no-solar counterfactual behind the lifetime-payback numbers, and the load/production splits.
- **How this analysis got it (Enphase-specific):** Enlighten (enlighten.enphaseenergy.com) →
  Reports → **SAM 8760** → pick year → Submit (report is emailed). Requires Enphase
  consumption metering (CTs installed). Format: single column `kWh`, exactly 8,760 hourly
  values per calendar year; `analysis/battery_backup_sims.py` stitches two years into a
  rolling 365 days. There are no identifiers in the file; it's withheld only because hourly
  whole-home load reveals occupancy patterns.
- **Different solar or monitoring hardware? Use your platform's equivalent:** SolarEdge
  (consumption-meter export), Tesla (Powerwall/app energy history), SMA, Fronius, and others
  expose the same consumption feed if consumption metering is installed, as does any
  standalone circuit monitor (Emporia Vue, Sense, IoTaWatt). Any source works if you can
  shape it into hourly kWh for the year; adjust the two-column loader in
  `analysis/battery_backup_sims.py` to your export's format.
- **No consumption metering at all?** Derive it: `load = production + imports − exports`,
  using your production records (dataset the solar platform always has) and the utility
  interval export from item 1. Hourly resolution keeps the energy balance honest; the
  derivation caveats are covered in `TECHNICAL.md`.

**3. Gas daily export** (`gas.csv`) — the same Green Button flow as item 1, for the gas
meter (daily therms). It feeds the gas/electrification analyses in
`analysis/extended_findings.py`. Skip it if you have no gas service.

Everything else needed to reproduce the analysis (daily production, PVOutput records,
the rate tables in `research/rates-reference.md`, and both models) is in this repo.
With your own files above plus current rates, the scripts regenerate every number.
Moving your own copy to a second machine or a fresh clone?
`./stage-private-data.sh <old-working-copy> <new-clone>` places the gitignored inputs
where the `private/verify` flow expects them.

## Refreshing this analysis (same house, new data)

1. My Energy Center → Usage → Green Button Download → last 13 months, CSV → `private/1-raw-data/`.
2. If SDG&E (Jan/Jun) or CEA (Feb/Jun) issued new rates, update `analysis/rates.py`, the
   single source of truth all current models import. If the household changed (vehicle,
   charger, cleaning event, appliance), update `private/household.yaml` too.
3. Re-run the pipeline scripts (`CLAUDE.md` "Commands" has the exact invocations) and confirm
   each `data/*.json` regenerates cleanly; that diff-check is the acceptance gate. For the
   strictest check, clone fresh, stage the private inputs with `stage-private-data.sh`, and
   run the same gates there: the pipeline reproduces byte-identically from a clean clone.
4. Regenerate the report from `report-template.html` per `reusable-prompt.md` Phase D, or
   paste `reusable-prompt.md` into a Claude Cowork session and let it redo everything.

## License

[MIT](LICENSE). The scripts, template, and documentation are free to reuse and adapt.
The committed `data/*` artifacts and `index.html` are one household's results, included
as worked examples; regenerate them from your own data rather than republishing these.

Spotted an error in the method, or a number that doesn't reproduce?
[Open an issue](https://github.com/ookla-ariel-ride/SDGE-Analysis/issues/new/choose);
there are templates for figure errors, reproduction problems, and documentation questions,
each with a privacy checklist so nothing personal lands in a public thread.

---

*Last reviewed: 2026-07-27, against commit `c29309c`.*
