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
- whether converting off gas pencils out: the furnace/heat-pump conversion alone, the
  water heater, and the full transition (with the gas meter's own fixed charge credited
  only where it actually goes away)
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
`private/intake-status.md`. The `has_ev` and `has_gas` flags in that file are load-bearing,
not documentation: they decide which analyses apply, and the scripts fail closed both ways —
a flag set true with its inputs missing is an incomplete intake, and a flag set false with
those inputs present is a contradiction. Absence of data is never read as "you don't have
one". Secrets (API keys, monitoring tokens) go ONLY into a gitignored
`.env`. Analysis may not start while any required field is missing: the scripts fail closed
without `household.yaml`. (The "private inputs" section below shows what the withheld files
look like.)

**2 · Add your personal PII patterns.** Create `private/pii-rules.toml` with your own name,
address, and account/meter numbers (see `CLAUDE.md` §4 for the format and the rule chain).
It stays local; the pre-commit hook picks it up automatically and blocks any commit
containing those values.

**3 · Run the analysis.** Three routes:
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
  `carbon_fullyear.py`, plus `soiling_analysis.py`, `billing_model_nem.py`,
  `lifetime_payback.py`, and — gas households only — `heat_pump_conversion.py` then
  `all_electric_endgame.py` (which depends on `heat_pump_conversion.py`'s own output plus
  `service_headroom.py`'s) as applicable); then fill `report-template.html`'s `{{TOKEN}}`s
  from your regenerated `data/*.json`.
- **Your-own-LLM-key route (no agentic coding tool required):** once `data/*.json` is
  regenerated, `analysis/generate_report.py` fills `report-template.html` and writes
  `index.generated.html` using a paid API key you already have — Anthropic, OpenAI, or
  Google Gemini — instead of an agent harness. Copy `.env.example` to `.env` and add the one
  key you plan to use (never committed; never passed as a CLI argument). Run
  `./.venv/bin/python analysis/generate_report.py --dry-run` first to see every request body
  it would send, written under `private/llm_dry_run/`, with zero sockets opened and zero cost.
  A real run needs `--provider` and `--model` (`--list-models` calls the vendor's own
  model-list endpoint so you never type a stale snapshot id). The model is handed one
  `TODO` block at a time and returns prose for that block only — it never sees the
  surrounding HTML — and every returned fragment is rejected if it contains a bare digit
  outside a `{{TOKEN}}` or `§N` reference, so it cannot invent a figure. A committed
  classification map (`analysis/report_blocks.py`) marks every block `prose` (LLM-written),
  `data` (filled mechanically from an artifact, e.g. one table row per plan), or `human`
  (hardware price quotes, incentive-program status, and the provenance note's review claim —
  things this pipeline has never measured and never will invent); pass researched answers for
  the `human` blocks with `--human-answers your-answers.json`. The run refuses to write
  anything while any block is unresolved, and successful blocks are cached under
  `private/report_cache/` so a re-run with nothing changed makes zero new API calls.
  `--humanize` adds an optional second de-AI-writing rewrite pass per block; a rewrite that
  doesn't clear the same checks silently falls back to the original rather than failing the
  run. See `TECHNICAL.md` §8 for the full provider/egress design.

**4 · Validate before you trust it.** The gates in `CLAUDE.md` §9, in order: your billing
model must reproduce your actual bills before you quote any absolute dollar; every committed
artifact must regenerate from its committed script; report deltas, not levels. Run the test
suites too — `for t in analysis/test_*.py; do ./.venv/bin/python "$t"; done` — since they
carry the fail-closed guards that stop a partial corpus or a stale artifact from being
published as a complete result. These gates have actually been run end-to-end: from a fresh
clone of this repo, with staged private inputs and a new venv, the pipeline regenerated every
committed artifact byte-identically.

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
> (`.github/workflows/tests.yml` runs the fail-closed guard suites alongside it), and
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
| `data/threeway_production_validation.csv` | Daily solar production, three independent ways: the Enphase meter, PVOutput, and a third `meter_derived` series computed from the whole-home CT's gross load minus net import/export |
| `data/pvoutput_daily.csv` | PVOutput daily generation (public record), Jul 2025–Jul 2026 |
| `data/enphase_daily_production.csv` | Enphase daily production (CT meter), Jul 2025–Jul 2026 |
| `data/pvoutput_5min_sample.csv` | PVOutput 5-minute production sample day |
| `data/behavior_rebuild.json` | Session-based EV/behavior shift scenarios + battery-after-behavior marginals |
| `data/electric_bill_summary.csv` | De-identified per-period totals parsed from the 12 detailed electric bills |
| `data/cleaning_study_daily.csv` | Multi-year daily production windows around the 2024 panel cleaning (diff-in-diff inputs) |
| `data/soiling_results.json` | Soiling/rain-recovery study results (rain events, regressions, annual economics) |
| `data/carbon_results.json` | Grid-carbon timing results (CAISO hourly intensity, household footprint, EV-timing deltas) — the retired 4-day study, kept as a workpaper; its stored cost note predates the canonical-engine rebase (`TECHNICAL.md` §3.10) |
| `data/carbon_fullyear_results.json` | Full-year carbon results: 364 of 365 days measured from CAISO, one day filled by month-hour-mean (the report's §13 carbon basis) |
| `data/caiso_hourly_intensity.csv` | Per-day hourly CAISO CO₂ intensity table behind the full-year carbon measurement |
| `data/carbon_dispatch_tradeoff.json` | Three battery dispatch policies compared on cost and CO2 against the same no-battery baseline: cost-minimizing (the existing dispatch policy, reused unmodified), carbon-minimizing (new, mirrors its structure with intensity swapped for price), and a third policy serving either condition — the tradeoff and cross-check figures between them |
| `data/extra_results.json` | Phantom-baseload decomposition, rate-escalation ladder, marginal price map, NBT re-billing, cleaning-cadence model |
| `data/extended_results.json` | Extended findings: AB 205 fixed-charge status, electrification dividend, away-days, supercharging/weekend-SOP shifts, gas HDD decomposition, 2039 NBT strategy, battery-payback tornado |
| `data/nbt_export_rates_2026.csv` | SDG&E's own published hourly Net Billing Tariff (NBT, "Solar Billing Plan") export-pricing table, condensed from the public MIDAS export-pricing files for both the 9-year-lock (NBT26) and no-lock (NBT00) rate vintages |
| `data/nem3_grandfathering.json` | This household's NEM 2.0 grandfathering value re-billed against the real hourly NBT export schedule (both vintages), plus the price-aware battery's own marginal value under that same real schedule, reconciled against `extended_results.json`'s flat-credit `nbt_2039` figures |
| `data/dsgs_event_calendar_2025.csv` | The real 2025 DSGS Option 3 event calendar (68 scheduled test-event date-hour slots, union across ~14 anonymized CEC aggregations, with CAISO LMP per hour) behind `dsgs_vpp_backtest.json`'s own backtest |
| `data/dsgs_vpp_backtest.json` | A hypothetical Powerwall 3's DSGS VPP revenue, backtested against the real 2025 event calendar and this household's own measured load/solar — every figure explicitly hypothetical, since the household owns no battery today |
| `data/cca_generation_rates.csv` | CEA's own charged per-(statement, billing period, season, TOU-period) generation rate, line-parsed directly from all 18 CCA-era bill PDFs — found flat across every one of them |
| `data/cca_bundled_counterfactual.json` | Two-directional repricing of every CCA-era and bundled-era billing period against the other provider's own same-date printed rate, segment by segment, to test whether switching to the CCA was a net win |
| `data/package_results.json` | LOW/MID/HIGH package figures from the integrated pipeline — savings, honest asset-alone paybacks (regenerated by `analysis/package_results.py`) |
| `data/deep_results.json` | Deep-dive outputs: TOU-DR-P wildcard, phantom baseload, EV sessions, vacation detection, Monte Carlo battery ROI |
| `data/gas_monthly_therms.csv`, `data/gas_bill_summary.csv` | Monthly gas usage and aggregated gas bill summary (electrification analysis inputs) |
| `data/weather_daily_tmean.csv`, `data/weather_results.json` | Open-Meteo daily temperatures + weather-normalized cooling results |
| `data/pvoutput_yearly_2020-2025.csv` | PVOutput per-year production stats, 2020–2025 (degradation analysis input) |
| `data/cleaning_study_peaks_2024.csv` | Peak-day production windows around the 2024 cleaning (diff-in-diff companion) |
| `data/wall_charger_daily.csv` | Tesla Wall Connector daily delivered kWh (wall-side) — the independent cross-check of the EV session detector |
| `data/battery_dispatch_policies.json` | Dispatch-policy results: savings, kWh served, cycles/day, hourly profiles, escalation ladder, §6 serviceable-load inputs |
| `data/battery_plan_matrix.json` | The §4 battery × plan matrix: no-battery / with-battery / battery-value per top-3 plan (table rates, cross-plan ranking; canonical-engine cross-check included) |
| `data/battery_sizing_curve.json` | Battery capacity and discharge-power swept independently through the same price-aware dispatch engine used elsewhere, with both shipping Powerwall 3 configurations landing as exact grid points rather than interpolated |
| `data/perfect_foresight_dispatch.json` | The true annual-bill-minimizing battery dispatch, solved as a linear program on the full measured year at identical hardware constraints — bounds how much value the greedy dispatch policy leaves on the table |
| `data/bill_periods_electric.csv` | One row per electric billing period across the whole downloaded corpus: days, net vs gross kWh, delivery/generation charges, generation provider (regenerated by `analysis/parse_bills.py`) |
| `data/bill_periods_gas.csv` | One row per gas billing period: therms, total gas service, baseline/non-baseline $/therm (day-weighted blend across rate segments), baseline allowance (therms), and the flat Gas Energy Charge $/therm (also day-weighted blended) |
| `data/bill_tou_detail.csv` | Per period × section × season × rate segment: the kWh and $/kWh exactly as printed on each bill — the per-bill rate evidence |
| `data/bill_gas_detail.csv` | Per period × charge type (Gas Service tiered baseline/non-baseline, the flat Gas Energy Charge, or Public Purpose Programs + State Regulatory Fee combined) × rate segment: each segment's own day or therm count and $/therm rate(s), exactly as printed on each bill — what `bill_periods_gas.csv`'s blended columns collapse, and what a true marginal-tier gas rebilling needs |
| `data/bill_corpus_boundary.json` | Where the electric bill corpus stops and why: the billing-history export that defines it, any statement parsed from a PDF but not published (with the reason and what would end the exclusion), and the resulting day coverage of the analysis window. A run with no billing-history export staged records a `boundary_not_derived` block instead — where the export was looked for, what was published unchecked as a result, and what would derive the boundary — so an empty exclusion list can never be read as a checked corpus. Written by `analysis/parse_bills.py` in the same atomic set as the bill artifacts it describes |
| `data/rate_vintages.csv` | Every rate cell's constant-rate spans across the bill corpus, each labelled with its evidence tier (directly observed / carried across a gap / absent) and its authority (a charged tariff, or a printed comparison that is not one) |
| `data/rate_rebilling_residuals.csv` | Per-statement reconstruction and corroboration table behind the historical rate engine: how each statement re-prices through the engine's timeline, and which of its printed lines an independent statement can corroborate |
| `data/tou_spread.json` | Rate escalation per season × TOU cell and for the on-peak-to-super-off-peak spread itself: each with its vintage count, its fit uncertainty both before and after widening for a data-chosen breakpoint, and the verdict that follows. On this corpus both seasons come out **not determined**, so the file records why and what would settle it rather than a measured escalation |
| `data/bill_decomposition.json` | Why the bill changed between two matched periods: billing-mode evidence, per season × TOU cell price/quantity bounds, the settlement component, and the provider-vs-vintage split |
| `data/tou_audit.csv`, `data/tou_audit_summary.json` | The utility's billed TOU buckets reconciled against the raw 15-minute export, per statement and in summary |
| `data/lifetime_payback.json` | Cumulative value of metered production against the install invoice, with the crossover dates and the blended rates it was derived from |
| `data/service_headroom.json` | Electrical service headroom under NEC 220.87: the measured demand basis, the calculated existing load it implies, what is left against the main breaker and the busbar, and the 120%-rule check on the existing PV backfeed |
| `data/irreducible_bill.json` | The strict floor of the annual electric bill that no purchase can remove (the per-day fixed charge alone), reported separately from non-bypassable charges — real, currently owed, but usage-dependent, not fixed: per-period extraction (cross-checked against an independently sourced TOU-table computation), the trailing-12-month figures, each component's share of each `package_results.json` package's projected bill, and the minimum-bill-provision and NBC-on-gross-kWh checks behind it |
| `data/tou_structure_stress.json` | Today's $/kWh rates held fixed while TOU window STRUCTURE (on-peak start/end, midday super-off-peak window, summer month set) is perturbed across scenarios individually labeled measured / historically-motivated / hypothetical |
| `data/uncertainty_results.json` | A 7-input Monte Carlo on the battery's own payback/NPV — rate escalation, degradation, install cost, EV-behavior persistence, production-measurement disagreement, soiling ambiguity, round-trip efficiency — reproducing the older 3-input Monte Carlo bit-for-bit as a verified special case |
| `data/gross_import_decomposition.json` | The rise in gross imports between two matched early-summer bill periods, decomposed into consumption vs. production terms under two independent diurnal-shape assumptions plus a clear-sky geometry check |
| `data/reprice_by_vintage.json` | An eight-term, exact-to-the-cent decomposition of the gap between the current-rate-everywhere model and the real billed year — window alignment, TOU-window-shape confounds, CCA/state-surcharge adders, and rate-vintage effects |
| `data/quiet_night_floor.json` | The overnight "phantom load" floor re-measured directly from interval data (not the older method's proxy), priced two independent ways, reconciled against `extra_results.json`'s own earlier phantom-load figure |
| `data/heat_pump_conversion.json` | Replacing the gas furnace + AC with a heat pump: furnace therms isolated two independent ways and cross-checked, the added electric load placed into real 15-minute intervals using the same capacity-capped daily shape the gas savings themselves are priced against, then re-billed across the measured year, payback and NPV (standalone and marginal-over-AC-replacement) across a COP × install-cost × gas-price sensitivity grid |
| `data/all_electric_endgame.json` | The full transition off gas: fixed charges isolated per statement, remaining gas end uses enumerated from the daily series, each conversion (water heater, furnace) costed and sequenced with the fixed-charge release credited only to the final step, added electric load re-billed jointly (not summed independently) with a quantified tier/solar double-count correction, service headroom checked against the panel, meter-removal research, and reconciliation against the furnace conversion and the HDD decomposition |

</details>

<details>
<summary><strong>Analysis scripts &amp; research</strong></summary>

| File | What it is |
|---|---|
| `analysis/analyze.py` | **Legacy** cross-plan ranking model (table rates, kept labeled — `CLAUDE.md` §9); current models import `analysis/rates.py` instead |
| `analysis/analyze_norelief.py` | Legacy variant: prices CEA generation without the Rate Relief Credit |
| `analysis/rates.py` | **Canonical rate constants + billing engine** for the CURRENT tariff (bill-derived; imported by all current models). Also owns the tariff calendar — TOU windows, season, and the holiday rule |
| `analysis/rates_history.py` | The tariff in force on any PAST date the bill corpus covers, read out of the committed bill artifacts. Every value carries its evidence tier and its authority, so a printed comparison rate can never be returned as a charged tariff → `data/rate_vintages.csv` |
| `analysis/tou_audit.py` | Reconciles the utility's billed TOU buckets against the raw interval export, scoring alternative day-type and window rules against the statements |
| `analysis/tou_spread.py` | Tests whether the on-peak-to-super-off-peak gap is widening, per season, from the per-bill rate evidence. Counts distinct rate changes rather than statement reprints, refits after the largest single step, and widens the interval for having chosen that step from the data. Neither one tariff redesign nor a densely reprinted tariff can be published as an ongoing trend → `data/tou_spread.json` |
| `analysis/bill_decomposition.py` | Decomposes a year-over-year bill change into price, quantity, TOU mix and generation provider, per season × TOU cell, after establishing from statement text whether the energy was billed monthly or accrued to the annual true-up |
| `analysis/report_data.py` | Builds the report's chart arrays from the committed artifacts, so every series on the page is regenerable |
| `analysis/publish.py` | Crash-consistent multi-artifact publication (lock, backup, restore) used where several artifacts must land as one set |
| `analysis/billing_model_nem.py` | Bill-validated NEM 2.0 monthly per-TOU-period netting model |
| `analysis/behavior_rebuild.py` | Session-based EV/behavior shift model — physically moves kWh and re-bills (supersedes the crude cap approach) |
| `analysis/battery_backup_sims.py` | Battery arbitrage + backup endurance simulations |
| `analysis/threeway_production_validation.py` | Regenerates the three-column production-validation series from committed daily records: PVOutput and Enphase-meter totals passed through unchanged, plus a third `meter_derived` series independently computed from the whole-home SAM CT's gross load minus the revenue meter's net import/export, with explicit DST-day reconciliation between the flat-clock SAM export and the wall-clock Green Button meter → `data/threeway_production_validation.csv` |
| `analysis/soiling_analysis.py` | Soiling from rain-recovery events + days-since-rain regression (NOAA/RCC ACIS precipitation) |
| `analysis/carbon_timing.py` | Grid-carbon timing from CAISO Today's Outlook history data (CO2 + demand) — original 4-day study |
| `analysis/carbon_fullyear.py` | Full-year carbon measurement: 364 of 365 CAISO days measured, one day filled by month-hour-mean → `data/carbon_fullyear_results.json` |
| `analysis/carbon_dispatch_tradeoff.py` | Runs the household battery three ways on the same measured year — cost-minimizing, carbon-minimizing, and a policy serving either condition — and compares all three on dollars and CO2 against the same no-battery baseline → `data/carbon_dispatch_tradeoff.json` |
| `analysis/extended_findings.py` | Extended-findings computations (AB 205, electrification dividend, gas HDD decomposition, 2039 strategy, tornado) → `data/extended_results.json` |
| `analysis/nem3_grandfathering.py` | NEM 2.0 grandfathering value re-billed against SDG&E's real hourly Net Billing Tariff export schedule (both rate vintages), plus the price-aware battery's own marginal value under that same real schedule, reconciled against `extended_findings.py`'s flat-credit `nbt_2039` figures → `data/nem3_grandfathering.json` |
| `analysis/dsgs_vpp_backtest.py` | Replays a hypothetical Powerwall 3's DSGS Option 3 VPP revenue against the real 2025 CEC event calendar (`data/dsgs_event_calendar_2025.csv`, 68 scheduled test-event date-hour slots, none a real emergency dispatch) and this household's own measured load/solar, inferring which anonymized UDC is SDG&E from enrollment scale and corroborating that against a named-utility report — every figure explicitly hypothetical, since the household owns no battery today → `data/dsgs_vpp_backtest.json` |
| `analysis/cca_rate_extraction.py` | Parses every CCA-era bill PDF's own "CCA Electric Generation Charges" section, matched to its billing period and cross-footed against the bill's own printed total, to extract CEA's charged per-season × TOU-period generation rate directly — found flat across all 18 statements → `data/cca_generation_rates.csv` |
| `analysis/cca_bundled_counterfactual.py` | Was switching to the CCA a win? Reprices the CCA-billed periods at SDG&E's own same-date bundled-generation comparison table, and the bundled-era baseline at the CCA's own charged rate, at SEGMENT level (excluding mixed-sign cells) rather than one representative date per period → `data/cca_bundled_counterfactual.json` |
| `analysis/deep_analyses.py` | Deep-dive script: TOU-DR-P wildcard, phantom load, EV sessions, vacation detection, Monte Carlo |
| `analysis/battery_dispatch_policies.py` | Battery dispatch-policy comparison — evening-only vs two-window vs price-aware (the report's battery basis) |
| `analysis/battery_plan_matrix.py` | Battery × plan matrix (§4): the price-aware PW3 dispatch billed under each top-3 plan's rate-table values → `data/battery_plan_matrix.json` |
| `analysis/battery_sizing_curve.py` | A sizing curve, not a two-product comparison: sweeps battery capacity (5–40 kWh at fixed discharge power) and discharge power (5–15 kW at fixed capacity) through the same price-aware dispatch engine used elsewhere, with both shipping Powerwall 3 configurations landing as exact grid points → `data/battery_sizing_curve.json` |
| `analysis/perfect_foresight_dispatch.py` | How much is a smarter controller worth? Solves the annual-bill-minimizing battery dispatch as a linear program (35,040 15-minute intervals/year, same hardware constraints and NEM-netting engine as the greedy heuristic) to bound how much value the greedy dispatch policy leaves on the table → `data/perfect_foresight_dispatch.json` |
| `analysis/package_results.py` | Composes `data/package_results.json` from the behavior + dispatch artifacts (no new computation) |
| `analysis/lifetime_payback.py` | Lifetime solar payback: cumulative production value vs install invoice, with crossover dates |
| `analysis/service_headroom.py` | Electrical service headroom from measured demand: takes the peak interval demand out of the Green Button export, applies the NEC 220.87 existing-dwelling method (measured maximum demand × 125%), and checks the result and the existing PV backfeed against the panel facts in `private/household.yaml` → `data/service_headroom.json` |
| `analysis/irreducible_bill.py` | Splits every electric billing period into a fixed daily charge, non-bypassable charges billed on gross imported kWh, taxes/fees and a residual energy bucket; cross-checks the residual against an independently sourced TOU-table computation, states the fixed daily charge alone as the strict floor over the trailing 12-month bill window (non-bypassable charges are reported separately — real, but usage-dependent, not fixed), and expresses each component as a share of each package's projected bill → `data/irreducible_bill.json` |
| `analysis/tou_structure_stress.py` | Holds today's $/kWh rates fixed and instead perturbs TOU window STRUCTURE (on-peak start/end, midday super-off-peak window, summer month set) across scenarios individually labeled measured / historically-motivated / hypothetical by their own evidentiary basis, to test a redrawn-boundary risk the escalation-only sensitivity can't see → `data/tou_structure_stress.json` |
| `analysis/uncertainty_propagation.py` | A 7-input Monte Carlo (rate escalation, battery degradation, install cost, EV-behavior persistence, production-measurement disagreement, soiling-rate ambiguity, round-trip efficiency), each bound to a specific committed artifact, reproducing the older 3-input Monte Carlo (`deep_analyses.py`) bit-for-bit as a verified special case, to report the battery's payback/NPV as a distribution rather than a point estimate → `data/uncertainty_results.json` |
| `analysis/gross_import_decomposition.py` | Decomposes the rise in gross imports between two matched early-summer bill periods into consumption vs. production terms under two independent diurnal-shape assumptions plus a clear-sky geometry check, since no pre-2025 hourly production record exists to measure the earlier period directly → `data/gross_import_decomposition.json` |
| `analysis/reprice_by_vintage.py` | Chains eight terms — window alignment, TOU-window-shape/PCIA-restart confound, separated CCA product-adder and state-surcharge terms, and rate-vintage effects — to decompose the gap between the current-rate-everywhere estimate and the real billed year exactly to the cent, confirming (via `cca_rate_extraction.py`'s flat-rate finding) that generation-rate vintage itself contributed zero → `data/reprice_by_vintage.json` |
| `analysis/quiet_night_floor.py` | Re-measures the overnight "phantom load" floor directly from interval data (the 15-minute Green Button import series plus the Enphase SAM whole-home CT), prices its removal two independent ways, and reconciles the small gap against `extra_results.json`'s own earlier phantom-load figure → `data/quiet_night_floor.json` |
| `analysis/heat_pump_conversion.py` | Heat-pump conversion scenario model: isolates furnace therms from the gas meter two independent ways, sizes the heat pump's replacement electricity by COP scenario, places it into real 15-minute intervals using the same capacity-capped daily heating shape gas savings are priced against, re-bills the measured year with the canonical NEM engine, and prices displaced gas at each real billing period's own realized rate → `data/heat_pump_conversion.json` |
| `analysis/all_electric_endgame.py` | Costs cancelling the gas meter entirely: isolates the fixed charge, enumerates remaining gas end uses, sequences and costs each conversion with the fixed-charge release credited only to the final step, jointly re-bills the combined added electric load (not two independent rebills summed, which double-claims solar), checks service headroom, researches meter removal, and reconciles against the furnace conversion and §10's existing figures → `data/all_electric_endgame.json` |
| `analysis/extra_results.py` | Regenerates `data/extra_results.json`'s `escalation` block — a RETIRED, evening-only-dispatch scenario, distinct by design from the current published escalation ladder in `data/battery_dispatch_policies.json` (TECHNICAL.md §3.11) — from a documented historical constant instead of an orphaned hand-typed figure; the file's other six keys (phantom baseload, price map, NBT bracket, cleaning, true-up, EV fleet) are one-time measurements with no other source, so they pass through unchanged |
| `research/rates-reference.md` | Every rate figure used: SDG&E UDC + EECC per plan, CEA generation, PCIA, fixed charges, baselines, TOU windows — with sources |
| `research/battery-research-notes.md` | 2026 battery prices/specs, incentive status, simulation summary |
| `research/extended-research-notes.md` | AB 205 / DSGS-VPP / outage-exposure / fuel-constant research (sources + captured figures) backing the extended findings |
| `research/sdge-plan-comparison-capture.md` | SDG&E's own plan-tool output vs this model |
| `analysis/parse_bills.py` | Parses the detailed bill PDFs into the per-period and TOU artifacts, and regenerates the two legacy bill summaries as its own reproduction gate. Reads `household.has_gas` — the flag, not the presence of a directory, decides whether gas is expected |
| `analysis/generate_report.py` | Orchestrates the "your-own-LLM-key" report route (step 3 above, `TECHNICAL.md` §8): resolves `report-template.html`'s TODO blocks per `report_blocks.py`'s classification, runs LLM output through a numeral guard and `prose_lint.py` with one corrective retry, caches successful fragments, and writes only `index.generated.html` — never `index.html` — and only when every block resolves |
| `analysis/report_blocks.py` | Parses every actionable TODO block out of `report-template.html` and classifies each as `data` (mechanical row-builder over a committed artifact), `human` (needs a fact this repo has never measured), or `prose` (answerable from `report_tokens.py`'s own token map) |
| `analysis/report_tokens.py` | Parses every `{{TOKEN}}` in `report-template.html` and resolves each against a committed, explicit source map (`data/*.json`, `data/*.csv`, `analysis/rates.py`, or public-ok `private/household.yaml` fields only) — raising `SystemExit` naming any token whose source goes missing rather than inventing a value |
| `analysis/llm_providers.py` | Vendor-native REST adapters (Anthropic, OpenAI, Gemini) for the report-generation LLM calls, funneling every outbound request through one chokepoint for egress auditing, with credentials loaded from a hand-parsed `.env` and never accepted as a CLI argument |
| `analysis/prose_lint.py` | Mechanical linter gating `generate_report.py`'s LLM-generated prose: flags `CLAUDE.md`'s banned process-narrative phrases, negative parallelisms, filler transitions, and promotional adjectives; any violation hard-fails that block rather than publishing it |
| `analysis/privacy_tiers.py` | Enforces `CLAUDE.md` §4's private-only/secret intake tiers mechanically: parses field tiers from `DATA-SOURCES-CHEATSHEET.md`, resolves each to its `private/household.yaml` path, and scans every tracked file for private-only values — run via `.githooks/pre-commit` as the actual enforcement gate |
| `analysis/dry_run.py` | Asks what a generator WOULD write into `data/` without letting it write anything: copies the tracked tree and the whole `private/` archive into a throwaway sandbox outside the checkout, runs the generator's own write path there, and diffs the sandbox's `data/` against the repo's (JSON by changed top-level key, CSV by changed rows). `--check` exits 1 if an artifact would change — the non-mutating counterpart of the `CLAUDE.md` §9 regeneration gate. A crash, a run that writes nothing, or a sandbox the generator's root walk-up could escape is reported as a failure, never as "no changes" |
| `analysis/test_*.py` | **46 test suites**, run by CI on every push. They are mostly *negative* tests: each one injects the defect it claims to catch and proves the code refuses. `test_scripts_runnable.py` additionally executes every generator and byte-diffs each committed artifact against a fresh run — the §9 gate, folded into the suite |
| `analysis/check_coverage.sh` | Local coverage gate (≥90% statement coverage across the analysis package); needs the private archive, so it does not run in CI |
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
   each `data/*.json` regenerates cleanly; that diff-check is the acceptance gate. Run the test
   suites as well — `test_scripts_runnable.py` performs the byte-diff across every owned
   artifact in one pass. For the strictest check, clone fresh, stage the private inputs with
   `stage-private-data.sh`, and run the same gates there: the pipeline reproduces
   byte-identically from a clean clone.
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

*Last reviewed: 2026-08-09, against commit `ef86ff7`.*
